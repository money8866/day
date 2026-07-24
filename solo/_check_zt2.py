import sqlite3, os

base = r'd:\mystock\solo\cache_backbone_tushare'
conn = sqlite3.connect(os.path.join(base, 'cache.db'))

# 找所有包含 20260724 的缓存key
cur = conn.execute("SELECT key FROM cache_data WHERE key LIKE '%20260724%' OR key LIKE '%2026-07-24%'")
keys = cur.fetchall()
print("20260724 缓存条目:")
for k in keys:
    print(f"  {k[0]}")

# 看看 theme_trend_sentiment_score.py 的 stock_basic 缓存
cur = conn.execute("SELECT key FROM cache_data WHERE key LIKE '%stock_basic%' LIMIT 5")
print("\nstock_basic缓存:")
for k in cur.fetchall():
    print(f"  {k[0]}")

# 直接查 theme_trend_sentiment 数据库看特高压的 detailed data
conn2 = sqlite3.connect(os.path.join(base, 'theme_trend_sentiment.db'))
cur = conn2.execute("SELECT * FROM theme_scores WHERE theme='特高压' AND trade_date='20260724'")
cols = [d[0] for d in cur.description]
row = cur.fetchone()
if row:
    data = dict(zip(cols, row))
    print(f"\n特高压今日数据:")
    for k, v in data.items():
        print(f"  {k}: {v}")

# 检查是否有 dc_member 缓存的 K线数据
cur = conn.execute("SELECT key FROM cache_data WHERE key LIKE '%tsc_daily_kline%' AND (key LIKE '%600089%' OR key LIKE '%000400%' OR key LIKE '%000586%' OR key LIKE '%920018%') LIMIT 10")
print("\n特高压相关K线缓存:")
for k in cur.fetchall():
    print(f"  {k[0]}")

conn.close()
conn2.close()
