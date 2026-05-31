
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
使用 ak.fund_portfolio_hold_em 获取 ETF 成份股进行分析
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

CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
os.makedirs(CACHE_DIR, exist_ok=True)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

THEME_PATH = os.path.join(BASE_DIR, "theme.json")

# ETF 代码与名称映射（可以从 theme.json 或 ETF_POOL 获取）
ETF_POOL = {
    '半导体': '512480', '芯片': '159995', '半导体设备': '159516',
    '人工智能': '159819', 'AI算力': '515980',
    '消费电子': '159732', '汽车电子': '159790',
    '新能源': '516160', '光伏': '515790', '储能': '159566',
    '电池': '159755', '新能源车': '515030',
    '创新药': '159992', '医疗器械': '159886',
    '机器人': '562500', '人工智能机器人': '159771',
    '军工': '512660',
}


def get_etf_holdings(etf_code, date='2024'):
    """
    获取 ETF 成份股（使用 ak.fund_portfolio_hold_em）
    
    参数:
        etf_code: ETF 代码，如 '510300'
        date: 查询年份，默认 '2024'
    """
    cache_file = os.path.join(CACHE_DIR, f"etf_hold_{etf_code}_{date}.pkl")
    
    # 检查缓存
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'rb') as f:
                return pickle.load(f)
        except:
            pass
    
    # 调用接口
    try:
        print(f"  正在获取 {etf_code} 的成份股...")
        df = ak.fund_portfolio_hold_em(symbol=etf_code, date=date)
        if df is not None and len(df) > 0:
            # 保存缓存
            with open(cache_file, 'wb') as f:
                pickle.dump(df, f)
            print(f"  ✓ 成功获取 {len(df)} 只成份股")
            return df
        else:
            print(f"  ⚠️ 未获取到 {etf_code} 的成份股数据")
            return None
    except Exception as e:
        print(f"  ✗ 获取 {etf_code} 成份股失败: {e}")
        return None


def get_etf_data(etf_code, days=150):
    """
    获取 ETF 行情数据（来自 etf_mainline_strategy_tushare.py）
    """
    try:
        today = datetime.now()
        start_date = (today - timedelta(days=days)).strftime("%Y%m%d")
        
        if etf_code.startswith("5") or etf_code.startswith("6"):
            ts_code = f"{etf_code}.SH"
        else:
            ts_code = f"{etf_code}.SZ"
        
        df = pro.fund_daily(
            ts_code=ts_code,
            start_date=start_date,
            fields="ts_code,trade_date,close,vol"
        )
        
        if df is not None and len(df) > 0:
            df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
            df = df.sort_values("trade_date").reset_index(drop=True)
            return df
        return None
    except Exception as e:
        print(f"  获取ETF数据失败 {etf_code}: {e}")
        return None


def calculate_etf_score(df, benchmark_df):
    """
    计算 ETF 综合得分（来自 etf_mainline_strategy_tushare.py）
    """
    if df is None or len(df) < 25:  # 至少需要20天+5天
        return None

    close = df['close']
    
    mom_20d = close.pct_change(20).iloc[-1] * 100 if len(df) > 20 else 0
    
    vol = df.get('vol', None)
    if vol is None or len(vol) < 20:
        vol_score = 50
    else:
        recent_vol_avg = vol.tail(5).mean()
        hist_vol_avg = vol.tail(20).mean()
        vol_ratio = recent_vol_avg / (hist_vol_avg + 1e-6)
        vol_score = min(vol_ratio * 50, 100)
    
    daily_returns = close.pct_change().dropna()
    if len(daily_returns) >= 20:
        volatility = daily_returns.tail(20).std() * np.sqrt(252) * 100
        if volatility > 0:
            risk_adj_score = min(mom_20d / volatility * 10, 100)
        else:
            risk_adj_score = 50
    else:
        risk_adj_score = 50
    
    rel_score = 50  # 默认值，简化
    
    total_score = (
        mom_20d * 0.40 +
        vol_score * 0.25 +
        risk_adj_score * 0.20 +
        rel_score * 0.15
    )
    
    return {
        'momentum': round(mom_20d, 2),
        'vol_score': round(vol_score, 2),
        'risk_adj': round(risk_adj_score, 2),
        'rel_strength': round(rel_score, 2),
        'total_score': round(total_score, 2)
    }


def get_stock_data(ts_code, days=120):
    """
    获取个股数据
    """
    cache_file = os.path.join(CACHE_DIR, f"stock_daily_{ts_code}.pkl")
    
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'rb') as f:
                return pickle.load(f)
        except:
            pass
    
    try:
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=days*1.5)).strftime('%Y%m%d')
        
        df = pro.daily(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields='ts_code,trade_date,open,high,low,close,vol,amount'
        )
        
        if df is not None and len(df) > 0:
            df = df.sort_values('trade_date').tail(days)
            with open(cache_file, 'wb') as f:
                pickle.dump(df, f)
            return df
        return None
    except Exception as e:
        return None


