# -*- coding: utf-8 -*-
"""V2 报告取数：把 breakout_analysis / regime / marketcap 的关键行压成紧凑文本"""
import pandas as pd, numpy as np, os, sys

BASE = r'd:\mystock\solo\report_daily\research'
OUT = []

def P(*a):
    s = ' '.join(str(x) for x in a)
    OUT.append(s)

def fmt(v, p=2):
    try:
        f = float(v)
    except Exception:
        return '-'
    if not np.isfinite(f):
        return '-'
    return ('%.' + str(p) + 'f%%') % (f * 100.0)

def load(nm):
    return pd.read_csv(os.path.join(BASE, nm))

d = load('three_flower_v2_breakout_analysis.csv')
for sec in d['section'].unique():
    sub = d[d['section'] == sec]
    P('')
    P('== ' + sec)
    P('  group | n | T+1/T+3/T+5/T+10/T+20 mean | med10 win10 pf10 | advF10(nB)')
    for g in sub['group'].unique():
        r = sub[sub['group'] == g]
        m = {int(x['horizon']): x for _, x in r.iterrows()}
        line = '  %-16s n=%-6s' % (g, int(m[5]['n']) if 5 in m else '-')
        line += ' | ' + ' / '.join(fmt((m[h]['mean'] if h in m else np.nan)) for h in (1, 3, 5, 10, 20))
        if 10 in m:
            line += ' | med %s win %s pf %s' % (fmt(m[10]['med']), fmt(m[10]['win'], 0), ('%.2f' % m[10]['pf'] if np.isfinite(m[10]['pf']) else '-'))
            line += ' | advF %s (nB=%s)' % (fmt(m[10]['adv_fix']), (int(m[10]['adv_fix_nb']) if np.isfinite(m[10]['adv_fix_nb']) else '-'))
        P(line)

for nm, tag in (('three_flower_v2_regime.csv', 'REGIME'), ('three_flower_v2_marketcap.csv', 'MKTCAP')):
    d2 = load(nm)
    P('')
    P('###### ' + tag)
    for sec in d2['section'].unique():
        sub = d2[d2['section'] == sec]
        P('')
        P('== ' + sec)
        for g in sub['group'].unique():
            r = sub[sub['group'] == g]
            m = {int(x['horizon']): x for _, x in r.iterrows()}
            if 10 not in m:
                continue
            P('  %-18s n=%-6s T+5 %s T+10 %s T+20 %s | med10 %s win10 %s pf10 %.2f | advF10 %s' % (
                g, int(m[10]['n']), fmt(m[5]['mean']) if 5 in m else '-', fmt(m[10]['mean']),
                fmt(m[20]['mean']) if 20 in m else '-', fmt(m[10]['med']), fmt(m[10]['win'], 0),
                (m[10]['pf'] if np.isfinite(m[10]['pf']) else float('nan')), fmt(m[10]['adv_fix'])))

w = load('three_flower_v2_volume_curve.csv')
P('')
P('###### VC')
P('  group | n | mean(T1..T20) | med(T1..T20) | win10 pf10 | mae10 mfe10')
for sec in w['section'].unique():
    sub = w[w['section'] == sec]
    P('== ' + sec)
    for g in sub['group'].unique():
        r = sub[sub['group'] == g]
        m = {int(x['horizon']): x for _, x in r.iterrows()}
        P('  %-14s n=%-6s | %s | %s | win10 %s pf10 %.2f | mae %s mfe %s' % (
            g, int(m[10]['n']),
            ' '.join(fmt(m[h]['mean']) for h in (1, 3, 5, 10, 20) if h in m),
            ' '.join(fmt(m[h]['med']) for h in (1, 3, 5, 10, 20) if h in m),
            fmt(m[10]['win'], 0), (m[10]['pf'] if np.isfinite(m[10]['pf']) else float('nan')),
            fmt(m[10]['mae_med']), fmt(m[10]['mfe_med'])))

with open(r'd:\mystock\solo\research\out\_v2dump.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(OUT))
print('OK rows=%d' % len(OUT))
