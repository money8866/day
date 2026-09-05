# -*- coding: utf-8 -*-
"""
Trade Execution Engine V3.1 —— 从 W7 候选股 → 次日可执行交易指令
================================================================
输入：w7_second_wave_{date}.md 的候选池（TOP20 / A榜 / B榜 / MID-HVT / 已突破名单）
处理：①解析候选 → ②补算技术结构(ATR/MA60/前高/支撑/量价性质) → ③逐票分类与门禁
      → ④Execution Score → ⑤V3.1 状态×质量×量能×市场环境门控 → ⑥输出明日执行指令报告

V3.1 相对 V3.0 的优化（源自 20240101~20260828 全市场回测对照）：
    G1 状态剔除：EXTREME_CHURN 永不 BUY（历史放量换手池右尾虽大但中位-2.2% 过热）；
    G2 状态×质量准入：SECOND_WAVE/DRYUP 无条件放行（历史 er20 均值 +2.0%/+2.5%），
       BREAKOUT_CONFIRM/RE_EXPANSION/ABSORPTION 需 Execution≥85，其余结构不给 BUY；
    G3 量比上界 volr≤2.2：放量过热追涨改等回踩（volr≥2 中位 -2.28%）；
    G4 市场环境门：全市场等权曲线 regime0（回撤>5% 或破 MA60×0.97）关闭 BUY；
    G5 止损重建：预警线之外增加结构失效位（0.93×Trigger / Trigger-4ATR），
       长线持仓用结构位，避免 3% 近止损在 20 天窗口高频扫损（历史命中率 78%）。

核心纪律（规范）：
    Alpha高分 ≠ 买点。必须过 Entry/Structure/Risk Gate 才能给 BUY。
    宁可错过，不追高；突破买确认，强势等回踩，吸筹等启动。
    每天最多少量 EXECUTABLE/CONDITIONAL，其余如实标注 WAIT/WATCH/AVOID。

用法：
    python trade_execution_engine.py --date 20260904 [--out report_daily/trade_execution_20260904.md]
"""
import argparse
import os
import re
import sys
from collections import OrderedDict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from w7_second_wave_engine import CacheReader, CACHE_DIR, OUTPUT_DIR  # noqa: E402

# ---------------------------------------------------------------- 常量（V3.0 规格可调）
BROKEN_STATES = {"BREAKOUT_CONFIRM", "SECOND_WAVE", "RE_EXPANSION"}   # 已突破类
NO_CHASE_STATES = {"EXTREME_CHURN"}                                   # 默认禁止追涨
BROKEN_AVOID_BELOW = 0.98        # 已突破标的收盘跌破触发价×0.98 → AVOID
BUY_BAND_LO = 0.985              # Buy Zone 下沿 = Trigger×0.985（Trigger±1.5%）
BUY_BAND_HI = 1.015              # Buy Zone 上沿 = Trigger×1.015
EXT_HI_COND = 0.02               # 现价≤Trigger×1.02 → CONDITIONAL(等回踩)；更高→WAIT RETEST
EXT_NOCHASE = 0.05               # 现价>Trigger×1.05 → NO CHASE，直接降级
GAP_NO_CHASE = 0.05              # 次日高开 >5% 默认不追
VOLR_BREAK_LOW = 1.2             # 有效放量突破量比下限
ALPHA_MIN_PRIMARY = 60.0         # PRIMARY BUY 硬门槛：Alpha≥60
DRISK_MAX_PRIMARY = 20.0         # PRIMARY BUY 硬门槛：DRisk≤20
# V3.0 Execution Score：Entry30 / Structure20 / Retest20 / Volume15 / Risk10 / Lifecycle5，Alpha=0
WEIGHTS = dict(eq=0.30, struct=0.20, retest=0.20, vol=0.15, risk=0.10, life=0.05)

# ---------------- V3.1 门控与止损重建（源自全市场回测对照） ----------------
P1_STATES = {"SECOND_WAVE", "DRYUP"}                  # G2 状态准入：P1 无条件可用
P2_STATES = {"BREAKOUT_CONFIRM", "RE_EXPANSION", "ABSORPTION"}  # P2 需 Execution≥P2_EXEC_MIN
P2_EXEC_MIN = 85.0                                    # G2 质量门槛
VOLR_MAX = 2.2                                        # G3 量比上界（> 视为放量过热）
REGIME_MIN = 1                                        # G4 市场环境：regime < 1（风险0）关闭 BUY
# G5 止损重建：预警线(stop) + 结构失效位(stop_struct)，长线持仓以结构位退出
STOP_K = 0.97
STOP_ATR = 2.2
STOP_STRUCT_K = 0.93
STOP_STRUCT_ATR = 4.0


def clip(v, lo=0.0, hi=100.0):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return lo


