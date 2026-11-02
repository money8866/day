# -*- coding: utf-8 -*-
"""
V10.1 动态仓位管理
=========================================
在 V10.0 五状态机（S1~S5）基础上增加：

  · S6 强势主升识别 —— 四个**独立**检查模块（指数趋势 / 市场广度 / 短线赚钱效应 / 主线持续性），
    各自输出结果、证据与置信度，不合并成一个无法解释的总分。
  · 仓位权限（position_authority）—— 市场环境允许持有的**最高**仓位。
  · 满仓授权（full_position_authorization）—— 独立函数，十项必要条件全过才允许 100%。
  · 个股机会匹配 —— 复用原有个股选股系统（picks_db/stock_picks.db），不重新设计买卖信号。
  · 组合风险预算 —— 单股 / 单主题 / 单行业上限 + 总风险预算 + 现金最低比例。
  · 分级加仓 —— 20→40→60→80→100 阶梯，每次加仓前重新检查。
  · 满仓退出（full_position_exit_check）—— 环境转弱时收回满仓权限。

★ 核心原则：市场决定仓位上限，个股信号决定买入机会，组合风险决定实际仓位。

★ 数据只读复用，不重复下载行情：
  · D:\\mystock\\cache_daily\\stock_data.db   —— 全市场日线 + 指数日线
  · solo\\picks_db\\stock_picks.db            —— 个股信号（信号/动作/止损/目标/行业/主题）
  · D:\\mystock\\report_daily\\theme_scores.db —— 主题持续性（lifecycle/days_strong/mainline_type）

独立运行：
  python market_regime_v101.py 20261030
  python market_regime_v101.py 20261030 --json
"""
import os
import sys
import json
import copy
import sqlite3
import argparse
import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(os.path.dirname(BASE_DIR), 'cache_daily')
STOCK_DB = os.path.join(CACHE_DIR, "stock_data.db")
PICKS_DB = os.path.join(BASE_DIR, 'picks_db', 'stock_picks.db')
# ★ 主题库真实路径在 solo/report_daily/ 下；
#   D://mystock//report_daily//theme_scores.db 是一个 0 字节的空文件（同名的坑）。
THEME_SCORES_DIR = os.path.join(BASE_DIR, 'report_daily')
THEME_SCORES_DB = os.path.join(THEME_SCORES_DIR, 'theme_scores.db')
CONFIG_PATH = os.path.join(BASE_DIR, 'config_v101_position.json')
V10_CONFIG_PATH = os.path.join(BASE_DIR, 'config_v10_market_regime.json')

# 状态常量（与 V10.0 保持一致，S6 为新增）
STATE_NORMAL = "S1"
STATE_DRAWDOWN = "S2"
STATE_PANIC = "S3"
STATE_EASING = "S4"
STATE_REPAIR = "S5"
STATE_STRONG = "S6"

STATE_CN = {
    STATE_NORMAL: "正常",
    STATE_DRAWDOWN: "退潮",
    STATE_PANIC: "恐慌加速",
    STATE_EASING: "卖压衰减",
    STATE_REPAIR: "修复确认",
    STATE_STRONG: "强势主升",
}

# 满仓授权结论
FULL_ALLOWED = "FULL_ALLOWED"
FULL_DENIED = "FULL_DENIED"
FULL_REVIEW = "FULL_REVIEW"

FULL_CN = {
    FULL_ALLOWED: "允许满仓",
    FULL_DENIED: "不允许满仓",
    FULL_REVIEW: "证据不足，需观察",
}

# 退出检查结论
EXIT_HOLD = "HOLD_POSITION"
EXIT_REDUCE = "REDUCE_POSITION"
EXIT_INVALID = "EXIT_INVALID_POSITIONS"

EXIT_CN = {
    EXIT_HOLD: "暂时维持，停止加仓",
    EXIT_REDUCE: "降低仓位",
    EXIT_INVALID: "处理逻辑已失效的持仓",
}


# ============================================================
# 配置
# ============================================================
def load_config(path: str = None) -> dict:
    """加载 V10.1 配置（与 V10.0 配置合并）。"""
    cfg = {}
    if os.path.exists(V10_CONFIG_PATH):
        with open(V10_CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg.update(json.load(f))
    p = path or CONFIG_PATH
    if os.path.exists(p):
        with open(p, 'r', encoding='utf-8') as f:
            cfg.update(json.load(f))
    return cfg


# ============================================================
# 数据访问（全部只读）
# ============================================================
def _ro(path: str):
    """只读连接。"""
    if not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(f"file:{path.replace(chr(92), '/')}?mode=ro",
                               uri=True, timeout=15)
        return conn
    except Exception:
        return None


def _query_index_hist(code: str, end_date: str, lookback: int = 260) -> Optional[pd.DataFrame]:
    """取指数日线（截至 end_date，向前 lookback 根）。"""
    conn = _ro(STOCK_DB)
    if conn is None:
        return None
    try:
        df = pd.read_sql_query(
            "SELECT trade_date, close, pct_chg, vol, amount FROM index_daily_cache "
            "WHERE ts_code = ? AND trade_date <= ? ORDER BY trade_date DESC LIMIT ?",
            conn, params=(code, end_date, lookback))
        return df if not df.empty else None
    except Exception:
        return None
    finally:
        conn.close()


def _query_daily(conn, trade_date: str) -> Optional[pd.DataFrame]:
    if conn is None:
        return None
    try:
        df = pd.read_sql_query(
            "SELECT ts_code, pct_chg, amount, close FROM daily_cache WHERE trade_date = ?",
            conn, params=(str(trade_date),))
        return df if not df.empty else None
    except Exception:
        return None


# ============================================================
# A. 指数趋势检查
# ============================================================
@dataclass
class ModuleResult:
    """单个检查模块的结果：结论 + 证据 + 置信度。不合并成总分。"""
    name: str
    passed: bool = False
    confidence: str = "低"
    evidence: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)


_SNAP_CACHE = {}
_HIST_PACK = {"value": None}   # ★ V10.2：全局 hist_pack（回放脚本注入）


def _get_snapshot(trade_date: str, cfg: dict, v10cfg: dict, hist_pack=None):
    """取市场快照（**按日期缓存**）。

    ★ V10.1 一天内会多次需要同一个快照：主流程 1 次 + 四模块各 1 次
      + S6 判定 1 次 = 最多 5 遍。每遍都要扫 daily_cache 算当日广度，
      198 个交易日的回放会超时（实测 SIGTERM）。
      这里按 trade_date 缓存，同一天只算一次。
    ★ 只在单进程内缓存，不影响结果正确性（同一交易日的快照是确定的）。

    ★ V10.2 修复：把 `hist_pack` 透传给 collect_market_snapshot。
      回放脚本会注入预计算的广度/成交额/涨跌停历史序列，
      若不透传则每次都回落到 `_collect_breadth_history` / `_collect_limit_history`
      逐日扫全表（实测单日 562 次 SQL 查询 → 全年回放超时）。
    """
    key = str(trade_date)
    if key not in _SNAP_CACHE:
        import market_regime_v10 as v10
        if hist_pack is None:
            hist_pack = _HIST_PACK.get("value")
        _SNAP_CACHE[key] = v10.collect_market_snapshot(key, v10cfg,
                                                       hist_pack=hist_pack)
    return _SNAP_CACHE[key]


def set_hist_pack(pack):
    """★ V10.2：注入预计算的历史序列（回放脚本用），返回旧值便于还原。"""
    old = _HIST_PACK.get("value")
    _HIST_PACK["value"] = pack
    return old


def check_index_trend(trade_date: str, cfg: dict) -> ModuleResult:
    """A. 指数趋势。

    ★ 不能仅凭单日反弹确认强势主升 → 要求多指数站上 MA20/MA60 且近 5/20 日收益为正。
    ★ 不能因价格分位低就认为市场即将反转 → 本模块**不**使用分位。
    ★ 不能用单一指数代表整个 A 股 → 要求至少 min_index_count 个指数同时满足。
    """
    res = ModuleResult(name="指数趋势")
    it = cfg["strong_uptrend"]["index_trend"]

    # V10.0 已算好的指数指标（含 MA20/MA60/ret5/ret20）
    try:
        import market_regime_v10 as v10
        v10cfg = v10.load_config()
        snap = _get_snapshot(trade_date, cfg, v10cfg)
        idx_map = snap.indexes
    except Exception as e:
        res.missing.append(f"指数数据不可用: {e}")
        res.evidence.append("指数数据缺失，无法判定趋势")
        return res

    if not idx_map:
        res.missing.append("指数数据为空")
        res.evidence.append("指数数据缺失，无法判定趋势")
        return res

    total = len(idx_map)
    above20, above60, ret5_ok, ret20_ok = [], [], [], []
    for name, ix in idx_map.items():
        if ix.above_ma20 is not None:
            above20.append((name, ix.above_ma20))
        if ix.above_ma60 is not None:
            above60.append((name, ix.above_ma60))
        if ix.ret5 is not None:
            ret5_ok.append((name, ix.ret5))
        if ix.ret20 is not None:
            ret20_ok.append((name, ix.ret20))

    if not above20 or not ret5_ok:
        res.missing.append("指数 MA/收益数据不完整")
        res.evidence.append("指数趋势数据不完整")
        return res

    n20 = sum(1 for _, v in above20 if v)
    n60 = sum(1 for _, v in above60 if v)
    r20_above = sum(1 for _, v in above20)
    r5_pos = sum(1 for _, v in ret5_ok if v > it["min_ret5"])
    r20_pos = sum(1 for _, v in ret20_ok if v > it["min_ret20"])

    ratio20 = n20 / r20_above if r20_above else 0.0
    ratio60 = n60 / len(above60) if above60 else 0.0
    ratio5 = r5_pos / len(ret5_ok) if ret5_ok else 0.0
    ratio20d = r20_pos / len(ret20_ok) if ret20_ok else 0.0

    res.details = {
        "index_count": total,
        "above_ma20_ratio": round(ratio20, 3),
        "above_ma60_ratio": round(ratio60, 3),
        "ret5_positive_ratio": round(ratio5, 3),
        "ret20_positive_ratio": round(ratio20d, 3),
        "detail": {
            "above_ma20": {n: v for n, v in above20},
            "above_ma60": {n: v for n, v in above60},
            "ret5": {n: round(v, 2) for n, v in ret5_ok},
            "ret20": {n: round(v, 2) for n, v in ret20_ok},
        },
    }

    # ---------- 条件逐项判定（V10.4 方向口径重构）----------
    # ★ V10.4：把「绝对水平门槛」换成「方向/相对口径」。
    #
    #   事实依据（2026 全年 198 个交易日，3 个事实标签 × 前后半段分割）：
    #     旧口径（站上MA20占比≥60% 且 站上MA60占比≥40% 且 近5日正占比≥50%
    #            且 近20日正占比≥50%）在「未来20日基准收益>3%」标签下
    #     **事后强市率 0.0%**（51 个样本无一命中），
    #     且该条件在 5 个标签 × 2 段共 10 格中有 8 格为**负向**。
    #   原因：2026 是宽幅震荡市，**指数全面走强往往意味着行情进入后段**
    #         （普涨 → 分化 → 回落），而非启动初期。
    #   新口径用两个**方向**条件替代四个绝对门槛：
    #     · ratio20 ≥ ratio60：短期站上均线的指数比例高于长期 → 短周期占优 = 启动特征
    #     · ratio5  ≥ ratio20d：近 5 日正收益的指数比例高于近 20 日 → 短期在转强
    #   两者在 6 格（3 标签 × 2 段）中**全部为正**（+3.0 ~ +12.2pp）。
    #
    #   注意：这里刻意**不加**「ratio20 ≥ 某绝对水位」的下限 ——
    #   实测该类条件在 6 格中全部强负（-7.6 ~ -21.4pp），
    #   因为它恰好筛掉了「指数尚未全面走强」的启动初期。
    c1 = ratio20 >= ratio60
    res.evidence.append(
        f"站上MA20占比 {ratio20*100:.0f}% ≥ 站上MA60占比 {ratio60*100:.0f}%（短周期占优）"
        + ("✓" if c1 else "✗"))
    c2 = ratio5 >= ratio20d
    res.evidence.append(
        f"近5日正收益占比 {ratio5*100:.0f}% ≥ 近20日 {ratio20d*100:.0f}%（短期转强）"
        + ("✓" if c2 else "✗"))
    c3 = total >= it["min_index_count"]
    res.evidence.append(f"参与指数 {total} 个（要求 ≥{it['min_index_count']}）" + ("✓" if c3 else "✗"))

    # 近期回撤（用中证2000 代表）—— 风险闸门，保留
    core = idx_map.get("中证2000")
    c4 = True
    if core and core.max_drawdown_20d is not None:
        # V10.0 的 max_drawdown_20d 是**负数百分数**（如 -16.37 表示回撤 16.37%），
        # 统一转为正的回撤幅度比例再与阈值比较。
        dd = abs(float(core.max_drawdown_20d)) / 100.0
        c4 = dd <= it["max_recent_drawdown"]
        res.evidence.append(
            f"中证2000近20日最大回撤 {dd*100:.1f}%"
            f"（要求 ≤{it['max_recent_drawdown']*100:.0f}%）" + ("✓" if c4 else "✗"))
        res.details["max_drawdown_20d"] = dd

    res.passed = all([c1, c2, c3, c4])
    res.confidence = "高" if res.passed and not res.missing else ("中" if res.passed else "低")
    return res


