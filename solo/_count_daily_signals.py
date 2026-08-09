# -*- coding: utf-8 -*-
"""统计"三指数动量>+3%"闸门通过日里的每日原始候选信号数分布(不限Top5)"""
import os, sys, time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

TDX_BT = r"d:\mystock\tdx_backtest"
SOLO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SOLO_DIR)
sys.path.insert(0, TDX_BT)
from data_loader import load_kline, iter_all_day_files, tdx_filename_to_ts_code
from strategy_backtest import load_stock_names
from volume_surge_strategy import (precompute_indicators,
                                   volume_surge_strategy_vectorized,
                                   VolSurgeFilters)
sys.path.insert(0, SOLO_DIR)
from winrate_vs_market_env import build_market_env, INDEX_CODES

THRESHOLD = 3.0  # 与 MOM_GATE_THRESHOLD 保持一致

start_date, end_date = "20240101", "20260807"
vf = VolSurgeFilters()
t0 = time.time()
load_stock_names()
dt = datetime.strptime(start_date, "%Y%m%d")
load_start = (dt - timedelta(days=400)).strftime("%Y%m%d")

# --- 三指数环境 ---
index_dfs = {}
for code in INDEX_CODES:
    try:
        df = load_kline(code, start_date=load_start, end_date=end_date)
        if not df.empty:
            index_dfs[code] = df
    except Exception as e:
        print(f"[Market] {code} 加载失败: {e}")
env_df = build_market_env(index_dfs) if index_dfs else pd.DataFrame()
print(f"[Market] 环境特征: {len(env_df)} 天")

# --- 全市场K线 ---
kline_dict = {}
codes_loaded = 0
for path in iter_all_day_files(markets=("SH", "SZ")):
    ts_code = tdx_filename_to_ts_code(path)
    if not ts_code or ts_code.startswith(("999", "8", "4", "5", "1", "2")):
        continue
    df = load_kline(ts_code, start_date=load_start, end_date=end_date)
    if df.empty or len(df) < 180:
        continue
    kline_dict[ts_code] = precompute_indicators(df)
    codes_loaded += 1
print(f"[Load] 加载 {codes_loaded} 只, 耗时 {time.time()-t0:.1f}s")

# --- 信号 ---
t0 = time.time()
signals_dict = {}
for ts_code, df_pre in kline_dict.items():
    sig = volume_surge_strategy_vectorized(df_pre, ts_code, vf)
    if sig.any():
        signals_dict[ts_code] = sig
print(f"[Signal] {len(signals_dict)} 只有信号, 耗时 {time.time()-t0:.1f}s")

all_dates = set()
for df in kline_dict.values():
    all_dates.update(df["trade_date"].tolist())
trade_dates = sorted(d for d in all_dates if start_date <= d <= end_date)
date_idx_map = {c: dict(zip(d["trade_date"], d.index))
                for c, d in kline_dict.items()}

# --- 统计通过闸门日的每日候选数 ---
daily_n = []      # 通过闸门日的候选信号数
daily_n_top5 = [] # 通过闸门日的Top5后成交数
passed_days = 0
for td in trade_dates:
    env = env_df.loc[td] if td in env_df.index else None
    if env is None:
        continue
    mom = float(env["mom20_avg"])
    if mom <= THRESHOLD:
        continue
    passed_days += 1
    n = 0
    for ts_code, sig in signals_dict.items():
        idx_map = date_idx_map.get(ts_code)
        if not idx_map:
            continue
        i = idx_map.get(td)
        if i is None or i >= len(sig):
            continue
        if sig[i] > 0:
            n += 1
    daily_n.append(n)
    daily_n_top5.append(min(n, 5))

arr = np.array(daily_n)
arr5 = np.array(daily_n_top5)
print("\n" + "=" * 60)
print(f"  三指数动量>+{THRESHOLD}% 闸门通过日统计")
print("=" * 60)
print(f"  通过闸门交易日:      {passed_days} 天")
print(f"  每日原始候选信号:")
print(f"    平均:             {arr.mean():.2f} 只/天")
print(f"    中位数:           {np.median(arr):.0f} 只/天")
print(f"    最小/最大:        {arr.min()} / {arr.max()} 只/天")
print(f"    每日候选≥1 天数:  {(arr >= 1).sum()} ({((arr >= 1).sum()/passed_days*100):.0f}%)")
print(f"    每日候选≥5 天数:  {(arr >= 5).sum()} ({((arr >= 5).sum()/passed_days*100):.0f}%)")
print(f"  每日Top5成交(实际):")
print(f"    平均:             {arr5.mean():.2f} 只/天")
print(f"    总和:             {arr5.sum()} 个信号")
# 分布
print(f"\n  每日候选数分布:")
bins = [(0,0),(1,2),(3,5),(6,10),(11,20),(21,np.inf)]
for lo, hi in bins:
    cnt = int(((arr >= lo) & (arr <= hi)).sum()) if hi != np.inf else int((arr >= lo).sum())
    label = f"{lo}" if lo == hi else f"{lo}-{hi}" if hi != np.inf else f">={lo}"
    print(f"    {label:>5} 只: {cnt:>4} 天 ({cnt/passed_days*100:5.1f}%)")
