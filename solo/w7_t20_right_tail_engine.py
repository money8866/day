"""W7 T20 Right-Tail Execution Engine V2.0
目标：在候选池中优先识别未来 T+20 最可能出现高涨幅右尾的股票。
链路：HVT/异常放量 -> 充分调整 -> 缩量/卖压衰竭 -> 平台整理 -> Re-expansion -> Breakout -> Healthy Retest -> 再放量 -> T+20 右尾。
"""
import argparse
import json
import os
import re
import time

import numpy as np
import pandas as pd

import w7_second_wave_engine as w7
from w7_second_wave_engine import (
    OUTPUT_DIR,
    CacheReader,
    MarketCtx,
    clip,
    finite,
    pct_position,
    percentile_rank,
    safe_mean,
)

# 选股记录与跟踪库(幂等落库, 供 update_tracking 回填 T+N 表现)
try:
    from stock_pick_db import DB_PATH as PICK_DB_PATH, record_picks
except Exception:
    PICK_DB_PATH = None
    record_picks = None

from sli import reader as sli_reader
from sli.classify import TYPE_PRIORITY_V2

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_daily")
STATE_PATH = os.path.join(OUTPUT_DIR, "w7_t20_state.json")
IGE_OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ige", "output")

ST_HVT = "HVT"
ST_ABSORPTION = "ABSORPTION"
ST_FULL_PULLBACK = "FULL_PULLBACK"
ST_DRYUP = "DRYUP"
ST_PLATFORM = "PLATFORM"
ST_SECOND_WAVE = "SECOND_WAVE"
ST_RE_EXPANSION = "RE_EXPANSION"
ST_BREAKOUT = "BREAKOUT"
ST_RETEST = "RETEST"
ST_RETEST_SUCCESS = "RETEST_SUCCESS"
ST_T20_RIGHT_TAIL = "T20_RIGHT_TAIL"
ST_DISTRIBUTION = "DISTRIBUTION"
ST_FAIL = "FAIL"
STATES = [ST_HVT, ST_ABSORPTION, ST_FULL_PULLBACK, ST_DRYUP, ST_PLATFORM, ST_SECOND_WAVE,
          ST_RE_EXPANSION, ST_BREAKOUT, ST_RETEST, ST_RETEST_SUCCESS, ST_T20_RIGHT_TAIL,
          ST_DISTRIBUTION, ST_FAIL]

W_T20 = {"hvt": 0.20, "pullback": 0.15, "platform": 0.15, "reexp": 0.15, "breakout": 0.10,
         "retest": 0.10, "fund": 0.05, "sector": 0.05, "market": 0.05}
W_PRIORITY = {"t20": 0.45, "structure": 0.20, "quality": 0.15, "fund": 0.10, "rs": 0.10}

GATE_T20 = 75.0
GATE_STRUCTURE = 75.0
GATE_RR = 2.0
GATE_T20_HVT_RB = 80.0
GATE_HVT_RB_STRICT = 85.0

SLI_LEADER_FILTER = True
SLI_LEADER_TYPES = set(TYPE_PRIORITY_V2)

RETEST_ZONE_LOW_ATR = 1.0
RETEST_ZONE_HIGH_ATR = 0.3
INVALID_ATR = 1.2
TARGET_ATR = 2.5
HVT_LOOKBACK = 120
BREAKOUT_TRACE_DAYS = 15

ACT_BUY = "BUY"
ACT_WAIT_RETEST = "WAIT_RETEST"
ACT_WAIT_BREAKOUT = "WAIT_BREAKOUT"
ACT_WAIT_CONFIRM = "WAIT_CONFIRM"
ACT_NO_CHASE = "NO_CHASE"
ACT_EXIT = "EXIT"
ACT_NO_TRADE = "NO_TRADE"

LAYER_PRIMARY = "PRIMARY_BUY"
LAYER_NEXT = "CONFIRMED_NEXT"
LAYER_WATCH = "WATCH_T20"
LAYER_EXTENDED = "EXTENDED_NO_CHASE"
LAYER_R1 = "R1"
LAYER_EXIT = "EXIT"
LAYER_NO_TRADE = "NO_TRADE"


def find_hvt_events(df, i, lookback=HVT_LOOKBACK, max_events=3):
    vols = df.vol.values
    events = []
    j_min = max(121, i - lookback)
    for j in range(i, j_min - 1, -1):
        vol = vols[j]
        if not vol or vol <= 0:
            continue
        m20 = float(np.mean(vols[max(0, j - 20):j])) if j >= 20 else 0.0
        if m20 <= 0 or vol < 1.8 * m20:
            continue
        ok, _ = w7.extreme_event(df, j)
        if not ok:
            continue
        events.append(event_record(df, j))
        if len(events) >= max_events:
            break
    return events


def event_record(df, j):
    row = df.iloc[j]
    vol = finite(row.vol)
    m20 = float(np.mean(df.vol.values[max(0, j - 20):j])) if j >= 20 else 0.0
    tr = finite(row.turnover_rate_f, finite(row.turnover_rate))
    hist_tr = pd.to_numeric(df.turnover_rate_f.iloc[:j], errors="coerce").fillna(0).max()
    vol_win = df.vol.values[max(0, j - 120):j]
    tr_win = pd.to_numeric(df.turnover_rate_f.iloc[max(0, j - 120):j], errors="coerce").fillna(0).values
    vol_pct = percentile_rank(vol_win, vol) if len(vol_win) else 0.0
    tr_pct = percentile_rank(tr_win, tr) if len(tr_win) else 0.0
    return {
        "idx": int(j),
        "date": str(row.trade_date),
        "close": finite(row.close),
        "turnover": tr,
        "amount_yi": finite(row.amount) / 100000.0,
        "vol_ratio20": vol / m20 if m20 > 0 else 0.0,
        "close_pos": pct_position(finite(row.open), finite(row.high), finite(row.low), finite(row.close)),
        "pct_chg": finite(row.pct_chg),
        "tr_vs_hist_high": tr / hist_tr if hist_tr and hist_tr > 0 else 0.0,
        "event_pct": min(vol_pct, tr_pct) * 100.0,
    }


def detect_platform(df, i, max_width=0.13):
    """平台检测基于截至 i-1 的窗口；返回 PlatformHigh/Low/Days/起点 与窗口，或 None"""
    if i < 15:
        return None
    for n in (10, 14, 18, 24, 30):
        if i - n < 5:
            continue
        seg = df.iloc[i - n:i]
        hi = finite(seg.high.max())
        lo = finite(seg.low.min())
        mid = (hi + lo) / 2.0
        if mid <= 0:
            return None
        width = (hi - lo) / mid
        big = int((seg.pct_chg.abs() > 7.0).sum())
        if width <= max_width and big == 0:
            atr = finite(df.iloc[i - 1].atr_bfq, mid * 0.03)
            dry = float(np.mean(df.vol.values[i - 5:i])) if i >= 5 else 0.0
            base_vol = float(np.mean(df.vol.values[max(0, i - n - 30):i - n])) if i - n >= 30 else float(np.mean(df.vol.values[max(0, i - n):i]))
            return {
                "high": hi, "low": lo, "mid": mid, "days": n,
                "atr": atr,
                "dryup_ratio": dry / base_vol if base_vol > 0 else 1.0,
                "start_date": str(df.iloc[i - n].trade_date),
            }
    return None


def hvt_chain(df, i, ev_idx):
    e = ev_idx
    post = df.iloc[e:i + 1]
    win = min(10, len(post))
    ref_high = finite(post.high.iloc[:win].max())
    low_pos = int(post.low.values.argmin())
    low = finite(post.low.iloc[low_pos])
    atr_at_low = finite(df.iloc[e + low_pos].atr_bfq, ref_high * 0.03)
    dd = ref_high / low - 1.0 if low > 0 else 0.0
    dd_atr = (ref_high - low) / atr_at_low if atr_at_low > 0 else 0.0
    pre_mid = float(np.mean(df.close.values[max(0, e - 20):e])) if e >= 20 else low
    broke_platform = low < pre_mid
    days_since_low = i - (e + low_pos)
    vols = post.vol.values
    n5 = min(5, len(vols))
    dry_ratio = float(np.mean(vols[-n5:])) / float(np.mean(vols)) if len(vols) and float(np.mean(vols)) > 0 else 1.0
    amp = (post.high.values - post.low.values) / post.close.values
    amp_now = float(np.mean(amp[-n5:])) if len(amp) else 0.0
    amp_prev = float(np.mean(amp)) if len(amp) else 0.0
    return {
        "event_idx": e, "ref_high": ref_high, "low": low, "low_pos": e + low_pos,
        "dd": dd, "dd_atr": dd_atr, "days_since_low": days_since_low,
        "pre_mid": pre_mid, "broke_platform": broke_platform,
        "dry_ratio": dry_ratio, "amp_now": amp_now, "amp_prev": amp_prev,
    }


def pullback_quality(chain):
    if not chain:
        return 0.0
    s = 40.0
    dd, dd_atr = chain["dd"], chain["dd_atr"]
    if 0.15 <= dd <= 0.35:
        s += 30.0
    elif 0.10 <= dd < 0.15 or 0.35 < dd <= 0.45:
        s += 18.0
    elif dd < 0.10 or dd > 0.55:
        s -= 15.0
    if 2.0 <= dd_atr <= 6.0:
        s += 10.0
    if 3 <= chain["days_since_low"] <= 40:
        s += 10.0
    if not chain["broke_platform"]:
        s += 10.0
    elif chain["dry_ratio"] <= 0.45:
        s += 4.0
    return clip(s)


