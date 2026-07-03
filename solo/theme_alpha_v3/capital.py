#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V3.0 - 资金评分模块
"""
import os
import sys
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
sys.path.insert(0, PROJECT_ROOT)

def calculate_capital_score(daily_df, moneyflow_df, theme_stocks, all_market_turnover=0):
    """计算资金评分"""
    if daily_df.empty or not theme_stocks:
        return 0
    
    df = daily_df[daily_df['ts_code'].isin(theme_stocks)].copy()
    if df.empty:
        return 0
    
    # ==================== ① 成交额占比 ====================
    theme_turnover = df['amount'].sum() / 100000000  # 亿元
    
    turnover_ratio = 0
    if all_market_turnover > 0:
        turnover_ratio = min(100, theme_turnover / all_market_turnover * 1000)
    
    # ==================== ② 成交额趋势 ====================
    turnover_trend = 50
    df_sorted = df.sort_values(['ts_code', 'trade_date'])
    df_sorted['amount_ma20'] = df_sorted.groupby('ts_code')['amount'].rolling(20).mean().reset_index(0, drop=True)
    df_sorted['amount_ma5'] = df_sorted.groupby('ts_code')['amount'].rolling(5).mean().reset_index(0, drop=True)
    
    latest = df_sorted.groupby('ts_code').last().reset_index()
    avg_ma20 = latest['amount_ma20'].mean()
    avg_ma5 = latest['amount_ma5'].mean()
    
    if avg_ma20 > 0:
        trend_slope = (avg_ma5 / avg_ma20 - 1) * 100
        turnover_trend = 50 + trend_slope * 2
        turnover_trend = max(0, min(100, turnover_trend))
    
    # ==================== ③ 资金流 ====================
    moneyflow_score = 50
    if not moneyflow_df.empty:
        mf_theme = moneyflow_df[moneyflow_df['ts_code'].isin(theme_stocks)].copy()
        if not mf_theme.empty:
            mf_theme = mf_theme.sort_values(['ts_code', 'trade_date'])
            
            # 近5日/20日净流入
            mf_latest = mf_theme.groupby('ts_code').last().reset_index()
            net_inflow = mf_latest['net_mf_amount'].sum() / 100000000  # 亿元
            
            # 超大单 + 大单
            buy_money = (
                mf_latest['large_buy_amount'].sum() +
                mf_latest['elarg_buy_amount'].sum() if 'elarg_buy_amount' in mf_latest.columns else 0
            ) / 100000000
            
            moneyflow_score = min(100, max(0, 50 + net_inflow * 2))
    
    # ==================== 组合 ====================
    capital_score = (
        turnover_ratio * 0.40 +
        turnover_trend * 0.35 +
        moneyflow_score * 0.25
    )
    
    return max(0, min(100, capital_score))

if __name__ == "__main__":
    print("[Capital] 资金模块加载完成")
