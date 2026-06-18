import sqlite3, os
import numpy as np
import pandas as pd

# 检查几只主板股票的 amount
kline_dir = r"D:\mystock\solo\cache_daily"

stocks = [
    ("600519.SH", "贵州茅台"),
    ("600036.SH", "招商银行"),
    ("601318.SH", "中国平安"),
    ("000858.SZ", "五粮液"),
    ("000001.SZ", "平安银行"),
    ("600030.SH", "中信证券"),
]

for code, name in stocks:
    p = os.path.join(kline_dir, f"{code}.csv")
    if not os.path.exists(p):
        print(f"{name}({code}): 无K线")
        continue
    df = pd.read_csv(p)
    df = df.sort_values("trade_date").tail(20)
    avg_amount = df["amount"].astype(float).mean()
    print(f"{name}({code}): 20日均 amount={avg_amount:.0f} | 若为亿元= {avg_amount:.2f}亿 | 若除以1万= {avg_amount/10000:.2f}亿")
