import sqlite3

db_path = r'd:\mystock\solo\cache_backbone_tushare\theme_analysis.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print('='*80)
print('📊 SQLite 数据库信息')
print('='*80)

# 查看表列表
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print(f'\n📋 数据表列表: {len(tables)} 个')
for table in tables:
    cursor.execute(f'SELECT COUNT(*) FROM {table[0]}')
    count = cursor.fetchone()[0]
    print(f'  ✓ {table[0]}: {count} 条记录')

# 查看主题打分示例
print('\n\n📈 主题打分示例 (前5条):')
cursor.execute('SELECT trade_date, theme_name, today_score, avg_score_10d, score_trend FROM theme_scores ORDER BY trade_date DESC LIMIT 5')
headers = ['日期', '主题', '今日评分', '10日均分', '趋势']
for row in cursor.fetchall():
    print(f'  {row[0]} | {row[1]:15s} | {row[2]:6.1f} | {row[3]:6.1f} | {row[4]}')

# 查看龙头股打分示例
print('\n\n🏆 龙头股打分示例 (前5条):')
cursor.execute('SELECT trade_date, name, ts_code, total_score, change_5, second_wave_prob FROM leader_scores ORDER BY total_score DESC LIMIT 5')
headers = ['日期', '名称', '代码', '总分', '5日涨幅', '二波概率']
for row in cursor.fetchall():
    print(f'  {row[0]} | {row[1]:10s} | {row[2]:10s} | {row[3]:6.1f} | {row[4]:+6.1f}% | {row[5]:3d}%')

# 查看策略推荐
print('\n\n🎯 策略推荐:')
cursor.execute('SELECT trade_date, strategy_type, name, total_score, probability FROM strategy_recommendations ORDER BY strategy_type, probability DESC')
headers = ['日期', '策略类型', '名称', '总分', '概率']
for row in cursor.fetchall():
    print(f'  {row[0]} | {row[1]:10s} | {row[2]:10s} | {row[3]:6.1f} | {row[4]:3d}%')

conn.close()
print('\n\n✅ 数据库验证完成！')
