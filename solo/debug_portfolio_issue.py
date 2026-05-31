#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断脚本：检查主题投资组合构建问题"""
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

print("=" * 60)
print("诊断：未生成有效投资组合问题")
print("=" * 60)

# 1. 检查 theme.json
json_path = os.path.join(BASE_DIR, "..", "theme.json")
print(f"\n[1] theme.json 路径: {json_path}")
print(f"    文件存在: {os.path.exists(json_path)}")

with open(json_path, 'r', encoding='utf-8') as f:
    data = json.load(f)
themes = data.get('HOT_THEMES', {})
print(f"    热点主题数量: {len(themes)}")
for name, cfg in themes.items():
    print(f"    - {name}:")
    print(f"        industry: {cfg.get('industry', [])}")
    print(f"        keywords: {cfg.get('keywords', [])}")
    print(f"        concept:  {cfg.get('concept', [])}")

# 2. 检查概念缓存
print(f"\n[2] 概念缓存检查")
cache_file = os.path.join(CACHE_DIR, "ths_concept_members.pkl")
print(f"    缓存路径: {cache_file}")
print(f"    缓存存在: {os.path.exists(cache_file)}")

if os.path.exists(cache_file):
    df = pd.read_pickle(cache_file)
    print(f"    缓存记录数: {len(df)}")
    print(f"    列名: {list(df.columns)}")
    if not df.empty:
        print(f"    示例:\n{df.head(2).to_string()}")

# 3. 检查股票基础信息
print(f"\n[3] 股票基础信息")
stock_df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name,industry')
print(f"    股票总数: {len(stock_df)}")
print(f"    有行业信息的股票: {stock_df['industry'].notna().sum()}")
print(f"    行业分布Top10:")
print(stock_df['industry'].value_counts().head(10).to_string())

# 4. 检查主题匹配情况
print(f"\n[4] 主题匹配测试")

# 构建股票行业字典
stock_industry_dict = {}
if not stock_df.empty:
    for _, row in stock_df.iterrows():
        if pd.notna(row['industry']):
            stock_industry_dict[row['ts_code']] = row['industry']

# 检查概念数据
if os.path.exists(cache_file):
    concept_df = pd.read_pickle(cache_file)
    stock_concepts = {}
    for _, row in concept_df.iterrows():
        ts_code = row['con_code']
        concept_name = row['concept_name']
        if ts_code not in stock_concepts:
            stock_concepts[ts_code] = []
        stock_concepts[ts_code].append(concept_name)
    print(f"    有概念的股票数: {len(stock_concepts)}")
else:
    stock_concepts = {}
    print(f"    无概念缓存数据")

# 按主题测试匹配
for theme_name, theme_data in list(themes.items())[:3]:
    industry_list = theme_data.get('industry', [])
    keyword_list = theme_data.get('keywords', [])
    concept_list = theme_data.get('concept', [])

    matched_by_industry = set()
    matched_by_keyword = set()
    matched_by_concept = set()

    # 按行业匹配
    for ts_code, industry in stock_industry_dict.items():
        if industry in industry_list:
            matched_by_industry.add(ts_code)

    # 按概念关键词匹配
    for ts_code, concepts in stock_concepts.items():
        concepts_str = '|'.join(concepts)
        for kw in keyword_list:
            if kw in concepts_str:
                matched_by_keyword.add(ts_code)
                break
        for c in concept_list:
            if c in concepts:
                matched_by_concept.add(ts_code)
                break

    all_matched = matched_by_industry | matched_by_keyword | matched_by_concept
    print(f"\n    主题 '{theme_name}':")
    print(f"        行业匹配: {len(matched_by_industry)} 只")
    print(f"        关键词匹配: {len(matched_by_keyword)} 只")
    print(f"        概念匹配: {len(matched_by_concept)} 只")
    print(f"        合计: {len(all_matched)} 只")

# 5. 检查市场数据
print(f"\n[5] 市场数据检查")
from datetime import datetime, timedelta
now = datetime.now()
if now.hour < 15:
    query_date = (now - timedelta(days=1)).strftime('%Y%m%d')
else:
    query_date = now.strftime('%Y%m%d')

cal = pro.trade_cal(exchange='', start_date='20200101', end_date=query_date)
cal = cal[cal['is_open'] == 1]
trade_date = str(cal[cal['cal_date'] <= query_date]['cal_date'].max())
print(f"    当前日期: {now.strftime('%Y%m%d')}")
print(f"    计算交易日: {trade_date}")

market_cap_df = pro.daily_basic(trade_date=trade_date, fields='ts_code,total_mv,turnover_rate')
print(f"    市场数据记录数: {len(market_cap_df)}")
if market_cap_df.empty:
    print(f"    警告: 市场数据为空！可能是交易日不正确或数据未更新")

print("\n" + "=" * 60)