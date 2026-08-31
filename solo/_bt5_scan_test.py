import sys
import time
sys.path.insert(0, r"D:\mystock\solo")
import numpy as np
import pandas as pd
from w7_second_wave_engine import (CacheReader, state_and_features, anchor_features, ANCHORS,
                                   similarity, alpha_hvt, alpha_trend, alpha_fina, alpha_rs,
                                   alpha_upside, t120_alpha_score, entry_score_v2, MarketCtx,
                                   finite, lifecycle, hvt_future_space, hvt_acceleration,
                                   hvt_platform, hvt_distribution_risk, hvt_v3_score,
                                   rank_score_v5, hvt_type, WATCH_MIN_SCORE)
from w7_backtest_v5 import find_events, v5_status

DATE_END = "20260828"
reader = CacheReader()
reader.load_all(DATE_END, codes=["000858.SZ", "300308.SZ", "000001.SZ", "600519.SH"], min_date="20230101", chunk=100, verbose=False)
reader.load_fina()
mdates, mvals = reader.market_curve(DATE_END)
mkt = MarketCtx(mdates, mvals)
anchors = {}
for label, (code, adate) in ANCHORS.items():
    anchors[label] = anchor_features(reader.bars_sql(code, DATE_END), adate)

def scan_one(code):
    t0 = time.time()
    df = reader.bars(code, DATE_END)
    events = find_events(df)
    n = len(df)
    closes = df.close.to_numpy(dtype=float)
    t1 = time.time()
    cnt = 0
    samples = []
    for i, ep in events:
        event_low = finite(df.low.iloc[i])
        for j in range(i + 1, min(i + 60, n - 2)):
            res = state_and_features(df, i, ep, end=j)
            if not res:
                continue
            base, state, pp, pp_ok, reexp, breakout, major_risk, dd, pressure = res
            d = str(df.iloc[j].trade_date)
            sim_a = similarity(base, anchors.get("中际旭创"))
            sim_b = similarity(base, anchors.get("华正新材"))
            hvt = (sim_a + sim_b) / 2.0
            dims = {"hvt": alpha_hvt(base, hvt, dd), "trend": alpha_trend(df, j),
                    "fina": alpha_fina(*reader.fina(code, as_of=d)), "rs": alpha_rs(df, j, mkt),
                    "upside": alpha_upside(df, j), "sector": 50.0}
            lc = lifecycle(df, j)
            fs = hvt_future_space(df, j, lc)
            acc = hvt_acceleration(df, j, mkt)
            plat = hvt_platform(df, i, j)
            dist_risk = hvt_distribution_risk(df, j, base, lc)
            score, base_score, absorption, penalty = hvt_v3_score(base, lc, dims["hvt"], fs, acc, dims["rs"], dims["fina"], plat, dist_risk)
            tp = hvt_type(state, lc, dist_risk)
            trend_confirmed = breakout or reexp or dims["trend"] >= 70
            status = v5_status(score, entry_score_v2(df, j, pp, pp_ok, reexp, breakout, event_low, mkt)[0], trend_confirmed, tp)
            if status == "WATCH" and base_score < WATCH_MIN_SCORE:
                continue
            cnt += 1
    return len(events), t1 - t0, time.time() - t1, cnt

for code in ["000858.SZ", "300308.SZ", "000001.SZ", "600519.SH"]:
    ev, t_find, t_scan, cnt = scan_one(code)
    print(f"{code}: events={ev} find_events={t_find:.2f}s scan={t_scan:.2f}s hits={cnt}", flush=True)
reader.close()