def dryup_quality(df, i, chain):
    if chain is None:
        return 0.0
    s = 40.0
    if chain["dry_ratio"] <= 0.40:
        s += 30.0
    elif chain["dry_ratio"] <= 0.55:
        s += 20.0
    elif chain["dry_ratio"] <= 0.70:
        s += 8.0
    if chain["amp_prev"] > 0 and chain["amp_now"] <= 0.75 * chain["amp_prev"]:
        s += 15.0
    closes = df.close.values
    n5 = min(5, len(closes))
    c_now = closes[-n5:]
    if c_now.mean() > 0 and float(np.std(c_now)) / float(np.mean(c_now)) <= 0.02:
        s += 15.0
    return clip(s)


def platform_quality(plat, df, i):
    if not plat:
        return 0.0
    s = 45.0
    width = (plat["high"] - plat["low"]) / plat["mid"] if plat["mid"] > 0 else 1.0
    if width <= 0.08:
        s += 25.0
    elif width <= 0.11:
        s += 18.0
    elif width <= 0.13:
        s += 10.0
    if plat["days"] >= 14:
        s += 15.0
    elif plat["days"] >= 10:
        s += 8.0
    if plat["dryup_ratio"] <= 0.55:
        s += 15.0
    elif plat["dryup_ratio"] <= 0.75:
        s += 8.0
    atr = plat["atr"]
    if atr > 0 and plat["mid"] > 0 and (plat["high"] - plat["low"]) / atr <= 5.0:
        s += 5.0
    return clip(s)


def reexpansion_quality(df, i, plat):
    if not plat:
        return 0.0
    vol_now = float(np.mean(df.vol.values[max(0, i - 2):i + 1]))
    dry = vol_now / (float(np.mean(df.vol.values[i - 8:i - 3])) if i >= 8 else vol_now) if vol_now > 0 else 0.0
    s = 35.0
    if 1.3 <= dry <= 2.5:
        s += 35.0
    elif 1.15 <= dry < 1.3 or 2.5 < dry <= 3.5:
        s += 20.0
    elif dry > 3.5:
        s -= 15.0
    close = finite(df.iloc[i].close)
    atr = plat["atr"] if plat["atr"] > 0 else close * 0.03
    gap = (plat["high"] - close) / atr if atr > 0 else 0.0
    if -0.2 <= gap <= 1.0:
        s += 30.0
    elif 1.0 < gap <= 2.0:
        s += 12.0
    elif gap > 2.0:
        s -= 20.0
    return clip(s)


def breakout_quality(df, i, plat, breakout_price, rs20, sector_strength):
    if not plat or breakout_price <= 0:
        return 0.0, {}
    row = df.iloc[i]
    close = finite(row.close)
    atr = finite(row.atr_bfq, close * 0.03)
    v20 = float(np.mean(df.vol.values[max(0, i - 19):i + 1]))
    vol_ratio = finite(row.vol) / v20 if v20 > 0 else 0.0
    cp = pct_position(finite(row.open), finite(row.high), finite(row.low), close)
    amp_pct = (close - breakout_price) / atr if atr > 0 else 0.0
    ma20 = finite(row.ma_bfq_20, close)
    ma20_gap = (close - ma20) / atr if atr > 0 else 0.0
    ind = sector_strength if sector_strength is not None else 50.0
    conseq = int(finite(row.updays, 0))
    s = 0.0
    s += 22.0 * clip(vol_ratio / 2.2 * 100.0) / 100.0
    s += 18.0 * clip(cp) / 100.0
    if 0.2 <= amp_pct <= 1.5:
        s += 14.0
    elif amp_pct < 0.2 or amp_pct <= 2.5:
        s += 7.0
    atr20 = float(np.mean(df.atr_bfq.values[max(0, i - 19):i + 1]))
    atr_exp = atr / atr20 if atr20 > 0 else 1.0
    if 1.0 <= atr_exp <= 1.4:
        s += 8.0
    elif atr_exp < 1.0:
        s += 4.0
    s += 12.0 * clip(rs20) / 100.0
    s += 10.0 * clip(ind) / 100.0
    if -1.0 <= ma20_gap <= 5.0:
        s += 10.0
    elif ma20_gap <= 8.0:
        s += 5.0
    if 1 <= conseq <= 3:
        s += 6.0
    detail = {"vol_ratio": vol_ratio, "close_pos": cp, "amp_atr": amp_pct, "atr_exp": atr_exp,
              "rs20": rs20, "ma20_gap": ma20_gap, "conseq": conseq}
    return clip(s), detail


def retest_quality(df, i, breakout_price, breakout_idx, atr, rs20, sector_strength):
    if breakout_price <= 0 or breakout_idx < 0 or i <= breakout_idx:
        return None
    post = df.iloc[breakout_idx + 1:i + 1]
    if post.empty:
        return None
    low_pos = int(post.low.values.argmin())
    low = finite(post.low.iloc[low_pos])
    depth_atr = (breakout_price - low) / atr if atr > 0 else 0.0
    days = len(post)
    v_bo = finite(df.iloc[breakout_idx].vol, 1.0)
    v_ret = float(np.mean(post.vol.values))
    vol_shrink = v_ret / v_bo if v_bo > 0 else 1.0
    last = df.iloc[i]
    cp = pct_position(finite(last.open), finite(last.high), finite(last.low), finite(last.close))
    ind = sector_strength if sector_strength is not None else 50.0
    s = 0.0
    if 0.2 <= depth_atr <= 1.0:
        s += 30.0
    elif 0.0 <= depth_atr < 0.2:
        s += 22.0
    elif depth_atr <= 1.5:
        s += 12.0
    else:
        s += 0.0
    if 1 <= days <= 5:
        s += 20.0
    elif days <= 8:
        s += 12.0
    elif days <= 12:
        s += 5.0
    if vol_shrink <= 0.5:
        s += 20.0
    elif vol_shrink <= 0.75:
        s += 12.0
    elif vol_shrink <= 1.0:
        s += 4.0
    else:
        s -= 6.0
    s += 15.0 * clip(cp) / 100.0
    s += 15.0 * clip(0.5 * rs20 + 0.5 * ind) / 100.0
    ok_zone = low >= breakout_price - RETEST_ZONE_LOW_ATR * atr
    recovered = finite(last.close) >= breakout_price
    re_exp = finite(last.vol) > v_ret * 1.15 and finite(last.close) > finite(last.open)
    grade = "A" if ok_zone and vol_shrink <= 0.6 and cp >= 50 and recovered and re_exp and ind >= 55 else ("B" if ok_zone and vol_shrink <= 0.8 else "C")
    detail = {"depth_atr": depth_atr, "days": days, "vol_shrink": vol_shrink,
              "close_pos": cp, "in_zone": ok_zone, "recovered": recovered, "re_expansion": re_exp, "grade": grade}
    return clip(s), detail


def extension_check(df, i, breakout_price, plat):
    row = df.iloc[i]
    close = finite(row.close)
    atr = finite(row.atr_bfq, close * 0.03)
    pct = finite(row.pct_chg)
    flags, score, hard = [], 0.0, ""
    ref = breakout_price if breakout_price > 0 else (plat["high"] if plat else 0.0)
    if ref > 0 and close > ref + 2.0 * atr:
        flags.append("价格超过突破位+2ATR")
        score += 60.0
        hard = "extended"
    v20 = float(np.mean(df.vol.values[max(0, i - 19):i + 1]))
    vol_ratio = finite(row.vol) / v20 if v20 > 0 else 0.0
    cp = pct_position(finite(row.open), finite(row.high), finite(row.low), close)
    if pct > 8.0 and vol_ratio > 2.0 and cp < 75:
        flags.append("单日涨幅>8%且无二次结构")
        score += 55.0
        hard = "no_trade"
    p1, p2 = finite(df.iloc[i - 1].pct_chg), finite(df.iloc[i - 2].pct_chg) if i >= 2 else 0.0
    if pct > 3.0 and p1 > 3.0 and pct >= p1 and p1 >= p2:
        flags.append("连续两日加速上行")
        score += 40.0
        if hard != "no_trade":
            hard = "extended"
    upper_shadow = finite(row.high) - max(finite(row.open), close)
    body = abs(close - finite(row.open))
    if vol_ratio > 2.5 and cp < 40 and upper_shadow > 2.0 * max(body, atr * 0.3):
        flags.append("巨量长上影")
        score += 45.0
    if i >= 1:
        v_prev = finite(df.iloc[i - 1].vol)
        r_prev = v_prev / v20 if v20 > 0 else 0.0
        if vol_ratio > 3.0 and r_prev > 3.0:
            flags.append("连续高潮量")
            score += 35.0
    if not flags:
        hard = ""
    elif hard == "":
        hard = "soft"
    return clip(score, 0.0, 100.0), flags, hard


