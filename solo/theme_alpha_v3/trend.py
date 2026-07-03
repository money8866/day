#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V3.0 - 趋势评分模块
"""
import os
import sys
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
sys.path.insert(0, PROJECT_ROOT)

def calculate_trend_score(daily_df, theme_stocks):
    """计算趋势评分"""
    if daily_df.empty or not theme_stocks:
        return 0
    
    df = daily_df[daily_df['ts_code'].isin(theme_stocks)].copy()
    if df.empty:
        return 0
    
    df = df.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
    
    # 计算每个股票的收益率
    df['pct_chg'] = df.groupby('ts_code')['close'].pct_change()
    
    # 计算主题平均收益
    theme_avg = df.groupby('trade_date').agg({
        'pct_chg': 'mean',
        'close': 'mean'
    }).reset_index().sort_values('trade_date')
    
    if len(theme_avg) < 60:
        return 0
    
    # ==================== ① Relative Momentum ====================
    close_vals = theme_avg['close'].values
    total_len = len(close_vals)
    
    returns = {}
    for period in [5, 10, 20, 40]:
        if total_len > period:
            returns[period] = (close_vals[-1] / close_vals[-period-1]) - 1
        else:
            returns[period] = 0
    
    # 简化版：直接组合，不需要全主题百分位
    momentum_score = (
        0.25 * min(100, max(0, returns.get(5, 0) * 500)) +
        0.30 * min(100, max(0, returns.get(10, 0) * 300)) +
        0.25 * min(100, max(0, returns.get(20, 0) * 200)) +
        0.20 * min(100, max(0, returns.get(40, 0) * 100))
    )
    
    # ==================== ② MA Trend ====================
    ma_scores = []
    for stock in theme_stocks:
        stock_df = df[df['ts_code'] == stock]
        if len(stock_df) < 60:
            continue
        
        closes = stock_df['close'].values
        ma5 = pd.Series(closes).rolling(5).mean().values
        ma10 = pd.Series(closes).rolling(10).mean().values
        ma20 = pd.Series(closes).rolling(20).mean().values
        ma60 = pd.Series(closes).rolling(60).mean().values
        
        last_price = closes[-1]
        ma5_score = 1 if last_price > ma5[-1] else 0
        ma10_score = 1 if last_price > ma10[-1] else 0
        ma20_score = 1 if last_price > ma20[-1] else 0
        ma60_score = 1 if last_price > ma60[-1] else 0
        
        ma_scores.append({
            'ma5': ma5_score,
            'ma10': ma10_score,
            'ma20': ma20_score,
            'ma60': ma60_score
        })
    
    if ma_scores:
        ma_df = pd.DataFrame(ma_scores)
        ma_breadth_score = (
            ma_df['ma5'].mean() * 25 +
            ma_df['ma10'].mean() * 25 +
            ma_df['ma20'].mean() * 25 +
            ma_df['ma60'].mean() * 25
        )
    else:
        ma_breadth_score = 50
    
    # ==================== ③ Trend Persistence ====================
    persistence_score = 0
    if len(theme_avg) >= 20:
        closes_avg = theme_avg['close'].values
        ema20 = pd.Series(closes_avg).ewm(span=20).mean().values
        
        # 连续创新高
        new_high_days = 0
        for i in range(max(0, len(closes_avg)-20), len(closes_avg)):
            if closes_avg[i] >= max(closes_avg[:i+1]):
                new_high_days += 1
        
        # EMA20向上天数
        ema_up_days = 0
        for i in range(max(1, len(ema20)-20), len(ema20)):
            if ema20[i] > ema20[i-1]:
                ema_up_days += 1
        
        # 过去20日上涨天数
        pct_arr = theme_avg['pct_chg'].values[-20:]
        up_days = sum(1 for x in pct_arr if x > 0)
        
        persistence_score = (
            min(100, new_high_days * 5) * 0.3 +
            min(100, ema_up_days * 5) * 0.3 +
            min(100, up_days * 5) * 0.4
        )
    
    # ==================== ④ Drawdown Quality ====================
    drawdown_score = 0
    if len(theme_avg) >= 20:
        closes_recent = theme_avg['close'].values[-40:]
        
        max_val = max(closes_recent)
        idx_max = np.argmax(closes_recent)
        
        min_after = min(closes_recent[idx_max:])
        max_drawdown = (max_val - min_after) / max_val
        
        recovery_ratio = (closes_recent[-1] - min_after) / (max_val - min_after) if max_val > min_after else 1
        
        drawdown_score = (
            max(0, 100 - max_drawdown * 300) * 0.5 +
            recovery_ratio * 100 * 0.5
        )
    
    # ==================== 组合 ====================
    trend_score = (
        momentum_score * 0.35 +
        ma_breadth_score * 0.20 +
        persistence_score * 0.25 +
        drawdown_score * 0.20
    )
    
    return max(0, min(100, trend_score))

if __name__ == "__main__":
    print("[Trend] 趋势模块加载完成")
