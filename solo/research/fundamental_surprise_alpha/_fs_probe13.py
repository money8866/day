# -*- coding: utf-8 -*-
"""校验 v3 panel 的质量：时间轴、反应变量、控制变量、前向收益覆盖"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fs_common import DATA, Log

log = Log('_fs_probe13.txt')
d = pd.read_parquet(os.path.join(DATA, 'panel.parquet'))
log('panel shape=%s  股票=%d' % (d.shape, d['ts_code'].nunique()))
log('列数=%d' % d.shape[1])

log('-' * 60)
log('A) 时间轴真实性（§4/§5）')
log('  ann_date 唯一(无 in-place) 检查: %s' % ('ann_date' in d.columns))
log('  delay_days 分布: ' + str(d['delay_days'].describe(percentiles=[.01, .5, .9, .99]).round(2).to_dict()))
chk = (pd.to_datetime(d['cal_date_k1'], format='%Y%m%d') > pd.to_datetime(d['ann_date'], format='%Y%m%d'))
log('  cal_date_k1 严格晚于 ann_date 占比: %.6f' % chk.mean())
log('  ann_date 为周末的占比: %.4f' % (pd.to_datetime(d['ann_date'], format='%Y%m%d').dt.dayofweek >= 5).mean())

log('-' * 60)
log('B) 价格反应变量覆盖（§14）')
for c in ['Gap', 'Ret_R1', 'Intra_R1', 'Pos_R1', 'VolR_R1', 'Cum_R3', 'Cum_R5',
          'RS_R1', 'RS_Gap', 'RS_Cum3']:
    if c in d.columns:
        v = pd.to_numeric(d[c], errors='coerce')
        log('  %-10s miss=%.4f  mean=%+.5f  std=%.5f' % (c, v.isna().mean(), v.mean(), v.std()))
    else:
        log('  %-10s 缺失列!' % c)

log('-' * 60)
log('C) 控制变量覆盖（§13/§17/§19）')
for c in ['mom_5', 'mom_10', 'mom_20', 'mom_60', 'turnover_rate', 'volume_ratio',
          'pe_ttm', 'pb', 'ps', 'ps_ttm', 'dv_ttm', 'total_mv', 'circ_mv', 'close_t0']:
    if c in d.columns:
        v = pd.to_numeric(d[c], errors='coerce')
        log('  %-14s miss=%.4f  median=%.4f' % (c, v.isna().mean(), v.median()))
    else:
        log('  %-14s 缺失列!' % c)

log('-' * 60)
log('D) 基本面特征覆盖（§7-§12）')
fcols = [c for c in d.columns if c.startswith(('rev_', 'np_', 'dp_', 'ocf', 'gpm', 'npm',
                                              'roe', 'roic', 'S1_', 'S2_', 'S3_',
                                              'total_assets_g', 'inventories_g',
                                              'accounts_receiv_g', 'debt_g', 'goodwill_g',
                                              'fix_assets_g', 'ar_vs_rev', 'inv_vs_rev',
                                              'debt_vs_rev', 'capex_g'))]
log('  基本面特征列数=%d' % len(fcols))
miss = {c: float(pd.to_numeric(d[c], errors='coerce').isna().mean()) for c in fcols}
miss = dict(sorted(miss.items(), key=lambda x: -x[1]))
log('  缺失率 Top20:')
for k, v in list(miss.items())[:20]:
    log('    %-24s %.4f' % (k, v))

log('-' * 60)
log('E) 前向收益覆盖（§21/§22）')
for h in (5, 10, 20, 60):
    for tag in ('E1', 'E2', 'E3', 'E5'):
        c = 'ex_%s_T%d' % (tag, h)
        if c in d.columns:
            v = pd.to_numeric(d[c], errors='coerce')
            log('  %-10s miss=%.4f  mean=%+.5f  std=%.5f' % (c, v.isna().mean(), v.mean(), v.std()))

log('-' * 60)
log('F) 分期 × 分布')
log(str(pd.crosstab(d['year'], d['period'])))
log('  行业数=%d  行业缺失=%.4f' % (d['ind_l1'].nunique(), d['ind_l1'].isna().mean()))

log('-' * 60)
log('G) 每股事件数分布（重复公告密度）')
vc = d.groupby('ts_code').size()
log('  min=%d median=%.0f max=%d' % (vc.min(), vc.median(), vc.max()))
dup = d.duplicated(['ts_code', 'end_date']).sum()
log('  同一 (ts_code,end_date) 重复行数=%d' % int(dup))

log.save()
print('DONE')
