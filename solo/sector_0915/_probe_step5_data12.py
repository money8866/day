# -*- coding: utf-8 -*-
"""probe 12: sector_master 字段 + membership origin/mapping 分布 + fina 覆盖"""
import json
import sqlite3
import pandas as pd

BASE = r"d:\mystock\solo\sector_0915"

m = json.load(open(BASE + r"\sector_master.json", encoding="utf-8"))
s0 = m["sectors"][0]
print("sector keys:", sorted(s0.keys()))
for k, v in s0.items():
    if isinstance(v, (list, dict)) and k != "sector_id":
        print(" ", k, "=", str(v)[:150])
    elif not isinstance(v, (list, dict)):
        print(" ", k, "=", v)
print("rotation_groups[0]:", m["rotation_groups"][0])

mb = pd.read_csv(BASE + r"\data\sector_membership.csv", dtype=str)
print("\nmembership_origin:\n", mb["membership_origin"].value_counts().to_string())
print("mapping_method:\n", mb["mapping_method"].value_counts().to_string())
print("board_type:\n", mb["board_type"].value_counts().to_string())
sub = mb[mb["membership_type"] == "THEMATIC"].head(3)
print("\nTHEMATIC sample:\n", sub[["ts_code", "sector_id", "mapping_method", "source_board", "reason"]].to_string())
sub2 = mb[mb["membership_type"] == "CORE"].head(3)
print("\nCORE sample:\n", sub2[["ts_code", "sector_id", "membership_origin", "mapping_method", "reason"]].to_string())

con = sqlite3.connect(r"D:\mystock\cache_daily\stock_data.db")
n = con.execute("select count(distinct ts_code) from fina_indicator_cache").fetchone()[0]
print("\nfina stocks:", n)
print("fina latest rows:", con.execute(
    "select ts_code, ann_date, end_date, roe, grossprofit_margin, or_yoy, netprofit_yoy, ocf_to_or, debt_to_assets "
    "from fina_indicator_cache where ts_code='000001.SZ' order by end_date desc limit 3").fetchall())
cols = [r[1] for r in con.execute("pragma table_info(fina_indicator_cache)").fetchall()]
print("fina cols:", cols)
con.close()

sb = pd.read_parquet(r"D:\mystock\solo\sli\cache\stock_basic.parquet")
print("\nstock_basic industry sample:", sb["industry"].dropna().unique()[:12].tolist())
print("list_status:", sb["list_status"].value_counts().to_dict())
