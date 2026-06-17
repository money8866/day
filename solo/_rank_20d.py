import sqlite3

conn = sqlite3.connect('d:/mystock/solo/cache_backbone_tushare/theme_trend_sentiment.db')
cur = conn.cursor()

# 获取最新日期
cur.execute('SELECT MAX(trade_date) FROM theme_scores')
latest = cur.fetchone()[0]
print(f"最新日期: {latest}\n")

# 按20日稳定性排名
cur.execute(f"""
    SELECT rank, theme, composite_score, trend_score, sentiment_score, 
           top10_days_10d, top10_days_20d
    FROM theme_scores 
    WHERE trade_date = '{latest}'
    ORDER BY top10_days_20d DESC, composite_score DESC
    LIMIT 30
""")

rows = cur.fetchall()
print(f"{'排名':<4} {'主题':<16} {'综合分':>6} {'趋势分':>6} {'情绪分':>6} {'10日稳':>6} {'20日稳':>6}")
print("-" * 70)
for r in rows:
    print(f"{r[0]:<4} {r[1]:<16} {r[2]:>6.1f} {r[3]:>6.1f} {r[4]:>6.1f} {r[5]:>6} {r[6]:>6}")

conn.close()
