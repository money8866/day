#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sqlite3

conn = sqlite3.connect('cache_backbone_tushare/theme_portfolio.db')
cursor = conn.cursor()

cursor.execute('SELECT COUNT(*) FROM portfolio')
print('投资组合股票总数:', cursor.fetchone()[0])

cursor.execute('SELECT layer, COUNT(*) FROM portfolio GROUP BY layer')
print('\n各层股票数:')
for row in cursor.fetchall():
    print(f'  {row[0]}: {row[1]}')

cursor.execute('SELECT theme_name, COUNT(*) FROM portfolio GROUP BY theme_name ORDER BY COUNT(*) DESC')
print('\n主题分布:')
for row in cursor.fetchall():
    print(f'  {row[0]}: {row[1]}')

cursor.execute('SELECT name, ts_code, theme_name, layer FROM portfolio WHERE layer="leader"')
print('\n龙头股票:')
for row in cursor.fetchall():
    print(f'  {row[0]} ({row[1]}) - {row[2]}')

conn.close()