# -*- coding: utf-8 -*-
"""Compact dump of selected sections for report writing (read-only)."""
import os
import pandas as pd

OUT = os.path.dirname(os.path.abspath(__file__))
E = pd.read_csv(os.path.join(OUT, 'three_stage_rebound_experiments_20260925.csv'))
O = pd.read_csv(os.path.join(OUT, 'three_stage_rebound_oos_20260925.csv'))
G = pd.read_csv(os.path.join(OUT, 'three_stage_rebound_edge_20260925.csv'))


def show(df, sec, period=None, dropna_col=None):
    d = df[df['section'] == sec]
    if period is not None:
        pc = 'period' if 'period' in d.columns else None
        if pc:
            d = d[d[pc] == period]
    if dropna_col:
        d = d[d[dropna_col].notna()]
    if d.empty:
        print('--- %s [empty]' % sec)
        return
    cols = [c for c in d.columns if c not in ('section', 'layer', 'mode', 'note', 'period')]
    print('=== %s  (%d rows)' % (sec, len(d)))
    print(d[cols].to_string(index=False))
    print()


print('### EXPERIMENTS')
for s in ('L1_分歧定义候选',):
    show(E, s, 'ALL')
for s in ('L2_卖压衰减曲线',):
    show(E, s, 'ALL')
for s in ('L2_缩量阈值',):
    show(E, s, 'ALL')
for s in ('L3_结构未破',):
    show(E, s, 'ALL')
for s in ('L4_再启动',):
    show(E, s, 'ALL')
for s in ('L5_再启动量能',):
    show(E, s, 'ALL')
for s in ('L6_Entry',):
    show(E, s, 'ALL')

print('### LAYERS (ALL)')
for s in ('LAYER_Regime', 'LAYER_首板质量', 'LAYER_板块', 'LAYER_市值档', 'LAYER_换手档'):
    show(E, s, 'ALL')

print('### COST')
show(E, 'COST_滑点', 'OOS')

print('### OOS file')
for s in ('OOS_分期', 'WF_窗口'):
    show(O, s)

print('### EDGE')
for s in ('EDGE_事件链', 'EDGE_Regime', 'EDGE_逐年', 'EDGE_对照', 'EDGE_滑点'):
    d = G[G['section'] == s]
    print('=== %s (%d rows)' % (s, len(d)))
    print(d.drop(columns=['section']).to_string(index=False))
    print()
