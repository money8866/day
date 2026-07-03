#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V6.0 - 持续性评分模块
"""
import os, sys, warnings
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
warnings.filterwarnings("ignore")


def compute_persistence_score(daily, codes):
    """返回 0-100 持续性评分"""
    sub = daily[daily["ts_code"].isin(codes)].copy()
    if sub.empty:
        return 50.0

    ret = sub.groupby("trade_date")["pct_chg"].mean().sort_index()
    price = sub.groupby("trade_date")["close"].mean().sort_index()
    pct = ret.values
    prc = price.values
    n = len(pct)

    # ① 连续上涨天数 (25%)
    consec = 0
    for i in range(n - 1, -1, -1):
        if pct[i] > 0:
            consec += 1
        else:
            break
    consec_score = np.clip(consec * 8, 0, 100)

    # ② EMA20 持续向上 (30%) — 放大系数 1000→3000，让明显上行趋势能拿高分
    ema_score = 50
    if n >= 20:
        x = np.arange(20)
        y = prc[-20:]
        slope = np.polyfit(x, y, 1)[0]
        ema_score = np.clip(50 + slope / prc[-1] * 3000, 0, 100)

    # ③ 相对排名保持 Top20% (25%)
    if n >= 20:
        up_days = np.sum(pct[-20:] > 0)
        rank_score = up_days / 20 * 100
    else:
        rank_score = np.sum(pct > 0) / n * 100 if n > 0 else 50

    # ④ 龙头连续强于板块 (20%)
    latest_day = sub["trade_date"].max()
    latest = sub[sub["trade_date"] == latest_day]
    top3 = latest.nlargest(3, "pct_chg")["ts_code"].tolist()
    leader_out = 0
    for code in top3:
        sd = sub[sub["ts_code"] == code].sort_values("trade_date")
        if len(sd) >= 10:
            # 过去10天中该票跑赢主题均值的比例
            sd_ret = sd["pct_chg"].values[-10:]
            theme_ret_10 = pct[-10:]
            min_len = min(len(sd_ret), len(theme_ret_10))
            out_days = np.sum(sd_ret[-min_len:] > theme_ret_10[-min_len:])
            leader_out += out_days / min_len
    leader_score = np.clip(50 + leader_out * 15, 0, 100)

    final = consec_score * 0.25 + ema_score * 0.30 + rank_score * 0.25 + leader_score * 0.20
    return float(np.clip(final, 0, 100))
