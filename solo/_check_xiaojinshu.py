import sqlite3

conn = sqlite3.connect('d:/mystock/solo/cache_backbone_tushare/theme_trend_sentiment.db')
cur = conn.cursor()

# 查询今天（20260615）的排名
cur.execute("""
    SELECT rank, theme, composite_score, n_stocks, top10_days_10d, top10_days_20d
    FROM theme_scores 
    WHERE trade_date = '20260615'
    ORDER BY rank
    LIMIT 30
""")

print("【20260615 排名前30】")
print(f"{'排名':<4} {'主题':<16} {'综合分':>6} {'成分':>5} {'10日稳':>6} {'20日稳':>6}")
print("-" * 55)
for row in cur.fetchall():
    print(f"{row[0]:<4} {row[1]:<16} {row[2]:>6.1f} {row[3]:>5} {row[4]:>6} {row[5]:>6}")

# 查询小金属的详细数据
cur.execute("""
    SELECT rank, theme, composite_score, n_stocks, top10_days_10d, top10_days_20d
    FROM theme_scores 
    WHERE trade_date = '20260615' AND theme = '小金属'
""")

print("\n【小金属在20260615的数据】")
row = cur.fetchone()
if row:
    print(f"排名: {row[0]}, 综合分: {row[2]}, 成分: {row[3]}, 10日稳: {row[4]}, 20日稳: {row[5]}")
else:
    print("未找到小金属数据")

conn.close()
