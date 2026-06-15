import json

constituents = json.load(open('cache_backbone_tushare/theme3_constituents_20260612.json', encoding='utf-8'))
all_themes = constituents.get('themes', [])

# 收集半导体和化工主题下的股票
semi_stocks = {}  # code -> stock info
chem_stocks = {}  # code -> stock info

for t in all_themes:
    cat = t.get('top_category', '')
    if cat == '半导体':
        for s in t.get('stocks', []):
            code = s.get('ts_code', '')
            s = dict(s)
            s['_theme'] = t.get('theme_name', '')
            semi_stocks[code] = s
    elif cat == '化工':
        for s in t.get('stocks', []):
            code = s.get('ts_code', '')
            s = dict(s)
            s['_theme'] = t.get('theme_name', '')
            chem_stocks[code] = s

# 找交集
cross = set(semi_stocks.keys()) & set(chem_stocks.keys())
print(f"半导体主题股票数: {len(semi_stocks)}")
print(f"化工主题股票数: {len(chem_stocks)}")
print(f"交叉股票数: {len(cross)}")
print()

if cross:
    # 按趋势分排序
    cross_list = []
    for code in cross:
        s_semi = semi_stocks[code]
        s_chem = chem_stocks[code]
        trend = s_semi.get('trend_score', 0) or 0
        amt = (s_semi.get('avg_amount_5d', 0) or 0) / 1e8
        chg5 = s_semi.get('change_5d_pct') or 0
        combined = s_semi.get('combined_score') or 0
        cross_list.append({
            'name': s_semi.get('name', ''),
            'code': code,
            'semi_theme': s_semi['_theme'],
            'chem_theme': s_chem['_theme'],
            'trend_score': trend,
            'combined_score': combined,
            'amount_yi': amt,
            'change_5d_pct': chg5,
            'ma10_slope_pct': s_semi.get('ma10_slope_pct') or 0,
            'close_above_ma5': s_semi.get('close_above_ma5'),
        })

    cross_list.sort(key=lambda x: -x['trend_score'])

    print(f"{'='*80}")
    print(f"  半导体 × 化工 交叉股票（按趋势分排序）")
    print(f"{'='*80}\n")
    print(f"  {'股票名称':<10} {'代码':<12} {'趋势分':>6} {'综合分':>7} {'5日涨幅':>8} {'MA10斜率':>9} {'成交额':>8} {'半导体子主题':<14} {'化工子主题':<12} {'MA5':>5}")
    print(f"  {'-'*80}")
    for s in cross_list:
        ma5 = '✅' if s['close_above_ma5'] else '❌'
        print(f"  {s['name']:<10} {s['code']:<12} {s['trend_score']:>6} {s['combined_score']:>7.1f} "
              f"{s['change_5d_pct']:>+7.1f}% {s['ma10_slope_pct']:>+8.2f}% {s['amount_yi']:>7.1f}亿 "
              f"{s['semi_theme']:<14} {s['chem_theme']:<12} {ma5:>5}")

    print(f"\n{'='*80}")
    print(f"  解读：这些股票同时属于半导体材料和化工两大主题")
    print(f"  属于[跨界双击]结构，在半导体国产替代+化工周期复苏双重逻辑下受益")
    print(f"{'='*80}")
else:
    print("未找到交叉股票")
