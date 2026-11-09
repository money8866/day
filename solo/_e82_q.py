# -*- coding: utf-8 -*-
import os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
d = pd.read_csv(os.path.join(HERE, "e82_pullback.csv"), dtype={"ts_code": str, "date_t0": str})
d = d[~d["date_t0"].str.startswith("202409")]

filters = [
    ("Q0 全部", d["pb"].notna()),
    ("F1 T+1涨>=8%", d["t1g"] >= 8),
    ("F2 F1+低位<20%", (d["t1g"] >= 8) & (d["rise"] < 20)),
    ("F3 缺口>=2且T1涨>=8", (d["gap"] >= 2) & (d["t1g"] >= 8)),
    ("F4 低位<20%", d["rise"] < 20),
    ("F5 平台突破", d["pb"].fillna(False)),
    ("F6 缺口>=2%", d["gap"] >= 2.0),
]
hdr = "%-20s %4s %8s %8s %8s %6s %7s %6s"
print("剔除 202409 后（样本 %d）" % d["ts_code"].nunique())
for ma, stop in ((5, "B"), (10, "B")):
    base = d[(d["ma"] == ma) & (d["stop"] == stop)]
    print("\n== MA%d 止损%s ==" % (ma, stop))
    print(hdr % ("筛选", "n", "收益均值", "中位", "超额", "胜率", "最大盈", "持有日"))
    for name, m in filters:
        s = base[m.reindex(base.index).fillna(False)]
        if s.empty:
            print("%-20s 无样本" % name)
            continue
        print(hdr % (name, len(s), "%+7.2f%%" % s["ret"].mean(), "%+7.2f%%" % s["ret"].median(),
                     "%+7.2f%%" % s["ex"].mean(),
                     "%5.0f%%" % ((s["ret"] > 0).mean() * 100),
                     "%+6.1f%%" % s["max_gain"].mean(), "%6.1f" % s["days"].mean()))
        t = s["ex"].mean() / (s["ex"].std() / (len(s) ** 0.5)) if len(s) > 2 else float("nan")
        print("    ↑ 超额 t 值 %.2f（std %.1f%%）" % (t, s["ex"].std()))
