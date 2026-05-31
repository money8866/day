#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""检查主题股票筛选问题"""
import os, sys, json, time, glob, pickle
import pandas as pd
import tushare as ts
from datetime import datetime, timedelta
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")

# 交易日
now = datetime.now()
query_date = now.strftime('%Y%m%d') if now.hour >= 15 else (now - timedelta(days=1)).strftime('%Y%m%d')
cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
cal = cal[cal['is_open'] == 1]
TRADE_DATE = str(cal[cal['cal_date'] <= query_date]['cal_date'].max())
print(f"交易日: {TRADE_DATE}")

# 获取匹配AI算力的股票
cache_file = os.path.join(CACHE_DIR, "ths_concept_members.pkl")
df = pd.read_pickle(cache_file)
stock_concepts = {}
for _, row in df.iterrows():
    ts_code = row['con_code']
    concept_name = row['concept_name']
    if ts_code not in stock_concepts:
        stock_concepts[ts_code] = []
    stock_concepts[ts_code].append(concept_name)

keyword_list = ['GPU', '算力', 'AI服务器', '液冷', 'CPO', '800G', '1.6T', '光模块', '数据中心', 'HBM']
matched_stocks = set()
for ts_code, concepts in stock_concepts.items():
    concepts_str = '|'.join(concepts)
    for kw in keyword_list:
        if kw in concepts_str:
            matched_stocks.add(ts_code)
            break

print(f"AI算力匹配股票数: {len(matched_stocks)}")

# 获取市场数据
market_cap_df = pro.daily_basic(trade_date=TRADE_DATE, fields='ts_code,total_mv,turnover_rate')
market_cap_dict = {row['ts_code']: row for _, row in market_cap_df.iterrows()}
print(f"有市场数据的股票数: {len(market_cap_dict)}")

# 检查匹配的股票是否在market_cap_dict中
in_market_cap = matched_stocks & set(market_cap_dict.keys())
print(f"匹配股票中有市场数据的: {len(in_market_cap)}")

# 批量获取日线数据
start_date = (pd.to_datetime(TRADE_DATE) - timedelta(days=40)).strftime('%Y%m%d')
matched_list = list(matched_stocks)
print(f"开始获取 {len(matched_list)} 只股票的日线数据...")

all_dfs = []
for i in range(0, len(matched_list), 500):
    batch_codes = matched_list[i:i+500]
    try:
        df_batch = pro.daily(ts_code=','.join(batch_codes), start_date=start_date, end_date=TRADE_DATE)
        if not df_batch.empty:
            all_dfs.append(df_batch)
            print(f"  批次 {i//500+1}: 获取 {len(df_batch)} 条")
        else:
            print(f"  批次 {i//500+1}: 无数据")
        time.sleep(0.1)
    except Exception as e:
        print(f"  批次 {i//500+1}: 错误 {e}")

if all_dfs:
    daily_df = pd.concat(all_dfs, ignore_index=True)
    print(f"总获取日线数据: {len(daily_df)} 条")
else:
    daily_df = pd.DataFrame()
    print("未获取到任何日线数据")

# 计算成交额
stock_amount_dict = {}
if not daily_df.empty:
    for ts_code, group in daily_df.groupby('ts_code'):
        group = group.sort_values('trade_date', ascending=False)
        stock_amount_dict[ts_code] = group['amount'].head(5).mean() / 100000000
    print(f"计算成交额后: {len(stock_amount_dict)} 只股票有数据")

    # 检查几个AI算力股票的成交额
    sample = list(matched_stocks)[:5]
    print("\n示例股票成交额:")
    for s in sample:
        amt = stock_amount_dict.get(s, None)
        if amt is not None:
            print(f"  {s}: amount={amt:.4f}亿")
        else:
            print(f"  {s}: 无数据")

# 检查流动性筛选
print("\n流动性筛选检查:")
pass_count = 0
fail_reasons = {}
for ts_code in matched_stocks:
    if ts_code not in market_cap_dict:
        fail_reasons['无市场数据'] = fail_reasons.get('无市场数据', 0) + 1
        continue
    mv = market_cap_dict[ts_code]['total_mv']
    turnover = market_cap_dict[ts_code].get('turnover_rate', 0)
    amount = stock_amount_dict.get(ts_code, 0)
    if turnover < 3:
        fail_reasons['换手率<3%'] = fail_reasons.get('换手率<3%', 0) + 1
        continue
    if amount < 0.5:
        fail_reasons['成交额<0.5亿'] = fail_reasons.get('成交额<0.5亿', 0) + 1
        continue
    pass_count += 1

print(f"通过筛选: {pass_count} 只")
for reason, count in fail_reasons.items():
    print(f"  {reason}: {count} 只")