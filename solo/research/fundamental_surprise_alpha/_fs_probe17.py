# -*- coding: utf-8 -*-
"""_fs_probe17: 严格前视污染（滞后报告 ann_date **严格晚于** 当期 ann_date）"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fs_common import DATA, Log

log = Log('_fs_probe17.txt')


def main():
    b = pd.read_parquet(os.path.join(DATA, 'events_base.parquet'))
    b = b.sort_values(['ts_code', 'end_date']).reset_index(drop=True)
    ad = b['ann_date'].astype(str)
    ed = b['end_date'].astype(str)
    tc = b['ts_code']

    log('events_base %s' % (b.shape,))
    for k in (1, 2, 3, 4):
        la = ad.groupby(tc).shift(k)
        ok = la.notna()
        eq = ok & (la == ad)
        gt = ok & (la > ad)          # 严格更晚 → 真前视
        log('shift(%d): 可比 %d  同日公告(年报+一季报) %.4f  严格晚于 %.4f (%d)' % (
            k, int(ok.sum()), eq.sum() / max(ok.sum(), 1),
            gt.sum() / max(ok.sum(), 1), int(gt.sum())))
        if k == 1 and gt.sum():
            sub = pd.DataFrame({'cur_end': ed[gt].values, 'cur_ann': ad[gt].values,
                                'lag_end': ed.groupby(tc).shift(k)[gt].values,
                                'lag_ann': la[gt].values})
            log('   严格晚于示例:')
            log(sub.head(10).to_string(index=False))
            log('   当期季度分布: %s' % (
                pd.Series(ed[gt].values).str[4:6].value_counts().to_dict()))

    # TTM 严格前视
    def ttm_flag(sub):
        a = sub['ann_date'].astype(str).values
        n = len(a)
        f = np.zeros(n, dtype=bool)
        for i in range(3, n):
            f[i] = bool((a[i - 3:i] > a[i]).any())
        return pd.Series(f, index=sub.index)

    fl = b.groupby('ts_code', sort=False, group_keys=False).apply(ttm_flag)
    log('TTM(rolling4) 严格含未公开报告占比 = %.5f (%d/%d)' % (
        fl.mean(), int(fl.sum()), len(fl)))
    if fl.sum():
        log('  当期季度分布: %s' % (
            b.loc[fl.values, 'end_date'].str[4:6].value_counts().to_dict()))

    # 一季报 vs 年报 是否同日
    log('-' * 70)
    m = b[b['end_date'].str[4:6] == '03'].copy()
    m['lag_ann'] = ad[m.index].groupby(m['ts_code']).shift(1)
    m['cur'] = m['ann_date'].astype(str)
    has = m['lag_ann'].notna()
    log('Q1 事件 %d：上一期(Q4年报) 与 Q1 同日公告 %.4f；年报更晚 %.5f' % (
        len(m), (has & (m['lag_ann'] == m['cur'])).sum() / max(has.sum(), 1),
        (has & (m['lag_ann'] > m['cur'])).sum() / max(has.sum(), 1)))

    # 全样本：同期(lag_ann == cur_ann)占比
    la1 = ad.groupby(tc).shift(1)
    log('=' * 70)
    log('全样本 shift(1) 同日公告占比 %.4f' % ((la1 == ad).sum() / max(la1.notna().sum(), 1)))
    log.save()


if __name__ == '__main__':
    main()
