import sqlite3, os
import numpy as np
import pandas as pd

BASE = r"D:\mystock\solo\cache_backbone_tushare"

# 1. 检查 theme_portfolio.db
conn = sqlite3.connect(os.path.join(BASE, "theme_portfolio.db"))
cur = conn.cursor()

cur.execute("SELECT DISTINCT trade_date FROM portfolio ORDER BY trade_date DESC LIMIT 1")
latest = cur.fetchone()
print("最新交易日:", latest[0] if latest else "无")

cur.execute(
    f"SELECT ts_code, name, theme_name, layer, mcap, turnover, purity FROM portfolio WHERE trade_date = ? LIMIT 10",
    (latest[0],)
)
print("\n=== 前10个样本（原始mcap值）===")
for row in cur.fetchall():
    print(f"  {row[0]} {row[1]} | {row[2]} | layer={row[3]} | mcap={row[4]} | turnover={row[5]} | purity={row[6]}")

# 统计 mcap 分布
cur.execute(f"SELECT mcap FROM portfolio WHERE trade_date = ? AND mcap IS NOT NULL", (latest[0],))
mcaps = [float(r[0]) for r in cur.fetchall() if r[0]]
mcaps_arr = np.array(mcaps)
print(f"\nmcap 统计: min={mcaps_arr.min():.0f}  median={np.median(mcaps_arr):.0f}  mean={mcaps_arr.mean():.0f}  max={mcaps_arr.max():.0f}  count={len(mcaps_arr)}")
# 分几个区间看
for thresh in [100, 1000, 10000, 100000, 1000000]:
    cnt = int((mcaps_arr <= thresh).sum())
    print(f"  mcap <= {thresh}: {cnt} 只")

conn.close()

# 2. 检查一个具体股票的K线数据确认amount单位
kline_path = os.path.join(r"D:\mystock\solo\cache_daily", "600519.SH.csv")
if os.path.exists(kline_path):
    df = pd.read_csv(kline_path)
    df = df.sort_values("trade_date").tail(5)
    print("\n=== 贵州茅台(600519) 最近5天 amount 原始值 ===")
    for _, r in df.iterrows():
        print(f"  {r['trade_date']}  close={r['close']:.2f}  amount={r['amount']:.0f}  pct_chg={r['pct_chg']:.2f}")

print("\n判断: mcap 看起来可能是什么单位? (亿/万/元?)")
