# -*- coding: utf-8 -*-
"""_fs_probe12: 校验 events_base 质量"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fs_common import DATA, Log

log = Log('_fs_probe12.txt')
ev = pd.read_parquet(os.path.join(DATA, 'events_base.parquet'))
log('shape=%s' % (ev.shape,))
log('股票=%d  end_date %s~%s  ann_date %s~%s' % (
    ev['ts_code'].nunique(), ev['end_date'].min(), ev['end_date'].max(),
    ev['ann_date'].min(), ev['ann_date'].max()))
log('ann_date 年度分布: %s' % ev['ann_date'].str[:4].value_counts().sort_index().to_dict())
log('end_date 季度分布: %s' % ev['end_date'].str[4:].value_counts().to_dict())
log('')
key = ['rev_yoy', 'np_yoy', 'dp_yoy', 'ocf_yoy', 'ocf_to_np', 'gpm', 'gpm_chg',
       'roe', 'roe_chg', 'roic', 'roic_chg', 'total_assets_g', 'inventories_g',
       'accounts_receiv_g', 'debt_g', 'ar_vs_rev', 'inv_vs_rev', 'debt_vs_rev',
       'S1_rev', 'S1_np', 'S1_dp', 'S1_ocf', 'S2_rev', 'S2_np', 'S2_dp', 'S2_ocf',
       'S4_np_vs_rev', 'S5_np_vs_ocf']
log('--- 缺失率 / 有效数 ---')
for c in key:
    if c in ev.columns:
        v = pd.to_numeric(ev[c], errors='coerce')
        log('  %-18s miss=%.4f  n=%d  mean=%+.4f  p01=%+.4f p99=%+.4f' % (
            c, v.isna().mean(), v.notna().sum(), v.mean(),
            v.quantile(0.01), v.quantile(0.99)))
    else:
        log('  %-18s [不存在]' % c)
log('')
sub = ev[ev['ann_date'] >= '20180101']
log('2018 以后事件=%d / %d 只' % (len(sub), sub['ts_code'].nunique()))
for c in ('rev_yoy', 'np_yoy', 'S1_np', 'S2_np', 'S4_np_vs_rev'):
    v = pd.to_numeric(sub[c], errors='coerce')
    log('  2018+ %-14s miss=%.4f' % (c, v.isna().mean()))
log('ann_agree=%s' % sub['ann_agree'].mean())
log('gap_days 中位=%.0f p01=%.0f p99=%.0f' % (
    sub['gap_days'].median(), sub['gap_days'].quantile(0.01), sub['gap_days'].quantile(0.99)))
log.save()
print('DONE')
