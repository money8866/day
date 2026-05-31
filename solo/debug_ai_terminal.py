#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断AI终端主题筛选问题"""
import os, json
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

# 加载theme.json
json_path = os.path.join(BASE_DIR, "..", "theme.json")
with open(json_path, 'r', encoding='utf-8') as f:
    data = json.load(f)
themes = data.get('HOT_THEMES', {})

# 加载概念缓存
cache_file = os.path.join(CACHE_DIR, "ths_concept_members.pkl")
df = pd.read_pickle(cache_file)

# 构建股票概念映射
stock_concepts = {}
for _, row in df.iterrows():
    ts_code = row['con_code']
    concept_name = row['concept_name']
    if ts_code not in stock_concepts:
        stock_concepts[ts_code] = []
    stock_concepts[ts_code].append(concept_name)

# 获取股票基础信息
stock_df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry')
stock_industry_dict = {row['ts_code']: row['industry'] for _, row in stock_df.iterrows() if pd.notna(row['industry'])}

# 获取市场数据
market_cap_df = pro.daily_basic(trade_date=TRADE_DATE, fields='ts_code,total_mv,turnover_rate')
market_cap_dict = {row['ts_code']: row for _, row in market_cap_df.iterrows()}

# 获取日线数据（成交额)
start_date = (pd.to_datetime(TRADE_DATE) - timedelta(days=20)).strftime('%Y%m%d')
daily_df = pro.daily(trade_date=TRADE_DATE, fields='ts_code,amount')
stock_amount_dict = {}
for ts_code, group in daily_df.groupby('ts_code'):
    stock_amount_dict[ts_code] = group['amount'].head(5).mean() / 100000000

# 分析AI终端主题
theme_name = "AI终端"
theme_data = themes[theme_name]
keyword_list = theme_data.get('keywords', [])
concept_list = theme_data.get('concept', [])
industry_list = theme_data.get('industry', [])

print(f"主题: {theme_name}")
print(f"  keywords: {keyword_list}")
print(f"  concept: {concept_list}")
print(f"  industry: {industry_list}")

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

print(f"\n初步匹配: {len(matched_stocks)} 只")

# 详细检查每只股票被过滤的原因
print("\n逐只检查流动性:")
filtered = []
no_data = 0
low_turnover = 0
low_amount = 0
weak_stock = 0

for ts_code in list(matched_stocks)[:20]:
    if ts_code not in market_cap_dict:
        no_data += 1
        continue
    mv = market_cap_dict[ts_code]['total_mv']
    turnover = market_cap_dict[ts_code].get('turnover_rate', 0)
    amount = stock_amount_dict.get(ts_code, 0)

    if turnover < 2:
        low_turnover += 1
        continue
    if amount < 0.1:
        low_amount += 1
        continue

    filtered.append({'ts_code': ts_code, 'mcap': mv/10000, 'turnover': turnover, 'amount': amount})

print(f"  无市场数据: {no_data}")
print(f"  换手率<2%: {low_turnover}")
print(f"  成交额<0.1亿: {low_amount}")
print(f"  流动性通过: {len(filtered)} 只")

if filtered:
    print("\n流动性通过的股票:")
    for s in filtered:
        print(f"  {s['ts_code']}: 换手率={s['turnover']:.2f}%, 成交额={s['amount']:.4f}亿")