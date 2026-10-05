# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
pd.set_option('display.width', 250)
from position_gate import backtest as B

feat = pd.read_pickle(os.path.join('position_gate', 'cache', 'gate_smoke.pkl')) \
    if os.path.exists(os.path.join('position_gate', 'cache', 'gate_smoke.pkl')) else None
if feat is None:
    from position_gate.data import GateData
    from position_gate import features as F, regime as R
    f, _ = F.build_all(data=GateData(), start='20260601', end='20260930')
    feat = R.run_gate(f)
    feat.to_pickle(os.path.join('position_gate', 'cache', 'gate_smoke.pkl'))

print('ret1 has nan?', feat['ret1'].isna().sum())
for cb in (0, 50):
    b = B.const_returns(feat, 100.0, cost_bp=cb)
    print('cb=', cb, 'cost.sum()=', b['cost'].sum(), 'ret_net.sum()=', b['ret_net'].sum())
    print('  first3 turnover', b['turnover'].head(3).tolist())
print(B.metrics(B.const_returns(feat, 100.0, cost_bp=0))['total_return_%'],
      B.metrics(B.const_returns(feat, 100.0, cost_bp=50))['total_return_%'])
