# -*- coding: utf-8 -*-
"""列出现有缓存表 + 检查是否存在任何指数相关表（临时脚本）"""
import sqlite3
import stock_cache as sc

c = sqlite3.connect(sc.DB_PATH)
rows = c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
print('DB:', sc.DB_PATH)
print('== 表清单 ==')
for r in rows:
    try:
        n = c.execute('SELECT COUNT(*) FROM "%s"' % r[0]).fetchone()[0]
    except Exception:
        n = -1
    print('%-34s %s' % (r[0], n))

print('\n== 疑似指数相关表 ==')
hits = [r[0] for r in rows if any(k in r[0].lower() for k in ('index', 'idx', 'bench'))]
print(hits or '（无）')

print('\n== 缓存中是否含指数代码（daily_cache 抽查）==')
try:
    n = c.execute("SELECT COUNT(*) FROM daily_cache WHERE ts_code LIKE '0000%.SH' OR ts_code LIKE '3990%.SZ'").fetchone()[0]
    print("daily_cache 中 0000xx.SH / 3990xx.SZ 行数:", n)
    sample = c.execute("SELECT DISTINCT ts_code FROM daily_cache WHERE ts_code LIKE '3990%.SZ' OR ts_code LIKE '000001.SH' LIMIT 10").fetchall()
    print('样例:', [s[0] for s in sample])
except Exception as e:
    print('查询失败:', e)
