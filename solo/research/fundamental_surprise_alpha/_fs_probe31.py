# -*- coding: utf-8 -*-
"""§38 Registry 字段完备性 + §39 九文件 + §41 首行 校验"""
import os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUTD = os.path.join(HERE, 'out')

REQ38 = ['alpha_id', 'feature', 'formula', 'announcement_rule', 'entry_rule',
         'horizon', 'train_ic', 'validation_ic', 'oos_ic', 'icir',
         'top10_spread', 'top20_spread', 'auc', 'effect_size', 'std_gap',
         'momentum_residual_alpha', 'value_residual_alpha',
         'industry_neutral_alpha', 'counterfactual', 'parameter_stability',
         'regime_stability', 'gross_return', 'net_return_10bp',
         'net_return_20bp', 'net_return_30bp', 'net_return_50bp',
         'status', 'fail_reason']

REQ39 = ['fundamental_surprise_alpha_report.md',
         'fundamental_surprise_alpha_registry.csv',
         'fundamental_surprise_alpha_oos.csv',
         'fundamental_surprise_alpha_ic.csv',
         'fundamental_surprise_alpha_walkforward.csv',
         'fundamental_surprise_alpha_regime.csv',
         'fundamental_surprise_alpha_counterfactual.csv',
         'fundamental_surprise_alpha_parameter_grid.csv',
         'fundamental_surprise_alpha.json']

rg = pd.read_csv(os.path.join(OUTD, 'fundamental_surprise_alpha_registry.csv'))
miss = [c for c in REQ38 if c not in rg.columns]
print('§38 缺失字段:', miss if miss else '无（%d/%d 全在）' % (len(REQ38), len(REQ38)))
print('§38 附加字段:', [c for c in rg.columns if c not in REQ38])
extra = [c for c in rg.columns if c.startswith('rank_separation') or
         c in ('rank_separation', 'precision_at_10')]
print('§23 附加指标:', extra)

print()
for f in REQ39:
    p = os.path.join(OUTD, f)
    print('  %-52s %s' % (f, 'OK %.1fKB' % (os.path.getsize(p) / 1024)
                          if os.path.exists(p) else 'MISSING'))

print()
head = open(os.path.join(OUTD, 'fundamental_surprise_alpha_report.md'),
            encoding='utf-8').read().split('\n')[:5]
for ln in head:
    print(ln)

print()
print('registry 行数', len(rg), '| status:', rg['status'].value_counts().to_dict())
print('PASS 行:')
print(rg[rg['status'] == 'PASS'][['feature', 'ic_mean', 'parameter_stability',
                                  'regime_stability', 'counterfactual']].to_string(index=False))

pg = pd.read_csv(os.path.join(OUTD, 'fundamental_surprise_alpha_parameter_grid.csv'))
print('\nparameter_grid 行数', len(pg), '| params:', sorted(pg['param'].unique().tolist()))