# ============================================================
# B. 市场广度检查
# ============================================================
def check_breadth(trade_date: str, cfg: dict) -> ModuleResult:
    """B. 市场广度。

    ★ 不得因为某一天上涨家数占比超过 55% 就直接进入 S6 →
      要求广度水平达标 + 相对 5/20 日均值持续改善 + 可选连续 2 日达标。
    """
    res = ModuleResult(name="市场广度")
    b = cfg["strong_uptrend"]["breadth"]
    try:
        import market_regime_v10 as v10
        v10cfg = v10.load_config()
        snap = _get_snapshot(trade_date, cfg, v10cfg)
        br = snap.breadth
        lim = snap.limit
    except Exception as e:
        res.missing.append(f"广度数据不可用: {e}")
        res.evidence.append("广度数据缺失，无法判定")
        return res

    if br.breadth_ratio is None:
        res.missing.append("广度比缺失")
        res.evidence.append("广度数据缺失，无法判定")
        return res

    ratio = br.breadth_ratio
    # ★ V10.0 的 _hist 存的是**百分数**（如 32.0 表示 32%），
    #   而 breadth_ratio 是**比例**（0.32）。此处统一转成比例再比较。
    hist_raw = br._hist or []
    hist = [h / 100.0 for h in hist_raw]
    ma5 = sum(hist[-5:]) / len(hist[-5:]) if len(hist) >= 5 else None
    ma20 = sum(hist[-20:]) / len(hist[-20:]) if len(hist) >= 20 else None

    res.details = {
        "breadth_ratio": round(ratio, 4),
        "breadth_ma5": round(ma5, 4) if ma5 else None,
        "breadth_ma20": round(ma20, 4) if ma20 else None,
        "up_count": br.up_count, "down_count": br.down_count,
        "zt_count": lim.zt_count, "dt_count": lim.dt_count,
        "history_len": len(hist),
        "breadth_prev": br.prev_breadth_ratio,
    }

    # ---------- 条件逐项判定（V10.4 方向口径重构）----------
    # ★ V10.4：以「广度改善方向」为核心，水平门槛只做宽松底线。
    #
    #   事实依据（2026 全年 198 个交易日）：
    #     「5日均广度 ≥ 20日均广度」是全部候选条件中**边际价值最高**的
    #     （5 个事实标签下 +16pp / -1.8pp / +7.2pp / +1.5pp / +6.9pp，
    #      分割验证 F1 从旧口径 16.7 → 21~22）。
    #   原因：2026 广度日均摆动 25pp、82 天摆动 >30pp，
    #         **均值水平**（5日均/20日均达标）在这种市场里几乎无区分度，
    #         而**方向**（短均线上穿长均线 = 市场在改善）才稳定有效。
    #
    #   被移除的旧条件及其实测边际价值：
    #     · 当日占比 ≥55%      → -0.5pp（且通过率仅 40%，筛掉太多）
    #     · 20日均 ≥45%        → -2.7pp（6 格中 5 格为负）
    #     · 连续 2 日 ≥55%     → -0.6pp
    #   保留但放宽：5日均 ≥45%（原 50%）—— 底线而非门槛。
    c1 = ma5 is None or ma20 is None or ma5 >= ma20
    res.evidence.append(
        (f"5日均广度 {ma5*100:.1f}% ≥ 20日均 {ma20*100:.1f}%（广度在改善）"
         if (ma5 is not None and ma20 is not None) else "广度均值不可用")
        + ("✓" if c1 else "✗"))

    c2 = ma5 is None or ma5 >= b["breadth_ma5_min"]
    res.evidence.append(
        (f"5日均广度 ≥{b['breadth_ma5_min']*100:.0f}% 底线"
         f"（当前 {ma5*100:.1f}%）" if ma5 else "5日均广度不可用")
        + ("✓" if c2 else "✗"))

    c3 = lim.zt_count is None or lim.zt_count >= b["zt_count_min"]
    res.evidence.append(
        (f"涨停 {lim.zt_count} 家（要求 ≥{b['zt_count_min']}）" if lim.zt_count is not None else "涨停数不可用")
        + ("✓" if c3 else "✗"))

    c4 = lim.dt_count is None or lim.dt_count <= b["dt_count_max"]
    res.evidence.append(
        (f"跌停 {lim.dt_count} 家（要求 ≤{b['dt_count_max']}）" if lim.dt_count is not None else "跌停数不可用")
        + ("✓" if c4 else "✗"))

    res.passed = all([c1, c2, c3, c4])
    res.confidence = "高" if res.passed else "低"
    return res


# ============================================================
# C. 短线赚钱效应检查
# ============================================================
def check_money_effect(trade_date: str, cfg: dict) -> ModuleResult:
    """C. 短线赚钱效应。

    ★ 区分「涨停数量增加」和「实际赚钱效应改善」→ 不只看涨停数，
      同时要求炸板率、晋级率、昨日涨停股次日表现。
    """
    res = ModuleResult(name="短线赚钱效应")
    m = cfg["strong_uptrend"]["money_effect"]
    try:
        import market_regime_v10 as v10
        v10cfg = v10.load_config()
        snap = _get_snapshot(trade_date, cfg, v10cfg)
        lim = snap.limit
    except Exception as e:
        res.missing.append(f"情绪数据不可用: {e}")
        res.evidence.append("短线情绪数据缺失，无法判定")
        return res

    if lim.zt_count is None:
        res.missing.append("涨停数缺失")
        res.evidence.append("短线情绪数据缺失，无法判定")
        return res

    res.details = {
        "zt_count": lim.zt_count, "dt_count": lim.dt_count,
        "broken_rate": lim.broken_rate, "promotion_rate": lim.promotion_rate,
        "strong_stock_mean_pct": lim.strong_stock_mean_pct,
        "strong_stock_up_ratio": lim.strong_stock_up_ratio,
    }

    # ---------- 条件逐项判定（V10.4 反向指标纠正）----------
    # ★ V10.4 关键纠正：**连板晋级率是反向指标，不是正向门槛。**
    #
    #   原设计：「晋级率 ≥25%」作为进入 S6 的**必要条件**。
    #   实测（2026 全年 198 天，按晋级率分档看未来 20 日等权基准）：
    #       晋级率  0~10%:  未来20日均 -0.7%  正样本率 25%  加速率 38%
    #       晋级率 10~20%:  未来20日均 -1.5%  正样本率 12%  加速率 30%
    #       晋级率 20~30%:  未来20日均 -1.9%  正样本率 15%  加速率 30%
    #       晋级率 30~40%:  未来20日均 -3.1%  正样本率  0%  加速率 12%
    #   晋级率越高，未来越差 —— **情绪高潮就是短期顶部**。
    #   原设计等于「必须市场情绪高潮才敢满仓」，逻辑倒挂。
    #   该条件在 5 个标签 × 2 段共 10 格中有 8 格为负向（-5.4 ~ -13.9pp）。
    #   现改为**反向闸门**：晋级率过高（情绪过热）时不进入 S6。
    #
    #   其余调整：
    #     · 涨停 ≥40 → 移除（与广度模块重复，实测 8/10 格为负）
    #     · 跌停 ≤25 → 保留但实测为负向，改用宽口径（≤35）
    #     · 炸板率 ≤30% → 保留（负反馈指标，方向正确）
    #     · 强股均值 ≥0、强股上涨比 ≥50% → 保留（昨日涨停股今日表现）
    c1 = lim.dt_count is None or lim.dt_count <= m["dt_count_max"]
    res.evidence.append(
        (f"跌停 {lim.dt_count} 家（要求 ≤{m['dt_count_max']}）" if lim.dt_count is not None else "跌停数不可用")
        + ("✓" if c1 else "✗"))

    c2 = lim.broken_rate is None or lim.broken_rate <= m["broken_rate_max"]
    res.evidence.append(
        (f"炸板率 {lim.broken_rate:.1f}%（要求 ≤{m['broken_rate_max']:.0f}%）"
         if lim.broken_rate is not None else "炸板率不可用") + ("✓" if c2 else "✗"))

    # 反向闸门：情绪过热（晋级率过高）时拒绝进入 S6
    c3 = True
    promo_max = m.get("promotion_rate_max")
    if promo_max is not None and lim.promotion_rate is not None:
        c3 = lim.promotion_rate <= promo_max
        res.evidence.append(
            f"连板晋级率 {lim.promotion_rate:.1f}%（要求 ≤{promo_max:.0f}%，"
            f"过热=短期顶部）" + ("✓" if c3 else "✗"))

    c4 = lim.strong_stock_mean_pct is None or lim.strong_stock_mean_pct >= m["strong_stock_mean_pct_min"]
    res.evidence.append(
        (f"昨日涨停股今日均值 {lim.strong_stock_mean_pct:+.2f}%（要求 ≥{m['strong_stock_mean_pct_min']:+.1f}%）"
         if lim.strong_stock_mean_pct is not None else "强势股次日表现不可用") + ("✓" if c4 else "✗"))

    # ★ strong_stock_up_ratio 是 0~1 的**比例**（0.5 = 50%），
    #   配置阈值同为比例口径，比较时不可再乘 100。
    c5 = lim.strong_stock_up_ratio is None or lim.strong_stock_up_ratio >= m["strong_stock_up_ratio_min"]
    res.evidence.append(
        (f"昨日涨停股今日上涨比例 {lim.strong_stock_up_ratio*100:.1f}%"
         f"（要求 ≥{m['strong_stock_up_ratio_min']*100:.0f}%）"
         if lim.strong_stock_up_ratio is not None else "上涨比例不可用") + ("✓" if c5 else "✗"))

    res.passed = all([c1, c2, c3, c4, c5])
    res.confidence = "高" if res.passed else "低"
    return res


