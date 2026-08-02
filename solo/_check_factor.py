# -*- coding: utf-8 -*-
"""快速验证技术因子和K线数据覆盖"""
import sqlite3
import os
import struct
import pandas as pd

CACHE_DIR = r'D:\mystock\cache_daily'
STOCK_DB = os.path.join(CACHE_DIR, 'stock_data.db')
TDX_PATH = r"C:\new_tdx"


def parse_tdx_day_file(filepath):
    if not os.path.exists(filepath):
        return None
    records = []
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32:
                break
            date_int = struct.unpack("<i", chunk[0:4])[0]
            open_p = struct.unpack("<i", chunk[4:8])[0] / 100.0
            high_p = struct.unpack("<i", chunk[8:12])[0] / 100.0
            low_p = struct.unpack("<i", chunk[12:16])[0] / 100.0
            close_p = struct.unpack("<i", chunk[16:20])[0] / 100.0
            amount_yuan = struct.unpack("<f", chunk[20:24])[0]
            vol_shares = struct.unpack("<i", chunk[24:28])[0] / 100.0
            records.append({
                "trade_date": str(date_int),
                "open": open_p, "high": high_p, "low": low_p,
                "close": close_p, "vol": vol_shares,
                "amount": round(amount_yuan / 1000, 3),
            })
    if not records:
        return None
    df = pd.DataFrame(records)
    df = df.sort_values("trade_date").reset_index(drop=True)
    df["pre_close"] = df["close"].shift(1)
    df["pct_chg"] = df["close"].pct_change() * 100
    df["pct_chg"] = df["pct_chg"].fillna(0)
    return df


# 检查SQLite
print("=" * 60)
print("SQLite技术因子覆盖:")
conn = sqlite3.connect(STOCK_DB, timeout=10.0)
r = conn.execute('SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM stk_factor_pro').fetchone()
print(f"  全表: trade_date={r[0]}~{r[1]}, 行数={r[2]}")
r = conn.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM stk_factor_pro WHERE ts_code='600594.SH'").fetchone()
print(f"  600594.SH: trade_date={r[0]}~{r[1]}, 行数={r[2]}")
r = conn.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM stk_factor_pro WHERE ts_code='300750.SZ'").fetchone()
print(f"  300750.SZ: trade_date={r[0]}~{r[1]}, 行数={r[2]}")
# 列字段
cols = conn.execute('PRAGMA table_info(stk_factor_pro)').fetchall()
factor_cols = [c[1] for c in cols if 'bfq' in c[1] or c[1] in ('close', 'total_mv', 'turnover_rate', 'ts_code', 'trade_date')]
print(f"  关键字段: {factor_cols[:20]}")
conn.close()

# 检查TDX K线
print("\n" + "=" * 60)
print("TDX K线覆盖:")
for code in ['600594.SH', '300750.SZ', '000001.SZ']:
    sym, market = code.split('.')
    prefix = 'sh' if market == 'SH' else 'sz'
    subdir = 'sh' if market == 'SH' else 'sz'
    fp = os.path.join(TDX_PATH, 'vipdoc', subdir, 'lday', f'{prefix}{sym}.day')
    if os.path.exists(fp):
        df = parse_tdx_day_file(fp)
        if df is not None:
            print(f"  {code}: {df['trade_date'].min()}~{df['trade_date'].max()}, {len(df)}条")
    else:
        print(f"  {code}: 文件不存在 {fp}")
