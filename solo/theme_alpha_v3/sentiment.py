#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V3.0 - 情绪评分模块
"""
import os
import sys
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
sys.path.insert(0, PROJECT_ROOT)

def calculate_sentiment_score(daily_df, limit_df, theme_stocks, index_return=0):
    """计算情绪评分"""
    if daily_df.empty or not theme_stocks:
        return 0
    
    df = daily_df[daily_df['ts_code'].isin(theme_stocks)].copy()
    if df.empty:
        return 0
    
    df = df.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
    latest_df = df.groupby('ts_code').last().reset_index()
    n_stocks = len(latest_df)
    
    # ==================== ① Breadth ====================
    up_count = sum(1 for _, row in latest_df.iterrows() if row['pct_chg'] > 0)
    breadth_score = (up_count / n_stocks) * 100 if n_stocks > 0 else 50
    
    # ==================== ② Strong Breadth ====================
    up3_count = sum(1 for _, row in latest_df.iterrows() if row['pct_chg'] > 3)
    up5_count = sum(1 for _, row in latest_df.iterrows() if row['pct_chg'] > 5)
    up8_count = sum(1 for _, row in latest_df.iterrows() if row['pct_chg'] > 8)
    
    strong_breadth_score = (
        (up3_count / n_stocks) * 40 +
        (up5_count / n_stocks) * 35 +
        (up8_count / n_stocks) * 25
    ) if n_stocks > 0 else 50
    
    # ==================== ③ Limit Up ====================
    limit_up_score = 30
    if not limit_df.empty:
        theme_limit = limit_df[limit_df['ts_code'].isin(theme_stocks)]
        limit_up_count = len(theme_limit[theme_limit['up_limit'] == 1]) if 'up_limit' in theme_limit.columns else 0
        
        limit_up_ratio = limit_up_count / n_stocks if n_stocks > 0 else 0
        limit_up_score = min(100, limit_up_ratio * 300)
    
    # ==================== ④ Heat ====================
    # 简化：用换手率作为热度
    avg_turnover = latest_df['amount'].mean() / (latest_df['close'].mean() * latest_df['vol'].mean() * 100) * 100
    heat_score = min(100, max(0, avg_turnover * 5))
    
    # ==================== ⑤ Relative Strength ====================
    theme_return = latest_df['pct_chg'].mean()
    relative_strength = min(100, max(0, 50 + (theme_return - index_return) * 50))
    
    # ==================== 组合 ====================
    sentiment_score = (
        breadth_score * 0.25 +
        strong_breadth_score * 0.25 +
        limit_up_score * 0.20 +
        heat_score * 0.15 +
        relative_strength * 0.15
    )
    
    return max(0, min(100, sentiment_score))

if __name__ == "__main__":
    print("[Sentiment] 情绪模块加载完成")
