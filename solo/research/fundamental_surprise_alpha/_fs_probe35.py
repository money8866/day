# -*- coding: utf-8 -*-
"""探明 HVT-BULL te_backtest 事件表结构 + 与本研究面板的可拼接性（只读）"""
import os
import numpy as np
import pandas as pd
from fs_common import DATA, Log

log = Log('_fs_probe35.txt')
TE = r'd:\mystock\solo\report_daily\te_backtest_events_20250101_20260828.csv'
COLS = ['ts_code', 'name', 'signal_date', 'decision_date', 'decision_lag',
        'decision_point', 'v3_state', 'signal_tier', 'execution_score',
        'execution_state', 'next_day_action', 'stock_type', 'confirmation_level',
        'buyability', 'continuation_score', 'fe_score', 'fundamental_score',
        'position_size', 'r1', 'r5', 'r10', 'r20', 'er10', 'er20', 'er60']

log('=' * 70)
log('1) 事件表结构')
d = pd.read_csv(TE, usecols=lambda c: c in COLS, low_memory=False)
log('  行数 %d  列 %s' % (len(d), list(d.columns)))
log('  decision_date: %s ~ %s  唯一日 %d'
    % (d['decision_date'].min(), d['decision_date'].max(),
       d['decision_date'].nunique()))
log('  ts_code 唯一 %d' % d['ts_code'].nunique())

log('')
log('2) 缺失率')
for c in d.columns:
    nn = float(pd.to_numeric(d[c], errors='coerce').notna().mean()) \
        if d[c].dtype != object else float(d[c].notna().mean())
    log('  %-22s %-8s notna=%.4f' % (c, str(d[c].dtype), nn))

log('')
log('3) 决策分类分布')
for c in ('decision_point', 'v3_state', 'execution_state', 'next_day_action',
          'signal_tier', 'stock_type'):
    if c in d.columns:
        vc = d[c].value_counts(dropna=False).head(12)
        log('  [%s]' % c)
        log('    ' + vc.to_string().replace('\n', '\n    '))

log('')
log('4) execution_score 分布（决策日）')
es = pd.to_numeric(d['execution_score'], errors='coerce')
log('  n=%d  min=%.2f p10=%.2f p50=%.2f p90=%.2f max=%.2f'
    % (es.notna().sum(), es.min(), es.quantile(.1), es.median(),
       es.quantile(.9), es.max()))
log('  按 decision_date 每日事件数：中位 %.1f  p90 %.1f  max %d'
    % (d.groupby('decision_date').size().median(),
       d.groupby('decision_date').size().quantile(.9),
       d.groupby('decision_date').size().max()))

log('')
log('5) decision_lag 分布（signal→decision）')
lag = pd.to_numeric(d['decision_lag'], errors='coerce')
log('  ' + lag.describe(percentiles=[.5, .9, .99]).to_string().replace('\n', '\n  '))

log('')
log('6) 与本研究面板的可拼接性')
p = pd.read_parquet(os.path.join(DATA, 'panel.parquet'),
                    columns=['ts_code', 'ann_date', 'k1', 'cal_date_k1',
                             'rev_acc', 'np_qoq'])
p['ann_date'] = p['ann_date'].astype(str)
log('  panel 事件 %d 行 / ts_code %d / ann_date %s ~ %s'
    % (len(p), p['ts_code'].nunique(), p['ann_date'].min(), p['ann_date'].max()))
codes_te = set(d['ts_code'].unique())
codes_pn = set(p['ts_code'].unique())
log('  te 代码 %d  panel 代码 %d  交集 %d  仅te %d'
    % (len(codes_te), len(codes_pn), len(codes_te & codes_pn),
       len(codes_te - codes_pn)))
# 时间覆盖：te 的 decision_date 落在 panel 的 ann_date 区间内？
lo, hi = d['decision_date'].min(), d['decision_date'].max()
sub = p[(p['ann_date'] <= hi)]
log('  decision_date 区间 %s ~ %s；panel 中 ann_date<=%s 的行数 %d'
    % (lo, hi, hi, len(sub)))
# 可命中率预估：每个 te 事件 (ts_code, decision_date) 往前找最近 ann_date
log.save()
print('DONE')
