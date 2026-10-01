# -*- coding: utf-8 -*-
"""W7 信号结构画像：前期 vs 转差期（用 daily_cache 复算口径）"""
import sqlite3, json
cc = sqlite3.connect('file:D:/mystock/cache_daily/stock_data.db?mode=ro', uri=True)
cols = [r[1] for r in cc.execute("pragma table_info(daily_cache)")]
print('[daily_cache cols]', cols)
pc = sqlite3.connect('file:d:/mystock/solo/picks_db/stock_picks.db?mode=ro', uri=True)
sig = pc.execute("select pick_date, ts_code, score, indicators, stock_name, industry from stock_pick "
                 "where strategy_id='w7_hvt' order by pick_date, ts_code").fetchall()

def feats(code, date):
    bars = cc.execute("select trade_date, open, high, low, close, vol from daily_cache "
                      "where ts_code=? and trade_date<=? order by trade_date desc limit 61",
                      (code, date)).fetchall()
    if len(bars) < 25:
        return None
    bars = bars[::-1]
    cl = [b[4] for b in bars]
    hi = [b[2] for b in bars]
    vo = [b[5] for b in bars]
    i = len(bars) - 1
    c = cl[i]
    m5 = sum(cl[i - 4:i + 1]) / 5
    m10 = sum(cl[i - 9:i + 1]) / 10
    m20 = sum(cl[i - 19:i + 1]) / 20
    v20 = sum(vo[i - 19:i + 1]) / 20
    h20 = max(hi[i - 19:i + 1])
    h60 = max(hi[max(0, i - 59):i + 1])
    l20 = min(cl[i - 19:i + 1])
    return {
        'd20': (c / m20 - 1) * 100,                 # 距MA20
        'm5m10': (m5 / m10 - 1) * 100,              # MA5 vs MA10
        'm20slope': (m20 / (sum(cl[i - 24:i - 4]) / 20) - 1) * 100 if i >= 24 else 0,
        'off20': (c / h20 - 1) * 100,               # 距20日高
        'off60': (c / h60 - 1) * 100,               # 距60日高
        'pos20': (c - l20) / (h20 - l20) * 100 if h20 > l20 else 50,
        'r5': (c / cl[i - 5] - 1) * 100,
        'r10': (c / cl[i - 10] - 1) * 100,
        'r20': (c / cl[i - 20] - 1) * 100,
        'volr': vo[i] / v20 if v20 > 0 else 0,
        'v20v60': v20 / (sum(vo[i - 59:i + 1]) / 60) if i >= 59 else 0,
    }

WIN = {'前期0825~0916': ('20260825', '20260916'),
       '转差0917~0924': ('20260917', '20260924'),
       '最新0928~0930': ('20260928', '20260930')}
KEYS = ['d20', 'm5m10', 'm20slope', 'off20', 'off60', 'pos20', 'r5', 'r10', 'r20', 'volr', 'v20v60']
agg = {}
detail = {}
for d, code, score, js, nm, ind_ in sig:
    f = feats(code, d)
    if not f:
        continue
    w = None
    for k, (a, b) in WIN.items():
        if a <= d <= b:
            w = k
    if not w:
        continue
    agg.setdefault(w, {}).setdefault('n', 0)
    agg[w]['n'] += 1
    for k in KEYS:
        agg[w].setdefault(k, 0.0)
        agg[w][k] += f[k]
    detail.setdefault(w, []).append((d, code, nm, score, f))

print()
print('%-16s %4s | %s' % ('区间', 'n', ' '.join('%9s' % k for k in KEYS)))
for w in WIN:
    if w not in agg:
        continue
    a = agg[w]
    print('%-16s %4d | %s' % (w, a['n'], ' '.join('%+9.2f' % (a[k] / a['n']) for k in KEYS)))

print()
print('=== 各区间「形态已坏」占比 ===')
for w in WIN:
    if w not in agg:
        continue
    n = agg[w]['n']
    for name, cond in (('close<MA20', lambda f: f['d20'] < 0),
                       ('MA5<MA10', lambda f: f['m5m10'] < 0),
                       ('MA20下行', lambda f: f['m20slope'] < 0),
                       ('距20日高<-8%', lambda f: f['off20'] < -8),
                       ('r5<0(近5日跌)', lambda f: f['r5'] < 0),
                       ('r20<0(近20日跌)', lambda f: f['r20'] < 0)):
        k = sum(1 for _, _, _, _, f in detail[w] if cond(f))
        print('  %-16s %-16s %3d/%3d = %5.1f%%' % (w, name, k, n, k * 100.0 / n))

print()
print('=== 转差期逐条结构画像（0917 起）===')
print('%-9s %-10s %-7s %5s | %7s %7s %8s %8s %8s %7s %7s %7s %6s'
      % ('日期', '代码', '名称', '分', 'dMA20', 'm5-m10', 'MA20sl', '离20高', '离60高', 'pos20', 'r5', 'r20', 'volr'))
for w in ('转差0917~0924', '最新0928~0930'):
    for d, code, nm, score, f in sorted(detail[w]):
        print('%-9s %-10s %-7s %5.1f | %+7.2f %+7.2f %+8.2f %+8.2f %+8.2f %7.1f %+7.2f %+7.2f %6.2f'
              % (d, code, (nm or '')[:7], score, f['d20'], f['m5m10'], f['m20slope'],
                 f['off20'], f['off60'], f['pos20'], f['r5'], f['r20'], f['volr']))
cc.close()
pc.close()
