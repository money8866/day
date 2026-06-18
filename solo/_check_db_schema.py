import sqlite3

# 检查 theme_portfolio.db 的表结构
conn = sqlite3.connect("D:/mystock/solo/cache_backbone_tushare/theme_portfolio.db")
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("Portfolio DB tables:")
for row in c.fetchall():
    print(f"  - {row[0]}")
    c.execute(f"PRAGMA table_info({row[0]})")
    cols = c.fetchall()
    for col in cols:
        print(f"      {col[1]} ({col[2]})")
conn.close()

# 检查 theme_scores 表
conn2 = sqlite3.connect("D:/mystock/solo/cache_backbone_tushare/theme_trend_sentiment.db")
c2 = conn2.cursor()
c2.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("\nTheme DB tables:")
for row in c2.fetchall():
    print(f"  - {row[0]}")
    c2.execute(f"PRAGMA table_info({row[0]})")
    cols = c2.fetchall()
    for col in cols:
        print(f"      {col[1]} ({col[2]})")
conn2.close()

# 检查 dc_hot.db
try:
    conn3 = sqlite3.connect("D:/mystock/solo/cache_backbone_tushare/dc_hot.db")
    c3 = conn3.cursor()
    c3.execute("SELECT name FROM sqlite_master WHERE type='table'")
    print("\nDC Hot DB tables:")
    for row in c3.fetchall():
        print(f"  - {row[0]}")
    conn3.close()
except Exception as e:
    print(f"\nDC Hot DB: {e}")
