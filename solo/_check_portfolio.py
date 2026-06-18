import sqlite3
import os

conn = sqlite3.connect(r"D:\mystock\solo\cache_backbone_tushare\theme_portfolio.db")
c = conn.cursor()

# 看几条样例
c.execute("SELECT * FROM portfolio LIMIT 5")
rows = c.fetchall()
cols = [d[0] for d in c.description]
print("Columns:", cols)
print()
for r in rows:
    print(r)

print()
# 统计主题分布
c.execute("SELECT theme_name, COUNT(*) FROM portfolio GROUP BY theme_name ORDER BY COUNT(*) DESC")
for r in c.fetchall()[:20]:
    print(f"  {r[0]}: {r[1]}")

# 市值范围
c.execute("SELECT MIN(mcap), MAX(mcap), AVG(mcap) FROM portfolio")
print(f"\n市值范围: {c.fetchone()}")

conn.close()
