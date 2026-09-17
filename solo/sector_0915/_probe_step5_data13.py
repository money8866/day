# -*- coding: utf-8 -*-
"""probe 13: DB 4 表精确 schema + stock_basic 列 + opportunity/today item 结构终检"""
import json
import sqlite3
import pandas as pd

con = sqlite3.connect(r"D:\mystock\cache_daily\stock_data.db")
for t in ("daily_cache", "daily_basic_cache", "adj_factor_cache", "fina_indicator_cache"):
    cols = [r[1] for r in con.execute(f"pragma table_info({t})").fetchall()]
    print(t, "cols:", cols)
con.close()

sb = pd.read_parquet(r"D:\mystock\solo\sli\cache\stock_basic.parquet")
print("\nstock_basic cols:", sb.columns.tolist())

BASE = r"d:\mystock\solo\sector_0915"
opp = json.load(open(BASE + r"\output\sector_opportunity_pool.json", encoding="utf-8"))
print("\nopportunity top keys:", sorted(opp.keys()))
opp_items = opp.get("opportunities") or opp.get("items") or []
print("opportunity item count:", len(opp_items))
if opp_items:
    print("opportunity item[0]:", json.dumps(opp_items[0], ensure_ascii=False, indent=1)[:900])

today = json.load(open(BASE + r"\output\sector_seos_today.json", encoding="utf-8"))
t_items = today.get("sectors") or today.get("items") or []
print("\ntoday item count:", len(t_items))
if t_items:
    print("today item[0] keys:", sorted(t_items[0].keys()))
    print("today item[0] sample:", json.dumps(t_items[0], ensure_ascii=False)[:700])
