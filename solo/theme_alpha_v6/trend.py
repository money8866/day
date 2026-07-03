#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V6.0 - 趋势评分模块

四维合成：
 ① Relative Momentum (35%) — 多周期收益率 + 全主题百分位排名
 ② MA Breadth (20%)        — 站上MA5/10/20/60 的股票比例
 ③ Trend Persistence (25%) — 连续新高/EMA20方向/上涨天数/Higher High & Low
 ④ Drawdown Quality (20%)  — 最大回撤/恢复速度
"""
import os, sys, warnings
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
warnings.filterwarnings("ignore")


def _percentile_rank(value, all_values):
    """计算在全部主题中的百分位排名 (0-100)"""
    arr = np.array(all_values)
    rank = np.sum(arr <= value) / len(arr) * 100
    return float(rank)


def compute_momentum(daily, codes):
    """计算多周期收益率"""
    sub = daily[daily["ts_code"].isin(codes)].copy()
    if sub.empty:
        return 0, 0, 0, 0
    price = sub.groupby("trade_date")["close"].mean().sort_index()
    n = len(price)
    r5 = (price.iloc[-1] / price.iloc[-6] - 1) if n > 5 else 0
    r10 = (price.iloc[-1] / price.iloc[-11] - 1) if n > 10 else 0
    r20 = (price.iloc[-1] / price.iloc[-21] - 1) if n > 20 else 0
    r40 = (price.iloc[-1] / price.iloc[-41] - 1) if n > 40 else 0
    return r5, r10, r20, r40


def compute_trend_score(daily, codes, all_momentums=None):
    """返回 0-100 趋势评分"""
    sub = daily[daily["ts_code"].isin(codes)].copy()
    if sub.empty or len(codes) < 3:
        return 50.0

    # ===== ① Relative Momentum (35%) =====
    r5, r10, r20, r40 = compute_momentum(daily, codes)
    raw_mom = r5 * 0.25 + r10 * 0.30 + r20 * 0.25 + r40 * 0.20

    if all_momentums is not None:
        # 使用全主题百分位排名
        mom_pct = _percentile_rank(raw_mom, all_momentums)
    else:
        mom_pct = np.clip(raw_mom * 1500 + 50, 0, 100)

    # ===== ② MA Breadth (20%) — 向量化 =====
    latest_day = sub["trade_date"].max()
    latest = sub[sub["trade_date"] == latest_day]
    # 用 groupby 一次性计算每只股票的 MA5/10/20/60
    sorted_sub = sub.sort_values(["ts_code", "trade_date"])
    close_arr = sorted_sub.groupby("ts_code")["close"].apply(list)
    ma_counts = []
    for c in close_arr:
        if len(c) < 60:
            continue
        arr = np.array(c, dtype=float)
        p = arr[-1]
        s = ((p > arr[-5:].mean()) + (p > arr[-10:].mean()) +
             (p > arr[-20:].mean()) + (p > arr[-60:].mean()))
        ma_counts.append(s)
    ma_score = (np.mean(ma_counts) / 4.0 * 100) if ma_counts else 50

    # ===== ③ Trend Persistence (25%) =====
    price = sub.groupby("trade_date")["close"].mean().sort_index()
    pct = sub.groupby("trade_date")["pct_chg"].mean().sort_index().values
    n = len(price)

    # 连续创新高
    cum_max = np.maximum.accumulate(price.values)
    new_high_days = np.sum(price.values[-20:] == cum_max[-20:]) if n >= 20 else 0

    # EMA20方向
    ema_up = 1 if n >= 20 and price.values[-1] > price.values[-21] else 0

    # 上涨天数占比
    up_ratio = np.sum(pct[-20:] > 0) / min(n, 20) if n > 0 else 0

    # Higher High / Higher Low (向量化，修复原逻辑错误)
    hh = 0
    hl = 0
    if n >= 10:
        vals = price.values[-10:]
        hh = int(np.sum(np.diff(vals) > 0))
        # Higher Low: 每日低点是否高于前日低点
        low_series = sub.groupby("trade_date")["low"].min().sort_index().values[-10:]
        hl = int(np.sum(np.diff(low_series) > 0))

    persist = (min(new_high_days / 20, 1) * 100 * 0.15 +
               ema_up * 100 * 0.35 +
               up_ratio * 100 * 0.25 +
               min(hh / 9, 1) * 100 * 0.15 +
               min(hl / 9, 1) * 100 * 0.10)

    # ===== ④ Drawdown Quality (20%) =====
    lookback = min(n, 40)
    if lookback >= 10:
        prices = price.values[-lookback:]
        running_max = np.maximum.accumulate(prices)
        dd = (running_max - prices) / running_max
        max_dd = dd.max()
        recovery = 0
        if max_dd > 0:
            valley = prices[np.argmax(dd)]
            peak = running_max[-1]
            recovery = (prices[-1] - valley) / (peak - valley) if peak > valley else 1
        dd_score = np.clip((1 - max_dd) * 70 + recovery * 30, 0, 100)
    else:
        dd_score = 50

    final = mom_pct * 0.35 + ma_score * 0.20 + persist * 0.25 + dd_score * 0.20
    return float(np.clip(final, 0, 100))
