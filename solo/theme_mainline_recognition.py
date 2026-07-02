#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
A股主题轮动与主线识别系统

目标：从每日主题数据中识别：
1）主线主题（可中线参与）
2）强势轮动主题（短线机会）
3）情绪题材（快进快出）
4）垃圾轮动/伪主题（过滤掉）

数据来源：theme_trend_sentiment.db 中的 theme_scores 表
"""

import os
import sys
import json
import sqlite3
import warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
DB_PATH = os.path.join(CACHE_DIR, "theme_trend_sentiment.db")


def get_trade_date():
    """获取最新交易日"""
    today = datetime.now()
    for i in range(5):
        dt = today - timedelta(days=i)
        if dt.weekday() < 5:
            return dt.strftime("%Y%m%d")
    return today.strftime("%Y%m%d")


def load_theme_data(trade_date=None):
    """从数据库加载主题数据"""
    if trade_date is None:
        trade_date = get_trade_date()
    
    conn = sqlite3.connect(DB_PATH)
    
    df = pd.read_sql(f"""
        SELECT theme, trend_score, sentiment_score, up_ratio, zt_count, 
               leader_score, trade_date
        FROM theme_scores 
        WHERE trade_date = '{trade_date}'
        ORDER BY composite_score DESC
    """, conn)
    
    prev_date = get_prev_trade_date(conn, trade_date)
    
    prev_df = pd.DataFrame()
    if prev_date:
        prev_df = pd.read_sql(f"""
            SELECT theme, trend_score
            FROM theme_scores 
            WHERE trade_date = '{prev_date}'
        """, conn)
    
    conn.close()
    
    return df, prev_df, trade_date, prev_date


def get_prev_trade_date(conn, trade_date):
    """获取前一个交易日"""
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT trade_date 
        FROM theme_scores 
        WHERE trade_date < ? 
        ORDER BY trade_date DESC 
        LIMIT 1
    """, (trade_date,))
    result = cur.fetchone()
    return result[0] if result else None


def compute_derived_factors(df, prev_df):
    """计算衍生因子"""
    prev_map = dict(prev_df[['theme', 'trend_score']].values) if not prev_df.empty else {}
    
    result = []
    for _, row in df.iterrows():
        theme = row['theme']
        trend_score = float(row['trend_score'] or 0)
        sentiment_score = float(row['sentiment_score'] or 0)
        up_ratio = float(row['up_ratio'] or 0)
        breadth = up_ratio / 100.0
        limit_up_count = int(row['zt_count'] or 0)
        leader_score = float(row['leader_score'] or 0)
        
        trend_score_yesterday = float(prev_map.get(theme, trend_score))
        
        momentum = trend_score - trend_score_yesterday
        
        acceleration = momentum
        
        theme_score = (
            0.40 * trend_score
            + 0.30 * sentiment_score
            + 0.15 * acceleration
        )
        
        if limit_up_count > 0 and up_ratio > 0:
            top1_stock_contribution = min(1.0, limit_up_count / up_ratio)
        else:
            top1_stock_contribution = leader_score / 100.0 if leader_score > 0 else 0
        
        result.append({
            'theme': theme,
            'trend_score': trend_score,
            'sentiment_score': sentiment_score,
            'breadth': breadth,
            'limit_up_count': limit_up_count,
            'top1_stock_contribution': top1_stock_contribution,
            'trend_score_yesterday': trend_score_yesterday,
            'momentum': momentum,
            'acceleration': acceleration,
            'theme_score': theme_score,
        })
    
    return pd.DataFrame(result)


def classify_themes(df):
    """分类主题"""
    mainline = []
    rotation = []
    sentiment = []
    garbage = []
    
    for _, row in df.iterrows():
        theme = row['theme']
        trend_score = row['trend_score']
        sentiment_score = row['sentiment_score']
        breadth = row['breadth']
        acceleration = row['acceleration']
        momentum = row['momentum']
        top1_stock_contribution = row['top1_stock_contribution']
        theme_score = row['theme_score']
        
        reasons = []
        if trend_score < 55:
            reasons.append(f"趋势分{trend_score:.0f}<55")
        if acceleration < 0:
            reasons.append(f"加速度{acceleration:.1f}<0")
        if breadth < 0.3:
            reasons.append(f"扩散度{breadth:.2f}<0.3")
        if sentiment_score < 50:
            reasons.append(f"情绪分{sentiment_score:.0f}<50")
        if top1_stock_contribution > 0.6:
            reasons.append(f"单票驱动{top1_stock_contribution:.2f}>0.6")
        
        is_garbage = len(reasons) >= 2
        
        if is_garbage:
            garbage.append({
                'theme': theme,
                'theme_score': theme_score,
                'reasons': reasons,
            })
            continue
        
        is_mainline = (
            trend_score > 70
            and acceleration > 0
            and momentum > 0
            and breadth >= 0.5
            and top1_stock_contribution < 0.4
            and sentiment_score > 60
        )
        
        if is_mainline:
            mainline.append({
                'theme': theme,
                'theme_score': theme_score,
                'trend_score': trend_score,
                'sentiment_score': sentiment_score,
                'breadth': breadth,
                'acceleration': acceleration,
            })
            continue
        
        is_rotation = (
            trend_score > 60
            and acceleration > 0
            and breadth > 0.4
        )
        
        if is_rotation:
            rotation.append({
                'theme': theme,
                'theme_score': theme_score,
                'trend_score': trend_score,
                'breadth': breadth,
                'acceleration': acceleration,
            })
            continue
        
        is_sentiment = (
            sentiment_score > 70
            and breadth < 0.4
            and top1_stock_contribution > 0.4
        )
        
        if is_sentiment:
            sentiment.append({
                'theme': theme,
                'theme_score': theme_score,
                'sentiment_score': sentiment_score,
                'breadth': breadth,
                'top1_stock_contribution': top1_stock_contribution,
            })
            continue
        
        garbage.append({
            'theme': theme,
            'theme_score': theme_score,
            'reasons': ['未满足任何分类条件'],
        })
    
    return mainline, rotation, sentiment, garbage


