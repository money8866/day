# -*- coding: utf-8 -*-
"""诊断 HVT 状态转换：区分「同一事件内倒退」与「跨事件（新周期开始）」。"""
import collections
import os
import sys

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import stock_structure_hvt_build as S

d = pd.read_csv(os.path.join(BASE_DIR, "data", "stock_hvt_history.csv"), dtype=str,
                usecols=["trade_date", "ts_code", "hvt_state", "hvt_event_date"])
d = d.sort_values(["ts_code", "trade_date"])
same_cnt = collections.Counter()
cross_cnt = collections.Counter()
total = 0
for code, g in d.groupby("ts_code", sort=False):
    prev = None
    for s, e in zip(g["hvt_state"].tolist(), g["hvt_event_date"].tolist()):
        if prev is not None:
            total += 1
            a, ea = prev
            if e != ea:
                cross_cnt[(a, s)] += 1
            elif not S._hvt_transition_ok(a, s, ea, e):
                same_cnt[(a, s)] += 1
        prev = (s, e)
print("总转换 %d；跨事件 %d；同事件非法 %d"
      % (total, sum(cross_cnt.values()), sum(same_cnt.values())))
print("\n跨事件转换模式：")
for (a, b), v in cross_cnt.most_common():
    print(f"  {v:5d}  {a} -> {b}")
print("\n同事件非法转换模式：")
for (a, b), v in same_cnt.most_common():
    print(f"  {v:5d}  {a} -> {b}")
