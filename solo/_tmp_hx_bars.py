"""临时：核对 300300.SZ 行情 + 对比标的（用完即删）"""
import sqlite3

DB = r"d:\mystock\cache_daily\stock_data.db"
CODES = ('300300.SZ', '600184.SH', '002902.SZ')
con = sqlite3.connect(DB)
cur = con.cursor()
print("[schema]", [r[1] for r in cur.execute("PRAGMA table_info(daily_cache)").fetchall()])
for c in CODES:
    rows = cur.execute(
        "SELECT trade_date, open, high, low, close, vol FROM daily_cache "
        "WHERE ts_code=? AND trade_date BETWEEN '20260918' AND '20260930' "
        "ORDER BY trade_date", (c,)).fetchall()
    print(f"\n=== {c} ===")
    prev = None
    for d, o, h, l, cl, v in rows:
        pct = (cl / prev - 1) * 100 if prev else 0.0
        print(f"  {d} O={o:.2f} H={h:.2f} L={l:.2f} C={cl:.2f} 涨={pct:+.2f}% vol={v:.0f}")
        prev = cl
con.close()
