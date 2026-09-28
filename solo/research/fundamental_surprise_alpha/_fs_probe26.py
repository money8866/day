# -*- coding: utf-8 -*-
"""检查 rev_acc / np_qoq 在 registry 中的完整行，并理清排序来源"""
import os
import pandas as pd
import numpy as np

pd.set_option('display.width', 250)
HERE = os.path.dirname(os.path.abspath(__file__))
OUTD = os.path.join(HERE, 'out')

rg = pd.read_csv(os.path.join(OUTD, 'fundamental_surprise_alpha_registry.csv'))
for f in ['rev_acc', 'np_qoq', 'np_acc']:
    s = rg[rg['feature'] == f]
    if len(s):
        r = s.iloc[0]
        print('--- %s (idx %d) ---' % (f, s.index[0]))
        for c in ['ic_mean', 'icir', 'train_ic', 'validation_ic', 'oos_ic',
                  'ic_mean_T20', 'top10_spread', 'top20_spread', 'top10_t',
                  'net_return_30bp', 'gross_return', 'auc', 'effect_size',
                  'momentum_residual_alpha', 'value_residual_alpha',
                  'industry_neutral_alpha', 'all_neutral_alpha',
                  'counterfactual', 'counterfactual_t', 'regime_stability',
                  'wf_same_sign', 'wf_n_window', 'parameter_stability',
                  'gate_pass_n', 'status', 'fail_reason']:
            print('   %-26s %s' % (c, r.get(c)))
        g = [c for c in rg.columns if c.startswith('G') and c[1].isdigit()]
        print('   gates:', {c: bool(r.get(c)) for c in g})

# 排序来源：比较 ic_mean 与该行 train_ic
print('\nregistry 内 ic_mean 与 train_ic 是否一致（前 5 行）:')
print(rg[['feature', 'ic_mean', 'train_ic', 'validation_ic', 'oos_ic']].head(5).to_string(index=False))
