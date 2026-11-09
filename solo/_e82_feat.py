# -*- coding: utf-8 -*-
"""从普查样本里找「结构守住」的事前特征（临时脚本）"""
import os
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
df = pd.read_csv(os.path.join(HERE, "e82_census.csv"), dtype={"ts_code": str, "date_t0": str})
df["held"] = (~df["brk_t0"]).astype(int)     # 40 日内收盘从未跌破 T0 最高

print("样本 %d，结构守住 %d（%.1f%%）" % (len(df), df["held"].sum(), df["held"].mean() * 100))


def bucket(col, edges, labels=None, name=None):
    s = df[col]
    labels = labels or ["%g~%g" % (edges[i], edges[i + 1]) for i in range(len(edges) - 1)]
    b = pd.cut(s, bins=edges, labels=labels, right=False)
    g = df.groupby(b, observed=False).agg(n=("held", "size"), held=("held", "sum"),
                                          rate=("held", "mean"),
                                          r40=("r40", "mean"), mx=("max_gain", "mean"))
    g["rate"] = (g["rate"] * 100).round(1)
    print("\n== %s ==" % (name or col))
    print(g.round(1).to_string())


bucket("gap", [0, 1.5, 2, 3, 4, 100], ["1~1.5", "1.5~2", "2~3", "3~4", ">=4"], "缺口幅度 %")
bucket("t0_vol_mult", [0, 1.5, 2, 3, 4, 6, 999],
       ["1.5~2", "2~3", "3~4", "4~6", "6~10", ">=10"], "T0 量能倍数")
bucket("rise_from_low", [0, 10, 20, 30, 40, 50], None, "距60日低点涨幅 %")
bucket("t1_gain", [-100, 0, 3, 5, 8, 11, 100], ["<0", "0~3", "3~5", "5~8", "8~11", ">=11"], "T+1 涨幅 %")
bucket("t1_vol_mult", [0, 1, 1.5, 2, 3, 99], None, "T+1 量 / T0 量")
bucket("mv_yi", [80, 150, 300, 500, 1000, 99999], ["80~150", "150~300", "300~500", "500~1000", ">=1000"], "市值 亿")

print("\n== 组合筛选（守住率 / 40日收益） ==")
combos = [
    ("T1涨>=8 且 T1量>=1.5x", (df["t1_gain"] >= 8) & (df["t1_vol_mult"] >= 1.5)),
    ("T1量>=1.5x 且 低位<20", (df["t1_vol_mult"] >= 1.5) & (df["rise_from_low"] < 20)),
    ("T1涨>=8 且 低位<20", (df["t1_gain"] >= 8) & (df["rise_from_low"] < 20)),
    ("T1涨>=8 T1量>=1.5x 低位<20",
     (df["t1_gain"] >= 8) & (df["t1_vol_mult"] >= 1.5) & (df["rise_from_low"] < 20)),
    ("T1涨>=8 T1量>=1.5x 缺口>=2",
     (df["t1_gain"] >= 8) & (df["t1_vol_mult"] >= 1.5) & (df["gap"] >= 2)),
    ("T1涨>=8 且 T0量>=3", (df["t1_gain"] >= 8) & (df["t0_vol_mult"] >= 3)),
    ("极米型(量>=3,T1涨>=8,低位<40)", (df["t0_vol_mult"] >= 3) & (df["t1_gain"] >= 8) & (df["rise_from_low"] < 40)),
]
for name, m in combos:
    s = df[m]
    if s.empty:
        print("  %-34s 无样本" % name)
        continue
    print("  %-34s n=%3d 守住 %3d (%4.1f%%)  r40 %+6.1f%%  最大盈 %+5.1f%%"
          % (name, len(s), s["held"].sum(), s["held"].mean() * 100,
             s["r40"].mean(), s["max_gain"].mean()))
