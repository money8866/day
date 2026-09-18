# -*- coding: utf-8 -*-
"""用 theme_scores.db 全历史回放新 V4.1 门禁逻辑（只读，诊断用）

目的：验证修改后 L2/L1/R 在 40 个交易日上的触发情况。
直接从 theme_score_v2 导入门禁函数，保证与生产口径一致（不复制逻辑）。

注意：历史库 20260724~20260902 的 fund_acc / gate_cap_amt 为空（两字段 0903 才引入），
故另跑一次"代入补全"（fund=75、cap=20亿）观察"若不缺这两个字段"时的 L2 触发情况。
"""
import sys, json, sqlite3
from collections import defaultdict

sys.path.insert(0, r'd:/mystock/solo')
from theme_score_v2 import (classify_capital_state, calc_mainline_tier_v4,
                            GATE_L2_MAX_PER_DAY)

DB = r'd:/mystock/solo/report_daily/theme_scores.db'
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("""SELECT trade_date, theme, lifecycle, theme_state, target_state, trend_score,
                      sentiment_score, composite_score, migration_score, up_ratio, zt_count,
                      fund_acc, hot_percentile, gate_cap_amt, ret_5, hot_phase, gate_feat
               FROM theme_scores ORDER BY trade_date""")
cols = ['trade_date', 'theme', 'lifecycle', 'theme_state', 'target_state', 'trend_score',
        'sentiment_score', 'composite_score', 'migration_score', 'up_ratio', 'zt_count',
        'fund_acc', 'hot_percentile', 'gate_cap_amt', 'ret_5', 'hot_phase', 'gate_feat']
raw = [dict(zip(cols, r)) for r in cur.fetchall()]
conn.close()
for d in raw:                       # hot_source 无独立列：从 gate_feat 快照解析
    try:
        d['hot_source'] = json.loads(d['gate_feat'] or '{}').get('hot_src', '') or ''
    except Exception:
        d['hot_source'] = ''

by_day = defaultdict(list)
for r in raw:
    by_day[r['trade_date']].append(r)
dates = sorted(by_day)

hist = defaultdict(list)            # theme -> 历史行（升序）
prev_row = {}                       # theme -> 上一交易日行


def to_r(d, fill=False):
    r = {
        'theme': d['theme'], 'lifecycle': d['lifecycle'] or '',
        'trend_score': float(d['trend_score'] or 0),
        'sentiment_score': float(d['sentiment_score'] or 0),
        'composite_score': float(d['composite_score'] or 0),
        'migration_score': float(d['migration_score'] or 0),
        'target_state': d['target_state'] or '',
        'fund_acc': float(d['fund_acc'] or 0),
        'ret_5': float(d['ret_5'] or 0),
        'hot_percentile': float(d['hot_percentile'] or 50),
        'hot_source': d['hot_source'] or '', 'hot_phase': d['hot_phase'] or '',
        'sentiment_detail': {'zt_count': int(d['zt_count'] or 0),
                             'up_ratio': float(d['up_ratio'] or 0)},
    }
    cap_amt = float(d['gate_cap_amt'] or 0)
    if fill:                        # 缺字段代入：仅用于近似验证
        if r['fund_acc'] <= 0:
            r['fund_acc'] = 75.0
        if cap_amt <= 0:
            cap_amt = 20.0
    r['top5_stocks'] = [{'amount': cap_amt}]
    return r


real, approx = {}, {}
for dt in dates:
    rs, ap = [], []
    for d in by_day[dt]:
        pv = prev_row.get(d['theme'])
        plc = (pv or {}).get('lifecycle', '')
        win = hist[d['theme']][-5:]
        r = to_r(d)
        r['capital_state'] = classify_capital_state(r, pv)
        r['gate_tier'] = calc_mainline_tier_v4(r, prev_lc=plc, hist_rows=win)
        rs.append(r)
        r2 = to_r(d, fill=True)
        r2['capital_state'] = classify_capital_state(r2, pv)
        r2['gate_tier'] = calc_mainline_tier_v4(r2, prev_lc=plc, hist_rows=win)
        ap.append(r2)
    for bucket in (rs, ap):          # L2 每日上限（与生产一致）
        hits = sorted([x for x in bucket if x['gate_tier'] == 'L2'],
                      key=lambda x: -x['composite_score'])
        for x in hits[GATE_L2_MAX_PER_DAY:]:
            x['gate_tier'] = 'L1'
    real[dt], approx[dt] = rs, ap
    for d in by_day[dt]:             # 更新窗口 / 前日
        hist[d['theme']].append(d)
        prev_row[d['theme']] = d

print('日期        L2  L1   R  L0 NONE | 近似L2 | L1/L2 明细')
tot = defaultdict(int)
apx_rows = []
for dt in dates:
    rs = real[dt]
    c = defaultdict(int)
    for x in rs:
        c[x['gate_tier']] += 1
        tot[x['gate_tier']] += 1
    a2 = [x for x in approx[dt] if x['gate_tier'] == 'L2']
    if a2:
        apx_rows.append((dt, [f"{x['theme']}({x['composite_score']:.0f}/{x['trend_score']:.0f}"
                               f"/宽{x['sentiment_detail']['up_ratio']:.0f}"
                               f"/涨{x['sentiment_detail']['zt_count']}/动{x['ret_5']:+.1f}"
                               f"/持{x['days_strong']})" for x in a2]))
    hits = [f"{x['gate_tier']}:{x['theme']}({x['composite_score']:.0f}/{x['trend_score']:.0f}"
            f"/宽{x['sentiment_detail']['up_ratio']:.0f}/涨{x['sentiment_detail']['zt_count']}"
            f"/动{x['ret_5']:+.1f}/持{x['days_strong']})"
            for x in rs if x['gate_tier'] in ('L2', 'L1')]
    print(f"{dt}  {c['L2']:2d}  {c['L1']:2d}  {c['R']:3d}  {c['L0']:2d} {c['NONE']:4d} | "
          f"{len(a2):4d}   | {' | '.join(hits) if hits else '—'}")

print()
print('=== 40 日累计档位分布（真实字段口径）===', dict(tot))
print()
print('=== 近似 L2（缺 fund/cap 的日用 fund=75 / cap=20亿 代入）===')
for dt, a in apx_rows:
    print(f"  {dt}  {' | '.join(a)}")
