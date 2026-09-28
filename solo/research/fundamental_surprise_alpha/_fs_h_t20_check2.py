# -*- coding: utf-8 -*-
"""决定性检验：gate2 simulate_nav 是否消费「入场日」那一天的篮子收益与 30bp 成本"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _fs_h_t20_lib import load_env, frozen_select, COST
from _fs_trade_gate2 import build_day_baskets, simulate_nav

env = load_env(verbose=False)
td, NCAL = env['td'], env['NCAL']
sel = frozen_select(env['df'], env['mv80'], 'rev_acc')

rm0, _, _ = build_day_baskets(sel, env['cmap'], env['C'], env['O'], NCAL, 0.0)
rm3, _, _ = build_day_baskets(sel, env['cmap'], env['C'], env['O'], NCAL, COST)
nav0 = simulate_nav(rm0, NCAL)
nav3 = simulate_nav(rm3, NCAL)
print('成本 0bp 时 NAV 终值 = %.8f' % nav0[-1])
print('成本 30bp 时 NAV 终值 = %.8f' % nav3[-1])
print('两者之差 = %.10e  → %s'
      % (nav3[-1] - nav0[-1],
         '成本未被消费（gate2 组合层是「零成本」读数）' if abs(nav3[-1] - nav0[-1]) < 1e-12
         else '成本被消费'))

# 入场日收益是否被消费：把 dm 里除 entry_day 外的键清空，看 NAV 是否还动
rmx = {}
for a, (dm, exp) in rm3.items():
    d2 = {k: v for k, v in dm.items() if k != a}
    rmx[a] = (d2, exp)
navx = simulate_nav(rmx, NCAL)
print('')
print('把「除入场日外全部清空」后 NAV 终值 = %.8f' % navx[-1])
rmy = {}
for a, (dm, exp) in rm3.items():
    rmy[a] = ({a: dm[a]}, exp)
navy = simulate_nav(rmy, NCAL)
print('把「只留入场日」后        NAV 终值 = %.8f' % navy[-1])
print('→ %s' % ('入场日收益未被消费（gate2 = E2 收盘入场口径）'
                if abs(navy[-1] - 1.0) < 1e-12 else '入场日收益被消费'))
print('')
print('DONE')
