#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V6.0 - 情绪评分模块

五维合成：
 ① Breadth (25%)         — 上涨家数占比
 ② Strong Breadth (25%)  — 涨幅>3%/5%/8% 的股票占比
 ③ Limit Up (20%)        — 涨停数/封板成功率 (limit_list_d)
 ④ Heat (15%)            — DC人气榜平均热度及5日趋势
 ⑤ Relative Strength (15%) — 主题收益 - 沪深300收益
"""
import os, sys, warnings
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
warnings.filterwarnings("ignore")


def compute_sentiment_score(daily, limit_df, dc_hot_df, codes, index_return=0):
    """返回 0-100 情绪评分"""
    sub = daily[daily["ts_code"].isin(codes)].copy()
    if sub.empty or len(codes) < 3:
        return 50.0

    latest_day = sub["trade_date"].max()
    latest = sub[sub["trade_date"] == latest_day]
    n = len(latest)
    pct_arr = latest["pct_chg"].values

    # ===== ① Breadth (25%) =====
    up_count = np.sum(pct_arr > 0)
    breadth = (up_count / n) * 100 if n > 0 else 50

    # ===== ② Strong Breadth (25%) =====
    up3 = np.sum(pct_arr > 3) / n * 40
    up5 = np.sum(pct_arr > 5) / n * 35
    up8 = np.sum(pct_arr > 8) / n * 25
    strong = up3 + up5 + up8

    # ===== ③ Limit Up (20%) =====
    limit_score = 30
    if limit_df is not None and not limit_df.empty:
        # 兼容不同列名
        code_col = "ts_code" if "ts_code" in limit_df.columns else None
        if code_col:
            theme_limit = limit_df[limit_df[code_col].isin(codes)]
            lu_count = len(theme_limit)
            lu_ratio = lu_count / n if n > 0 else 0
            # 涨停数越多得分越高：1%涨停 → 50分，5% → 100分
            limit_score = np.clip(lu_ratio * 1000, 0, 100)

            # 封板成功率（如有）
            if "up_limit" in theme_limit.columns:
                success = theme_limit["up_limit"].sum()
                limit_score = np.clip((success / n) * 1000, 0, 100) if n > 0 else 30

    # ===== ④ Heat (15%) =====
    heat_score = 50
    if dc_hot_df is not None and not dc_hot_df.empty:
        code_col = "ts_code" if "ts_code" in dc_hot_df.columns else "code"
        if code_col in dc_hot_df.columns:
            theme_hot = dc_hot_df[dc_hot_df[code_col].isin(codes)]
            if not theme_hot.empty:
                # 热度值列名兼容
                hot_col = "hot_value" if "hot_value" in theme_hot.columns else "hot"
                if hot_col in theme_hot.columns:
                    avg_hot = theme_hot[hot_col].mean()
                    # 排名越靠前(数字越小)热度越高
                    rank_col = "hot_rank" if "hot_rank" in theme_hot.columns else None
                    if rank_col:
                        avg_rank = theme_hot[rank_col].mean()
                        # 排名 1 → 100分, 排名 2000 → 0分
                        heat_score = np.clip(100 - avg_rank / 20, 0, 100)
                    else:
                        heat_score = np.clip(avg_hot / 100, 0, 100)

    # ===== ⑤ Relative Strength (15%) =====
    theme_ret = latest["pct_chg"].mean() if n > 0 else 0
    rs = np.clip(50 + (theme_ret - index_return) * 50, 0, 100)

    final = breadth * 0.25 + strong * 0.25 + limit_score * 0.20 + heat_score * 0.15 + rs * 0.15
    return float(np.clip(final, 0, 100))
