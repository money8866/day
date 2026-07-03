#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V6.0 - 风险评分模块

风险越高分越高 (0-100)
包含：波动率、振幅、换手率、连续大涨、拥挤度、龙虎榜异常
"""
import os, sys, warnings
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
warnings.filterwarnings("ignore")


def compute_risk_score(daily, codes, daily_basic=None, top_df=None):
    """返回 0-100 风险评分"""
    sub = daily[daily["ts_code"].isin(codes)].copy()
    if sub.empty:
        return 50.0

    ret = sub.groupby("trade_date")["pct_chg"].mean().sort_index()
    pct = ret.values
    n = len(pct)

    # ① 波动率 (25%)
    vol_score = np.clip(np.std(pct[-10:]) * 500, 0, 100) if n >= 10 else 50

    # ② 振幅 (20%)
    hi = sub.groupby("trade_date")["high"].mean()
    lo = sub.groupby("trade_date")["low"].mean()
    cl = sub.groupby("trade_date")["close"].mean()
    if len(hi) >= 10:
        amp = ((hi.values[-10:] - lo.values[-10:]) / cl.values[-10:]).mean() * 100
        amp_score = np.clip(amp * 6, 0, 100)
    else:
        amp_score = 50

    # ③ 换手率 (20%)
    tr_score = 50
    if daily_basic is not None and not daily_basic.empty:
        theme_basic = daily_basic[daily_basic["ts_code"].isin(codes)]
        if not theme_basic.empty and "turnover_rate" in theme_basic.columns:
            tr = theme_basic["turnover_rate"].mean()
            tr_score = np.clip(tr * 3, 0, 100)

    # ④ 连续大涨 (20%)
    big = np.sum(pct[-10:] > 5) if n >= 10 else 0
    surge_score = np.clip(big * 15, 0, 100)

    # ⑤ 拥挤度 (15%)
    latest_day = sub["trade_date"].max()
    latest = sub[sub["trade_date"] == latest_day]
    theme_amt = latest["amount"].sum()
    total_amt = sub[sub["trade_date"] == latest_day]["amount"].sum()
    crowd = theme_amt / (total_amt + 1e8)
    crowd_score = np.clip(crowd * 5, 0, 100)

    final = vol_score * 0.25 + amp_score * 0.20 + tr_score * 0.20 + \
            surge_score * 0.20 + crowd_score * 0.15
    return float(np.clip(final, 0, 100))