# ============================================================
# D. 主线持续性检查
# ============================================================
def _load_theme_rows(trade_date: str) -> Optional[List[tuple]]:
    """取某日主题评分行。

    优先读 sqlite（theme_scores.db）；sqlite 不可用或无该日数据时，
    回退读 CSV 快照（theme_scores_v2_YYYYMMDD.csv，覆盖 62 个交易日）。
    两者字段口径一致。找不到任何来源时返回 None（= UNKNOWN）。
    """
    cols = ("theme", "lifecycle", "days_strong", "mainline_type",
            "trend_score", "zt_count", "up_ratio", "leader_name")
    conn = _ro(THEME_SCORES_DB)
    if conn is not None:
        try:
            cur = conn.execute(
                "SELECT theme, lifecycle, days_strong, mainline_type, trend_score, "
                "zt_count, up_ratio, leader_name FROM theme_scores WHERE trade_date = ?",
                (trade_date,))
            got = cur.fetchall()
            if got:
                return got
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
    # 回退：CSV 快照
    csv_path = os.path.join(THEME_SCORES_DIR, f"theme_scores_v2_{trade_date}.csv")
    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path)
            if df.empty:
                return []
            need = list(cols)
            if not all(c in df.columns for c in need):
                return []
            out = []
            for _, r in df.iterrows():
                out.append(tuple(r.get(c) for c in need))
            return out
        except Exception:
            return None
    return None


def check_mainline(trade_date: str, cfg: dict) -> ModuleResult:
    """D. 主线持续性（V10.2：改用行业 ETF 走势判定）。

    ★ 为什么从「主题打分」改为「ETF 走势」：
      1. **覆盖完整**：theme_scores.db 只有 20260724 起（64/198 天），
         导致 S6 因 require_all_modules=true 而**结构性不可能**（主线模块
         134 天 UNKNOWN）。ETF 行情可回溯数年（1410 个交易日）→ 覆盖全年。
      2. **口径客观**：ETF 净值是市场真实成交价格，不受题材热度主观打分影响。
      3. **可回测**：ETF 走势是市场事实而非模型输出，可做样本外检验。

    ★ ETF 数据陷阱：ETF 会做**份额折算**，close 序列跳变 50~75%
      （实测 515880.SH 全年 close 法收益 -86.26%，实际仅 -15.93%）。
      etf_mainline 模块一律用 **pct_chg 累乘**构造复权净值，不直接用 close。

    ★ 降级策略：ETF 数据不可用时回退到 theme_scores.db（主题打分），
      并在 details 中标注 source，供上游判断置信度。
    """
    res = ModuleResult(name="主线持续性")
    mc = cfg["strong_uptrend"]["mainline"]

    source = mc.get("source", "etf")
    if source == "etf":
        try:
            import etf_mainline as _etf
            ecfg = _etf.load_config()
            panel = _etf.load_etf_panel()
            if panel is None or panel.empty:
                raise RuntimeError("ETF 行情缓存为空")
            r = _etf.detect_mainlines(str(trade_date), ecfg, panel)
            if r.get("missing"):
                raise RuntimeError(r.get("note") or "ETF 主线判定不可用")
            return _mainline_result_from_etf(res, r, mc)
        except Exception as e:
            res.evidence.append(f"ETF 主线判定不可用（{e}），回退主题打分")
            res.pipeline_gaps.append(f"ETF 主线不可用：{e}")

    # ---- 降级：主题打分口径 ----
    rows = _load_theme_rows(str(trade_date))
    if rows is None:
        res.missing.append(f"主题数据不可用（{trade_date}）")
        res.evidence.append("主题数据缺失，无法判定主线")
        return res
    if not rows:
        res.missing.append(f"主题数据无 {trade_date} 记录")
        res.evidence.append("主题数据缺失，无法判定主线")
        return res

    cands = []
    for (theme, lifecycle, days_strong, mtype, trend_score, zt_count, up_ratio, leader) in rows:
        if lifecycle not in mc["allowed_lifecycle"]:
            continue
        if mtype not in mc["allowed_mainline_type"]:
            continue
        if days_strong is None or days_strong < mc["min_days_strong"]:
            continue
        if trend_score is None or trend_score < mc["min_theme_trend_score"]:
            continue
        if zt_count is None or zt_count < mc["min_theme_zt_count"]:
            continue
        if up_ratio is None or up_ratio < mc["min_theme_up_ratio"]:
            continue
        cands.append({
            "theme": theme, "lifecycle": lifecycle, "days_strong": days_strong,
            "mainline_type": mtype, "trend_score": trend_score,
            "zt_count": zt_count, "up_ratio": up_ratio, "leader": leader,
        })

    res.details = {
        "source": "theme_scores",
        "theme_count": len(rows),
        "qualified_count": len(cands),
        "candidates": cands[:5],
    }

    if not cands:
        res.evidence.append(f"无主线候选（{len(rows)} 个主题全部未通过主线条件）")
        res.evidence.append(
            f"  条件：lifecycle∈{mc['allowed_lifecycle']} 且 mainline_type∈{mc['allowed_mainline_type']}"
            f" 且 days_strong≥{mc['min_days_strong']} 且 trend_score≥{mc['min_theme_trend_score']}")
        res.passed = False
        res.confidence = "中"     # 有数据但无候选 —— 判定是确定的
        return res

    for c in cands[:3]:
        res.evidence.append(
            f"{c['theme']}：{c['lifecycle']}｜连续强态 {c['days_strong']} 日｜"
            f"{c['mainline_type']}｜趋势分 {c['trend_score']:.1f}｜"
            f"涨停 {c['zt_count']} 家｜上涨宽度 {c['up_ratio']:.0f}%"
            + (f"｜龙头 {c['leader']}" if c["leader"] else ""))

    res.passed = len(cands) >= mc["min_mainline_count"]
    if not res.passed:
        res.evidence.append(f"主线数量 {len(cands)} < 要求 {mc['min_mainline_count']}")
    res.confidence = "高" if res.passed else "低"
    return res


def _mainline_result_from_etf(res: ModuleResult, r: dict, mc: dict) -> ModuleResult:
    """把 ETF 主线识别结果整理成 ModuleResult。"""
    inds = r.get("industries") or []
    det = r.get("details") or {}
    res.details = {
        "source": "etf",
        "industry_count": len(det),
        "qualified_count": len(inds),
        "industries": inds,
        "candidates": [
            {
                "industry": ind,
                "days_strong": det[ind].get("days_strong", 1),
                "excess20": det[ind].get("excess20"),
                "excess60": det[ind].get("excess60"),
                "ret20": det[ind].get("ret20"),
                "amt_ratio": det[ind].get("amt_ratio"),
                "above20_ratio": det[ind].get("above20_ratio"),
                "etf_count": det[ind].get("etf_count"),
            }
            for ind in inds
        ],
    }
    if not inds:
        # 有数据但无主线 —— 判定是确定的，不是缺失
        top = sorted(det.keys(),
                     key=lambda i: (det[i].get("excess20") or -999), reverse=True)[:3]
        if top:
            res.evidence.append(
                "无 ETF 主线（相对强度前 3：" + "、".join(
                    f"{t} 超额{det[t].get('excess20', 0):.1f}%" for t in top) + "）")
        else:
            res.evidence.append("无 ETF 主线")
        res.passed = False
        res.confidence = "中"
        return res

    for ind in inds[:3]:
        d = det[ind]
        res.evidence.append(
            f"{ind}：连续强态 {d.get('days_strong', 1)} 日｜"
            f"超额20日 {d.get('excess20', 0):.1f}%｜超额60日 {d.get('excess60', 0):.1f}%｜"
            f"近20日 {d.get('ret20', 0):.1f}%｜量能 {d.get('amt_ratio', 0):.2f}倍｜"
            f"行业内 {d.get('etf_count', 0)} 只 ETF 同向")

    res.passed = len(inds) >= mc.get("min_mainline_count", 1)
    if not res.passed:
        res.evidence.append(f"主线数量 {len(inds)} < 要求 {mc.get('min_mainline_count', 1)}")
    res.confidence = "高" if res.passed else "低"
    return res



# ============================================================
# S6 强势主升识别（组合四模块）
# ============================================================
@dataclass
class StrongUptrendResult:
    is_strong: bool = False
    modules: Dict[str, ModuleResult] = field(default_factory=dict)
    passed_count: int = 0
    required: int = 4
    evidence: List[str] = field(default_factory=list)
    uncertainties: List[str] = field(default_factory=list)
    confidence: str = "低"


def detect_strong_uptrend(trade_date: str, cfg: dict) -> StrongUptrendResult:
    """S6 强势主升识别：四个独立模块各自判定，不合并成单一总分。

    ★ S6 不能仅由指数上涨、单日放量或综合评分高触发 →
      四模块必须全部通过（require_all_modules=true 时）。
    """
    out = StrongUptrendResult()
    out.modules = {
        "指数趋势": check_index_trend(trade_date, cfg),
        "市场广度": check_breadth(trade_date, cfg),
        "短线赚钱效应": check_money_effect(trade_date, cfg),
        "主线持续性": check_mainline(trade_date, cfg),
    }
    out.passed_count = sum(1 for m in out.modules.values() if m.passed)
    need = cfg.get("min_modules_passed", 4)
    if cfg["strong_uptrend"].get("require_all_modules", True):
        need = len(out.modules)
    out.required = need

    for name, m in out.modules.items():
        status = "✓" if m.passed else "✗"
        out.evidence.append(f"[{status}] {name}（置信度{m.confidence}）")
        for e in m.evidence:
            out.evidence.append(f"      {e}")
        if m.missing:
            out.uncertainties.append(f"{name}：{'; '.join(m.missing)}")

    out.is_strong = out.passed_count >= need
    out.confidence = "高" if out.is_strong and not out.uncertainties else (
        "中" if out.is_strong else "低")
    return out


# ============================================================
# limit_stats 历史（涨跌停/炸板率的日度序列）
# ============================================================
LIMIT_STATS_DB = os.path.join(BASE_DIR, 'cache_backbone_tushare', 'market_analysis.db')


def load_limit_stats(trade_date: str) -> Dict[str, Any]:
    """读取 market_analysis.db 的 limit_stats 行（当日 + 前一交易日）。"""
    out = {"broken_rate": None, "prev_broken_rate": None,
           "dt_count": None, "prev_dt_count": None}
    conn = _ro(LIMIT_STATS_DB)
    if conn is None:
        return out
    try:
        cur = conn.execute(
            "SELECT trade_date, broken_rate, dt_count FROM limit_stats "
            "WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 2", (str(trade_date),))
        rows = cur.fetchall()
    except Exception:
        rows = []
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if not rows:
        return out
    # rows[0] 是当日（若当日缺失则 rows[0] 就是前一日 → 只填 prev）
    if rows[0][0] == str(trade_date):
        out["broken_rate"] = rows[0][1]
        out["dt_count"] = rows[0][2]
        if len(rows) > 1:
            out["prev_broken_rate"] = rows[1][1]
            out["prev_dt_count"] = rows[1][2]
    else:
        out["prev_broken_rate"] = rows[0][1]
        out["prev_dt_count"] = rows[0][2]
    return out


# ============================================================
# 个股机会匹配（复用原有个股选股系统，不重新设计买卖信号）
# ============================================================
@dataclass
class StockCandidate:
    """一只候选股及其风险特征。"""
    ts_code: str = ""
    name: str = ""
    strategy_id: str = ""
    signal: str = ""
    action: str = ""
    score: Optional[float] = None
    close: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    industry: str = ""
    concept: str = ""
    stop_distance: Optional[float] = None      # (close - stop) / close，比例
    risk_reward: Optional[float] = None        # (target - close) / (close - stop)
    liquidity_amount: Optional[float] = None   # 成交额（万元）
    category: str = "不满足买入条件"            # 合格买入信号 / 条件观察信号 / 不满足买入条件


