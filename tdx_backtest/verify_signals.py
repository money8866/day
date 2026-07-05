# -*- coding: utf-8 -*-
"""验证向量化版本信号与原版 strategy 函数是否一致

分别在每天调用原版 strategy 和向量化 strategy_vectorized,
对比信号数和具体触发的 (ts_code, trade_date) 是否一致.
"""
from __future__ import annotations
import os
import sys
import time
from collections import Counter

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_loader import load_kline, iter_all_day_files, tdx_filename_to_ts_code
from strategy_backtest import (
    Filters, strategy, precompute_indicators, strategy_vectorized,
    load_stock_names,
)


def main(start_date: str = "20250501", max_stocks: int = 100):
    load_stock_names()

    # 加载股票 + 预计算
    codes = []
    klines = {}
    klines_pre = {}
    t0 = time.time()
    for path in iter_all_day_files(markets=("SH", "SZ")):
        if max_stocks and len(codes) >= max_stocks:
            break
        ts_code = tdx_filename_to_ts_code(path)
        if not ts_code:
            continue
        if ts_code.startswith("999") or ts_code.startswith("8") or ts_code.startswith("4"):
            continue
        df = load_kline(ts_code, start_date="20240101")
        if df.empty or len(df) < 80:
            continue
        if "pct_chg" not in df.columns:
            df["pct_chg"] = df["close"].pct_change() * 100
        klines[ts_code] = df
        klines_pre[ts_code] = precompute_indicators(df)
        codes.append(ts_code)
    print(f"加载 {len(codes)} 只股票, 耗时 {time.time()-t0:.1f}s")

    # 收集回测区间
    all_dates = set()
    for df in klines.values():
        all_dates.update(df["trade_date"].tolist())
    trade_dates = sorted([d for d in all_dates if d >= start_date])
    print(f"回测交易日: {len(trade_dates)}")

    flt = Filters()
    print(f"过滤条件: {flt.label()}\n")

    # === 方式1: 原版 strategy (逐日逐股调用) ===
    print("=" * 70)
    print("  方式1: 原版 strategy (逐日逐股)")
    print("=" * 70)
    t0 = time.time()
    sig_orig = []  # [(ts_code, trade_date), ...]
    for i, td in enumerate(trade_dates):
        for ts_code in codes:
            df = klines[ts_code]
            # 截止 td 当天的切片
            mask = df["trade_date"] <= td
            df_slice = df[mask]
            if len(df_slice) < 80:
                continue
            try:
                # 假设市值满足
                if strategy(df_slice, ts_code, "强", total_mv=1e12, filters=flt):
                    sig_orig.append((ts_code, td))
            except Exception as e:
                pass
        if (i+1) % 50 == 0:
            print(f"  [{i+1}/{len(trade_dates)}] 累计信号 {len(sig_orig)}, 耗时 {time.time()-t0:.1f}s")
    t_orig = time.time() - t0
    print(f"  完成: 信号数 {len(sig_orig)}, 总耗时 {t_orig:.1f}s")

    # === 方式2: 向量化 strategy_vectorized ===
    print("\n" + "=" * 70)
    print("  方式2: 向量化 strategy_vectorized")
    print("=" * 70)
    t0 = time.time()
    sig_vec = []
    sig_vec_count = 0
    for ts_code in codes:
        df_pre = klines_pre[ts_code]
        sig = strategy_vectorized(df_pre, ts_code, flt)
        if not sig.any():
            continue
        sig_vec_count += int(sig.sum())
        # 转换为 (ts_code, trade_date) 列表
        df = klines[ts_code]
        for i, flag in enumerate(sig):
            if flag:
                td = df.iloc[i]["trade_date"]
                if td >= start_date:
                    sig_vec.append((ts_code, td))
    t_vec = time.time() - t0
    print(f"  完成: 信号数 {len(sig_vec)}, 总耗时 {t_vec:.1f}s")

    # === 对比 ===
    print("\n" + "=" * 70)
    print("  对比结果")
    print("=" * 70)
    set_orig = set(sig_orig)
    set_vec = set(sig_vec)
    common = set_orig & set_vec
    only_orig = set_orig - set_vec
    only_vec = set_vec - set_orig
    print(f"  原版信号数:     {len(set_orig)}")
    print(f"  向量化信号数:   {len(set_vec)}")
    print(f"  共同信号:       {len(common)}")
    print(f"  仅原版有:       {len(only_orig)}")
    print(f"  仅向量化有:     {len(only_vec)}")
    print(f"  加速比:         {t_orig/max(t_vec,0.001):.1f}x")

    if only_orig:
        print(f"\n  仅原版有的信号 (前10):")
        for ts, td in sorted(only_orig)[:10]:
            print(f"    {td}  {ts}")
    if only_vec:
        print(f"\n  仅向量化有的信号 (前10):")
        for ts, td in sorted(only_vec)[:10]:
            print(f"    {td}  {ts}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20250501")
    parser.add_argument("--max-stocks", type=int, default=100)
    args = parser.parse_args()
    main(args.start, args.max_stocks)
