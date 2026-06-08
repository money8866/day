#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
将 theme_pattern_stock_picker.py 生成的龙头/中军/补涨中军数据同步到 theme_portfolio.db
用于 realtime 监测
"""

import os
import sys
import sqlite3
import pandas as pd
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, 'cache_backbone_tushare')

TRADE_DATE = datetime.now().strftime('%Y%m%d')

def sync_to_portfolio_db():
    # 读取 theme_pattern_stocks.csv
    csv_path = os.path.join(CACHE_DIR, 'theme_pattern_stocks.csv')
    if not os.path.exists(csv_path):
        print(f"❌ 未找到 {csv_path}，请先运行 theme_pattern_stock_picker.py")
        return False

    df = pd.read_csv(csv_path)
    if df.empty:
        print("❌ theme_pattern_stocks.csv 为空")
        return False

    print(f"读取 {len(df)} 条数据")

    # 连接数据库
    db_path = os.path.join(CACHE_DIR, "theme_portfolio.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 删除旧数据
    cursor.execute('DROP TABLE IF EXISTS themes')
    cursor.execute('DROP TABLE IF EXISTS portfolio')

    # 创建表结构
    cursor.execute('''CREATE TABLE themes (id INTEGER PRIMARY KEY, theme_name TEXT UNIQUE, industry TEXT, keywords TEXT)''')
    cursor.execute('''CREATE TABLE portfolio (id INTEGER PRIMARY KEY, ts_code TEXT, name TEXT, theme_name TEXT, layer TEXT, mcap REAL, turnover REAL, amount REAL, purity INTEGER, trend REAL, volatility REAL, trade_date TEXT)''')

    # 按主题分组，每只股票根据 buy_type 设置 layer
    # 中军 -> 龙头
    # 补涨中军 -> 核心

    portfolio_records = []

    for _, row in df.iterrows():
        code = row['code']
        name = row['name']
        theme = row['theme_name']
        buy_type = row['buy_type']

        if buy_type == '中军':
            layer = 'leader'
        elif buy_type == '补涨中军':
            layer = 'core'
        else:
            layer = 'follower'

        mcap = row.get('mcap', 0) or 0
        turnover = row.get('turnover_rate', 0) or 0
        amount = row.get('avg_amount_20', 0) or 0
        final_score = row.get('final_score', 0) or 0

        portfolio_records.append({
            'ts_code': code,
            'name': name,
            'theme_name': theme,
            'layer': layer,
            'mcap': mcap,
            'turnover': turnover,
            'amount': amount,
            'purity': int(final_score),  # 用 final_score 作为 purity
            'trend': row.get('theme_score', 0) or 0,
            'volatility': row.get('RS20', 0) or 0,
            'trade_date': TRADE_DATE
        })

    # 插入数据
    for stock in portfolio_records:
        cursor.execute('''INSERT INTO portfolio (ts_code, name, theme_name, layer, mcap, turnover, amount, purity, trend, volatility, trade_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (stock['ts_code'], stock['name'], stock['theme_name'], stock['layer'],
             stock['mcap'], stock['turnover'], stock['amount'], stock['purity'],
             stock['trend'], stock['volatility'], stock['trade_date']))

    # 统计每种类型的数量
    leader_count = sum(1 for r in portfolio_records if r['layer'] == 'leader')
    core_count = sum(1 for r in portfolio_records if r['layer'] == 'core')
    follower_count = sum(1 for r in portfolio_records if r['layer'] == 'follower')

    conn.commit()

    # 显示结果
    print(f"\n✅ 数据已同步到 {db_path}")
    print(f"   龙头(中军): {leader_count} 只")
    print(f"   核心(补涨中军): {core_count} 只")
    print(f"   跟随: {follower_count} 只")

    # 显示详情
    print("\n各主题龙头和中军：")
    current_theme = None
    for stock in sorted(portfolio_records, key=lambda x: (x['theme_name'], x['layer'])):
        if stock['theme_name'] != current_theme:
            current_theme = stock['theme_name']
            print(f"\n【{current_theme}】")
        layer_display = "龙头" if stock['layer'] == 'leader' else "中军" if stock['layer'] == 'core' else "跟随"
        print(f"   {layer_display}: {stock['name']} ({stock['ts_code']})")

    conn.close()
    return True

if __name__ == "__main__":
    print("=" * 60)
    print("同步龙头/中军/补涨中军到 theme_portfolio.db")
    print("=" * 60)
    sync_to_portfolio_db()
    print("=" * 60)