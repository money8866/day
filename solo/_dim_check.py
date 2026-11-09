# -*- coding: utf-8 -*-
"""
对 _dim_events.csv 做三项稳健性检验：
1) 去重叠：每个 (ts_code, 自然年) 只保留第一条信号（同一股票连续季度信号高度重叠，t 值虚高）
2) 月度分布：确认「各格中位超额全负」是否仅由等权基准右偏造成
3) 极值档对比：户数大减 vs 大增 的差值，在去重叠样本上的 t
"""
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ev = pd.read_csv(os.path.join(HERE, "_dim_events.csv"),
                 dtype={"ts_code": str, "end_date": str, "ann_date": str, "signal_date": str})
ev["year"] = ev["signal_date"].str[:4]
ev["month"] = ev["signal_date"].str[:6]

CHG_BINS = [-1e9, -15, -5, 5, 15, 1e9]
CHG_LAB = ["<-15%", "-15~-5", "-5~+5", "+5~+15", ">+15%"]
ev["c"] = pd.cut(ev["chg"], CHG_BINS, labels=CHG_LAB)

print("全样本事件 %d 条" % len(ev))

# ── 1. 去重叠：每 (ts_code, year) 只留第一条 ──
u = ev.sort_values(["ts_code", "signal_date"], kind="mergesort").drop_duplicates(
    subset=["ts_code", "year"], keep="first")
print("去重叠后 %d 条（股票 %d 只，年度 %s）"
      % (len(u), u["ts_code"].nunique(), sorted(u["year"].unique())))

for hz, tag in ((20, "20日"), (40, "40日")):
    col, bm = "ret%d" % hz, "bm%d" % hz
    d = u.dropna(subset=[col, bm]).copy()
    d["ex"] = d[col] - d[bm]
    print("\n== 去重叠 · %s · 户数环比 → 超额 ==" % tag)
    rows = []
    for k, s in d.groupby("c", observed=False):
        if len(s) == 0:
            continue
        rows.append({
            "格子": k, "n": len(s),
            "超额均": s["ex"].mean(), "超额中位": s["ex"].median(),
            "收益均": s[col].mean(), "收益中位": s[col].median(),
            "胜率": (s[col] > 0).mean() * 100.0,
            "超基准胜率": (s["ex"] > 0).mean() * 100.0,
            "t值": s["ex"].mean() / (s["ex"].std() / np.sqrt(len(s))),
        })
    print(pd.DataFrame(rows).set_index("格子").round(2).to_string())

# ── 2. 月度分布：中位超额为何全负 ──
print("\n== 全部事件的信号月份分布（Top10）与中位超额 ==")
mm = ev.dropna(subset=["ex20"]).groupby("month").agg(
    n=("ex20", "size"), bm20=("bm20", "median"), ex中位=("ex20", "median"))
mm["占比%"] = mm["n"] / mm["n"].sum() * 100.0
print(mm.sort_values("n", ascending=False).head(10).round(2).to_string())

print("\n== 说明：基准 bm 用横截面「均值」，个股收益右偏，故中位数天然低于均值 ==")
print("全样本：ret20 均值 %+.2f%% / 中位 %+.2f%%；bm20 均值 %+.2f%%"
      % (ev["ret20"].mean(), ev["ret20"].median(), ev["bm20"].mean()))

# ── 3. 极值档对比（去重叠） ──
d = u.dropna(subset=["ex20"]).copy()
d["ex"] = d["ex20"]
lo = d[d["c"].isin(["<-15%", "-15~-5"])]
hi = d[d["c"] == ">+15%"]
print("\n== 极值档对比（去重叠 · 20日） ==")
print("  户数减少组 n=%d 超额均 %+.2f%% 中位 %+.2f%%"
      % (len(lo), lo["ex"].mean(), lo["ex"].median()))
print("  户数大增组 n=%d 超额均 %+.2f%% 中位 %+.2f%%"
      % (len(hi), hi["ex"].mean(), hi["ex"].median()))
diff = lo["ex"].mean() - hi["ex"].mean()
se = np.sqrt(lo["ex"].var() / len(lo) + hi["ex"].var() / len(hi))
print("  差值 %+.2f%%，t=%5.2f（去重叠，同年度内独立）" % (diff, diff / se))

# ── 4. 分年（去重叠） ──
print("\n== 去重叠 · 分年 · 超额均值(%) ==")
print(u.dropna(subset=["ex20"]).pivot_table(
    index="c", columns="year", values="ex20", aggfunc="mean", observed=False).round(2).to_string())
print("\n== 去重叠 · 分年 · 样本数 ==")
print(u.dropna(subset=["ex20"]).pivot_table(
    index="c", columns="year", values="ex20", aggfunc="size", observed=False)
      .fillna(0).astype(int).to_string())

# ── 5. 单调性打分（去重叠，20日）：五档均值的秩相关 ──
g = u.dropna(subset=["ex20"]).groupby("c", observed=False)["ex20"].mean()
print("\n== 五档超额均值（去重叠） ==")
print(g.round(2).to_string())
rank = np.argsort(np.argsort(g.to_numpy()))
print("与户数档位的 Spearman 秩相关: %.3f （-1 = 完美单调递减）"
      % np.corrcoef(np.arange(len(g)), g.to_numpy())[0, 1])
