"""查 宗申动力(001696) / 万丰奥威(002085) 的东财行业+概念板块"""
import json, sqlite3, os

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache_backbone_tushare", "cache.db")
print(f"DB: {DB}")
print(f"exists: {os.path.exists(DB)}")
