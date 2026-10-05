# -*- coding: utf-8 -*-
"""一次性探针: 摸清中证2000相关数据在本地 Tushare 缓存中的可得性"""
import sqlite3

DB = r"D:\mystock\cache_daily\stock_data.db"
c = sqlite3.connect(DB)
tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("TABLES:", tables)

# 1) index_daily_cache 里有哪些指数
try:
    n, = c.execute("SELECT COUNT(*) FROM index_daily_cache").fetchone()
    print("index_daily_cache rows:", n)
    rows = c.execute("SELECT ts_code, COUNT(*), MIN(trade_date), MAX(trade_date) "
                     "FROM index_daily_cache GROUP BY ts_code ORDER BY COUNT(*) DESC").fetchall()
    print("index count:", len(rows))
    for r in rows[:80]:
        print("   IDX", r)
except Exception as e:
    print("index_daily_cache ERR:", e)

# 2) 找 932000 / 中证2000 相关指数
for like in ("932000", "9%"):
    try:
        rows = c.execute("SELECT DISTINCT ts_code FROM index_daily_cache WHERE ts_code LIKE ?", (like,)).fetchall()
        print(f"LIKE {like}:", rows[:60])
    except Exception as e:
        print("like err", e)

# 3) 全表清单里搜可能的成分股表
for t in tables:
    low = t.lower()
    if any(k in low for k in ("member", "index", "weight", "constituent", "hs300", "basic")):
        try:
            n, = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()
            print(f"  >> {t}: {n} rows")
        except Exception as e:
            print("  >>", t, "ERR", e)
