# -*- coding: utf-8 -*-
"""汇总 §25 参数网格 / §29 Regime / §27 成本 / §28 时间稳定性"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
pd.set_option('display.width', 300)
pd.set_option('display.max_rows', 500)


def rd(n):
    p = os.path.join(HERE, n)
    return pd.read_csv(p) if os.path.exists(p) else None


print('===== §25-a 选品分位网格（spread）=====')
g = rd('breakout_alpha_parameter_grid.csv')
q = g[g.grid_type == 'quantile']
print(q.pivot_table(index=['feature', 'horizon'], columns='param_value', values='spread')
      .round(4).to_string())

print('\n===== §25-a 横向: 每个分位点的 spread 符号是否全一致 =====')
for (f, h), grp in q.groupby(['feature', 'horizon']):
    v = grp.sort_values('param_value')['spread'].values
    print('%-24s H=%d  spread=%s  signs=%s  单调=%s' % (
        f, h, np.round(v, 4).tolist(), list(np.sign(v)), 
        'Y' if np.all(np.diff(v) <= 0) or np.all(np.diff(v) >= 0) else 'N'))

print('\n===== §25-b 压力位窗口网格 (Distance_to_R{N}) =====')
r = g[g.grid_type == 'resistance_N']
print(r.pivot_table(index=['param_value'], columns='horizon', values=['spread', 'ic_mean'])
      .round(4).to_string())

print('\n===== §25-c VR 阈值网格 =====')
v = g[g.grid_type == 'vr_threshold']
print(v.pivot_table(index=['feature', 'param_value'], columns='horizon',
                    values=['top_ret', 'n_rows']).round(5).to_string())

print('\n===== §29 Regime =====')
rg = rd('breakout_alpha_regime.csv')
t = rg[rg.regime != 'DIRECTION_CHECK']
print(t.pivot_table(index=['feature', 'horizon'], columns='regime',
                    values=['ic_mean', 'spread10', 'auc']).round(4).to_string())
print('\n方向反转标记:')
print(rg[rg.regime == 'DIRECTION_CHECK'][['feature', 'horizon', 'regime_reversed']]
      .to_string(index=False))

print('\n===== §27 成本 =====')
c = rd('breakout_alpha_cost.csv')
print(c.pivot_table(index=['feature', 'horizon'], columns='slip',
                    values=['top10_net', 'spread_net']).round(4).to_string())

print('\n===== §28 时间稳定性 =====')
s = rd('breakout_alpha_stability.csv')
print(s.pivot_table(index=['feature', 'horizon'], columns='freq',
                    values=['positive_ratio', 'best_share_of_positive']).round(3).to_string())
print('\n各年 spread 明细（year）:')
y = s[s.freq == 'year']
print(y.pivot_table(index='feature', columns='best_bucket', values='mean_spread')
      .round(4).to_string())
print('\n季度正比例明细:')
qq = s[s.freq == 'quarter'][['feature', 'horizon', 'n_buckets', 'positive_ratio',
                             'ic_positive_ratio', 'best_share_of_positive']]
print(qq.round(3).to_string(index=False))
print('\n月度正比例明细:')
mm = s[s.freq == 'month'][['feature', 'horizon', 'n_buckets', 'positive_ratio',
                           'ic_positive_ratio', 'best_share_of_positive']]
print(mm.round(3).to_string(index=False))

print('\n===== §24 Walk Forward 汇总 =====')
w = rd('breakout_alpha_walkforward.csv')
w['ic_sign'] = np.sign(w['ic_mean'])
print(w.pivot_table(index=['feature', 'horizon'], columns='window',
                    values=['ic_mean', 'spread10']).round(4).to_string())
print('\n各窗口 IC 与 spread 的符号一致性:')
for (f, h), grp in w.groupby(['feature', 'horizon']):
    grp = grp.sort_values('window')
    print('%-24s H=%d IC符号=%s  spread符号=%s' % (
        f, h, list(grp['ic_sign'].astype(int)), list(np.sign(grp['spread10']).astype(int))))
