#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V6.0 - 资金评分模块

三维合成：
 ① 成交额占市场比例 (40%)
 ② 成交额20日趋势斜率 (35%)
 ③ Moneyflow 资金流净流入 (25%)
"""
import os, sys, warnings
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
warnings.filterwarnings("ignore")


def compute_capital_score(daily, moneyflow, codes, market_turnover=0):
    """返回 0-100 资金评分"""
    sub = daily[daily["ts_code"].isin(codes)].copy()
    if sub.empty:
        return 50.0

    latest_day = sub["trade_date"].max()

    # ===== ① 成交额占市场比例 (40%) =====
    latest = sub[sub["trade_date"] == latest_day]
    theme_amt = latest["amount"].sum() / 1e8  # 亿元
    if market_turnover > 0:
        ratio = theme_amt / market_turnover
        # 占比 0.5% → 50分，1% → 80分，2%+ → 100分
        turnover_score = np.clip(ratio * 4000, 0, 100)
    else:
        turnover_score = 50

    # ===== ② 成交额20日趋势 (35%) =====
    daily_amt = sub.groupby("trade_date")["amount"].sum()
    if len(daily_amt) >= 20:
        recent = daily_amt.values[-20:].astype(float)
        x = np.arange(20)
        slope = np.polyfit(x, recent, 1)[0]
        avg = np.mean(recent)
        if avg > 0:
            slope_pct = slope / avg * 100
            # 正斜率 1%/天 → 80分
            slope_score = np.clip(50 + slope_pct * 30, 0, 100)
        else:
            slope_score = 50
    else:
        slope_score = 50

    # ===== ③ Moneyflow (25%) =====
    mf_score = 50
    if moneyflow is not None and not moneyflow.empty:
        mf_sub = moneyflow[moneyflow["ts_code"].isin(codes)]
        if not mf_sub.empty and "net_mf_amount" in mf_sub.columns:
            # 最近5日净流入
            mf_dates = sorted(mf_sub["trade_date"].unique())
            recent_5d = mf_dates[-5:] if len(mf_dates) >= 5 else mf_dates
            mf_recent = mf_sub[mf_sub["trade_date"].isin(recent_5d)]
            net_5d = mf_recent["net_mf_amount"].sum() / 1e8  # 亿元

            # 最近20日净流入
            recent_20d = mf_dates[-20:] if len(mf_dates) >= 20 else mf_dates
            mf_20d = mf_sub[mf_sub["trade_date"].isin(recent_20d)]
            net_20d = mf_20d["net_mf_amount"].sum() / 1e8

            # 超大单+大单净买入
            buy_cols = [c for c in ["elarg_buy_amount", "large_buy_amount"] if c in mf_sub.columns]
            sell_cols = [c for c in ["elarg_sell_amount", "large_sell_amount"] if c in mf_sub.columns]
            big_buy = mf_recent[buy_cols].sum().sum() / 1e8 if buy_cols else 0
            big_sell = mf_recent[sell_cols].sum().sum() / 1e8 if sell_cols else 0
            big_net = big_buy - big_sell

            # 综合：5日净流入权重60%，20日权重40%
            combined = net_5d * 0.6 + net_20d * 0.4
            mf_score = np.clip(50 + combined * 3, 0, 100)

    final = turnover_score * 0.40 + slope_score * 0.35 + mf_score * 0.25
    return float(np.clip(final, 0, 100))