def structure_score(df, i):
    row = df.iloc[i]
    close = finite(row.close)
    ma5 = finite(row.ma_bfq_5, close)
    ma10 = finite(row.ma_bfq_10, close)
    ma20 = finite(row.ma_bfq_20, close)
    ma60 = finite(row.ma_bfq_60, close)
    atr = finite(row.atr_bfq, close * 0.03)
    s = 40.0
    if ma5 > ma10 > ma20 > ma60:
        s += 25.0
    elif ma20 > ma60:
        s += 12.0
    gap = (close - ma20) / atr if atr > 0 else 0.0
    if -0.5 <= gap <= 2.0:
        s += 20.0
    elif gap <= 3.0:
        s += 8.0
    elif gap > 5.0:
        s -= 15.0
    if finite(row.rsi_bfq_6, 50.0) < 75:
        s += 5.0
    if finite(row.updays, 0) >= 2 and finite(row.updays, 0) <= 6:
        s += 10.0
    return clip(s)


def fund_engine(reader, code, as_of):
    now, prev = reader.fina(code, as_of)
    if now is None:
        return 50.0, False, ["财务数据缺失，中性处理"]
    score = 50.0
    flags = []
    or_yoy = finite(now.or_yoy, 0.0)
    np_yoy = finite(now.netprofit_yoy, 0.0)
    gp = finite(now.grossprofit_margin, None)
    gp_prev = finite(prev.grossprofit_margin, None) if prev is not None else None
    ocf = finite(now.ocf_to_or, None)
    roe = finite(now.roe, None)
    if or_yoy >= 20.0:
        score += 15.0
        flags.append(f"营收增速{or_yoy:.1f}%")
    elif or_yoy >= 5.0:
        score += 8.0
    elif or_yoy < 0.0:
        score -= 10.0
    if np_yoy >= 30.0:
        score += 15.0
        flags.append(f"净利增速{np_yoy:.1f}%")
    elif np_yoy >= 10.0:
        score += 8.0
    elif np_yoy < 0.0:
        score -= 10.0
    global_margin = False
    if gp is not None and gp_prev is not None and gp > gp_prev:
        score += 8.0
        flags.append(f"毛利率{gp:.1f}%改善")
        if gp > gp_prev + 0.3 and or_yoy >= 20.0:
            global_margin = True
            flags.append("GLOBAL_MARGIN_EXPANSION≈(毛利率改善+收入高增)")
    if ocf is not None and ocf >= 0.8:
        score += 6.0
    if roe is not None and roe >= 10.0:
        score += 6.0
    return clip(score, 0.0, 100.0), global_margin, flags


def market_regime(mkt, date):
    p = int(np.searchsorted(mkt.dates, date))
    if p >= len(mkt.vals) or len(mkt.vals) < 130:
        return "NEUTRAL", "strict"
    v = mkt.vals[:p + 1]
    cur = v[-1]
    ret20 = cur / v[-21] - 1.0 if len(v) > 21 else 0.0
    ret60 = cur / v[-61] - 1.0 if len(v) > 61 else 0.0
    ma60 = float(np.mean(v[-60:]))
    lo120 = float(np.min(v[-120:]))
    hi120 = float(np.max(v[-120:]))
    if ret60 > 0.04 and cur > ma60:
        return "BULL", "normal"
    if ret20 > 0.02 and cur > ma60 and (cur - lo120) / lo120 > 0.06:
        return "RECOVERY", "normal"
    if ret20 < -0.04 or (hi120 > 0 and cur / hi120 - 1.0 < -0.12):
        return "BEAR", "harsh"
    if ret20 < -0.02 or ret60 < -0.05:
        return "WEAK", "harsh"
    return "NEUTRAL", "strict"


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {"run_date": "", "codes": {}}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1)


def update_streaks(results, date):
    state = load_state()
    prev_date = str(state.get("run_date", ""))
    codes = state.get("codes", {})
    out = {}
    for r in results:
        code = r["code"]
        old = codes.get(code, {})
        if prev_date == date:
            prev_streak = int(old.get("prev_streak", 0))
        else:
            prev_streak = int(old.get("streak", 0))
        new_key = r.get("event_key", "")
        old_key = str(old.get("event_key", ""))
        reset = bool(new_key) and bool(old_key) and new_key != old_key
        if r["action"] in (ACT_BUY, ACT_WAIT_RETEST, ACT_WAIT_BREAKOUT, ACT_WAIT_CONFIRM):
            streak = 0
        elif reset:
            streak = 0
        else:
            streak = prev_streak + 1 if r["layer"] not in (LAYER_PRIMARY, LAYER_NEXT) else 0
        r1 = True if streak >= 3 else False
        if reset:
            r1 = False
        out[code] = {"prev_streak": prev_streak, "streak": streak, "r1": r1,
                     "event_key": new_key or old_key, "event_date": r.get("event_date", "")}
        r["streak"] = streak
        if r1 and r["layer"] not in (LAYER_EXIT,):
            r["layer"] = LAYER_R1
            r["action"] = ACT_NO_TRADE
    state = {"run_date": date, "codes": out}
    save_state(state)
    return state


def load_sli_leader_map(asof=""):
    """一次性加载 SLI_V2 快照，构建 ts_code -> 龙头信息映射（快照自动回退；失败返回空表，由 sli_fields fail-closed）。"""
    try:
        panel = sli_reader.get_panel(asof or None)
    except Exception as exc:
        print(f"[t20] SLI_V2 快照加载失败，按非龙头 fail-closed 处理: {exc}", flush=True)
        return {}, {}
    if panel is None or panel.empty:
        print("[t20] SLI_V2 快照为空，按非龙头 fail-closed 处理", flush=True)
        return {}, {}
    meta = dict(panel.attrs.get("_sli_meta", {}) or {})
    cols = [c for c in ("ts_code", "name", "subsector", "l3_name", "sli_v2", "sub_rank",
                        "leader_type_v2", "all_types_v2", "lifecycle") if c in panel.columns]
    p = panel[cols].copy()
    p["ts_code"] = p["ts_code"].astype(str)
    if "leader_type_v2" in p.columns:
        p["leader_type_v2"] = p["leader_type_v2"].fillna("NONE").astype(str)
    info = {r["ts_code"]: r.to_dict() for _, r in p.iterrows()}
    n_leader = sum(1 for v in info.values() if v.get("leader_type_v2") in SLI_LEADER_TYPES)
    print(f"[t20] SLI_V2 快照={meta.get('snapshot_date', '?')} 覆盖={len(info)} 龙头={n_leader}", flush=True)
    return info, meta


def load_ige_adj(asof=""):
    """加载行业增长弹性 IGE_ADJ 快照（ige/output/ige_full_{date}.csv，股票级）。
    优先选与 asof 同日期的文件，无则回退最近快照。返回 (info, snap)；
    info[code] = {ige_adj, ige_mix, sw_l1, sw_l3}，缺失股票不入表（主流程注入 None 排尾）。"""
    if not os.path.isdir(IGE_OUT_DIR):
        print("[t20] IGE 输出目录不存在，跳过 IGE_ADJ 接入", flush=True)
        return {}, ""
    try:
        files = sorted(f for f in os.listdir(IGE_OUT_DIR)
                       if re.fullmatch(r"ige_full_\d{8}\.csv", f))
    except OSError as exc:
        print(f"[t20] IGE 目录读取失败: {exc}", flush=True)
        return {}, ""
    if not files:
        print("[t20] 无 ige_full_*.csv 快照，跳过 IGE_ADJ 接入", flush=True)
        return {}, ""
    target = f"ige_full_{asof}.csv" if asof else ""
    chosen = target if target in files else files[-1]
    snap = chosen[len("ige_full_"):-len(".csv")]
    if target and chosen != target:
        print(f"[t20] IGE 无 {asof} 同日快照，回退最近快照 {snap}", flush=True)
    try:
        df = pd.read_csv(os.path.join(IGE_OUT_DIR, chosen),
                         encoding="utf-8-sig", dtype={"code": str})
    except Exception as exc:
        print(f"[t20] IGE 快照 {chosen} 读取失败: {exc}", flush=True)
        return {}, ""
    need = [c for c in ("code", "ige_adj", "ige_mix", "sw_l1", "sw_l3") if c in df.columns]
    if "code" not in need or "ige_adj" not in need:
        print(f"[t20] IGE 快照 {chosen} 缺必需列，跳过 IGE_ADJ 接入", flush=True)
        return {}, ""
    info = {}
    for rec in df[need].to_dict("records"):
        code = str(rec.get("code") or "").strip()
        if not code:
            continue
        info[code] = {
            "ige_adj": finite(rec.get("ige_adj"), None),
            "ige_mix": finite(rec.get("ige_mix"), None),
            "sw_l1": "" if pd.isna(rec.get("sw_l1")) else str(rec["sw_l1"]),
            "sw_l3": "" if pd.isna(rec.get("sw_l3")) else str(rec["sw_l3"]),
        }
    print(f"[t20] IGE_ADJ 快照={snap} 覆盖={len(info)}", flush=True)
    return info, snap


