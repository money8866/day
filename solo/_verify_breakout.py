import json

data = json.load(open('cache_backbone_tushare/trend_lifecycle_v11_20260612.json', encoding='utf-8'))

print("="*70)
print(f"  V11 引擎输出验证 - 爆发潜力分模块")
print(f"  交易日: {data.get('trade_date')}  主线: {data.get('primary_mainline')}")
print("="*70)

bt = data.get('breakout_themes_top5', [])
print(f"\n🔥 Top{len(bt)} 爆发潜力主题:\n")

for i, t in enumerate(bt):
    print(f"  [{i+1}] {t['top_category']} - 爆发分={t['breakout_score']} ({t['level']})")
    print(f"        股票数: {t['stock_count']} | 成交额: {t['total_amount_yi']}亿 | 均趋势分: {t['avg_trend_score']}")
    print(f"        5日均涨: {t['avg_change5_pct']:+.1f}% | 10日均涨: {t['avg_change10_pct']:+.1f}% | MA10斜率: {t['avg_ma10_slope_pct']:+.2f}%")
    print(f"        站稳MA5: {t['above_ma5_ratio']:.0f}% | 高趋势(trend>=70): {t['high_trend_ratio']:.0f}% | 5日上涨: {t['up_5d_ratio']:.0f}%")
    print(f"        V11资金连续性: {t['capital_continuity_from_v11']:.0f}")
    comp = t['score_components']
    print(f"        分项: 站稳MA5={comp['above_ma5']} 高趋势={comp['high_trend']} 5日OK={comp['chg5_ok']} MA10OK={comp['ma10_ok']} 趋势OK={comp['trend_ok']} 资金Bonus={comp['cap_bonus']} 上涨比例={comp['up_ratio_bonus']:.1f} 回流信号={comp['reversal']}")

    # 子主题
    subs = t.get('sub_themes', [])
    if subs:
        print(f"\n        📁 子主题拆解 ({len(subs)}个):")
        for s in subs:
            ind = [x['name'] for x in s.get('top_individuals', [])]
            print(f"          - {s['sub_theme']}({s['stock_count']}只) 站稳MA5={s['above_ma5_ratio']:.0f}% 均趋势={s['avg_trend_score']:.0f} 5日={s['avg_change5_pct']:+.1f}% 成交额={s['total_amount_yi']:.0f}亿")
            if ind:
                print(f"            → 爆发力个股: {', '.join([f'{n}(trend>=70, amt>=3亿)' for n in ind[:3]])}")

    # 爆发力个股
    bs = t.get('top_breakout_stocks', [])
    if bs:
        print(f"\n        🐉 Top爆发力个股 (trend>=60, 成交额>=3亿):")
        for j, s in enumerate(bs[:6]):
            ma5_str = "✅" if s['close_above_ma5'] else "❌"
            print(f"          {j+1}. {s['name']:<10}({s['ts_code']}) 趋势={s['trend_score']:>3} 综合={s['combined_score']:>5.1f} 5日={s['change_5d_pct']:+6.1f}% 10日={s['change_10d_pct']:+6.1f}% MA10={s['ma10_slope_pct']:+7.2f}% 成交={s['amount_yi']:>6.1f}亿 {ma5_str}站稳MA5")

    print()

print("="*70)
print(f"  📊 快速解读:")
print(f"  - Top3爆发主题 = {bt[0]['top_category']}/{bt[1]['top_category']}/{bt[2]['top_category']}")
print(f"  - 当前主线 {data.get('primary_mainline')} 也有资金关注，但爆发出现在非主线板块")
print(f"  - 爆发分 ≥75为高潜力信号，建议关注龙头股回踩MA5机会")
print("="*70)
