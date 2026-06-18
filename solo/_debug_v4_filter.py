import os
import sqlite3
import numpy as np
import pandas as pd
from collections import defaultdict

BASE_DIR = r"D:\mystock\solo"
KLINE_CACHE = os.path.join(BASE_DIR, "cache_daily")
PORTFOLIO_DB = os.path.join(BASE_DIR, "cache_backbone_tushare", "theme_portfolio.db")

def is_mainboard(code):
    if code.startswith("60") or code.startswith("68"):
        return True
    if code.startswith("00") or code.startswith("30"):
        return True
    return False

def sma(arr, n):
    if len(arr) < n:
        return np.zeros(len(arr))
    result = np.zeros(len(arr))
    for i in range(n - 1):
        result[i] = np.mean(arr[: i + 1])
    result[n - 1 :] = np.convolve(arr, np.ones(n) / n, mode="valid")
    return result

def slope_n(arr, n):
    if len(arr) < n + 5:
        return 0.0
    recent = arr[max(0, len(arr) - n - 5) :]
    x = np.arange(len(recent))
    try:
        s = np.polyfit(x, recent, 1)[0]
        return s / np.mean(recent) * 100 if np.mean(recent) > 0 else 0
    except Exception:
        return 0.0

# 加载股票池
conn = sqlite3.connect(PORTFOLIO_DB)
c = conn.cursor()
c.execute("SELECT ts_code, name, theme_name, mcap FROM portfolio")
rows = c.fetchall()
conn.close()

stock_map = {}
theme_map = {}
for r in rows:
    if r[0] not in stock_map:
        stock_map[r[0]] = {"name": r[1], "mcap": float(r[3]) if r[3] else 0}
        theme_map[r[0]] = r[2]

print(f"股票池: {len(stock_map)} 只")

# 调试各层过滤
stats = {"total": 0, "mainboard": 0, "has_kline": 0, "mc_ok": 0, "amt_ok": 0, "bull_ok": 0}
examples = []

for code, info in stock_map.items():
    stats["total"] += 1
    if not is_mainboard(code):
        continue
    stats["mainboard"] += 1

    fpath = os.path.join(KLINE_CACHE, f"{code}.csv")
    if not os.path.exists(fpath):
        continue
    df = pd.read_csv(fpath)
    if df.empty or len(df) < 60:
        continue
    stats["has_kline"] += 1

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)
    last = len(df) - 1

    close = df["close"].values
    amount = df["amount"].values
    mc = info["mcap"]
    amt_20 = float(np.mean(amount[max(0, last-19):last+1])) / 1e8

    # 检查市值过滤
    if mc < 100 or mc > 5000:
        continue
    stats["mc_ok"] += 1

    # 检查成交过滤
    if amt_20 < 3:
        continue
    stats["amt_ok"] += 1

    # 检查bull_score
    ma5 = sma(close, 5)[last]
    ma10 = sma(close, 10)[last]
    ma20 = sma(close, 20)[last]
    ma60 = sma(close, 60)[last]
    slope20 = slope_n(close, 20)

    bull = 0
    if ma10 > ma20: bull += 20
    if ma20 > ma60: bull += 20
    if close[last] > ma20: bull += 20
    if slope20 > 0: bull += 20
    if ma5 > ma10: bull += 20

    examples.append({
        "code": code, "name": info["name"], "mc": mc, "amt": amt_20,
        "bull": bull, "theme": theme_map.get(code, "")
    })

    if bull >= 40:
        stats["bull_ok"] += 1

print(f"\n过滤统计:")
for k, v in stats.items():
    print(f"  {k}: {v}")

# 看看bull_score分布
print(f"\n通过市值+成交的股票 bull_score 分布:")
bull_counts = defaultdict(int)
for e in examples:
    bull_counts[e["bull"]] += 1
for k in sorted(bull_counts.keys()):
    print(f"  bull={k}: {bull_counts[k]} 只")

# 看看市值分布
print(f"\n市值分布 (通过主板+K线+市值过滤):")
mcap_bins = [(0,100),(100,300),(300,1000),(1000,3000),(3000,5000),(5000,99999)]
for low, high in mcap_bins:
    cnt = sum(1 for e in examples if low <= e["mc"] < high)
    print(f"  {low}-{high}亿: {cnt} 只")

# 成交分布
print(f"\n20日均成交分布:")
amt_bins = [(0,3),(3,10),(10,30),(30,100),(100,99999)]
for low, high in amt_bins:
    cnt = sum(1 for e in examples if low <= e["amt"] < high)
    print(f"  {low}-{high}亿: {cnt} 只")
