#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V3.0 - 龙头识别模块
"""
import os
import sys
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
sys.path.insert(0, PROJECT_ROOT)

def identify_leader(daily_df, top_df, theme_stocks):
    """识别龙头股"""
    if daily_df.empty or not theme_stocks:
        return None, 0
    
    df = daily_df[daily_df['ts_code'].isin(theme_stocks)].copy()
    if df.empty:
        return None, 0
    
    df = df.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
    
    scores = []
    for stock in theme_stocks:
        sdf = df[df['ts_code'] == stock]
        if len(sdf) < 20:
            continue
        
        closes = sdf['close'].values
        amounts = sdf['amount'].values
        pct_chgs = sdf['pct_chg'].values
        
        # ① Relative Strength
        ret5 = (closes[-1] / closes[-6]) - 1 if len(closes) > 5 else 0
        ret10 = (closes[-1] / closes[-11]) - 1 if len(closes) > 10 else 0
        rs_score = min(100, max(0, (ret5 * 100 + ret10 * 50) * 0.5))
        
        # ② 成交额
        avg_amount = amounts[-10:].mean() / 100000000  # 亿元
        amount_score = min(100, avg_amount * 2)
        
        # ③ 趋势
        ma5 = pd.Series(closes).rolling(5).mean().values
        ma10 = pd.Series(closes).rolling(10).mean().values
        ma20 = pd.Series(closes).rolling(20).mean().values
        
        trend_score = 0
        if closes[-1] > ma5[-1] and ma5[-1] > ma10[-1] and ma10[-1] > ma20[-1]:
            trend_score = 100
        elif closes[-1] > ma20[-1]:
            trend_score = 70
        else:
            trend_score = 40
        
        # ④ 连续强势
        consecutive_up = 0
        for i in range(len(pct_chgs)-1, max(0, len(pct_chgs)-10), -1):
            if pct_chgs[i] > 0:
                consecutive_up += 1
            else:
                break
        consecutive_score = min(100, consecutive_up * 10)
        
        # ⑤ 龙虎榜加分
        top_score = 0
        if not top_df.empty and stock in top_df['ts_code'].values:
            top_score = 30
        
        # 综合评分
        total_score = (
            rs_score * 0.30 +
            amount_score * 0.25 +
            trend_score * 0.20 +
            consecutive_score * 0.15 +
            top_score * 0.10
        )
        
        scores.append((stock, total_score))
    
    if scores:
        scores.sort(key=lambda x: -x[1])
        return scores[0][0], scores[0][1]
    
    return None, 0

if __name__ == "__main__":
    print("[Leader] 龙头识别模块加载完成")
