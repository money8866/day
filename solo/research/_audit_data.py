"""三花聚顶研究 - 第一阶段数据审计

只读检查 stock_data.db 中已有缓存的覆盖范围与字段完整性，为后续事件研究提供依据。
不写入、不修改任何现有表。
"""
import os
import sqlite3
import pandas as pd

DB = r"D:\mystock\cache_daily\stock_data.db"
CACHE = r"D:\mystock\cache_daily"


def tables(conn):
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]


def cols(conn, t):
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")').fetchall()]


def main():
    conn = sqlite3.connect(DB, timeout=60)
    ts = tables(conn)
    print("=== TABLES ===")
    for t in ts:
        try:
            n = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        except Exception as e:
            n = f"ERR:{e}"
        print(f"{t:32s} rows={n}")

    print("\n=== COLUMNS ===")
    for t in ts:
        print(f"{t:32s} {cols(conn, t)}")

    print("\n=== daily_cache coverage ===")
    if 'daily_cache' in ts:
        r = conn.execute("SELECT MIN(trade_date),MAX(trade_date),COUNT(DISTINCT ts_code),COUNT(*) FROM daily_cache").fetchone()
        print("range:", r)
        print("per-year rows:")
        for row in conn.execute("SELECT substr(trade_date,1,4) y,COUNT(*) FROM daily_cache GROUP BY y ORDER BY y").fetchall():
            print("  ", row)
        print("recent days:", conn.execute("SELECT trade_date,COUNT(*) FROM daily_cache GROUP BY trade_date ORDER BY trade_date DESC LIMIT 5").fetchall())
        print("earliest days:", conn.execute("SELECT trade_date,COUNT(*) FROM daily_cache GROUP BY trade_date ORDER BY trade_date ASC LIMIT 5").fetchall())

    print("\n=== daily_basic_cache coverage ===")
    if 'daily_basic_cache' in ts:
        r = conn.execute("SELECT MIN(trade_date),MAX(trade_date),COUNT(DISTINCT ts_code),COUNT(*) FROM daily_basic_cache").fetchone()
        print("range:", r)
        for row in conn.execute("SELECT substr(trade_date,1,4) y,COUNT(*) FROM daily_basic_cache GROUP BY y ORDER BY y").fetchall():
            print("  ", row)

    print("\n=== adj_factor_cache coverage ===")
    if 'adj_factor_cache' in ts:
        r = conn.execute("SELECT MIN(trade_date),MAX(trade_date),COUNT(DISTINCT ts_code),COUNT(*) FROM adj_factor_cache").fetchone()
        print("range:", r)

    print("\n=== index_daily_cache coverage ===")
    if 'index_daily_cache' in ts:
        for row in conn.execute("SELECT ts_code,MIN(trade_date),MAX(trade_date),COUNT(*) FROM index_daily_cache GROUP BY ts_code").fetchall():
            print("  ", row)

    print("\n=== other limit/related tables ===")
    for t in ts:
        if any(k in t.lower() for k in ('limit', 'zt', 'kpl', 'hot')):
            cl = cols(conn, t)
            print(f"{t}: {cl}")
            try:
                print("  sample:", conn.execute(f'SELECT * FROM "{t}" LIMIT 2').fetchall())
            except Exception as e:
                print("  ", e)

    print("\n=== cache_daily dir: limit-related files ===")
    for fn in sorted(os.listdir(CACHE)):
        low = fn.lower()
        if any(k in low for k in ('limit', 'zt_', '_zt', 'kpl', 'stock_basic', 'trade_cal')):
            p = os.path.join(CACHE, fn)
            print(f"  {fn} {os.path.getsize(p) if os.path.isfile(p) else '<dir>'}")

    conn.close()


if __name__ == '__main__':
    main()
