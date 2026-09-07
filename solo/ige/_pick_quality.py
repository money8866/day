# -*- coding: utf-8 -*-
"""IGE v1.1 一次性筛选：§23 最高质量「行业→个股」组合。
条件：ige_mix 高 + Lifecycle=ACCELERATION + 行业利润加速 PA>0 + 行业内 SIA 高。
输出：ige/output/ige_quality_industry_<date>.csv / ige_quality_picks_<date>.csv
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, r"d:\mystock\solo")
from ige import data as D

DATE = "20260901"
OUT = r"d:\mystock\solo\ige\output"

full = pd.read_csv(fr"{OUT}\ige_full_{DATE}.csv")
ind = pd.read_csv(fr"{OUT}\ige_industry_{DATE}.csv")

# ── 个股利润加速 PA_i = 净利同比Q0 - Q-1（财务缓存，当时已披露口径）──
fin = D.load_finance(DATE)
pa = fin[["ts_code", "np_yoy_0", "np_yoy_1"]].copy()
pa["pa_i"] = pd.to_numeric(pa["np_yoy_0"], errors="coerce") - pd.to_numeric(
    pa["np_yoy_1"], errors="coerce"
)
pa = pa[["ts_code", "pa_i"]]
full = full.merge(pa, left_on="code", right_on="ts_code", how="left")

# 行业中位利润加速与 PA 覆盖率
g = full.groupby("l3_code")
ind_pa = pd.DataFrame({
    "ind_pa_med": g["pa_i"].median(),
    "ind_pa_n": g["pa_i"].count(),
}).reset_index()
ind = ind.merge(ind_pa, on="l3_code", how="left")

# ── 行业侧：ACCELERATION 且 ige_mix 高，且行业利润加速 >0（缺失则不满足）──
s1 = ind[ind["industry_lifecycle"] == "ACCELERATION"].copy()
s1 = s1[s1["ige_mix"] >= 60.0]
s1 = s1[s1["ind_pa_med"].notna() & (s1["ind_pa_med"] > 0.0)]
s1 = s1.sort_values(["ige_mix", "acceleration_score"],
                    ascending=False).reset_index(drop=True)
s1 = s1[s1["ige_mix"].notna()]
s1["quality"] = "TOP"
sel_l3 = set(s1["l3_code"])

# ── 行业内个股：SIA 高 + IndustryElasticity 高 ──
pick = full[full["l3_code"].isin(sel_l3)].copy()
if len(pick):
    pick = pick[pick["code"].str.endswith((".SH", ".SZ"))]  # 不含北交所
    pick["sia"] = pd.to_numeric(pick["sia"], errors="coerce")
    pick["industry_elasticity"] = pd.to_numeric(
        pick["industry_elasticity"], errors="coerce")
    pick["acceleration_confirm"] = pd.to_numeric(
        pick["acceleration_confirm"], errors="coerce")
    # 行业内 SIA 百分位（>0 观测）
    pick["sia_pct"] = pick.groupby("l3_code")["sia"].rank(pct=True)
    p2 = pick[(pick["sia"].notna()) & (pick["sia_pct"] >= 0.70)].copy()
    p2 = p2[p2["industry_elasticity"] >= 60.0]
    p2 = p2.sort_values(["ige_mix", "sia_pct", "industry_elasticity"],
                        ascending=[False, False, False])

# ── 保存 ──
k_cols = ["sw_l1", "sw_l2", "sw_l3", "industry_sample_n", "industry_confidence",
          "ige_mix", "ige_adj", "acceleration_score", "profit_elasticity_score",
          "ind_pa_med", "ind_pa_n", "acceleration_confirm",
          "industry_elasticity_state", "industry_elasticity_type"]
s1[["sw_l1", "sw_l2", "sw_l3", "l3_code"] + [c for c in k_cols if c in s1.columns]] \
    .to_csv(fr"{OUT}\ige_quality_industry_{DATE}.csv", index=False)

if len(pick):
    p_cols = ["code", "name", "sw_l3", "ige_mix", "ige_adj",
              "ind_pa_med", "sia_profit", "sia_price", "sia", "sia_pct",
              "industry_elasticity", "industry_elasticity_state",
              "industry_confidence", "rocket_eligible"]
    p2[[c for c in p_cols if c in p2.columns]] \
        .to_csv(fr"{OUT}\ige_quality_picks_{DATE}.csv", index=False)
    # 剔除 ST / 风险警示后，每个高质量行业内 SIA 头部 3 只作为组合代表
    p2 = p2[~p2["name"].astype(str).str.contains("ST", na=False)]
    comb = (p2.sort_values(["ige_mix", "sia_pct"],
                           ascending=[False, False])
              .groupby("l3_code", sort=False).head(3))
    comb.to_csv(fr"{OUT}\ige_quality_combo_{DATE}.csv", index=False)

# ── 打印 ──
print(f"IGE v1.1 §23 高质量组合筛选　行业 {len(s1)} 个 / 候选股 {len(p2) if len(pick) else 0} 只")
print("─" * 30)
cols = ["sw_l1", "sw_l3", "ige_mix", "ige_adj", "industry_sample_n",
        "industry_confidence", "ind_pa_med", "acceleration_confirm"]
print(s1[[c for c in cols if c in s1.columns]].head(20).to_string(index=False))
print("─" * 30)
if len(pick):
    top_stocks = p2.head(15)[["code", "name", "sw_l3", "sia", "industry_elasticity"]]
    print("候选股 Top（按行业igemix + 行业内 SIA 分位）")
    print(top_stocks.to_string(index=False))
    print("─" * 30)
    print("个股按行业计数：")
    print(p2["sw_l3"].value_counts().to_string())
