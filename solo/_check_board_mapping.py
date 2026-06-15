import json, os
import pandas as pd

# 1. 查看东财板块数据的典型板块名称
cache_path = 'd:/mystock/solo/cache_backbone_tushare/dc_all_members.csv'
df = pd.read_csv(cache_path)

# 所有板块名（去重）
all_boards = list(set(df['concept_name'].tolist()))
print(f"总共 {len(all_boards)} 个独特板块")

# 2. 对关键主题，显示其成分股在东财板块中的命中情况
with open('d:/mystock/solo/theme3.json', 'r', encoding='utf-8') as f:
    t3 = json.load(f)
flat = t3.get('THEME_FLAT_MAP', {})

# 选择几个主题
for theme_name in ['AI算力芯片', '先进封装', '功率半导体', '人形机器人整机与集成', '低空飞行器制造', '新型储能']:
    cfg = flat.get(theme_name)
    if not cfg:
        continue
    tags = cfg.get('business_dna_tags', []) or []
    print(f"\n{'=' * 60}")
    print(f"【{theme_name}】")
    print(f"  business_dna_tags: {tags}")
    print(f"  core_semantic: {cfg.get('core_semantic', [])}")
    print(f"  industry_soft_constraints: {cfg.get('industry_soft_constraints', {})}")
    print(f"  industry_roles: {cfg.get('industry_roles', {})}")

    # 搜索东财板块中包含这些关键词的板块
    matched_boards = []
    for tag in tags[:5]:  # 只看前5个标签
        for b in all_boards:
            if tag in str(b):
                matched_boards.append((tag, b))
    if matched_boards:
        print(f"  东财板块命中（前15）:")
        for tag, b in matched_boards[:15]:
            # 统计有多少只股票在这个板块
            count = len(df[df['concept_name'] == b])
            print(f"    [{tag}] -> {b} ({count}只)")
    else:
        print(f"  ⚠ 无直接东财板块命中！")

    # 3. 检查核心成分股在东财板块的实际分布
    constituents_path = 'd:/mystock/solo/cache_backbone_tushare/theme3_constituents_20260612.json'
    with open(constituents_path, 'r', encoding='utf-8') as f:
        const_data = json.load(f)
    theme_data = next((t for t in const_data.get('themes', []) if t.get('theme_name') == theme_name), None)
    if theme_data and theme_data.get('stocks'):
        print(f"\n  Top 5 成分股及其东财板块:")
        for stock in theme_data['stocks'][:5]:
            name = stock.get('name', '')
            code = stock.get('ts_code', '')
            # 找出这只股票在东财的所有板块
            stock_rows = df[df['con_code'] == code]
            boards = stock_rows['concept_name'].tolist()
            print(f"    {name}({code}): {boards[:8] if boards else '无'}")
