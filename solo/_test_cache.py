# -*- coding: utf-8 -*-
import sqlite3, os, json
import pandas as pd

BASE = r'd:\mystock\solo\cache_backbone_tushare'
CACHE = os.path.join(BASE, 'cache.db')
SCORE = os.path.join(BASE, 'theme_trend_sentiment.db')

# 检查 cache.db 中的所有 key
conn = sqlite3.connect(CACHE)
cur = conn.cursor()
cur.execute('SELECT key FROM cache_data')
keys = [row[0] for row in cur.fetchall()]
print(f'[cache.db] keys ({len(keys)}):')
for k in keys:
    print(f'  - {k}')
conn.close()

# 读取 theme_trend_sentiment.db
conn = sqlite3.connect(SCORE)
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print(f'\n[theme_scores.db] tables: {[r[0] for r in cur.fetchall()]}')

cur.execute('SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date DESC LIMIT 5')
print(f'最近交易日: {[r[0] for r in cur.fetchall()]}')

cur.execute('SELECT COUNT(*) FROM theme_scores')
print(f'总记录数: {cur.fetchone()[0]}')

# 读取第一条数据查看
cur.execute('SELECT * FROM theme_scores LIMIT 1')
row = cur.fetchone()
cols = [desc[0] for desc in cur.description]
print(f'\n字段: {cols}')
print(f'示例: {dict(zip(cols, row))}')
conn.close()

# 检查其他 db
for fname in ['theme_portfolio.db', 'market_analysis.db']:
    fp = os.path.join(BASE, fname)
    if os.path.exists(fp):
        conn = sqlite3.connect(fp)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        print(f'\n[{fname}] tables: {tables}')
        for t in tables:
            try:
                cur.execute(f'SELECT COUNT(*) FROM {t}')
                n = cur.fetchone()[0]
                cur.execute(f'SELECT * FROM {t} LIMIT 1')
                r = cur.fetchone()
                cols = [desc[0] for desc in cur.description]
                print(f'  {t}: {n}条, 字段: {cols}')
                print(f'       示例: {dict(zip(cols, r))}')
            except Exception as e:
                print(f'  {t}: 错误 {e}')
        conn.close()
    else:
        print(f'\n[{fname}] 不存在')

# 检查 cache_daily 中的 kline 文件
kline_dir = r'd:\mystock\solo\cache_daily'
if os.path.exists(kline_dir):
    files = [f for f in os.listdir(kline_dir) if f.endswith('.csv')]
    print(f'\n[cache_daily] K线文件: {len(files)} 个')
    if files:
        df = pd.read_csv(os.path.join(kline_dir, files[0]))
        print(f'   示例文件: {files[0]}')
        print(f'   列: {list(df.columns)}')
        print(f'   最近3条:')
        print(df.tail(3))
