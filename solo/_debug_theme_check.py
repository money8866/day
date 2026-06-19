import sqlite3

db_path = 'd:/mystock/solo/cache_backbone_tushare/theme_trend_sentiment.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT DISTINCT theme FROM theme_scores ORDER BY theme")
themes = cursor.fetchall()
print('数据库中所有主题:')
for t in themes:
    print(f'  {t[0]}')
print(f'共{len(themes)}个主题')

cursor.execute("SELECT theme, COUNT(*) as cnt FROM theme_scores GROUP BY theme HAVING theme LIKE '%金属%' OR theme LIKE '%稀土%' OR theme LIKE '%磁%'")
print('\n金属相关主题记录数:')
for t in cursor.fetchall():
    print(f'  {t[0]}: {t[1]}次')

# Check小金属
cursor.execute("SELECT trade_date, composite_score, trend_score, sentiment_score, theme_state FROM theme_scores WHERE theme='小金属' ORDER BY trade_date DESC LIMIT 20")
xj = cursor.fetchall()
print(f'\n小金属最近{len(xj)}次记录:')
for r in xj:
    print(f'  {r[0]}: 综合{r[1]:.0f} 趋势{r[2]:.0f} 情绪{r[3]:.0f} 状态{r[4]}')

conn.close()