def compute_final_rank_score(items):
    """计算最终排序分数"""
    for item in items:
        item['final_rank_score'] = (
            0.45 * item['theme_score']
            + 0.25 * item.get('breadth', 0) * 100
            + 0.20 * max(0, item.get('acceleration', 0))
            + 0.10 * item.get('sentiment_score', 0)
        )


def print_report(mainline, rotation, sentiment, garbage, trade_date):
    """输出报告"""
    print("=" * 80)
    print(f"📊 A股主题轮动与主线识别报告")
    print(f"📅 日期: {trade_date}")
    print("=" * 80)
    
    if mainline:
        compute_final_rank_score(mainline)
        mainline_sorted = sorted(mainline, key=lambda x: -x['final_rank_score'])
        
        print("\n【主线主题】（可中线参与）")
        print("-" * 80)
        print(f"{'排名':<4} {'主题':<15} {'主题评分':<8} {'趋势分':<6} {'情绪分':<6} {'扩散度':<6} {'加速度':<6} {'排序分':<8}")
        print("-" * 80)
        for i, item in enumerate(mainline_sorted, 1):
            print(f"{i:<4} {item['theme']:<15} {item['theme_score']:<8.1f} {item['trend_score']:<6.0f} {item['sentiment_score']:<6.0f} {item['breadth']:<6.2f} {item['acceleration']:<6.1f} {item['final_rank_score']:<8.1f}")
    
    if rotation:
        compute_final_rank_score(rotation)
        rotation_sorted = sorted(rotation, key=lambda x: -x['final_rank_score'])
        
        print("\n【强势轮动】（短线机会）")
        print("-" * 80)
        print(f"{'排名':<4} {'主题':<15} {'主题评分':<8} {'趋势分':<6} {'扩散度':<6} {'加速度':<6} {'排序分':<8}")
        print("-" * 80)
        for i, item in enumerate(rotation_sorted, 1):
            print(f"{i:<4} {item['theme']:<15} {item['theme_score']:<8.1f} {item['trend_score']:<6.0f} {item['breadth']:<6.2f} {item['acceleration']:<6.1f} {item['final_rank_score']:<8.1f}")
    
    if sentiment:
        sentiment_sorted = sorted(sentiment, key=lambda x: -x['theme_score'])
        
        print("\n【情绪题材】（快进快出）")
        print("-" * 80)
        print(f"{'排名':<4} {'主题':<15} {'主题评分':<8} {'情绪分':<6} {'扩散度':<6} {'单票驱动':<8}")
        print("-" * 80)
        for i, item in enumerate(sentiment_sorted, 1):
            print(f"{i:<4} {item['theme']:<15} {item['theme_score']:<8.1f} {item['sentiment_score']:<6.0f} {item['breadth']:<6.2f} {item['top1_stock_contribution']:<8.2f}")
    
    if garbage:
        garbage_sorted = sorted(garbage, key=lambda x: -x['theme_score'])
        
        print("\n【过滤掉的垃圾主题】")
        print("-" * 80)
        print(f"{'主题':<15} {'主题评分':<8} {'原因'}")
        print("-" * 80)
        for item in garbage_sorted:
            reason_str = "; ".join(item['reasons'])
            print(f"{item['theme']:<15} {item['theme_score']:<8.1f} {reason_str}")
    
    print("\n" + "=" * 80)
    print("【关键原则】")
    print("  1. 主线 = 趋势 + 扩散 + 加速")
    print("  2. 轮动 = 趋势 + 加速，但未扩散")
    print("  3. 情绪 = 情绪强，但无扩散")
    print("  4. 垃圾 = 无趋势 or 无扩散 or 单票驱动")
    print("=" * 80)


def main():
    """主函数"""
    if not os.path.exists(DB_PATH):
        print(f"错误：数据库文件不存在 {DB_PATH}")
        print("请先运行 theme_trend_sentiment_score.py 生成数据")
        return
    
    df, prev_df, trade_date, prev_date = load_theme_data()
    
    if df.empty:
        print(f"错误：{trade_date} 无主题数据")
        return
    
    print(f"[数据] 加载 {len(df)} 个主题，昨日数据: {'有' if not prev_df.empty else '无'}")
    
    df_with_factors = compute_derived_factors(df, prev_df)
    
    mainline, rotation, sentiment, garbage = classify_themes(df_with_factors)
    
    print_report(mainline, rotation, sentiment, garbage, trade_date)


if __name__ == "__main__":
    main()
