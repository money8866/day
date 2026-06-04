#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""调试银行主题匹配"""

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

def debug_bank_theme():
    print(f"=" * 80)
    print(f"调试银行主题匹配")
    print(f"=" * 80)
    
    # 1. 加载主题配置
    print("\n[1] 加载主题配置...")
    hot_themes = theme_score.load_theme_json()
    
    bank_cfg = hot_themes["银行"]
    print(f"银行主题配置:")
    print(f"  行业列表: {bank_cfg.get('industry', [])}")
    print(f"  概念列表: {bank_cfg.get('concept', [])}")
    print(f"  排除关键词: {bank_cfg.get('exclude_keywords', [])}")
    
    # 2. 获取股票基础信息
    print("\n[2] 获取股票基础信息...")
    stock_basic = theme_score.get_stock_basic()
    
    # 3. 获取概念数据
    print("\n[3] 获取概念数据...")
    dc_df = theme_score.get_dc_members()
    
    # 4. 匹配主题
    print("\n[4] 匹配主题...")
    theme_stock_map, name_map_basic, stock_industry, stock_concepts_map = theme_score.match_theme_stocks(
        hot_themes, dc_df, stock_basic
    )
    
    bank_stocks = theme_stock_map["银行"]
    print(f"\n银行主题共匹配到 {len(bank_stocks)} 只股票")
    
    # 检查深南电路和华能国际
    check_stocks = ["深南电路", "华能国际"]
    for stock_name in check_stocks:
        target_code = None
        for code, name in name_map_basic.items():
            if stock_name in name:
                target_code = code
                break
        
        if target_code:
            print(f"\n--- {stock_name} ({target_code}) ---")
            print(f"  行业: {stock_industry.get(target_code, 'N/A')}")
            print(f"  概念: {stock_concepts_map.get(target_code, [])}")
            
            if target_code in bank_stocks:
                print(f"  匹配方式: {bank_stocks[target_code].get('via', 'N/A')}")
                print(f"  行业匹配: {bank_stocks[target_code].get('industry_match', False)}")

if __name__ == "__main__":
    debug_bank_theme()
