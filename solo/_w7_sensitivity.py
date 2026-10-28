# -*- coding: utf-8 -*-
"""301550.SZ W7 阈值敏感性反事实：找出「差一点就触发」的日子。

对类型A/类型B 的关键阈值逐项放松，统计：
  - 现口径触发集
  - 单项放松后的增量触发日（含事后表现）
用于判断「引擎对这票是漏判」还是「这票确实不满足 W7 形态」。
"""
import importlib.util
import os
import sys
from itertools import product

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


def probe(mod, df, ei, end, shrink_a, shrink_b, break_volr):
    """在给定阈值下检查类型A/B，返回 (state, info) 或 (None, None)。"""
    lv = mod._t0_levels(df, ei)
    t0_open, t0_close, t0_vol = lv["t0_open"], lv["t0_close"], lv["t0_vol"]
    if min(t0_open, t0_close, t0_vol) <= 0:
        return None, None
    latest = df.iloc[end]
    age = end - ei
    vol20_pre = mod.safe_mean(df.vol.iloc[max(0, ei - 20):ei])
    if vol20_pre <= 0 or t0_vol < vol20_pre * 1.3:
        return None, None
    cut = mod._t0_pullback_segment(df, ei, end)
    if cut is None:
        return None, None
    pull, pk_rel, cur_low, low_rel = cut
    if low_rel < 0:
        return None, None
    seg_all = df.iloc[ei + 1:end + 1]
    deep_low = mod.finite(seg_all.low.min(), 0.0)
    if deep_low <= 0:
        return None, None
    o, h, l, c = (mod.finite(latest.open), mod.finite(latest.high),
                  mod.finite(latest.low), mod.finite(latest.close))
    if min(o, h, l, c) <= 0:
        return None, None
    vol20_now = mod.safe_mean(df.vol.iloc[max(0, end - 19):end])
    ma60 = mod.finite(latest.ma_bfq_60, 0.0)

    # ---- 类型B ----
    if 3 <= age <= 40:
        pre = df.iloc[ei + 1:end]
        if not pre.empty:
            pre_low = mod.finite(pre.low.min(), 0.0)
            pre_vol = mod.safe_mean(pre.vol)
            span = h - l
            if (pre_low > 0 and pre_low <= t0_close * (1 - mod.W7_T0_PULLBACK_MIN)
                    and pre_low >= t0_open * (1 - mod.W7_T0_OPEN_TOL)
                    and l >= t0_open * (1 - mod.W7_T0_OPEN_TOL)
                    and pre_vol <= t0_vol * shrink_b
                    and c > t0_close
                    and vol20_now > 0 and mod.finite(latest.vol) >= vol20_now * break_volr
                    and span > 0 and mod.pct_position(o, h, l, c) >= mod.W7_T0_BREAK_CLOSE_POS
                    and (h - c) / span <= mod.W7_T0_BREAK_SHADOW
                    and (ma60 <= 0 or c >= ma60 * mod.W7_T0_MA60_K)
                    and c <= t0_close * mod.W7_T0_BREAK_MAX_EXT):
                return "T0_REBREAK", {"t0_date": lv["t0_date"], "t0_open": t0_open, "t0_close": t0_close,
                                      "pull_low": pre_low, "age": age,
                                      "break_volr": mod.finite(latest.vol) / vol20_now if vol20_now else 0}

    # ---- 类型A ----
    if 3 <= age <= 60:
        pull_vol = mod.safe_mean(pull.vol)
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
        low_idx = ei + 1 + pk_rel + 1 + low_rel
        if (deep_low <= t0_close * (1 - mod.W7_T0_PULLBACK_MIN)
                and deep_low <= t0_open * (1 + mod.W7_T0_REACH)
                and deep_low >= t0_open * (1 - mod.W7_T0_OPEN_TOL)
                and pull_vol <= t0_vol * shrink_a
                and stall
                and end - low_idx <= mod.W7_T0_STALL_MAX_BARS
                and c <= cur_low * 1.05
                and (ma60 <= 0 or c >= ma60 * mod.W7_T0_MA60_K)
                and c <= t0_close * mod.W7_T0_STALL_MAX_EXT
                and (vol20_now <= 0 or mod.finite(latest.vol) <= vol20_now * mod.W7_T0_STALL_VOLR)):
            return "T0_PULLBACK_HOLD", {"t0_date": lv["t0_date"], "t0_open": t0_open, "t0_close": t0_close,
                                        "pull_low": deep_low, "age": age, "stall": stall,
                                        "pull_vol_ratio": pull_vol / t0_vol}
    return None, None


