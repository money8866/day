# -*- coding: utf-8 -*-
"""详细测试 theme.json 链识别效果"""
import sys, os
sys.path.insert(0, '.')
from chain_mapping import load_theme_json, _in_industry_list, _match_keyword

# 测试一些之前链为空的股票
test_stocks = [
    ("大豪科技", "专用设备", ["消费电子", "智能机器"]),
    ("吉比特", "游戏", ["游戏", "手游"]),
    ("泰山石油", "石油贸易", ["油品改革", "天然气"]),
    ("通达电气", "汽车零部件", ["智能汽车", "车联网"]),
    ("润贝航科", "其他交运设备", ["大飞机", "民航"]),
    ("科创新材", "新材料", ["新材料", "稀土"]),
    ("赛分科技", "专用设备", ["半导体", "芯片"]),
]

hot_themes = load_theme_json()

print("详细匹配分析")
print("=" * 90)
for name, industry, concepts in test_stocks:
    print(f"\n【{name}】行业={industry} 概念={concepts}")
    search_text = f"{name} {industry} {' '.join(concepts)}"
    matched = []
    for theme_name, cfg in hot_themes.items():
        industry_list = cfg.get("industry", [])
        concept_list = cfg.get("concept", [])
        keyword_list = cfg.get("keywords", [])
        
        ind_match = _in_industry_list(industry, industry_list) if industry_list else False
        conc_match = any(c in concept_list for c in concepts) if concept_list and concepts else False
        kw_match = _match_keyword(search_text, keyword_list) if keyword_list else False
        
        if ind_match or conc_match or kw_match:
            matched.append((theme_name, ind_match, conc_match, kw_match))
    
    if matched:
        for theme_name, ind_match, conc_match, kw_match in matched:
            print(f"  → {theme_name:<20} 行业匹配={ind_match} 概念匹配={conc_match} 关键词匹配={kw_match}")
    else:
        print("  → 无匹配主题")