def sli_fields(code, sli_info):
    """单股 SLI_V2 龙头字段：无快照或 leader_type_v2=NONE 视为非龙头（硬过滤 fail-closed）。"""
    si = (sli_info or {}).get(code)
    if not si:
        return {"sli_v2": None, "sli_rank": None, "sli_leader_type": "NO_SNAPSHOT",
                "sli_subsector": "", "sli_leader": not SLI_LEADER_FILTER, "sli_block": ""}
    lt = str(si.get("leader_type_v2") or "NONE")
    v2_v = finite(si.get("sli_v2"), None)
    rank_v = finite(si.get("sub_rank"), None)
    return {
        "sli_v2": round(v2_v, 1) if v2_v is not None else None,
        "sli_rank": int(rank_v) if rank_v is not None else None,
        "sli_leader_type": lt,
        "sli_subsector": str(si.get("subsector") or si.get("l3_name") or ""),
        "sli_leader": (not SLI_LEADER_FILTER) or lt in SLI_LEADER_TYPES,
        "sli_block": "",
    }


def sli_tag(r):
    """报告用单股 SLI 标签，如 ABSOLUTE_LEADER#1 93.1。"""
    if not r.get("sli_leader_type") or r["sli_leader_type"] == "NO_SNAPSHOT":
        return "-"
    rk = f"#{int(r['sli_rank'])}" if r.get("sli_rank") else ""
    sv = f"{r['sli_v2']:.1f}" if r.get("sli_v2") is not None else "-"
    return f"{r['sli_leader_type']}{rk} {sv}"


def _derive_tech(df):
    """本地派生宽表指标列（窄表源没有，W7 迁移替代）。

    与 w7 引擎的 ma_bfq_* 同口径：基于每股前复权收盘(=close×adj/每股末因子)，
    组内按 trade_date 升序，满窗计算。
    - ma_bfq_5/30/250：qfq close 滚动均线（补齐 engine 未算的窗口）
    - atr_bfq：qfq 价格的 ATR(14, Wilder)，用于幅度归一
    - rsi_bfq_6：qfq close 的 RSI(6, Wilder)
    - macd_bfq：qfq close 的 MACD 柱 2×(DIF−DEA)，12/26/9
    - updays：连续上涨交易日数（pct_chg>0 计，否则清零）
    幂等：列已存在则跳过。
    """
    need = [c for c in ("ma_bfq_5", "ma_bfq_30", "ma_bfq_250", "atr_bfq",
                        "rsi_bfq_6", "macd_bfq", "updays") if c not in df.columns]
    if not need:
        return df
    df = df.copy()
    price = w7._qfq_price(df)  # 与 engine ma_bfq_* 同源的前复权收盘
    for w, col in ((5, "ma_bfq_5"), (30, "ma_bfq_30"), (250, "ma_bfq_250")):
        if col in need:
            df[col] = price.rolling(w, min_periods=w).mean()
    if "atr_bfq" in need:
        cl = price
        pc = cl.shift(1)
        factor = price / df["close"].astype(float).replace(0, np.nan)
        hq = df["high"].astype(float) * factor
        lq = df["low"].astype(float) * factor
        tr = pd.concat([(hq - lq).abs(), (hq - pc).abs(), (lq - pc).abs()], axis=1).max(axis=1)
        df["atr_bfq"] = tr.ewm(alpha=1.0 / 14.0, adjust=False, min_periods=14).mean()
    if "rsi_bfq_6" in need:
        delta = price.diff()
        up = delta.clip(lower=0.0)
        dn = (-delta).clip(lower=0.0)
        au = up.ewm(alpha=1.0 / 6.0, adjust=False, min_periods=6).mean()
        ad = dn.ewm(alpha=1.0 / 6.0, adjust=False, min_periods=6).mean()
        rs = au / ad.replace(0, np.nan)
        rsi = 100.0 - 100.0 / (1.0 + rs)
        df["rsi_bfq_6"] = rsi.where(ad > 0, 100.0).where(delta.notna(), np.nan)
    if "macd_bfq" in need:
        e12 = price.ewm(span=12, adjust=False).mean()
        e26 = price.ewm(span=26, adjust=False).mean()
        dif = e12 - e26
        dea = dif.ewm(span=9, adjust=False).mean()
        df["macd_bfq"] = 2.0 * (dif - dea)
    if "updays" in need:
        up = (pd.to_numeric(df["pct_chg"], errors="coerce") > 0).astype(int)
        grp = up.ne(up.shift()).cumsum()
        cnt = up.groupby(grp).cumsum()
        df["updays"] = cnt.where(up == 1, 0)
    return df


