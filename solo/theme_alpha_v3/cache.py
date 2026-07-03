#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V3.0 - 缓存模块
"""
import os
import time
import sqlite3
import json
import hashlib
import pandas as pd
import config

CACHE_DB = os.path.join(config.CACHE_DIR, "cache.db")

def init_db():
    """初始化缓存数据库"""
    conn = sqlite3.connect(CACHE_DB)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cache_data (
            key TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            data_type TEXT,
            expire_time INTEGER,
            created_at INTEGER
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_hash(key):
    """生成键的hash值"""
    return hashlib.md5(str(key).encode()).hexdigest()

def cache_get(key, max_age=3600*24):
    """获取缓存数据"""
    conn = sqlite3.connect(CACHE_DB)
    cursor = conn.cursor()
    key_hash = get_hash(key)
    cursor.execute(
        "SELECT data, data_type, expire_time FROM cache_data WHERE key = ?",
        (key_hash,)
    )
    row = cursor.fetchone()
    conn.close()
    
    if row:
        data_str, data_type, expire_time = row
        if expire_time is None or expire_time > int(time.time()):
            try:
                if data_type == "dataframe":
                    df_data = json.loads(data_str)
                    return pd.DataFrame.from_dict(df_data)
                return json.loads(data_str)
            except:
                pass
    return None

def cache_set(key, data, max_age=3600*24, data_type="json"):
    """设置缓存数据"""
    conn = sqlite3.connect(CACHE_DB)
    cursor = conn.cursor()
    key_hash = get_hash(key)
    
    data_str = ""
    if isinstance(data, pd.DataFrame):
        data_str = data.to_json(orient="split")
        data_type = "dataframe"
    else:
        data_str = json.dumps(data)
    
    expire_time = int(time.time()) + max_age if max_age else None
    
    cursor.execute('''
        INSERT OR REPLACE INTO cache_data (key, data, data_type, expire_time, created_at)
        VALUES (?, ?, ?, ?, ?)
    ''', (key_hash, data_str, data_type, expire_time, int(time.time())))
    conn.commit()
    conn.close()

def cache_df(key, df, max_age=3600*24):
    """缓存DataFrame到本地文件"""
    file_path = os.path.join(config.DAILY_CACHE, f"{key}.parquet")
    df.to_parquet(file_path)
    return file_path

def load_df_cache(key):
    """从本地文件加载缓存的DataFrame"""
    file_path = os.path.join(config.DAILY_CACHE, f"{key}.parquet")
    if os.path.exists(file_path):
        return pd.read_parquet(file_path)
    return None

print("[Cache] 缓存模块加载完成")