@dataclass
class OpportunityResult:
    qualified: List[StockCandidate] = field(default_factory=list)
    observe: List[StockCandidate] = field(default_factory=list)
    rejected: List[StockCandidate] = field(default_factory=list)
    data_available: bool = False
    note: str = ""

    @property
    def qualified_count(self) -> int:
        return len(self.qualified)


def load_opportunities(trade_date: str, cfg: dict) -> OpportunityResult:
    """读取原有个股选股系统的当日信号，分类为合格/观察/不满足。

    ★ 完全复用 picks_db/stock_picks.db 的信号定义，不重新设计买卖信号。
    ★ 只统计短线策略（horizon=short）；horizon=mid 的中线仓单独统计，不计入短线可部署仓位。
    """
    out = OpportunityResult()
    rules = cfg["qualified_signal_rules"]

    conn = _ro(PICKS_DB)
    if conn is None:
        out.note = "stock_picks.db 不可用"
        return out

    # 取策略的 horizon（用于排除中线仓）
    horizons = {}
    try:
        for r in conn.execute("SELECT strategy_id, horizon, enabled FROM strategy").fetchall():
            horizons[r[0]] = (r[1], r[2])
    except Exception:
        pass

    try:
        rows = conn.execute(
            "SELECT p.ts_code, p.stock_name, p.strategy_id, p.signal, p.action, p.score, "
            "p.close, p.stop_price, p.target_price, p.industry, p.concept, p.indicators "
            "FROM stock_pick p WHERE p.pick_date = ?", (str(trade_date),)).fetchall()
    except Exception as e:
        out.note = f"选股查询失败: {e}"
        conn.close()
        return out
    conn.close()

    if not rows:
        out.note = f"{trade_date} 无选股信号记录"
        return out

    out.data_available = True
    buy_actions = set(rules["buy_actions"])
    observe_actions = set(rules["observe_actions"])
    exclude_actions = set(rules["exclude_actions"])

    for (code, name, sid, signal, action, score, close, stop, target,
         industry, concept, indicators) in rows:
        act = (action or '').strip()
        sig = (signal or '').strip()
        hor, enabled = horizons.get(sid, (None, 1))

        # 中线仓排除
        if rules.get("exclude_horizon_mid") and hor == 'mid':
            continue

        c = StockCandidate(
            ts_code=code or '', name=name or '', strategy_id=sid or '',
            signal=sig, action=act, score=score, close=close,
            stop_price=stop, target_price=target,
            industry=(industry or '').strip(), concept=(concept or '').strip())

        # 止损距离与风险收益比
        if close and stop and stop > 0 and close > stop:
            c.stop_distance = (close - stop) / close
        if (close and stop and target and close > stop and target > close):
            c.risk_reward = (target - close) / (close - stop)

        # 流动性（从 indicators 取成交额）
        if indicators:
            try:
                import json as _json
                ind = _json.loads(indicators) if isinstance(indicators, str) else indicators
                if isinstance(ind, dict):
                    for k in ('amount', '成交额', 'amount_yi', '总成交额'):
                        if k in ind:
                            v = ind.get(k)
                            try:
                                c.liquidity_amount = float(v)
                            except Exception:
                                pass
                            break
            except Exception:
                pass

        # 分类
        if act in exclude_actions:
            c.category = "不满足买入条件"
        elif act in buy_actions:
            c.category = "合格买入信号"
        elif act in observe_actions:
            c.category = "条件观察信号"
        else:
            # 未在名单里的动作：保守处理 —— 只在明确含 BUY 语义时算合格
            if 'BUY' in act.upper() and 'WAIT' not in act.upper() and 'NO_CHASE' not in act.upper():
                c.category = "合格买入信号"
            elif '观察' in act or 'WATCH' in act or '等待' in act or '跟踪' in act:
                c.category = "条件观察信号"
            else:
                c.category = "不满足买入条件"

        if c.category == "合格买入信号":
            out.qualified.append(c)
        elif c.category == "条件观察信号":
            out.observe.append(c)
        else:
            out.rejected.append(c)

    out.qualified.sort(key=lambda x: (-(x.score or 0)))
    out.observe.sort(key=lambda x: (-(x.score or 0)))
    return out


# ============================================================
# 仓位权限（市场环境允许持有的最高仓位）
# ============================================================
@dataclass
class PositionAuthority:
    """仓位权限：市场环境允许的最高仓位。与实际建议仓位严格分离。"""
    state: str = STATE_NORMAL
    max_position: int = 60
    action: str = ""
    allow_probe: bool = False
    allow_enter_strong: bool = False
    note: str = ""


def position_authority(state: str, cfg: dict) -> PositionAuthority:
    """按市场状态给出仓位权限。

    ★ 市场允许持有 100%，不代表系统必须持有 100%。
    """
    pa = PositionAuthority()
    pa.state = state
    spec = cfg["position_authority"].get(state, cfg["position_authority"]["S1"])
    pa.max_position = int(spec.get("max_position", 60))
    pa.action = spec.get("action", "")
    pa.allow_probe = bool(spec.get("allow_probe", False))
    pa.allow_enter_strong = (state == STATE_STRONG)
    if state == STATE_STRONG:
        pa.note = "S6 强势主升：仓位上限可放开至 100%，但仍需满仓授权 + 组合风险检查"
    return pa


# ============================================================
# 组合风险预算
# ============================================================
@dataclass
class PortfolioRisk:
    """组合风险预算使用情况。"""
    total_risk_pct: float = 0.0            # 各持仓潜在止损损失合计（占净值 %）
    risk_budget_pct: float = 0.0            # 预算上限
    budget_usage: float = 0.0               # 使用率 0~1
    industry_pct: Dict[str, float] = field(default_factory=dict)
    theme_pct: Dict[str, float] = field(default_factory=dict)
    max_industry_pct: float = 0.0
    max_theme_pct: float = 0.0
    cash_floor_max: float = 90.0        # 现金最低保留对应的仓位上限
    cash_floor_ok: bool = True
    violations: List[str] = field(default_factory=list)
    ok: bool = True                     # 组合风险本身是否合格（不含现金约束）
    used_proxy: bool = False            # ★ V10.2：是否使用了 ETF 波动率代理（无候选标的）
    proxy_stop_distance: Optional[float] = None  # 代理止损距离


def check_portfolio_risk(candidates: List[StockCandidate], total_capital_pct: float,
                         cfg: dict, state: Optional[str] = None,
                         authority=None,
                         fallback_stop_distance: Optional[float] = None) -> PortfolioRisk:
    """计算组合风险预算使用情况。

    单股风险金额 = 持仓市值 × 止损距离比例（含成本与滑点）。
    ★ 组合风险预算与目标仓位冲突时，**优先遵守组合风险预算**。
    ★ V10.2：预算按市场状态分档（risk_budget_by_state），
      传入 state 时按该档计算；不传则用基准值。

    ★ V10.2 选股数据去耦：当**没有候选标的**时（选股数据缺失/滞后），
      用 `fallback_stop_distance`（由 ETF 波动率代理得出）估算组合风险，
      而不是直接判定「无法计算」。这样仓位建议不再被选股数据可用性绑架。
    """
    pr = cfg["portfolio_risk"]
    out = PortfolioRisk()
    out.risk_budget_pct = pr["max_total_risk_pct"]
    if state:
        budget = _risk_budget_for_state(state, authority, pr)
        if budget:
            out.risk_budget_pct = budget

    cost = pr.get("cost_pct", 0.0)
    slip = pr.get("slippage_pct", 0.0) if pr.get("include_slippage") else 0.0
    extra = (cost + slip) / 100.0

    # 单只最大仓位
    per_stock_cap = pr["max_single_stock_pct"]
    if total_capital_pct > 0 and candidates:
        n = max(1, len(candidates))
        raw = total_capital_pct / n
        per_stock_cap = min(per_stock_cap, raw)

    industry_w: Dict[str, float] = {}
    theme_w: Dict[str, float] = {}
    total_risk = 0.0

    if candidates:
        for c in candidates:
            # 单股权重（等权，受单股上限约束）
            w = min(per_stock_cap, total_capital_pct / max(1, len(candidates)))
            if c.industry:
                industry_w[c.industry] = industry_w.get(c.industry, 0.0) + w
            if c.concept:
                theme_w[c.concept] = theme_w.get(c.concept, 0.0) + w
            if c.stop_distance is not None:
                total_risk += w * (c.stop_distance + extra)
    elif fallback_stop_distance:
        # ★ 无候选标的 → 用 ETF 波动率代理的典型止损距离，
        #   按「最大可接受单股权重」估算组合总风险（保守口径）。
        w = min(per_stock_cap, total_capital_pct) if total_capital_pct > 0 else 0.0
        total_risk = w * (fallback_stop_distance + extra)
        out.used_proxy = True
        out.proxy_stop_distance = round(fallback_stop_distance, 4)

    out.total_risk_pct = round(total_risk, 3)
    out.budget_usage = round(total_risk / out.risk_budget_pct, 3) if out.risk_budget_pct else 0.0
    out.industry_pct = {k: round(v, 2) for k, v in sorted(
        industry_w.items(), key=lambda x: -x[1])}
    out.theme_pct = {k: round(v, 2) for k, v in sorted(
        theme_w.items(), key=lambda x: -x[1])}
    out.max_industry_pct = max(industry_w.values()) if industry_w else 0.0
    out.max_theme_pct = max(theme_w.values()) if theme_w else 0.0

    # 违规检查
    if out.total_risk_pct > out.risk_budget_pct:
        out.violations.append(
            f"组合总风险 {out.total_risk_pct:.2f}% 超出预算 {out.risk_budget_pct:.2f}%")
        out.ok = False
    for k, v in out.industry_pct.items():
        if v > pr["max_single_industry_pct"]:
            out.violations.append(
                f"行业「{k}」集中度 {v:.1f}% 超出上限 {pr['max_single_industry_pct']:.1f}%")
            out.ok = False
    for k, v in out.theme_pct.items():
        if v > pr["max_single_theme_pct"]:
            out.violations.append(
                f"主题「{k}」集中度 {v:.1f}% 超出上限 {pr['max_single_theme_pct']:.1f}%")
            out.ok = False
    if out.max_theme_pct > pr["max_correlated_group_pct"]:
        out.violations.append(
            f"高相关组集中度 {out.max_theme_pct:.1f}% 超出 {pr['max_correlated_group_pct']:.1f}%")
        out.ok = False

    # ★ 现金最低保留不在这里判定为「违规」。
    #   原因：仓位 100% 与「必须保留 10% 现金」在逻辑上直接矛盾，
    #   若在此处判违规，则满仓授权永远不可能通过（配置自相矛盾）。
    #   现金约束的正确位置是「实际建议仓位」的上限（见 calculate_target_position）。
    out.cash_floor_max = 100 - pr["min_cash_pct"]
    out.cash_floor_ok = total_capital_pct <= out.cash_floor_max
    if not out.cash_floor_ok:
        out.violations.append(
            f"仓位 {total_capital_pct:.0f}% 超过现金最低保留 {pr['min_cash_pct']:.0f}% "
            f"对应的上限 {out.cash_floor_max:.0f}%")

    return out


# ============================================================
# 满仓授权
# ============================================================
@dataclass
class FullAuthorization:
    """满仓授权结论。"""
    result: str = FULL_DENIED
    reason_cn: str = ""
    failed_conditions: List[str] = field(default_factory=list)
    passed_conditions: List[str] = field(default_factory=list)
    missing_data: List[str] = field(default_factory=list)
    checks: Dict[str, bool] = field(default_factory=dict)
    pipeline_gaps: List[str] = field(default_factory=list)
    """上游数据管道缺口（如选股策略未写目标价）。
    ★ 与 missing_data 区分：管道缺口不等于「核心数据缺失」，
      不应因此否决满仓授权（否则数据管道问题会锁死整个仓位体系）。"""
    info: str = ""
    """信息性说明（不参与授权判定）。★ V10.2：用于承载已移出授权的选股信息。"""


