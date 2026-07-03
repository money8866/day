#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V5.0 - 资金评分模块

三个子维度：
 ① 成交额占市场比例 (40%)
 ② 成交额 20 日趋势斜率 (35%)
 ③ 资金流净流入 (25%)
"""
import os, sys, warnings
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BASE_DIR))
warnings.filterwarnings("ignore")


def compute_capital_score(daily: pd.DataFrame, moneyflow: pd.DataFrame,
                          theme_codes: list, market_turnover: float = 0) -> float:
    """返回 0-100 的资金评分"""
    sub = daily[daily["ts_code"].isin(theme_codes)].copy()
    if sub.empty:
        return 50.0

    # ===== ① 成交额占比 (40%) =====
    latest_day = sub["trade_date"].max()
    latest_sub = sub[sub["trade_date"] == latest_day]
    theme_amt = latest_sub["amount"].sum() / 1e8  # 亿元
    ratio_pct = (theme_amt / market_turnover * 100) if market_turnover > 0 else 0.1
    turnover_ratio_score = np.clip(ratio_pct * 100, 0, 100)

    # ===== ② 成交额 20 日趋势 (35%) =====
    daily_amt = sub.groupby("trade_date")["amount"].sum()
    if len(daily_amt) >= 20:
        recent = daily_amt.values[-20:].astype(float)
        x = np.arange(20)
        slope = np.polyfit(x, recent, 1)[0]
        # 正面斜率映射到 50-100，负面到 0-50
        if slope > 0:
            slope_score = 50 + np.clip(slope / 100000000, 0, 50)
        else:
            slope_score = 50 + np.clip(slope / 50000000, -50, 0)
    else:
        slope_score = 50

    # ===== ③ 资金流 (25%) =====
    if not moneyflow.empty:
        mf_sub = moneyflow[moneyflow["ts_code"].isin(theme_codes)]
        if not mf_sub.empty:
            # 净流入总和（亿元）
            net = mf_sub["net_mf_amount"].sum() / 1e8 if "net_mf_amount" in mf_sub.columns else 0
            mf_score = np.clip(50 + net * 5, 0, 100)
        else:
            mf_score = 50
    else:
        mf_score = 50

    final = (turnover_ratio_score * 0.40 + slope_score * 0.35 + mf_score * 0.25)
    return float(np.clip(final, 0, 100))


if __name__ == "__main__":
    print("[Capital] 资金评分模块加载完成")
