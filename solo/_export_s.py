import json

with open('report_daily/mainboard_second_wave.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

s_stocks = [r for r in data['data'] if r.get('rating') == 'S']
s_stocks.sort(key=lambda x: x['second_wave_score'] * 0.6 + x['value_preservation_score'] * 0.4, reverse=True)

print(f"S级股票共 {len(s_stocks)} 只\n")
print("=" * 130)
print(f"{'排名':<4} {'代码':<12} {'名称':<10} {'主题':<14} {'市值(亿)':<10} {'20日均(亿)':<10} {'二波分':<7} {'价值分':<7} {'评级':<4} {'阶段'}")
print("-" * 130)

for i, r in enumerate(s_stocks, 1):
    print(f"{i:<4} {r['ts_code']:<12} {r['name']:<10} {r['theme']:<14} {r['market_cap_yi']:<10.0f} {r['avg_amount_20d_yi']:<10.1f} {r['second_wave_score']:<7.1f} {r['value_preservation_score']:<7.1f} {r['rating']:<4} {r['stage']}")

print("=" * 130)
print(f"\n共 {len(s_stocks)} 只 S 级股票")
