import sqlite3, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
con = sqlite3.connect(r"D:\mystock\cache_daily\stock_data.db")
tabs = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name")]
print("tables:", tabs)
for t in ["stk_factor_pro", "daily_cache", "daily_basic_cache"]:
    if t in tabs:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        dmin, dmax = con.execute(f"SELECT MIN(trade_date), MAX(trade_date) FROM {t}").fetchone()
        print(f"--- {t}: rows={n} range={dmin}..{dmax} ncols={len(cols)}")
        print("   cols:", cols)
print("cache_meta:", con.execute("SELECT * FROM cache_meta").fetchall())
