import sqlite3

conn = sqlite3.connect('d:/mystock/solo/cache_backbone_tushare/theme_trend_sentiment.db')
cur = conn.cursor()

# 检查表结构
cur.execute("PRAGMA table_info(theme_scores)")
cols = cur.fetchall()
print("表结构:", [c[1] for c in cols])

cur.execute('SELECT MAX(trade_date) FROM theme_scores')
latest = cur.fetchone()[0]
print(f"\n最新日期: {latest}\n")

# 查询人形机器人
cur.execute(f"""
    SELECT rank, theme, composite_score, trend_score, sentiment_score, 
           top10_days_10d, top10_days_20d, n_stocks
    FROM theme_scores 
    WHERE trade_date = '{latest}' AND theme LIKE '%人形机器人%'
""")

row = cur.fetchone()
if row:
    print(f"人形机器人数据:")
    print(f"  排名: {row[0]}, 综合分: {row[2]}, 趋势分: {row[3]}, 情绪分: {row[4]}, 10日稳: {row[5]}, 20日稳: {row[6]}, 成分股: {row[7]}")
else:
    print("未找到人形机器人数据")

# 查询所有包含"机器人"的主题（按20日稳排名）
cur.execute(f"""
    SELECT rank, theme, composite_score, trend_score, sentiment_score, 
           top10_days_10d, top10_days_20d
    FROM theme_scores 
    WHERE trade_date = '{latest}' AND theme LIKE '%机器人%'
    ORDER BY top10_days_20d DESC
""")

print("\n所有'机器人'相关主题（按20日稳排名）:")
for r in cur.fetchall():
    print(f"  {r[0]:>2}. {r[1]:<16} 综合{r[2]:>5.1f} 趋势{r[3]:>5.1f} 情绪{r[4]:>5.1f} 10稳{r[5]:>3} 20稳{r[6]:>3}")

# 排名34的位置
cur.execute(f"""
    SELECT rank, theme, composite_score, top10_days_10d, top10_days_20d
    FROM theme_scores 
    WHERE trade_date = '{latest}' AND (rank BETWEEN 30 AND 40)
    ORDER BY rank
""")

print("\n排名30-40区间:")
for r in cur.fetchall():
    print(f"  {r[0]:>2}. {r[1]:<16} 综合{r[2]:>5.1f} 10稳{r[3]:>3} 20稳{r[4]:>3}")

conn.close()