def full_position_authorization(
        state: str,
        index_trend: ModuleResult,
        breadth: ModuleResult,
        money_effect: ModuleResult,
        mainline: ModuleResult,
        opportunities: OpportunityResult,
        portfolio: PortfolioRisk,
        cfg: dict) -> FullAuthorization:
    """满仓授权检查。

    ★ 十项必要条件必须全部满足才返回 FULL_ALLOWED。
    ★ 数据缺失 → FULL_REVIEW 或 FULL_DENIED，**不得默认通过**。
    ★ 返回 ALLOWED 也**不等于**要买满 —— 实际建议仓位仍由 calculate_target_position 决定。
    """
    fa = FullAuthorization()
    auth = cfg["full_position_authorization"]
    pr = cfg["portfolio_risk"]

    # ---- 1. 市场状态必须为 S6 ----
    c1 = (state == STATE_STRONG)
    fa.checks["市场状态为S6"] = c1
    (fa.passed_conditions if c1 else fa.failed_conditions).append(
        f"市场状态为 S6（当前 {state} {STATE_CN.get(state, state)}）")

    # ---- 2. 指数趋势 ----
    c2 = index_trend.passed
    fa.checks["指数趋势检查通过"] = c2
    (fa.passed_conditions if c2 else fa.failed_conditions).append(
        f"指数趋势检查{'通过' if c2 else '未通过'}")
    if index_trend.missing:
        fa.missing_data.extend(index_trend.missing)

    # ---- 3. 市场广度持续改善 ----
    c3 = breadth.passed
    fa.checks["市场广度持续改善"] = c3
    (fa.passed_conditions if c3 else fa.failed_conditions).append(
        f"市场广度检查{'通过' if c3 else '未通过'}")
    if breadth.missing:
        fa.missing_data.extend(breadth.missing)

    # ---- 4. 短线赚钱效应 ----
    c4 = money_effect.passed
    fa.checks["短线赚钱效应确认"] = c4
    (fa.passed_conditions if c4 else fa.failed_conditions).append(
        f"短线赚钱效应{'确认' if c4 else '未确认'}")
    if money_effect.missing:
        fa.missing_data.extend(money_effect.missing)

    # ---- 5. 主线持续性 ----
    c5 = mainline.passed
    fa.checks["主线持续性通过"] = c5
    (fa.passed_conditions if c5 else fa.failed_conditions).append(
        f"主线持续性{'通过' if c5 else '未通过'}")
    if mainline.missing:
        fa.missing_data.extend(mainline.missing)

    # ---- 6. ★ V10.2：合格个股数量 —— 已移出满仓授权 ----
    #   授权是**环境层面**的判断（这个市场允不允许满仓），
    #   「今天有几只合格票」属于执行层面，不该否决环境授权。
    #   保留为 informational，不参与 checks。
    nq = opportunities.qualified_count
    fa.info = (f"当前合格买入信号 {nq} 只"
               f"（不参与满仓授权判定，仅供建仓参考）")

    # ---- 7. ★ V10.2：个股买点质量 —— 已移出满仓授权 ----
    #   原判定依赖个股止损价/目标价（来自选股库）：
    #     · 止损距离覆盖率 82.2%、风险收益比覆盖率仅 25.8%
    #     · 「最小 rr ≥ 1.2」用单只异常标的否决全场
    #     · 缺字段还会污染第 10 项「核心数据完整」→ 双重锁死 100% 仓位
    #   现改为：止损距离的合理性改由**组合风险预算**（ETF 波动率代理）把关，
    #   满仓授权只保留市场环境层面的五项 + 数据完整性。
    q_all = opportunities.qualified
    sd_list = [c.stop_distance for c in q_all if c.stop_distance is not None]
    if sd_list:
        max_sd = max(sd_list)
        fa.info += f"；候选股最大止损距离 {max_sd*100:.1f}%"

    c7 = True   # 不再作为授权项


    # ---- 8. 组合风险检查 ----
    # ★ V10.2：组合风险必须用**代理口径**判定，否则授权结果会随「今天有几只票」变化。
    #   原实现直接用 portfolio.ok（基于候选股实际止损价），
    #   实测同一状态下合格标的 0 只 → 授权 ALLOWED、5 只 → 授权 DENIED（风险 6.15%>6%）。
    #   现改为：若 portfolio 已带代理止损距离，则按代理口径重算风险并比较预算。
    _proxy_sd = getattr(portfolio, "proxy_stop_distance", None)
    if _proxy_sd is None:
        _proxy_sd = _default_proxy_stop_distance()
    if _proxy_sd is not None:
        _pr_cfg = cfg.get("portfolio_risk", {})
        _per_stock = min(_pr_cfg.get("max_single_stock_pct", 15.0),
                         100.0)
        _extra = (_pr_cfg.get("cost_pct", 0.0)
                  + (_pr_cfg.get("slippage_pct", 0.0)
                     if _pr_cfg.get("include_slippage") else 0.0)) / 100.0
        _proxy_risk = round(_per_stock * (_proxy_sd + _extra), 3)
        _proxy_budget = portfolio.risk_budget_pct or _pr_cfg.get("max_total_risk_pct", 3.0)
        c8 = _proxy_risk <= _proxy_budget
        fa.checks["组合风险检查通过"] = c8
        if c8:
            fa.passed_conditions.append(
                f"组合风险 { _proxy_risk:.2f}% / 预算 {_proxy_budget:.2f}%"
                f"（代理止损 {_proxy_sd*100:.1f}%）")
        else:
            fa.failed_conditions.append(
                f"组合风险 {_proxy_risk:.2f}% 超出预算 {_proxy_budget:.2f}%"
                f"（代理止损 {_proxy_sd*100:.1f}%）")
    else:
        c8 = portfolio.ok
        fa.checks["组合风险检查通过"] = c8
        if c8:
            fa.passed_conditions.append(
                f"组合风险 {portfolio.total_risk_pct:.2f}% / 预算 {portfolio.risk_budget_pct:.2f}%"
                f"（使用率 {portfolio.budget_usage*100:.0f}%）")
        else:
            fa.failed_conditions.extend(portfolio.violations or ["组合风险检查未通过"])

    # ---- 9. 账户流动性需求允许提高仓位 ----
    c9 = True
    if auth.get("min_account_liquidity_ok", True):
        c9 = True   # 账户层面参数在组合风险里已体现（现金最低比例）
    fa.checks["账户流动性允许"] = c9
    fa.passed_conditions.append("账户流动性检查（现金最低保留比例）通过")

    # ---- 10. 核心数据完整 ----
    c10 = not fa.missing_data
    fa.checks["核心数据完整"] = c10
    if c10:
        fa.passed_conditions.append("核心数据完整，无关键指标缺失")
    else:
        fa.failed_conditions.append(
            f"关键数据缺失：{'; '.join(fa.missing_data[:3])}")

    # ---- 汇总 ----
    critical = ["市场状态为S6", "指数趋势检查通过", "市场广度持续改善",
                "短线赚钱效应确认", "主线持续性通过", "核心数据完整"]
    critical_failed = [k for k in critical if not fa.checks.get(k, False)]

    if fa.missing_data and not c10:
        fa.result = FULL_REVIEW
        fa.reason_cn = "关键数据缺失，证据不足，需进一步观察"
    elif critical_failed:
        fa.result = FULL_DENIED
        fa.reason_cn = f"关键条件未满足：{'、'.join(critical_failed)}"
    elif auth.get("require_all_conditions", True):
        if all(fa.checks.values()):
            fa.result = FULL_ALLOWED
            fa.reason_cn = "十项必要条件全部满足，允许目标仓位达到 100%"
        else:
            failed = [k for k, v in fa.checks.items() if not v]
            fa.result = FULL_DENIED
            fa.reason_cn = f"条件未全部满足：{'、'.join(failed)}"
    else:
        if all(fa.checks.get(k, False) for k in critical):
            fa.result = FULL_ALLOWED
            fa.reason_cn = "关键条件全部满足，允许目标仓位达到 100%"
        else:
            fa.result = FULL_DENIED
            fa.reason_cn = f"关键条件未满足：{'、'.join(critical_failed)}"

    return fa


# ============================================================
# 实际建议仓位计算（权限 ∩ 机会 ∩ 组合风险）
# ============================================================
@dataclass
class TargetPosition:
    """实际建议仓位。三个概念严格分离。"""
    state: str = STATE_NORMAL
    authority_max: int = 60              # ① 仓位权限：市场环境允许的最高仓位
    suggested: int = 0                    # ② 实际建议仓位（系统建议，非必须买入）
    current_position: Optional[int] = None  # ③ 当前实际仓位（外部提供，未提供为 None）
    add_room: Optional[int] = None        # 可加仓空间（实际仓位未提供 → None = UNKNOWN）
    limited_by: List[str] = field(default_factory=list)   # 限制原因
    qualified_count: int = 0
    observe_count: int = 0
    opportunity_note: str = ""
    portfolio: Optional[PortfolioRisk] = None
    authorization: Optional[FullAuthorization] = None
    action: str = ""                     # 操作建议
    note: str = ""


def _risk_budget_for_state(state: str, authority, pr: dict) -> Optional[float]:
    """按市场状态返回允许的组合总风险预算（占净值百分比）。

    ★ V10.2 新增：把「账户级风险预算」与「市场环境」联动。

    背景：原实现 max_total_risk_pct 固定 3%，实测把仓位天花板锁死在 30~40% ——
    单股止损距离均值 6%、单股上限 15% 时，每只贡献约 0.9% 风险，
    3% 预算最多支撑 3~4 只满仓股（40~60% 仓位）。
    这与「市场是否允许高仓位」无关，却成了主观感受里
    「60% 以上升仓位没到位」的主因。

    现在按权限分档（可在配置 risk_budget_by_state 覆盖）：
      S3 恐慌   → 1.0%  （几乎清仓，只留极小试错）
      S4 衰减   → 1.5%
      S2 退潮   → 2.0%
      S5 修复   → 3.5%
      S1 正常   → 4.5%
      S6 主升   → 6.0%  （配合满仓授权才真正可用）
    """
    table = pr.get("risk_budget_by_state") or {}
    if state in table:
        return float(table[state])
    # 未配置时按权限线性插值：权限 20% → 1.5%，权限 100% → 6%
    mx = float(pr.get("max_total_risk_pct", 3.0))
    auth = getattr(authority, "max_position", 60) or 60
    ratio = max(0.0, min(1.0, (auth - 20) / 80.0))
    return round(1.5 + (mx - 1.5) * ratio if mx > 1.5 else mx, 2)


_PROXY_SD_CACHE = {}


def _default_proxy_stop_distance() -> Optional[float]:
    """取市场典型止损距离（ETF 波动率代理），带进程内缓存。

    ★ 这是「仓位计算不依赖选股数据」的关键：
      组合风险预算需要止损距离，原本取自个股止损价（来自选股库）；
      改用行业 ETF 的日均绝对涨幅作为代理，任何时候都能算，
      且与「今天有几只合格票」完全无关。
    """
    if "sd" in _PROXY_SD_CACHE:
        return _PROXY_SD_CACHE["sd"]
    sd = None
    try:
        import etf_mainline as _etf
        v = _etf.estimate_market_volatility()
        sd = v.get("stop_distance")
    except Exception:
        sd = None
    if sd is None:
        # ETF 数据不可用时的兜底：配置里的基准值
        try:
            cfg = load_config()
            vol = cfg.get("volatility", {})
            sd = float(vol.get("fallback_stop_distance", 0.05))
        except Exception:
            sd = 0.05
    _PROXY_SD_CACHE["sd"] = sd
    return sd


