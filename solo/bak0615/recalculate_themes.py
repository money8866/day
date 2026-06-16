#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""重新计算20260529的主题评分"""
import sys
import os
import pandas as pd
import numpy as np
from pprint import pprint

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from theme_rotation_analysis_final import (
    load_theme_portfolio_from_sqlite,
    identify_theme_leaders,
    get_trade_dates,
    get_stock_history,
    pro
)

print("=" * 80)
print(" 重新计算 20260529 的主题评分")
print("=" * 80)
print()

# 1. 加载主题投资组合
theme_stocks_map, name_map = load_theme_portfolio_from_sqlite()

# 2. 手动清理缓存
import pickle
cache_dir = "cache_backbone_tushare"
if os.path.exists(cache_dir):
    for filename in os.listdir(cache_dir):
        if "cache_daily" in filename or "cache_stock" in filename:
            try:
                os.remove(os.path.join(cache_dir, filename))
            except:
                pass

# 3. 逐个计算主题评分
print("开始逐个计算主题评分...")
theme_scores = {}
all_leaders = {}

for idx, (theme_name, stock_codes) in enumerate(theme_stocks_map.items(), 1):
    print(f"\n[{idx}/{len(theme_stocks_map)}] 处理主题: {theme_name}")
    
    # 直接用原始的 identify_theme_leaders 函数
    leaders = identify_theme_leaders(list(stock_codes), name_map)
    
    all_leaders[theme_name] = leaders
    
    if leaders:
        score = np.mean([l['total_score'] for l in leaders])
        theme_scores[theme_name] = score
        print(f"  → 平均评分: {score:.1f}")
        print(f"  → 龙头数: {len(leaders)}")
        
        if leaders:
            top = leaders[0]
            print(f"  → 最强龙头: {top['name']} ({top['ts_code']}), 评分: {top['total_score']:.1f}")
    else:
        theme_scores[theme_name] = 0.0
        print(f"  → 无有效龙头！")

# 4. 排序输出
sorted_themes = sorted(theme_scores.items(), key=lambda x: x[1], reverse=True)
print("\n" + "=" * 80)
print(" 今日主题排名（TOP 10）")
print("=" * 80)
for idx, (theme, score) in enumerate(sorted_themes[:10], 1):
    print(f"{idx}. {theme}: {score:.1f}")
    leaders = all_leaders[theme]
    if leaders:
        top = leaders[0]
        print(f"   最强龙头: {top['name']} ({top['ts_code']}), 评分: {top['total_score']:.1f}")
        print(f"   5日涨幅: {top['change_5']:.1f}%, 20日涨幅: {top['change_20']:.1f}%")

print("\n" + "=" * 80)
print("保存结果到 CSV")
print("=" * 80)

result_df = pd.DataFrame([
    {
        "排名": idx+1,
        "主题": theme,
        "今日评分": round(score, 1)
    } for idx, (theme, score) in enumerate(sorted_themes)
])
result_df.to_csv("cache_backbone_tushare/theme_ranking_test.csv", index=False, encoding="utf-8-sig")
print("✓ 已保存到 cache_backbone_tushare/theme_ranking_test.csv")
