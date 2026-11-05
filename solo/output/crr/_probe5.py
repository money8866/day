# -*- coding: utf-8 -*-
import sqlite3, glob, os
import pandas as pd

CACHE = r"D:\mystock\cache_daily\stock_data.db"
SLI = r"d:\mystock\solo\sli\db\sli.db"
IND = r"d:\mystock\solo\cache_industry\industry_holdings.db"
PQ = r"D:\mystock\cache_daily\parquet"

def cols(db, t):
    c = sqlite3.connect(db)
    try:
        d = pd.read_sql_query(f"PRAGMA table_info({t})", c)
        return d["name"].tolist()
    finally:
        c.close()

def tables(db):
    c = sqlite3.connect(db)
    try:
        d = pd.read_sql_query("SELECT name FROM sqlite_master WHERE type='table'", c)
        return d["name"].tolist()
    finally:
        c.close()

print("### CACHE_DB tables")
print(tables(CACHE))
print("### SLI tables")
print(tables(SLI))
print("### leaderboard_v2 cols")
print(cols(SLI, "leaderboard_v2"))
print("### ind tables")
print(tables(IND))
print("### forecast_vip sample")
fs = sorted(glob.glob(os.path.join(PQ, "forecast_vip_*.parquet")))
print([os.path.basename(f) for f in fs])
if fs:
    d = pd.read_parquet(fs[-1])
    print("fcols", d.columns.tolist())
    print(d.head(3).to_dict("records"))
print("### fina_indicator parquet sample")
fi = sorted(glob.glob(os.path.join(PQ, "fina_indicator_600004*.parquet")))
if fi:
    d = pd.read_parquet(fi[0])
    print(os.path.basename(fi[0]), d.columns.tolist())
    print(d.head(2).to_dict("records"))
