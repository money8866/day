# -*- coding: utf-8 -*-
# 回填 fina_indicator（财务指标）到 stock_data.db 的新表 fina_indicator_cache
# 字段覆盖：营收增速 or_yoy / 净利增速 netprofit_yoy / 毛利率 grossprofit_margin / 现金流质量 ocf_to_or / ROE
# 逐股拉取（每股 1 次 API，20230101 起全部报告期），断点续跑：已入库股票自动跳过
import sys
import time
import sqlite3
import pandas as pd

sys.path.insert(0, r"d:\mystock\solo")
import stock_cache as sc

DB_PATH = r"D:\mystock\cache_daily\stock_data.db"
FIELDS = "ts_code,ann_date,end_date,grossprofit_margin,netprofit_yoy,or_yoy,netprofit_2yoy,ocf_to_or,ocf_yoy,roe,debt_to_assets"
START_DATE = "20230101"
END_DATE = "20261231"


def ensure_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fina_indicator_cache (
            ts_code TEXT NOT NULL,
            ann_date TEXT,
            end_date TEXT NOT NULL,
            grossprofit_margin REAL,
            netprofit_yoy REAL,
            or_yoy REAL,
            netprofit_2yoy REAL,
            ocf_to_or REAL,
            ocf_yoy REAL,
            roe REAL,
            debt_to_assets REAL,
            PRIMARY KEY (ts_code, end_date)
        )
    """)
    conn.commit()


def universe_codes():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT DISTINCT ts_code FROM stk_factor_pro WHERE trade_date=(SELECT MAX(trade_date) FROM stk_factor_pro)").fetchall()
    conn.close()
    return [r[0] for r in rows]


def done_codes(conn):
    return set(r[0] for r in conn.execute("SELECT DISTINCT ts_code FROM fina_indicator_cache").fetchall())


def main():
    pro = sc._get_pro()
    conn = sqlite3.connect(DB_PATH)
    ensure_table(conn)
    codes = universe_codes()
    done = done_codes(conn)
    todo = [c for c in codes if c not in done]
    print(f"[fina] 全市场 {len(codes)} 只，已入库 {len(done)}，待回填 {len(todo)}", flush=True)
    ok = fail = 0
    t0 = time.time()
    buf = []
    for k, code in enumerate(todo, 1):
        try:
            df = pro.fina_indicator(ts_code=code, start_date=START_DATE, end_date=END_DATE, fields=FIELDS)
            if df is not None and len(df) > 0:
                buf.append(df)
                ok += 1
            else:
                conn.execute("INSERT OR REPLACE INTO fina_indicator_cache (ts_code,end_date) VALUES (?,'EMPTY')", (code,))
                ok += 1
        except Exception as e:
            fail += 1
            print(f"[fina] {code} 失败: {e}", flush=True)
        if len(buf) >= 50:
            pd.concat(buf, ignore_index=True).drop_duplicates(["ts_code", "end_date"], keep="last").to_sql(
                "fina_indicator_cache", conn, if_exists="append", index=False, method="multi", chunksize=500)
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_fina ON fina_indicator_cache(ts_code,end_date)")
            buf = []
            conn.commit()
        if k % 100 == 0:
            el = time.time() - t0
            print(f"[fina] 进度 {k}/{len(todo)} 成功={ok} 失败={fail} 耗时={el:.0f}s 剩余约 {(el / k) * (len(todo) - k):.0f}s", flush=True)
        time.sleep(0.12)
    if buf:
        pd.concat(buf, ignore_index=True).drop_duplicates(["ts_code", "end_date"], keep="last").to_sql(
            "fina_indicator_cache", conn, if_exists="append", index=False, method="multi", chunksize=500)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_fina ON fina_indicator_cache(ts_code,end_date)")
        conn.commit()
    print(f"[fina] 回填结束: 成功={ok} 失败={fail}", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
