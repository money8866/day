#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
回溯生成历史数据脚本
"""
import os
import sys
import sqlite3
import numpy as np
import pandas as pd
import tushare as ts
from datetime import datetime, timedelta
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

original_expanduser = os.path.expanduser
safe_cache_dir = os.path.join(BASE_DIR, 'cache_backbone_tushare')
os.makedirs(safe_cache_dir, exist_ok=True)

def safe_expanduser(path):
    if '~/tk.csv' in path or '\\tk.csv' in path or 'tk.csv' in path:
        return os.path.join(safe_cache_dir, 'tk.csv')
    return original_expanduser(path)

os.path.expanduser = safe_expanduser

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', '.env'))
TUSHARE_TOKEN = os.getenv('TUSHARE_TOKEN')
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

def get_trade_dates(start_date, end_date):
    cal = pro.trade_cal(exchange='', start_date=start_date, end_date=end_date)
    cal = cal[cal['is_open'] == 1]
    return list(cal['cal_date'].astype(str))

def get_index_kline(ts_code, start_date, end_date):
    df = pro.index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    if df is None or df.empty:
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    if df is not None and not df.empty:
        df = df.sort_values('trade_date').reset_index(drop=True)
    return df

def calc_trend_score(df):
    if df is None or len(df) < 20:
        return 50.0, "无数据"
    
    latest = df.iloc[-1]
    ma5 = df['close'].rolling(5).mean().iloc[-1]
    ma10 = df['close'].rolling(10).mean().iloc[-1]
    ma20 = df['close'].rolling(20).mean().iloc[-1]
    ma60 = df['close'].rolling(60).mean().iloc[-1] if len(df) >= 60 else ma20
    
    ma5_slope = (ma5 - df['close'].rolling(5).mean().iloc[-5]) / ma5 * 100 if len(df) >= 10 else 0
    ma10_slope = (ma10 - df['close'].rolling(10).mean().iloc[-10]) / ma10 * 100 if len(df) >= 20 else 0
    ma20_slope = (ma20 - df['close'].rolling(20).mean().iloc[-20]) / ma20 * 100 if len(df) >= 40 else 0
    
    is_below_ma20 = latest['close'] < ma20
    is_below_ma10 = latest['close'] < ma10
    is_suppressed_by_ma10 = latest['close'] < ma10 and ma10 < ma20
    is_ma_down = ma5 < ma10 < ma20
    is_ma_trend_down = ma5_slope < 0 or ma10_slope < 0 or ma20_slope < 0
    
    down_days = 0
    for i in range(1, 6):
        if len(df) > i and df['pct_chg'].iloc[-i] < 0:
            down_days += 1
        else:
            break
    is_consecutive_down = down_days >= 2
    
    is_gradually_down = False
    if len(df) >= 10:
        recent5_avg = df['close'].tail(5).mean()
        prev5_avg = df['close'].tail(10).head(5).mean()
        if recent5_avg < prev5_avg * 0.995:
            is_gradually_down = True
    
    ma_score = 0
    if latest['close'] > ma5 > ma10 > ma20:
        ma_score = 30
    elif latest['close'] > ma5 > ma10:
        ma_score = 25
    elif ma5 > ma10 > ma20:
        ma_score = 20
    elif latest['close'] > ma20:
        ma_score = 15
    elif is_below_ma20 and is_below_ma10 and is_ma_down:
        ma_score = 3
    elif is_below_ma20:
        ma_score = 5
    else:
        ma_score = 10
    
    closes = df['close'].tail(10).values
    x = np.arange(10)
    slope, _ = np.polyfit(x, closes, 1)
    slope_pct = slope / closes[0] * 100
    slope_score = 12.5 + slope_pct * 3
    slope_score = min(25, max(0, slope_score))
    
    gain5 = (df['close'].iloc[-1] / df['close'].iloc[-6] - 1) * 100 if len(df) >= 6 else 0
    gain10 = (df['close'].iloc[-1] / df['close'].iloc[-11] - 1) * 100 if len(df) >= 11 else 0
    gain_score = 12.5 + (gain5 + gain10) / 2
    gain_score = min(25, max(0, gain_score))
    
    recent_high = df['high'].tail(20).max()
    recent_low = df['low'].tail(20).min()
    
    break_score = 12
    if latest['close'] > recent_high:
        break_score = 20
    elif latest['close'] < recent_low:
        break_score = 2
    elif latest['close'] < recent_low * 1.05:
        break_score = 5
    elif latest['close'] > recent_high * 0.95:
        break_score = 18
    else:
        break_score = 10
    
    trend_score = ma_score + slope_score + gain_score + break_score
    trend_score = max(0, min(100, trend_score))
    
    if is_below_ma20 and is_below_ma10 and (is_ma_down or is_ma_trend_down or is_consecutive_down or is_gradually_down or is_suppressed_by_ma10):
        trend_status = "下降趋势"
        trend_score = min(trend_score, 30)
    elif is_below_ma20:
        trend_status = "震荡偏弱"
        trend_score = min(trend_score, 40)
    elif ma5 > ma10 > ma20 and latest['close'] > ma5:
        trend_status = "上升趋势"
    elif ma5 > ma10 > ma20:
        trend_status = "震荡偏强"
    else:
        trend_status = "震荡偏弱"
    
    return trend_score, trend_status

def calc_sentiment_score(df):
    if df is None or len(df) < 20:
        return 50.0, "无数据"
    
    latest = df.iloc[-1]
    
    vol5 = df['vol'].tail(5).mean()
    vol20 = df['vol'].tail(20).mean()
    vol_ratio = vol5 / vol20 if vol20 > 0 else 1
    vol_score = min(25, max(0, 12.5 + (vol_ratio - 1) * 20))
    
    amplitude = (latest['high'] - latest['low']) / latest['low'] * 100
    amp_score = min(25, max(0, 12.5 + (amplitude - 2) * 3))
    
    vol20 = df['pct_chg'].tail(20).std()
    volatility = vol20 if not pd.isna(vol20) else 1.5
    vola_score = min(25, max(0, 12.5 + (2 - volatility) * 5))
    
    streak = 0
    for i in range(1, 6):
        if len(df) > i:
            if df['pct_chg'].iloc[-i] > 0:
                streak += 1
            else:
                break
    streak_score = min(25, max(0, 12.5 + streak * 3))
    
    sentiment_score = vol_score + amp_score + vola_score + streak_score
    sentiment_score = max(0, min(100, sentiment_score))
    
    if sentiment_score >= 70:
        sentiment_status = "情绪高涨"
    elif sentiment_score >= 50:
        sentiment_status = "情绪温和"
    elif sentiment_score >= 30:
        sentiment_status = "情绪低迷"
    else:
        sentiment_status = "情绪退潮"
    
    return sentiment_score, sentiment_status

def save_to_database(trade_date, results):
    db_path = os.path.join(safe_cache_dir, "market_analysis.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        for r in results:
            cursor.execute('''
                INSERT OR REPLACE INTO index_analysis 
                (trade_date, index_name, index_code, trend_score, trend_status, 
                 sentiment_score, sentiment_status, close_price, pct_change)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                trade_date,
                r['name'],
                r['code'],
                r['trend_score'],
                r['trend_status'],
                r['sentiment_score'],
                r['sentiment_status'],
                r['close'],
                r['pct_chg']
            ))
        
        conn.commit()
        print(f"  ✅ {trade_date} 数据已保存")
    except Exception as e:
        print(f"  ❌ {trade_date} 保存失败: {e}")
        conn.rollback()
    finally:
        conn.close()

def init_db():
    db_path = os.path.join(safe_cache_dir, "market_analysis.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS index_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            index_name TEXT NOT NULL,
            index_code TEXT NOT NULL,
            trend_score REAL,
            trend_status TEXT,
            sentiment_score REAL,
            sentiment_status TEXT,
            close_price REAL,
            pct_change REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(trade_date, index_code)
        )
    ''')
    conn.commit()
    conn.close()

def process_dates(trade_dates):
    indices = {
        "沪深300": "000300.SH",
        "上证指数": "000001.SH",
        "创业板指": "399006.SZ"
    }
    
    init_db()
    
    for trade_date in trade_dates:
        print(f"\n📊 处理日期: {trade_date}")
        
        results = []
        analysis_start_date = (datetime.strptime(trade_date, '%Y%m%d') - timedelta(days=90)).strftime('%Y%m%d')
        
        for name, code in indices.items():
            df = get_index_kline(code, analysis_start_date, trade_date)
            
            if df is None or df.empty:
                print(f"   ❌ {name} 无数据")
                continue
            
            df = df[df['trade_date'] <= trade_date]
            if df.empty:
                print(f"   ❌ {name} 无有效数据")
                continue
            
            trend_score, trend_status = calc_trend_score(df)
            sentiment_score, sentiment_status = calc_sentiment_score(df)
            latest = df.iloc[-1]
            
            print(f"   ✅ {name}: 趋势={trend_status}({trend_score:.1f}) 情绪={sentiment_status}({sentiment_score:.1f})")
            
            results.append({
                "name": name,
                "code": code,
                "trend_score": trend_score,
                "trend_status": trend_status,
                "sentiment_score": sentiment_score,
                "sentiment_status": sentiment_status,
                "close": latest['close'],
                "pct_chg": latest['pct_chg']
            })
        
        if results:
            save_to_database(trade_date, results)
    
    print("\n🎉 回溯完成！")

def main():
    if len(sys.argv) >= 3:
        start_date = sys.argv[1]
        end_date = sys.argv[2]
        print(f"🔄 正在回溯生成 {start_date} ~ {end_date} 的数据...")
        trade_dates = get_trade_dates(start_date, end_date)
    elif len(sys.argv) == 2:
        days = int(sys.argv[1])
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=days + 10)).strftime('%Y%m%d')
        trade_dates = get_trade_dates(start_date, end_date)
        trade_dates = trade_dates[-days:]
    else:
        days = 3
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=days + 10)).strftime('%Y%m%d')
        trade_dates = get_trade_dates(start_date, end_date)
        trade_dates = trade_dates[-days:]
    
    print(f"📅 目标日期: {trade_dates}")
    process_dates(trade_dates)

if __name__ == '__main__':
    main()