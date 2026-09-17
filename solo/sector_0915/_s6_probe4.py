# -*- coding: utf-8 -*-
"""Step 6 探针 4：测量 legacy HVT 引擎（detect/evaluate/update_tracking）的可用性与单股成本。"""
import os
import sys
import sqlite3
import time

import numpy as np
import pandas as pd
import yaml

PROJ = r"d:\mystock\solo"
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from hvt_bull.engine import HvtBullEngine  # noqa: E402

with open(os.path.join(PROJ, "hvt_bull", "config.yaml"), encoding="utf-8") as f:
    LCFG = yaml.safe_load(f)

DB = r"D:\mystock\cache_daily\stock_data.db"
START, END = "20240801", "20260916"

cand = pd.read_csv(r"d:\mystock\solo\sector_0915\data\sector_stock_candidate_daily.csv",
                   dtype={"trade_date": str, "ts_code": str}, low_memory=False)
sub = cand[cand["candidate_status"].isin(["CANDIDATE", "WATCH"])]
codes = sorted(sub["ts_code"].unique())[:12]

con = sqlite3.connect(DB)
px = pd.read_sql_query(
    "SELECT ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol, amount "
    "FROM daily_cache WHERE trade_date>=? AND trade_date<=?", con, params=(START, END))
db = pd.read_sql_query(
    "SELECT ts_code, trade_date, turnover_rate, volume_ratio FROM daily_basic_cache "
    "WHERE trade_date>=? AND trade_date<=?", con, params=(START, END))
con.close()
for d in (px, db):
    d["ts_code"] = d["ts_code"].astype(str)
    d["trade_date"] = d["trade_date"].astype(str)
px = px[px["ts_code"].isin(codes)]
db = db[db["ts_code"].isin(codes)]
print("px", px.shape, "db", db.shape)

df_all = px.merge(db, on=["ts_code", "trade_date"], how="left").sort_values(
    ["ts_code", "trade_date"], kind="mergesort").reset_index(drop=True)
df_all = df_all.dropna(subset=["close", "vol"]).reset_index(drop=True)

eng = HvtBullEngine(LCFG)
n_ev = 0
t_detect = 0.0
t_track = 0.0
n_track = 0
for code, g in df_all.groupby("ts_code", sort=False):
    g = g.reset_index(drop=True)
    n = len(g)
    if n < 80:
        continue
    tr = g["turnover_rate"].to_numpy(dtype=float)
    prev20 = pd.Series(tr).shift(1).rolling(20, min_periods=10).mean().to_numpy()
    tratio = np.where(prev20 > 0, tr / prev20, 0.0)
    q95 = pd.Series(tr).shift(1).rolling(120, min_periods=60).quantile(0.95).to_numpy()
    cand_idx = np.where((tratio >= 1.8) & (tr >= np.nan_to_num(q95, nan=1e18) * 0.98))[0]
    t0 = time.time()
    evs = []
    for idx in cand_idx:
        ev = eng.detect_hvt(g, int(idx))
        if ev is None:
            continue
        eng.evaluate_event(g, ev)
        if not eng.price_strength_ok(ev):
            continue
        evs.append(int(idx))
    t_detect += time.time() - t0
    n_ev += len(evs)
    # 对最近事件在若干 panel 日上跑 update_tracking
    if evs:
        for end_idx in range(max(0, n - 10), n):
            ev = eng.detect_hvt(g, evs[-1])
            if ev is None:
                continue
            eng.evaluate_event(g, ev)
            t0 = time.time()
            eng.update_tracking(g, ev, end_idx=end_idx + 1)
            t_track += time.time() - t0
            n_track += 1
    print(f"{code}: rows={n} prefilter={len(cand_idx)} hvt={len(evs)} state={getattr(evs and ev or None,'state',None) if evs else None}")

print(f"stocks={len(codes)} hvt_events={n_ev} detect_time={t_detect:.2f}s "
      f"track_calls={n_track} track_time={t_track:.3f}s "
      f"per_track_ms={1000*t_track/max(1,n_track):.2f}")
print(f"extrapolate 3557 stocks: detect={t_detect/len(codes)*3557:.0f}s "
      f"track={t_track/max(1,n_track)*37042:.0f}s")
