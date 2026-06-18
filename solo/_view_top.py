import json
with open('report_daily/mainboard_second_wave.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
print('=' * 120)
print('排名 股票    主题         市值(亿) 20日均(亿) 识别分 二波分 价值分 评级 阶段')
print('-' * 120)
for i, r in enumerate(data['data'][:25], 1):
    print(f'{i:<4} {r["name"]:<8} {r["theme"]:<12} {r["market_cap_yi"]:<8.0f} {r["avg_amount_20d_yi"]:<10.1f} {r["recognition_score"]:<6.1f} {r["second_wave_score"]:<6.1f} {r["value_preservation_score"]:<6.1f} {r["rating"]:<4} {r["stage"][:20]}')
print('=' * 120)