def check_stock_conditions(df):
    """
    检查个股是否符合条件：
    - MA5 > MA20
    - 量比在 0.8-3.0 之间
    - 20日涨幅 < 50%
    """
    if df is None or len(df) < 60:
        return False, "数据不足"
    
    df = df.copy()
    df['ma5'] = df['close'].rolling(5).mean()
    df['ma20'] = df['close'].rolling(20).mean()
    df['vol_ma20'] = df['vol'].rolling(20).mean()
    df['vol_ratio'] = df['vol'] / df['vol_ma20']
    df['return_20'] = df['close'].pct_change(20) * 100
    
    latest = df.iloc[-1]
    
    # 条件1: MA5 > MA20
    if pd.isna(latest['ma5']) or pd.isna(latest['ma20']) or latest['ma5'] <= latest['ma20']:
        return False, "MA5 <= MA20"
    
    # 条件2: 量比在 0.8-3.0 之间
    if pd.isna(latest['vol_ratio']) or not (0.8 < latest['vol_ratio'] < 3.0):
        return False, f"量比不符合 ({latest.get('vol_ratio', 0):.2f})"
    
    # 条件3: 20日涨幅 < 50%
    if latest.get('return_20', 0) >= 50:
        return False, f"涨幅过大 ({latest.get('return_20', 0):.1f}%)"
    
    return True, {
        'return_20': round(latest.get('return_20', 0), 2),
        'vol_ratio': round(latest.get('vol_ratio', 0), 2),
        'ma5': round(latest['ma5'], 2),
        'ma20': round(latest['ma20'], 2)
    }


def format_stock_code(stock_code):
    """
    处理股票代码格式，转成 tushare 支持的格式（如 '002025.SZ', '600519.SH'）
    """
    stock_code = str(stock_code).zfill(6)  # 补零到6位
    if stock_code.startswith('6'):
        return f"{stock_code}.SH"
    else:
        return f"{stock_code}.SZ"


def analyze_etf(etf_name, etf_code):
    """
    分析单个 ETF：获取成份股并筛选符合条件的
    """
    print(f"\n{'='*80}")
    print(f"🏷️ 分析 ETF: {etf_name} ({etf_code})")
    print(f"{'='*80}")
    
    # 1. 获取 ETF 行情数据并计算得分
    print("\n[1] 获取 ETF 行情数据...")
    etf_df = get_etf_data(etf_code)
    if etf_df is not None:
        etf_score = calculate_etf_score(etf_df, None)
        if etf_score:
            print(f"✓ ETF 综合得分: {etf_score['total_score']:.1f}, 20日涨幅: {etf_score['momentum']:+.2f}%")
    
    # 2. 获取 ETF 成份股
    print(f"\n[2] 获取 ETF 成份股...")
    holdings_df = get_etf_holdings(etf_code)
    if holdings_df is None or len(holdings_df) == 0:
        print("⚠️ 未获取到成份股数据")
        return None
    
    # 去重：按股票代码去重，保留权重最大的
    holdings_df = holdings_df.sort_values('占净值比例', ascending=False).drop_duplicates(subset=['股票代码'])
    print(f"✓ 共获取 {len(holdings_df)} 只成份股 (去重后)")
    
    # 3. 筛选符合条件的个股
    print(f"\n[3] 筛选符合条件的个股...")
    qualified = []
    
    for idx, row in holdings_df.iterrows():
        stock_code = row.get('股票代码')
        stock_name = row.get('股票名称')
        
        if not stock_code or not stock_name:
            continue
        
        # 格式化股票代码
        ts_code = format_stock_code(stock_code)
        
        # 获取行情数据
        stock_df = get_stock_data(ts_code)
        
        # 检查条件
        is_ok, result = check_stock_conditions(stock_df)
        
        if is_ok:
            qualified.append({
                'stock_code': stock_code,
                'ts_code': ts_code,
                'name': stock_name,
                'weight': row.get('占净值比例'),
                'value': row.get('持仓市值'),
                'return_20': result.get('return_20'),
                'vol_ratio': result.get('vol_ratio'),
                'ma5': result.get('ma5'),
                'ma20': result.get('ma20')
            })
        
        if (idx + 1) % 20 == 0:
            print(f"  已处理 {idx+1}/{len(holdings_df)} 只...")
        
        # 休眠避免触发限流
        time.sleep(0.15)
    
    # 4. 打印结果
    print(f"\n[4] 分析结果:")
    if len(qualified) > 0:
        print(f"\n✅ 共找到 {len(qualified)} 只符合条件的个股:")
        
        qualified_sorted = sorted(qualified, key=lambda x: x['weight'] if x['weight'] else 0, reverse=True)
        
        print(f"\n  {'排名':<4}{'代码':<10}{'名称':<10}{'权重(%)':<10}{'20日涨幅(%)':<15}{'量比':<10}{'MA5/MA20':<15}")
        print(f"  {'-'*75}")
        
        for i, item in enumerate(qualified_sorted[:15], 1):
            print(f"  {i:<4}{item['ts_code']:<10}{item['name']:<10}"
                  f"{item['weight']:<10}{item['return_20']:<15.2f}"
                  f"{item['vol_ratio']:<10.2f}"
                  f"{item['ma5']:.2f}/{item['ma20']:.2f}")
        
        return qualified_sorted
    else:
        print("\n⚠️ 没有找到符合条件的个股")
        return None


def main():
    print("="*80)
    print("🚀 基于 ETF 成份股的选股分析 (使用 akshare)")
    print("="*80)
    print(f"分析时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 选择几个热门 ETF 进行分析
    target_etfs = [
        ('AI算力', '515980'),
        ('芯片', '159995'),
        ('半导体设备', '159516'),
        ('消费电子', '159732'),
    ]
    
    all_results = {}
    for etf_name, etf_code in target_etfs:
        result = analyze_etf(etf_name, etf_code)
        if result:
            all_results[etf_name] = result
    
    # 汇总结果
    if all_results:
        print(f"\n\n{'='*80}")
        print(f"🏆 全部 ETF 分析结果汇总")
        print(f"{'='*80}")
        
        for etf_name, stocks in all_results.items():
            print(f"\n{etf_name} ({len(stocks)}只):")
            for s in stocks[:5]:
                print(f"  - {s['name']} ({s['ts_code']}), 权重: {s['weight']}%, 20日涨幅: {s['return_20']}%")
    
    print("\n\n✅ 分析完成！")


if __name__ == "__main__":
    main()
