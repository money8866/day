# -*- coding: utf-8 -*-
"""probe 10: 复核 seos_daily breadth/delta 量纲一致性与全样本分布"""
import pandas as pd
import numpy as np

SEOS = r"d:\mystock\solo\sector_0915\data\sector_seos_daily.csv"

df = pd.read_csv(SEOS, dtype={"trade_date": str, "sector_id": str})
print("rows:", len(df), "themes:", df["sector_id"].nunique(),
      "dates:", df["trade_date"].min(), "->", df["trade_date"].max())
print("cols sample:", [c for c in df.columns if "breadth" in c])

cols = ["breadth", "core_breadth", "primary_breadth", "secondary_breadth",
        "breadth_delta_5", "core_breadth_delta_5", "primary_breadth_delta_3",
        "top5_concentration", "volume_ratio_5", "theme_amount_share",
        "ew_ret_5", "theme_health", "seos_score", "current_state"]
exist = [c for c in cols if c in df.columns]
print(df[exist].describe(percentiles=[.05, .25, .5, .75, .95]).T.round(4).to_string())

# 一致性验证：按公式重算 breadth_delta_5，与存量列比对
df = df.sort_values(["sector_id", "trade_date"], kind="mergesort")
recomputed = df["breadth"] - df.groupby("sector_id", sort=False)["breadth"].shift(5)
gap = (pd.to_numeric(df["breadth_delta_5"], errors="coerce") - recomputed).abs().max()
print("\n|stored_delta_5 - recomputed| max =", gap)

# breadth 原始定义域检查
b = pd.to_numeric(df["breadth"], errors="coerce")
print("breadth: min=%.4f max=%.4f  any_nan=%d" % (b.min(), b.max(), b.isna().sum()))
print("breadth 分布分位:", np.round(b.quantile([0, .1, .5, .9, 1]).values, 4))

# state 分布
print("\ncurrent_state 分布:")
print(df["current_state"].value_counts(dropna=False).to_string() if "current_state" in df.columns else "N/A")
