# -*- coding: utf-8 -*-
"""_fs_probe15: 核心结果速览（只读 out/，不改任何东西）"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fs_common import OUTD, Log

log = Log('_fs_probe15.txt')
pd.set_option('display.width', 240)
pd.set_option('display.max_columns', 40)


def main():
    ic = pd.read_csv(os.path.join(OUTD, 'ic_full.csv'))
    sp = pd.read_csv(os.path.join(OUTD, 'spread.csv'))
    ds = pd.read_csv(os.path.join(OUTD, 'disc.csv'))
    bl = pd.read_csv(os.path.join(OUTD, 'baseline.csv'))
    qd = pd.read_csv(os.path.join(OUTD, 'quadrant.csv'))
    log('ic %s  sp %s  ds %s  bl %s  qd %s' % (ic.shape, sp.shape, ds.shape,
                                              bl.shape, qd.shape))

    log('-' * 70)
    log('A) §24 Baseline（T+5 / T+20 / T+10, anchor=E1 / E3）')
    b = bl[bl['horizon'].isin([5, 20])].copy()
    cols = ['anchor', 'baseline', 'horizon', 'ic_mean', 'icir', 'spr10', 'spr20',
            'auc', 'prec', 'ic_train', 'ic_valid', 'ic_oos']
    for c in cols:
        if c not in b.columns:
            b[c] = np.nan
    log(b[cols].round(4).to_string(index=False))

    log('-' * 70)
    log('B) §21 Top-25 特征（anchor=E1, variant=raw, horizon=T+5, ALL）')
    r5 = ic[(ic['anchor'] == 'E1') & (ic['variant'] == 'raw') &
            (ic['horizon'] == 5) & (ic['period'] == 'ALL')].copy()
    r5['absic'] = r5['ic_mean'].abs()
    r5 = r5.sort_values('absic', ascending=False).head(25)
    log(r5[['feature', 'category', 'ic_mean', 'ic_med', 'ic_std', 'icir', 'pos',
            'n_coh']].round(4).to_string(index=False))

    log('-' * 70)
    log('C) §17 Momentum 残差（E1, T+5, ALL）：原始 vs resid_mom vs resid_all')
    def pick(vn):
        z = ic[(ic['anchor'] == 'E1') & (ic['variant'] == vn) &
               (ic['horizon'] == 5) & (ic['period'] == 'ALL')]
        return z.set_index('feature')['ic_mean']
    t = pd.DataFrame({'raw': pick('raw'), 'mom': pick('resid_mom'),
                      'val': pick('resid_val'), 'ind': pick('resid_ind'),
                      'all': pick('resid_all')})
    t['abs_raw'] = t['raw'].abs()
    t = t.sort_values('abs_raw', ascending=False).head(25)
    log(t.round(4).to_string())

    log('-' * 70)
    log('D) §22 Spread / §23 Disc（E1, raw, T+5）Top-20 by |spr10|')
    s5 = sp[(sp['anchor'] == 'E1') & (sp['variant'] == 'raw') & (sp['horizon'] == 5)].copy()
    d5 = ds[(ds['anchor'] == 'E1') & (ds['variant'] == 'raw') & (ds['horizon'] == 5)].copy()
    m = s5.merge(d5, on=['feature', 'anchor', 'variant', 'horizon'], how='left',
                 suffixes=('', '_d'))
    m['abs_s'] = m['spr10'].abs()
    m = m.sort_values('abs_s', ascending=False).head(20)
    log(m[['feature', 'spr10', 'spr10_t', 'spr20', 'net10', 'net30', 'net50',
           'auc', 'eff', 'stdgap', 'ranksep', 'prec']].round(4).to_string(index=False))

    log('-' * 70)
    log('E) §25 四象限 T+5 ALL（A=高基本面/低反应）')
    q = qd[(qd['horizon'] == 5) & (qd['period'] == 'ALL')]
    p = q.pivot_table(index=['react', 'fund'], columns='group',
                      values='mean_exret').reset_index()
    log(p.round(4).to_string(index=False))
    log('  A - C（高基本面内 低反应 vs 低反应?）以上为各组绝对超额收益均值')
    p2 = q.pivot_table(index=['react', 'fund'], columns='group',
                       values='mean_exret')
    if 'A_Fhi_Plow' in p2.columns and 'B_Fhi_Phigh' in p2.columns:
        log('  A-B = %s' % (p2['A_Fhi_Plow'] - p2['B_Fhi_Phigh']).round(4).to_dict())

    log('-' * 70)
    log('F) 审计')
    au = pd.read_csv(os.path.join(OUTD, 'audit_dataset.csv'))
    au.columns = ['k', 'value']
    log(au.to_string(index=False))
    log.save()


if __name__ == '__main__':
    main()
