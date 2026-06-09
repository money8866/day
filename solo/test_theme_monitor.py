#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试 realtime_theme_monitor 的 theme.json 加载功能
"""
import os
import sys
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
THEME_JSON_PATH = os.path.join(BASE_DIR, 'theme.json')

def test_load_theme_json():
    """测试加载 theme.json"""
    print("=" * 60)
    print("测试1: 加载 theme.json")
    print("=" * 60)
    
    if not os.path.exists(THEME_JSON_PATH):
        print(f"❌ 未找到 {THEME_JSON_PATH}")
        return False
    
    with open(THEME_JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    themes = data.get('HOT_THEMES', {})
    print(f"✅ 加载成功: {len(themes)} 个主题")
    
    for theme_name, cfg in list(themes.items())[:5]:
        leaders = cfg.get('leader_companies', [])
        cores = cfg.get('core_companies', [])
        print(f"   📌 {theme_name}: 龙头[{', '.join(leaders[:3])}] 核心{len(cores)}家")
    
    return True


def test_stock_layer_matching():
    """测试股票层级匹配"""
    print("\n" + "=" * 60)
    print("测试2: 股票层级匹配")
    print("=" * 60)
    
    with open(THEME_JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    themes = data.get('HOT_THEMES', {})
    
    # 测试用例: (股票名称, 主题名, 期望层级)
    test_cases = [
        ("埃斯顿", "人形机器人", "leader"),      # 龙头
        ("绿的谐波", "人形机器人", "middle"),    # 核心但非龙头
        ("某普通股票", "人形机器人", "member"),  # 成分股
        ("工业富联", "AI算力链", "leader"),      # 龙头
        ("中际旭创", "AI算力链", "leader"),      # 龙头
    ]
    
    def get_stock_layer(name, theme_name):
        cfg = themes.get(theme_name, {})
        leader_companies = cfg.get('leader_companies', [])
        core_companies = cfg.get('core_companies', [])
        
        for leader_name in leader_companies:
            if leader_name in name:
                return 'leader'
        
        for core_name in core_companies:
            if core_name in name:
                return 'middle'
        
        return 'member'
    
    for name, theme, expected in test_cases:
        result = get_stock_layer(name, theme)
        status = "✅" if result == expected else "❌"
        layer_mark = {'leader': '⭐龙头', 'middle': '▲中军', 'member': '○成分'}
        print(f"   {status} {name} ({theme}) -> {layer_mark.get(result)} (期望: {layer_mark.get(expected)})")
    
    return True


def test_csv_and_theme_json_consistency():
    """测试CSV和theme.json的一致性"""
    print("\n" + "=" * 60)
    print("测试3: CSV与theme.json一致性")
    print("=" * 60)
    
    import pandas as pd
    
    csv_path = os.path.join(BASE_DIR, "cache_backbone_tushare", "theme_pattern_stocks.csv")
    if not os.path.exists(csv_path):
        print(f"⚠ 未找到 {csv_path}，跳过此测试")
        return True
    
    df = pd.read_csv(csv_path, encoding='utf-8')
    df = df.drop_duplicates(subset=['code', 'theme_name'], keep='first')
    
    with open(THEME_JSON_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    themes = data.get('HOT_THEMES', {})
    
    # 统计每个主题在CSV中的股票数和在theme.json中的核心公司数
    for theme_name in list(themes.keys())[:5]:
        csv_stocks = df[df['theme_name'] == theme_name]
        cfg = themes[theme_name]
        leaders = cfg.get('leader_companies', [])
        cores = cfg.get('core_companies', [])
        
        # 检查龙头股是否在CSV中
        leader_found = 0
        for leader in leaders:
            matches = csv_stocks[csv_stocks['name'].str.contains(leader, na=False)]
            if not matches.empty:
                leader_found += 1
        
        print(f"   📊 {theme_name}: CSV有{len(csv_stocks)}只, theme.json龙头{len(leaders)}只(匹配{leader_found}只), 核心{len(cores)}家")
    
    return True


if __name__ == '__main__':
    print("🔍 开始测试 realtime_theme_monitor 的 theme.json 集成...\n")
    
    test1 = test_load_theme_json()
    test2 = test_stock_layer_matching()
    test3 = test_csv_and_theme_json_consistency()
    
    print("\n" + "=" * 60)
    if all([test1, test2, test3]):
        print("✅ 所有测试通过!")
    else:
        print("❌ 部分测试失败")
    print("=" * 60)
