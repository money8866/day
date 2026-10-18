#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
「0仓位后什么时候能入场」实证脚本
1. 用引擎自带 breakdown 状态机在 2024-2026 全样本逐日判定，统计空仓持续时长与解除后表现
2. 找出 2026 样本中仓位从 <=10% 升到 >=25% 的转折日，看事前信号
3. 当前各触发条件的距离量化
"""
import sqlite3
import pandas as pd
import numpy as np
from io import StringIO
import sys
sys.path.insert(0, 'D:/mystock/solo')
from position_engine_v95 import _detect_key_low_breakdown

CACHE_DB = 'D:/mystock/solo/cache_backbone_tushare/cache.db'
MA_DB = 'D:/mystock/solo/cache_backbone_tushare/market_analysis.db'

# ---------- 1. 加载上证全历史（hist + 2026 kline） ----------
conn = sqlite3.connect(CACHE_DB)
sh = {}
df = pd.read_sql_query("SELECT trade_date, open, high, low, close FROM hist_000001_SH", conn)
for _, r in df.iterrows():
    sh[str(r['trade_date'])] = (float(r['open']), float(r['high']), float(r['low']), float(r['close']))
cur = conn.cursor()
cur.execute("SELECT key, data FROM cache_data WHERE key LIKE 'tsc_index_kline_ts_code_000001.SH_%'")
for key, data in cur.fetchall():
    df2 = pd.read_csv(StringIO(data))
    for _, r in df2.iterrows():
        sh[str(r['trade_date'])] = (float(r['open']), float(r['high']), float(r['low']), float(r['close']))
conn.close()

dates = sorted(sh.keys())
SH = pd.DataFrame([sh[d] for d in dates], index=pd.Index(dates, name='trade_date'),
                  columns=['open', 'high', 'low', 'close']).sort_index()
print('上证全样本: {} 天（{} ~ {}）'.format(len(SH), SH.index[0], SH.index[-1]))

# ---------- 2. 逐日跑 breakdown 状态机（扩展窗口，与当日实时判断一致） ----------
WINDOW = 120
active = pd.Series(False, index=SH.index)
key_low_s = pd.Series(np.nan, index=SH.index)
for t in range(WINDOW, len(SH)):
    win = SH.iloc[t - WINDOW + 1:t + 1].reset_index()
    res = _detect_key_low_breakdown({'上证指数': win})
    active.iloc[t] = res['active']
    key_low_s.iloc[t] = res['key_low'] if res['key_low'] else np.nan

# ---------- 3. 空仓区间统计 ----------
segments = []
in_seg = False
for t in range(1, len(SH)):
    d = SH.index[t]
    if active.iloc[t] and not in_seg:
        start = d; in_seg = True
    elif not active.iloc[t] and in_seg:
        segments.append((start, SH.index[t - 1]))
        in_seg = False
if in_seg:
    segments.append((start, SH.index[-1]))

print()
print('=' * 78)
print('一、breakdown 空仓区间统计（2024-01 ~ 2026-10，共 {} 段）'.format(len(segments)))
print('=' * 78)
print('{:<6}{:<14}{:<14}{:>8}{:>14}{:>10}'.format('#', '开始(跌破日)', '结束(解除日)', '持续天数', '结束日收盘', '未来5日'))
recs = []
for i, (s, e) in enumerate(segments, 1):
    si = SH.index.get_loc(s); ei = SH.index.get_loc(e)
    dur = ei - si + 1
    end_close = SH['close'].iloc[ei]
    r5 = (SH['close'].iloc[ei + 5] / end_close - 1) * 100 if ei + 5 < len(SH) else float('nan')
    recs.append({'s': s, 'e': e, 'dur': dur, 'r5': r5})
    mark = ' ← 当前进行中' if e == SH.index[-1] else ''
    print('{:<6}{:<14}{:<14}{:>6}天{:>13.0f}{:>9.2f}%{}'.format(
        i, s, e, dur, end_close, r5, mark))

done = [r for r in recs if not (r['e'] == SH.index[-1])]
if done:
    durs = [r['dur'] for r in done]
    r5s = [r['r5'] for r in done if not np.isnan(r['r5'])]
    print()
    print('已完成段: 持续时长 中位数 {} 天（范围 {}~{} 天）'.format(
        int(np.median(durs)), min(durs), max(durs)))
    print('解除日未来5日收益: 均值 {:+.2f}%  胜率 {:.0f}%  (n={})'.format(
        np.mean(r5s), (np.array(r5s) > 0).mean() * 100, len(r5s)))

# 当前段
cur_seg = recs[-1] if recs else None
if cur_seg and cur_seg['e'] == SH.index[-1]:
    dur_now = len(SH) - 1 - SH.index.get_loc(cur_seg['s']) + 1
    last_close = SH['close'].iloc[-1]
    kl = key_low_s.iloc[-1]
    print()
    print('当前段: 跌破日 {}，已持续 {} 天（截至 {}）'.format(cur_seg['s'], dur_now, SH.index[-1]))
    if not pd.isna(kl):
        print('关键低点 {:.2f}，当前收盘 {:.2f}，距解除（收复）还差 {:+.2f}%'.format(
            kl, last_close, (kl / last_close - 1) * 100))

# ---------- 4. 2026 样本仓位转折日 ----------
print()
print('=' * 78)
print('二、2026 样本：仓位从 <=10% 升到 >=25% 的转折日（事前信号）')
print('=' * 78)
conn = sqlite3.connect(MA_DB)
adv = pd.read_sql_query(
    "SELECT trade_date, total_position FROM overall_analysis ORDER BY trade_date", conn)
conn.close()
adv['trade_date'] = adv['trade_date'].astype(str)
pos = adv.set_index('trade_date')['total_position'].astype(float)

# 读取 limit stats 用于信号
import json, glob, os
stats = {}
for f in glob.glob('D:/mystock/solo/cache_daily/full_market_stats_*.json'):
    d = os.path.basename(f).replace('full_market_stats_', '').replace('.json', '')
    try:
        j = json.load(open(f, encoding='utf-8'))
        stats[d] = {'zb': j.get('broken_rate', 0), 'zt': j.get('zt_count', 0),
                    'up': j.get('up_ratio', 50), 'h': j.get('max_limit_height', 0)}
    except Exception:
        pass

pdates = [d for d in pos.index if d in stats]
turns = []
for i in range(1, len(pdates)):
    d0, d1 = pdates[i - 1], pdates[i]
    if pos[d0] <= 10 and pos[d1] >= 25:
        turns.append(d1)

print('共 {} 个转折日'.format(len(turns)))
print('{:<12}{:>8}{:>10}{:>9}{:>9}{:>8}{:>10}'.format(
    '转折日', '新仓位', '前日仓位', '炸板率', '涨家占比', '涨停数', '未来5日'))
zz_dates = None
for d in turns:
    s = stats.get(d, {})
    i = pdates.index(d)
    prev = pdates[i - 1] if i > 0 else None
    r5 = float('nan')
    if d in SH.index:
        di = SH.index.get_loc(d)
        if di + 5 < len(SH):
            r5 = (SH['close'].iloc[di + 5] / SH['close'].iloc[di] - 1) * 100
    print('{:<12}{:>7.0f}%{:>9.0f}%{:>9.1f}{:>8.0f}%{:>9.0f}{:>9.2f}%'.format(
        d, pos[d], pos[prev] if prev else float('nan'),
        s.get('zb', float('nan')), s.get('up', float('nan')),
        s.get('zt', float('nan')), r5))

# ---------- 5. 当前条件距离 ----------
print()
print('=' * 78)
print('三、当前（20261016）各入场触发条件的距离')
print('=' * 78)
d = '20261016'
s = stats.get(d, {})
up = s.get('up', 0); zb = s.get('zb', 0); zt = s.get('zt', 0)
kl = key_low_s.iloc[-1]
last_close = SH['close'].iloc[-1]
print('{:<26}{:>12}{:>14}'.format('条件', '当前值', '触发线'))
print('{:<26}{:>11.1f}%{:>13}  差 {:+.1f}pp'.format('炸板率（一级/二级加仓）', zb, '<25% / <20%', 25 - zb))
print('{:<26}{:>11.0f}%{:>13}  ✓ 已过'.format('涨家占比（广度否决）', up, '>55% / >60%'))
print('{:<26}{:>11.0f}{:>13}  ✓ 已过'.format('涨停数（二级加仓）', zt, '>60'))
print('{:<26}{:>11.2f}{:>13}  差 {:+.2f}%'.format('上证收盘 vs 关键低点', last_close, '> {:.2f}'.format(kl) if not pd.isna(kl) else '—', (kl / last_close - 1) * 100 if not pd.isna(kl) else float('nan')))
print('{:<26}{:>12}{:>13}'.format('主线强度', '弱', '强(top1>60)'))
