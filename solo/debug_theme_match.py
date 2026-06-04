#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""调试主题成分股匹配"""

import os
import sys
import json
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(BASE_DIR)
sys.path.append(parent_dir)

# 添加环境配置
os.environ["TUSHARE_TOKEN_PATH"] = os.path.join(BASE_DIR, "cache_backbone_tushare", "tk.csv")
os.environ["SAFE_CACHE_DIR"] = os.path.join(BASE_DIR, "cache_backbone_tushare")

import tushare as ts
from dotenv import load_dotenv

env_path = os.path.join(parent_dir, "config", ".env")
load_dotenv(env_path)
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# 导入主题相关模块
import theme_trend_sentiment_score as theme_score

def debug_stock_theme(stock_name):
    print(f"=" * 80)
    print(f"调试股票: {stock_name}")
    print(f"=" * 80)
    
    # 1. 加载主题配置
    print("\n[1] 加载主题配置...")
    hot_themes = theme_score.load_theme_json()
    
    # 2. 获取股票基础信息
    print("\n[2] 获取股票基础信息...")
    stock_basic = theme_score.get_stock_basic()
    
    # 查找股票代码
    target_code = None
    for _, row in stock_basic.iterrows():
        if stock_name in row.get("name", ""):
            target_code = row["ts_code"]
            print(f"找到股票: {row['name']} ({target_code})")
            print(f"  行业: {row.get('industry', 'N/A')}")
            print(f"  市场: {row.get('market', 'N/A')}")
            break
    
    if not target_code:
        print(f"未找到股票: {stock_name}")
        return
    
    # 3. 获取概念数据
    print("\n[3] 获取概念数据...")
    dc_df = theme_score.get_dc_members()
    
    # 查找该股票的概念
    stock_concepts = []
    for _, r in dc_df.iterrows():
        if r["con_code"] == target_code:
            stock_concepts.append(r["concept_name"])
    
    print(f"该股票所属概念:")
    for c in stock_concepts:
        print(f"  - {c}")
    
    # 4. 匹配主题
    print("\n[4] 检查主题匹配...")
    theme_stock_map, name_map_basic, stock_industry, stock_concepts_map = theme_score.match_theme_stocks(
        hot_themes, dc_df, stock_basic
    )
    
    # 检查该股票被匹配到哪些主题
    matched_themes = []
    for theme_name, stocks in theme_stock_map.items():
        if target_code in stocks:
            match_info = stocks[target_code]
            matched_themes.append({
                "theme": theme_name,
                "via": match_info.get("via", "N/A"),
                "industry_match": match_info.get("industry_match", False)
            })
    
    print(f"\n该股票被匹配到的主题:")
    for mt in matched_themes:
        print(f"  - {mt['theme']} (匹配方式: {mt['via']}, 行业匹配: {mt['industry_match']})")
        
        # 详细检查为什么匹配到这个主题
        cfg = hot_themes[mt['theme']]
        industry_list = cfg.get("industry", [])
        concept_list = cfg.get("concept", [])
        
        print(f"    主题行业列表: {industry_list}")
        print(f"    主题概念列表: {concept_list}")
        
        # 检查行业匹配
        stock_ind = stock_industry.get(target_code, "")
        print(f"    股票行业: {stock_ind}")
        
        # 检查概念匹配
        stock_concepts_list = stock_concepts_map.get(target_code, [])
        hit_concepts = []
        for c in concept_list:
            if c in stock_concepts_list:
                hit_concepts.append(c)
        
        if hit_concepts:
            print(f"    命中的概念: {hit_concepts}")

if __name__ == "__main__":
    debug_stock_theme("东杰智能")
