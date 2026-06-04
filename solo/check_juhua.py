#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查巨化股份的主题归类情况"""

import os
import sys
import pandas as pd
from datetime import datetime

# 添加项目路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import theme_trend_sentiment_score as theme_score

# 设置目标日期
TRADE_DATE = "20260604"

print("=" * 80)
print(f"检查巨化股份 (600160.SH) 的主题归类情况 - {TRADE_DATE}")
print("=" * 80)

# 1. 加载主题配置
hot_themes = theme_score.load_theme_json()
print(f"\n[1/4] 加载了 {len(hot_themes)} 个主题配置")

# 2. 加载数据
dc_df = theme_score.get_dc_members()
print(f"[2/4] 加载了 {len(dc_df)} 条东财板块成份股记录")

stock_basic_df = theme_score.get_stock_basic()
print(f"[3/4] 加载了 {len(stock_basic_df)} 条股票基础信息")

# 3. 匹配主题
theme_stock_map, name_map_basic, stock_industry, stock_concepts = theme_score.match_theme_stocks(
    hot_themes, dc_df, stock_basic_df
)
print(f"[4/4] 完成主题匹配")

# 查找巨化股份
juhua_code = "600160.SH"
print(f"\n" + "=" * 80)
print(f"分析巨化股份 ({juhua_code})")
print("=" * 80)

# 查看巨化股份的基础信息
stock_info = stock_basic_df[stock_basic_df['ts_code'] == juhua_code].iloc[0] if len(stock_basic_df[stock_basic_df['ts_code'] == juhua_code]) > 0 else None
if stock_info is not None:
    print(f"\n名称: {stock_info.get('name', '')}")
    print(f"行业: {stock_info.get('industry', '')}")
    print(f"市场: {stock_info.get('market', '')}")

# 查看巨化股份的概念标签
juhua_concepts = stock_concepts.get(juhua_code, [])
print(f"\n概念标签: {juhua_concepts}")

# 查看巨化股份被归类到哪些主题
print(f"\n所属主题:")
for theme_name, matched in theme_stock_map.items():
    if juhua_code in matched:
        info = matched[juhua_code]
        print(f"  - {theme_name} ({info['via']})")

# 特别查看半导体主题的匹配情况
print(f"\n" + "=" * 80)
print(f"半导体主题匹配详情")
print("=" * 80)

semiconductor_cfg = hot_themes.get("半导体", {})
print(f"\n半导体主题配置:")
print(f"  行业: {semiconductor_cfg.get('industry', [])}")
print(f"  概念: {semiconductor_cfg.get('concept', [])}")

# 检查巨化股份是否在半导体主题中
if "半导体" in theme_stock_map:
    if juhua_code in theme_stock_map["半导体"]:
        info = theme_stock_map["半导体"][juhua_code]
        print(f"\n巨化股份在半导体主题中: {info['via']}")
        
        # 检查具体的匹配原因
        if info['industry_match']:
            # 检查行业匹配
            ind = stock_industry.get(juhua_code, "")
            print(f"  行业匹配: {ind}")
            
            # 检查行业是否在配置列表中
            for industry in semiconductor_cfg.get('industry', []):
                if industry in ind:
                    print(f"    匹配到: {industry}")
        
        # 检查概念匹配
        concepts = stock_concepts.get(juhua_code, [])
        print(f"\n  概念匹配:")
        for concept in semiconductor_cfg.get('concept', []):
            if concept in concepts:
                print(f"    匹配到: {concept}")
    else:
        print(f"\n巨化股份不在半导体主题中")