def analyze_t20(code, name, industry, df, reader, mkt, sector_strength, sector_growth, regime, gate_level, date, sli_info=None):
    if df.empty or len(df) < 250:
        return None
    i = len(df) - 1
    row = df.iloc[i]
    close = finite(row.close)
    if close <= 0:
        return None
    atr = finite(row.atr_bfq, close * 0.03)
    if atr <= 0:
        return None
    events = find_hvt_events(df, i)
    ev = events[0] if events else None
    ev_idx = ev["idx"] if ev else -1
    chain = hvt_chain(df, i, ev_idx) if ev_idx > 0 else None
    plat = detect_platform(df, i)
    rs20_base = mkt.ret(str(df.iloc[max(0, i - 20)].trade_date), date) if mkt else 0.0
    ret20 = close / finite(df.iloc[i - 20].close, close) - 1.0 if i >= 20 else 0.0
    rs20 = (ret20 - rs20_base) if rs20_base is not None else 0.0
    ind_str = sector_strength.get(industry, 50.0)
    ind_growth = sector_growth.get(industry, 50.0)

    breakout_price = 0.0
    breakout_idx = -1
    breakout_date = ""
    if ev_idx > 0:
        post = df.iloc[ev_idx:i + 1]
        ref_high = chain["ref_high"]
        for k in range(len(post) - 1, -1, -1):
            c = finite(post.close.values[k])
            if c <= ref_high:
                continue
            prev_hi = finite(post.high.values[k - 1]) if k >= 1 else 0.0
            prev_c = finite(post.close.values[k - 1]) if k >= 1 else 0.0
            if c >= prev_hi or (prev_c > 0 and prev_c <= ref_high):
                breakout_idx = ev_idx + k
                break
    if breakout_idx < 0 and plat:
        if close > plat["high"]:
            breakout_price = plat["high"]
            breakout_idx = i
        else:
            pre = df.iloc[i - BREAKOUT_TRACE_DAYS:i + 1]
            for k in range(len(pre) - 2, -1, -1):
                c = finite(pre.iloc[k].close)
                pp = detect_platform(df, i - (len(pre) - 1 - k) - 1)
                if pp and c > pp["high"]:
                    breakout_price = pp["high"]
                    breakout_idx = i - (len(pre) - 1 - k)
                    break
    if breakout_idx >= 0 and breakout_price <= 0:
        bplat = detect_platform(df, breakout_idx) or plat
        if bplat:
            breakout_price = bplat["high"]
        else:
            breakout_price = finite(df.iloc[breakout_idx].close, close)
    b_quality, b_detail = breakout_quality(df, breakout_idx if breakout_idx >= 0 else i, plat, breakout_price, clip(50 + rs20 * 400), ind_str) if breakout_price > 0 else (0.0, {})
    rq = retest_quality(df, i, breakout_price, breakout_idx, atr, clip(50 + rs20 * 400), ind_str) if breakout_price > 0 else None
    r_quality = rq[0] if rq else None
    r_detail = rq[1] if rq else {}

    ext_score, ext_flags, ext_hard = extension_check(df, i, breakout_price, plat)

    zone_low = breakout_price - RETEST_ZONE_LOW_ATR * atr if breakout_price > 0 else (plat["high"] - RETEST_ZONE_LOW_ATR * atr if plat else close - atr)
    zone_high = breakout_price + RETEST_ZONE_HIGH_ATR * atr if breakout_price > 0 else (plat["high"] + RETEST_ZONE_HIGH_ATR * atr if plat else close)
    invalid = max(plat["low"], breakout_price - INVALID_ATR * atr) if (plat and breakout_price > 0) else (breakout_price - INVALID_ATR * atr if breakout_price > 0 else finite(row.ma_bfq_20, close) - 1.5 * atr)
    target = breakout_price + TARGET_ATR * atr if breakout_price > 0 else (plat["high"] + TARGET_ATR * atr if plat else close + 2.0 * atr)
    entry = breakout_price if breakout_price > 0 else (plat["high"] if plat else close)
    rr = (target - entry) / max(entry - invalid, 0.02 * entry) if entry > invalid else 0.0

    # 空间评分输入：上方最近压力 = 近60/120/250日前高中首个高于现价者（不含当日）
    near_res = 0.0
    res_win = 0
    for win in (60, 120, 250):
        if i >= win:
            hw = float(df["high"].iloc[i - win:i].replace({np.nan: 0.0}).max())
            if hw > close * 1.002:
                near_res, res_win = hw, win
                break
    if near_res <= 0:
        near_res = target  # 250日内无前高压制（创新高）→ 以引擎目标为第一参考压力
        res_win = 0

    v20 = float(np.mean(df.vol.values[max(0, i - 19):i + 1]))
    vol_ratio = finite(row.vol) / v20 if v20 > 0 else 0.0
    fail = False
    dist = False
    fail_flags = []
    if plat and v20 > 0 and vol_ratio > 1.8 and close < plat["low"]:
        fail = True
        fail_flags.append("放量破坏平台")
    if plat and close < plat["low"] and vol_ratio > 1.2 and not fail_flags:
        fail = True
        fail_flags.append("失守平台低点")
    ma20 = finite(row.ma_bfq_20, close)
    if close < ma20 - 1.5 * atr and finite(row.ma_bfq_60, close) < finite(df.iloc[i - 10].ma_bfq_60, close):
        fail = True
        fail_flags.append("MA20/MA60趋势破坏")
    if breakout_price > 0 and breakout_idx >= 0 and i > breakout_idx:
        post_low = finite(df.iloc[breakout_idx + 1:i + 1].low.min()) if i > breakout_idx + 1 else close
        if post_low < breakout_price - INVALID_ATR * atr and vol_ratio > 1.5:
            fail = True
            fail_flags.append("跌破突破失效位（放量）")
    cp = pct_position(finite(row.open), finite(row.high), finite(row.low), close)
    if vol_ratio > 2.5 and cp < 35 and finite(row.pct_chg) > 3.0 and close < finite(row.ma_bfq_10, close):
        dist = True
        fail_flags.append("派发（高潮放量+收盘弱）")

    fund_score, global_margin, fund_flags = fund_engine(reader, code, date)

    hvt_q = 0.0
    if ev:
        s = 30.0 + ev["event_pct"] * 0.3
        s += clip(ev["vol_ratio20"] / 4.0 * 30.0)
        if ev["close_pos"] >= 60:
            s += 10.0
        if ev["tr_vs_hist_high"] >= 0.8:
            s += 10.0
        age = i - ev["idx"]
        if age <= 30:
            s += 10.0
        elif age <= 60:
            s += 4.0
        else:
            s -= 10.0
        hvt_q = clip(s)
    p_q = pullback_quality(chain) if chain else 0.0
    pl_q = platform_quality(plat, df, i)
    re_q = reexpansion_quality(df, i, plat)
    bq_for_score = b_quality if breakout_price > 0 else (pl_q * 0.6 if plat else 0.0)
    rq_for_score = r_quality if r_quality is not None else 40.0
    mkt_q = {"BULL": 90.0, "RECOVERY": 80.0, "NEUTRAL": 55.0, "WEAK": 30.0, "BEAR": 15.0}.get(regime, 50.0)
    t20_score = 100.0 * (
        W_T20["hvt"] * hvt_q / 100.0
        + W_T20["pullback"] * (p_q if chain else pl_q * 0.5) / 100.0
        + W_T20["platform"] * pl_q / 100.0
        + W_T20["reexp"] * re_q / 100.0
        + W_T20["breakout"] * bq_for_score / 100.0
        + W_T20["retest"] * rq_for_score / 100.0
        + W_T20["fund"] * fund_score / 100.0
        + W_T20["sector"] * clip(0.6 * ind_str + 0.4 * clip(50 + rs20 * 400)) / 100.0
        + W_T20["market"] * mkt_q / 100.0
    )
    t20_score = clip(t20_score)
    st_score = structure_score(df, i)
    priority = 100.0 * (
        W_PRIORITY["t20"] * t20_score / 100.0
        + W_PRIORITY["structure"] * st_score / 100.0
        + W_PRIORITY["quality"] * (max(b_quality, r_quality if r_quality is not None else 0.0) if (b_quality or r_quality) else pl_q * 0.4) / 100.0
        + W_PRIORITY["fund"] * fund_score / 100.0
        + W_PRIORITY["rs"] * clip(0.5 * ind_str + 0.5 * clip(50 + rs20 * 400)) / 100.0
    )
    priority = clip(priority)
    if ext_hard == "soft":
        priority = clip(priority - ext_score * 0.5)
    chain_steps = 0
    if ev:
        chain_steps = 1
        if chain and chain["dd"] >= 0.10:
            chain_steps += 1
        if chain and chain["dry_ratio"] <= 0.55:
            chain_steps += 1
        if plat and ev_idx <= i - plat["days"]:
            chain_steps += 1
        if re_q >= 60:
            chain_steps += 1
        if breakout_price > 0:
            chain_steps += 1
        if rq and rq[1].get("recovered") and rq[1].get("in_zone"):
            chain_steps += 1

    trend_up = finite(row.ma_bfq_60, close) > finite(df.iloc[i - 10].ma_bfq_60, close) if i >= 10 else False
    ret60 = close / finite(df.iloc[i - 60].close, close) - 1.0 if i >= 60 else 0.0
    turn_up = finite(row.pct_chg) > 0.0 or cp >= 55
    pullback_ok = trend_up and ret60 > 0.10 and abs(close - ma20) <= 1.0 * atr and vol_ratio < 0.95 and turn_up
    retest_ok = rq is not None and r_detail.get("in_zone")
    breakout_ok = breakout_price > 0 and b_quality >= 70 and ext_score < 40
    hvt_rb_ok = ev is not None and chain_steps >= 6

    buy_type = ""
    if hvt_rb_ok and (breakout_ok or retest_ok) and t20_score >= GATE_T20_HVT_RB:
        buy_type = "HVT_RB_BUY"
    elif breakout_ok and t20_score >= GATE_T20 and st_score >= GATE_STRUCTURE:
        buy_type = "BREAKOUT_BUY"
    elif pullback_ok and t20_score >= GATE_T20 and st_score >= GATE_STRUCTURE:
        buy_type = "PULLBACK_BUY"

    hard_block = ext_score >= 55 or ext_flags or fail or dist
    gated = bool(buy_type) and t20_score >= GATE_T20 and st_score >= GATE_STRUCTURE and rr >= GATE_RR and not hard_block
    if gate_level == "harsh":
        if buy_type == "HVT_RB_BUY" and t20_score >= GATE_T20_HVT_RB:
            pass
        elif buy_type == "PULLBACK_BUY" and t20_score >= GATE_HVT_RB_STRICT and st_score >= GATE_HVT_RB_STRICT:
            pass
        else:
            gated = False

    if fail:
        lifecycle = ST_FAIL
    elif dist:
        lifecycle = ST_DISTRIBUTION
    elif breakout_price > 0 and breakout_idx == i and close > breakout_price:
        lifecycle = ST_BREAKOUT
    elif breakout_price > 0 and i > breakout_idx:
        if rq and r_detail.get("recovered") and r_detail.get("in_zone") and r_quality >= 70 and b_quality >= 65:
            lifecycle = ST_T20_RIGHT_TAIL
        elif rq and r_detail.get("recovered"):
            lifecycle = ST_RETEST_SUCCESS
        elif close <= zone_high and close >= zone_low:
            lifecycle = ST_RETEST
        elif close < zone_low:
            lifecycle = ST_FAIL if fail else ST_RETEST
        else:
            lifecycle = ST_RETEST_SUCCESS if close > breakout_price else ST_RETEST
    elif plat and re_q >= 60 and re_q > pl_q:
        lifecycle = ST_RE_EXPANSION
    elif plat:
        lifecycle = ST_PLATFORM
    elif chain and chain["dd"] >= 0.10 and chain["dry_ratio"] > 0.55:
        lifecycle = ST_FULL_PULLBACK
    elif chain and chain["dd"] >= 0.10 and chain["dry_ratio"] <= 0.55:
        lifecycle = ST_DRYUP
    elif ev and (i - ev["idx"]) <= 3:
        lifecycle = ST_HVT
    elif ev and (i - ev["idx"]) <= 12 and chain["dd"] < 0.08:
        lifecycle = ST_ABSORPTION
    elif len(events) >= 2:
        lifecycle = ST_SECOND_WAVE
    else:
        lifecycle = ST_PLATFORM if plat else ST_DRYUP

    sli = sli_fields(code, sli_info)

    missing = []
    if not buy_type:
        if not ev and not plat:
            missing.append("等待结构形成（平台或HVT事件）")
        elif t20_score < GATE_T20:
            missing.append(f"T20右尾分{t20_score:.0f}<75")
        elif st_score < GATE_STRUCTURE:
            missing.append(f"结构分{st_score:.0f}<75")
        elif plat and breakout_price <= 0:
            missing.append(f"等待放量突破{plat['high']:.2f}")
        elif rq is None and breakout_price > 0:
            missing.append("等待回踩确认")
        elif rr < GATE_RR:
            missing.append(f"风险收益比{rr:.1f}<2.0")
        else:
            missing.append("等待质量分确认")
    elif not gated:
        if hard_block:
            missing.append("EXTENSION硬门控触发" if ext_flags else "结构失效风险")
        elif t20_score < GATE_T20:
            missing.append(f"T20右尾分{t20_score:.0f}<75")
        elif rr < GATE_RR:
            missing.append(f"风险收益比{rr:.1f}<2.0")

    if gated and buy_type and not sli["sli_leader"]:
        missing.append(f"非SLI_V2细分龙头（硬过滤：leader_type_v2={sli['sli_leader_type']}，赛道第{sli['sli_rank'] if sli['sli_rank'] else '-'}）")

    if fail or dist:
        layer, action = LAYER_EXIT, ACT_EXIT
    elif ext_hard == "no_trade":
        layer, action = LAYER_NO_TRADE, ACT_NO_TRADE
    elif ext_hard == "extended":
        layer, action = LAYER_EXTENDED, ACT_NO_CHASE
    elif gated and buy_type and sli["sli_leader"]:
        layer, action = LAYER_PRIMARY, ACT_BUY
    elif buy_type and not gated and len(missing) == 1:
        layer, action = LAYER_NEXT, ACT_WAIT_CONFIRM
    elif buy_type:
        layer, action = LAYER_NEXT, ACT_WAIT_CONFIRM
    elif plat and breakout_price <= 0 and t20_score >= 60:
        layer, action = LAYER_WATCH, ACT_WAIT_BREAKOUT
    elif breakout_price > 0 and not retest_ok and t20_score >= 60:
        layer, action = LAYER_WATCH, ACT_WAIT_RETEST
    else:
        layer, action = LAYER_NO_TRADE, ACT_NO_TRADE

    if gated and buy_type == "HVT_RB_BUY" and lifecycle == ST_BREAKOUT and sli["sli_leader"]:
        action = ACT_BUY
    elif gated and buy_type and lifecycle == ST_RETEST:
        action = ACT_WAIT_RETEST
        layer = LAYER_NEXT

    top_pick_core = (
        bool(gated) and buy_type == "HVT_RB_BUY"
        and lifecycle in (ST_RETEST_SUCCESS, ST_T20_RIGHT_TAIL)
        and r_quality is not None and r_quality >= 60.0
        and st_score >= 85.0
        and not ext_flags and ext_score <= 0.0
        and rr >= 2.08
        and global_margin
    )
    top_pick = bool(top_pick_core and sli["sli_leader"])
    if top_pick_core and not sli["sli_leader"]:
        sli["sli_block"] = (
            f"六分量达标但非SLI_V2细分龙头：leader_type_v2={sli['sli_leader_type']}"
            f"，赛道第{sli['sli_rank'] if sli['sli_rank'] else '-'}"
            f"，SLI_V2={sli['sli_v2'] if sli['sli_v2'] is not None else '无快照'}")
    event_key = f"{code}_{ev['date']}" if ev else ""
    return {
        "code": code, "name": name, "industry": industry,
        "lifecycle": lifecycle, "buy_type": buy_type,
        "t20_score": round(t20_score, 1), "structure": round(st_score, 1),
        "retest_quality": round(r_quality, 1) if r_quality is not None else None,
        "breakout_quality": round(b_quality, 1),
        "ext_score": round(ext_score, 1), "ext_flags": ext_flags, "ext_hard": ext_hard, "fail_flags": fail_flags,
        "rr": round(rr, 2),
        "breakout_price": round(breakout_price, 2), "zone_low": round(zone_low, 2),
        "zone_high": round(zone_high, 2), "invalid": round(invalid, 2),
        "ma20": round(ma20, 2), "atr": round(atr, 2),
        "close": close, "pct_chg": finite(row.pct_chg),
        "target": round(target, 2), "near_res": round(near_res, 2), "res_win": res_win,
        "platform": {"high": round(plat["high"], 2), "low": round(plat["low"], 2),
                     "days": plat["days"], "start": plat["start_date"],
                     "dryup": round(plat["dryup_ratio"], 2)} if plat else None,
        "event": {k: (round(v, 2) if isinstance(v, float) else v) for k, v in ev.items() if k != "idx"} if ev else None,
        "chain_steps": chain_steps,
        "fund_score": round(fund_score, 1), "fund_flags": fund_flags,
        "global_margin": global_margin,
        "sector_strength": round(ind_str, 1), "sector_growth": round(ind_growth, 1),
        "rs20": round(rs20 * 100, 1),
        "priority": round(priority, 1),
        "top_pick": top_pick,
        **sli,
        "layer": layer, "action": action, "missing": missing,
        "event_key": event_key, "event_date": ev["date"] if ev else "",
        "streak": 0,
    }


