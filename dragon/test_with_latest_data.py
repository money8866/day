#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查找最新交易日并测试历史主线跟踪"""
import tushare as ts
from datetime import datetime, timedelta
import sys
sys.path.insert(0, 'C:\\Users\\kongx\\mystock\\dragon')

TOKEN = 'bdd5007be4e91aadf516c81fa4d12b14b0bbee164a302a1cef33859d'
ts.set_token(TOKEN)
pro = ts.pro_api()

# 1. 查找最新交易日
end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - timedelta(days=10)).strftime('%Y%m%d')

df = pro.trade_cal(exchange='SSE', start_date=start_date, end_date=end_date)
df_open = df[df['is_open'] == 1].sort_values('cal_date', ascending=False)

if len(df_open) > 0:
    latest_date = df_open.iloc[0]['cal_date']
    print(f'LATEST_TRADE_DATE={latest_date}')
else:
    print('ERROR: No trading day found')
    sys.exit(1)

# 2. 测试历史主线跟踪
from sector_strength import analyze_sector_strength, track_history_themes, find_recurring_themes

print(f'\nTesting with trade_date={latest_date}...')
result = analyze_sector_strength(trade_date=latest_date)
main_themes = result.get('main_themes', [])

top5_names = [theme['name'] for theme in main_themes[:5]]
print(f'TOP5: {top5_names}')

# 3. 记录到历史
history = track_history_themes(latest_date, top5_names)
print(f'History entries: {len(history)}')

# 4. 查找反复活跃板块
recurring = find_recurring_themes(history, min_count=2)
if recurring:
    print('Recurring themes:')
    for r in recurring:
        name = r['name']
        count = r['count']
        print(f'  {name}: {count} times')
else:
    print('No recurring themes yet (need more data)')

print('\nDone!')
