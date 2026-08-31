# -*- coding: utf-8 -*-
"""修复突破验证：扫描华正新材事件后各时点(20/40/60/90天)，确认曾FAILED后修复突破会重新出信号"""
import sys
sys.path.insert(0, r"D:\mystock\solo")
import numpy as np
from w7_second_wave_engine import (
    CacheReader, MarketCtx, state_and_features, extreme_event,
    similarity, anchor_features, alpha_hvt, alpha_trend, alpha_fina,
    alpha_rs, alpha_upside, alpha_sector, t120_alpha_score, entry_score_v2,
    finite, MIN_BARS, DIM_NAMES,
)

reader = CacheReader()
mdates, mvals = reader.market_curve("20260828")
mkt = MarketCtx(mdates, mvals)
nfina = reader.load_fina()

ANCHORS2 = {"中际旭创": ("300308.SZ", "20250508"), "华正新材": ("603186.SH", "20250812")}
anchors = {}
for label, (code, ad) in ANCHORS2.items():
    anchors[label] = anchor_features(reader.bars_sql(code, "20260828"), ad)

code, ad = "603186.SH", "20250812"
df_full = reader.bars_sql(code, "20260828")
dts = df_full.trade_date.astype(str).tolist()
i = dts.index(ad)
closes = df_full.close.to_numpy(dtype=float)

print(f"华正新材 {code} 天量日 {ad} (close={closes[i]:.2f}) 事件后时点扫描：\n")
for offset in (20, 40, 60, 90):
    end = min(i + offset, len(df_full) - 1)
    df_slice = df_full.iloc[:end + 1].reset_index(drop=True)
    dts_s = df_slice.trade_date.astype(str).tolist()
    ei = dts_s.index(ad)
    ok, ep = extreme_event(df_slice, ei)
    base, state, pp, pp_ok, reexp, breakout, major_risk, drawdown, pressure = state_and_features(df_slice, ei, ep)
    last = len(df_slice) - 1
    current = dict(base)
    sim_a = similarity(current, anchors.get("中际旭创"))
    sim_b = similarity(current, anchors.get("华正新材"))
    hvt = (sim_a + sim_b) / 2
    fina_now, fina_prev = reader.fina(code, as_of=str(df_slice.iloc[last].trade_date))
    dims = {
        "hvt": alpha_hvt(base, hvt, drawdown),
        "trend": alpha_trend(df_slice, last),
        "fina": alpha_fina(fina_now, fina_prev),
        "rs": alpha_rs(df_slice, last, mkt),
        "upside": alpha_upside(df_slice, last),
        "sector": alpha_sector("", {}, {}),
    }
    t120 = t120_alpha_score(dims)
    event_low = finite(df_slice.low.iloc[ei], 0.0)
    entry, entry_dims = entry_score_v2(df_slice, last, pp, pp_ok, reexp, breakout, event_low, mkt)
    trend_state = state in ("BREAKOUT_CONFIRM", "SECOND_WAVE", "RE_EXPANSION")
    trend_confirmed = breakout or reexp or dims["trend"] >= 70
    if trend_state:
        if t120 >= 85 and entry >= 80: status = "PRIMARY_BUY"
        elif t120 >= 85: status = "T120_ROCKET"
        elif t120 >= 70 and trend_confirmed: status = "CONFIRMED"
        else: status = "WATCH"
    elif state in ("FAILED", "DISTRIBUTION") or major_risk:
        status = "CONFIRMED" if (breakout and t120 >= 70) else "WATCH"
    elif t120 >= 85 and entry >= 80: status = "PRIMARY_BUY"
    elif t120 >= 85: status = "T120_ROCKET"
    elif t120 >= 70 and trend_confirmed: status = "CONFIRMED"
    else: status = "WATCH"
    r120 = closes[min(i + 120, len(closes) - 1)] / closes[i] - 1
    print(f"T+{offset:>3} ({dts_s[-1]}): state={state:<16} T120={t120:5.1f} ENTRY={entry:5.1f} breakout={breakout} buy={status:<11} 当时价={closes[end]:6.2f} → 最终T120={r120*100:+.0f}%")

reader.close()
