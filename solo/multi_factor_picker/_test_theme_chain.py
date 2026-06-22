# -*- coding: utf-8 -*-
"""测试 theme.json 链识别效果"""
import sys, os
sys.path.insert(0, '.')
from chain_mapping import identify_stock_chain_v3, load_theme_json

# 测试一些之前链为空的股票
test_stocks = [
    ("大豪科技", "专用设备", ["消费电子", "智能机器"]),
    ("吉比特", "游戏", ["游戏", "手游"]),
    ("泰山石油", "石油贸易", ["油品改革", "天然气"]),
    ("通达电气", "汽车零部件", ["智能汽车", "车联网"]),
    ("润贝航科", "其他交运设备", ["大飞机", "民航"]),
    ("科创新材", "新材料", ["新材料", "稀土"]),
    ("赛分科技", "专用设备", ["半导体", "芯片"]),
    # 验证核心公司白名单
    ("中际旭创", "通信设备", ["光模块", "CPO"]),
    ("新易盛", "通信设备", ["光模块"]),
    ("寒武纪", "半导体", ["AI芯片", "国产芯片"]),
    # 验证排除逻辑
    ("浪潮信息", "计算机设备", ["AI服务器", "算力"]),
]

print("theme.json 链识别测试")
print("=" * 70)
for name, industry, concepts in test_stocks:
    chain = identify_stock_chain_v3(name, industry, concepts)
    print(f"{name:<8} 行业={industry:<12} 概念={', '.join(concepts):<20} → 链={chain or '空'}")

# 统计 theme.json 配置
hot_themes = load_theme_json()
print(f"\n\ntheme.json 配置统计")
print("=" * 70)
print(f"主题数量: {len(hot_themes)}")
for theme_name, cfg in hot_themes.items():
    industry_list = cfg.get("industry", [])
    concept_list = cfg.get("concept", [])
    keyword_list = cfg.get("keywords", [])
    core_companies = cfg.get("core_companies", [])
    print(f"  {theme_name:<20} 行业={len(industry_list)} 概念={len(concept_list)} 关键词={len(keyword_list)} 核心公司={len(core_companies)}")
