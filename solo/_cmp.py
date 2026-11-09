# -*- coding: utf-8 -*-
"""核对两次回踩模拟的差异（临时脚本）"""
import os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
o = pd.read_csv(os.path.join(HERE, "e82_pullback.csv"), dtype={"ts_code": str, "date_t0": str})
n = pd.read_csv(os.path.join(HERE, "e08_low_start_trades.csv"), dtype={"ts_code": str, "t0_date": str})

o = o[(o["ma"] == 5) & (o["stop"] == "B") & (o["rise"] < 20) & (~o["date_t0"].str.startswith("202409"))]
n = n[(n["ma"] == 5) & (n["rise_from_low"] < 20) & (~n["t0_date"].str.startswith("202409"))]
print("旧 n=%d  ret %.2f  bench %.2f  ex %.2f" % (len(o), o["ret"].mean(), o["bench"].mean(), o["ex"].mean()))
print("新 n=%d  ret %.2f  bench %.2f  ex %.2f" % (len(n), n["ret"].mean(), n["bench"].mean(), n["ex"].mean()))

o2 = o.rename(columns={"date_t0": "t0_date", "d_in": "din_o"})
m = n.merge(o2[["ts_code", "t0_date", "din_o", "ret", "bench"]], on=["ts_code", "t0_date"], how="inner")
print("重叠 %d 笔" % len(m))
if len(m):
    print("  入场日一致 %d / %d" % ((m["d_in"] == m["din_o"]).sum(), len(m)))
    print("  收益相关 %.3f，均值差 %.2f" % (m["ret_x"].corr(m["ret_y"]), (m["ret_x"] - m["ret_y"]).mean()))
    bad = m[(m["ret_x"] - m["ret_y"]).abs() > 1]
    print("  差异>1pct 的笔数 %d" % len(bad))
    if len(bad):
        print(bad[["ts_code", "t0_date", "d_in", "din_o", "ret_x", "ret_y", "days_x", "days_y"]].head(10).to_string())
