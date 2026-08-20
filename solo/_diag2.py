#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""诊断OTHER股票的STEALTH分类卡点"""
import sys
from datetime import datetime

sys.path.insert(0, r'd:\mystock\solo')

from _run_v5_now import fetch_minute_bars, rebuild_snap
import realtime_theme_monitor as rtm
from nd2_pattern import PatternClassifier

monitor = rtm.RealtimeThemeMonitor()
monitor.load_theme_db()
monitor.load_component_klines()
monitor.fetch_all_quotes()

today = datetime.now().strftime('%Y-%m-%d')
out = []
for ts in ['603108.SH', '002104.SZ', '000803.SZ', '605167.SH']:
    q = monitor.quotes.get(ts)
    if not q:
        out.append(f'{ts} 无行情')
        continue
    bars = fetch_minute_bars(ts, today)
    snap = rebuild_snap(q, bars, datetime.now())
    kl = monitor.stock_klines.get(ts)
    pat, f, d = PatternClassifier.classify(q, kl, snap or {}, ts)
    out.append(f'{ts} {q.get("name","")} 形态={pat}')
    out.append(f'  pct={q.get("pct_chg",0):.2f} (需0.5~3)')
    if snap:
        tail_rally = (q['price'] - snap['tail_base_price']) / snap['tail_base_price'] * 100
        out.append(f'  tail_rally={tail_rally:.2f}% (需0.1~3)')
        dist_high = (q['high'] - q['price']) / q['price'] * 100
        out.append(f'  dist_high={dist_high:.2f}% (需<1)')
        cp = (q['price'] - q['low']) / (q['high'] - q['low']) if q['high'] > q['low'] else 0
        out.append(f'  close_pos={cp:.3f} (需>=0.75)')
        out.append(f'  tail_ratio={f.get("tail_vs_noon_ratio")} (需>=1.2)')
        out.append(f'  爆量检查: cur={f.get("cur_vol")} vs 20dmax={f.get("vol_20d_max")}')
    else:
        out.append('  无snap!')

with open(r'd:\mystock\solo\_diag_out.txt', 'w', encoding='utf-8') as fp:
    fp.write('\n'.join(out))
print('done')
