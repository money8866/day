import json

with open('d:/mystock/solo/cache_backbone_tushare/theme3_constituents_20260612.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# 按子主题计算指标
sub_themes = []
for t in data['themes']:
    primary = t.get('top_category', '')
    sub_name = t.get('theme_name', '')
    stocks = t.get('stocks', [])

    if not stocks:
        continue

    n = len(stocks)
    avg_change5 = sum(s.get('change_5d_pct', 0) or 0 for s in stocks) / n
    avg_change20 = sum(s.get('change_20d_pct', 0) or 0 for s in stocks) / n
    avg_change60 = sum(s.get('change_60d_pct', 0) or 0 for s in stocks) / n
    above_ma60 = sum(1 for s in stocks if s.get('close_above_ma60')) / n * 100
    total_amount = sum(s.get('avg_amount_5d', 0) or 0 for s in stocks) / 1e8

    mid_term = (
        min(100, avg_change20 * 5 + 50) * 0.3 +
        min(100, avg_change60 * 2.5 + 50) * 0.3 +
        above_ma60 * 0.4
    )

    sub_themes.append({
        'primary': primary,
        'sub': sub_name,
        'n_stocks': n,
        'mid_term': mid_term,
        'avg_change5': avg_change5,
        'avg_change20': avg_change20,
        'avg_change60': avg_change60,
        'above_ma60': above_ma60,
        'amount_yi': total_amount,
    })

sub_themes.sort(key=lambda x: -x['mid_term'])

print("【昨日（20260612）二级主题排名】")
print(f"{'一级主题':<10} {'二级主题':<18} {'股票数':>5} {'中期分':>6} {'5日均':>6} {'60日均':>7} {'MA60站上':>7} {'成交额':>8}")
print("-" * 90)

for s in sub_themes[:40]:
    print(f"{s['primary']:<10} {s['sub']:<18} {s['n_stocks']:>5} {s['mid_term']:>6.1f} {s['avg_change5']:>6.1f} {s['avg_change60']:>7.1f} {s['above_ma60']:>7.1f} {s['amount_yi']:>8.1f}亿")
