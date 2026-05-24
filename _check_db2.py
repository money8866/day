# -*- coding: utf-8 -*-
import sqlite3, os

path = r'C:\Users\kongx\mystock\cache_db\tdx_concept.db'
conn = sqlite3.connect(path)

# 某天的数据，按涨幅排序（假设col4是涨跌幅）
cur2 = conn.execute("SELECT * FROM tdx_index WHERE trade_date='20260521' ORDER BY idx_count DESC LIMIT 5")
print("Top 5 by idx_count on 20260521:")
for r in cur2.fetchall():
    print(f"  {r}")

# 看看有哪些trade_date
cur3 = conn.execute("SELECT DISTINCT trade_date FROM tdx_index ORDER BY trade_date DESC LIMIT 5")
print("\nAvailable dates:")
for r in cur3.fetchall():
    print(f"  {r}")

# tdx_member 样本
cur4 = conn.execute("SELECT * FROM tdx_member WHERE trade_date='20260521' LIMIT 3")
print("\ntdx_member samples:")
for r in cur4.fetchall():
    print(f"  {r}")

conn.close()
