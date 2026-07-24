# -*- coding: utf-8 -*-
"""列出特高压主题的成份股（从主题产业匹配逻辑回溯）"""
import sqlite3, os, json
import pandas as pd

# 方式1: 从 theme_trend_sentiment.db 查特高压的K线数据源
# 先从缓存DB中找到特高压的全部股票

# 方式2: 直接从 theme.json 匹配逻辑找股票
# 先加载 theme.json
with open(r'd:\mystock\solo\theme.json', encoding='utf-8') as f:
    theme_data = json.load(f)

uhv = theme_data.get('特高压', {})
print("特高压主题定义:")
print(f"  industry: {uhv.get('industry')}")
print(f"  concept: {uhv.get('concept')}")
print(f"  keywords: {uhv.get('keywords')}")
print(f"  exclude: {uhv.get('exclude_keywords')}")
print()

# 从 theme_trend_sentiment_score.py 的 load_dc_members 找到匹配逻辑
# 看看最近一次特高压计算用了哪些股票
# 从 DB 里查特高压的行情数据
db = sqlite3.connect(r'd:\mystock\solo\cache_backbone_tushare\theme_trend_sentiment.db')

# 查今天(20260724)特高压的详细信息
cur = db.execute("""
    SELECT * FROM theme_scores 
    WHERE theme='特高压' AND trade_date='20260724'
    LIMIT 1
""")
cols = [d[0] for d in cur.description]
cur = db.execute("""
    SELECT * FROM theme_scores 
    WHERE theme='特高压' AND trade_date='20260724'
""")
row = cur.fetchone()
if row:
    data = dict(zip(cols, row))
    print("DB中特高压纪录:")
    for k, v in data.items():
        print(f"  {k}: {v}")

# 查最近的DC板块缓存
conn2 = sqlite3.connect(r'd:\mystock\solo\cache_backbone_tushare\cache.db')
cur2 = conn2.execute("""
    SELECT key FROM cache_data 
    WHERE key LIKE '%dc_member%' OR key LIKE '%concept%' 
    ORDER BY key DESC LIMIT 20
""")
print("\nDC缓存key:")
for r in cur2.fetchall():
    print(f"  {r[0]}")
conn2.close()
db.close()
