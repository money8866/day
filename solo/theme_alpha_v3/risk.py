#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V3.0 - 风险评分模块
"""
import os
import sys
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
sys.path.insert(0, PROJECT_ROOT)

def calculate_risk_score(daily_df, theme_stocks, dc_hot_df=None):
    """计算风险评分（越高风险越大）"""
    if daily_df.empty or not theme_stocks:
        return 50
    
    df = daily_df[daily_df['ts_code'].isin(theme_stocks)].copy()
    if df.empty:
        return 50
    
    df = df.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
    
    # 计算主题平均收益序列
    theme_avg = df.groupby('trade_date').agg({
        'pct_chg': 'mean',
        'close': 'mean',
        'high': 'mean',
        'low': 'mean'
    }).reset_index().sort_values('trade_date')
    
    if len(theme_avg) < 10:
        return 50
    
    risk_score = 0
    
    # ==================== ① 近10日波动率 ====================
    pct_arr = theme_avg['pct_chg'].values[-10:]
    vol = np.std(pct_arr) * 100
    volatility_score = min(100, max(0, vol * 3))
    
    # ==================== ② 振幅 ====================
    high_vals = theme_avg['high'].values[-10:]
    low_vals = theme_avg['low'].values[-10:]
    avg_amplitude = (np.mean(high_vals - low_vals) / theme_avg['close'].values[-10]) * 100
    amplitude_score = min(100, max(0, avg_amplitude * 4))
    
    # ==================== ③ 换手率 ====================
    turnover = 0
    latest_df = df.groupby('ts_code').last().reset_index()
    if 'turnover_rate' in latest_df.columns:
        turnover = latest_df['turnover_rate'].mean()
    turnover_score = min(100, max(0, turnover * 3))
    
    # ==================== ④ 连续大涨 ====================
    consecutive_risk = 0
    pct_all = theme_avg['pct_chg'].values
    big_up_days = sum(1 for x in pct_all[-10:] if x > 5)
    consecutive_risk = min(100, big_up_days * 15)
    
    # ==================== ⑤ 热度过高 ====================
    heat_risk = 0
    if dc_hot_df is not None and not dc_hot_df.empty:
        theme_hot = dc_hot_df[dc_hot_df['ts_code'].isin(theme_stocks)]
        if not theme_hot.empty:
            avg_hot = theme_hot['hot_value'].mean() if 'hot_value' in theme_hot_df.columns else 0
            heat_risk = min(100, avg_hot * 2)
    
    # ==================== 组合 ====================
    risk_score = (
        volatility_score * 0.25 +
        amplitude_score * 0.20 +
        turnover_score * 0.20 +
        consecutive_risk * 0.20 +
        heat_risk * 0.15
    )
    
    return max(0, min(100, risk_score))

if __name__ == "__main__":
    print("[Risk] 风险模块加载完成")
