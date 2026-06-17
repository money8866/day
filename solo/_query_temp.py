import sqlite3, os

db = r'd:\mystock\solo\cache_backbone_tushare\theme_trend_sentiment.db'
conn = sqlite3.connect(db)

# 查询煤炭链最近10天数据
print("=== 煤炭链 最近10天 ===")
cur = conn.execute("SELECT trade_date, trend_score, sentiment_score, composite_score, theme_state FROM theme_scores WHERE theme = '煤炭链' ORDER BY trade_date DESC LIMIT 10")
for r in cur.fetchall():
    print(f'  {r[0]}  趋势={r[1]}  情绪={r[2]}  综合={r[3]}  状态={r[4]}')
conn.close()

# 查询今天所有主题
print()
print("=== 20260617 全部主题 ===")
conn = sqlite3.connect(db)
cur = conn.execute("SELECT theme, trend_score, sentiment_score, composite_score FROM theme_scores WHERE trade_date = '20260617' ORDER BY composite_score DESC")
for r in cur.fetchall():
    print(f'  {r[0]:12s}  趋势={r[1]:6.1f}  情绪={r[2]:6.1f}  综合={r[3]:6.1f}')
conn.close()

os.remove(r'd:\mystock\solo\_query_temp.py')