def main():
    mod = load_engine()
    reader = mod.CacheReader()
    code = "301550.SZ"
    idx = pd.read_sql_query(
        "SELECT trade_date, close FROM index_daily_cache WHERE ts_code=? ORDER BY trade_date",
        reader.conn, params=(mod.MARKET_INDEX_CODE,))
    idx["trade_date"] = idx.trade_date.astype(str)
    idx["close"] = pd.to_numeric(idx.close, errors="coerce")
    idx_map = dict(zip(idx.trade_date, idx.close))
    idx_dates = list(idx.trade_date)
    full = reader.bars_sql(code, "20261028")
    alld = list(full.trade_date.astype(str))
    cmap = dict(zip(alld, full.close.astype(float)))
    lowmap = dict(zip(alld, full.low.astype(float)))
    q = ("SELECT DISTINCT trade_date FROM daily_cache WHERE ts_code=? AND trade_date>='20260801' ORDER BY trade_date")
    dates = [r[0] for r in reader.conn.execute(q, (code,))]

    # 预取每日 K 线与现口径锚
    day_cache = {}
    for d in dates:
        df = reader.bars_sql(code, d)
        if len(df) < mod.MIN_BARS:
            continue
        anc = mod.find_event_anchor(df) or mod.find_activation_anchor(df)
        if not anc:
            continue
        day_cache[d] = (df, anc[0], len(df) - 1)

    scenarios = [
        ("现口径", 0.50, 0.70, 2.20),
        ("A缩量0.70", 0.70, 0.70, 2.20),
        ("A缩量0.85", 0.85, 0.70, 2.20),
        ("A缩量1.00", 1.00, 0.70, 2.20),
        ("B缩量0.85", 0.50, 0.85, 2.20),
        ("B突破量1.50", 0.50, 0.70, 1.50),
        ("B突破量1.20", 0.50, 0.70, 1.20),
        ("A缩量0.85+B量1.50", 0.85, 0.70, 1.50),
        ("全放松(A1.0/B1.2)", 1.00, 0.85, 1.20),
    ]
    allrows = []
    base_set = None
    for name, sa, sb, bv in scenarios:
        hits = []
        for d, (df, ei, end) in day_cache.items():
            st, info = probe(mod, df, ei, end, sa, sb, bv)
            if not st:
                continue
            entry = mod.finite(df.iloc[end].close)
            i0 = alld.index(d)
            fail = mod.finite(info.get("t0_open"), 0.0) if st == "T0_PULLBACK_HOLD" else mod.finite(info.get("pull_low"), 0.0)
            rec = {"情景": name, "date": d, "state": st, "T0": info.get("t0_date"), "age": info.get("age"),
                   "entry": entry, "失效位": round(fail, 2)}
            for h in (3, 10):
                j = i0 + h
                rec["T+%d" % h] = round((cmap[alld[j]] / entry - 1) * 100, 2) if j < len(alld) else None
                ij = idx_dates.index(d)
                k = ij + h
                rec["超额+%d" % h] = (round((cmap[alld[j]] / entry - 1) * 100 - (idx_map[idx_dates[k]] / idx_map[d] - 1) * 100, 2)
                                   if (j < len(alld) and k < len(idx_dates)) else None)
            hit = next((alld[j] for j in range(i0 + 1, min(i0 + 11, len(alld))) if lowmap[alld[j]] <= fail), None)
            rec["10日内触失效位"] = hit or "未触及"
            hits.append(rec)
            allrows.append(rec)
        s = {(h["date"], h["state"]) for h in hits}
        if base_set is None:
            base_set = s
            print("现口径触发：%d 个" % len(s))
        else:
            new = s - base_set
            print("%-22s 触发=%2d  较现口径新增=%d %s" % (name, len(s), len(new),
                                                         sorted(new) if new else ""))
    out = pd.DataFrame(allrows)
    p = os.path.join(SOLO, "_w7_replay_out", "sensitivity_301550.csv")
    out.to_csv(p, index=False, encoding="utf-8-sig")
    print("\n输出：%s" % p)
    reader.close()


if __name__ == "__main__":
    main()
