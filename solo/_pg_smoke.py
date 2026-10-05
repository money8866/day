# -*- coding: utf-8 -*-
"""冒烟测试：小时间窗跑通 features → regime → backtest 全链路"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
pd.set_option('display.width', 300)

from position_gate.data import GateData
from position_gate import features as F
from position_gate import regime as R
from position_gate import backtest as B

S, E = '20260601', '20260930'
d = GateData()
feat, parts = F.build_all(data=d, start=S, end=E)
print('feat shape', feat.shape)
print('cols:', list(feat.columns))
gate = R.run_gate(feat)
print(gate[['close', 'ma20', 'breadth_ma20', 'riskoff_count', 'regime',
            'position_min', 'position_target', 'position_max', 'reason']].tail(8).to_string())
print(B.compare(gate).to_string(index=False))
print('splits:'); print(B.split_compare(gate).to_string(index=False))
