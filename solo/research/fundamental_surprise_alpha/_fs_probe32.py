# -*- coding: utf-8 -*-
"""探明 panel.parquet 可用字段：价格/收益/流动性/行业/日期键（供可交易性闸门使用）"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fs_common import DATA, OUTD, Log

log = Log('_fs_probe32.txt')

p = os.path.join(DATA, 'panel.parquet')
df = pd.read_parquet(p)
log('panel %s 行=%d 列=%d' % (p, len(df), df.shape[1]))
log('')
cols = list(df.columns)
groups = {
    'k/date': [c for c in cols if c in ('k', 'k1', 'trade_date', 'ann_date', 'end_date')
               or 'date' in c],
    'entry_px': [c for c in cols if c.startswith('entry_px')],
    'ret_': [c for c in cols if c.startswith('ret_')],
    'ex_': [c for c in cols if c.startswith('ex_')],
    'price/react': [c for c in cols if c in ('Gap', 'Ret_R1', 'Intra_R1', 'Pos_R1',
                                             'VolR_R1', 'RS_R1', 'Cum_R3', 'RS_Cum3',
                                             'Cum_R5')],
    'liq/size': [c for c in cols if c in ('total_mv', 'circ_mv', 'turnover_rate',
                                          'volume_ratio', 'amount', 'vol', 'close',
                                          'open', 'pre_close', 'high', 'low')],
    'ind': [c for c in cols if c.startswith('ind')],
    'target feat': ['rev_acc', 'np_qoq', 'np_acc', 'rev_qoq', 'dp_acc', 'mom_20',
                    'mom_5', 'mom_60', 'ln_mv'],
}
for g, cs in groups.items():
    log('  %-12s %s' % (g, cs))
log('')
log('全部列名:')
log('  ' + ', '.join(cols))
log('')

# 关键列的非空率
for c in ['k', 'k1', 'ann_date', 'entry_px_E1', 'entry_px_E2',
          'ex_E1_T5', 'ex_E1_T20', 'ex_E2_T5', 'ret_E1_T5', 'ret_E2_T5',
          'Gap', 'turnover_rate', 'total_mv', 'rev_acc', 'np_qoq', 'ind_l1']:
    if c in df.columns:
        log('  %-14s notna=%.4f' % (c, float(df[c].notna().mean())))
    else:
        log('  %-14s MISSING' % c)
log('')

# 交易日期上下文
from fs_common import CD
cal = pd.read_parquet(os.path.join(DATA, 'calendar.parquet'))
log('calendar 列=%s 行=%d' % (list(cal.columns), len(cal)))
log(cal.head(3).to_string())
log(cal.tail(3).to_string())

log.save()
print('DONE')
