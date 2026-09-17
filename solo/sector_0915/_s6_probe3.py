# -*- coding: utf-8 -*-
"""Step 6 侦察探针 3（只读）：确认输入规模 / 依赖 / 指标列可用性。"""
import os
import sqlite3
import pandas as pd

BASE = r"d:\mystock\solo\sector_0915"
cand = pd.read_csv(os.path.join(BASE, "data", "sector_stock_candidate_daily.csv"),
                   dtype={"trade_date": str, "ts_code": str})
print("pandas", pd.__version__)
print("panel", cand.shape, cand["trade_date"].min(), "->", cand["trade_date"].max())
sub = cand[cand["candidate_status"].isin(["CANDIDATE", "WATCH"])]
print("CANDIDATE+WATCH rows", len(sub), "codes", sub["ts_code"].nunique(),
      "dates", sub["trade_date"].nunique())
print("all codes", cand["ts_code"].nunique())

print("stock_basic exists", os.path.exists(r"D:\mystock\solo\sli\cache\stock_basic.parquet"))
sb = pd.read_parquet(r"D:\mystock\solo\sli\cache\stock_basic.parquet")
print("stock_basic", sb.shape, list(sb.columns)[:10])

print("hvt_bull/config.yaml exists", os.path.exists(r"d:\mystock\solo\hvt_bull\config.yaml"))
try:
    from sli.utils import setup_logging
    print("setup_logging OK")
except Exception as e:
    print("setup_logging FAIL", e)

con = sqlite3.connect(r"D:\mystock\cache_daily\stock_data.db")
q = pd.read_sql_query(
    "SELECT COUNT(*) n, MIN(trade_date) a, MAX(trade_date) b FROM daily_basic_cache "
    "WHERE trade_date >= '20240801'", con)
print("daily_basic from 20240801:", q.to_dict("records"))
q2 = pd.read_sql_query(
    "SELECT COUNT(DISTINCT trade_date) n FROM daily_cache WHERE trade_date >= '20240801'", con)
print("trade days from 20240801:", q2.to_dict("records"))
q3 = pd.read_sql_query(
    "SELECT trade_date FROM daily_cache WHERE ts_code='000001.SZ' AND trade_date>='20240801' "
    "ORDER BY trade_date", con)
print("SZ calendar n =", len(q3), q3["trade_date"].iloc[0], q3["trade_date"].iloc[-1])
con.close()

print("STK_FACTOR cols sample:")
con = sqlite3.connect(r"D:\mystock\cache_daily\stock_data.db")
c = pd.read_sql_query("SELECT * FROM stk_factor_pro LIMIT 1", con)
print([x for x in c.columns if "ma_" in x or "atr" in x][:20])
con.close()

for f in ("output/sector_stock_candidate_today.json", "output/sector_stock_candidate_pool.json",
          "data/sector_membership.csv", "sector_master.json", "config/sector_mapping.json"):
    p = os.path.join(BASE, f.replace("/", os.sep))
    print(f, os.path.exists(p), os.path.getsize(p) if os.path.exists(p) else -1)
