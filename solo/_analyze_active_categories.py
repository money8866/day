import json

# 读取 V11 引擎结果
d = json.load(open('cache_backbone_tushare/trend_lifecycle_v11_20260612.json', encoding='utf-8'))

# 读取成分股数据
constituents = json.load(open('cache_backbone_tushare/theme3_constituents_20260612.json', encoding='utf-8'))

print('=== 全市场一级主题活跃度分析 (2026-06-12) ===\n')

# 按主线分排序
ranking = sorted(d.get('all_theme_ranking', []), key=lambda x: -x.get('mainline_score', 0))

print('{:<10} {:<10} {:<10} {:<10} {:<10} {:<12} {}'.format(
    '一级主题', '主线分', '资金连续性', '扩散力', '龙头结构', '市场占比%', '分类/生命周期'))
print('-' * 90)

for t in ranking:
    market_share = t.get('market_share_pct', 0)
    lifecycle = t.get('lifecycle_stage', '')
    classification = t.get('classification', '')
    # 活跃度标记
    if t['mainline_score'] >= 60:
        flag = '🔥'
    elif t['mainline_score'] >= 50:
        flag = '🌡️'
    elif t['capital_continuity'] >= 40:
        flag = '🌿'
    else:
        flag = '❄️'
    print('{:<12} {:<10.1f} {:<12.1f} {:<10.1f} {:<10.1f} {:<12.1f} {} / {} {}'.format(
        t['theme'], t['mainline_score'], t['capital_continuity'],
        t['diffusion_score'], t['leader_structure'], market_share,
        classification, lifecycle, flag))

print()

# 分析每一级主题的成分股质量
print('=== 活跃主题详细分析 ===\n')

# 按资金连续性排序（反映真实资金活跃）
active_by_cap = sorted(ranking, key=lambda x: -x.get('capital_continuity', 0))

# 查看各主题下活跃股票（trend >= 60, amt >= 5亿）
cat_active_stocks = {}
for theme_item in constituents.get('themes', []):
    cat = theme_item.get('top_category', '')
    if cat not in cat_active_stocks:
        cat_active_stocks[cat] = []
    for s in theme_item.get('stocks', []):
        trend = s.get('trend_score', 0) or 0
        amt = (s.get('avg_amount_5d') or 0) / 1e8
        if trend >= 60 and amt >= 5:
            cat_active_stocks[cat].append({
                'name': s.get('name', ''),
                'ts_code': s.get('ts_code', ''),
                'sub_theme': theme_item.get('theme_name', ''),
                'trend': trend,
                'combined': s.get('combined_score', 0),
                'amt': round(amt, 1),
                'chg10': round(s.get('change_10d_pct', 0) or 0, 1)
            })

# 按主线分排序展示
for t in ranking:
    cat = t['theme']
    actives = cat_active_stocks.get(cat, [])
    # 去重
    seen = set()
    unique_actives = []
    for s in actives:
        if s['ts_code'] not in seen:
            seen.add(s['ts_code'])
            unique_actives.append(s)
    unique_actives.sort(key=lambda x: -x['trend'])
    
    flag = '🔥' if t['mainline_score'] >= 60 else '🌡️' if t['mainline_score'] >= 50 else '🌿' if t['capital_continuity'] >= 40 else '❄️'
    
    print(f"{flag} 【{cat}】主线分={t['mainline_score']:.1f} | 资金连续性={t['capital_continuity']:.1f} | 生命周期={t['lifecycle_stage']}")
    print(f"   分类: {t['classification']} | 扩散力={t['diffusion_score']:.1f} | 龙头结构={t['leader_structure']:.1f} | 市场占比={t['market_share_pct']:.1f}%")
    print(f"   活跃趋势股 (trend>=60, 成交额>=5亿): {len(unique_actives)} 只")
    if unique_actives:
        print(f"   Top活跃: " + ", ".join([f"{s['name']}({s['trend']})" for s in unique_actives[:8]]))
    print()
