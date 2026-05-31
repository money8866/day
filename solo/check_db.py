#!/usr/bin/env python
# -*- coding: utf-8 -*-
import sqlite3
import os

db_path = "cache_backbone_tushare/theme_analysis.db"
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print(f"数据库中的表：{len(tables)} 个")
    for table in tables:
        print(f"  - {table[0]}")
        cursor.execute(f"SELECT COUNT(*) FROM {table[0]}")
        count = cursor.fetchone()[0]
        print(f"    记录数: {count}")
    conn.close()
else:
    print("数据库文件不存在")
