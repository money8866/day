# -*- coding: utf-8 -*-
"""_fs_probe23: 头部候选的 regime / walk-forward 明细"""
import os
import pandas as pd

OUTD = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')
rg = pd.read_csv(os.path.join(OUTD, 'regime.csv'))
wf = pd.read_csv(os.path.join(OUTD, 'walkforward.csv'))
tops = ['rev_acc', 'np_qoq', 'np_acc', 'dp_acc', 'dp_qoq', 'rev_qoq',
        'S2h4_np', 'turnover_rate', 'mom_20', 'bp']
for f in tops:
    r = rg[(rg['anchor'] == 'E1') & (rg['horizon'] == 5) & (rg['feature'] == f)]
    d = {x['regime']: round(x['ic_mean'], 4) for _, x in r.iterrows()}
    n = {x['regime']: int(x['n']) for _, x in r.iterrows()}
    w = wf[(wf['anchor'] == 'E1') & (wf['horizon'] == 5) & (wf['feature'] == f)]
    print('%-14s regime=%s  n=%s  wf_same=%.3f  wf_ic_oos均值=%s' % (
        f, d, n, w['same_sign'].mean(), [round(v, 4) for v in w['ic_oos']]))