def calculate_target_position(state: str, authority: PositionAuthority,
                             opportunities: OpportunityResult,
                             portfolio: PortfolioRisk,
                             authorization: FullAuthorization,
                             current_position: Optional[int],
                             cfg: dict) -> TargetPosition:
    """计算实际建议仓位。

    ★ 实际建议仓位**不得超过市场仓位权限**。
    ★ 合格买点不足时不强行买入 —— 市场允许 100% 但机会不足时，建议仓位仍应低。
    ★ 组合风险预算优先于目标仓位。
    """
    tp = TargetPosition()
    tp.state = state
    tp.authority_max = authority.max_position
    tp.qualified_count = opportunities.qualified_count
    tp.observe_count = len(opportunities.observe)
    tp.opportunity_note = opportunities.note
    tp.portfolio = portfolio
    tp.authorization = authorization
    tp.current_position = current_position

    pr = cfg["portfolio_risk"]
    auth_cfg = cfg.get("full_position_authorization", {})
    nq = opportunities.qualified_count

    # --- 上限 1：仓位权限 ---
    cap = authority.max_position

    # --- 上限 2：★ V10.2 已移除「合格机会数量」这一上限 ---
    #   原实现：无合格买点 → cap=0；买点少于 min_qualified_for_deployment → 按比例缩减。
    #   这让**选股数据的可用性直接决定仓位建议**：
    #     · stock_picks.db 只覆盖 20260601 起 → 1~5 月 132 天建议仓位恒为 0；
    #     · 环境判断明明给出 60% 权限，却因「今天没选出票」被压到 0。
    #   职责划分：环境模块回答「这个市场该拿多少仓」，选股模块回答「买哪几只」。
    #   两者不应互相绑架 —— 环境判断不该因为选股库当天没跑就失真。
    #   现改为：选股数量只在报告与分级加仓中作为提示，不参与仓位上限计算。
    if not opportunities.data_available:
        tp.limited_by.append("选股数据不可用（不影响环境仓位建议）")
    elif nq == 0:
        tp.limited_by.append("当前无合格买入信号（环境仓位建议不变，实际建仓另行判断）")
    else:
        tp.limited_by.append(f"合格买点 {nq} 只（环境仓位建议不因机会数量调整）")

    # --- 上限 3：组合风险预算（★ V10.2 完全脱离选股数据）---
    #   设计要点：
    #   · 风险敞口 = 仓位权限 × 典型止损距离（ETF 波动率代理）
    #     —— 不再依赖候选股的实际止损价，也就不依赖「今天有几只票」。
    #   · 风险预算按市场状态分档（risk_budget_by_state）：
    #     市场环境越好，允许承担的风险越高（与仓位权限同向）。
    #   · 之前的实现是「先按候选股算风险，再放宽预算」，逻辑绕且受选股影响：
    #     同一状态下，合格标的 0 只 vs 5 只会得出不同的建议仓位（60% vs 43%）。
    budget = _risk_budget_for_state(state, authority, pr)
    proxy_sd = getattr(portfolio, "proxy_stop_distance", None)
    if not proxy_sd:
        # portfolio 未带代理值时自行获取（保证函数独立可用、结果与选股无关）
        proxy_sd = _default_proxy_stop_distance()
    if proxy_sd:
        # 用代理口径重算：单股权重取「最大可接受单股权重」，保守但与选股无关
        per_stock = min(pr.get("max_single_stock_pct", 15.0),
                        authority.max_position)
        extra = (pr.get("cost_pct", 0.0)
                 + (pr.get("slippage_pct", 0.0) if pr.get("include_slippage") else 0.0)) / 100.0
        proxy_risk = round(per_stock * (proxy_sd + extra), 3)
        portfolio = copy.deepcopy(portfolio)
        portfolio.total_risk_pct = proxy_risk
        portfolio.risk_budget_pct = budget
        portfolio.budget_usage = (round(proxy_risk / budget, 3) if budget else 0.0)
        portfolio.used_proxy = True
        portfolio.proxy_stop_distance = proxy_sd
        portfolio.violations = [v for v in (portfolio.violations or [])
                                if "总风险" not in v]
        portfolio.ok = proxy_risk <= budget
        if not portfolio.ok:
            portfolio.violations.append(
                f"组合总风险 {proxy_risk:.2f}% 超出预算 {budget:.2f}%")
        tp.limited_by.append(
            f"{STATE_CN.get(state, state)} 风险预算 {budget:.1f}%"
            f"（代理止损 {proxy_sd*100:.1f}%，敞口 {proxy_risk:.2f}%）")
    else:
        portfolio.risk_budget_pct = budget
        portfolio.budget_usage = (round(portfolio.total_risk_pct / budget, 3)
                                   if budget else 0.0)

    if not portfolio.ok:
        # 组合风险超限时按比例缩减
        if portfolio.risk_budget_pct > 0:
            scale = portfolio.risk_budget_pct / max(portfolio.total_risk_pct, 1e-9)
            scale = min(1.0, scale)
        else:
            scale = 0.0
        cap = int(cap * scale)
        tp.limited_by.append(
            f"组合风险超限（{portfolio.total_risk_pct:.2f}%/{portfolio.risk_budget_pct:.2f}%），"
            f"目标仓位按 {scale*100:.0f}% 缩减")
        for v in (portfolio.violations or [])[:3]:
            tp.limited_by.append(v)


    # --- 上限 4：满仓授权（只影响能否到 100%，不影响低于 100% 的仓位）---
    if authorization.result != FULL_ALLOWED and cap >= 100:
        # ★ V10.2：改为配置化，并区分「待复核」与「明确否决」。
        #   原实现硬编码：FULL_REVIEW→99、其余→60。后者过于粗暴 ——
        #   在 S1(权限60%) 场景下把仓位压到 60 看似无害，
        #   但在 S6 场景下会把「否决」直接等同于「只能 6 成」，
        #   使权限阶梯失去意义。现改为按配置给出降级上限。
        review_cap = auth_cfg.get("cap_when_review", 99)
        denied_cap = auth_cfg.get("cap_when_denied", 80)
        cap = review_cap if authorization.result == FULL_REVIEW else denied_cap
        tp.limited_by.append(f"满仓授权未通过（{authorization.reason_cn}），目标仓位上限受限")

    # --- 上限 5：现金最低保留（决定实际仓位上限，而非否决满仓授权）---
    max_by_cash = portfolio.cash_floor_max if portfolio else (100 - pr["min_cash_pct"])
    if cap > max_by_cash:
        cap = max_by_cash
        tp.limited_by.append(
            f"现金最低保留 {pr['min_cash_pct']:.0f}%，目标仓位上限 {max_by_cash:.0f}%")

    tp.suggested = max(0, min(int(cap), 100))

    # --- 可加仓空间 ---
    if current_position is not None:
        tp.add_room = max(0, tp.suggested - current_position)
    else:
        tp.add_room = None   # UNKNOWN

    # --- 操作建议 ---
    if current_position is None:
        tp.action = "维持/按建议执行" if tp.suggested > 0 else "空仓观望"
    elif current_position > tp.suggested:
        tp.action = "降低仓位"
    elif tp.add_room is not None and tp.add_room > 0:
        tp.action = "允许试探性加仓" if tp.suggested <= 20 else "允许提高仓位"
    else:
        tp.action = "保持仓位"

    # --- 机会不足的提示（仅提示，不再限制仓位）---
    # ★ V10.2：选股数量不再参与仓位计算，这里只做执行层面的提醒。
    if opportunities.data_available and nq == 0 and authority.max_position >= 60:
        tp.note = ("环境层面允许的仓位较高，但当前无合格买入信号 —— "
                   "这是执行层面的提示，不改变环境仓位建议。")
    elif authority.max_position >= 100 and authorization.result == FULL_ALLOWED \
            and opportunities.data_available and nq < 2:
        tp.note = ("满仓授权已通过，但当前合格信号偏少，"
                   "建议分批建仓而非一次性打满。")

    return tp


# ============================================================
# 分级加仓
# ============================================================
@dataclass
class LadderResult:
    """分级加仓建议。"""
    current_step: int = 0                 # 当前所在阶梯（索引）
    next_step: Optional[int] = None       # 下一阶梯目标仓位
    can_add: bool = False
    reason: str = ""
    checks: Dict[str, bool] = field(default_factory=dict)
    max_single_step_pct: int = 20
    info: str = ""
    """★ V10.2：提示性信息（不参与加仓闸门判定）。"""


def plan_ladder_add(current_position: Optional[int], target: TargetPosition,
                    state: str, authorization: FullAuthorization,
                    portfolio: PortfolioRisk, opportunities: OpportunityResult,
                    cfg: dict) -> LadderResult:
    """分级加仓：决定是否允许从当前仓位往上加一级。

    ★ 不允许一次性从低仓位直接跳到 100%。
    ★ 加仓由**新的交易信号与风险检查**触发，不由固定时间间隔触发。
    """
    lad = cfg["ladder"]
    steps = lad["steps"]
    out = LadderResult()
    out.max_single_step_pct = lad.get("max_single_step_pct", 20)

    if current_position is None:
        out.reason = "未提供当前实际仓位，无法规划加仓阶梯"
        out.can_add = False
        return out

    # 定位当前阶梯
    cur = 0
    for i, s in enumerate(steps):
        if current_position >= s:
            cur = i
    out.current_step = cur

    if cur >= len(steps) - 1:
        out.reason = "已在最高阶梯（100%），无加仓空间"
        out.can_add = False
        return out

    next_target = steps[cur + 1]
    out.next_step = next_target

    # 单次加仓幅度上限
    if next_target - current_position > out.max_single_step_pct:
        next_target = current_position + out.max_single_step_pct
        out.next_step = next_target

    # ---- 加仓前的六项检查 ----
    c1 = (state == STATE_STRONG or state == STATE_REPAIR or state == STATE_NORMAL)
    out.checks["市场状态维持"] = c1

    c2 = (authorization.result == FULL_ALLOWED
          or authorization.result == FULL_REVIEW
          or state != STATE_STRONG)
    out.checks["满仓授权条件未恶化"] = c2

    # ★ V10.2：c3「出现有效买点」—— 选股数据已移出仓位计算，
    #   此项不再作为**加仓闸门**（否则选股库当天没跑就无法加仓）。
    #   改为提示性信息：仓位阶梯由环境决定，执行时若无合格标的则自然建不了仓。
    c3 = True
    out.checks["选股信号可用（提示项）"] = c3
    out.info = (f"当前合格买点 {opportunities.qualified_count} 只"
                f"（不影响加仓阶梯判定，执行时需自行确认标的）")

    c4 = portfolio.ok
    out.checks["组合风险仍合格"] = c4

    c5 = True   # 主线检查在 S6 判定中已包含；非 S6 状态不额外限制
    out.checks["主线未发生变化"] = c5

    c6 = target.suggested >= next_target
    out.checks["目标仓位支持本级"] = c6

    failed = [k for k, v in out.checks.items() if not v]
    if failed:
        out.can_add = False
        out.reason = f"加仓检查未通过：{'、'.join(failed)}"
    else:
        out.can_add = True
        out.reason = (f"允许从 {current_position}% 加至 {next_target}%"
                      f"（阶梯 {steps[cur]}→{steps[cur+1]}）")

    return out


