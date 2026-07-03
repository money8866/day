#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V5.0 - 情绪评分模块

五个子维度：
 ① Breadth (25%)         — 上涨家数占比
 ② Strong Breadth (25%)  — 涨幅>3%/5%/8% 的股票占比
 ③ Limit Up (20%)        — 涨停数、炸板率趋近封板率
 ④ Heat (15%)            — 平均热度
 ⑤ Relative Strength (15%) — 主题平均收益 - 沪深300收益
"""
import os, sys, warnings
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BASE_DIR))
warnings.filterwarnings("ignore")


def compute_sentiment_score(daily: pd.DataFrame, limit_df: pd.DataFrame,
                            theme_codes: list, index_return: float = 0) -> float:
    """返回 0-100 的情绪评分"""
    sub = daily[daily["ts_code"].isin(theme_codes)].copy()
    if sub.empty or len(theme_codes) < 3:
        return 50.0

    latest_day = sub["trade_date"].max()
    latest = sub[sub["trade_date"] == latest_day]
    n_stocks = len(latest)

    # ===== ① Breadth (25%) =====
    up_count = (latest["pct_chg"] > 0).sum()
    breadth_score = (up_count / n_stocks) * 100 if n_stocks > 0 else 50

    # ===== ② Strong Breadth (25%) =====
    strong_up = latest["pct_chg"].values
    up3 = np.sum(strong_up > 3) / n_stocks * 40
    up5 = np.sum(strong_up > 5) / n_stocks * 35
    up8 = np.sum(strong_up > 8) / n_stocks * 25
    strong_breadth_score = up3 + up5 + up8

    # ===== ③ Limit Up (20%) =====
    limit_score = 30
    if not limit_df.empty:
        theme_limit = limit_df[limit_df["ts_code"].isin(theme_codes)]
        lu_cnt = len(theme_limit)
        # 涨停强度 = 涨停数 / 主题股票数
        lu_ratio = lu_cnt / n_stocks if n_stocks > 0 else 0
        limit_score = np.clip(lu_ratio * 500, 0, 100)

    # ===== ④ Heat (15%) =====
    # 使用换手率代理热度
    if "turnover_rate" in latest.columns:
        avg_turnover = latest["turnover_rate"].mean()
        heat_score = np.clip(avg_turnover * 5, 0, 100)
    else:
        # 使用成交量比
        avg_vol_ratio = latest["vol"].mean() / (latest["vol"].mean() + 1)
        heat_score = np.clip(avg_vol_ratio * 50, 0, 100)

    # ===== ⑤ Relative Strength (15%) =====
    theme_ret = latest["pct_chg"].mean() if n_stocks > 0 else 0
    rs_score = np.clip(50 + (theme_ret - index_return) * 50, 0, 100)

    final = (breadth_score * 0.25 + strong_breadth_score * 0.25 +
             limit_score * 0.20 + heat_score * 0.15 + rs_score * 0.15)
    return float(np.clip(final, 0, 100))


if __name__ == "__main__":
    print("[Sentiment] 情绪评分模块加载完成")
