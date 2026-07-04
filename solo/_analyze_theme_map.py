# -*- coding: utf-8 -*-
import json
from collections import Counter, defaultdict

with open(r'd:\mystock\cache_daily\theme_stock_map_latest.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

print('=== 基本统计 ===')
print(f"交易日期: {data['trade_date']}")
print(f"更新时间: {data['update_time']}")
print(f"主题数量: {data['n_themes']}")
print(f"股票数量: {data['n_stocks']}")
print(f"股票引用总数: {data['n_stock_refs']}")
print(f"平均每个主题包含股票数: {data['n_stock_refs']/data['n_themes']:.1f}")

theme_counts = {}
stock_themes = defaultdict(set)
stock_scores = defaultdict(dict)

for theme, stocks in data['themes'].items():
    theme_counts[theme] = len(stocks)
    for s in stocks:
        code = s['code']
        stock_themes[code].add(theme)
        stock_scores[code][theme] = s.get('score', 0)

print('\n=== 每个主题的成份股数量（降序） ===')
for theme, cnt in sorted(theme_counts.items(), key=lambda x: -x[1]):
    print(f'{theme}: {cnt}')

print('\n=== TOP10 大主题 ===')
for theme, cnt in sorted(theme_counts.items(), key=lambda x: -x[1])[:10]:
    print(f'{theme}: {cnt}')

print('\n=== BOTTOM10 小主题 ===')
for theme, cnt in sorted(theme_counts.items(), key=lambda x: x[1])[:10]:
    print(f'{theme}: {cnt}')

# 重叠度分析
print('\n=== 股票跨主题分布 ===')
multi_theme_counts = Counter(len(v) for v in stock_themes.values())
for k in sorted(multi_theme_counts.keys()):
    print(f'属于 {k} 个主题的股票数: {multi_theme_counts[k]}')

print('\n=== 跨主题最多的TOP20股票 ===')
cross_list = [(code, len(themes), themes) for code, themes in stock_themes.items()]
for code, n, themes in sorted(cross_list, key=lambda x: -x[1])[:20]:
    print(f'{code}: {n}个主题 -> {", ".join(sorted(themes))}')

# 主题间重叠矩阵
print('\n=== 主题间重叠TOP30（共同股票数） ===')
theme_stock_sets = {t: set(s['code'] for s in stocks) for t, stocks in data['themes'].items()}
overlaps = []
for t1 in theme_stock_sets:
    for t2 in theme_stock_sets:
        if t1 >= t2:
            continue
        common = theme_stock_sets[t1] & theme_stock_sets[t2]
        if common:
            overlaps.append((t1, t2, len(common), len(common)/min(len(theme_stock_sets[t1]), len(theme_stock_sets[t2]))))

for t1, t2, n, ratio in sorted(overlaps, key=lambda x: -x[2])[:30]:
    print(f'{t1} vs {t2}: 共同{n}只 (占小主题{ratio*100:.1f}%)')

# 分析via来源
print('\n=== 成份股来源分布（按via） ===')
via_counter = Counter()
for theme, stocks in data['themes'].items():
    for s in stocks:
        via_counter[s.get('via', 'unknown')] += 1
for via, cnt in via_counter.most_common():
    print(f'{via}: {cnt}')