# ============================================================
# 满仓退出检查
# ============================================================
@dataclass
class ExitResult:
    """满仓退出检查结论。"""
    result: str = EXIT_HOLD
    reason_cn: str = ""
    triggered: List[str] = field(default_factory=list)
    severity: str = "低"
    suggestion: str = ""


def full_position_exit_check(state: str, prev_state: Optional[str],
                             breadth_ratio: Optional[float],
                             prev_breadth_ratio: Optional[float],
                             dt_count: Optional[int], prev_dt_count: Optional[int],
                             broken_rate: Optional[float], prev_broken_rate: Optional[float],
                             mainline_ok: bool, core_holdings_risk_count: int = 0,
                             portfolio_ok: bool = True, data_missing: bool = False,
                             cfg: dict = None) -> ExitResult:
    """满仓退出检查：市场转弱时收回满仓权限。

    ★ 市场环境恶化不等于所有股票必须同步卖出 → 按严重程度分级。
    ★ 本模块只生成建议，**不擅自执行交易**。
    """
    ec = cfg["exit_check"] if cfg else {}
    out = ExitResult()

    if data_missing:
        out.result = EXIT_HOLD
        out.severity = "中"
        out.reason_cn = "关键行情数据缺失，无法评估满仓权限，保持但停止加仓"
        out.triggered.append("关键数据缺失")
        out.suggestion = "停止加仓，核实数据后再评估"
        return out

    triggers = []
    severity_level = 0   # 0=无 1=停止加仓 2=降低仓位 3=处理失效持仓

    # 1. 状态由 S6 退回
    if prev_state == STATE_STRONG and state != STATE_STRONG:
        triggers.append(f"市场状态由 S6 退回 {state} {STATE_CN.get(state, state)}")
        severity_level = max(severity_level, 2)

    # 2. 广度持续恶化
    if (breadth_ratio is not None and prev_breadth_ratio is not None):
        drop = (prev_breadth_ratio - breadth_ratio) * 100
        if drop >= ec.get("breadth_drop_pp", 10.0):
            triggers.append(f"市场广度下降 {drop:.1f}pp（前 {prev_breadth_ratio*100:.1f}% → 今 {breadth_ratio*100:.1f}%）")
            severity_level = max(severity_level, 2)

    # 3. 跌停显著增加
    if dt_count is not None and prev_dt_count:
        ratio = dt_count / prev_dt_count
        if ratio >= ec.get("dt_increase_ratio", 1.5) or (dt_count - prev_dt_count) >= ec.get("dt_abs_increase", 20):
            triggers.append(f"跌停家数 {prev_dt_count}→{dt_count}（放大 {ratio:.2f} 倍）")
            severity_level = max(severity_level, 2)

    # 4. 炸板率显著恶化
    if broken_rate is not None and prev_broken_rate is not None:
        up = broken_rate - prev_broken_rate
        if up >= ec.get("broken_rate_increase_pp", 15.0):
            triggers.append(f"炸板率上升 {up:.1f}pp（前 {prev_broken_rate:.1f}% → 今 {broken_rate:.1f}%）")
            severity_level = max(severity_level, 2)

    # 5. 主线持续性破坏
    if ec.get("mainline_broken", True) and not mainline_ok:
        triggers.append("主线持续性被破坏")
        severity_level = max(severity_level, 1)

    # 6. 多只核心持仓同时出现风险信号
    if core_holdings_risk_count >= ec.get("core_holdings_risk_count", 3):
        triggers.append(f"{core_holdings_risk_count} 只核心持仓同时出现风险信号")
        severity_level = max(severity_level, 3)

    # 7. 组合风险超预算
    if ec.get("risk_budget_overflow", True) and not portfolio_ok:
        triggers.append("组合风险超出预算")
        severity_level = max(severity_level, 2)

    out.triggered = triggers
    if severity_level == 0:
        out.result = EXIT_HOLD
        out.severity = "低"
        out.reason_cn = "未触发满仓退出条件，维持满仓权限"
        out.suggestion = "维持当前仓位，按既定策略执行"
    elif severity_level == 1:
        out.result = EXIT_HOLD
        out.severity = "中"
        out.reason_cn = "出现风险信号但未达降仓标准：" + "；".join(triggers)
        out.suggestion = "暂时维持仓位，但**停止加仓**，等待下一日确认"
    elif severity_level == 2:
        out.result = EXIT_REDUCE
        out.severity = "高"
        out.reason_cn = "市场明显转弱，收回满仓权限：" + "；".join(triggers)
        out.suggestion = "降低仓位至风险档位对应的上限，优先减风险暴露最大的持仓"
    else:
        out.result = EXIT_INVALID
        out.severity = "高"
        out.reason_cn = "个股交易逻辑已失效：" + "；".join(triggers)
        out.suggestion = "处理交易逻辑已失效的持仓（**注意：环境恶化不等于所有股票同步卖出**）"

    return out


# ============================================================
# 主流程：整合 V10.0 状态机 + V10.1 动态仓位
# ============================================================
def decide_state_with_strong(trade_date: str, cfg: dict,
                             v10_decision=None) -> Tuple[str, Any]:
    """在 V10.0 五状态机基础上叠加 S6。

    ★ S6 优先级高于 S5（修复确认）—— 强势主升比修复确认更强。
    ★ S6 的进入/退出也走连续天数确认（与其它状态一致）。
    ★ 但**进入 S6 不等于满仓授权**，满仓是独立的十项检查。
    """
    import market_regime_v10 as v10

    if v10_decision is None:
        v10cfg = v10.load_config()
        snap = _get_snapshot(trade_date, cfg, v10cfg)
        node = v10.detect_market_nodes(snap, v10cfg)
        prev = v10.load_prev_state(str(trade_date))
        v10_decision = v10.decide_state(snap, node, prev, v10cfg)

    state = v10_decision.state
    strong = detect_strong_uptrend(trade_date, cfg)

    # S6 需要连续确认
    sm = cfg.get("state_machine", {})
    need = sm.get("confirm_days", {}).get(STATE_STRONG, 2)
    exit_days = sm.get("exit_days", {}).get(STATE_STRONG, 2)

    prev = None
    try:
        import market_regime_v10 as v10
        prev = v10.load_prev_state(str(trade_date))
    except Exception:
        prev = None

    prev_pending_target = (prev or {}).get("pending_target")
    prev_pending_days = (prev or {}).get("pending_days") or 0

    if strong.is_strong:
        if state == STATE_STRONG:
            return STATE_STRONG, v10_decision
        hits = (prev_pending_days + 1) if prev_pending_target == STATE_STRONG else 1
        if hits >= need:
            d = v10.RegimeDecision()
            d.state = STATE_STRONG
            d.state_cn = STATE_CN[STATE_STRONG]
            d.prev_state = state
            d.consecutive_days = 1
            d.changed = True
            d.confidence = strong.confidence
            d.reasons.append(f"强势主升四模块全部通过（{strong.passed_count}/{strong.required}），进入 S6")
            d.triggers = strong.evidence[:3]
            return STATE_STRONG, d
        # 未确认 → 维持原状态，但记录待确认
        v10_decision.pending_target = STATE_STRONG
        v10_decision.pending_days = hits
        v10_decision.reasons.append(
            f"强势主升信号第{hits}次出现（需连续{need}日确认），暂维持 {state}")
        return state, v10_decision

    # S6 退出：连续 exit_days 日不满足才真正退出
    if state == STATE_STRONG:
        exit_streak = (prev_pending_days + 1) if prev_pending_target == "EXIT" else 1
        if exit_streak >= exit_days:
            v10_decision.state = STATE_REPAIR
            v10_decision.state_cn = STATE_CN[STATE_REPAIR]
            v10_decision.consecutive_days = 1
            v10_decision.changed = True
            v10_decision.reasons.append(
                f"连续{exit_streak}日不满足强势主升条件，退出 S6 回到 S5")
            v10_decision.confidence = "中"
            return STATE_REPAIR, v10_decision
        v10_decision.pending_target = "EXIT"
        v10_decision.pending_days = exit_streak
        v10_decision.reasons.append(
            f"强势主升条件不再满足（需连续{exit_days}日确认退出），暂维持 S6")
        return STATE_STRONG, v10_decision

    return state, v10_decision


@dataclass
class V101Result:
    trade_date: str = ""
    state: str = STATE_NORMAL
    state_cn: str = ""
    state_days: int = 1
    state_confidence: str = "低"
    state_reasons: List[str] = field(default_factory=list)
    strong: Optional[StrongUptrendResult] = None
    authority: Optional[PositionAuthority] = None
    opportunities: Optional[OpportunityResult] = None
    portfolio: Optional[PortfolioRisk] = None
    authorization: Optional[FullAuthorization] = None
    target: Optional[TargetPosition] = None
    ladder: Optional[LadderResult] = None
    exit_check: Optional[ExitResult] = None
    prev_state: Optional[str] = None
    snapshot: Any = None
    v10_decision: Any = None
    market_volatility: Optional[dict] = None
    """★ V10.2：市场波动率代理（ETF 口径），组合风险预算的止损距离来源。
    使仓位计算不依赖选股数据。"""


