#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
获取2026年5月22日真实数据的主线板块 + 中军分析
使用 mystock 目录下的缓存数据
"""
import os, sys, pickle, warnings, time
import numpy as np
import pandas as pd
import tushare as ts

warnings.filterwarnings('ignore')

# 配置
PYTHON_PATH = r"C:\Users\kongx\AppData\Local\Python\pythoncore-3.14-64\python.exe"
TUSHARE_TOKEN = 'bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d'
pro = ts.pro_api(TUSHARE_TOKEN)

TDX_PATH = r"C:\new_tdx\vipdoc"
TRADE_DATE = "20260522"
BASE_DIR = r"c:\Users\kongx\mystock"
CACHE_DIR = r"c:\Users\kongx\mystock\solo\cache_backbone_tdx"
os.makedirs(CACHE_DIR, exist_ok=True)


def parse_tdx_day_file(filepath):
    """解析通达信 .day 文件"""
    if not os.path.exists(filepath):
        return None
    data = []
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32:
                break
            date_int = int.from_bytes(chunk[0:4], "little")
            open_p = int.from_bytes(chunk[4:8], "little") / 100
            high_p = int.from_bytes(chunk[8:12], "little") / 100
            low_p = int.from_bytes(chunk[12:16], "little") / 100
            close_p = int.from_bytes(chunk[16:20], "little") / 100
            volume = int.from_bytes(chunk[20:24], "little")
            amount = int.from_bytes(chunk[24:28], "little") / 100.0
            dt = pd.to_datetime(str(date_int), format='%Y%m%d')
            data.append({
                "date": dt, "date_int": date_int,
                "open": open_p, "high": high_p, "low": low_p,
                "close": close_p, "vol": volume, "amount": amount
            })
    return data


def get_tdx_kline(ts_code, end_date_str, n_days=120):
    """读取TDX日线数据"""
    code = ts_code.split('.')[0]
    market = ts_code.split('.')[1].lower()
    subdir = "lday"
    filename = f"{market}{code}.day"
    filepath = os.path.join(TDX_PATH, market, subdir, filename)
    raw = parse_tdx_day_file(filepath)
    if not raw:
        return None
    end_dt = pd.to_datetime(end_date_str, format='%Y%m%d')
    filtered = [r for r in raw if r['date'] <= end_dt]
    if len(filtered) < 60:
        return None
    recent = filtered[-n_days:]
    df = pd.DataFrame(recent)
    df['pct_chg'] = df['close'].pct_change() * 100
    return df


def get_concept_map():
    """从缓存数据获取概念板块映射"""
    print("\n[2/4] 从缓存数据获取概念板块映射...")
    concept_detail_path = os.path.join(BASE_DIR, "cache_daily", "ths_concept_detail.pkl")
    
    if not os.path.exists(concept_detail_path):
        print(f"   缓存文件不存在: {concept_detail_path}")
        return {}
    
    try:
        df = pd.read_pickle(concept_detail_path)
        print(f"   加载成功，共 {len(df)} 条记录")
        
        # 构建概念->股票列表映射
        concept_map = {}
        for concept_name, group in df.groupby('concept_name'):
            stocks = group['con_code'].tolist()
            if len(stocks) >= 10:  # 只保留成分股>=10的概念
                concept_map[concept_name] = stocks
        
        print(f"   找到 {len(concept_map)} 个有效概念板块")
        return concept_map
    except Exception as e:
        print(f"   加载失败: {e}")
        return {}


def get_real_data():
    """获取真实数据"""
    print("=" * 80)
    print("正在获取 2026年5月22日 真实数据...")
    print("=" * 80)
    
    # 1. 获取市值数据
    print("\n[1/4] 获取市值和成交数据...")
    try:
        daily_basic = pro.daily_basic(trade_date=TRADE_DATE, 
                                       fields='ts_code,total_mv,circ_mv,amount,turnover_rate')
        print(f"   获取 {len(daily_basic)} 只股票的市值数据")
    except Exception as e:
        print(f"   获取失败: {e}")
        daily_basic = pd.DataFrame()
    
    # 2. 获取概念板块映射（从缓存）
    concept_map = get_concept_map()
    
    # 3. 获取日线数据（使用TDX）
    print("\n[3/4] 获取日线数据（通达信）...")
    
    return daily_basic, concept_map


def analyze_sectors(daily_basic, concept_map, trade_date):
    """分析板块"""
    print("\n" + "=" * 80)
    print("【主线板块分析】")
    print("=" * 80)
    
    sector_results = []
    
    # 创建市值查询字典
    market_cap_dict = {}
    if not daily_basic.empty:
        for _, row in daily_basic.iterrows():
            market_cap_dict[row['ts_code']] = row
    
    analyzed_count = 0
    for sector_name, stocks in concept_map.items():
        if len(stocks) < 10 or analyzed_count >= 30:  # 分析前30个板块
            continue
        
        analyzed_count += 1
        
        # 获取板块成分股的日线数据
        sector_stocks_data = []
        stock_count = 0
        for stock_code in stocks:
            if stock_count >= 30:  # 每板块最多取30只
                break
            df = get_tdx_kline(stock_code, trade_date, 30)
            if df is not None and len(df) > 5:
                sector_stocks_data.append(df)
                stock_count += 1
        
        if len(sector_stocks_data) < 5:
            continue
        
        # 计算板块动量和涨停数
        total_momentum = 0
        zt_count = 0
        total_amount = 0
        
        for df in sector_stocks_data:
            if len(df) >= 2:
                pct = df['pct_chg'].iloc[-1]
                total_momentum += pct
                if pct >= 9.5:
                    zt_count += 1
                # 获取成交金额
                if not df.empty:
                    last_row = df.iloc[-1]
                    if 'amount' in last_row:
                        total_amount += last_row['amount']
        
        avg_momentum = total_momentum / len(sector_stocks_data) if sector_stocks_data else 0
        avg_amount = total_amount / len(sector_stocks_data) if sector_stocks_data else 0
        
        sector_results.append({
            '板块名称': sector_name,
            '板块类型': '概念',
            '成分股数': len(stocks),
            '分析股数': len(sector_stocks_data),
            '平均涨幅': round(avg_momentum, 2),
            '涨停数': zt_count,
            '动量': round(avg_momentum * 10, 2),
            '平均成交额(万)': round(avg_amount / 10000, 2) if avg_amount else 0
        })
    
    # 排序
    sector_results = sorted(sector_results, key=lambda x: x['动量'], reverse=True)
    
    return sector_results[:15]


def main():
    print("\n" + "=" * 80)
    print("  2026年5月22日 主线板块 + 中军分析（真实数据）")
    print("=" * 80)
    
    # 获取真实数据
    daily_basic, concept_map = get_real_data()
    
    # 分析板块
    sectors = analyze_sectors(daily_basic, concept_map, TRADE_DATE)
    
    print("\n主线板块（前15名）:")
    print("-" * 100)
    print(f"{'排名':^4} | {'板块名称':^20} | {'类型':^4} | {'成分':^5} | {'分析':^5} | {'均涨':^8} | {'涨停':^5} | {'动量':^8}")
    print("-" * 100)
    for i, s in enumerate(sectors[:15], 1):
        print(f"{i:^4} | {s['板块名称']:^20s} | {s['板块类型']:^4} | {s['成分股数']:^5d} | {s['分析股数']:^5d} | {s['平均涨幅']:^8.2f}% | {s['涨停数']:^5d} | {s['动量']:^8.2f}")
    
    # 保存结果
    result_df = pd.DataFrame(sectors[:15])
    result_file = os.path.join(CACHE_DIR, f"sectors_{TRADE_DATE}.csv")
    result_df.to_csv(result_file, index=False, encoding='utf-8-sig')
    print(f"\n结果已保存至: {result_file}")
    
    return result_df


if __name__ == "__main__":
    main()
