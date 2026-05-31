#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""详细诊断主题股票筛选过程"""
import os, sys, json
import pandas as pd
import tushare as ts
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")

def get_last_trade_date():
    from datetime import datetime, timedelta
    now = datetime.now()
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        query_date = now.strftime('%Y%m%d')
    cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
    cal = cal[cal['is_open'] == 1]
    return str(cal[cal['cal_date'] <= query_date]['cal_date'].max())

TRADE_DATE = get_last_trade_date()
print(f"交易日: {TRADE_DATE}")

# 加载 theme.json
json_path = os.path.join(BASE_DIR, "..", "theme.json")
with open(json_path, 'r', encoding='utf-8') as f:
    data = json.load(f)
themes = data.get('HOT_THEMES', {})

# 加载概念缓存
cache_file = os.path.join(CACHE_DIR, "ths_concept_members.pkl")
df = pd.read_pickle(cache_file)
stock_concepts = {}
for _, row in df.iterrows():
    ts_code = row['con_code']
    concept_name = row['concept_name']
    if ts_code not in stock_concepts:
        stock_concepts[ts_code] = []
    stock_concepts[ts_code].append(concept_name)
print(f"有概念的股票数: {len(stock_concepts)}")

# 获取股票基础信息
stock_df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry')
stock_industry_dict = {}
for _, row in stock_df.iterrows():
    if pd.notna(row['industry']):
        stock_industry_dict[row['ts_code']] = row['industry']
print(f"有行业的股票数: {len(stock_industry_dict)}")

# 获取市场数据
market_cap_df = pro.daily_basic(trade_date=TRADE_DATE, fields='ts_code,total_mv,turnover_rate')
market_cap_dict = {row['ts_code']: row for _, row in market_cap_df.iterrows()}
print(f"有市场数据的股票数: {len(market_cap_dict)}")

# 获取日线数据（成交额）
from datetime import timedelta
start_date = (pd.to_datetime(TRADE_DATE) - pd.Timedelta(days=20)).strftime('%Y%m%d')
daily_df = pro.daily(ts_code=','.join(list(market_cap_dict.keys())[:100]), start_date=start_date, end_date=TRADE_DATE)
stock_amount_dict = {}
for ts_code, group in daily_df.groupby('ts_code'):
    stock_amount_dict[ts_code] = group['amount'].head(5).mean() / 100000000
print(f"有日线数据的股票数: {len(stock_amount_dict)}")

# 测试主题
theme_name = "AI算力"
theme_data = themes[theme_name]
industry_list = theme_data.get('industry', [])
keyword_list = theme_data.get('keywords', [])
concept_list = theme_data.get('concept', [])

print(f"\n测试主题: {theme_name}")
print(f"  industry: {industry_list}")
print(f"  keywords: {keyword_list[:5]}...")
print(f"  concept: {concept_list[:5]}...")

# 匹配股票
matched_stocks = set()
for ts_code, concepts in stock_concepts.items():
    concepts_str = '|'.join(concepts)
    for kw in keyword_list:
        if kw in concepts_str:
            matched_stocks.add(ts_code)
            break
    for c in concept_list:
        if c in concepts:
            matched_stocks.add(ts_code)
            break

print(f"\n初步匹配: {len(matched_stocks)} 只股票")

# 详细检查前10只匹配股票
print("\n前10只匹配股票详情:")
sample_stocks = list(matched_stocks)[:10]
for ts_code in sample_stocks:
    concepts = stock_concepts.get(ts_code, [])
    concepts_str = '|'.join(concepts)
    matched_kws = [kw for kw in keyword_list if kw in concepts_str]
    matched_cons = [c for c in concept_list if c in concepts]
    print(f"  {ts_code}: 匹配keywords={matched_kws[:3]}, 匹配concept={matched_cons}")

# 流动性筛选
print("\n流动性筛选检查:")
filtered = []
for ts_code in matched_stocks:
    if ts_code not in market_cap_dict:
        print(f"  {ts_code}: 无市场数据 (market_cap_dict)")
        continue
    mv = market_cap_dict[ts_code]['total_mv']
    turnover = market_cap_dict[ts_code].get('turnover_rate', 0)
    amount = stock_amount_dict.get(ts_code, 0)
    if turnover < 3:
        print(f"  {ts_code}: 换手率{turnover:.2f}% < 3%")
        continue
    if amount < 0.5:
        print(f"  {ts_code}: 成交额{amount:.2f}亿 < 0.5亿")
        continue
    filtered.append({'ts_code': ts_code, 'mcap': mv/10000, 'turnover': turnover, 'amount': amount})

print(f"\n流动性筛选后: {len(filtered)} 只")

# 检查market_cap_df的数据结构
print("\n检查 market_cap_df 数据结构:")
print(market_cap_df.head())
print(f"\n列名: {list(market_cap_df.columns)}")