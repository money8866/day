# -*- coding: utf-8 -*-
"""诊断：单票逐日 as-of 回放中，T0 锚定与类型A/B 探测器逐条卡点定位。"""
import importlib.util
import os
import sys

import numpy as np
import pandas as pd

SOLO = r"d:\mystock\solo"
ENGINE = os.path.join(SOLO, "w7_second_wave_engine.py")


def load_engine():
    spec = importlib.util.spec_from_file_location("w7eng", ENGINE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["w7eng"] = mod
    spec.loader.exec_module(mod)
    return mod


def diag_t0(mod, df, event_idx, end, tag):
    """逐条打印类型A/B 判定过程。"""
    lv = mod._t0_levels(df, event_idx)
    t0_open, t0_close, t0_vol = lv["t0_open"], lv["t0_close"], lv["t0_vol"]
    latest = df.iloc[end]
    out = []
    out.append(("T0", "%s O=%.2f C=%.2f vol=%.0f pct=%.2f" % (lv["t0_date"], t0_open, t0_close, t0_vol, lv["t0_pct"])))
    age = end - event_idx
    if age < 3:
        out.append(("年龄", "age=%d <3 直接排除" % age))
        return out
    if age > mod.W7_T0_MAX_AGE:
        out.append(("年龄", "age=%d >MAX_AGE=%d 排除" % (age, mod.W7_T0_MAX_AGE)))
        return out
    vol20_pre = mod.safe_mean(df.vol.iloc[max(0, event_idx - 20):event_idx])
    r1 = t0_vol >= vol20_pre * 1.3
    out.append(("①T0放量", "%.0f vs %.0f×1.3=%s" % (t0_vol, vol20_pre, "OK" if r1 else "FAIL")))
    cut = mod._t0_pullback_segment(df, event_idx, end)
    if cut is None:
        out.append(("回踩段", "None"))
        return out
    pull, pk_rel, cur_low, low_rel = cut
    seg_all = df.iloc[event_idx + 1:end + 1]
    deep_low = mod.finite(seg_all.low.min(), 0.0)
    out.append(("回踩段", "bars=%d 峰值rel=%d 最低rel=%d deep_low=%.2f" % (len(pull), pk_rel, low_rel, deep_low)))
    if low_rel < 0:
        out.append(("回踩段", "尚未出现回踩段（仍在冲高途中）"))
        return out
    r2 = deep_low <= t0_close * (1 - mod.W7_T0_PULLBACK_MIN)
    out.append(("②回踩成立", "deep_low=%.2f vs T0收×%.2f=%s" % (deep_low, 1 - mod.W7_T0_PULLBACK_MIN, "OK" if r2 else "FAIL")))
    r3 = deep_low <= t0_open * (1 + mod.W7_T0_REACH)
    out.append(("③回踩到位", "deep_low=%.2f vs T0开×%.2f=%s" % (deep_low, 1 + mod.W7_T0_REACH, "OK" if r3 else "FAIL")))
    r4 = deep_low >= t0_open * (1 - mod.W7_T0_OPEN_TOL)
    out.append(("④守住T0开", "deep_low=%.2f vs %.2f=%s" % (deep_low, t0_open * (1 - mod.W7_T0_OPEN_TOL), "OK" if r4 else "FAIL")))
    pull_vol = mod.safe_mean(pull.vol)
    r5 = pull_vol <= t0_vol * mod.W7_T0_SHRINK
    out.append(("⑤缩量(A)", "%.0f vs %.0f×%.2f=%s" % (pull_vol, t0_vol, mod.W7_T0_SHRINK, "OK" if r5 else "FAIL")))
    pre = df.iloc[event_idx + 1:end]
    pre_vol = mod.safe_mean(pre.vol) if not pre.empty else 0
    r5b = pre_vol <= t0_vol * mod.W7_T0_SHRINK_B
    out.append(("③缩量(B)", "%.0f vs %.0f×%.2f=%s" % (pre_vol, t0_vol, mod.W7_T0_SHRINK_B, "OK" if r5b else "FAIL")))
    o, h, l, c = (mod.finite(latest.open), mod.finite(latest.high), mod.finite(latest.low), mod.finite(latest.close))
    span = h - l
    body = abs(c - o)
    up_sh = (h - max(o, c)) / span if span > 0 else 0
    dn_sh = (min(o, c) - l) / span if span > 0 else 0
    pct = mod.finite(latest.pct_chg)
    stall = ""
    if span > 0:
        if body <= 0.35 * span and up_sh >= 0.15 and dn_sh >= 0.15:
            stall = "十字星"
        elif dn_sh >= 0.40:
            stall = "长下影"
        elif c > o and pct >= 1.5:
            stall = "阳线收复"
    out.append(("⑥止跌信号", "%s (body=%.2f up=%.2f dn=%.2f pct=%.2f)" % (stall or "无", body / span if span else 0, up_sh, dn_sh, pct)))
    low_idx = event_idx + 1 + pk_rel + 1 + low_rel
    out.append(("⑦低点邻近", "end-low_idx=%d (max=%d) 收盘vs低点=%.2f%% %s"
                % (end - low_idx, mod.W7_T0_STALL_MAX_BARS, (c / cur_low - 1) * 100 if cur_low else 0,
                   "OK" if (end - low_idx <= mod.W7_T0_STALL_MAX_BARS and c <= cur_low * 1.05) else "FAIL")))
    ma60 = mod.finite(latest.ma_bfq_60, 0.0)
    out.append(("⑧中期未破", "close=%.2f vs MA60×%.2f=%.2f %s" % (c, mod.W7_T0_MA60_K, ma60 * mod.W7_T0_MA60_K,
                                                          "OK" if (ma60 <= 0 or c >= ma60 * mod.W7_T0_MA60_K) else "FAIL")))
    out.append(("⑨不追高(A)", "close=%.2f vs T0收×%.2f=%.2f %s" % (c, mod.W7_T0_STALL_MAX_EXT, t0_close * mod.W7_T0_STALL_MAX_EXT,
                                                          "OK" if c <= t0_close * mod.W7_T0_STALL_MAX_EXT else "FAIL")))
    vol20_now = mod.safe_mean(df.vol.iloc[max(0, end - 19):end])
    r10 = (vol20_now <= 0) or (mod.finite(latest.vol) <= vol20_now * mod.W7_T0_STALL_VOLR)
    out.append(("⑩止跌非放量", "vol=%.0f vs vol20×%.2f=%.0f %s" % (mod.finite(latest.vol), mod.W7_T0_STALL_VOLR,
                                                             vol20_now * mod.W7_T0_STALL_VOLR, "OK" if r10 else "FAIL")))
    # 类型B
    out.append(("④突破(B)", "close=%.2f vs T0收=%.2f %s" % (c, t0_close, "OK" if c > t0_close else "FAIL")))
    r5b2 = vol20_now > 0 and mod.finite(latest.vol) >= vol20_now * mod.W7_T0_BREAK_VOLR
    out.append(("⑤放量(B)", "vol=%.0f vs vol20×%.2f=%.0f %s" % (mod.finite(latest.vol), mod.W7_T0_BREAK_VOLR,
                                                            vol20_now * mod.W7_T0_BREAK_VOLR, "OK" if r5b2 else "FAIL")))
    pos = mod.pct_position(o, h, l, c)
    out.append(("⑥收盘强势(B)", "pos=%.0f%% (≥%d) 上影=%.2f (≤%.2f)" % (pos, mod.W7_T0_BREAK_CLOSE_POS,
                                                                  (h - c) / span if span else 0, mod.W7_T0_BREAK_SHADOW)))
    return out


def main():
    mod = load_engine()
    reader = mod.CacheReader()
    reader.load_fina()
    code = sys.argv[1] if len(sys.argv) > 1 else "301550.SZ"
    start = sys.argv[2] if len(sys.argv) > 2 else "20260801"

    q = ("SELECT DISTINCT trade_date FROM daily_cache WHERE ts_code=? "
         "AND trade_date>=? ORDER BY trade_date")
    dates = [r[0] for r in reader.conn.execute(q, (code, start))]
    basic = reader.basic.loc[code] if code in reader.basic.index else {}
    name = str(basic.get("name", code))

    print("=== %s %s 逐日锚定诊断 ===" % (code, name))
    for d in dates:
        df = reader.bars_sql(code, d)
        if len(df) < mod.MIN_BARS:
            print("%s NO_DATA" % d)
            continue
        anchor = mod.find_event_anchor(df)
        fb = False
        if not anchor:
            anchor = mod.find_activation_anchor(df)
            fb = True
        if not anchor:
            print("%s  NO_ANCHOR(两种锚都找不到)" % d)
            continue
        ei, ep = anchor
        ev = df.iloc[ei]
        # preshift 后锚
        print("%s  T0=%s pct=%.2f vol=%.0f %s | 收盘=%.2f age=%d"
              % (d, ev.trade_date, mod.finite(ev.pct_chg), mod.finite(ev.vol),
                 "(兜底锚)" if fb else "(P%.0f)" % ep, mod.finite(df.iloc[-1].close), len(df) - 1 - ei))
        # 只打印距锚 3~15 根的关键日（信号可能出现的窗口）
        age = len(df) - 1 - ei
        if 3 <= age <= 16:
            for k, v in diag_t0(mod, df, ei, len(df) - 1, d):
                print("        %-14s %s" % (k, v))
    reader.close()


if __name__ == "__main__":
    main()
