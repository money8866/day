#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断主题评分问题"""

import sys
import os
import tushare as ts
from dotenv import load_dotenv

# 加载环境变量
DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)

# 初始化Tushare
ts.set_token(os.getenv('TUSHARE_TOKEN'))
pro = ts.pro_api()

def test_data_loading():
    """测试数据加载"""
    print("="*60)
    print("测试数据加载")
    print("="*60)
    
    # 测试1: 检查今天的日期
    import datetime
    today = datetime.datetime.now().strftime("%Y%m%d")
    print(f"\n今天的日期: {today}")
    
    # 测试2: 检查是否是交易日
    print(f"\n检查是否是交易日...")
    try:
        cal_df = pro.trade_cal(
            start_date='20260525',
            end_date='20260530',
            is_open='1'
        )
        print(f"可用的交易日:")
        print(cal_df)
    except Exception as e:
        print(f"❌ 检查交易日失败: {e}")
    
    # 测试3: 检查主题投资组合
    print(f"\n检查主题投资组合...")
    try:
        import sqlite3
        conn = sqlite3.connect('theme_portfolio.db')
        cursor = conn.cursor()
        
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        print(f"数据库表: {[t[0] for t in tables]}")
        
        if tables:
            cursor.execute("SELECT COUNT(*) FROM theme_stocks")
            count = cursor.fetchone()[0]
            print(f"主题股票数量: {count}")
            
            # 查看前5条记录
            cursor.execute("SELECT * FROM theme_stocks LIMIT 5")
            rows = cursor.fetchall()
            print(f"\n前5条记录:")
            for row in rows:
                print(f"  {row}")
        
        conn.close()
    except Exception as e:
        print(f"❌ 检查主题投资组合失败: {e}")
    
    # 测试4: 测试股票数据获取
    print(f"\n测试股票数据获取...")
    test_codes = ['000001.SZ', '300001.SZ', '688001.SH']
    
    for ts_code in test_codes:
        try:
            df = pro.daily(
                ts_code=ts_code,
                start_date='20260525',
                end_date='20260529'
            )
            if not df.empty:
                print(f"  ✅ {ts_code}: 获取到 {len(df)} 条数据")
                print(f"     最新日期: {df.iloc[0]['trade_date']}, 涨跌幅: {df.iloc[0]['pct_chg']}%")
            else:
                print(f"  ⚠️ {ts_code}: 无数据")
        except Exception as e:
            print(f"  ❌ {ts_code}: 获取失败 - {e}")
    
    # 测试5: 检查缓存的日线数据
    print(f"\n检查缓存的日线数据...")
    cache_dir = "cache_backbone_tushare"
    if os.path.exists(cache_dir):
        cache_files = [f for f in os.listdir(cache_dir) if f.startswith('cache_daily_') or f.startswith('cache_20day_daily_')]
        print(f"缓存文件数量: {len(cache_files)}")
        if cache_files:
            print(f"最新的5个缓存文件:")
            cache_files.sort(reverse=True)
            for f in cache_files[:5]:
                print(f"  {f}")

if __name__ == "__main__":
    test_data_loading()
