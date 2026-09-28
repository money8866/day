# -*- coding: utf-8 -*-
"""_fs_run_pg: 只补跑 §32 参数网格（复用 fs_run_robust 的 load_panel/run_param_grid）"""
import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fs_common import OUTD
from fs_run_robust import load_panel, run_param_grid

df = load_panel()
pg = run_param_grid(df)
pd.DataFrame(pg).to_csv(os.path.join(OUTD, 'parameter_grid.csv'),
                        index=False, encoding='utf-8-sig')
print('parameter_grid 行数 = %d' % len(pg))
print('DONE')
