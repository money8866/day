import sqlite3
c = sqlite3.connect('report_daily/theme_scores.db')
print('日期数:', c.execute("SELECT COUNT(DISTINCT trade_date) FROM theme_scores").fetchone())
print('日期列表:', [r[0] for r in c.execute("SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date")][-10:])
cols = [r[1] for r in c.execute('PRAGMA table_info(theme_scores)')]
print('theme_scores 列:', cols)
print('theme_top_stocks 日期:', [r[0] for r in c.execute("SELECT DISTINCT trade_date FROM theme_top_stocks ORDER BY trade_date")])
# theme_state 分布（最近日期）
for d in [r[0] for r in c.execute("SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date DESC LIMIT 3")]:
    print(d, dict(c.execute("SELECT theme_state,COUNT(*) FROM theme_scores WHERE trade_date=? GROUP BY theme_state", (d,)).fetchall()))
