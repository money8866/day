# -*- coding: utf-8 -*-
import sqlite3
conn = sqlite3.connect(r'C:\Users\kongx\mystock\hot_sector.db')
c = conn.cursor()
c.execute("SELECT name FROM sqlite_master")
print('All objects:', c.fetchall())
conn.close()
