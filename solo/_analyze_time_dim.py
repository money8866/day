import json

raw = json.load(open('cache_backbone_tushare/theme3_constituents_20260612.json', encoding='utf-8'))
all_themes = raw.get('themes', [])

# 一级主题统计
cats = {}
for t in all_themes:
    cat = t.get('top_category', '')
    stocks = t.get('stocks', [])
    if not stocks:
        continue
    if cat not in cats:
        cats[cat] = []
    cats[cat].append({'name': t.get('theme_name'), 'stocks': stocks})

print("="*80)
print(f"  各一级主题时间维度分析 (短期 vs 中期)")
print("="*80)
print(f"\n  {'主题':<10} {'成分股':>7} {'5日均涨':>8} {'10日均涨':>8} "
      f"{'站稳MA5%':>9} {'MA10向上%':>10} {'高趋势股%':>10} {'短期动量':>8} {'中期持续':>8}")
print(f"  {'-'*10} {'-'*7} {'-'*8} {'-'*8} {'-'*9} {'-'*10} {'-'*10} {'-'*8} {'-'*8}")

for cat, themes in cats.items():
    # 去重合并所有成分股
    seen = set()
    all_stocks = []
    for t in themes:
        for s in t['stocks']:
            code = s.get('ts_code', '')
            if code in seen:
                continue
            seen.add(code)
            all_stocks.append(s)

    n = len(all_stocks)
    if n == 0:
        continue

    avg_chg5 = sum((s.get('change_5d_pct') or 0) for s in all_stocks) / n
    avg_chg10 = sum((s.get('change_10d_pct') or 0) for s in all_stocks) / n
    above_ma5 = sum(1 for s in all_stocks if s.get('close_above_ma5') is True) / n * 100
    pos_ma10 = sum(1 for s in all_stocks if (s.get('ma10_slope_pct') or 0) > 0) / n * 100
    high_trend = sum(1 for s in all_stocks if (s.get('trend_score') or 0) >= 70) / n * 100
    avg_trend = sum((s.get('trend_score') or 0) for s in all_stocks) / n

    # 短期动量评分（1-5日强度）
    short_score = (
        min(100, avg_chg5 * 20) * 0.50     # 5日涨幅强度（放大20倍后截断）
        + above_ma5 * 0.30                   # 站稳MA5比例（信号确认）
        + min(100, high_trend * 2) * 0.20    # 高趋势股比例（强度广度）
    )

    # 中期持续性评分（10-20日强度+稳定性）
    mid_score = (
        min(100, avg_chg10 * 10) * 0.40     # 10日涨幅（中期趋势）
        + pos_ma10 * 0.35                     # MA10向上比例（中期健康度）
        + avg_trend * 0.25                    # 平均趋势分（综合质量）
    )

    # 总评分（新）
    total_new = short_score * 0.45 + mid_score * 0.55

    # 可视化指标
    short_bar = '█' * int(short_score / 5)
    mid_bar = '█' * int(mid_score / 5)

    print(f"  {cat:<10} {n:>7} {avg_chg5:>+7.1f}% {avg_chg10:>+7.1f}% "
          f"{above_ma5:>8.0f}% {pos_ma10:>9.0f}% {high_trend:>9.0f}% "
          f"{short_score:>7.1f} {short_bar} {mid_score:>7.1f} {mid_bar}")

print(f"\n{'='*80}")
print(f"  💡 指标说明:")
print(f"     短期动量 = 5日涨幅强度(50%) + 站稳MA5比例(30%) + 高趋势比例(20%)")
print(f"     中期持续 = 10日涨幅强度(40%) + MA10向上比例(35%) + 均趋势分(25%)")
print(f"     综合 = 短期(45%) + 中期(55%)")
print(f"\n  📌 核心发现:")
print(f"     - 资源/化工：短期强但中期弱（5日+4-7%，10日+0.6%）")
print(f"     - 金融：短期+中期都较强，但资金连续性下降中")
print(f"     - 半导体/AI：短期反弹但中期仍弱（10日跌幅较大）")
print(f"{'='*80}")
