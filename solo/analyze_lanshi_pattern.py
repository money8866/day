#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
分析蓝思科技的中军走势特征
学习其沿着均线温和小阳小阴上升后加速的模式
"""

import os
import sys
import time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# 配置路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(BASE_DIR)
sys.path.append(parent_dir)

# Patch tushare path
original_expanduser = os.path.expanduser
safe_cache_dir = os.path.join(BASE_DIR, 'cache_backbone_tushare')
os.makedirs(safe_cache_dir, exist_ok=True)

def safe_expanduser(path):
    if '~/tk.csv' in path or '\\tk.csv' in path or 'tk.csv' in path:
        return os.path.join(safe_cache_dir, 'tk.csv')
    return original_expanduser(path)

os.path.expanduser = safe_expanduser

import tushare as ts
from dotenv import load_dotenv

env_path = os.path.join(parent_dir, "config", ".env")
load_dotenv(env_path)
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

def get_stock_kline(ts_code, start_date, end_date):
    """获取股票K线数据"""
    df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    time.sleep(0.1)
    return df

def analyze_lanshi_trend():
    """分析蓝思科技走势"""
    print("=" * 80)
    print("分析蓝思科技(300433.SZ)的中军走势特征")
    print("=" * 80)
    
    # 获取最近60个交易日的数据
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=90)).strftime('%Y%m%d')
    
    df = get_stock_kline('300433.SZ', start_date, end_date)
    df = df.sort_values('trade_date')
    
    print(f"\n数据区间: {df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}")
    print(f"总交易日数: {len(df)}")
    
    # 计算均线
    df['ma5'] = df['close'].rolling(5).mean()
    df['ma10'] = df['close'].rolling(10).mean()
    df['ma20'] = df['close'].rolling(20).mean()
    df['ma60'] = df['close'].rolling(60).mean()
    
    # 计算成交量均线
    df['vol_ma5'] = df['vol'].rolling(5).mean()
    df['vol_ma10'] = df['vol'].rolling(10).mean()
    
    # 计算近期涨幅
    df['pct_chg'] = df['close'].pct_change() * 100
    
    # 打印最近20天的数据
    print("\n" + "=" * 80)
    print("最近20个交易日数据:")
    print("=" * 80)
    print(f"{'日期':<10}{'收盘':<8}{'涨跌%':<8}{'MA5':<8}{'MA10':<8}{'MA20':<8}{'量比':<8}{'K线形态':<15}")
    print("-" * 80)
    
    recent_df = df.tail(20).copy()
    
    for idx, row in recent_df.iterrows():
        date = row['trade_date']
        close = row['close']
        pct_chg = row['pct_chg']
        ma5 = row['ma5']
        ma10 = row['ma10']
        ma20 = row['ma20']
        
        # 计算量比（当日成交量/前5日均量）
        vol_ma5 = row['vol_ma5']
        vol_ratio = row['vol'] / vol_ma5 if vol_ma5 > 0 else 0
        
        # 判断K线形态
        pattern = ""
        pct = pct_chg if not pd.isna(pct_chg) else 0
        
        if pct > 7:
            pattern = "涨停"
        elif pct > 4:
            pattern = "大阳"
        elif pct > 2:
            pattern = "中阳"
        elif pct > 0:
            pattern = "小阳"
        elif pct == 0:
            pattern = "平盘"
        elif pct > -2:
            pattern = "小阴"
        elif pct > -5:
            pattern = "中阴"
        else:
            pattern = "大阴"
        
        # 判断均线状态
        if not pd.isna(ma5) and not pd.isna(ma10) and not pd.isna(ma20):
            if ma5 > ma10 > ma20:
                pattern += "-多头"
            elif ma10 > ma5 > ma20:
                pattern += "-上穿"
            elif ma5 < ma10 < ma20:
                pattern += "-空头"
            elif ma10 < ma5 < ma20:
                pattern += "-下穿"
            else:
                pattern += "-缠绕"
        
        print(f"{date:<10}{close:<8.2f}{pct:<8.2f}{ma5:<8.2f}{ma10:<8.2f}{ma20:<8.2f}{vol_ratio:<8.2f}{pattern:<15}")
    
    print("\n" + "=" * 80)
    print("走势特征分析:")
    print("=" * 80)
    
    # 分析最近5天的走势
    last_5 = df.tail(5)
    last_10 = df.tail(10)
    
    # 计算涨幅
    last_5_chg = (last_5['close'].iloc[-1] / last_5['close'].iloc[0] - 1) * 100
    last_10_chg = (last_10['close'].iloc[-1] / last_10['close'].iloc[0] - 1) * 100
    
    print(f"\n最近5日涨幅: {last_5_chg:.2f}%")
    print(f"最近10日涨幅: {last_10_chg:.2f}%")
    
    # 分析均线状态
    latest = df.iloc[-1]
    ma5, ma10, ma20 = latest['ma5'], latest['ma10'], latest['ma20']
    
    print(f"\n当前均线状态:")
    print(f"  MA5: {ma5:.2f}")
    print(f"  MA10: {ma10:.2f}")
    print(f"  MA20: {ma20:.2f}")
    
    if ma5 > ma10 > ma20:
        print(f"  → 多头排列（强势）")
    elif ma10 > ma5 > ma20:
        print(f"  → MA5上穿MA20（加速信号）")
    else:
        print(f"  → 均线缠绕（整理阶段）")
    
    # 计算斜率（均线向上/向下）
    if len(df) >= 20:
        ma20_slope = (df['ma20'].iloc[-1] / df['ma20'].iloc[-10] - 1) * 100
        ma10_slope = (df['ma10'].iloc[-1] / df['ma10'].iloc[-10] - 1) * 100
        print(f"\n均线斜率（10日变化）:")
        print(f"  MA20: {ma20_slope:+.2f}%")
        print(f"  MA10: {ma10_slope:+.2f}%")
    
    # 分析量价配合
    avg_vol = df['vol'].tail(20).mean()
    recent_vol = df['vol'].tail(5).mean()
    vol_ratio = recent_vol / avg_vol
    print(f"\n量能分析:")
    print(f"  近5日均量/20日均量: {vol_ratio:.2f}")
    if vol_ratio > 1.5:
        print(f"  → 明显放量（可能有主力介入）")
    elif vol_ratio > 1.1:
        print(f"  →温和放量")
    else:
        print(f"  → 量能平稳")
    
    print("\n" + "=" * 80)
    print("关键形态识别:")
    print("=" * 80)
    
    # 识别"均线缠绕后加速"形态
    df_check = df.tail(30).copy()
    
    # 检查前20天是否均线缠绕
    ma_diff_20 = abs(df_check['ma5'].iloc[-21] - df_check['ma20'].iloc[-21]) / df_check['ma20'].iloc[-21]
    # 检查最近5天是否加速
    ma_diff_5 = abs(df_check['ma5'].iloc[-1] - df_check['ma20'].iloc[-1]) / df_check['ma20'].iloc[-1]
    
    print(f"\n均线收敛度（前20天）: {ma_diff_20:.2%}")
    print(f"均线发散度（最近5天）: {ma_diff_5:.2%}")
    
    if ma_diff_20 < 0.03 and ma_diff_5 > 0.05:
        print(f"  → 识别为'均线收敛后加速'形态！")
    elif ma_diff_5 > 0.1:
        print(f"  → 已经进入加速阶段")
    else:
        print(f"  → 仍处于均线收敛或缓慢上升阶段")
    
    # 检查是否有"小阳小阴上升"阶段
    print(f"\n近期K线特征:")
    small_up_days = 0
    for i in range(-5, 0):
        pct = df_check['pct_chg'].iloc[i]
        if 0 < pct < 3:
            small_up_days += 1
    
    print(f"  近5日小阳（0-3%）天数: {small_up_days}天")
    
    if small_up_days >= 3:
        print(f"  → 识别为'温和小阳上升'阶段")
    
    return df

def find_similar_pattern_stocks():
    """找出类似蓝思科技走势的股票"""
    print("\n" + "=" * 80)
    print("寻找类似走势的股票")
    print("=" * 80)
    
    # 获取电力链和煤炭链的成份股
    sys.path.append(BASE_DIR)
    try:
        import theme_trend_sentiment_score as theme_score
        
        hot_themes = theme_score.load_theme_json()
        dc_df = theme_score.get_dc_members()
        stock_basic = theme_score.get_stock_basic()
        
        theme_stock_map, _, _, _ = theme_score.match_theme_stocks(
            hot_themes, dc_df, stock_basic
        )
        
        # 获取电力链和煤炭链的股票
        target_themes = ['电力链', '煤炭链']
        all_codes = []
        for theme in target_themes:
            if theme in theme_stock_map:
                codes = list(theme_stock_map[theme].keys())
                all_codes.extend(codes)
        
        all_codes = list(set(all_codes))[:50]  # 限制数量
        
        print(f"\n分析 {len(all_codes)} 只股票...")
        
        # 获取这些股票的K线数据
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=90)).strftime('%Y%m%d')
        
        similar_stocks = []
        
        for code in all_codes[:30]:
            try:
                df = pro.daily(ts_code=code, start_date=start_date, end_date=end_date)
                time.sleep(0.05)
                
                if len(df) < 30:
                    continue
                
                df = df.sort_values('trade_date')
                
                # 计算均线
                df['ma5'] = df['close'].rolling(5).mean()
                df['ma10'] = df['close'].rolling(10).mean()
                df['ma20'] = df['close'].rolling(20).mean()
                df['ma60'] = df['close'].rolling(60).mean()
                
                # 计算涨幅
                df['pct_chg'] = df['close'].pct_change() * 100
                
                # 检查均线收敛度
                ma_diff = abs(df['ma5'].iloc[-1] - df['ma20'].iloc[-1]) / df['ma20'].iloc[-1]
                
                # 检查均线发散趋势（加速信号）
                ma_diff_prev = abs(df['ma5'].iloc[-5] - df['ma20'].iloc[-5]) / df['ma20'].iloc[-5]
                
                # 检查近期是否有温和小阳上升
                recent_5 = df.tail(5)
                small_up_days = sum(1 for p in recent_5['pct_chg'] if 0 < p < 3)
                
                # 检查是否有加速迹象
                acceleration = ma_diff - ma_diff_prev
                
                # 综合评分
                score = 0
                if ma_diff > 0.05:  # 均线开始发散
                    score += 30
                if acceleration > 0.02:  # 加速明显
                    score += 30
                if small_up_days >= 3:  # 温和小阳
                    score += 20
                if df['ma5'].iloc[-1] > df['ma10'].iloc[-1]:  # 短期均线在上
                    score += 10
                if df['pct_chg'].iloc[-1] > 0:  # 今日上涨
                    score += 10
                
                if score >= 50:
                    similar_stocks.append({
                        'code': code,
                        'score': score,
                        'ma_diff': ma_diff,
                        'acceleration': acceleration,
                        'small_up_days': small_up_days,
                        'close': df['close'].iloc[-1],
                        'pct_chg': df['pct_chg'].iloc[-1]
                    })
                    
            except Exception as e:
                continue
        
        # 排序并输出
        similar_stocks.sort(key=lambda x: x['score'], reverse=True)
        
        print(f"\n找到 {len(similar_stocks)} 只类似走势的股票:")
        print("-" * 80)
        print(f"{'代码':<12}{'评分':<6}{'均线发散':<10}{'加速':<10}{'小阳天':<8}{'收盘':<8}{'涨跌%'}")
        print("-" * 80)
        
        for stock in similar_stocks[:10]:
            print(f"{stock['code']:<12}{stock['score']:<6}{stock['ma_diff']:<10.2%}{stock['acceleration']:<10.2%}"
                  f"{stock['small_up_days']:<8}{stock['close']:<8.2f}{stock['pct_chg']:<8.2f}")
        
        return similar_stocks
        
    except Exception as e:
        print(f"分析失败: {e}")
        return []

if __name__ == '__main__':
    # 分析蓝思科技走势
    lanshi_df = analyze_lanshi_trend()
    
    # 寻找类似走势的股票
    similar = find_similar_pattern_stocks()
    
    print("\n" + "=" * 80)
    print("分析完成！")
    print("=" * 80)
