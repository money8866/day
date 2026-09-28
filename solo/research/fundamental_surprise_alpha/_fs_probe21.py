# -*- coding: utf-8 -*-
"""_fs_probe21: 汇总 E1 主锚点关键指标（不依赖面板，仅读 out/*.csv）"""
import os
import numpy as np
import pandas as pd

OUTD = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')


def rd(n):
    p = os.path.join(OUTD, n)
    return pd.read_csv(p) if os.path.exists(p) else pd.DataFrame()


ic = rd('ic_full.csv')
sp = rd('spread.csv')
ds = rd('disc.csv')
cf = rd('counterfactual.csv')
oos = rd('oos.csv')
wf = rd('walkforward.csv')
rg = rd('regime.csv')
pg = rd('parameter_grid.csv')

ic = ic[ic['anchor'] == 'E1']
sp = sp[(sp['anchor'] == 'E1') & (sp['variant'] == 'raw')]
ds = ds[(ds['anchor'] == 'E1') & (ds['variant'] == 'raw')]
oos = oos[oos['anchor'] == 'E1'] if len(oos) else oos
wf = wf[wf['anchor'] == 'E1'] if len(wf) else wf
rg = rg[rg['anchor'] == 'E1'] if len(rg) else rg


def build(H):
    z = ic[(ic['horizon'] == H) & (ic['period'] == 'ALL')]
    piv = z.pivot_table(index='feature', columns='variant', values='ic_mean')
    base = z[z['variant'] == 'raw'].set_index('feature')
    piv['icir'] = base['icir']
    piv['n_coh'] = base['n_coh']
    s = sp[sp['horizon'] == H].set_index('feature')
    piv = piv.join(s[['spr10', 'spr20', 'spr10_t', 'net10', 'net20', 'net30', 'net50']])
    d = ds[ds['horizon'] == H].set_index('feature')
    piv = piv.join(d[['auc', 'eff', 'stdgap', 'ranksep', 'prec']])
    c = cf[cf['horizon'] == H].set_index('feature')
    piv = piv.join(c[['cf_alpha', 'cf_t']])
    o = oos[oos['horizon'] == H].set_index('feature')
    piv = piv.join(o[['TRAIN', 'VALID', 'OOS', 'same_sign_3']])
    w = wf[wf['horizon'] == H].groupby('feature').agg(
        wf_same=('same_sign', 'mean'), wf_ic_oos=('ic_oos', 'mean'))
    piv = piv.join(w)
    r = piv['raw'].dropna()
    return piv.reindex(r.abs().sort_values(ascending=False).index)


cols = ['raw', 'resid_mom', 'resid_val', 'resid_siz', 'resid_ind', 'resid_all',
        'icir', 'n_coh', 'spr10', 'spr10_t', 'net30', 'auc', 'prec',
        'TRAIN', 'VALID', 'OOS', 'same_sign_3', 'wf_same', 'cf_alpha', 'cf_t']

p5 = build(5)
print('=== E1 / T+5 全样本：按 |raw IC| 排序（前 30）===')
print(p5[cols].head(30).round(4).to_string())
p5.to_csv(os.path.join(OUTD, '_probe21_e1_h5.csv'), encoding='utf-8-sig')

p20 = build(20)
print('\n=== E1 / T+20：按 |raw IC| 排序（前 20）===')
print(p20[['raw', 'resid_mom', 'resid_val', 'resid_siz', 'resid_ind', 'resid_all',
           'icir', 'spr10', 'net30', 'auc']].head(20).round(4).to_string())

print('\n=== 成本：T+5 Top10-Bot10（raw，全锚点）===')
sp5 = sp[sp['horizon'] == 5]
print('  n=%d  毛价差>0: %d   >0.1%%: %d   >0.2%%: %d   >0.3%%: %d   30bp后>0: %d'
      % (len(sp5), int((sp5['spr10'] > 0).sum()), int((sp5['spr10'] > 0.001).sum()),
         int((sp5['spr10'] > 0.002).sum()), int((sp5['spr10'] > 0.003).sum()),
         int((sp5['net30'] > 0).sum())))

oo5 = oos[oos['horizon'] == 5]
print('\n=== §28 OOS 三期同向（T+5，E1）===')
print('  n=%d  same_sign_3 占比 %.4f  (TRAIN均值 %.4f / VALID %.4f / OOS %.4f)'
      % (len(oo5), float(oo5['same_sign_3'].mean()), oo5['TRAIN'].mean(),
         oo5['VALID'].mean(), oo5['OOS'].mean()))

print('\n=== §30 Regime（T+5，E1，全部特征）===')
print(rg[rg['horizon'] == 5].groupby('regime')['ic_mean']
      .agg(['count', 'mean', 'median', lambda s: float((s > 0).mean())])
      .rename(columns={'<lambda_0>': 'ic_pos_ratio'}).round(4).to_string())

print('\n=== §29 Walk-forward（T+5，E1，全部特征）===')
w5 = wf[wf['horizon'] == 5]
print(w5.groupby('window').agg(n=('same_sign', 'size'), same_sign=('same_sign', 'mean'),
                               ic_train=('ic_train', 'mean'),
                               ic_oos=('ic_oos', 'mean')).round(4).to_string())

print('\n=== §31 Counterfactual 方向一致性（E1）===')
for H in (5, 20):
    c = cf[cf['horizon'] == H]
    print('  H=%d  n=%d  正且|t|>2: %d (%.3f)  负且|t|>2: %d (%.3f)  中位 cf_alpha %.5f'
          % (H, len(c), int(((c['cf_alpha'] > 0) & (c['cf_t'].abs() > 2)).sum()),
             float(((c['cf_alpha'] > 0) & (c['cf_t'].abs() > 2)).mean()),
             int(((c['cf_alpha'] < 0) & (c['cf_t'].abs() > 2)).sum()),
             float(((c['cf_alpha'] < 0) & (c['cf_t'].abs() > 2)).mean()),
             float(c['cf_alpha'].median())))

print('\n=== §32 参数网格 ===')
if len(pg):
    print(pg.groupby(['param', 'value'])['ic_resid']
          .agg(['count', 'mean', 'median']).round(4).to_string())
else:
    print('  （parameter_grid.csv 尚未生成）')
print('\nDONE')
