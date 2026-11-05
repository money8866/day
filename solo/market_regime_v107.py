# -*- coding: utf-8 -*-
"""V10.7 增量优化 —— 量价口径 / S4卖压衰减确认 / S5反弹确认 / 结构性机会例外 / 仓位语义分离

=========================================
设计原则：**增量，不重构**
  · V10.0（market_regime_v10.py）与 V10.1（market_regime_v101.py）**一行不改**，
    本模块只做「读取 + 叠加」：
      V10.7 = V10.1 的完整决策记录 ＋ 三层新增确认 ＋ 状态门控 ＋ 仓位语义分离
  · 状态机投票链、节点识别、S6 四模块、满仓授权逻辑全部沿用 V10.0/V10.1；
    V10.7 的门控只作用在**状态升级方向**（更保守），不改写原模块。

新增的四层：
  ① 量价判定（AmountVerdict）
     成交额必须同时输出：环比变化率 / 相对5日均变化率 / 指数涨跌幅 / 上涨股票占比。
     ★ 禁止仅凭「成交额高于5日均量」判定放量上涨 ——
       放量上涨 = 放量 ∧ 指数收涨 ∧ 上涨占比达标；指数涨但广度差 → 「放量滞涨」。

  ② S4 卖压衰减确认（check_easing_confirmation）
     跌停家数连续变化 / 下跌家数连续变化 / 上涨占比连续变化 /
     涨停跌停比 / 成交额变化 —— 五项独立判定，缺一即 FAIL，缺数据即 UNKNOWN。

  ③ S5 反弹确认（check_rebound_confirmation）
     上涨占比持续改善 / 跌停明显下降 / 涨停跌停比改善 /
     成交额与指数方向匹配 / 主线持续性赚钱效应 —— 单日普涨不算 S5。

  ④ STRUCTURAL_ALPHA_EXCEPTION（check_structural_alpha）
     S3/S4/S6 下识别「弱市中的结构性强势行业」：
       入池：连续强势≥15日 ∧ 20日超额≥10% ∧ 60日趋势未恶化
             ∧ 成分股广度达标 ∧ 排除单只权重股拉升
       复核：成分股业绩基本面 ∧ 行业内上涨家数占比 ∧ 领涨股趋势 ∧ 成交额持续性
     ★ 只允许在原有市场仓位上限内申请授权，不突破状态权限。

仓位语义分离（PositionPlan）：
  目标仓位 / 建议区间 / 仓位硬上限 / 主动加仓权限 / 满仓授权 —— 五者分列。
  ★ 目标仓位是**环境层面的参考值**，不是必须达到的持仓比例；
    S4 目标 20% 但无合格标的时，实际仓位可以低于 20%，甚至保持现金。

独立运行：
  python market_regime_v107.py 20261103
  python market_regime_v107.py 20261103 --json
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

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import market_regime_v10 as v10
import market_regime_v101 as v101
import market_micro as micro
import position_p0_fix as p0fix
import position_optimizer as posopt
import swing_gate
import bull_latch

CONFIG_PATH = os.path.join(BASE_DIR, "config_v107_refine.json")

# 状态常量（直接复用 V10.1，避免两套常量漂移）
STATE_NORMAL = v101.STATE_NORMAL
STATE_DRAWDOWN = v101.STATE_DRAWDOWN
STATE_PANIC = v101.STATE_PANIC
STATE_EASING = v101.STATE_EASING
STATE_REPAIR = v101.STATE_REPAIR
STATE_STRONG = v101.STATE_STRONG
STATE_CN = v101.STATE_CN

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_UNKNOWN = "UNKNOWN"

VERDICT_CN = {VERDICT_PASS: "通过", VERDICT_FAIL: "未通过", VERDICT_UNKNOWN: "数据不足"}


# ============================================================
# 配置
# ============================================================
def load_config(path: str = None) -> dict:
    """合并配置：V10.0 → V10.1 → V10.7。"""
    cfg = v101.load_config()
    p = path or CONFIG_PATH
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                user = json.load(f)
            for k, v in user.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    cfg[k].update(v)
                else:
                    cfg[k] = v
        except Exception:
            pass
    return cfg


# ============================================================
# ① 量价判定
# ============================================================
@dataclass
class AmountVerdict:
    """量价四要素 + 形态判定。"""
    trade_date: str = ""
    amount_yi: Optional[float] = None            # 当日全市场成交额（亿元）
    prev_amount_yi: Optional[float] = None       # 前一交易日成交额
    chg_vs_prev: Optional[float] = None          # ① 环比成交额变化率 %
    ma5_amount_yi: Optional[float] = None        # 前5日均额（不含当日）
    chg_vs_ma5: Optional[float] = None           # ② 相对5日均成交额变化率 %
    index_chg: Optional[float] = None            # ③ 指数涨跌幅 %
    up_ratio: Optional[float] = None             # ④ 上涨股票占比
    up_count: Optional[int] = None
    down_count: Optional[int] = None
    expand: bool = False
    shrink: bool = False
    pattern: str = VERDICT_UNKNOWN                # 放量上涨/放量滞涨/缩量上涨/放量下跌/缩量下跌/成交额持平
    is_volume_up: bool = False                   # ★ 真正的「放量上涨」（三要素齐备）
    evidence: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)


def amount_verdict(trade_date: str, cfg: dict = None,
                   series: List[Dict[str, Any]] = None) -> AmountVerdict:
    """量价判定。

    ★ 核心约束：**不得仅凭成交额高于5日均量就判定放量上涨**。
      「放量上涨」必须三要素齐备：
        ① 相对5日均额 > +expand_threshold
        ② 核心指数（中证2000）收涨
        ③ 上涨股票占比 ≥ breadth_sync_min
      指数涨但广度不足 → 判为「放量滞涨（个股分化）」，不算放量上涨。

    series: 可选，盘中传入「历史序列 + 盘中合成行」，避免直接改写全局缓存。
    """
    cfg = cfg or load_config()
    ac = cfg.get("amount_volume", {})
    out = AmountVerdict(trade_date=str(trade_date))

    seq = series if series is not None else micro.load_series(trade_date, 12)
    if not seq:
        out.missing.append("市场微结构序列不可用（先跑 market_micro.build）")
        out.evidence.append("成交额/广度数据缺失 → 量价判定 UNKNOWN")
        return out

    cur = seq[-1]
    if str(cur['trade_date']) != str(trade_date):
        out.missing.append(
            f"微结构序列末行 {cur['trade_date']} ≠ 目标 {trade_date}")
        out.evidence.append("当日数据未就绪 → 量价判定 UNKNOWN")
        return out

    out.amount_yi = cur['amount_yi']
    out.up_count = cur['up_count']
    out.down_count = cur['down_count']
    out.up_ratio = cur['up_ratio']
    out.index_chg = cur['index_chg']

    # 环比成交额变化率（前一交易日）
    if len(seq) >= 2 and seq[-2].get('amount_yi'):
        out.prev_amount_yi = seq[-2]['amount_yi']
        out.chg_vs_prev = round(
            (out.amount_yi / out.prev_amount_yi - 1) * 100, 2)

    # 相对5日均额变化率（均额**不含当日**）
    w = int(ac.get("ma_window", 5))
    prev_amts = [s['amount_yi'] for s in seq[-(w + 1):-1]
                 if s.get('amount_yi') is not None]
    if len(prev_amts) >= w:
        out.ma5_amount_yi = round(float(np.mean(prev_amts)), 2)
        if out.ma5_amount_yi:
            out.chg_vs_ma5 = round(
                (out.amount_yi / out.ma5_amount_yi - 1) * 100, 2)

    if out.index_chg is None:
        out.missing.append("核心指数当日涨跌幅不可用")

    # ---- 放量 / 缩量 ----
    exp_th = float(ac.get("expand_threshold_pct", 2.0))
    shr_th = float(ac.get("shrink_threshold_pct", -2.0))
    if out.chg_vs_ma5 is None:
        out.pattern = VERDICT_UNKNOWN
        out.evidence.append("5日均额不可用 → 无法判定放量/缩量")
    else:
        out.expand = out.chg_vs_ma5 >= exp_th
        out.shrink = out.chg_vs_ma5 <= shr_th

    # ---- 形态 ----
    idx_up = (out.index_chg is not None
              and out.index_chg > float(ac.get("index_up_threshold_pct", 0.0)))
    breadth_ok = (out.up_ratio is not None
                  and out.up_ratio >= float(ac.get("breadth_sync_min", 0.5)))

    if out.chg_vs_ma5 is None:
        out.pattern = VERDICT_UNKNOWN
    elif out.expand and idx_up and breadth_ok:
        out.pattern = "放量上涨"
        out.is_volume_up = True
    elif out.expand and idx_up and not breadth_ok:
        out.pattern = "放量滞涨（指数涨、个股分化）"
    elif out.expand and not idx_up:
        out.pattern = "放量下跌"
    elif out.shrink and idx_up:
        out.pattern = "缩量上涨"
    elif out.shrink and not idx_up:
        out.pattern = "缩量下跌"
    else:
        out.pattern = "成交额与5日均持平"

    out.evidence.append(
        f"成交额 {out.amount_yi:,.0f} 亿｜环比 "
        + (f"{out.chg_vs_prev:+.1f}%" if out.chg_vs_prev is not None else "UNKNOWN")
        + f"｜相对5日均 "
        + (f"{out.chg_vs_ma5:+.1f}%" if out.chg_vs_ma5 is not None else "UNKNOWN")
        + f"｜指数 {out.index_chg:+.2f}%" if out.index_chg is not None else "")
    out.evidence.append(
        f"上涨股票占比 {out.up_ratio*100:.1f}%（{out.up_count}↑ / {out.down_count}↓）")
    out.evidence.append(f"形态判定：{out.pattern}"
                        + ("（放量↑ + 指数↑ + 广度达标，三要素齐备）"
                           if out.is_volume_up else
                           "（未同时满足 放量↑/指数↑/广度达标，不计为放量上涨）"))
    return out


# ============================================================
# ② / ③ 确认类结果
# ============================================================
@dataclass
class ConfirmationResult:
    """确认类判定结果：PASS / FAIL / UNKNOWN。"""
    name: str = ""
    verdict: str = VERDICT_UNKNOWN
    checks: Dict[str, bool] = field(default_factory=dict)
    evidence: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.verdict == VERDICT_PASS


def _decay(seq: List[Optional[float]], increasing: bool) -> bool:
    """「衰减」口径：今日相对昨日**改善**，且相对前日也**改善**。

    ★ 为什么不用严格连续下降：
      暴跌后的 V 形修复日（如 2026-11-02）典型序列为
        跌停家数 [51, 95, 10]、上涨占比 [14.2%, 16.3%, 85.1%]
      严格「连续2日下降」会要求 51>95>10 → **不成立**，
      于是一个「跌停从 95 只骤降到 10 只、上涨占比从 16% 跳到 85%」
      的典型卖压衰减日反而被判 FAIL —— 与设计意图完全相反。

      「衰减」口径：今日 < 昨日 且 今日 < 前日（下跌家数/跌停家数方向）；
                    今日 > 昨日 且 今日 > 前日（上涨占比方向）。
      它表达的是「卖压在退潮且今天仍在退潮」，同时不会把
      「低位单日脉冲」误判为连续改善（后者仍需前两日也在改善）。

    严格口径（_consecutive）仍可通过配置切换，两种口径都在测试中覆盖。
    """
    if len(seq) < 3:
        return False
    a, b, c = seq[-3], seq[-2], seq[-1]
    if a is None or b is None or c is None:
        return False
    if increasing:
        return (c > b) and (c > a)
    return (c < b) and (c < a)


def _check_trend(seq: List[Optional[float]], need: int, increasing: bool,
                 mode: str = "decay") -> bool:
    """按配置选择「衰减」或「严格连续」口径。"""
    if mode == "strict":
        return _consecutive(seq, need, increasing)
    return _decay(seq, increasing)


def _series_of(micro_series: List[Dict[str, Any]], offset: int) -> Optional[Dict]:
    """取序列倒数第 offset+1 个元素（offset=0 为最新日）。

    ★ off-by-one 注意：offset=2 时需要至少 3 个元素 → len >= 3 合法。
    """
    idx = -(offset + 1)
    if len(micro_series) < abs(idx):
        return None
    return micro_series[idx]


def _consecutive(seq: List[Optional[float]], need: int,
                 increasing: bool) -> bool:
    """判定序列末尾是否**连续 need 次**同向变化。

    ★ 口径说明：「连续2日改善」= 最后 2 次变化方向一致且同向。
      例：序列 [16.3, 85.1, 38.9]（10/30→11/02→11/03）
        连续2日改善 → 需 16.3<85.1 且 85.1<38.9 → **不成立**（正确）
        连续2日下降 → 需 16.3>85.1 → 不成立（正确）
      注意不能只比较最后两点 —— 单日反弹不构成「持续改善」。
    """
    n = len(seq)
    if n < 2:
        return False
    k = min(int(need), n - 1)
    start = n - 1 - k
    for i in range(start, n - 1):
        a, b = seq[i], seq[i + 1]
        if a is None or b is None:
            return False
        if increasing:
            if not (b > a):
                return False
        else:
            if not (b < a):
                return False
    return True


def _zt_dt_ratio(row: Optional[Dict]) -> Optional[float]:
    if not row:
        return None
    zt, dt = row.get('zt_count'), row.get('dt_count')
    if zt is None or dt is None:
        return None
    if dt == 0:
        return float('inf') if zt > 0 else None
    return zt / dt


def check_easing_confirmation(trade_date: str, cfg: dict = None,
                              micro_series: List[Dict[str, Any]] = None) -> ConfirmationResult:
    """S4 卖压衰减确认。

    ★ 必须同时看**跌停家数**与**市场广度**，
      不得仅凭「指数上涨」或「成交额下降」就确认卖压衰减。

    五项判定（全部通过才 PASS）：
      E1 卖压连续衰减      —— 跌停连续下降 且 下跌家数连续下降
      E2 跌停家数连续下降  —— dt[t] < dt[t-1] < dt[t-2]
      E3 市场广度改善      —— 上涨占比连续改善
      E4 涨停/跌停比不恶化 —— 比值 ≥ 下限且高于前日
      E5 无局部风险恶化    —— 跌停未激增 / 广度未塌陷 / 未出现放量下跌
    """
    cfg = cfg or load_config()
    ec = cfg.get("easing_confirmation", {})
    out = ConfirmationResult(name="卖压衰减确认")
    series = micro_series if micro_series is not None else micro.load_series(trade_date, 12)

    if len(series) < int(ec.get("min_history_days", 3)):
        out.missing.append(f"历史不足（{len(series)} 天，需 ≥{ec.get('min_history_days', 3)} 天）")
        out.evidence.append("卖压衰减确认 UNKNOWN（数据不足）")
        return out

    cur = series[-1]
    if str(cur['trade_date']) != str(trade_date):
        out.missing.append(f"序列末行 {cur['trade_date']} ≠ 目标 {trade_date}")
        out.evidence.append("卖压衰减确认 UNKNOWN（当日数据未就绪）")
        return out

    d1 = _series_of(series, 1)      # 前一交易日
    d2 = _series_of(series, 2)      # 前二交易日
    need = int(ec.get("dt_consecutive_decrease", 2))
    out.details = {
        "dt_series": [s.get('dt_count') for s in series[-4:]],
        "down_series": [s.get('down_count') for s in series[-4:]],
        "up_ratio_series": [round(s.get('up_ratio') * 100, 2) for s in series[-4:]
                            if s.get('up_ratio') is not None],
        "zt_dt_series": [(_zt_dt_ratio(s) if _zt_dt_ratio(s) is None
                          else round(_zt_dt_ratio(s), 3)) for s in series[-4:]],
    }

    # ---- E1/E2 跌停家数连续下降 ----
    dt_seq = [s.get('dt_count') for s in (d2, d1, cur)]
    dt_mode = ec.get("dt_decrease_mode", "decay")
    if any(v is None for v in dt_seq):
        e2 = False
        out.missing.append("跌停家数序列不完整")
    else:
        e2 = _check_trend(dt_seq, int(ec.get("dt_consecutive_decrease", 2)),
                          increasing=False, mode=dt_mode)
    out.checks["跌停家数连续下降"] = e2
    out.evidence.append(
        f"跌停家数 {dt_seq} → "
        + ("✓卖压在衰减（今日低于昨日且低于前日）" if e2 else "✗未连续下降")
        + (f"［{dt_mode}口径］" if dt_mode != "strict" else "［strict连续口径］"))

    # ---- E1 卖压连续衰减（跌停 + 下跌家数双降） ----
    dn_seq = [s.get('down_count') for s in (d2, d1, cur)]
    if any(v is None for v in dn_seq):
        e1 = False
        out.missing.append("下跌家数序列不完整")
    else:
        dn_dec = _check_trend(dn_seq,
                              int(ec.get("down_consecutive_decrease", 2)),
                              increasing=False, mode=dt_mode)
        e1 = bool(e2 and dn_dec)
    out.checks["卖压连续衰减"] = e1
    out.evidence.append(
        f"下跌家数 {dn_seq} → "
        + ("✓跌停与下跌家数同步衰减" if e1 else "✗卖压未连续衰减"))

    # ---- E3 市场广度改善 ----
    br_seq = [s.get('up_ratio') for s in (d2, d1, cur)]
    br_mode = ec.get("breadth_improve_mode", "decay")
    if any(v is None for v in br_seq):
        e3 = False
        out.missing.append("上涨占比序列不完整")
    else:
        e3 = _check_trend(br_seq, int(ec.get("breadth_consecutive_improve", 2)),
                          increasing=True, mode=br_mode)
    out.checks["市场广度改善"] = e3
    out.evidence.append(
        f"上涨占比 {[round(v*100,1) for v in br_seq]}% → "
        + ("✓连续改善" if e3 else "✗未连续改善")
        + (f"［{br_mode}口径］" if br_mode != "strict" else "［strict连续口径］"))

    # ---- E4 涨停/跌停比 ----
    r_cur, r_prev = _zt_dt_ratio(cur), _zt_dt_ratio(d1)
    if r_cur is None or r_prev is None:
        e4 = False
        out.missing.append("涨停/跌停比不可用")
        out.evidence.append("涨停/跌停比 UNKNOWN")
    else:
        floor = float(ec.get("zt_dt_ratio_min", 1.0))
        better = (r_cur > r_prev) if ec.get("zt_dt_ratio_improve_required", True) else True
        e4 = bool(r_cur >= floor and better)
        out.evidence.append(
            f"涨停/跌停比 {r_prev:.2f} → {r_cur:.2f}"
            f"（要求 ≥{floor:.1f} 且高于前日）" + ("✓" if e4 else "✗"))
    out.checks["涨停跌停比不恶化"] = e4

    # ---- E5 无局部风险恶化 ----
    local_bad = []
    spike = float(ec.get("local_risk_dt_spike_ratio", 2.0))
    if dt_seq[0] and dt_seq[-1] is not None and dt_seq[-1] >= dt_seq[0] * spike \
            and dt_seq[-1] > (dt_seq[0] or 0):
        local_bad.append(f"跌停家数放大（{dt_seq[0]}→{dt_seq[-1]}）")
    if br_seq[0] is not None and br_seq[-1] is not None:
        drop_pp = (br_seq[0] - br_seq[-1]) * 100
        if drop_pp >= float(ec.get("local_risk_breadth_drop_pp", 15.0)):
            local_bad.append(f"广度塌陷 {drop_pp:.1f}pp")
    amt_v = amount_verdict(trade_date, cfg)
    if ec.get("local_risk_amount_expand_down", True) and amt_v.pattern == "放量下跌":
        local_bad.append("出现放量下跌")
    out.details["local_risk"] = local_bad
    e5 = not local_bad
    out.checks["无局部风险恶化"] = e5
    out.evidence.append(
        "无局部风险恶化 ✓" if e5 else
        ("局部风险恶化：" + "；".join(local_bad) + " ✗"))

    # ---- 成交额变化（支撑证据，不单独否决） ----
    out.details["amount"] = {
        "amount_yi": amt_v.amount_yi,
        "chg_vs_prev": amt_v.chg_vs_prev,
        "chg_vs_ma5": amt_v.chg_vs_ma5,
        "index_chg": amt_v.index_chg,
        "up_ratio": amt_v.up_ratio,
        "pattern": amt_v.pattern,
    }
    out.evidence.append(
        f"成交额变化：{amt_v.amount_yi:,.0f} 亿｜环比 "
        + (f"{amt_v.chg_vs_prev:+.1f}%" if amt_v.chg_vs_prev is not None else "UNKNOWN")
        + f"｜相对5日均 "
        + (f"{amt_v.chg_vs_ma5:+.1f}%" if amt_v.chg_vs_ma5 is not None else "UNKNOWN")
        + f"｜形态 {amt_v.pattern}")

    if out.missing:
        out.verdict = VERDICT_UNKNOWN
    elif all(out.checks.values()):
        out.verdict = VERDICT_PASS
    else:
        out.verdict = VERDICT_FAIL
    return out


def check_rebound_confirmation(trade_date: str, cfg: dict = None,
                                mainline_module=None,
                                micro_series: List[Dict[str, Any]] = None,
                                amount: AmountVerdict = None) -> ConfirmationResult:
    """S5 反弹确认 —— 买盘是否开始形成**持续**优势。

    ★ 不得将单日普涨直接认定为 S5：
      「上涨占比持续改善」要求至少连续 2 日抬升。

    五项判定：
      R1 上涨占比持续改善（连续 ≥2 日，且水平达标）
      R2 跌停数量明显下降
      R3 涨停/跌停比改善
      R4 成交额与指数方向匹配
      R5 主线行业出现持续性赚钱效应
    """
    cfg = cfg or load_config()
    rc = cfg.get("rebound_confirmation", {})
    out = ConfirmationResult(name="反弹确认")
    series = micro_series if micro_series is not None else micro.load_series(trade_date, 12)

    if len(series) < int(rc.get("min_history_days", 3)):
        out.missing.append(f"历史不足（{len(series)} 天）")
        out.evidence.append("反弹确认 UNKNOWN（数据不足）")
        return out

    cur = series[-1]
    if str(cur['trade_date']) != str(trade_date):
        out.missing.append(f"序列末行 {cur['trade_date']} ≠ 目标 {trade_date}")
        out.evidence.append("反弹确认 UNKNOWN（当日数据未就绪）")
        return out

    d1 = _series_of(series, 1)
    d2 = _series_of(series, 2)
    amt = amount if amount is not None else amount_verdict(trade_date, cfg)

    # ---- R1 上涨占比持续改善 ----
    br_seq = [s.get('up_ratio') for s in (d2, d1, cur)]
    if any(v is None for v in br_seq):
        r1 = False
        out.missing.append("上涨占比序列不完整")
    else:
        need = int(rc.get("breadth_consecutive_improve", 2))
        mode = rc.get("breadth_improve_mode", "decay")
        rising = _check_trend(br_seq, need, increasing=True, mode=mode)
        level = br_seq[-1] >= float(rc.get("breadth_level_min", 0.45))
        r1 = bool(rising and level)
        out.evidence.append(
            f"上涨占比 {[round(v*100,1) for v in br_seq]}%"
            f"（连续{need}日改善{'✓' if rising else '✗'}，当前水平 "
            f"{br_seq[-1]*100:.1f}% ≥{float(rc.get('breadth_level_min',0.45))*100:.0f}%"
            f"{'✓' if level else '✗'}）")
    out.checks["上涨占比持续改善"] = r1

    # ---- R2 跌停数量明显下降 ----
    dt_seq = [s.get('dt_count') for s in (d1, cur)]
    if any(v is None for v in dt_seq):
        r2 = False
        out.missing.append("跌停家数不完整")
    else:
        ratio = float(rc.get("dt_decrease_ratio", 0.7))
        r2 = bool(dt_seq[1] <= dt_seq[0] * ratio)
        out.evidence.append(
            f"跌停家数 {dt_seq[0]} → {dt_seq[1]}"
            f"（要求 ≤前日×{ratio:.0%}）" + ("✓" if r2 else "✗"))
    out.checks["跌停数量明显下降"] = r2

    # ---- R3 涨停/跌停比改善 ----
    r_cur, r_prev = _zt_dt_ratio(cur), _zt_dt_ratio(d1)
    if r_cur is None or r_prev is None:
        r3 = False
        out.missing.append("涨停/跌停比不可用")
    else:
        floor = float(rc.get("zt_dt_ratio_min", 2.0))
        r3 = bool(r_cur >= floor and r_cur > r_prev)
        out.evidence.append(
            f"涨停/跌停比 {r_prev:.2f} → {r_cur:.2f}"
            f"（要求 ≥{floor:.1f} 且高于前日）" + ("✓" if r3 else "✗"))
    out.checks["涨停跌停比改善"] = r3

    # ---- R4 成交额与指数方向匹配 ----
    if amt.index_chg is None or amt.chg_vs_ma5 is None:
        r4 = False
        out.missing.append("量价要素缺失")
        out.evidence.append("成交额与指数方向 UNKNOWN")
    else:
        r4 = amt.pattern in ("放量上涨", "缩量上涨")
        out.evidence.append(
            f"成交额与指数方向：{amt.pattern}"
            f"（成交额相对5日均 {amt.chg_vs_ma5:+.1f}%，指数 {amt.index_chg:+.2f}%，"
            f"上涨占比 {amt.up_ratio*100:.1f}%）" + ("✓匹配" if r4 else "✗不匹配"))
    out.checks["成交额与指数方向匹配"] = r4

    # ---- R5 主线行业持续性赚钱效应 ----
    if mainline_module is None:
        r5 = False
        out.missing.append("主线模块不可用")
        out.evidence.append("主线持续性赚钱效应 UNKNOWN")
    else:
        inds = (mainline_module.details or {}).get("industries") or []
        cands = (mainline_module.details or {}).get("candidates") or []
        need_days = int(rc.get("mainline_days_strong_min", 3))
        need_ex = float(rc.get("mainline_excess20_min", 0.0))
        ok = False
        for c in cands:
            ds = c.get('days_strong') or 0
            ex = c.get('excess20')
            ex = ex if ex is not None else -999
            if ds >= need_days and ex > need_ex:
                ok = True
                break
        r5 = bool(inds and ok)
        if inds:
            out.evidence.append(
                f"主线 {inds[0]}｜连续强态 {cands[0].get('days_strong') if cands else '?'} 日"
                f"｜超额20日 {cands[0].get('excess20') if cands else '?'}%"
                f"（要求连续强态≥{need_days}日且超额20日>{need_ex}%）"
                + ("✓" if r5 else "✗"))
        else:
            out.evidence.append("无主线行业 → 主线持续性赚钱效应不成立 ✗")
    out.checks["主线持续性赚钱效应"] = r5

    if out.missing:
        out.verdict = VERDICT_UNKNOWN
    elif all(out.checks.values()):
        out.verdict = VERDICT_PASS
    else:
        out.verdict = VERDICT_FAIL
    return out


# ============================================================
# ④ STRUCTURAL_ALPHA_EXCEPTION
# ============================================================
@dataclass
class StructuralAlphaResult:
    """结构性机会判定。"""
    verdict: str = VERDICT_UNKNOWN
    industry: Optional[str] = None           # 最强行业
    days_strong: Optional[int] = None        # 行业持续性（连续强态天数）
    breadth_up_ratio: Optional[float] = None  # 行业内部广度（当日上涨占比）
    breadth_up20_ratio: Optional[float] = None
    ex_top1_ret20: Optional[float] = None    # 剔除第一权重股后的加权收益
    top_weight: Optional[float] = None
    weighted_ret20: Optional[float] = None
    leader_code: Optional[str] = None
    leader_ret20: Optional[float] = None
    leader_above_ma20: Optional[bool] = None
    median_netprofit_yoy: Optional[float] = None
    median_roe: Optional[float] = None
    fundamental_coverage: Optional[float] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    checks: Dict[str, bool] = field(default_factory=dict)
    verification: Dict[str, bool] = field(default_factory=dict)
    evidence: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    position_note: str = ""
    """★ 结构性机会只允许在原有市场仓位上限内申请授权。"""


def _industry_panel(trade_date: str):
    """取行业 ETF 面板与行业序列（含基准序列）。"""
    import etf_mainline as em
    ecfg = em.load_config()
    panel = em.load_etf_panel()
    if panel is None or panel.empty:
        return None, None, None
    series = em.compute_industry_series(panel, ecfg)
    return panel, series, ecfg


def _bench_nav(series: Dict[str, Any], td: str, ma60_window: int = 60):
    """构造与 detect_mainlines 同口径的基准序列（所有行业 nav 等权）。"""
    all_nav = []
    for ind, s in series.items():
        sub = s[s["trade_date"] <= td]
        if len(sub) < ma60_window:
            continue
        all_nav.append(sub["nav"].values)
    if not all_nav:
        return None
    minlen = min(len(a) for a in all_nav)
    return np.mean([a[-minlen:] for a in all_nav], axis=0)


def check_structural_alpha(trade_date: str, state: str, cfg: dict = None,
                           market_up_ratio: Optional[float] = None
                           ) -> StructuralAlphaResult:
    """STRUCTURAL_ALPHA_EXCEPTION —— 弱市/过渡期中的结构性行业机会。

    ★ 只在 S3 / S4 / S6 下允许识别（其他状态市场整体已覆盖该机会）。
    ★ 结构性机会**不得突破**市场状态的仓位权限，只在上限内申请授权。
    ★ 入池五项 + 复核四项，全部通过才 PASS；数据缺失 → UNKNOWN。
    """
    cfg = cfg or load_config()
    sc = cfg.get("structural_alpha", {})
    out = StructuralAlphaResult()

    allowed = sc.get("allowed_states", ["S3", "S4", "S6"])
    if state not in allowed:
        out.verdict = VERDICT_FAIL
        out.evidence.append(
            f"当前状态 {state} 不在结构性机会允许范围 {allowed} 内 → 不适用")
        out.position_note = "不适用（非允许状态）"
        return out

    try:
        import etf_mainline as em
    except Exception as e:
        out.missing.append(f"ETF 主线模块不可用: {e}")
        out.evidence.append("结构性机会 UNKNOWN（ETF 模块不可用）")
        return out

    panel, series, ecfg = _industry_panel(trade_date)
    if not series:
        out.missing.append("行业 ETF 序列不可用")
        out.evidence.append("结构性机会 UNKNOWN（ETF 数据缺失）")
        return out

    td = str(trade_date)
    bench = _bench_nav(series, td, int(sc.get("ma60_window", 60)))
    if bench is None:
        out.missing.append("基准序列不可用")
        out.evidence.append("结构性机会 UNKNOWN（基准序列缺失）")
        return out

    ma60w = int(sc.get("ma60_window", 60))
    ma20w = int(sc.get("ma20_window", 20))
    min_ex20 = float(sc.get("min_excess20", 10.0))
    min_days = int(sc.get("min_days_strong", 15))
    dd_tol = float(sc.get("max_drawdown_from_high_pct", -12.0))
    max_dd60 = float(sc.get("max_dd60", -18.0))
    min_ret60 = float(sc.get("trend60_min_ret", 0.0))

    # ---- 逐行业计算 ----
    cands: List[Dict[str, Any]] = []
    bench_ret20 = (bench[-1] / bench[-21] - 1) * 100 if len(bench) > 21 else None
    bench_ret60 = (bench[-1] / bench[-61] - 1) * 100 if len(bench) > 61 else None

    for ind, s in series.items():
        sub = s[s["trade_date"] <= td]
        if len(sub) < ma60w + 1:
            continue
        nav = sub["nav"].values
        amt = sub["amount"].values
        cur = nav[-1]
        ma20 = float(np.mean(nav[-ma20w:]))
        ma60 = float(np.mean(nav[-ma60w:]))
        ret20 = (cur / nav[-21] - 1) * 100
        ret60 = (cur / nav[-61] - 1) * 100
        ex20 = (ret20 - bench_ret20) if bench_ret20 is not None else None
        ex60 = (ret60 - bench_ret60) if bench_ret60 is not None else None
        hi20 = float(np.max(nav[-20:]))
        dd20 = (cur / hi20 - 1) * 100
        hi60 = float(np.max(nav[-60:]))
        dd60 = (cur / hi60 - 1) * 100

        # 连续强态（与 etf_mainline 同口径）
        try:
            days = em._count_strong_days(s, {}, td, ecfg, bench_nav=bench)
        except Exception:
            days = 1

        cands.append({
            "industry": ind,
            "days_strong": int(days),
            "ret20": round(ret20, 2), "ret60": round(ret60, 2),
            "excess20": round(ex20, 2) if ex20 is not None else None,
            "excess60": round(ex60, 2) if ex60 is not None else None,
            "dd20": round(dd20, 2), "dd60": round(dd60, 2),
            "above_ma20": bool(cur > ma20), "above_ma60": bool(cur > ma60),
            "amt_ratio": (round(amt[-1] / float(np.mean(amt[-20:])), 2)
                          if float(np.mean(amt[-20:])) > 0 else None),
            "amt_ma5_vs_ma20": (round(float(np.mean(amt[-5:])) / float(np.mean(amt[-20:])), 3)
                                if float(np.mean(amt[-20:])) > 0 else None),
            "trend_ok": bool((cur > ma20) or (dd20 >= dd_tol)),
        })

    # ---- 入池筛选 ----
    near_high = float(sc.get("trend60_near_high_pct", -8.0))
    pool = []
    for c in cands:
        c["c_days"] = c["days_strong"] >= min_days
        c["c_ex20"] = (c["excess20"] is not None and c["excess20"] >= min_ex20)
        # ★ 60 日趋势「没有明显恶化」：不是要求 60 日收益为正，
        #   而是「没有深度破位」—— 仍在 60 日高点附近（回撤 ≤ trend60_near_high）
        #   同样算趋势健康（例：食品饮料 60 日收益 -1.0%、距 60 日高仅 -0.1%）。
        c["c_trend60"] = bool(
            (c["ret60"] >= min_ret60 or c["dd60"] >= near_high)
            and c["dd60"] >= max_dd60)
        if c["c_days"] and c["c_ex20"] and c["c_trend60"]:
            pool.append(c)
    pool.sort(key=lambda x: (x["excess20"] or -999), reverse=True)

    if not pool:
        top = sorted(cands, key=lambda x: (x["excess20"] or -999), reverse=True)[:3]
        out.verdict = VERDICT_FAIL
        out.candidates = top
        out.evidence.append(
            "无行业同时满足「连续强态≥%d日 + 20日超额≥%.0f%% + 60日趋势未恶化」"
            % (min_days, min_ex20))
        if top:
            out.evidence.append(
                "相对强度前 3：" + "、".join(
                    f"{t['industry']} 超额20日 {t['excess20']:.1f}%／连续强态 {t['days_strong']}日"
                    for t in top))
        out.position_note = "无结构性机会"
        return out

    # ---- 最强候选：先做成分股层面的检验 ----
    import industry_constituents as ic
    icfg = ic.load_config()
    min_constituents = int(sc.get("min_constituents", 20))
    up20_min = float(sc.get("breadth_up20_ratio_min", 0.55))
    ex_best_min = float(sc.get("ex_best_breadth_min", 0.50))
    today_min = float(sc.get("breadth_today_min", 0.40))
    best = None
    max_cand = int(sc.get("max_analyze_candidates", 6))
    for c in pool[:max_cand]:
        st = ic.analyze_industry(c["industry"], trade_date, icfg)
        c["structure"] = st
        # ★ 入池「成分股广度」用 **20 日**口径 —— 结构性机会是
        #   「过去持续走强」，不是「今天涨得好」；单日普跌不应否定一个
        #   连续 15 日走强的行业。当日广度放到复核项里。
        c["c_breadth"] = bool(
            st.get("constituent_count", 0) >= min_constituents
            and (st.get("breadth_up20_ratio") or 0) >= up20_min)
        ex1 = st.get("ex_top1_ret20")
        exb = st.get("ex_best_breadth20")
        # ★ 排除「单只权重股拉升」：两个口径都要过 ——
        #   ① 剔除 ETF 第一权重股后，加权 20 日收益仍为正
        #   ② 剔除表现最好的一只成分股后，行业广度仍不塌陷
        c["c_not_single_stock"] = bool(
            (ex1 is None or ex1 >= float(sc.get("ex_top1_ret_min", 0.0)))
            and (exb is None or exb >= ex_best_min))
        if best is None and c["c_breadth"] and c["c_not_single_stock"]:
            best = c
    if best is None:
        # 没有通过成分股检验的 → 按证据排序取第一个作为「最强行业」展示
        best = max(pool[:max_cand], key=lambda x: (x.get("excess20") or -999))

    st = best.get("structure") or {}
    out.industry = best["industry"]
    out.days_strong = best["days_strong"]
    out.breadth_up_ratio = st.get("breadth_up_ratio")
    out.breadth_up20_ratio = st.get("breadth_up20_ratio")
    out.ex_top1_ret20 = st.get("ex_top1_ret20")
    out.top_weight = st.get("top_weight")
    out.weighted_ret20 = st.get("weighted_ret20")
    out.leader_code = st.get("leader_code")
    out.leader_ret20 = st.get("leader_ret20")
    out.leader_above_ma20 = st.get("leader_above_ma20")
    out.median_netprofit_yoy = st.get("median_netprofit_yoy")
    out.median_roe = st.get("median_roe")
    out.fundamental_coverage = st.get("fundamental_coverage")
    out.candidates = [{k: v for k, v in c.items() if k != "structure"}
                      for c in pool[:5]]

    # ---- 入池五项 ----
    out.checks = {
        "连续强势≥%d日" % min_days: best["c_days"],
        "20日超额≥%.0f%%" % min_ex20: best["c_ex20"],
        "60日趋势未恶化": best["c_trend60"],
        "成分股广度达标": best["c_breadth"],
        "排除单权重股拉升": best["c_not_single_stock"],
    }
    out.evidence.append(
        f"最强候选：{best['industry']}｜连续强态 {best['days_strong']} 日"
        f"｜超额20日 {best['excess20']:.1f}%｜近20日 {best['ret20']:.1f}%"
        f"｜近60日 {best['ret60']:.1f}%｜距60日高 {best['dd60']:.1f}%")
    if st.get("constituent_count"):
        out.evidence.append(
            f"成分股 {st.get('constituent_count')} 只（持仓公告日 {st.get('holdings_ann_date')}）"
            f"｜当日上涨占比 "
            + (f"{st.get('breadth_up_ratio')*100:.1f}%" if st.get("breadth_up_ratio") is not None else "UNKNOWN")
            + "｜20日上涨占比 "
            + (f"{st.get('breadth_up20_ratio')*100:.1f}%" if st.get("breadth_up20_ratio") is not None else "UNKNOWN"))
    else:
        out.missing.append("行业成分股持仓数据不可用")
    out.evidence.append(
        f"剔除第一权重股后成分股加权20日收益 "
        + (f"{st.get('ex_top1_ret20'):.2f}%" if st.get("ex_top1_ret20") is not None else "UNKNOWN")
        + f"（第一权重股权重 "
        + (f"{st.get('top_weight')*100:.1f}%" if st.get("top_weight") is not None else "UNKNOWN")
        + "）")

    # ---- 出池后四项复核 ----
    v1_fund = False
    if (st.get("fundamental_coverage") is not None
            and st.get("fundamental_coverage") >= float(sc.get("fundamental_min_coverage", 0.5))
            and st.get("median_netprofit_yoy") is not None
            and st.get("median_netprofit_yoy") >= float(sc.get("fundamental_netprofit_yoy_min", 0.0))
            and st.get("median_roe") is not None
            and st.get("median_roe") >= float(sc.get("fundamental_roe_min", 0.0))):
        v1_fund = True
    out.verification["成分股业绩与基本面"] = v1_fund
    out.evidence.append(
        "基本面复核："
        + (f"覆盖率 {st.get('fundamental_coverage')*100:.0f}%、"
           f"净利同比中位 {st.get('median_netprofit_yoy'):+.1f}%、"
           f"ROE 中位 {st.get('median_roe'):.1f}%" if v1_fund else
           ("数据不足（覆盖率或中位数不达标）" if st.get("fundamental_coverage") is not None
            else "基本面数据不可用")))

    v2_breadth = bool(
        st.get("breadth_up_ratio") is not None
        and (st.get("breadth_up_ratio") or 0) >= today_min
        and (st.get("breadth_up20_ratio") or 0) >= up20_min)
    if market_up_ratio is not None:
        # 与大盘对比：行业当日上涨占比不低于大盘 → 才是真正的「相对强势」
        v2_breadth = v2_breadth and (st.get("breadth_up_ratio") or 0) >= market_up_ratio
    out.verification["行业内上涨家数占比"] = v2_breadth
    out.evidence.append(
        "行业内广度复核："
        + (f"当日 {st.get('breadth_up_ratio')*100:.1f}%"
           f"（大盘 {market_up_ratio*100:.1f}%）/ 20日 {st.get('breadth_up20_ratio')*100:.1f}%"
           if st.get("breadth_up_ratio") is not None else "UNKNOWN"))

    v3_leader = bool(st.get("leader_ret20") is not None and st["leader_ret20"] > 0
                     and (not sc.get("leader_above_ma20", True)
                          or st.get("leader_above_ma20") is True))
    out.verification["领涨股趋势"] = v3_leader
    out.evidence.append(
        "领涨股复核："
        + (f"{st.get('leader_code')} 20日 {st.get('leader_ret20'):+.1f}%、"
           f"站上MA20 {'是' if st.get('leader_above_ma20') else '否'}"
           if st.get("leader_code") else "领涨股数据不可用"))

    v4_amt = bool(best.get("amt_ratio") is not None
                  and best["amt_ratio"] >= float(sc.get("amt_ratio_min", 1.0))
                  and (best.get("amt_ma5_vs_ma20") is None
                       or best["amt_ma5_vs_ma20"] >= 1.0))
    out.verification["成交额持续性"] = v4_amt
    out.evidence.append(
        "成交额持续性复核："
        + (f"量比 {best.get('amt_ratio')}、5日均/20日均 {best.get('amt_ma5_vs_ma20')}"
           if best.get("amt_ratio") is not None else "UNKNOWN"))

    # ---- 汇总 ----
    if out.missing:
        out.verdict = VERDICT_UNKNOWN
    elif all(out.checks.values()) and all(out.verification.values()):
        out.verdict = VERDICT_PASS
    else:
        out.verdict = VERDICT_FAIL

    state_cap = 0
    try:
        state_cap = int(v101.position_authority(state, cfg).max_position)
    except Exception:
        state_cap = 0
    if out.verdict == VERDICT_PASS:
        out.position_note = (
            f"结构性机会成立，但**不得突破市场状态 {state} 的仓位权限（{state_cap}%）**；"
            "仅在该上限内集中于强势行业建仓，不额外放大总仓位。")
    else:
        out.position_note = (
            f"结构性机会未成立 → 不申请任何额外授权；"
            f"仓位仍受市场状态 {state} 的权限（{state_cap}%）约束。")
    return out


# ============================================================
# 仓位语义分离
# ============================================================
@dataclass
class PositionPlan:
    """仓位五要素分列。"""
    state: str = STATE_NORMAL
    target_position: int = 0              # 目标仓位（环境层面参考值）
    suggested_low: int = 0                # 建议区间下限
    suggested_high: int = 0               # 建议区间上限
    hard_cap: int = 0                     # 仓位硬上限（任何情况下不可突破）
    allow_active_add: bool = False        # 主动加仓权限
    active_add_conditions: List[str] = field(default_factory=list)
    full_authorization: str = ""          # 满仓授权
    actual_hint: Optional[int] = None     # 实际仓位参考（含执行层约束）
    actual_note: str = ""
    target_is_mandatory: bool = False     # ★ 目标仓位不视为必须持仓
    cash_floor_note: str = ""             # ★ P2：现金地板与满仓授权的语义说明
    limits: List[str] = field(default_factory=list)


def build_position_plan(state: str, target, authority, portfolio,
                        authorization, opportunities, current_position,
                        cfg: dict, risk_cap: Optional[int] = None,
                        extra_cap: Optional[int] = None,
                        lo_ratio: Optional[float] = None,
                        waive_cash_floor: bool = False,
                        authorized_cap: Optional[int] = None) -> PositionPlan:
    """构造仓位计划：目标 / 区间 / 硬上限 / 主动加仓 / 满仓授权。

    ★ P0 修复：``risk_cap`` 为「由风险预算反解的最大仓位」。
    ★ P1/P2：``extra_cap``（确认门控回落）、``lo_ratio``（动态建议区间）、
      ``waive_cash_floor``（S6 现金地板豁免，默认关闭）。
    ★ V10.7.4：``authorized_cap`` 仅由 bull_latch 授权链给出，**只放宽
      「状态权限」这一项**；现金地板 / 风险预算 / 确认门控 / 满仓授权否决
      仍逐项取 min。不传这些可选参数时行为与旧版完全一致（A/B 兼容）。
    """
    ps = cfg.get("position_semantics", {})
    out = PositionPlan(state=state)
    out.target_position = int(target.suggested) if target else 0
    out.full_authorization = authorization.result if authorization else ""
    out.target_is_mandatory = False

    # 硬上限 = 仓位权限 ∩ 现金下限 ∩ 满仓授权 ∩ 风险预算反解上限 ∩ 确认门控
    hard = int(authority.max_position) if authority else 0
    if authorized_cap is not None:
        state_cap_original = hard
        hard = max(hard, max(0, min(int(authorized_cap), 100)))
        out.limits.append(
            f"主升段锁存授权：状态权限 {state_cap_original}% → {hard}%"
            f"（P0 风险预算重算 + 满仓授权 FULL_ALLOWED；仅放宽状态权限一项，"
            f"现金地板/风险预算/确认门控/满仓授权否决仍逐项取 min）")
    if (portfolio is not None and portfolio.cash_floor_max is not None
            and not waive_cash_floor):
        hard = min(hard, int(portfolio.cash_floor_max))
    if risk_cap is not None:
        hard = min(hard, int(risk_cap))
        out.limits.append(f"风险预算反解仓位上限 {int(risk_cap)}%")
    if extra_cap is not None:
        hard = min(hard, int(extra_cap))
        out.limits.append(f"确认门控回落仓位上限 {int(extra_cap)}%（非 PASS 即继续保持观察）")
    if authorization and authorization.result != v101.FULL_ALLOWED:
        auth_cfg = cfg.get("full_position_authorization", {})
        cap = (auth_cfg.get("cap_when_review", 99)
               if authorization.result == v101.FULL_REVIEW
               else auth_cfg.get("cap_when_denied", 80))
        hard = min(hard, int(cap))
    out.hard_cap = max(0, min(hard, 100))

    # 建议区间
    nq = opportunities.qualified_count if opportunities else 0
    has_signal = bool(opportunities and opportunities.data_available and nq > 0)
    lo_ratio_used = (float(lo_ratio) if lo_ratio is not None
                     else float(ps.get("range_low_with_signal", 0.5)))
    hi_ratio = float(ps.get("range_high_ratio", 1.0))
    if not has_signal and ps.get("cash_when_no_qualified_signal", True):
        out.suggested_low = 0
        out.suggested_high = out.target_position
        out.actual_hint = 0
        out.actual_note = (
            f"目标仓位 {out.target_position}% 是**环境层面的参考值**，"
            "不是必须达到的持仓比例。当前无合格买入标的，"
            "实际仓位可以低于目标，甚至保持现金（0%）。")
    else:
        out.suggested_low = int(round(out.target_position * lo_ratio_used))
        out.suggested_high = int(round(out.target_position * hi_ratio))
        out.actual_hint = out.target_position
        out.actual_note = (
            f"有 {nq} 只合格买入标的，建议在 {out.suggested_low}%~{out.suggested_high}% 区间内建仓。")

    # 主动加仓权限
    state_spec = (cfg.get("position_authority", {}) or {}).get(state, {})
    allow_probe = bool(state_spec.get("allow_probe", False))
    conds = []
    if not allow_probe:
        conds.append(f"市场状态 {state} 不允许主动加仓")
    if current_position is not None and out.target_position <= current_position:
        conds.append(f"当前仓位 {current_position}% 已达/超过目标 {out.target_position}%")
    if portfolio is not None and not portfolio.ok:
        conds.append("组合风险不合格")
    if authorization and authorization.result == v101.FULL_DENIED:
        conds.append("满仓授权被否决")
    out.allow_active_add = bool(allow_probe and current_position is not None
                                 and out.target_position > (current_position or 0)
                                 and (portfolio is None or portfolio.ok))
    out.active_add_conditions = conds if conds else ["当前满足主动加仓条件"]
    if out.allow_active_add:
        out.limits.append(
            f"允许主动加仓：{current_position}% → {out.target_position}%"
            f"（单次不超过 {int((cfg.get('ladder') or {}).get('max_single_step_pct', 20))}%）")
    else:
        out.limits.extend(conds)
    return out


# ============================================================
# 状态门控
# ============================================================
@dataclass
class GateResult:
    base_state: str = ""
    effective_state: str = ""
    applied: List[str] = field(default_factory=list)
    blocked: List[str] = field(default_factory=list)


def _prev_trade_date(trade_date: str, series: List[Dict]) -> Optional[str]:
    if len(series) >= 2:
        return str(series[-2]['trade_date'])
    return None


def apply_state_gates(base_state: str, base_decision, easing: ConfirmationResult,
                       rebound: ConfirmationResult, trade_date: str,
                       cfg: dict, micro_series: List[Dict] = None) -> GateResult:
    """状态门控：确认不通过 → 不升级到更高仓位权限的状态。

    ★ 只在**升级方向**设卡（更保守），不改写 V10.0/V10.1 的状态判定。
    ★ UNKNOWN 不算通过（缺失指标不得默认通过）。
    ★ **门控不得放大仓位**：被否决后的落点状态，其仓位权限必须 ≤ 原状态，
      否则维持原状态并只做标注（否则「回退到 S2(30%)」会反而放大仓位）。
    ★ S5 升级被否决时落在 **S4 观察**（而不是直接掉回 S3）——
      对应「如果条件不完整，继续保持S4观察」。
    """
    g = cfg.get("gates", {})
    out = GateResult(base_state=base_state, effective_state=base_state)
    prev_state = getattr(base_decision, "prev_state", None) if base_decision else None
    changed = bool(getattr(base_decision, "changed", False)) if base_decision else False

    series = micro_series if micro_series is not None else micro.load_series(trade_date, 12)

    def _cap(state):
        try:
            return int(v101.position_authority(state, cfg).max_position)
        except Exception:
            return 0

    def _fallback(preferred, base):
        """选择一个**不放大仓位**的落点状态。"""
        if preferred is None:
            return base
        if _cap(preferred) <= _cap(base):
            return preferred
        return base

    # ---- G1：进入 S4 需要卖压衰减确认 ----
    if base_state == STATE_EASING and changed and g.get("require_easing_for_s4_entry", True):
        if easing.verdict != VERDICT_PASS:
            target = _fallback(prev_state, base_state)
            out.effective_state = target
            out.blocked.append(
                f"S4 进入被否决：卖压衰减确认 {easing.verdict}"
                f"（{'、'.join([k for k, v in easing.checks.items() if not v]) or '数据不足'}）"
                + ("" if target != base_state else
                   "；回退目标权限更高 → 维持 S4 观察，门控不放宽仓位"))
        else:
            out.applied.append("S4 进入：卖压衰减确认 PASS")
    elif base_state == STATE_EASING and not changed:
        # 已在 S4：确认不通过 → 明确标注为「S4 观察」，不得升级
        if easing.verdict != VERDICT_PASS:
            out.applied.append(
                f"S4 观察：卖压衰减确认 {easing.verdict} → 维持 S4（不得升级 S5）")
        else:
            out.applied.append("S4 维持：卖压衰减确认 PASS")

    # ---- G2：进入 S5 需要卖压衰减 + 反弹确认 ----
    if base_state == STATE_REPAIR and changed and g.get("require_rebound_for_s5_entry", True):
        if easing.verdict != VERDICT_PASS or rebound.verdict != VERDICT_PASS:
            miss = []
            if easing.verdict != VERDICT_PASS:
                miss.append("卖压衰减确认 " + easing.verdict)
            if rebound.verdict != VERDICT_PASS:
                miss.append("反弹确认 " + rebound.verdict)
            # 落点：前一日已是改善态 → 保留；否则退到 S4 观察
            if prev_state in (STATE_EASING, STATE_REPAIR):
                target = prev_state
            else:
                target = STATE_EASING
            target = _fallback(target, base_state)
            out.effective_state = target
            out.blocked.append(
                "S5 升级被否决：" + "；".join(miss)
                + f" → 落点 {target} {STATE_CN.get(target, target)}（S4 观察）")
        else:
            out.applied.append("S5 升级：卖压衰减 + 反弹确认均 PASS")

    # ---- G3：维持 S5 需反弹确认连续 2 日成立，否则退回 S4 ----
    if (base_state == STATE_REPAIR and not changed
            and g.get("require_rebound_for_s5_entry", True)):
        if rebound.verdict == VERDICT_FAIL:
            prev_td = _prev_trade_date(trade_date, series)
            prev_fail = False
            if prev_td:
                try:
                    pr = check_rebound_confirmation(prev_td, cfg)
                    prev_fail = (pr.verdict == VERDICT_FAIL)
                except Exception:
                    prev_fail = False
            if prev_fail:
                out.effective_state = STATE_EASING
                out.blocked.append(
                    f"S5 维持被否决：反弹确认连续 2 日未通过 → 退回 {STATE_EASING} {STATE_CN.get(STATE_EASING)}")
            else:
                out.applied.append("S5 维持：反弹确认昨日未通过，但未连续 2 日，暂维持")
        elif rebound.verdict == VERDICT_PASS:
            out.applied.append("S5 维持：反弹确认 PASS")
    return out


# ============================================================
# V10.7 状态持久化（独立表，不污染 V10.0 的 regime_state）
# ============================================================
def _v107_state_db():
    db = v10.STATE_DB
    os.makedirs(os.path.dirname(db), exist_ok=True)
    conn = sqlite3.connect(db, timeout=30)
    conn.execute("""CREATE TABLE IF NOT EXISTS regime_state_v107 (
        trade_date TEXT PRIMARY KEY, state TEXT, base_state TEXT,
        consecutive_days INTEGER, confidence TEXT, reasons TEXT,
        easing_verdict TEXT, rebound_verdict TEXT, structural_verdict TEXT,
        target_position INTEGER, hard_cap INTEGER, gates TEXT, updated_at TEXT)""")
    conn.commit()
    return conn


def load_prev_v107_state(trade_date: str):
    if not os.path.exists(v10.STATE_DB):
        return None
    try:
        conn = _v107_state_db()
        cur = conn.execute(
            "SELECT trade_date, state, base_state, consecutive_days, reasons "
            "FROM regime_state_v107 WHERE trade_date < ? "
            "ORDER BY trade_date DESC LIMIT 1", (str(trade_date),))
        r = cur.fetchone()
        if not r:
            return None
        return {"trade_date": r[0], "state": r[1], "base_state": r[2],
                "consecutive_days": r[3] or 0,
                "reasons": json.loads(r[4]) if r[4] else []}
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def save_v107_state(trade_date: str, state: str, base_state: str,
                     consecutive_days: int, reasons: List[str],
                     easing: str, rebound: str, structural: str,
                     target: int, hard_cap: int, gates: List[str]):
    try:
        conn = _v107_state_db()
        conn.execute(
            "INSERT OR REPLACE INTO regime_state_v107 "
            "(trade_date, state, base_state, consecutive_days, confidence, reasons, "
            " easing_verdict, rebound_verdict, structural_verdict, target_position, "
            " hard_cap, gates, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(trade_date), state, base_state, int(consecutive_days), "中",
             json.dumps(reasons or [], ensure_ascii=False),
             easing, rebound, structural, int(target), int(hard_cap),
             json.dumps(gates or [], ensure_ascii=False),
             datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ============================================================
# 主流程
# ============================================================
@dataclass
class V107Result:
    trade_date: str = ""
    base: Any = None                       # V10.1 完整结果（保留原决策记录）
    amount: Optional[AmountVerdict] = None
    easing: Optional[ConfirmationResult] = None
    rebound: Optional[ConfirmationResult] = None
    structural: Optional[StructuralAlphaResult] = None
    gate: Optional[GateResult] = None
    state: str = STATE_NORMAL
    state_cn: str = ""
    state_days: int = 1
    authority: Optional[Any] = None
    target: Optional[Any] = None
    authorization: Optional[Any] = None
    portfolio: Optional[Any] = None
    plan: Optional[PositionPlan] = None
    switch_conditions: Dict[str, str] = field(default_factory=dict)
    p0: Optional[Any] = None                # ★ P0 风险口径修复记录（可对比）
    opt: Optional[Any] = None               # ★ P1/P2 仓位优化记录（可对比）
    swing: Optional[Any] = None             # ★ V10.7.3 波段闸门记录（可对比）
    bull: Optional[Any] = None              # ★ V10.7.4 主升段锁存记录（可对比）
    ladder_patch: Optional[Any] = None      # ★ P2 修正后的阶梯（原记录保留在 r.base）
    current_position: Optional[int] = None


def _switch_conditions(state: str, cfg: dict) -> Dict[str, str]:
    sc = cfg.get("switch_conditions", {})
    order = [STATE_PANIC, STATE_EASING, STATE_REPAIR, STATE_STRONG,
             STATE_NORMAL, STATE_DRAWDOWN]
    i = order.index(state) if state in order else 0
    # 找下一个权限更高的状态
    nxt = None
    for st in order[i + 1:]:
        nxt = st
        break
    key_map = {
        (STATE_PANIC, STATE_EASING): "s3_to_s4",
        (STATE_EASING, STATE_REPAIR): "s4_to_s5",
        (STATE_REPAIR, STATE_STRONG): "s5_to_s6",
    }
    out = {}
    up_key = key_map.get((state, nxt))
    if up_key:
        out["upgrade"] = sc.get(up_key, "")
    down_key = "s4_to_s3" if state == STATE_EASING else None
    if down_key:
        out["downgrade"] = sc.get(down_key, "")
    out["s6_exit"] = sc.get("any_to_s6_exit", "")
    return out


def analyze_trade_date_v107(trade_date: str, cfg: dict = None,
                            current_position: Optional[int] = None,
                            persist: bool = True,
                            optimizer_history: Optional[Dict] = None,
                            swing_gate_history: Optional[Dict] = None,
                            bull_latch_history: Optional[Dict] = None) -> V107Result:
    """V10.7 完整链路。

    V10.1 决策记录（完整保留） → 量价判定 → S4确认 → S5确认 → 结构性机会
    → 状态门控 → 按**门控后状态**重算权限/授权/仓位 → 仓位语义分离
    → P0 风险口径修复 → P1/P2 仓位优化（平滑器 / 确认门控 / 语义边界）
    → V10.7.3 波段闸门（纯收紧型 overlay，默认只开 S2/S5 状态过滤）
    → V10.7.4 主升段锁存（默认关闭的升仓层，需 P0 重算 + 满仓授权）

    ``optimizer_history``：回放时可传入一个可变 dict 作为内存历史链；
    不传则读 ``position_optimizer_state`` 表中严格早于当日的记录。
    ``swing_gate_history``：波段闸门的独立内存历史链（``swing_gate_state`` 表）。
    ``bull_latch_history``：主升段锁存的独立内存历史链（``bull_latch_state`` 表）。
    """
    cfg = cfg or load_config()
    out = V107Result(trade_date=str(trade_date))

    # ---- 1) V10.1 完整链路（原有模块一行不改） ----
    base = v101.analyze_trade_date_v101(str(trade_date), cfg=v101.load_config(),
                                       current_position=current_position,
                                       persist=persist)
    out.base = base
    base_state = base.state
    base_dec = base.v10_decision

    # ---- 2) 量价判定 ----
    series = micro.load_series(trade_date, 12)
    out.amount = amount_verdict(trade_date, cfg)

    # ---- 3) S4 卖压衰减确认 ----
    out.easing = check_easing_confirmation(trade_date, cfg, micro_series=series)

    # ---- 4) S5 反弹确认 ----
    ml_mod = (base.strong.modules.get("主线持续性") if base.strong else None)
    out.rebound = check_rebound_confirmation(trade_date, cfg,
                                             mainline_module=ml_mod,
                                             micro_series=series,
                                             amount=out.amount)

    # ---- 5) 结构性机会（按**基础状态**判定，允许状态 S3/S4/S6） ----
    mkt_up_ratio = None
    if series and str(series[-1]['trade_date']) == str(trade_date):
        mkt_up_ratio = series[-1].get('up_ratio')
    out.structural = check_structural_alpha(str(trade_date), base_state, cfg,
                                             market_up_ratio=mkt_up_ratio)

    # ---- 6) 状态门控 ----
    out.gate = apply_state_gates(base_state, base_dec, out.easing, out.rebound,
                                  str(trade_date), cfg, micro_series=series)
    state = out.gate.effective_state
    out.state = state
    out.state_cn = STATE_CN.get(state, state)

    # ---- 7) 按门控后状态重算权限 / 授权 / 仓位 ----
    authority = v101.position_authority(state, cfg)
    # ★ 安全网：门控只允许收紧仓位，绝不允许因为状态回退而放大权限
    base_cap = int(v101.position_authority(base_state, cfg).max_position)
    if authority.max_position > base_cap:
        authority.max_position = base_cap
        authority.note = (f"门控后权限收紧至 {base_cap}%"
                          f"（原状态 {base_state} 权限，不得因状态回退而放大）")
    out.authority = authority
    portfolio = base.portfolio
    authorization = v101.full_position_authorization(
        state,
        base.strong.modules.get("指数趋势") if base.strong else None,
        base.strong.modules.get("市场广度") if base.strong else None,
        base.strong.modules.get("短线赚钱效应") if base.strong else None,
        base.strong.modules.get("主线持续性") if base.strong else None,
        base.opportunities, portfolio, cfg)
    out.authorization = authorization
    target = v101.calculate_target_position(state, authority, base.opportunities,
                                            portfolio, authorization,
                                            current_position, cfg)
    out.target = target
    out.portfolio = target.portfolio if target.portfolio else portfolio
    out.plan = build_position_plan(state, target, authority, out.portfolio,
                                   authorization, base.opportunities,
                                   current_position, cfg)

    # ---- 7b) ★ P0 修复：统一风险口径 + 决策/报告同一对象 + 风险上限闭环 ----
    #   v10/v101 源码一行不动；r.base 的 V10.1 原决策记录不被修改。
    out.p0 = p0fix.apply_p0_fix(out, cfg)
    out.plan = build_position_plan(state, out.target, authority, out.portfolio,
                                   authorization, base.opportunities,
                                   current_position, cfg,
                                   risk_cap=out.p0.risk_cap)
    out.p0.hard_cap_after = out.plan.hard_cap
    out.p0.changed = bool(out.p0.changed
                          or (out.p0.hard_cap_before is not None
                              and out.p0.hard_cap_before != out.plan.hard_cap))
    if out.p0.consistent is None and out.p0.enabled:
        # 止损距离缺失 → UNKNOWN，不得默认通过
        out.plan.allow_active_add = False
        out.plan.active_add_conditions.append(
            "组合风险口径 UNKNOWN（止损距离缺失），不得默认通过")
        out.plan.limits.append("主动试探权限因风险口径 UNKNOWN 被关闭")
    out.switch_conditions = _switch_conditions(state, cfg)

    # ---- 7c) ★ P1/P2 仓位优化（增量层；enabled=false 时完全不施加） ----
    #   历史链严格早于当日；波动率序列由模块自行按 trade_date 截断加载。
    out.current_position = current_position
    hist = optimizer_history if optimizer_history is not None else \
        posopt.load_history(str(trade_date))
    # 历史链过期校验由 posopt.compute 内部按 trade_date 截断完成（无未来数据）
    adj = posopt.compute(out, cfg, history=hist, series=None,
                         current_position=current_position)
    out.opt = adj.audit
    # plan 重建参数集中定义：7c（P1/P2）与 7d（波段闸门）共用，
    # 且 A/B 关闭 P1/P2 时仍保持 7b 的 risk_cap，不会放宽硬上限。
    waived = bool((adj.audit.cash_floor or {}).get("waived"))
    _cap_kwargs = dict(
        risk_cap=(adj.risk_cap if adj.enabled
                  else (out.p0.risk_cap if out.p0 is not None else None)),
        extra_cap=(adj.extra_cap if adj.enabled else None),
        lo_ratio=(adj.lo_ratio if adj.enabled else None),
        waive_cash_floor=waived,
    )

    def _rebuild_plan():
        return build_position_plan(
            state, out.target, authority, out.portfolio, authorization,
            base.opportunities, current_position, cfg, **_cap_kwargs)

    if adj.enabled:
        old_t = int(out.target.suggested)
        out.target.suggested = int(adj.target_override)
        if not hasattr(out.target, "limited_by") or out.target.limited_by is None:
            out.target.limited_by = []
        if adj.target_override != old_t:
            out.target.limited_by.append(
                f"[P1] 仓位平滑器：目标 {old_t}% → {adj.target_override}%"
                f"（升仓限速/降仓冷却，硬上限不受影响）")

        out.plan = _rebuild_plan()
        if out.target.suggested > out.plan.hard_cap:
            # 只收紧不放宽：目标不得超过确认门控/风险预算后的硬上限
            out.target.suggested = int(out.plan.hard_cap)
            out.plan = _rebuild_plan()
        if adj.deny_active_add:
            out.plan.allow_active_add = False
            out.plan.limits = [x for x in out.plan.limits
                               if not str(x).startswith("允许主动加仓")]
            out.plan.active_add_conditions.extend(adj.deny_reasons)
            out.plan.limits.extend(adj.deny_reasons)
        out.plan.cash_floor_note = adj.cash_floor_note
        out.ladder_patch = adj.ladder_patch
        adj.audit.target_after = out.plan.target_position
        adj.audit.hard_cap_after = out.plan.hard_cap
        adj.history_record["target"] = out.plan.target_position
        adj.history_record["hard_cap"] = out.plan.hard_cap
        if persist or (optimizer_history is None
                       and (cfg.get("position_optimizer") or {})
                       .get("persist_history_without_state", True)):
            # ★ 盘后/微信链路用 persist=False，但平滑器仍需自己的历史链：
            #   写独立表，严格按 trade_date 读取，不触碰 V10.0/V10.1 状态表。
            posopt.save_history(str(trade_date), adj.history_record)
        if optimizer_history is not None and adj.history_record:
            optimizer_history.clear()
            optimizer_history.update(adj.history_record)

    # ---- 7d) ★ V10.7.3 波段闸门（纯收紧型 overlay；enabled=false 完全不施加） ----
    #   作用在 7c 之后的最终目标上；闸门有自己的独立历史链，
    #   P1 平滑器历史链仍存「闸门之前」的目标（与回测实验口径一致）。
    sg_base = int(out.plan.target_position)
    _sgcfg = cfg.get("swing_gate") or {}
    sg_hist = (swing_gate_history if swing_gate_history is not None
               else swing_gate.load_history(str(trade_date)))
    sg = swing_gate.compute(out, cfg, base_target=sg_base,
                            hard_cap=int(out.plan.hard_cap),
                            history=sg_hist)
    out.swing = sg.audit
    if sg.enabled and sg.effective_target is not None \
            and sg.effective_target < sg_base:
        old_eff = int(out.target.suggested)
        out.target.suggested = int(sg.effective_target)
        if not hasattr(out.target, "limited_by") or out.target.limited_by is None:
            out.target.limited_by = []
        out.target.limited_by.append(
            f"[波段闸门] 有效目标 {old_eff}% → {sg.effective_target}%"
            f"（档位 {(sg.audit.profile or 'default')}，只收紧不放宽）")
        out.plan = _rebuild_plan()
        # 重建会重置 7c 的附加约束，按原口径重新施加
        if adj.enabled and adj.deny_active_add:
            out.plan.allow_active_add = False
            out.plan.limits = [x for x in out.plan.limits
                               if not str(x).startswith("允许主动加仓")]
            out.plan.active_add_conditions.extend(adj.deny_reasons)
            out.plan.limits.extend(adj.deny_reasons)
        if sg.deny_active_add:
            out.plan.allow_active_add = False
            out.plan.limits = [x for x in out.plan.limits
                               if not str(x).startswith("允许主动加仓")]
            out.plan.active_add_conditions.append(
                "波段闸门信号 UNKNOWN，不默认通过 → 关闭主动加仓")
            out.plan.limits.append("主动试探权限因波段闸门 UNKNOWN 被关闭")
        out.plan.cash_floor_note = adj.cash_floor_note
        # ★ 不回填 adj.audit：P1/P2 段的 target_after 仍反映 P1/P2 自身结果，
        #   闸门段用 base_target→effective_target 单独呈现，便于逐层对比。
    sg.audit.target_after = int(out.plan.target_position)
    if sg.enabled and sg.history_record:
        if persist or (swing_gate_history is None
                       and _sgcfg.get("persist_history_without_state", True)):
            # 与 P1 同理：盘后/微信链路 persist=False 也要写闸门独立历史表
            swing_gate.save_history(str(trade_date), sg.history_record)
        if swing_gate_history is not None:
            swing_gate_history.clear()
            swing_gate_history.update(sg.history_record)

    # ---- 7e) ★ V10.7.4 主升段锁存（默认关闭；突破 hard_cap 需显式授权） ----
    #   作用在 7d 之后的有效目标上；自己的历史链不污染 P1 / 闸门链。
    #   enabled=false 时 compute 直接返回 base，下面完全不动 plan（A/B 逐字节一致）。
    bl_base = int(out.plan.target_position)
    _blcfg = cfg.get("bull_latch") or {}
    bl_hist = (bull_latch_history if bull_latch_history is not None
               else bull_latch.load_history(str(trade_date)))
    bl = bull_latch.compute(out, cfg, base_target=bl_base,
                            hard_cap=int(out.plan.hard_cap),
                            history=bl_hist)
    out.bull = bl.audit
    bl_changed = bool(bl.enabled and (
        (bl.effective_target is not None and bl.effective_target != bl_base)
        or bl.authorized_hard_cap is not None))
    if bl_changed:
        old_eff = int(out.target.suggested)
        new_eff = int(bl.effective_target if bl.effective_target is not None
                      else old_eff)
        out.target.suggested = new_eff
        if not hasattr(out.target, "limited_by") or out.target.limited_by is None:
            out.target.limited_by = []
        out.target.limited_by.append(
            f"[主升段锁存] 有效目标 {old_eff}% → {new_eff}%"
            f"（档位 {(bl.audit.profile or 'recommended')}，"
            f"授权 {bl.audit.authorization.get('mode', 'NOT_NEEDED')}）")
        if bl.authorized_hard_cap is not None \
                and bl.authorized_hard_cap > out.plan.hard_cap:
            # ★ 不是绕过上限，而是经 P0 重算 + 满仓授权后重算上限：
            #   只放宽状态权限一项，其余约束仍逐项取 min。
            _cap_kwargs["authorized_cap"] = int(bl.authorized_hard_cap)
            if _blcfg.get("waive_cash_floor_on_latch", False):
                _cap_kwargs["waive_cash_floor"] = True
        out.plan = _rebuild_plan()
        if out.target.suggested > out.plan.hard_cap:
            out.target.suggested = int(out.plan.hard_cap)
            out.plan = _rebuild_plan()
        # 重建会重置 7c/7d 的附加约束，按原口径重新施加
        if adj.enabled and adj.deny_active_add:
            out.plan.allow_active_add = False
            out.plan.limits = [x for x in out.plan.limits
                               if not str(x).startswith("允许主动加仓")]
            out.plan.active_add_conditions.extend(adj.deny_reasons)
            out.plan.limits.extend(adj.deny_reasons)
        if sg.enabled and sg.deny_active_add:
            out.plan.allow_active_add = False
            out.plan.limits = [x for x in out.plan.limits
                               if not str(x).startswith("允许主动加仓")]
            out.plan.active_add_conditions.append(
                "波段闸门信号 UNKNOWN，不默认通过 → 关闭主动加仓")
            out.plan.limits.append("主动试探权限因波段闸门 UNKNOWN 被关闭")
        if bl.deny_active_add:
            out.plan.allow_active_add = False
            out.plan.limits = [x for x in out.plan.limits
                               if not str(x).startswith("允许主动加仓")]
            out.plan.active_add_conditions.append(
                "主升段锁存信号 UNKNOWN，不默认通过 → 关闭主动加仓")
            out.plan.limits.append("主动试探权限因主升段锁存 UNKNOWN 被关闭")
        out.plan.cash_floor_note = adj.cash_floor_note
    bl.audit.target_after = int(out.plan.target_position)
    if bl.enabled and bl.history_record:
        if persist or (bull_latch_history is None
                       and _blcfg.get("persist_history_without_state", True)):
            # 与 P1/闸门同理：盘后/微信链路 persist=False 也写独立历史表
            bull_latch.save_history(str(trade_date), bl.history_record)
        if bull_latch_history is not None:
            bull_latch_history.clear()
            bull_latch_history.update(bl.history_record)

    # ---- 8) 状态连续天数（V10.7 自己的表） ----
    prev = load_prev_v107_state(str(trade_date)) if persist else None
    out.state_days = (prev["consecutive_days"] + 1) if (prev and prev["state"] == state) else 1

    # ---- 9) 持久化 ----
    if persist:
        save_v107_state(str(trade_date), state, base_state, out.state_days,
                        list(base_dec.reasons or []) if base_dec else [],
                        out.easing.verdict, out.rebound.verdict,
                        out.structural.verdict, out.plan.target_position,
                        out.plan.hard_cap,
                        out.gate.blocked + out.gate.applied)
    return out


# ============================================================
# 报告渲染
# ============================================================
def render_v107_report(r: V107Result, cfg: dict = None) -> str:
    cfg = cfg or load_config()
    L = []
    L.append("═" * 66)
    L.append("      A股市场环境与仓位决策系统  V10.7（增量优化版）")
    L.append("═" * 66)
    L.append(f"交易日：{r.trade_date}")

    # ---------- 1) 状态与三项确认 ----------
    L.append("")
    L.append("【市场状态】")
    L.append(f"  当前状态：{r.state} {r.state_cn}")
    if r.gate and r.gate.base_state != r.state:
        L.append(f"  原始状态（V10.1）：{r.gate.base_state} {STATE_CN.get(r.gate.base_state, '')}"
                 f" → 门控后 {r.state}")
    for b in (r.gate.blocked if r.gate else []):
        L.append(f"  ⚠ 门控：{b}")
    for a in (r.gate.applied if r.gate else []):
        L.append(f"  ✓ 门控：{a}")
    L.append(f"  卖压衰减确认：{r.easing.verdict if r.easing else VERDICT_UNKNOWN}")
    L.append(f"  反弹确认：{r.rebound.verdict if r.rebound else VERDICT_UNKNOWN}")
    L.append(f"  结构性机会：{r.structural.verdict if r.structural else VERDICT_UNKNOWN}")

    # ---------- 2) 量价 ----------
    if r.amount:
        a = r.amount
        L.append("")
        L.append("【量价判定】")
        L.append(f"  环比成交额变化率：{a.chg_vs_prev:+.2f}%"
                 if a.chg_vs_prev is not None else "  环比成交额变化率：UNKNOWN")
        L.append(f"  相对5日均成交额变化率：{a.chg_vs_ma5:+.2f}%"
                 if a.chg_vs_ma5 is not None else "  相对5日均成交额变化率：UNKNOWN")
        L.append(f"  指数涨跌幅：{a.index_chg:+.2f}%"
                 if a.index_chg is not None else "  指数涨跌幅：UNKNOWN")
        L.append(f"  上涨股票占比：{a.up_ratio*100:.2f}%"
                 if a.up_ratio is not None else "  上涨股票占比：UNKNOWN")
        L.append(f"  形态：{a.pattern}"
                 + ("　★放量上涨（放量↑ + 指数↑ + 广度达标，三要素齐备）"
                    if a.is_volume_up else "　（未同时满足三要素，不计为放量上涨）"))

    # ---------- 3) S4 确认明细 ----------
    if r.easing:
        L.append("")
        L.append("【卖压衰减确认】" + VERDICT_CN.get(r.easing.verdict, ""))
        for k, v in r.easing.checks.items():
            L.append(f"    [{'✓' if v else '✗'}] {k}")
        for e in r.easing.evidence:
            L.append(f"        {e}")
        if r.easing.missing:
            L.append(f"    缺失：{'; '.join(r.easing.missing[:3])}")

    # ---------- 4) S5 确认明细 ----------
    if r.rebound:
        L.append("")
        L.append("【反弹确认】" + VERDICT_CN.get(r.rebound.verdict, ""))
        for k, v in r.rebound.checks.items():
            L.append(f"    [{'✓' if v else '✗'}] {k}")
        for e in r.rebound.evidence:
            L.append(f"        {e}")
        if r.rebound.missing:
            L.append(f"    缺失：{'; '.join(r.rebound.missing[:3])}")

    # ---------- 5) 结构性机会 ----------
    if r.structural:
        s = r.structural
        L.append("")
        L.append("【结构性机会】" + VERDICT_CN.get(s.verdict, ""))
        L.append(f"  最强行业：{s.industry or '—'}")
        L.append(f"  行业持续性：{s.days_strong if s.days_strong is not None else 'UNKNOWN'} 日"
                 f"（连续强态，要求 ≥{int((cfg.get('structural_alpha') or {}).get('min_days_strong', 15))} 日）")
        L.append(f"  行业内部广度：当日 "
                 + (f"{s.breadth_up_ratio*100:.1f}%" if s.breadth_up_ratio is not None else "UNKNOWN")
                 + "｜20日 "
                 + (f"{s.breadth_up20_ratio*100:.1f}%" if s.breadth_up20_ratio is not None else "UNKNOWN"))
        if s.checks:
            for k, v in s.checks.items():
                L.append(f"    [{'✓' if v else '✗'}] 入池条件：{k}")
        if s.verification:
            for k, v in s.verification.items():
                L.append(f"    [{'✓' if v else '✗'}] 复核：{k}")
        for e in s.evidence:
            L.append(f"        {e}")
        if s.missing:
            L.append(f"    缺失：{'; '.join(s.missing[:3])}")
        if s.position_note:
            L.append(f"  授权口径：{s.position_note}")

    # ---------- 6) 仓位 ----------
    if r.plan:
        p = r.plan
        L.append("")
        L.append("【仓位】")
        L.append(f"  目标仓位：{p.target_position}%　（环境层面参考值，**不是必须达到的持仓比例**）")
        L.append(f"  建议仓位区间：{p.suggested_low}% ~ {p.suggested_high}%")
        L.append(f"  仓位硬上限：{p.hard_cap}%")
        L.append(f"  主动试探权限：{'允许' if p.allow_active_add else '不允许'}")
        for c in p.active_add_conditions:
            L.append(f"        · {c}")
        L.append(f"  满仓授权：{p.full_authorization}"
                 + (f"（{v101.FULL_CN.get(p.full_authorization, '')}）"
                    if p.full_authorization else ""))
        if p.actual_hint is not None:
            L.append(f"  实际仓位参考：{p.actual_hint}%")
        if p.actual_note:
            L.append(f"        {p.actual_note}")
        if getattr(p, "cash_floor_note", ""):
            L.append(f"  现金地板口径：{p.cash_floor_note}")

    # ---------- 6b) P0 风险口径修复（可对比） ----------
    if r.p0 is not None:
        L.extend(p0fix.render_p0_section(r.p0))

    # ---------- 6c) P1/P2 仓位优化（可对比） ----------
    if getattr(r, "opt", None) is not None:
        L.extend(posopt.render_optimizer_section(r.opt))

    # ---------- 6d) V10.7.3 波段闸门（可对比） ----------
    if getattr(r, "swing", None) is not None:
        L.extend(swing_gate.render_gate_section(r.swing))

    # ---------- 6e) V10.7.4 主升段锁存（可对比） ----------
    if getattr(r, "bull", None) is not None:
        L.extend(bull_latch.render_latch_section(r.bull))

    # ---------- 7) 切换关键条件 ----------
    if r.switch_conditions:
        L.append("")
        L.append("【切换关键条件】")
        for k, v in r.switch_conditions.items():
            if v:
                L.append(f"  {k}：{v}")

    # ---------- 8) V10.1 原决策记录（保留，便于新旧对比） ----------
    if r.base is not None:
        L.append("")
        L.append("─" * 66)
        L.append("【V10.1 原决策记录（保留，用于新旧对比）】")
        L.append("─" * 66)
        try:
            L.append(v101.render_v101_report(r.base, v101.load_config()))
        except Exception as e:
            L.append(f"  （V10.1 报告渲染失败：{e}）")

    L.append("")
    L.append("═" * 66)
    return "\n".join(L)


def render_v107_summary(r: V107Result) -> Dict[str, Any]:
    """输出要求的关键字段（结构化）。"""
    p = r.plan
    return {
        "trade_date": r.trade_date,
        "市场状态": f"{r.state} {r.state_cn}",
        "卖压衰减确认": r.easing.verdict if r.easing else VERDICT_UNKNOWN,
        "反弹确认": r.rebound.verdict if r.rebound else VERDICT_UNKNOWN,
        "结构性机会": r.structural.verdict if r.structural else VERDICT_UNKNOWN,
        "最强行业": r.structural.industry if r.structural else None,
        "行业持续性": (f"{r.structural.days_strong} 日"
                       if r.structural and r.structural.days_strong is not None else None),
        "行业内部广度": (f"当日 {r.structural.breadth_up_ratio*100:.1f}% / "
                        f"20日 {r.structural.breadth_up20_ratio*100:.1f}%"
                        if r.structural and r.structural.breadth_up_ratio is not None
                        else None),
        "主动试探权限": (p.allow_active_add if p else None),
        "建议仓位": (f"{p.suggested_low}%~{p.suggested_high}%" if p else None),
        "仓位硬上限": (p.hard_cap if p else None),
        "满仓授权": (p.full_authorization if p else None),
        "切换关键条件": r.switch_conditions,
        # ★ P0 / P1P2 的口径对比（不改变上面用户要求的字段契约）
        "P0风险口径修复": (r.p0.as_dict() if r.p0 is not None else None),
        "P1P2仓位优化": (r.opt.as_dict() if getattr(r, "opt", None) is not None else None),
        "波段闸门": (r.swing.as_dict() if getattr(r, "swing", None) is not None else None),
        "主升段锁存": (r.bull.as_dict() if getattr(r, "bull", None) is not None else None),
    }


def _json_default(o):
    """numpy 标量/数组 → Python 原生类型。

    既有 ``--json`` 链路在 ``structural.checks`` 遇到 numpy.bool_ 时会抛
    「Object of type bool is not JSON serializable」（numpy 2.x 的类型名），
    这里只做序列化兜底，不改变任何决策值。
    """
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def main():
    ap = argparse.ArgumentParser(description="A股市场环境与仓位决策系统 V10.7")
    ap.add_argument("trade_date", nargs="?", help="交易日 YYYYMMDD")
    ap.add_argument("--current-position", type=int, help="当前实际仓位（%%）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-persist", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    td = args.trade_date or datetime.datetime.now().strftime('%Y%m%d')
    r = analyze_trade_date_v107(td, cfg, args.current_position,
                                 persist=not args.no_persist)
    if args.json:
        print(json.dumps({
            "summary": render_v107_summary(r),
            "amount": {
                "chg_vs_prev": r.amount.chg_vs_prev if r.amount else None,
                "chg_vs_ma5": r.amount.chg_vs_ma5 if r.amount else None,
                "index_chg": r.amount.index_chg if r.amount else None,
                "up_ratio": r.amount.up_ratio if r.amount else None,
                "pattern": r.amount.pattern if r.amount else None,
                "is_volume_up": r.amount.is_volume_up if r.amount else None,
            },
            "easing": {"verdict": r.easing.verdict, "checks": r.easing.checks,
                       "evidence": r.easing.evidence, "missing": r.easing.missing},
            "rebound": {"verdict": r.rebound.verdict, "checks": r.rebound.checks,
                        "evidence": r.rebound.evidence, "missing": r.rebound.missing},
            "structural": {
                "verdict": r.structural.verdict if r.structural else None,
                "industry": r.structural.industry if r.structural else None,
                "checks": r.structural.checks if r.structural else {},
                "verification": r.structural.verification if r.structural else {},
                "evidence": r.structural.evidence if r.structural else [],
                "position_note": r.structural.position_note if r.structural else "",
            },
            "gate": {"base_state": r.gate.base_state if r.gate else None,
                     "effective_state": r.gate.effective_state if r.gate else None,
                     "blocked": r.gate.blocked if r.gate else [],
                     "applied": r.gate.applied if r.gate else []},
            "base_v101": {
                "state": r.base.state if r.base else None,
                "suggested": r.base.target.suggested if (r.base and r.base.target) else None,
                "authority_max": r.base.authority.max_position if (r.base and r.base.authority) else None,
            },
            "p0": r.p0.as_dict() if r.p0 is not None else None,
        }, ensure_ascii=False, indent=2, default=_json_default))
    else:
        print(render_v107_report(r, cfg))


if __name__ == "__main__":
    main()
