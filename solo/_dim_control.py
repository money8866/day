# -*- coding: utf-8 -*-
"""
控制变量检验：股东户数环比在控制「流通市值 / 换手率」后是否仍有独立信息。

做法：在**每个信号日内部**按 circ_mv（或 turnover_rate_f）做五分位，
再看 (市值层 × 户数档) 的超额；若户数档柱状差异在每层内都还在，才是独立信息。
"""
import os
import sqlite3

import numpy as np
import pandas as pd

CACHE = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB = os.path.join(CACHE, "stock_data.db")
HERE = os.path.dirname(os.path.abspath(__file__))

ev = pd.read_csv(os.path.join(HERE, "_dim_events.csv"),
                 dtype={"ts_code": str, "end_date": str, "ann_date": str, "signal_date": str})
ev["year"] = ev["signal_date"].str[:4]
CHG_BINS = [-1e9, -15, -5, 5, 15, 1e9]
CHG_LAB = ["<-15%", "-15~-5", "-5~+5", "+5~+15", ">+15%"]
ev["c"] = pd.cut(ev["chg"], CHG_BINS, labels=CHG_LAB)
# 去重叠：每 (ts_code, year) 第一条
ev = ev.sort_values(["ts_code", "signal_date"], kind="mergesort").drop_duplicates(
    subset=["ts_code", "year"], keep="first")

con = sqlite3.connect(DB, timeout=120)
db = pd.read_sql_query(
    "SELECT ts_code, trade_date, circ_mv, turnover_rate_f, total_mv "
    "FROM daily_basic_cache WHERE trade_date >= '20221201'", con)
con.close()
db["trade_date"] = db["trade_date"].astype(str)
for c in ("circ_mv", "turnover_rate_f", "total_mv"):
    db[c] = pd.to_numeric(db[c], errors="coerce")

ev = ev.merge(db.rename(columns={"trade_date": "signal_date"}),
              on=["ts_code", "signal_date"], how="left")
print("去重叠事件 %d 条，市值匹配 %d 条" % (len(ev), ev["circ_mv"].notna().sum()))

d = ev.dropna(subset=["ex20", "circ_mv"]).copy()
d = d[d["circ_mv"] > 0]

# 每个信号日内部五分位（用秩分桶，避免 qcut 边界重复报错）
def _q5(s):
    r = s.rank(method="first")
    return ((r - 1) * 5 // max(len(s), 1)).astype("Int64")


d["mvq"] = d.groupby("signal_date")["circ_mv"].transform(_q5)
d["toq"] = d.groupby("signal_date")["turnover_rate_f"].transform(_q5)
d["mvq"] = d["mvq"].map({0: "Q1小", 1: "Q2", 2: "Q3", 3: "Q4", 4: "Q5大"})
d["toq"] = d["toq"].map({0: "T1低", 1: "T2", 2: "T3", 3: "T4", 4: "T5高"})

for dim, name in (("mvq", "流通市值五分位"), ("toq", "换手率五分位")):
    print("\n" + "=" * 92)
    print("== %s × 户数档 → 未来20日超额均值(%%), 括号内 n ==" % name)
    pv = d.pivot_table(index=dim, columns="c", values="ex20", aggfunc="mean", observed=False)
    pn = d.pivot_table(index=dim, columns="c", values="ex20", aggfunc="size", observed=False)
    show = pv.round(2).astype(str) + " (" + pn.fillna(0).astype(int).astype(str) + ")"
    print(show.to_string())
    print("\n列内去均值（消除层间水平差，只看户数档的相对高低）:")
    print((pv.sub(pv.mean(axis=1), axis=0)).round(2).to_string())

print("\n" + "=" * 92)
print("== 各市值层内：户数大减(<-15%%) - 户数大增(>+15%%) 的超额差 ==")
for q, s in d.groupby("mvq", observed=False):
    lo = s[s["c"] == "<-15%"]["ex20"]
    hi = s[s["c"] == ">+15%"]["ex20"]
    if len(lo) < 30 or len(hi) < 30:
        print("  %-5s 样本不足 (n=%d/%d)" % (q, len(lo), len(hi)))
        continue
    diff = lo.mean() - hi.mean()
    se = np.sqrt(lo.var() / len(lo) + hi.var() / len(hi))
    print("  %-5s n=%4d/%4d  差 %+5.2f%%  t=%5.2f" % (q, len(lo), len(hi), diff, diff / se))

print("\n== 全样本市值中位数(亿元) ==")
print("  户数<-15%%: %.1f   户数>+15%%: %.1f"
      % (d[d["c"] == "<-15%"]["circ_mv"].median() / 1e4,
         d[d["c"] == ">+15%"]["circ_mv"].median() / 1e4))
