# -*- coding: utf-8 -*-
import sys, os, time
sys.path.insert(0, r'D:\mystock\solo')
os.chdir(r'D:\mystock\solo')
import realtime_monitor_tdx as m

mon = m.RealtimeMonitor()
print('持仓:', mon.positions)
print('主题:', list(mon.theme_stocks.keys()))

t0 = time.time()
ok = mon.fetch_all()
print('fetch_all耗时: %.2fs' % (time.time()-t0))
print('行情数量:', len(mon.quotes))
print('指数:', {k: ('%.2f%%' % v.get('pct_chg', 0)) for k, v in mon.index_quotes.items()})

theme_data = mon.analyze_themes()
for theme, data in sorted(theme_data.items(), key=lambda x: x[1]['avg_pct'], reverse=True):
    print('  %s: %.1f%% ↑%d/%d' % (theme, data['avg_pct'], data['up_count'], data['total']))

positions = mon.analyze_positions()
for p in positions:
    e = '🟢' if p['pct'] >= 0 else '🔴'
    print('  持仓 %s: %s%.1f%%' % (p['name'], e, p['pct']))

alerts = mon.detect_anomalies()
print('预警: %d条' % len(alerts))
for a in alerts:
    print('  %s %s' % (a['level'], a['title']))

score, status, pos = mon.calc_market_score()
print('市场评分: %s %s 仓位%d%%' % (score, status, pos))
print('测试完成!')
