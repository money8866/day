
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Integrated version: ETF Component Stock Analysis System
- Get complete ETF list and scoring algorithm from etf_quant.py
- Use akshare to get ETF holdings
- Complete data caching mechanism
"""
import os
import sys
import pickle
import warnings
import time
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

import numpy as np
import pandas as pd
import tushare as ts
import akshare as ak

warnings.filterwarnings('ignore')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(BASE_DIR)
DOTENV_PATH = os.path.join(PARENT_DIR, "config", ".env")
load_dotenv(DOTENV_PATH)

CACHE_DIR = os.path.join(BASE_DIR, "cache_etf_theme")
os.makedirs(CACHE_DIR, exist_ok=True)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

THEME_PATH = os.path.join(BASE_DIR, "theme.json")


# =========================================================
# ETF pool copied from etf_quant.py
# =========================================================
ETF_POOL = {
    '半导体': '512480.SH',
    '人工智能': '159819.SZ',
    '算力': '561210.SH',
    '机器人': '562500.SH',
    '软件': '515230.SH',
    '通信': '515880.SH',
    '新能源': '516160.SH',
    '光伏': '515790.SH',
    '储能': '159566.SZ',
    '军工': '512660.SH',
    '创新药': '159992.SZ',
    '消费电子': '159732.SZ',
    '黄金': '518880.SH',
    '证券': '512880.SH',
    '红利': '515180.SH',
    '银行': '512800.SH',
    '消费': '159928.SZ',
    '酒': '512690.SH',
    '电池': '159755.SZ',
    '有色金属': '516650.SH',
    '芯片': '159995.SZ',
    '化工': '159870.SZ',
    '半导体设备': '159516.SZ',
    '煤炭': '515220.SH',
    '游戏': '159869.SZ',
    '金融科技': '159851.SZ',
    '电力': '159611.SZ',
    '电网设备': '561380.SH',
    '新能源车': '515030.SH',
    '航空航天': '159227.SZ',
    '医疗器械': '159883.SZ',
    '食品饮料': '159736.SZ',
    '钢铁': '515210.SH',
}


# =========================================================
# Get last trade date
# =========================================================
def get_last_trade_date():
    now = datetime.now()
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')

    cal = pro.trade_cal(exchange='', start_date='20240101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    return str(cal[cal['cal_date'] <= query_date]['cal_date'].max())


TRADE_DATE = get_last_trade_date()


# =========================================================
# Get ETF data (with cache)
# =========================================================
def get_etf_data(ts_code):
    cache_file = os.path.join(CACHE_DIR, f"{ts_code}_daily.csv")
    
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            df['trade_date'] = df['trade_date'].astype(str)
            if len(df) > 120 and (df['trade_date'] == TRADE_DATE).any():
                return df.sort_values('trade_date')
        except:
            pass
    
    try:
        df = pro.fund_daily(ts_code=ts_code, start_date='20240101', end_date=TRADE_DATE)
        if df.empty:
            return None
        
        df = df.sort_values('trade_date')
        df.to_csv(cache_file, index=False)
        time.sleep(0.05)
        return df
    except Exception as e:
        print(f"{ts_code} data fetch failed: {e}")
        return None


# =========================================================
# Get index data
# =========================================================
def get_index_data():
    cache_file = os.path.join(CACHE_DIR, "index_000300.csv")
    
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            if len(df) > 100:
                return df
        except:
            pass
    
    df = pro.index_daily(ts_code='000300.SH', start_date='20240101', end_date=TRADE_DATE)
    df = df.sort_values('trade_date')
    df.to_csv(cache_file, index=False)
    return df


# =========================================================
# Get ETF holdings (akshare interface, with cache)
# =========================================================
def get_etf_holdings(etf_code, date='2024'):
    cache_file = os.path.join(CACHE_DIR, f"hold_{etf_code}.pkl")
    
    if os.path.exists(cache_file):
        try:
            cache_time = os.path.getmtime(cache_file)
            if (time.time() - cache_time) < 86400 * 7:
                with open(cache_file, 'rb') as f:
                    return pickle.load(f)
        except:
            pass
    
    try:
        print(f"  Fetching holdings for {etf_code}...")
        df = ak.fund_portfolio_hold_em(symbol=etf_code, date=date)
        if df is not None and len(df) > 0:
            if '占净值比例' in df.columns:
                df = df.sort_values('占净值比例', ascending=False).drop_duplicates(subset=['股票代码'])
            
            with open(cache_file, 'wb') as f:
                pickle.dump(df, f)
            print(f"  Success, {len(df)} stocks")
            return df
        else:
            print(f"  No data for {etf_code}")
            return None
    except Exception as e:
        print(f"  Failed to get {etf_code} holdings: {e}")
        return None


# =========================================================
# Calculate technical indicators (copied from etf_quant.py)
# =========================================================
def calc_indicators(df):
    df = df.copy()
    
    for ma in [5, 10, 20, 60]:
        df[f'ma{ma}'] = df['close'].rolling(ma).mean()
    
    df['vol5'] = df['vol'].rolling(5).mean()
    
    for n in [5, 10, 20]:
        df[f'pct{n}'] = (df['close'] / df['close'].shift(n) - 1) * 100
    
    df['slope20'] = (df['ma20'] / df['ma20'].shift(5) - 1) * 100
    df['volatility'] = df['pct_chg'].rolling(10).std()
    
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1)))
    )
    df['atr'] = df['tr'].rolling(14).mean()
    
    return df


# =========================================================
# Relative Strength RS (copied from etf_quant.py)
# =========================================================
def relative_strength(df, index_df):
    etf_return = (df['close'].iloc[-1] / df['close'].iloc[-20] - 1) * 100
    index_return = (index_df['close'].iloc[-1] / index_df['close'].iloc[-20] - 1) * 100
    return round(etf_return - index_return, 2)


# =========================================================
# Weekly trend (copied from etf_quant.py)
# =========================================================
def weekly_trend(df):
    try:
        weekly = df.copy()
        weekly.index = pd.to_datetime(weekly['trade_date'])
        weekly = weekly.resample('W').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'vol': 'sum'
        })
        weekly['ma5'] = weekly['close'].rolling(5).mean()
        weekly['ma10'] = weekly['close'].rolling(10).mean()
        latest = weekly.iloc[-1]
        return latest['ma5'] > latest['ma10']
    except:
        return False


# =========================================================
# Mainline start signal
# =========================================================
def mainline_start(df):
    if len(df) < 32:
        return False
    try:
        latest = df.iloc[-1]
        range30 = (df['high'].rolling(30).max().iloc[-2] / df['low'].rolling(30).min().iloc[-2])
        breakout = latest['close'] > df['high'].rolling(30).max().iloc[-2]
        volume_expand = latest['vol'] > df['vol5'].iloc[-2] * 1.5
        return range30 < 1.25 and breakout and volume_expand
    except:
        return False


# =========================================================
# Main uptrend
# =========================================================
def main_uptrend(df):
    if len(df) < 25:
        return False
    latest = df.iloc[-1]
    try:
        return (
            latest['ma5'] > latest['ma10'] > latest['ma20']
            and latest['slope20'] > 2
            and latest['pct5'] > latest['pct10'] / 2
        )
    except:
        return False


# =========================================================
# Volume structure
# =========================================================
def volume_structure(df):
    try:
        vol_trend = df['vol'].tail(20).corr(pd.Series(range(20), index=df.tail(20).index))
        score = max(0, min(10, vol_trend * 10))
        return score
    except:
        return 5


# =========================================================
# Breadth score
# =========================================================
def breadth_score(df):
    try:
        positive_days = (df['pct_chg'].tail(10) > 0).sum()
        return positive_days
    except:
        return 5


# =========================================================
# AI sentiment
# =========================================================
def ai_sentiment(industry):
    return 5


# =========================================================
# Wave stage
# =========================================================
def wave_stage(df):
    try:
        recent_max = df['high'].tail(30).max()
        recent_min = df['low'].tail(30).min()
        latest = df['close'].iloc[-1]
        rise = (latest - recent_min) / recent_min * 100
        return 'up' if rise > 0 else 'down', rise
    except:
        return 'unknown', 0


# =========================================================
# Trend exhaustion
# =========================================================
def trend_exhaust(df):
    try:
        latest = df.iloc[-1]
        if len(df) < 10:
            return False
        pct5_ago = df['pct_chg'].tail(10).head(5).mean()
        pct5_now = df['pct_chg'].tail(5).mean()
        return pct5_now < pct5_ago and pct5_now < 0.5
    except:
        return False


# =========================================================
# Volatility compress
# =========================================================
def volatility_compress(df):
    try:
        latest_atr = df['atr'].iloc[-1]
        atr_mean = df['atr'].rolling(20).mean().iloc[-1]
        return latest_atr < atr_mean * 0.8
    except:
        return False


# =========================================================
# First dip
# =========================================================
def first_dip(df):
    try:
        latest = df.iloc[-1]
        return (
            latest['close'] > latest['ma20']
            and latest['vol'] < latest['vol5']
            and abs(latest['close'] - latest['ma10']) / latest['ma10'] < 0.015
        )
    except:
        return False


# =========================================================
# ETF total score (complete copy from etf_quant.py)
# =========================================================
def etf_score(df, industry, index_df):
    latest = df.iloc[-1]
    score = 0
    
    score += latest['pct5'] * 2
    score += latest['pct10']
    
    if latest['ma5'] > latest['ma10'] > latest['ma20']:
        score += 20
    
    if latest['slope20'] > 2:
        score += 15
    
    rs = relative_strength(df, index_df)
    score += rs * 1.5
    
    if mainline_start(df):
        score += 25
    
    if main_uptrend(df):
        score += 20
    
    if first_dip(df):
        score += 20
    
    if weekly_trend(df):
        score += 15
    
    if volatility_compress(df):
        score += 10
    
    score += volume_structure(df)
    score += breadth_score(df)
    score += ai_sentiment(industry) * 0.3
    
    stage, rise = wave_stage(df)
    if rise > 20:
        score -= 15
    
    if trend_exhaust(df):
        score -= 30
    
    if not pd.isna(latest['volatility']):
        score -= latest['volatility']
    
    return round(score, 2), rs


# =========================================================
# Get stock data
# =========================================================
def get_stock_data(ts_code, days=120):
    cache_file = os.path.join(CACHE_DIR, f"stock_{ts_code}.pkl")
    
    if os.path.exists(cache_file):
        try:
            cache_time = os.path.getmtime(cache_file)
            if (time.time() - cache_time) < 86400:
                with open(cache_file, 'rb') as f:
                    return pickle.load(f)
        except:
            pass
    
    try:
        end_date = TRADE_DATE
        start_date = (datetime.strptime(TRADE_DATE, '%Y%m%d') - timedelta(days=days * 1.5)).strftime('%Y%m%d')
        
        df = pro.daily(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields='ts_code,trade_date,open,high,low,close,vol,amount,pct_chg'
        )
        
        if df is not None and len(df) > 0:
            df = df.sort_values('trade_date').tail(days)
            with open(cache_file, 'wb') as f:
                pickle.dump(df, f)
            time.sleep(0.05)
            return df
        return None
    except Exception as e:
        return None


# =========================================================
# Check stock conditions
# =========================================================
def check_stock_conditions(df):
    if df is None or len(df) < 60:
        return False, "Insufficient data"
    
    df = df.copy()
    df['ma5'] = df['close'].rolling(5).mean()
    df['ma20'] = df['close'].rolling(20).mean()
    df['vol_ma20'] = df['vol'].rolling(20).mean()
    df['vol_ratio'] = df['vol'] / df['vol_ma20']
    df['return_20'] = df['close'].pct_change(20) * 100
    
    latest = df.iloc[-1]
    
    if pd.isna(latest['ma5']) or pd.isna(latest['ma20']) or latest['ma5'] <= latest['ma20']:
        return False, "MA5 <= MA20"
    
    if pd.isna(latest['vol_ratio']) or not (0.8 < latest['vol_ratio'] < 3.0):
        return False, f"Vol ratio {latest.get('vol_ratio', 0):.2f}"
    
    if latest.get('return_20', 0) >= 50:
        return False, f"Rise too big {latest.get('return_20', 0):.1f}%"
    
    return True, {
        'return_20': round(latest.get('return_20', 0), 2),
        'vol_ratio': round(latest.get('vol_ratio', 0), 2),
        'ma5': round(latest['ma5'], 2),
        'ma20': round(latest['ma20'], 2),
        'close': round(latest['close'], 2)
    }


# =========================================================
# Format stock code
# =========================================================
def format_stock_code(code):
    try:
        code = str(code).zfill(6)
        if code.startswith('6'):
            return f"{code}.SH"
        elif code.startswith('0') or code.startswith('3'):
            return f"{code}.SZ"
        elif code.startswith('8') or code.startswith('4'):
            return f"{code}.BJ"
        return None
    except:
        return None


# =========================================================
# Analyze single ETF
# =========================================================
def analyze_etf(industry, ts_code, index_df):
    print(f"\n{'='*80}")
    print(f"Analyzing: {industry} ({ts_code})")
    print(f"{'='*80}")
    
    etf_df = get_etf_data(ts_code)
    if etf_df is None:
        print(f"No data for {ts_code}")
        return None
    
    etf_df = calc_indicators(etf_df)
    score, rs = etf_score(etf_df, industry, index_df)
    
    print(f"\nETF score: {score:.2f}, RS: {rs:.2f}")
    
    simple_code = ts_code.replace('.SH', '').replace('.SZ', '')
    holdings_df = get_etf_holdings(simple_code)
    if holdings_df is None or len(holdings_df) == 0:
        print(f"No holdings data for {industry}")
        return {
            'industry': industry,
            'ts_code': ts_code,
            'etf_score': score,
            'rs': rs,
            'qualified_stocks': []
        }
    
    qualified = []
    print(f"\nAnalyzing holdings...")
    
    for idx, row in holdings_df.iterrows():
        try:
            stock_name = row.get('股票名称', '')
            stock_code_raw = row.get('股票代码', '')
            weight = row.get('占净值比例', 0)
            
            # 过滤微量持仓（权重<0.01%），避免非核心成分股污染
            if weight is None or (isinstance(weight, (int, float)) and weight < 0.01):
                continue
            
            if not stock_code_raw or pd.isna(stock_code_raw):
                continue
            
            ts_stock_code = format_stock_code(stock_code_raw)
            if not ts_stock_code:
                continue
            
            stock_df = get_stock_data(ts_stock_code)
            
            is_ok, result = check_stock_conditions(stock_df)
            
            if is_ok:
                qualified.append({
                    'ts_code': ts_stock_code,
                    'name': stock_name,
                    'weight': weight,
                    **result
                })
        
        except Exception as e:
            continue
    
    if qualified:
        qualified_sorted = sorted(qualified, key=lambda x: x['weight'] or 0, reverse=True)
        
        print(f"\nFound {len(qualified)} qualified stocks:")
        print(f"\n  {'Rank':<4} {'Code':<12} {'Name':<10} {'Weight':<8} {'20d Rise':<12} {'Vol Ratio':<10} {'MA5/MA20':<15}")
        print(f"  {'-'*85}")
        
        for i, s in enumerate(qualified_sorted[:15], 1):
            print(f"  {i:<4} {s['ts_code']:<12} {s['name']:<10} {s['weight']:<8} {s['return_20']:<12.2f} {s['vol_ratio']:<10.2f} {s['ma5']:.2f}/{s['ma20']:.2f}")
    else:
        print(f"\nNo qualified stocks for this ETF")
    
    return {
        'industry': industry,
        'ts_code': ts_code,
        'etf_score': score,
        'rs': rs,
        'qualified_stocks': qualified
    }


# =========================================================
# Main function
# =========================================================
def main():
    print("="*80)
    print("ETF Component Stock Analysis System")
    print("="*80)
    print(f"Analysis time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Trade date: {TRADE_DATE}")
    print(f"ETF count: {len(ETF_POOL)}")
    
    print(f"\nGetting benchmark index...")
    index_df = get_index_data()
    if index_df is None:
        print("No index data")
        return
    
    all_results = []
    for industry, ts_code in ETF_POOL.items():
        result = analyze_etf(industry, ts_code, index_df)
        if result:
            all_results.append(result)
    
    print(f"\n\n{'='*80}")
    print("All ETF Results")
    print(f"{'='*80}")
    
    all_results_sorted = sorted(all_results, key=lambda x: x['etf_score'], reverse=True)
    
    print(f"\n  {'Rank':<4} {'Industry':<15} {'Score':<10} {'RS':<8} {'Count':<12}")
    print(f"  {'-'*65}")
    for i, result in enumerate(all_results_sorted[:20], 1):
        print(f"  {i:<4} {result['industry']:<15} {result['etf_score']:<10.2f} {result['rs']:<8.2f} {len(result['qualified_stocks']):<12}")
    
    all_qualified_stocks = []
    for result in all_results_sorted:
        for stock in result['qualified_stocks']:
            stock['industry'] = result['industry']
            stock['etf_score'] = result['etf_score']
            all_qualified_stocks.append(stock)
    
    if all_qualified_stocks:
        all_qualified_stocks_sorted = sorted(all_qualified_stocks, key=lambda x: x['return_20'], reverse=True)
        
        print(f"\n\nAll Qualified Stocks (Total: {len(all_qualified_stocks)})")
        print(f"\n  {'Rank':<4} {'Name':<10} {'Industry':<15} {'Code':<12} {'Weight':<8} {'20d Rise':<12} {'Vol Ratio':<10} {'ETF Score':<10}")
        print(f"  {'-'*100}")
        
        for i, s in enumerate(all_qualified_stocks_sorted[:30], 1):
            print(f"  {i:<4} {s['name']:<10} {s['industry']:<15} {s['ts_code']:<12} {s['weight']:<8} {s['return_20']:<12.2f} {s['vol_ratio']:<10.2f} {s['etf_score']:<10.2f}")
    
    print(f"\n\n{'='*80}")
    print("Analysis complete")
    print(f"{'='*80}")


if __name__ == "__main__":
    main()

