# -*- coding: utf-8 -*-
import sqlite3
from io import StringIO
import pandas as pd

db = 'd:/mystock/solo/cache_backbone_tushare/cache.db'
conn = sqlite3.connect(db)
cur = conn.cursor()

codes = [
    ("000001.SH", "上证指数"),
    ("000300.SH", "沪深300"),
    ("932000.CSI", "中证2000"),
]

for code, name in codes:
    # 使用正确的key格式
    key_pattern = f"tsc_index_kline_ts_code_{code}_%"
    cur.execute("SELECT data FROM cache_data WHERE key LIKE ? LIMIT 1", (key_pattern,))
    row = cur.fetchone()
    if row:
        df = pd.read_csv(StringIO(row[0]))
        print(f"{name}({code}): {len(df)} rows")
        print(f"  字段: {list(df.columns)}")
        print(f"  最新日期: {df['trade_date'].iloc[-1] if 'trade_date' in df.columns else 'N/A'}")
        print(f"  最新close: {df['close'].iloc[-1] if 'close' in df.columns else 'N/A'}")
        # 计算MA
        closes = df['close'].astype(float).values
        ma5 = closes[-5:].mean()
        ma10 = closes[-10:].mean()
        ma20 = closes[-20:].mean()
        cur_price = closes[-1]
        print(f"  MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f} 当前={cur_price:.2f}")
        # 评分
        ma_score = 0
        if ma5 > ma10 > ma20: ma_score = 40
        elif ma5 > ma10: ma_score = 30
        elif ma5 > ma20: ma_score = 20
        elif ma5 < ma10 < ma20: ma_score = 10
        else: ma_score = 15
        idx_score = 30 if cur_price > ma20 else (20 if cur_price > ma10 else (10 if cur_price > ma5 else 0))
        print(f"  MA_score={ma_score} IDX_score={idx_score} → trend={ma_score+idx_score}")
    else:
        print(f"{name}({code}): NOT FOUND")
    print()

conn.close()
