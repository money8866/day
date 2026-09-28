# -*- coding: utf-8 -*-
"""H-T20 前置校验（只读）：
  A) 用 gate2 原函数逐字复现 ARCHIVE 发布读数（G1-G3 冻结证据）
  B) 验证 gate2 `simulate_nav` 是否消费入场日（key=k1）那一天的篮子收益
     —— 若未消费，则 (i) 入场日开盘→收盘收益缺失，(ii) 挂在 R[:,0] 上的 30bp 成本也缺失
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _fs_h_t20_lib import (load_env, frozen_select, build_baskets, sim_fixed,
                           COST, COST_SIDE, ARCHIVE_SLOT, HORIZONS)
from _fs_trade_gate2 import build_day_baskets, simulate_nav, metrics

env = load_env()
td, lvl, NCAL = env['td'], env['lvl'], env['NCAL']
sel = frozen_select(env['df'], env['mv80'], 'rev_acc')
print('ARCHIVE rev_acc RANK-POST 事件 %d / 入场日 %d' % (len(sel), sel['k1'].nunique()))

# ---------- A) gate2 逐字复现 ----------
rm5, n5, nd5 = build_day_baskets(sel, env['cmap'], env['C'], env['O'], NCAL, COST)
nav5 = simulate_nav(rm5, NCAL)
mt = metrics(nav5, lvl, td, 'ARCHIVE_rev_acc_T5')
print('')
print('[A] gate2 逐字复现 T+5: 超额CAGR=%.4f IR=%.3f Sharpe=%.3f MDD=%.4f'
      % (mt['excess_cagr'], mt['ir'], mt['sharpe'], mt['mdd']))
print('    ARCHIVE 报告值  : 超额CAGR=0.0829 IR=0.384 Sharpe=0.820 MDD=-0.246')
s5 = pd.Series(nav5, index=td).dropna()
for pn, (a, b) in (('TRAIN', ('20180101', '20231231')),
                   ('VALID', ('20240101', '20251231')),
                   ('OOS', ('20260101', '20260930'))):
    x = s5[(s5.index >= a) & (s5.index <= b)]
    bb = pd.Series(lvl, index=td)[(pd.Series(lvl, index=td).index >= a)
                                  & (pd.Series(lvl, index=td).index <= b)]
    y = len(x) / 242.0
    print('    %-6s 超额=%+.4f (报告值见正文)' % (
        pn, ((x.iloc[-1] / x.iloc[0]) ** (1 / y) - 1) - ((bb.iloc[-1] / bb.iloc[0]) ** (1 / y) - 1)))

# ---------- B) 入场日收益是否被消费 ----------
k1s = sorted(rm5.keys())
a = k1s[len(k1s) // 2]
dm, exp = rm5[a]
print('')
print('[B] 抽样 sleeve k1=%d expire=%d  dm keys=%s' % (a, exp, sorted(dm.keys())))
print('    dm[entry_day]=%+.5f   （若 simulate_nav 未消费该键 → 该收益与成本均丢失）'
      % dm[a])
# 把该 sleeve 单独放进空组合，看 NAV 是否变化
nav_one = simulate_nav({a: (dm, exp)}, NCAL)
print('    单独运行: NAV 变化 = %.8f（0 表示入场日收益与成本都未被使用）'
      % (nav_one[-1] - 1.0))

# ---------- C) 正确 E1 记账（本引擎） ----------
print('')
bm, nb, nbd = build_baskets(sel, env, 5)
navE, expoE, nposE, nbE, trE = sim_fixed(bm, NCAL, ARCHIVE_SLOT, COST_SIDE)
from _fs_h_t20_lib import _perf
p = _perf(navE, env, name='E1_NET_T5', expo=expoE, trade=trE)
print('[C] 本引擎 E1 记账（含入场日开盘→收盘 + 30bp 往返）T+5: '
      '超额CAGR=%.4f IR=%.3f Sharpe=%.3f MDD=%.4f 平均敞口=%.3f 成本=%.4f'
      % (p['excess_ret'], p['ir'], p['sharpe'], p['mdd'], p['avg_expo'], p['trade_cost']))
navG, expoG, nposG, nbG, trG = sim_fixed(bm, NCAL, ARCHIVE_SLOT, 0.0)
pg = _perf(navG, env, name='E1_GROSS_T5', expo=expoG, trade=trG)
print('    E1 毛（零成本）    T+5: 超额CAGR=%.4f' % pg['excess_ret'])
print('    → 入场日收益贡献 = %.4f ; 成本拖累 = %.4f'
      % (p['excess_ret'] - mt['excess_cagr'], p['excess_ret'] - pg['excess_ret']))
print('')
print('DONE')
