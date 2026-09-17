# -*- coding: utf-8 -*-
"""probe 11: Step 5 构建脚本所需的全部输入结构终检"""
import json
import pandas as pd

BASE = r"d:\mystock\solo\sector_0915"

# 1) config v1.1 合法性
cfg = json.load(open(BASE + r"\config\sector_stock_candidate_config.json", encoding="utf-8"))
print("config version:", cfg["version"], "| keys:", len(cfg))

# 2) seos_daily 全列
sd = pd.read_csv(BASE + r"\data\sector_seos_daily.csv", dtype={"trade_date": str, "sector_id": str}, nrows=3)
print("\nseos_daily cols(%d):" % len(sd.columns))
print(sorted(sd.columns.tolist()))

# 3) state_history 列 + 最近一行样例
sh = pd.read_csv(BASE + r"\data\sector_state_history.csv", dtype={"trade_date": str, "sector_id": str})
print("\nstate_history cols(%d):" % len(sh.columns), sorted(sh.columns.tolist()))
last = sh[sh["trade_date"] == sh["trade_date"].max()]
print("state_history last date:", sh["trade_date"].max(), "rows:", len(last))
print(last[["sector_id", "current_state", "state_duration"]].head(3).to_string() if "current_state" in sh.columns else sh.head(2).to_string())

# 4) today / opportunity JSON 结构
tj = json.load(open(BASE + r"\output\sector_seos_today.json", encoding="utf-8"))
print("\nseos_today top keys:", list(tj.keys())[:8])
t0 = (tj.get("themes") or tj.get("sectors") or [None])[0]
if t0: print("seos_today item keys:", sorted(t0.keys()))

op = json.load(open(BASE + r"\output\sector_opportunity_pool.json", encoding="utf-8"))
print("\nopportunity top keys:", list(op.keys())[:8])
o0 = (op.get("themes") or op.get("sectors") or op.get("opportunities") or [None])[0]
if o0: print("opportunity item keys:", sorted(o0.keys()))

# 5) membership 分布
mb = pd.read_csv(BASE + r"\data\sector_membership.csv", dtype={"ts_code": str, "sector_id": str, "effective_date": str})
print("\nmembership rows:", len(mb), "stocks:", mb["ts_code"].nunique(), "themes:", mb["sector_id"].nunique())
print("membership_type:\n", mb["membership_type"].value_counts().to_string())
print("is_static:", mb["is_static"].value_counts(dropna=False).to_dict())
print("effective_date range:", mb["effective_date"].min(), "->", mb["effective_date"].max())
print("membership_confidence:", mb["membership_confidence"].describe().round(3).to_dict())
print("stocks in >1 theme:", int((mb.groupby("ts_code")["sector_id"].nunique() > 1).sum()))

# 6) DB 覆盖
import sqlite3
con = sqlite3.connect(r"D:\mystock\cache_daily\stock_data.db")
for t in ["daily_cache", "adj_factor_cache", "daily_basic_cache", "fina_indicator_cache"]:
    n, dmin, dmax = con.execute(f"select count(*), min(trade_date), max(trade_date) from {t}").fetchone()
    print(f"{t}: {n} rows, {dmin} -> {dmax}")
fn = con.execute("select count(distinct ts_code) from fina_indicator_cache").fetchone()[0]
print("fina stocks:", fn, "| ann_date sample:",
      con.execute("select ann_date, end_date from fina_indicator_cache order by ann_date desc limit 2").fetchall())
con.close()
