# -*- coding: utf-8 -*-
"""紧凑导出：候选组均值/PF/成本曲线/逐年/匹配显著性"""
import os
import pandas as pd

OUT = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(OUT)
D = '20260925'
O = pd.read_csv(os.path.join(ROOT, 'post_firstboard_alpha_oos_%s.csv' % D))
C = pd.read_csv(os.path.join(ROOT, 'post_firstboard_alpha_counterfactual_%s.csv' % D))

print('=== BASE ===')
print(O[O.section == 'BASE'].drop(columns=['section']).round(4).to_string(index=False))

print('\n=== CAND 均值 / PF ===')
c = O[O.section == 'CAND']
for col in ('t3_med', 't3_mean', 't3_pf', 't5_med', 't5_mean', 't5_pf', 't10_med', 't10_mean'):
    print('\n--', col)
    print(c.pivot_table(index='group', columns='period', values=col)
          .reindex(columns=['ALL', 'TRAIN', 'VALID', 'OOS']).round(4).to_string())

print('\n=== COST 曲线（T+5 中位数, 各滑点）===')
k = O[O.section == 'COST']
for sl in (0.0, 0.001, 0.002, 0.003):
    t = k[k.slip == sl]
    print('\n-- slip %.1f%%  成本 %.3f%%' % (sl * 100, sl * 200 + 0.055))
    print(t.pivot_table(index='group', columns='period', values='t5_med')
          .reindex(columns=['ALL', 'TRAIN', 'VALID', 'OOS']).mul(100).round(3).to_string())

print('\n=== 逐年 T+5 中位数 % ===')
y = O[O.section == 'OOS_年度']
print(y.pivot_table(index='group', columns='period', values='t5_med').mul(100).round(3).to_string())
print('\n-- 逐年 N')
print(y.pivot_table(index='group', columns='period', values='N').to_string())

print('\n=== CF 匹配 delta(pp) 与 t ===')
cf = C[C.section == 'CF_匹配']
print(cf.pivot_table(index='group', columns='period', values='matched_delta')
      .reindex(columns=['ALL', 'TRAIN', 'VALID', 'OOS']).mul(100).round(3).to_string())
print('\n-- matched_t')
print(cf.pivot_table(index='group', columns='period', values='matched_t')
      .reindex(columns=['ALL', 'TRAIN', 'VALID', 'OOS']).round(2).to_string())
print('\n-- n_strata / N_matched')
print(cf[['group', 'period', 'n_strata', 'N_matched']].to_string(index=False))
