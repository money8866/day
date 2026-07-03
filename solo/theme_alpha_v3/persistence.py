#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V3.0 - 持续性评分模块
"""
import os
import sys
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
sys.path.insert(0, PROJECT_ROOT)

def calculate_persistence_score(daily_df, theme_stocks):
    """计算持续性评分"""
    if daily_df.empty or not theme_stocks:
        return 0
    
    df = daily_df[daily_df['ts_code'].isin(theme_stocks)].copy()
    if df.empty:
        return 0
    
    df = df.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
    
    # 计算主题平均收益序列
    theme_avg = df.groupby('trade_date').agg({
        'pct_chg': 'mean',
        'close': 'mean'
    }).reset_index().sort_values('trade_date')
    
    if len(theme_avg) < 20:
        return 50
    
    # ==================== 连续上涨天数 ====================
    pct_arr = theme_avg['pct_chg'].values
    consecutive_up = 0
    for i in range(len(pct_arr)-1, -1, -1):
        if pct_arr[i] > 0:
            consecutive_up += 1
        else:
            break
    
    consecutive_up_score = min(100, consecutive_up * 10)
    
    # ==================== EMA20持续向上 ====================
    closes = theme_avg['close'].values
    ema20 = pd.Series(closes).ewm(span=20).mean().values
    
    ema_up_streak = 0
    for i in range(len(ema20)-1, 0, -1):
        if ema20[i] > ema20[i-1]:
            ema_up_streak += 1
        else:
            break
    
    ema_up_score = min(100, ema_up_streak * 8)
    
    # ==================== 相对排名保持 ====================
    # 简化：假设排名保持稳定
    rank_score = 70
    
    # ==================== 龙头持续性 ====================
    leader_score = 60
    if len(theme_stocks) > 5:
        # 找到龙头股
        stock_returns = []
        for stock in theme_stocks:
            sdf = df[df['ts_code'] == stock]
            if len(sdf) >= 20:
                rets = sdf['close'].values
                ret20 = (rets[-1] / rets[-20]) - 1 if len(rets) > 20 else 0
                stock_returns.append((stock, ret20))
        
        if stock_returns:
            stock_returns.sort(key=lambda x: -x[1])
            leader_score = min(100, max(0, 50 + stock_returns[0][1] * 100))
    
    # ==================== 组合 ====================
    persistence_score = (
        consecutive_up_score * 0.25 +
        ema_up_score * 0.30 +
        rank_score * 0.25 +
        leader_score * 0.20
    )
    
    return max(0, min(100, persistence_score))

if __name__ == "__main__":
    print("[Persistence] 持续性模块加载完成")