def fmt_price(v):
    return f"{v:.2f}" if isinstance(v, (int, float)) else "-"


def markdown(results, date, regime, gate_level, universe_n, sli_meta=None):
    results_sorted = sorted(results, key=lambda x: x["priority"], reverse=True)
    primary = [r for r in results_sorted if r["layer"] == LAYER_PRIMARY]
    nxt = [r for r in results_sorted if r["layer"] == LAYER_NEXT]
    watch = [r for r in results_sorted if r["layer"] == LAYER_WATCH]
    extended = [r for r in results_sorted if r["layer"] == LAYER_EXTENDED]
    r1 = [r for r in results_sorted if r["layer"] == LAYER_R1]
    exits = [r for r in results_sorted if r["layer"] == LAYER_EXIT]
    no_trades = [r for r in results_sorted if r.get("ext_hard") == "no_trade"]
    ige_snap = next((str(r.get("ige_snap") or "") for r in results if r.get("ige_snap")), "")

    def _ige_adj(r):
        return r.get("ige_adj") if isinstance(r.get("ige_adj"), (int, float)) else -1.0

    def _ige_tag(r):
        return f"{r['ige_adj']:.1f}" if isinstance(r.get("ige_adj"), (int, float)) else "-"

    def _space_score(r):
        """走势空间优选分 0-100：33% 上方弹性 + 33% 下方容错 + 34% 右侧完成度。"""
        close = r["close"]
        atr = r.get("atr") or 0.0
        res = r.get("near_res") or 0.0
        if res > close:
            up_pct = (res / close - 1.0) * 100.0
        else:
            up_pct = (r.get("target", close * 1.06) / close - 1.0) * 100.0
        s_up = clip(up_pct / 12.0 * 100.0)
        d_atr = (close - r["zone_low"]) / atr if atr > 0 else 3.0
        s_down = clip(100.0 - d_atr * 22.0)
        base = {"T20_RIGHT_TAIL": 90.0, "RETEST_SUCCESS": 80.0, "RE_EXPANSION": 72.0,
                "BREAKOUT": 68.0, "RETEST": 55.0, "HVT": 45.0, "ABSORPTION": 45.0,
                "PLATFORM": 40.0, "DRYUP": 40.0, "FULL_PULLBACK": 40.0}.get(r["lifecycle"], 50.0)
        s_stage = clip(base + (int(r.get("chain_steps", 0) or 0) - 5) * 4.0)
        return round(0.33 * s_up + 0.33 * s_down + 0.34 * s_stage, 1)

    # 空间优选分优先：可操作候选（PRIMARY_BUY / TOP_PICK / CONFIRMED_NEXT / 决策树）按
    # (SPACE空间优选分, IGE_ADJ, 原 T20 综合分) 降序；顶部【T20 RIGHT-TAIL TOP】排名表保留原 T20 综合分排序不动。
    cand_sorted = sorted(results, key=lambda r: (_space_score(r), _ige_adj(r), r["priority"]), reverse=True)
    cand_primary = [r for r in cand_sorted if r["layer"] == LAYER_PRIMARY]
    cand_nxt = [r for r in cand_sorted if r["layer"] == LAYER_NEXT]
    cand_watch = [r for r in cand_sorted if r["layer"] == LAYER_WATCH]
    lines = []
    lines.append(f"# W7 T20 Right-Tail 引擎报告 {date}")
    lines.append("")
    lines.append(f"> 目标：识别未来 T+20 最可能出现高涨幅右尾的股票（非 T+1 胜率）。市场环境：**{regime}**（门控级别：{gate_level}），股池 {universe_n} 只，候选 {len(results)} 只。")
    lines.append("")
    if gate_level == "harsh":
        lines.append("> 弱市门控生效：仅接受 HVT_RB_BUY（T20≥80）或极强 PULLBACK_BUY（T20≥85 且结构≥85），不因候选减少降低标准。")
        lines.append("")
    if ige_snap:
        lines.append(f"> 候选排序 = 走势空间优选分 SPACE（上方弹性33%：距最近前高压力幅度 / 下方容错33%：现价距理想买点ATR / 右侧完成度34%：Lifecycle+链路）→ IGE_ADJ → T20综合分，降序；顶部 T20 RIGHT-TAIL TOP 表保留原 T20 综合分序并附 IGE_ADJ 标注（行业增长弹性，快照 {ige_snap}）。")
        lines.append("")
    lines.append("## 【T20 RIGHT-TAIL TOP】")
    lines.append("")
    lines.append("| 排名 | 代码 | 名称 | BUY_TYPE | Lifecycle | IGE_ADJ | T20右尾分 | 结构分 | Retest | Extension | 操作 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    top = (primary + nxt + watch + extended)[:20]
    for n, r in enumerate(top, 1):
        rq = f"{r['retest_quality']:.0f}" if r["retest_quality"] is not None else "-"
        lines.append(f"| {n} | {r['code']} | {r['name']} | {r['buy_type'] or '-'} | {r['lifecycle']} | {_ige_tag(r)} | {r['t20_score']:.0f} | {r['structure']:.0f} | {rq} | {r['ext_score']:.0f} | **{r['action']}** |")
    lines.append("")
    picks = [r for r in cand_sorted if r.get("top_pick")]  # 与 PRIMARY_BUY 一致，按 SPACE 空间优选分优先
    blocked = [r for r in cand_sorted if r.get("sli_block")]
    snap = str((sli_meta or {}).get("snapshot_date", "?"))
    lines.append("## 【TOP_PICK】")
    lines.append("")
    if picks:
        lines.append("> 最优组合信号（七项全中）：HVT_RB_BUY × Lifecycle=RETEST_SUCCESS/T20_RIGHT_TAIL × Retest≥60 × 结构≥85 × Extension=0（无任何扩张痕迹） × RR≥2.08（平台低点抬高） × GLOBAL_MARGIN_EXPANSION × SLI_V2细分龙头。")
        lines.append(f"> SLI_V2 龙头硬过滤（强关联 sli.classify.TYPE_PRIORITY_V2）：leader_type_v2 须为 {'/'.join(sorted(SLI_LEADER_TYPES))} 之一；快照={snap}，非龙头或无快照一律剔除（fail-closed）。")
        lines.append("")
        lines.append("| # | 代码 | 名称 | IGE_ADJ | T20 | 结构 | Retest | RR | 现价 | 突破价 | 回踩区 | 失效位 | 目标位 | SLI龙头 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for n, r in enumerate(picks, 1):
            rq = f"{r['retest_quality']:.0f}" if r["retest_quality"] is not None else "-"
            tgt = r["breakout_price"] + 2.5 * r["atr"] if r["breakout_price"] > 0 else r["close"] + 2.0 * r["atr"]
            lines.append(f"| {n} | {r['code']} | {r['name']} | {_ige_tag(r)} | {r['t20_score']:.0f} | {r['structure']:.0f} | {rq} | {r['rr']:.2f} | {fmt_price(r['close'])} | {fmt_price(r['breakout_price'])} | [{fmt_price(r['zone_low'])}, {fmt_price(r['zone_high'])}] | {fmt_price(r['invalid'])} | {tgt:.2f} | {sli_tag(r)} |")
    elif blocked:
        lines.append("无（六分量达标候选均被 SLI_V2 龙头硬过滤剔除，宁缺毋滥）")
    else:
        lines.append("无（七项条件未同时满足，宁缺毋滥）")
    if blocked:
        det = "；".join(
            f"{r['code']} {r['name']}(leader_type={r['sli_leader_type']}"
            f"，赛道第{r['sli_rank'] if r['sli_rank'] else '-'}"
            f"，SLI_V2={r['sli_v2'] if r['sli_v2'] is not None else '无快照'})"
            for r in blocked)
        lines.append("")
        lines.append(f"> ⛔ SLI_V2 龙头硬过滤剔除 {len(blocked)} 只六分量达标候选：{det}")
    lines.append("")
    lines.append("## 【PRIMARY_BUY】")
    lines.append("")
    lines.append("> 入口硬过滤：仅 SLI_V2 细分龙头可进入 PRIMARY_BUY（leader_type_v2 须为六类龙头之一；非龙头或无快照一律 fail-closed 降级 CONFIRMED_NEXT，缺口标明 SLI 身份）。")
    lines.append("")
    if cand_primary:
        for r in cand_primary:
            ev = r["event"]
            ev_desc = f"HVT {ev['date']} 换手{ev['turnover']:.1f}% 量比20D {ev['vol_ratio20']:.1f}x" if ev else "无HVT事件"
            gm = "；GLOBAL_MARGIN_EXPANSION≈成立" if r["global_margin"] else ""
            lines.append(f"### {r['code']} {r['name']}（{r['industry']}）")
            lines.append(f"- 行业弹性：IGE_ADJ **{_ige_tag(r)}**（申万 {r.get('ige_sw_l1') or '-'}·{r.get('ige_sw_l3') or '-'}）")
            lines.append(f"- BUY_TYPE：{r['buy_type']}｜Lifecycle：{r['lifecycle']}｜链路完成 {r['chain_steps']}/7{'｜⭐TOP_PICK（七分量全中）' if r.get('top_pick') else ''}")
            lines.append(f"- T20右尾分 {r['t20_score']}｜结构分 {r['structure']}｜突破质量 {r['breakout_quality']}｜Retest {r['retest_quality'] if r['retest_quality'] is not None else '-'}｜Extension {r['ext_score']}｜RR {r['rr']}")
            lines.append(f"- 关键位：突破价 {fmt_price(r['breakout_price'])}｜回踩区 [{fmt_price(r['zone_low'])}, {fmt_price(r['zone_high'])}]｜失效位 {fmt_price(r['invalid'])}｜MA20 {fmt_price(r['ma20'])}｜ATR {fmt_price(r['atr'])}")
            lines.append(f"- 催化：{ev_desc}；{'；'.join(r['fund_flags'])}{gm}")
            lines.append(f"- **明日动作：{r['action']}**（条件：回踩区缩量企稳或不破失效位放量确认）")
            lines.append("")
    else:
        lines.append("无满足全部硬门控（T20≥75 且结构≥75 且 RR≥2.0 且无 EXTENSION/失效否决 且 SLI_V2细分龙头）的标的。")
        lines.append("")
    lines.append("## 【CONFIRMED_NEXT】")
    lines.append("")
    if cand_nxt:
        for r in cand_nxt:
            lines.append(f"- {r['code']} {r['name']}｜{r['buy_type'] or '-'}｜{r['lifecycle']}｜IGE_ADJ {_ige_tag(r)}｜T20 {r['t20_score']:.0f}/结构 {r['structure']:.0f}｜缺口：{r['missing'][0] if r['missing'] else '等待确认'}｜关键位 突破 {fmt_price(r['breakout_price'])}/回踩区 [{fmt_price(r['zone_low'])},{fmt_price(r['zone_high'])}]")
    else:
        lines.append("无（所有候选要么已满足 PRIMARY_BUY，要么缺口不唯一）")
    lines.append("")
    lines.append("## 【EXTENDED / NO CHASE】")
    lines.append("")
    if extended or no_trades:
        for r in extended:
            reason = "；".join(r["ext_flags"]) if r["ext_flags"] else f"扩张分 {r['ext_score']:.0f} 过高"
            lines.append(f"- {r['code']} {r['name']}｜T20 {r['t20_score']:.0f}｜不能追的原因：{reason}（结构/基本面不差但位置过高，等待回踩重新给机会）")
        for r in no_trades:
            reason = "；".join(r["ext_flags"]) if r["ext_flags"] else f"扩张分 {r['ext_score']:.0f} 过高"
            lines.append(f"- {r['code']} {r['name']}｜T20 {r['t20_score']:.0f}｜NO TRADE：{reason}（单日>8%且无二次结构，禁入不等待）")
    else:
        lines.append("无")
    lines.append("")
    lines.append("## 【R1 / EXIT】")
    lines.append("")
    if r1:
        for r in r1:
            lines.append(f"- R1：{r['code']} {r['name']}｜连续 {r['streak']} 日未确认买点，从执行池剔除（若出现新 HVT/新平台事件将自动重置复活）")
    if exits:
        for r in exits:
            reason = "；".join(r.get("fail_flags") or []) or ("结构失效" if r["lifecycle"] == ST_FAIL else "派发迹象（高潮放量+长上影+跌破短期结构）")
            lines.append(f"- EXIT：{r['code']} {r['name']}｜{reason}")
    if not r1 and not exits:
        lines.append("无")
    lines.append("")
    lines.append("## 【明日开盘决策树】")
    lines.append("")
    tree_pool = (cand_primary + cand_nxt + [w for w in cand_watch if w["action"] in (ACT_WAIT_BREAKOUT, ACT_WAIT_RETEST)])[:20]
    if tree_pool:
        for r in tree_pool:
            if r["action"] == ACT_BUY:
                cond = f"开盘价≥{fmt_price(r['zone_high'])} 且不破失效位 {fmt_price(r['invalid'])} 放量确认 → 执行"
            elif r["action"] == ACT_WAIT_RETEST:
                cond = f"回踩至 [{fmt_price(r['zone_low'])}, {fmt_price(r['zone_high'])}] 且缩量企稳 → 确认后执行；跌破 {fmt_price(r['invalid'])} 放量 → 放弃"
            elif r["action"] == ACT_WAIT_BREAKOUT:
                cond = f"放量站上 {fmt_price(r['platform']['high']) if r['platform'] else fmt_price(r['breakout_price'])} → 触发评估"
            elif r["action"] == ACT_WAIT_CONFIRM:
                if not r.get("sli_leader", True):
                    cond = "SLI_V2 龙头身份缺失（硬过滤），禁止升级 PRIMARY_BUY，仅跟踪观察"
                else:
                    cond = f"满足缺口条件（{r['missing'][0] if r['missing'] else '确认'}）→ 升级 PRIMARY_BUY"
            else:
                cond = r["missing"][0] if r["missing"] else "无触发条件"
            lines.append(f"- {r['code']} {r['name']}：**{r['action']}** @ {cond}")
    else:
        lines.append("- 今日无满足质量条件的候选：**NO TRADE**（不为产生交易降低门槛）")
    lines.append("")
    state_counts = {}
    for r in results:
        state_counts[r["lifecycle"]] = state_counts.get(r["lifecycle"], 0) + 1
    dist_line = " ｜ ".join(f"{k}×{v}" for k, v in sorted(state_counts.items(), key=lambda kv: -kv[1]))
    lines.append(f"## 状态分布")
    lines.append("")
    lines.append(f"{dist_line if dist_line else '无候选'}")
    lines.append("")
    lines.append("---")
    lines.append(f"*生成时间 {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}｜无满足最高质量条件时明确输出 NO TRADE；宁错过，不降门槛。*")
    return "\n".join(lines)


def _track_picks(results):
    """当日可买信号(action=BUY, 即 PRIMARY_BUY 层)转成 stock_pick_db 落库记录。

    V5.1 收口：仅落当日买点，CONFIRMED_NEXT 的 WAIT_CONFIRM/WAIT_RETEST/WAIT_BREAKOUT
    属等待观察态，不再进跟踪表与飞书，避免与「W7 二波/突破当日买点」的可买口径混淆。
    标准列直接映射: ts_code/stock_name/close/pct_chg/signal/action/score/rank_no/
    industry/stop_price(失效位)/target_price(引擎目标价);
    其余字段(lifecycle/buy_type/结构分/回踩区/MA20/IGE…) 自动进 indicators JSON 列。
    """
    rows = [r for r in results if r.get("action") == ACT_BUY]
    rows.sort(key=lambda r: -float(r.get("priority") or 0))
    out = []
    for idx, r in enumerate(rows, 1):
        out.append({
            "ts_code": r["code"], "stock_name": r["name"], "industry": r.get("industry"),
            "close": r.get("close"), "pct_chg": r.get("pct_chg"),
            "signal": r.get("layer"), "action": r.get("action"),
            "score": r.get("t20_score"), "rank_no": idx,
            "stop_price": r.get("invalid"), "target_price": r.get("target"),
            "lifecycle": r.get("lifecycle"), "buy_type": r.get("buy_type"),
            "structure": r.get("structure"), "retest_quality": r.get("retest_quality"),
            "breakout_quality": r.get("breakout_quality"), "rr": r.get("rr"),
            "zone_low": r.get("zone_low"), "zone_high": r.get("zone_high"),
            "breakout_price": r.get("breakout_price"), "ma20": r.get("ma20"),
            "atr": r.get("atr"), "near_res": r.get("near_res"),
            "chain_steps": r.get("chain_steps"), "priority": r.get("priority"),
            "top_pick": bool(r.get("top_pick")), "event_date": r.get("event_date"),
            "ige_adj": r.get("ige_adj"), "ige_mix": r.get("ige_mix"),
            "sli_leader": bool(r.get("sli_leader")),
        })
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output", default="")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    reader = CacheReader()
    date = args.date or reader.latest_date()
    universe = reader.universe(date)
    load_codes = list(universe["ts_code"].tolist()) if not universe.empty else []
    print(f"[t20] 日期={date} 股池={len(load_codes)} 开始加载历史...", flush=True)
    reader.load_all(date, codes=load_codes, verbose=args.verbose)
    # W7 窄表链路：宽表技术列(atr/rsi/macd/updays/ma5/30/250) 本地派生补全
    for _c, _f in reader.frames.items():
        reader.frames[_c] = _derive_tech(_f)
    print(f"[t20] 本地派生技术列完成 frames={len(reader.frames)}", flush=True)
    mdates, mvals = reader.market_curve(date)
    mkt = MarketCtx(mdates, mvals)
    nfina = reader.load_fina()
    industry_map = {}
    if not universe.empty:
        for _, r in universe.iterrows():
            industry_map[str(r.get("ts_code", ""))] = str(r.get("industry") or "")
    by_ind, fin_ind = {}, {}
    for code, f in reader.frames.items():
        if len(f) < 21:
            continue
        c0, c1 = finite(f.iloc[-21].close, 0.0), finite(f.iloc[-1].close, 0.0)
        ind = industry_map.get(code, "")
        if c0 <= 0 or not ind or ind == "nan":
            continue
        by_ind.setdefault(ind, []).append(c1 / c0 - 1.0)
        g = reader.fina_frames.get(code)
        if g is not None and len(g):
            np_g = finite(g.iloc[-1].netprofit_yoy, None)
            if np_g is not None:
                fin_ind.setdefault(ind, []).append(np_g)
    sector_strength = {ind: clip(50 + float(np.median(v)) * 150) for ind, v in by_ind.items() if len(v) >= 3}
    sector_growth = {ind: clip(50 + float(np.median(v)) * 1.1) for ind, v in fin_ind.items() if len(v) >= 3}
    regime, gate_level = market_regime(mkt, date)
    print(f"[t20] 财务覆盖={nfina} 行业={len(sector_strength)} 市场环境={regime}({gate_level})", flush=True)
    results = []
    rows = universe.to_dict("records")
    if args.limit:
        rows = rows[:args.limit]
    sli_info, sli_meta = load_sli_leader_map(date)
    ige_info, ige_snap = load_ige_adj(date)
    t_start = time.time()
    for n, row in enumerate(rows):
        if n and n % 500 == 0:
            print(f"[t20] 分析进度 {n}/{len(rows)} 耗时={time.time()-t_start:.1f}s", flush=True)
        code = str(row.get("ts_code", ""))
        if "ST" in str(row.get("name", "")).upper() or "退" in str(row.get("name", "")):
            continue
        name = str(row.get("name") or code)
        basic = reader.basic.loc[code] if code in reader.basic.index else {}
        list_date = str(basic.get("list_date", "")) if hasattr(basic, "get") else ""
        if list_date and list_date.isdigit() and int(list_date) > int(date) - 365:
            continue
        df = reader.bars(code, date)
        industry = str(row.get("industry") or (basic.get("industry", "") if hasattr(basic, "get") else ""))
        try:
            r = analyze_t20(code, name, industry, df, reader, mkt, sector_strength, sector_growth, regime, gate_level, date, sli_info)
        except Exception as exc:
            if args.verbose:
                print(f"[t20] {code} 分析失败: {exc}", flush=True)
            continue
        if r:
            ig = ige_info.get(code)
            r["ige_adj"] = ig["ige_adj"] if ig else None
            r["ige_mix"] = ig["ige_mix"] if ig else None
            r["ige_sw_l1"] = ig["sw_l1"] if ig else ""
            r["ige_sw_l3"] = ig["sw_l3"] if ig else ""
            r["ige_snap"] = ige_snap
            results.append(r)
    update_streaks(results, date)
    text = markdown(results, date, regime, gate_level, len(rows), sli_meta)
    output = args.output or os.path.join(OUTPUT_DIR, f"w7_t20_right_tail_{date}.md")
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as fh:
        fh.write(text)
    # 接入 stock_pick_db 跟踪(幂等, 失败不阻塞报告): 每日 PRIMARY_BUY/CONFIRMED_NEXT 落库,
    # 之后由 python stock_pick_db.py tracking 按失效位/目标价回填 T+N 表现与胜率
    if record_picks is not None:
        try:
            if PICK_DB_PATH:
                os.makedirs(os.path.dirname(PICK_DB_PATH), exist_ok=True)
            db_rows = _track_picks(results)
            db_n = record_picks("w7_t20", "W7 T20 右尾跟踪", db_rows, pick_date=date)
            print(f"[t20] stock_pick_db 写入 {db_n}/{len(db_rows)} 条 "
                  f"(strategy=w7_t20 pick_date={date})", flush=True)
        except Exception as exc:
            print(f"[t20] stock_pick_db 写入失败(不影响报告): {exc}", flush=True)
    layer_counts = {}
    for r in results:
        layer_counts[r["layer"]] = layer_counts.get(r["layer"], 0) + 1
    state_counts = {}
    for r in results:
        state_counts[r["lifecycle"]] = state_counts.get(r["lifecycle"], 0) + 1
    buys = [r for r in results if r["layer"] == "PRIMARY_BUY"]
    picks = [r for r in results if r.get("top_pick")]
    sli_blocked = [r for r in results if r.get("sli_block")]
    ige_covered = sum(1 for r in results if isinstance(r.get("ige_adj"), (int, float)))
    stats = {
        "date": date, "universe": len(rows), "results": len(results), "output": output,
        "regime": regime, "gate_level": gate_level,
        "layers": layer_counts, "states": {k: v for k, v in state_counts.items() if v},
        "ige": {"snapshot": ige_snap, "covered": ige_covered,
                "adj_min": min((r["ige_adj"] for r in results if isinstance(r.get("ige_adj"), (int, float))), default=None),
                "adj_max": max((r["ige_adj"] for r in results if isinstance(r.get("ige_adj"), (int, float))), default=None)},
        "sli": {"snapshot": str((sli_meta or {}).get("snapshot_date", "")),
                "leaders_in_results": sum(1 for r in results if r.get("sli_leader")),
                "blocked": [{"code": r["code"], "name": r["name"], "leader_type": r["sli_leader_type"],
                             "sub_rank": r["sli_rank"], "sli_v2": r["sli_v2"]} for r in sli_blocked]},
        "top_picks": [{"code": r["code"], "name": r["name"], "t20": r["t20_score"],
                       "retest": r["retest_quality"], "rr": r["rr"], "sli": sli_tag(r)} for r in picks],
        "primary_buys": [{"code": r["code"], "name": r["name"], "type": r["buy_type"],
                          "t20": r["t20_score"], "priority": r["priority"], "action": r["action"],
                          "ige_adj": (float(r["ige_adj"]) if isinstance(r.get("ige_adj"), (int, float)) else None)}
                         for r in buys],
        "elapsed": round(time.time() - t_start, 1),
    }
    print(json.dumps(stats, ensure_ascii=False))
    reader.close()


if __name__ == "__main__":
    main()