# -*- coding: utf-8 -*-
import sys, os, time
sys.path.insert(0, r'D:\mystock\solo')
os.chdir(r'D:\mystock\solo')

import realtime_monitor_tdx as m

# 修改run方法，只运行1轮
mon = m.RealtimeMonitor()
print('持仓:', mon.positions)
print('主题数:', len(mon.theme_stocks))

start = time.time()
ok = mon.fetch_all()
print('获取行情耗时: %.2fs' % (time.time()-start))
print('行情数量:', len(mon.quotes))
print('指数:', {k: v.get('pct_chg') for k, v in mon.index_quotes.items()})

# 打印前几只股票
for code, q in list(mon.quotes.items())[:5]:
    print('  %s: %.2f (%.2f%%)' % (code, q.get('price'), q.get('pct_chg')))

# 分析主题
theme_data = mon.analyze_theme()
print('\n主题分析:')
for theme, data in sorted(theme_data.items(), key=lambda x: x[1]['avg_pct'], reverse=True)[:3]:
    print('  %s: %.1f%% ↑%d/%d' % (theme, data['avg_pct'], data['up_count'], data['total']))

# 检测预警
alerts = mon.detect_anomalies()
print('\n预警: %d条' % len(alerts))
for a in alerts[:3]:
    print('  %s: %s' % (a['level'], a['title']))

print('\n测试完成!')