def analyze_trade_date_v101(trade_date: str, cfg: dict = None,
                            current_position: Optional[int] = None,
                            persist: bool = True) -> V101Result:
    """V10.1 完整链路。

    市场快照 → 节点 → 状态机(含S6) → 仓位权限 → 个股机会 → 组合风险
    → 满仓授权 → 实际建议仓位 → 分级加仓 → 满仓退出检查
    """
    cfg = cfg or load_config()
    out = V101Result()
    out.trade_date = str(trade_date)

    import market_regime_v10 as v10
    v10cfg = v10.load_config()

    # 1) 市场快照与 V10.0 状态机
    snap = _get_snapshot(trade_date, cfg, v10cfg)
    out.snapshot = snap
    node = v10.detect_market_nodes(snap, v10cfg)
    prev = v10.load_prev_state(str(trade_date))
    out.prev_state = prev.get("state") if prev else None
    v10_dec = v10.decide_state(snap, node, prev, v10cfg)

    # 2) S6 判定与状态机叠加
    state, dec = decide_state_with_strong(str(trade_date), cfg, v10_dec)
    out.state = state
    out.state_cn = STATE_CN.get(state, state)
    out.state_days = dec.consecutive_days
    out.state_confidence = dec.confidence
    out.state_reasons = dec.reasons
    out.v10_decision = dec

    # 3) 强势主升四模块（无论是否进 S6 都输出，便于复核）
    out.strong = detect_strong_uptrend(str(trade_date), cfg)

    # 4) 仓位权限
    out.authority = position_authority(state, cfg)

    # 5) 个股机会（★ V10.2：仅用于报告展示与执行提示，不参与仓位计算）
    out.opportunities = load_opportunities(str(trade_date), cfg)

    # 5b) 市场波动率代理（ETF 口径）—— 组合风险预算的止损距离来源，
    #     使风险计算**不依赖选股数据**
    try:
        import etf_mainline as _etf
        _vol = _etf.estimate_market_volatility(str(trade_date), _etf.load_config())
    except Exception:
        _vol = {"stop_distance": None, "daily_range": None, "sample": 0}
    out.market_volatility = _vol
    _proxy_sd = _vol.get("stop_distance")

    # 6) 组合风险（先按权限上限估算，再用实际建议仓位复算）
    #    ★ V10.2：传入 state 与 authority，使风险预算按市场状态分档
    #    ★ V10.2：无候选标的时用 ETF 波动率代理估算，不再「无法计算」
    prelim = check_portfolio_risk(out.opportunities.qualified,
                                   out.authority.max_position, cfg,
                                   state=state, authority=out.authority,
                                   fallback_stop_distance=_proxy_sd)
    out.portfolio = prelim

    # 7) 满仓授权
    out.authorization = full_position_authorization(
        state,
        out.strong.modules.get("指数趋势") or ModuleResult("指数趋势"),
        out.strong.modules.get("市场广度") or ModuleResult("市场广度"),
        out.strong.modules.get("短线赚钱效应") or ModuleResult("短线赚钱效应"),
        out.strong.modules.get("主线持续性") or ModuleResult("主线持续性"),
        out.opportunities, prelim, cfg)

    # 8) 实际建议仓位
    out.target = calculate_target_position(
        state, out.authority, out.opportunities, prelim,
        out.authorization, current_position, cfg)

    # 9) 用实际建议仓位复算组合风险（更准确）
    if out.target.suggested > 0:
        final_pr = check_portfolio_risk(out.opportunities.qualified,
                                        out.target.suggested, cfg,
                                        state=state, authority=out.authority,
                                        fallback_stop_distance=_proxy_sd)
        out.portfolio = final_pr
        out.target.portfolio = final_pr

    # 10) 分级加仓
    out.ladder = plan_ladder_add(current_position, out.target, state,
                                 out.authorization, out.portfolio,
                                 out.opportunities, cfg)

    # 11) 满仓退出检查
    lim = snap.limit
    ls = load_limit_stats(str(trade_date))
    prev_dt = lim.prev_dt_count if lim.prev_dt_count is not None else ls["prev_dt_count"]
    prev_zb = ls["prev_broken_rate"]
    out.exit_check = full_position_exit_check(
        state=state, prev_state=out.prev_state,
        breadth_ratio=snap.breadth.breadth_ratio,
        prev_breadth_ratio=snap.breadth.prev_breadth_ratio,
        dt_count=lim.dt_count if lim.dt_count is not None else ls["dt_count"],
        prev_dt_count=prev_dt,
        broken_rate=lim.broken_rate if lim.broken_rate is not None else ls["broken_rate"],
        prev_broken_rate=prev_zb,
        mainline_ok=out.strong.modules.get("主线持续性").passed
        if out.strong.modules.get("主线持续性") else False,
        portfolio_ok=out.portfolio.ok,
        data_missing=bool(out.strong.uncertainties),
        cfg=cfg)

    # 12) 持久化状态（供次日状态链读取：状态/连续天数/待确认计数）
    if persist:
        try:
            import market_regime_v10 as v10
            # ★ V10.5 修复：必须传 vote_history。
            #   原实现漏传 → save_state 的 vote_history 默认为 None → 写库为 []。
            #   后果：滑动窗口多数决的投票链**每天被清空**，
            #   次日 decide_state 拿到的 prev_votes 永远是空列表，
            #   窗口内永远只有「今日 1 票」→ min_votes=2 永远达不到
            #   → 状态机几乎永远停在 prev_state。
            #   实测 2026-11-02：10/30 应投 S3 票却存 []，
            #   导致 11/02 的窗口只有 ["S4"] 1 票 → 维持 S3 → 权限 5%。
            v10.save_state(str(trade_date), dec, node.node_type,
                           getattr(dec, "vote_history", None))
        except Exception as e:
            print(f"[V10.1] 状态保存失败: {e}")

    return out


# ============================================================
# 报告渲染
# ============================================================
def render_v101_report(r: V101Result, cfg: dict) -> str:
    L = []
    L.append("═" * 60)
    L.append("      A股动态仓位管理  V10.1")
    L.append("═" * 60)
    L.append(f"交易日：{r.trade_date}")

    # ---------- 【市场状态】 ----------
    L.append("")
    L.append("【市场状态】")
    L.append(f"  当前状态：{r.state} {r.state_cn}")
    L.append(f"  状态持续时间：{r.state_days} 个交易日")
    L.append(f"  状态置信度：{r.state_confidence}")
    for i, s in enumerate(r.state_reasons[:3], 1):
        L.append(f"  核心证据{i}：{s}")

    # ---------- S6 四模块 ----------
    if r.strong:
        L.append("")
        L.append("【强势主升四模块】(各自独立判定，不合并为总分)")
        for name, m in r.strong.modules.items():
            mark = "✓通过" if m.passed else "✗未通过"
            L.append(f"  [{mark}] {name}（置信度{m.confidence}）")
            for e in m.evidence[:4]:
                L.append(f"        {e}")
        if r.strong.uncertainties:
            L.append(f"  不确定因素：{'; '.join(r.strong.uncertainties[:3])}")

    # ---------- 当前主线（ETF 口径） ----------
    _ml_mod = (r.strong.modules.get("主线持续性") if r.strong else None)
    if _ml_mod is not None and _ml_mod.details:
        _d = _ml_mod.details
        if _d.get("source") == "etf":
            L.append("")
            _inds = _d.get("industries") or []
            L.append("【当前主线】(行业ETF走势口径)")
            if _inds:
                for _c in (_d.get("candidates") or [])[:3]:
                    L.append(
                        f"  ★ {_c.get('industry')}：连续强态 {_c.get('days_strong', 1)} 日｜"
                        f"超额20日 {_c.get('excess20', 0):.1f}%｜"
                        f"超额60日 {_c.get('excess60', 0):.1f}%｜"
                        f"近20日 {_c.get('ret20', 0):.1f}%｜"
                        f"量能 {_c.get('amt_ratio', 0):.2f}倍")
            else:
                L.append("  无主线（相对强度居前行业均未同时满足趋势/资金/宽度）")

    # ---------- 【仓位权限】 ----------
    if r.authority:
        L.append("")
        L.append("【仓位权限】")
        L.append(f"  当前市场允许的最高仓位：{r.authority.max_position}%")
        L.append(f"  是否允许进入S6：{'是' if r.authority.allow_enter_strong else '否'}")
        if r.authorization:
            L.append(f"  满仓授权：{r.authorization.result}（{FULL_CN.get(r.authorization.result, '')}）")
            L.append(f"  授权理由：{r.authorization.reason_cn}")
            if r.authorization.info:
                L.append(f"  补充信息：{r.authorization.info}")
            if r.authorization.failed_conditions:
                L.append("  未通过的具体原因：")
                for c in r.authorization.failed_conditions[:5]:
                    L.append(f"    · {c}")

    # ---------- 【仓位计算】 ----------
    if r.target:
        t = r.target
        L.append("")
        L.append("【仓位计算】")
        L.append(f"  当前实际仓位：{t.current_position if t.current_position is not None else '未提供'}")
        L.append(f"  当前目标仓位（建议）：{t.suggested}%")
        L.append(f"  仓位权限上限：{t.authority_max}%")
        L.append(f"  当前可加仓空间：{t.add_room if t.add_room is not None else 'UNKNOWN（未提供实际仓位）'}")
        # ★ V10.2：选股数量不再参与仓位计算，标注为仅供参考
        L.append(f"  合格买入股票数量：{t.qualified_count} 只（条件观察 {t.observe_count} 只）"
                 f"　※ 仅供参考，不参与仓位建议计算")
        if r.market_volatility and r.market_volatility.get("stop_distance"):
            mv = r.market_volatility
            L.append(f"  市场波动率代理（ETF）：日均绝对涨幅 {mv['daily_range']*100:.2f}%"
                     f"（样本 {mv['sample']} 只）→ 典型止损距离 {mv['stop_distance']*100:.1f}%")
        if t.portfolio:
            p = t.portfolio
            L.append(f"  组合风险预算：{p.total_risk_pct:.2f}% / {p.risk_budget_pct:.2f}%"
                     f"（使用率 {p.budget_usage*100:.0f}%）")
            if p.used_proxy:
                L.append(f"    ※ 无候选标的，使用 ETF 波动率代理止损距离 "
                         f"{p.proxy_stop_distance*100:.1f}%")
            if p.industry_pct:
                top = list(p.industry_pct.items())[:3]
                L.append("  行业集中度：" + "、".join(f"{k} {v:.1f}%" for k, v in top))
            if p.theme_pct:
                top = list(p.theme_pct.items())[:3]
                L.append("  主题集中度：" + "、".join(f"{k} {v:.1f}%" for k, v in top))
        if t.limited_by:
            L.append("  仓位限制原因：")
            for c in t.limited_by[:5]:
                L.append(f"    · {c}")
        if t.note:
            L.append(f"  ⚠ {t.note}")

    # ---------- 【操作建议】 ----------
    L.append("")
    L.append("【操作建议】")
    L.append(f"  {r.target.action if r.target else '—'}")
    if r.ladder:
        L.append(f"  分级加仓：{'允许' if r.ladder.can_add else '停止'}"
                 f"（当前阶梯 {r.ladder.current_step}）")
        L.append(f"    {r.ladder.reason}")
    if r.exit_check:
        L.append(f"  满仓退出检查：{r.exit_check.result}（{EXIT_CN.get(r.exit_check.result, '')}）")
        L.append(f"    {r.exit_check.reason_cn}")
        if r.exit_check.suggestion:
            L.append(f"    建议：{r.exit_check.suggestion}")

    # ---------- 一句话 ----------
    L.append("")
    L.append("─" * 60)
    tgt = r.target.suggested if r.target else 0
    auth_txt = (FULL_CN.get(r.authorization.result, '') if r.authorization else '')
    L.append(f"▎{r.state} {r.state_cn}｜权限上限 {r.authority.max_position if r.authority else 0}%"
             f"｜建议仓位 {tgt}%｜满仓授权：{auth_txt}")
    L.append("─" * 60)
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="A股动态仓位管理 V10.1")
    ap.add_argument("trade_date", nargs="?", help="交易日 YYYYMMDD，默认最近交易日")
    ap.add_argument("--config", help="配置文件路径")
    ap.add_argument("--current-position", type=int, help="当前实际仓位（%%）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    cfg = load_config(args.config)
    td = args.trade_date or datetime.datetime.now().strftime('%Y%m%d')
    r = analyze_trade_date_v101(td, cfg, args.current_position)

    if args.json:
        print(json.dumps({
            "trade_date": r.trade_date,
            "state": {"code": r.state, "name": r.state_cn, "days": r.state_days,
                      "confidence": r.state_confidence},
            "authority": {"max_position": r.authority.max_position} if r.authority else {},
            "strong_modules": {
                k: {"passed": v.passed, "confidence": v.confidence, "evidence": v.evidence}
                for k, v in (r.strong.modules.items() if r.strong else [])
            },
            "opportunities": {
                "qualified": r.opportunities.qualified_count if r.opportunities else 0,
                "observe": len(r.opportunities.observe) if r.opportunities else 0,
            },
            "portfolio": {
                "total_risk_pct": r.portfolio.total_risk_pct if r.portfolio else None,
                "budget_usage": r.portfolio.budget_usage if r.portfolio else None,
                "ok": r.portfolio.ok if r.portfolio else None,
            },
            "authorization": {
                "result": r.authorization.result if r.authorization else None,
                "reason": r.authorization.reason_cn if r.authorization else None,
                "failed": r.authorization.failed_conditions if r.authorization else [],
            },
            "target": {
                "suggested": r.target.suggested if r.target else None,
                "authority_max": r.target.authority_max if r.target else None,
                "add_room": r.target.add_room if r.target else None,
                "action": r.target.action if r.target else None,
                "limited_by": r.target.limited_by if r.target else [],
            },
            "ladder": {"can_add": r.ladder.can_add, "reason": r.ladder.reason} if r.ladder else {},
            "exit_check": {
                "result": r.exit_check.result if r.exit_check else None,
                "triggered": r.exit_check.triggered if r.exit_check else [],
            },
        }, ensure_ascii=False, indent=2))
    else:
        print(render_v101_report(r, cfg))


if __name__ == "__main__":
    main()
