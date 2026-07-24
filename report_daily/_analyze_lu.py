# -*- coding: utf-8 -*-
import json
from collections import Counter
lu = json.load(open('D:/mystock/report_daily/q_limitup_0724.json', encoding='utf-8'))['items']
print('=== 40 LIMIT-UP STOCKS (07-24) ===')
themes = Counter()
for s in sorted(lu, key=lambda x: -x['continue_day_cnt']):
    print('%-8s %-4s %4d%% 封单%5.2f亿 %s' % (
        s['name'], s['continue_day_text'], s['price_change_ratio_pct'],
        s['seal_money']/1e8, s['limit_up_reason']))
    for t in s['limit_up_reason'].split('+'):
        themes[t.strip()] += 1
print()
print('=== THEME FREQ (from limit_up_reason) ===')
for t, c in themes.most_common(30):
    print('  %s: %d' % (t, c))
