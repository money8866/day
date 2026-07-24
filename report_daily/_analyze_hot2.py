# -*- coding: utf-8 -*-
import json
print('=== HOT STOCKS TOP30 (07-24) ===')
hot = json.load(open('D:/mystock/report_daily/q_hot_0724.json', encoding='utf-8'))['data']['item']
for s in hot:
    print('  #%-2d %-7s heat=%-9s trend=%-5s chg_rank=%s' % (
        s['rank'], s['name'], s['heat'], s.get('rank_trend',''), s.get('rank_change','')))

print()
print('=== DRAGON TIGER (07-24) ===')
dt = json.load(open('D:/mystock/report_daily/q_dragon_0724.json', encoding='utf-8'))['data']
print('dt keys:', list(dt.keys()) if isinstance(dt, dict) else type(dt))
if isinstance(dt, dict):
    for k, v in dt.items():
        if isinstance(v, list):
            print('  %s: %d items' % (k, len(v)))
            if v: print('   sample:', json.dumps(v[0], ensure_ascii=False)[:400])
        else:
            print('  %s:', str(v)[:120])
elif isinstance(dt, list):
    print('  list len:', len(dt))
    if dt: print('   sample:', json.dumps(dt[0], ensure_ascii=False)[:400])

print()
print('=== POSITIONS (07-24 close) ===')
pos = json.load(open('D:/mystock/report_daily/q_pos_0724.json', encoding='utf-8'))['data']['item']
for s in pos:
    print('  %-8s %-7s last=%8.3f chg%%=%6.2f turnover(亿)=%7.1f vol(亿手)=%6.2f' % (
        s['thscode'], s.get('name',''), s['last_price'],
        s['price_change_ratio_pct'], s['turnover']/1e8, s['volume']/1e8))
