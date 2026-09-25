"""三花聚顶研究 - 数据审计第二轮：stk_factor_pro 范围 / kpl.db / 板块分布 / Regime 与 HVT 产物"""
import os
import sqlite3
import glob

DB = r"D:\mystock\cache_daily\stock_data.db"
CD = r"D:\mystock\cache_daily"


def main():
    conn = sqlite3.connect(DB, timeout=60)
    print("=== stk_factor_pro coverage ===")
    print(conn.execute("SELECT MIN(trade_date),MAX(trade_date),COUNT(DISTINCT ts_code),COUNT(*) FROM stk_factor_pro").fetchone())
    print("per-year:")
    for r in conn.execute("SELECT substr(trade_date,1,4) y,COUNT(*) FROM stk_factor_pro GROUP BY y ORDER BY y").fetchall():
        print("  ", r)

    print("\n=== suffix (board) distribution in daily_cache ===")
    for r in conn.execute(
        "SELECT substr(ts_code,1,3) pfx, COUNT(DISTINCT ts_code) FROM daily_cache "
        "GROUP BY pfx ORDER BY 2 DESC LIMIT 25").fetchall():
        print("  ", r)
    print("BJ count:", conn.execute("SELECT COUNT(DISTINCT ts_code) FROM daily_cache WHERE ts_code LIKE '%.BJ'").fetchone())
    print("main board 60/000/001/002/003:", conn.execute(
        "SELECT COUNT(DISTINCT ts_code) FROM daily_cache WHERE substr(ts_code,1,2) IN ('60','00')").fetchone())

    print("\n=== kpl.db ===")
    kp = sqlite3.connect(os.path.join(CD, "kpl.db"), timeout=30)
    for r in kp.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        t = r[0]
        try:
            n = kp.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            cl = [c[1] for c in kp.execute(f'PRAGMA table_info("{t}")').fetchall()]
            print(f"  {t} rows={n} cols={cl[:20]}")
            dfr = kp.execute(f'SELECT * FROM "{t}" LIMIT 1').fetchall()
            print("     sample:", str(dfr)[:400])
        except Exception as e:
            print(f"  {t} ERR {e}")
    kp.close()

    print("\n=== market_regime_v3 outputs ===")
    for p in glob.glob(r"d:\mystock\solo\market_regime_v3\**\*", recursive=True):
        print("  ", p.replace("d:\\mystock\\solo\\", ""))

    print("\n=== hvt_bull outputs (cache_daily / report_daily) ===")
    for pat in ("*hvt*", "*HVT*"):
        for p in glob.glob(os.path.join(CD, pat)):
            print("  CD:", os.path.basename(p))
    for p in glob.glob(r"d:\mystock\solo\hvt_bull\**\*", recursive=True)[:80]:
        print("  ", p.replace("d:\\mystock\\solo\\", ""))

    print("\n=== report_daily dir listing ===")
    for p in sorted(glob.glob(r"d:\mystock\solo\report_daily\*")):
        print("  ", os.path.basename(p))

    conn.close()


if __name__ == '__main__':
    main()
