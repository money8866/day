#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import sys
import json
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOTENV_PATH = "d:/mystock/config/.env"
load_dotenv(DOTENV_PATH)

import tushare as ts
import pandas as pd
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
pro = ts.pro_api(TUSHARE_TOKEN)

# 1. 检查 theme.json
json_path = os.path.join(BASE_DIR, "..", "theme.json")
with open(json_path, 'r', encoding='utf-8') as f:
    data = json.load(f)
themes = data.get('HOT_THEMES', {})
print(f"1. 主题数量: {len(themes)}")

# 2. 检查 stock_concepts
CACHE_DIR = os.path.join(BASE_DIR, "cache_backbone_tushare")
cache_file = os.path.join(CACHE_DIR, "ths_concept_members.pkl")

if os.path.exists(cache_file):
    df = pd.read_pickle(cache_file)
    print(f"\n2. 概念成分股缓存记录数: {len(df)}")
    print(f"   列名: {df.columns.tolist()}")
else:
    print("\n2. 概念成分股缓存不存在")
    df = None

# 3. 构建 stock_concepts
stock_concepts = {}
if df is not None:
    for _, row in df.iterrows():
        ts_code = row['con_code']
        concept_name = row['concept_name']
        if ts_code not in stock_concepts:
            stock_concepts[ts_code] = []
        stock_concepts[ts_code].append(concept_name)
    print(f"   股票数量: {len(stock_concepts)}")
    print(f"   示例股票概念: {list(stock_concepts.items())[0]}")

# 4. 检查股票基础信息
print("\n3. 获取股票基础信息...")
stock_list_df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry')
print(f"   股票数量: {len(stock_list_df)}")

# 5. 检查市场数据
from datetime import datetime, timedelta
now = datetime.now()
trade_date = now.strftime('%Y%m%d') if now.hour >= 15 else (now - timedelta(days=1)).strftime('%Y%m%d')
print(f"\n4. 交易日: {trade_date}")

market_cap_df = pro.daily_basic(trade_date=trade_date, fields='ts_code,total_mv,turnover_rate')
print(f"   市场数据股票数量: {len(market_cap_df)}")

# 6. 模拟匹配逻辑
print("\n5. 模拟匹配第一个主题...")
first_theme_name = list(themes.keys())[0]
theme_data = themes[first_theme_name]
print(f"   主题名: {first_theme_name}")
print(f"   industry: {theme_data.get('industry', [])}")
print(f"   keywords: {theme_data.get('keywords', [])[:3]}...")
print(f"   concept: {theme_data.get('concept', [])}")

industry_list = theme_data.get('industry', [])
keyword_list = theme_data.get('keywords', [])
concept_list = theme_data.get('concept', [])

# 构建股票行业映射
stock_industry_dict = {}
for _, row in stock_list_df.iterrows():
    stock_industry_dict[row['ts_code']] = row.get('industry', '')

matched_stocks = set()

# 按行业匹配
for ts_code, industry in stock_industry_dict.items():
    if industry in industry_list:
        matched_stocks.add(ts_code)
print(f"\n   按行业匹配: {len(matched_stocks)} 只")

# 按关键词匹配
for ts_code, concepts in stock_concepts.items():
    concepts_str = '|'.join(concepts)
    for kw in keyword_list:
        if kw in concepts_str:
            matched_stocks.add(ts_code)
            break
print(f"   按关键词匹配后: {len(matched_stocks)} 只")

# 按概念匹配
for ts_code, concepts in stock_concepts.items():
    for c in concept_list:
        if c in concepts:
            matched_stocks.add(ts_code)
            break
print(f"   按概念匹配后: {len(matched_stocks)} 只")

# 7. 检查流动性过滤
market_cap_dict = {}
if not market_cap_df.empty:
    market_cap_dict = {row['ts_code']: row for _, row in market_cap_df.iterrows()}

filtered_stocks = []
for ts_code in matched_stocks:
    if ts_code not in market_cap_dict:
        continue
    mv = market_cap_dict[ts_code]['total_mv']
    turnover = market_cap_dict[ts_code].get('turnover_rate', 0)

    # 流动性过滤：换手率>3%
    if turnover < 3:
        continue

    filtered_stocks.append(ts_code)

print(f"\n6. 流动性过滤后(换手率>3%): {len(filtered_stocks)} 只")
print(f"   示例: {filtered_stocks[:5]}")
