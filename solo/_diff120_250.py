# -*- coding: utf-8 -*-
"""对比 120 vs 250 窗口的事件csv：事件集是否一致、哪些列有差异"""
import pandas as pd

a = pd.read_csv(r"d:/mystock/solo/report_daily/double_sli_pool_events_win120.csv")
b = pd.read_csv(r"d:/mystock/solo/report_daily/double_sli_pool_events.csv")
print("rows:", len(a), len(b))
ka, kb = set(zip(a.code, a.date)), set(zip(b.code, b.date))
print("事件集 相同:", ka == kb, "| 仅old:", len(ka - kb), "| 仅new:", len(kb - ka))
cols = ["p_vol", "p_turn"]
for c in cols:
    diff = (a[c] != b[c]).sum()
    print(f"{c} 不同行数: {diff}")
    if diff:
        m = a[c] != b[c]
        print("  old 分布:", a.loc[m, c].describe()[["min", "max", "mean"]].round(1).to_dict())
        print("  new 分布:", b.loc[m, c].describe()[["min", "max", "mean"]].round(1).to_dict())
d250 = "W>=250: old=%d new=%d" % ((a.W >= 250).sum(), (b.W >= 250).sum())
print(d250)
