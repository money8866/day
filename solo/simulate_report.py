#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""模拟generate_report函数的逻辑"""

import sys
import os
import numpy as np

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入必要的模块
from theme_rotation_analysis_final import (
    load_theme_portfolio_from_sqlite,
    calculate_theme_historical_rankings,
    identify_theme_leaders
)

def simulate_generate_report():
    """模拟generate_report函数的逻辑"""
    print("="*60)
    print("模拟generate_report函数")
    print("="*60)
    
    # 模拟trade_dates
    trade_dates = ['20260529']
    
    # 1. 加载主题投资组合
    print("\n1. 加载主题投资组合...")
    theme_stocks_map, name_map = load_theme_portfolio_from_sqlite()
    print(f"加载了 {len(theme_stocks_map)} 个主题，{len(name_map)} 只股票")
    
    # 2. 计算主题历史排名和平均分
    print("\n2. 计算主题历史排名和平均分...")
    theme_summary = calculate_theme_historical_rankings(theme_stocks_map, trade_dates)
    print(f"计算完成，共 {len(theme_summary)} 个主题")
    
    # 3. 计算主题评分
    print("\n3. 计算主题评分...")
    theme_scores = {}
    theme_leaders = {}
    
    for theme_name, theme_stocks in theme_stocks_map.items():
        print(f"\n处理主题: {theme_name}")
        
        leaders = identify_theme_leaders(list(theme_stocks), name_map)
        theme_leaders[theme_name] = leaders
        
        print(f"  识别到 {len(leaders)} 个龙头")
        
        if leaders:
            avg_score = np.mean([l['total_score'] for l in leaders])
            theme_scores[theme_name] = avg_score
            print(f"  平均评分: {avg_score:.1f}")
        else:
            theme_scores[theme_name] = 0
            print(f"  ⚠️ 未识别到龙头，评分设为0")
    
    # 4. 排序并显示结果
    print("\n4. 主题评分排名...")
    ranked_themes = sorted(theme_scores.items(), key=lambda x: x[1], reverse=True)
    
    for rank, (theme, score) in enumerate(ranked_themes[:10], 1):
        print(f"  {rank}. {theme}: {score:.1f}")
    
    # 5. 保存结果
    print("\n5. 保存结果...")
    import pandas as pd
    ranking_data = []
    for rank, (theme, today_score) in enumerate(ranked_themes, 1):
        summary = theme_summary.get(theme, {})
        ranking_data.append({
            '排名': rank,
            '主题': theme,
            '今日评分': round(today_score, 2),
            '近10日平均分': round(summary.get('avg_score_10d', 0), 2),
            '近10日平均排名': round(summary.get('avg_rank_10d', 0), 1),
            '趋势': summary.get('score_trend', '未知'),
            '排名变化': summary.get('rank_change', 0)
        })
    
    ranking_df = pd.DataFrame(ranking_data)
    output_file = "cache_backbone_tushare/theme_ranking_test.csv"
    ranking_df.to_csv(output_file, index=False, encoding='utf-8-sig')
    print(f"✅ 结果已保存到: {output_file}")

if __name__ == "__main__":
    simulate_generate_report()
