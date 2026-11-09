# -*- coding: utf-8 -*-
"""
股东户数抓取：按季度 enddate + ann_date 区间分块，落库 holder_num_cache。

要点：
1) offset 分页在服务端排序不稳定，可能漏/重；改用 enddate + ann_date 小区间过滤，结果确定。
2) 同一 (ts_code, end_date) 可有多条 ann_date（补充/更正披露）→ 只保留最早 ann_date，
   保证 point-in-time（信号日只能用当时已知数据）。
3) 只保留季度末 end_date，保证口径可比。
"""
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if "TUSHARE_TOKEN" not in os.environ:
    for _l in open(r"D:\mystock\config\.env"):
        if _l.strip().startswith("TUSHARE_TOKEN="):
            os.environ["TUSHARE_TOKEN"] = _l.strip().split("=", 1)[1].strip().strip('"')
            break

import pandas as pd
import tushare as ts

CACHE = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB = os.path.join(CACHE, "stock_data.db")

QUARTER_ENDS = []
for y in range(2020, 2027):
    for md in ("0331", "0630", "0930", "1231"):
        q = "%d%s" % (y, md)
        if q <= "20260930":
            QUARTER_ENDS.append(q)

WIN = 10     # ann_date 窗口天数
SPAN = 155   # 季末后最大披露跨度（年报最晚 4/30，约 120 天，留余量）

pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])

con = sqlite3.connect(DB, timeout=120)
con.execute("""
CREATE TABLE IF NOT EXISTS holder_num_cache (
    ts_code TEXT NOT NULL,
    end_date TEXT NOT NULL,
    ann_date TEXT,
    holder_num REAL,
    PRIMARY KEY (ts_code, end_date)
)""")
con.execute("CREATE INDEX IF NOT EXISTS idx_holder_end ON holder_num_cache(end_date)")
con.commit()

done = {r[0] for r in con.execute("SELECT DISTINCT end_date FROM holder_num_cache").fetchall()}
print("已有季度: %d，待抓取季度: %d" % (len(done), len([q for q in QUARTER_ENDS if q not in done])))

from datetime import datetime, timedelta


def _add(day, n):
    return (datetime.strptime(day, "%Y%m%d") + timedelta(days=n)).strftime("%Y%m%d")


for q in QUARTER_ENDS:
    if q in done:
        continue
    frames = []
    a = q
    while a <= _add(q, SPAN):
        b = _add(a, WIN - 1)
        got = None
        for attempt in range(3):
            try:
                got = pro.stk_holdernumber(enddate=q, start_date=a, end_date=b)
                break
            except Exception as e:
                print("  [重试%d] %s %s~%s: %s" % (attempt + 1, q, a, b, e))
                time.sleep(2.0)
        if got is not None and len(got):
            frames.append(got)
        time.sleep(0.12)
        a = _add(a, WIN)

    if not frames:
        print("%s 无数据" % q)
        con.execute("INSERT OR IGNORE INTO holder_num_cache VALUES ('__NODATA__','%s','',NULL)" % q)
        con.commit()
        continue

    df = pd.concat(frames, ignore_index=True)
    df = df[df["end_date"] == q]
    df["ann_date"] = df["ann_date"].fillna("").astype(str)
    df["holder_num"] = pd.to_numeric(df["holder_num"], errors="coerce")
    df = df[df["holder_num"].notna() & (df["holder_num"] > 0)]
    # 同一 (ts_code, end_date) 保留最早 ann_date
    df = df.sort_values(["ts_code", "ann_date"], kind="mergesort").drop_duplicates(
        subset=["ts_code", "end_date"], keep="first")
    df = df[["ts_code", "end_date", "ann_date", "holder_num"]]
    con.executemany(
        "INSERT OR REPLACE INTO holder_num_cache VALUES (?,?,?,?)",
        df.itertuples(index=False, name=None))
    con.commit()
    print("%s 抓取 %d 只股票（覆盖 ann_date %s~%s）"
          % (q, len(df), df["ann_date"].min(), df["ann_date"].max()))

con.execute("DELETE FROM holder_num_cache WHERE ts_code='__NODATA__'")
con.commit()
n = con.execute("SELECT COUNT(*) FROM holder_num_cache").fetchone()[0]
q_n = con.execute("SELECT COUNT(DISTINCT end_date) FROM holder_num_cache").fetchone()[0]
print("\n落库完成：%d 行，%d 个季度" % (n, q_n))
print(con.execute(
    "SELECT end_date, COUNT(*) FROM holder_num_cache GROUP BY end_date ORDER BY end_date").fetchall())
con.close()
