#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""持仓初始化程序"""

import os
import sys
import sqlite3
import pandas as pd
import tushare as ts
from datetime import datetime

# 路径配置
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", ".env"))

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
ts.set_token(TUSHARE_TOKEN)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")
os.makedirs(REPORT_DIR, exist_ok=True)
DB_PATH = os.path.join(REPORT_DIR, "etf_result.db")

def get_last_trade_date():
    """获取最后一个交易日"""
    now = datetime.now()
    if now.hour < 15:
        query_date = (now - pd.Timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')
    
    cal = ts.pro_api().trade_cal(exchange='', start_date='20240101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    return str(cal[cal['cal_date'] <= query_date]['cal_date'].max())

TRADE_DATE = get_last_trade_date()
print(f"交易日: {TRADE_DATE}")

def init_portfolio_table():
    """初始化持仓表"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            ts_code TEXT PRIMARY KEY, industry TEXT, buy_date TEXT, buy_price REAL,
            current_price REAL, shares INTEGER DEFAULT 0, target_weight REAL DEFAULT 0,
            stop_loss REAL DEFAULT 0, take_profit REAL DEFAULT 0, status TEXT DEFAULT 'holding'
        )
    """)
    try:
        cursor.execute("ALTER TABLE portfolio ADD COLUMN target_weight REAL DEFAULT 0")
        conn.commit()
    except:
        pass
    conn.commit()
    conn.close()

def clear_and_init_portfolio():
    """清空并初始化持仓"""
    print("\n正在清空现有持仓...")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 清空持仓
    cursor.execute("DELETE FROM portfolio WHERE status='holding'")
    
    # 获取ETF当前价格
    etf_codes = ['159611.SZ', '515220.SH']  # 电力ETF, 煤炭ETF
    price_dict = {}
    
    for code in etf_codes:
        try:
            df = ts.pro_api().fund_daily(ts_code=code, start_date=TRADE_DATE, end_date=TRADE_DATE)
            if not df.empty:
                price_dict[code] = df.iloc[0]['close']
            else:
                # 如果没有今天数据，取最近一天
                df = ts.pro_api().fund_daily(ts_code=code, start_date='20250101', end_date=TRADE_DATE)
                if not df.empty:
                    df_sorted = df.sort_values('trade_date', ascending=False)
                    price_dict[code] = df_sorted.iloc[0]['close']
        except Exception as e:
            print(f"获取 {code} 价格失败: {e}")
    
    print(f"\n获取到的价格: {price_dict}")
    
    # 初始化持仓
    holdings = [
        {
            "ts_code": "159611.SZ",
            "industry": "电力",
            "buy_date": TRADE_DATE,
            "buy_price": price_dict.get("159611.SZ", 1.25),
            "current_price": price_dict.get("159611.SZ", 1.25),
            "shares": 1000,
            "target_weight": 15.0,
            "stop_loss": round(price_dict.get("159611.SZ", 1.25) * 0.95, 3),
            "take_profit": round(price_dict.get("159611.SZ", 1.25) * 1.20, 3),
            "status": "holding"
        },
        {
            "ts_code": "515220.SH",
            "industry": "煤炭",
            "buy_date": TRADE_DATE,
            "buy_price": price_dict.get("515220.SH", 2.00),
            "current_price": price_dict.get("515220.SH", 2.00),
            "shares": 500,
            "target_weight": 5.0,
            "stop_loss": round(price_dict.get("515220.SH", 2.00) * 0.95, 3),
            "take_profit": round(price_dict.get("515220.SH", 2.00) * 1.20, 3),
            "status": "holding"
        }
    ]
    
    for holding in holdings:
        cursor.execute("""
            INSERT OR REPLACE INTO portfolio
            (ts_code, industry, buy_date, buy_price, current_price, shares, target_weight, stop_loss, take_profit, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'holding')
        """, (holding["ts_code"], holding["industry"], holding["buy_date"], 
             holding["buy_price"], holding["current_price"], holding["shares"], 
             holding["target_weight"], holding["stop_loss"], holding["take_profit"]))
    
    conn.commit()
    
    # 查看持仓
    print("\n初始化完成！当前持仓：")
    df = pd.read_sql("SELECT * FROM portfolio WHERE status='holding'", conn)
    print(df.to_string(index=False))
    
    conn.close()
    
    return len(holdings)

if __name__ == "__main__":
    print("=" * 60)
    print("持仓初始化程序")
    print("=" * 60)
    
    init_portfolio_table()
    
    count = clear_and_init_portfolio()
    
    print(f"\n✅ 成功初始化 {count} 只ETF持仓！")
    print(f"   - 电力 (159611.SZ): 15%")
    print(f"   - 煤炭 (515220.SH): 5%")
    print(f"\n数据库位置: {DB_PATH}")