def parse_md_pool(md_path):
    """解析 W7 报告：18 列表格(TOP20/A/B/MID) → 完整候选；10/6 列表格 → 参考信息。返回 (cands, refs)。"""
    with open(md_path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    cands, refs = {}, {}
    cur_section = ""
    for ln in lines:
        if ln.startswith("## "):
            cur_section = ln.strip("# ").strip()
            continue
        if not ln.startswith("| "):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 3 or not cells[0].isdigit():
            continue  # 表头/分隔/说明行
        # 数据行：cells[0]=序号 cells[1]=代码 cells[2]=名称
        if len(cells) == 18:
            code, name = cells[1], cells[2]
            try:
                rec = dict(code=code, name=name, score=float(cells[3]), type=cells[4], close=float(cells[5]),
                           pressure=float(cells[6]), ma20=float(cells[7]), volr=float(cells[8].lstrip("×")),
                           hvt=float(cells[9]), absorption=float(cells[10]), life=float(cells[11]),
                           space=float(cells[12]), accel=float(cells[13]), rs=float(cells[14]),
                           fina=float(cells[15]), drisk=float(cells[16]), state=cells[17],
                           section=cur_section.split("（")[0].split("　")[0])
            except ValueError:
                continue
            cands[code] = rec  # 后出现覆盖前（B/MID 与 TOP20 重复时用明细榜字段一致）
        elif len(cells) == 10:  # 已突破名单（无 v5 dims）
            refs.setdefault("broken_list", []).append(
                dict(code=cells[1], name=cells[2], score=float(cells[3]), type=cells[4],
                     close=float(cells[5]), pressure=float(cells[6]), ma20=float(cells[7]),
                     volr=float(cells[8].lstrip("×")), state=cells[9]))
        elif len(cells) == 6:   # C榜 潜力票（参考）
            refs.setdefault("watch_potential", []).append(
                dict(code=cells[1], name=cells[2], score=float(cells[3]), type=cells[4],
                     state=cells[5], reason=cells[6]))
    return cands, refs


def tech_features(reader, code, date):
    """从日线缓存补算结构指标。无数据返回 None。"""
    df = reader.bars(code, date)
    if df is None or len(df) < 30:
        return None
    df = df.reset_index(drop=True)
    cur, prev = df.iloc[-1], df.iloc[-2]
    close, high, low = float(cur.close), float(cur.high), float(cur.low)
    opn = float(cur.open)
    # ATR14（简单平均真实波幅）
    prev_close = df.close.shift(1)
    tr = pd.concat([(df.high - df.low), (df.high - prev_close).abs(), (df.low - prev_close).abs()], axis=1).max(axis=1)
    atr = float(tr.tail(14).mean())
    ma60 = float(df.ma_bfq_60.iloc[-1]) if pd.notna(df.ma_bfq_60.iloc[-1]) else float(df.close.tail(60).mean())
    ma60_slope = (ma60 / float(df.ma_bfq_60.iloc[-21]) - 1) if (len(df) >= 81 and pd.notna(df.ma_bfq_60.iloc[-21])) else 0.0
    high20 = float(df.high.tail(21).iloc[:-1].max()) if len(df) >= 21 else high   # 近20日前高(不含当日)
    high60 = float(df.high.tail(61).iloc[:-1].max()) if len(df) >= 61 else high
    low10 = float(df.low.tail(10).min())                                        # 近10日支撑
    low20 = float(df.low.tail(20).min())
    ma20 = float(df.ma_bfq_20.iloc[-1]) if pd.notna(df.ma_bfq_20.iloc[-1]) else float(df.close.tail(20).mean())
    ma10 = float(df.ma_bfq_10.iloc[-1]) if ("ma_bfq_10" in df.columns and pd.notna(df.ma_bfq_10.iloc[-1])) \
        else float(df.close.tail(10).mean())
    # 当日 K 线性质
    upper_shadow = high - max(opn, close)
    is_yang = close >= opn
    long_shadow = upper_shadow > 0.5 * atr if atr > 0 else False
    ret20 = close / float(df.close.iloc[-21]) - 1 if len(df) >= 21 else 0.0
    return dict(atr=atr, ma60=ma60, ma60_slope=ma60_slope, ma20=ma20, ma10=ma10, high20=high20, high60=high60,
                low10=low10, low20=low20, is_yang=is_yang, long_shadow=long_shadow, ret20=ret20,
                close=close, high=high, low=low)


def classify(c, t):
    """返回 (entry_type, eq, action, reason)。规则按 Trade Execution Engine V3.0 规范量化。

    V3.0 要点：
      · Buy Zone = Trigger ±1.5%；现价在区内+结构完好 → PRIMARY(过门槛后)/CONDITIONAL；
        略高(≤+2%) → CONDITIONAL；+2%~+5% → WAIT RETEST；>+5% → NO CHASE。
      · PRIMARY 硬门槛：Alpha≥60 且 DRisk≤20（未过则自动降级 CONDITIONAL BUY）。
      · ABSORPTION/EXTREME_CHURN 永远不给 PRIMARY；ABSORPTION 需放量突破才能升级。
    """
    close, pressure = c["close"], c["pressure"]
    ext = close / pressure - 1.0                      # 现价相对触发价的偏离(>0 在上方)
    below = pressure / close - 1.0 if close > 0 else 9.9  # 距突破触发价还需涨幅
    volr, accel, drisk, alpha = c["volr"], c["accel"], c["drisk"], c["score"]
    state = c["state"]

    def gate_primary(eq_, action_, reason_, entry_):
        """PRIMARY 硬门槛：Alpha≥60 & DRisk≤20；否则降级 CONDITIONAL，不编造 PRIMARY。"""
        if action_ == "PRIMARY BUY" and (alpha < ALPHA_MIN_PRIMARY or drisk > DRISK_MAX_PRIMARY):
            fails = []
            if alpha < ALPHA_MIN_PRIMARY:
                fails.append(f"Alpha {alpha:.1f}<{ALPHA_MIN_PRIMARY:.0f}")
            if drisk > DRISK_MAX_PRIMARY:
                fails.append(f"DRisk {drisk:.0f}>{DRISK_MAX_PRIMARY:.0f}")
            action_ = "CONDITIONAL BUY"
            eq_ = min(eq_, 88.0)
            reason_ += f"（未过 PRIMARY 硬门槛：{'、'.join(fails)}，降级按条件买）"
        return entry_, eq_, action_, reason_

    # 0) 硬否决
    if drisk >= 55 or state == "FAILED":
        return "NA", 30.0, "AVOID", f"DRisk={drisk:.0f} 或状态 {state}，风险收益比差"
    if c["type"] == "DISTRIBUTION":
        return "NA", 30.0, "AVOID", "DISTRIBUTION 派发类型不进执行"

    # 1) 已突破类（BREAKOUT_CONFIRM / SECOND_WAVE / RE_EXPANSION）—— 突破后回踩是首选买点
    if state in BROKEN_STATES:
        if close < pressure * BROKEN_AVOID_BELOW:
            return "BROKEN", 38.0, "AVOID", f"收盘 {close:.2f} 已跌破触发价 {pressure:.2f}，结构失效"
        if ext < 0:
            if ext < -0.015:
                return "RETEST", 72.0, "WAIT", f"回踩到 Buy Zone 下方({ext:.1%})，尚未重新站回 {pressure:.2f}，等确认"
            if volr > 1.8 and not t["is_yang"]:
                return "RETEST", 68.0, "WAIT", "回踩放量收阴，等止跌企稳信号，不接"
            eq, action, entry_type = 86.0, "CONDITIONAL BUY", "RETEST"
            reason = f"回踩入买区（{ext:.1%}）未有效跌破，次日不破 {pressure:.2f}+承接 → 执行"
        elif ext <= BUY_BAND_HI - 1.0:                      # 落在 Buy Zone（≤Trigger+1.5%）
            entry_type = "RETEST"
            if t["long_shadow"]:
                eq, action = 76.0, "WAIT"
                reason = f"在买区内但当日长上影，等次日量价确认不追"
            elif volr < 0.6:
                eq, action = 80.0, "CONDITIONAL BUY"
                reason = f"回踩买区内但量能萎缩({volr:.1f}×)，需承接确认后执行"
            elif volr > 2.2:
                eq, action = 76.0, "WAIT"
                reason = f"回踩放量({volr:.1f}×)换手偏高，防高位巨量，等企稳"
            else:                                           # 量价正常 → PRIMARY(过门槛)
                eq, action = 92.0, "PRIMARY BUY"
                reason = f"突破后回踩触发价上方 {ext:.1%}，量能正常({volr:.1f}×)风险低(DRisk {drisk:.0f})"
                entry_type, eq, action, reason = gate_primary(eq, action, reason, entry_type)
        elif ext <= EXT_HI_COND:                            # Trigger+1.5% ~ +2%
            eq, action, entry_type = 82.0, "CONDITIONAL BUY", "RETEST"
            reason = f"现价略高于 Buy Zone（+{ext:.1%}），次日等回踩 {pressure:.2f} 执行区再考虑"
        elif ext <= EXT_NOCHASE:
            eq, action, entry_type = 74.0, "WAIT", "RETEST"
            reason = f"现价高出触发价 {ext:.1%}，离买点较远，等回踩 {pressure:.2f} 买区"
        else:
            eq, action, entry_type = 66.0, "WAIT", "RETEST"
            reason = f"现价高出触发价 {ext:.1%}（>{EXT_NOCHASE:.0%}），NO CHASE，只等回踩"
        # 高位加速 + 明显超涨强制降级
        if accel >= 90 and ext > EXT_HI_COND:
            eq, action = min(eq, 74.0), "WAIT"
            reason = f"加速分≥90 且已脱离买点(+{ext:.1%}) → OVEREXTENDED，等回踩不追"
        return entry_type, eq, action, reason

    # 2) ABSORPTION —— 吸筹等启动：机会发现，不是买入确认（永远不给 PRIMARY）
    if state == "ABSORPTION":
        if close >= pressure:
            if volr >= VOLR_BREAK_LOW and t["is_yang"] and not t["long_shadow"]:
                eq, action, entry_type = 88.0, "CONDITIONAL BUY", "BREAKOUT"
                reason = f"放量({volr:.1f}×)突破触发价 {pressure:.2f} 收盘站稳，次日回踩 Buy Zone 不破 → 执行"
            elif volr >= 0.9 and not t["long_shadow"]:
                eq, action, entry_type = 84.0, "CONDITIONAL BUY", "BREAKOUT"
                reason = f"已触突破价但量能未放大({volr:.1f}×)，需放量确认后才执行"
            elif t["long_shadow"]:
                eq, action, entry_type = 76.0, "WAIT", "BREAKOUT"
                reason = "今日触突破位但长上影，等明日量价确认不追"
            else:
                eq, action, entry_type = 72.0, "WAIT", "BREAKOUT"
                reason = f"触突破位但量能不配合({volr:.1f}×)，等放量突破信号"
        elif below <= 0.01:
            eq, action, entry_type = 86.0, "CONDITIONAL BUY", "PLATFORM"
            reason = f"吸筹平台贴线（距触发价仅 {below:.1%}），等放量(≥{VOLR_BREAK_LOW:.1f}×)突破执行"
        elif below <= 0.02:
            eq, action, entry_type = 84.0, "CONDITIONAL BUY", "PLATFORM"
            reason = f"平台接近突破（距触发价 {below:.1%}），放量突破即执行"
        elif below <= 0.03 and volr >= VOLR_BREAK_LOW:
            eq, action, entry_type = 84.0, "CONDITIONAL BUY", "PLATFORM"
            reason = f"平台放量迫近触发价（距 {below:.1%} 量比{volr:.1f}×），突破确认执行"
        elif below <= 0.03:
            eq, action, entry_type = 74.0, "WAIT", "PLATFORM"
            reason = f"平台接近触发价（距 {below:.1%}）但未放量，等突破信号"
        elif below <= 0.05:
            eq, action, entry_type = 70.0, "WAIT", "PLATFORM"
            reason = f"仍在平台中段（距触发价 {below:.1%}），等突破信号"
        else:
            eq, action, entry_type = 58.0, "WATCH", "PLATFORM"
            reason = f"距突破位仍远({below:.1%})，跟踪为主"
        return entry_type, eq, action, reason

    # 3) EXTREME_CHURN —— 默认 NO CHASE：需 缩量企稳→重新放量→突破确认，至多条件买，不给 PRIMARY
    if state in NO_CHASE_STATES:
        if close >= pressure and volr >= 1.2 and not t["long_shadow"] and t["is_yang"]:
            eq, action, entry_type = 86.0, "CONDITIONAL BUY", "BREAKOUT"
            reason = f"极端换手后重新放量({volr:.1f}×)突破触发价 {pressure:.2f}，次日回踩可执行"
        elif close >= pressure * 0.985 and 0.5 <= volr <= 1.6 and t["is_yang"] and not t["long_shadow"]:
            eq, action, entry_type = 74.0, "WAIT", "CHURN"
            reason = f"极端换手后缩量企稳转强（量比{volr:.1f}×），等重新放量突破确认，不提前追"
        else:
            eq, action, entry_type = 56.0, "WATCH", "CHURN"
            reason = "EXTREME_CHURN 默认 NO CHASE：等缩量企稳+重新放量+突破确认，现不追"
        return entry_type, eq, action, reason

    # 4) 其余（DRYUP/其他状态）—— 仅接近触发价且量能转好给条件观察
    if below <= 0.02 and volr >= 1.0:
        eq, action, entry_type = 76.0, "CONDITIONAL BUY", "BREAKOUT"
        reason = f"状态 {state}，贴近触发价且量能转好，等突破确认"
    else:
        eq, action, entry_type = 55.0, "WATCH", "OBSERVE"
        reason = f"状态 {state}，未形成明确执行结构，跟踪"
    return entry_type, eq, action, reason


def sub_scores(c, t, eq):
    """Structure / Volume / Risk 分量。"""
    # Structure：吸收质量 + 空间 + 趋势 + 位置
    trend_score = 80.0
    if c["close"] > t["ma60"] and t["ma60_slope"] > 0:
        trend_score = 92.0
    elif c["close"] < t["ma20"]:
        trend_score = 55.0
    struct = clip(0.35 * c["absorption"] + 0.20 * c["space"] + 0.25 * trend_score + 0.20 * (100 - c["drisk"]))
    # Volume：量比性质
    volr = c["volr"]
    if 1.0 <= volr <= 2.5 and t["is_yang"] and not t["long_shadow"]:
        vol_q = 88.0
    elif 0.5 <= volr < 1.0 and t["is_yang"]:
        vol_q = 75.0
    elif volr > 2.5 and t["is_yang"]:
        vol_q = 70.0
    elif volr > 2.5 and not t["is_yang"]:
        vol_q = 40.0           # 放量收阴=派发嫌疑
    else:
        vol_q = 60.0
    risk_q = 100.0 - c["drisk"]
    return struct, vol_q, risk_q


def retest_score(c, t):
    """回踩/买点状态分（0-100）：衡量『现价相对 Trigger 的回踩质量』。
    V3 权重 20%。核心信号：贴近 Buy Zone、未破 Trigger、回踩量能收缩/正常、
    站回短均线(MA10)、无长上影；放量收阴/长上影/巨量换手 = 回踩失败信号 → 扣分。
    """
    close, pressure = c["close"], c["pressure"]
    ext = close / pressure - 1.0
    state, volr = c["state"], c["volr"]
    ma10 = t.get("ma10", close)
    if state in BROKEN_STATES:                       # 已突破回踩：buy zone 内最佳
        if 0.0 <= ext <= BUY_BAND_HI - 1.0:
            s = 95.0
        elif -0.015 <= ext < 0.0:
            s = 82.0
        elif ext <= EXT_HI_COND:
            s = 74.0
        elif ext <= EXT_NOCHASE:
            s = 55.0
        else:
            s = 42.0
        if t["long_shadow"]:
            s -= 10.0
        if volr > 2.2 and close < pressure:
            s -= 12.0                                 # 回踩放量下跌 = 承接失败
        elif 0.6 <= volr <= 1.6 and ext >= 0:
            s += 4.0                                  # 回踩缩量/温和且站上触发价
        if close > ma10:
            s += 4.0                                  # 站回短均线
    elif state == "ABSORPTION":                      # 吸筹未突破：贴近+量能=条件成熟度
        if close >= pressure and volr >= VOLR_BREAK_LOW:
            s = 92.0                                  # 放量突破日，次日回踩即检验
        elif close >= pressure:
            s = 78.0
        else:
            below = pressure / close - 1.0
            s = 86.0 if below <= 0.01 else 80.0 if below <= 0.02 else 72.0 if below <= 0.03 \
                else 60.0 if below <= 0.05 else 50.0
        if t["long_shadow"]:
            s -= 8.0
    elif state in NO_CHASE_STATES:
        s = 62.0 if close >= pressure * 0.985 else 46.0
    else:
        s = 55.0
    return clip(s)


def position_for(action, drisk):
    """仓位：PRIMARY 8-10%、CONDITIONAL 5-8%、PROBE 3%；DRisk 高自动降档。"""
    if action == "PRIMARY BUY":
        return 10.0 if drisk <= 10 else 8.0
    if action == "CONDITIONAL BUY":
        return 6.0 if drisk <= 15 else 5.0
    if action == "PROBE":
        return 3.0
    return 0.0


def market_regime_series(dates, vals):
    """市场环境 regime（G4 门）。dates/vals = CacheReader.market_curve 输出（全市场等权累计）。
    口径（历史回测验证）：
      regime 0（风险）= 值 < MA60×0.97（跌破长均线强约束），或近20个市场日回撤 >5%；
      regime 2（多头）= 市场 MA20>MA60 且值站上 MA20；
      其余（含曲线不足 60 点） = 1（中性，不 gate）。
    返回与 dates 等长的 ndarray(int)；只依赖当日及以前数据，无未来函数。
    """
    s = pd.Series(np.asarray(vals, dtype=float))
    n = len(s)
    r = np.ones(n, dtype=int)
    if n < 61:
        return r
    ma20 = s.rolling(20).mean()
    ma60 = s.rolling(60).mean()
    ret20 = s / s.shift(20) - 1.0
    bull = ((ma20 > ma60) & (s > ma20)).fillna(False).to_numpy()
    risk = ((s < ma60 * 0.97) | (ret20 < -0.05)).fillna(False).to_numpy()
    r[bull] = 2
    r[risk] = 0      # 风险优先于多头（edge case 下宁可错杀）
    return r


def v31_decision(c, t, regime=1, _cls=None):
    """V3.1 统一决策（单一事实源，回测与日更共用）。
    = V3.0 classify 分层 + Execution Score，再施 V3.1 门控（只收紧不放松）：
      G1 状态剔除：EXTREME_CHURN → WATCH（NO CHASE，需先缩量企稳+重新突破确认）
      G2 状态×质量：SECOND_WAVE/DRYUP 无条件放行；BREAKOUT_CONFIRM/RE_EXPANSION/
         ABSORPTION 需 Execution≥P2_EXEC_MIN；其余结构原 BUY → WAIT（不直接执行）
      G3 量比上界：volr>VOLR_MAX → WAIT（放量过热，追涨改等回踩）
      G4 市场环境：regime<REGIME_MIN（regime0）→ WAIT（环境风险，等企稳）
    止损重建（G5）：stop=预警线 max(0.97×Trigger, Trigger-2.2×ATR)；
       stop_struct=结构失效位 max(0.93×Trigger, Trigger-4.0×ATR)，长线退出以此为准。
    返回 dict(entry_type, eq, action, reason, gate, exec, stop, stop_struct, buy_lo, buy_hi)。
    """
    pressure, close, atr = c["pressure"], c["close"], t["atr"]
    if _cls is None:
        _cls = classify(c, t)
    entry_type, eq, action, reason = _cls
    struct, vol_q, risk_q = sub_scores(c, t, eq)
    retest_q = retest_score(c, t)
    exec_score = clip(WEIGHTS["eq"] * eq + WEIGHTS["struct"] * struct + WEIGHTS["retest"] * retest_q
                      + WEIGHTS["vol"] * vol_q + WEIGHTS["risk"] * risk_q + WEIGHTS["life"] * c["life"])
    gate = []
    if action in ("PRIMARY BUY", "CONDITIONAL BUY"):
        st = c["state"]
        if st in NO_CHASE_STATES:
            gate.append(f"G1 {st} 禁止追涨")
            action, eq, entry_type = "WATCH", min(eq, 60.0), "CHURN"
        elif st not in P1_STATES and st not in P2_STATES:
            gate.append(f"G2 {st} 非 P1/P2 结构")
            action, eq, entry_type = "WAIT", min(eq, 78.0), "RETEST"
        elif st in P2_STATES and exec_score < P2_EXEC_MIN:
            gate.append(f"G2 {st} Exec {exec_score:.0f}<{P2_EXEC_MIN:.0f}")
            action, eq, entry_type = "WAIT", min(eq, 78.0), "RETEST"
        if action in ("PRIMARY BUY", "CONDITIONAL BUY") and c["volr"] > VOLR_MAX:
            gate.append(f"G3 volr {c['volr']:.1f}>{VOLR_MAX:.1f} 放量过热")
            action, eq = "WAIT", min(eq, 72.0)
            if entry_type != "RETEST":
                entry_type = "RETEST"
        if action in ("PRIMARY BUY", "CONDITIONAL BUY") and regime < REGIME_MIN:
            gate.append(f"G4 regime {regime} 环境风险")
            action, eq, entry_type = "WAIT", min(eq, 60.0), "OBSERVE"
    stop = max(round(pressure * STOP_K, 2), round(pressure - STOP_ATR * atr, 2))
    stop_struct = max(round(pressure * STOP_STRUCT_K, 2), round(pressure - STOP_STRUCT_ATR * atr, 2))
    return dict(entry_type=entry_type, eq=clip(eq), action=action, reason=reason,
                gate="；".join(gate), exec=exec_score,
                stop=stop, stop_struct=stop_struct,
                buy_lo=round(pressure * BUY_BAND_LO, 2), buy_hi=round(pressure * BUY_BAND_HI, 2),
                struct=struct, vol_q=vol_q, risk_q=risk_q, retest_q=retest_q)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    reader = CacheReader()
    date = args.date or reader.latest_date()
    md_path = os.path.join(OUTPUT_DIR, f"w7_second_wave_{date}.md")
    if not os.path.exists(md_path):
        print(f"[exec] 找不到 W7 报告: {md_path}")
        sys.exit(1)
    cands, refs = parse_md_pool(md_path)
    print(f"[exec] 日期={date} 解析完整候选={len(cands)} 参考({ {k: len(v) for k, v in refs.items()} })", flush=True)

    # 数据：只加载候选代码（含补算所需历史）
    codes = list(cands.keys())
    reader.load_all(date, codes=codes, verbose=False)
    print(f"[exec] 已加载 {len(reader.frames)} 只日线", flush=True)

    # 市场环境 regime（决策日单一时点，作为 G4 门传递给每只标的）
    mdates, mvals = reader.market_curve(date)
    regime = int(market_regime_series(mdates, mvals)[-1]) if len(mvals) else 1
    print(f"[exec] 市场 regime={regime}（2=多头 / 1=中性 / 0=风险，regime0 关闭 BUY）", flush=True)

    rows = []
    for code, c in cands.items():
        t = tech_features(reader, code, date)
        if t is None:
            if args.debug:
                print(f"[exec] {code} 数据不足，跳过", flush=True)
            continue
        c = dict(c, close=t["close"], ma20=t["ma20"])  # 缓存最新价兜底 md 行
        dec = v31_decision(c, t, regime)
        action, entry_type, eq, exec_score = dec["action"], dec["entry_type"], dec["eq"], dec["exec"]
        pos = position_for(action, c["drisk"])
        # V3.1 止损：stop=预警线 / stop_struct=结构失效位（长线持仓退出基准）
        risk_pct = (c["close"] - dec["stop_struct"]) / c["close"] * 100
        upside = clip(1.5 * t["atr"] / c["close"] + (0.02 if c["space"] >= 70 else 0.0) + 0.02, 0.02, 0.10)
        target5 = round(c["close"] * (1 + upside), 2)
        reason = dec["reason"] + (f"（门控：{dec['gate']}）" if dec["gate"] else "")
        rows.append(dict(code=code, name=c["name"], alpha=c["score"], exec=exec_score, eq=eq,
                         state=c["state"], entry=entry_type, trigger=c["pressure"], close=c["close"],
                         buy_lo=dec["buy_lo"], buy_hi=dec["buy_hi"],
                         stop=dec["stop"], stop_struct=dec["stop_struct"],
                         risk=risk_pct, target5=target5, action=action, reason=reason,
                         volr=c["volr"], accel=c["accel"], drisk=c["drisk"], retest=dec["retest_q"],
                         pos=pos, regime=regime, gate=dec["gate"], section=c["section"]))

    # 排序：Action 优先级内按 Execution Score（V3：Alpha 不再参与排序权重）
    order = {"PRIMARY BUY": 0, "CONDITIONAL BUY": 1, "PROBE": 2, "WAIT": 3, "WATCH": 4, "AVOID": 5}
    rows.sort(key=lambda r: (order.get(r["action"], 9), -r["exec"], -r["alpha"]))

    # ---- 报告 ----
    out = args.out or os.path.join(OUTPUT_DIR, f"trade_execution_{date}.md")
    L = []
    L.append(f"# Trade Execution Engine V3.1（{date}）次日可执行交易指令\n")
    L.append(f"输入：W7 候选池 {len(cands)} 只　|　完成结构补算 {len(rows)} 只　|　市场 regime={regime}")
    act_n = {}
    for r in rows:
        act_n[r["action"]] = act_n.get(r["action"], 0) + 1
    L.append("分级：" + "　".join(f"{k}={act_n.get(k, 0)}" for k in
              ["PRIMARY BUY", "CONDITIONAL BUY", "WAIT", "WATCH", "AVOID"]))
    gated_n = sum(1 for r in rows if r["gate"])
    L.append(f"V3.1 门控：{gated_n} 只被降级（G1 极端换手剔除 / G2 状态×质量 / G3 量比过热 / G4 环境风险）"
             "；BUY 仅保留 P1 结构（SECOND_WAVE/DRYUP）或 P2 且 Exec≥85 且 volr≤2.2 的标的\n")

    # TOP EXECUTION（固定 8 列）：可行动层(PRIMARY→CONDITIONAL→WAIT)按 Execution 取前 5
    def _act_rank(a):
        return {"PRIMARY BUY": 0, "CONDITIONAL BUY": 1, "WAIT": 2, "WATCH": 3, "AVOID": 4}.get(a, 9)
    actionable = [r for r in rows if r["action"] in ("PRIMARY BUY", "CONDITIONAL BUY", "WAIT")]
    actionable.sort(key=lambda r: (_act_rank(r["action"]), -r["exec"], -r["alpha"]))
    top = actionable[:5]

    L.append("## TOP EXECUTION（5只）\n")
    L.append("| Rank | 股票 | Alpha | Exec | Action | Trigger | Buy Zone | Stop |")
    L.append("| ---- | --- | ----: | ---: | --------------- | ------: | ----------- | ----: |")
    if top:
        for i, r in enumerate(top, 1):
            L.append(f"| {i} | {r['name']} | {r['alpha']:.1f} | {r['exec']:.0f} | {r['action']} "
                     f"| {r['trigger']:.2f} | {r['buy_lo']:.2f}–{r['buy_hi']:.2f} | {r['stop_struct']:.2f} |")
    else:
        L.append("| - | _今日无通过 Entry/Structure/Risk 三门的可执行标的_ | - | - | NO TRADE | - | - | - |")
    L.append("\n> 排序依据=Execution（买点质量，Alpha 权重 0）；同 Action 内按 Execution 降序。"
             "Exec 由 Entry30%+Structure20%+Retest20%+Volume15%+Risk10%+Lifecycle5% 构成。"
             "Stop 列=结构失效位（长线持仓退出基准，预警线见全候选明细）。\n")

    L.append("## 全候选分层明细\n")
    L.append("| # | 股票 | Alpha | Exec | 现价 | 触发价 | 距触发价 | 量比 | 状态 | Entry | Retest | Stop | 仓位 | Action | 门控 |")
    L.append("| -- | -- | --: | --: | --: | --: | --: | --: | -- | -- | --: | --: | --: | -- | -- |")
    for i, r in enumerate(rows, 1):
        ext = r["close"] / r["trigger"] - 1 if r["trigger"] else 0
        gate_tag = r["gate"][:2] if r["gate"].startswith("G") else "-"
        L.append(f"| {i} | {r['name']}({r['code']}) | {r['alpha']:.1f} | {r['exec']:.0f} | {r['close']:.2f} | {r['trigger']:.2f} "
                 f"| {ext:+.1%} | ×{r['volr']:.1f} | {r['state']} | {r['entry']} | {r['retest']:.0f} "
                 f"| {r['stop_struct']:.2f} | {r['pos']:.0f}% | {r['action']} | {gate_tag} |")

    # 每只 PRIMARY BUY 的完整交易指令（V3 第二十二节格式）
    prim = [r for r in rows if r["action"] == "PRIMARY BUY"]
    L.append("\n## 最终交易指令（PRIMARY BUY）\n")
    if prim:
        for r in prim:
            L.append(f"**【{r['name']} {r['code']}】**\n")
            L.append(f"Action：PRIMARY BUY\n")
            L.append(f"Trigger：{r['trigger']:.2f}")
            L.append(f"Buy Zone：{r['buy_lo']:.2f}–{r['buy_hi']:.2f}")
            L.append(f"Stop：{r['stop_struct']:.2f}（结构失效位；预警线 {r['stop']:.2f}）")
            L.append(f"仓位：{r['pos']:.0f}%\n")
            L.append(f"结构：{r['state']} / {r['entry']}（{r['reason']}）")
            L.append("明日执行：")
            L.append(f"· 回踩 {r['buy_lo']:.2f}–{r['buy_hi']:.2f} → 观察承接；")
            L.append(f"· 回踩不破 {r['trigger']:.2f} → 买入；")
            L.append("· 重新放量转强 → 加强确认；")
            L.append("· 高开2%–5% → 不追，等回踩；")
            L.append(f"· 高开>5% → NO CHASE；")
            L.append(f"· 收盘跌破预警线 {r['stop']:.2f} → 减仓复核；跌破结构位 {r['stop_struct']:.2f} → 交易失效。\n")
    else:
        L.append("_今日没有 PRIMARY BUY。_（宁可输出 0 个，也不把 WAIT 强行变成 BUY）\n")

    # 三个问题（V3 第十九~二十一节）
    L.append("## 三个问题\n")
    L.append("### ① 明天买谁\n")
    if prim:
        for i, r in enumerate(prim[:3], 1):
            L.append(f"{i}. {r['name']}({r['code']})")
        L.append("\n共同特征：突破/回踩结构成立，买点明确（贴近 Trigger±1.5%），量价健康，DRisk可控（≤20）。")
    else:
        L.append("**明天没有 PRIMARY BUY。**")
        conds = [r for r in rows if r["action"] == "CONDITIONAL BUY"][:3]
        if conds:
            L.append("最接近可执行的条件单（需先触发突破/回踩确认）：" +
                     "、".join(f"{r['name']}(BuyZone {r['buy_lo']:.2f}–{r['buy_hi']:.2f})" for r in conds))
        L.append("\n不能为凑数量强行推荐 PRIMARY。")

    L.append("\n### ② 什么价格买\n")
    if prim:
        for r in prim:
            L.append(f"- **{r['name']}**　Trigger：{r['trigger']:.2f}　Buy Zone：{r['buy_lo']:.2f}–{r['buy_hi']:.2f}　"
                     f"Stop：{r['stop_struct']:.2f}（预警 {r['stop']:.2f}）　仓位：{r['pos']:.0f}%")
            L.append(f"  执行：回踩 Buy Zone {r['buy_lo']:.2f}–{r['buy_hi']:.2f} 并出现承接（不破 {r['trigger']:.2f}）后买入。")
    else:
        L.append("无 PRIMARY → 无固定买价；条件单以对应 Buy Zone 为触发参考（见全候选明细）。")

    L.append("\n### ③ 什么情况不买\n")
    L.append("1. 高开 >5% → NO CHASE\n"
             "2. 高开 2%–5% → 等回踩，不追\n"
             "3. 跌破 Trigger → 不买\n"
             "4. 放量滞涨（volr>2.2）→ 不追，等回踩\n"
             "5. 长上影冲高回落 → 不买\n"
             "6. 回踩放量下跌 → 不买\n"
             "7. EXTREME_CHURN 未完成回踩 → 不追（G1）\n"
             "8. 非 P1/P2 结构或 P2 执行质量不足 Exec<85 → 不直接执行（G2）\n"
             "9. 市场 regime0（大盘回撤>5% 或破长均线）→ 不开新仓（G4）\n"
             "10. 收盘跌破结构失效位 → 交易失效\n")

    L.append("---\n*风险提示：本引擎输出为盘后执行预案，非投资建议。全部价格基于 {d} 收盘数据；次日需以实际开盘走势复核。*".format(d=date))
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"[exec] 已输出: {out}")
    if args.debug:
        for r in rows[:25]:
            print(f"[exec] {r['action']:<15} {r['name']:<6} {r['code']} A={r['alpha']:.1f} E={r['exec']:.0f} "
                  f"eq={r['eq']:.0f} rt={r['retest']:.0f} pos={r['pos']:.0f} close={r['close']:.2f} trg={r['trigger']:.2f} "
                  f"volr={r['volr']:.1f} state={r['state']} | {r['reason']}")


if __name__ == "__main__":
    main()
