# -*- coding: utf-8 -*-
"""
UDC (User-Defined Cache) - RIB 引擎自定义缓存层

特性：
  - SQLite 持久化（WAL 模式，线程安全）
  - 支持多种缓存类型：价格、指标、财务、市场
  - 自动过期清理
  - 分层缓存：内存 LRU + SQLite 持久化
  - 统一读写接口
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .config import CACHE_DB_PATH

# ──────────────────────────────────────────────
# 建表 SQL
# ──────────────────────────────────────────────
_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS udc_price_cache (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    ttl INTEGER NOT NULL DEFAULT 86400,
    PRIMARY KEY (ts_code, trade_date)
);

CREATE TABLE IF NOT EXISTS udc_indicator_cache (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    ttl INTEGER NOT NULL DEFAULT 86400,
    PRIMARY KEY (ts_code, trade_date)
);

CREATE TABLE IF NOT EXISTS udc_impulse_cache (
    ts_code TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    ttl INTEGER NOT NULL DEFAULT 86400,
    PRIMARY KEY (ts_code)
);

CREATE TABLE IF NOT EXISTS udc_downtrend_cache (
    ts_code TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    ttl INTEGER NOT NULL DEFAULT 86400,
    PRIMARY KEY (ts_code)
);

CREATE TABLE IF NOT EXISTS udc_post_base_cache (
    ts_code TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    ttl INTEGER NOT NULL DEFAULT 86400,
    PRIMARY KEY (ts_code)
);

CREATE TABLE IF NOT EXISTS udc_breakout_cache (
    ts_code TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    ttl INTEGER NOT NULL DEFAULT 86400,
    PRIMARY KEY (ts_code)
);

CREATE TABLE IF NOT EXISTS udc_pullback_cache (
    ts_code TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    ttl INTEGER NOT NULL DEFAULT 86400,
    PRIMARY KEY (ts_code)
);

CREATE TABLE IF NOT EXISTS udc_market_cache (
    cache_key TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    ttl INTEGER NOT NULL DEFAULT 3600
);

CREATE TABLE IF NOT EXISTS udc_theme_cache (
    cache_key TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    ttl INTEGER NOT NULL DEFAULT 3600
);

CREATE TABLE IF NOT EXISTS udc_risk_cache (
    ts_code TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    ttl INTEGER NOT NULL DEFAULT 86400
);

CREATE TABLE IF NOT EXISTS udc_result_cache (
    date TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    ttl INTEGER NOT NULL DEFAULT 604800,
    PRIMARY KEY (date)
);

CREATE TABLE IF NOT EXISTS udc_backtest_cache (
    cache_key TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    ttl INTEGER NOT NULL DEFAULT 2592000
);
"""


class LRUCache:
    """内存 LRU 缓存（用于热点数据加速）"""

    def __init__(self, max_size: int = 256):
        self._cache: OrderedDict[str, tuple] = OrderedDict()
        self._max_size = max_size
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key in self._cache:
                value, expire_ts = self._cache[key]
                if time.time() < expire_ts:
                    self._cache.move_to_end(key)
                    self._hits += 1
                    return value
                else:
                    del self._cache[key]
            self._misses += 1
            return None

    def set(self, key: str, value: Any, ttl_seconds: int = 300):
        with self._lock:
            if key in self._cache:
                del self._cache[key]
            elif len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
            self._cache[key] = (value, time.time() + ttl_seconds)

    def clear(self):
        with self._lock:
            self._cache.clear()

    @property
    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / total if total > 0 else 0
            return {"hits": self._hits, "misses": self._misses, "hit_rate": hit_rate}


class UDCache:
    """UDC 缓存层：内存 LRU + SQLite 持久化。"""

    def __init__(self, db_path: str = CACHE_DB_PATH):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._mem = LRUCache(max_size=512)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            db_dir = os.path.dirname(self._db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            self._conn = sqlite3.connect(
                self._db_path, check_same_thread=False, timeout=30
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript(_CREATE_TABLES_SQL)
        conn.commit()

    def _is_expired(self, created_at: str, ttl_seconds: int) -> bool:
        try:
            ctime = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
            return (datetime.now() - ctime).total_seconds() > ttl_seconds
        except (ValueError, TypeError):
            return True

    def _now_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ─── 通用读写 ────────────────────────────
    def get(self, table: str, key_col: str, key_val: str,
            ttl_seconds: Optional[int] = None) -> Optional[Any]:
        """从缓存读取。先查内存 LRU，再查 SQLite。"""
        mem_key = f"{table}:{key_col}={key_val}"
        cached = self._mem.get(mem_key)
        if cached is not None:
            return cached

        try:
            with self._lock:
                conn = self._get_conn()
                row = conn.execute(
                    f"SELECT data_json, created_at, ttl FROM {table} WHERE {key_col} = ?",
                    (key_val,)
                ).fetchone()
                if row is None:
                    return None
                created_at = row["created_at"]
                ttl = row["ttl"] if ttl_seconds is None else ttl_seconds
                if self._is_expired(created_at, ttl):
                    conn.execute(f"DELETE FROM {table} WHERE {key_col} = ?", (key_val,))
                    conn.commit()
                    return None
                data = json.loads(row["data_json"])
                self._mem.set(mem_key, data, min(ttl, 300))
                return data
        except Exception:
            return None

    def set(self, table: str, key_col: str, key_val: str,
            data: Any, ttl_seconds: int = 86400) -> None:
        """写入缓存。同时写入内存 LRU 和 SQLite。"""
        if data is None:
            return
        mem_key = f"{table}:{key_col}={key_val}"
        self._mem.set(mem_key, data, min(ttl_seconds, 300))
        try:
            data_json = json.dumps(data, ensure_ascii=False, default=str)
            with self._lock:
                conn = self._get_conn()
                conn.execute(
                    f"INSERT OR REPLACE INTO {table} "
                    f"({key_col}, data_json, created_at, ttl) VALUES (?, ?, ?, ?)",
                    (key_val, data_json, self._now_str(), ttl_seconds)
                )
                conn.commit()
        except Exception:
            pass

    def get_compound(self, table: str, key_cols: List[str], key_vals: List[str],
                     ttl_seconds: Optional[int] = None) -> Optional[Any]:
        """复合主键读取。"""
        mem_key = f"{table}:" + ",".join(f"{c}={v}" for c, v in zip(key_cols, key_vals))
        cached = self._mem.get(mem_key)
        if cached is not None:
            return cached

        try:
            where = " AND ".join(f"{c} = ?" for c in key_cols)
            with self._lock:
                conn = self._get_conn()
                row = conn.execute(
                    f"SELECT data_json, created_at, ttl FROM {table} WHERE {where}",
                    key_vals
                ).fetchone()
                if row is None:
                    return None
                ttl = row["ttl"] if ttl_seconds is None else ttl_seconds
                if self._is_expired(row["created_at"], ttl):
                    conn.execute(f"DELETE FROM {table} WHERE {where}", key_vals)
                    conn.commit()
                    return None
                data = json.loads(row["data_json"])
                self._mem.set(mem_key, data, min(ttl, 300))
                return data
        except Exception:
            return None

    def set_compound(self, table: str, key_cols: List[str], key_vals: List[str],
                     data: Any, ttl_seconds: int = 86400) -> None:
        """复合主键写入。"""
        if data is None:
            return
        mem_key = f"{table}:" + ",".join(f"{c}={v}" for c, v in zip(key_cols, key_vals))
        self._mem.set(mem_key, data, min(ttl_seconds, 300))
        try:
            data_json = json.dumps(data, ensure_ascii=False, default=str)
            cols = ", ".join(key_cols + ["data_json", "created_at", "ttl"])
            placeholders = ", ".join(["?"] * (len(key_cols) + 3))
            conflict_cols = ", ".join(key_cols)
            sql = (
                f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
                f"ON CONFLICT({conflict_cols}) DO UPDATE SET "
                f"data_json = excluded.data_json, "
                f"created_at = excluded.created_at, "
                f"ttl = excluded.ttl"
            )
            with self._lock:
                conn = self._get_conn()
                conn.execute(sql, key_vals + [data_json, self._now_str(), ttl_seconds])
                conn.commit()
        except Exception:
            pass

    # ─── 便捷方法 ─────────────────────────────
    def get_price(self, ts_code: str, trade_date: str) -> Optional[List[Dict]]:
        return self.get_compound(
            "udc_price_cache", ["ts_code", "trade_date"], [ts_code, trade_date]
        )

    def set_price(self, ts_code: str, trade_date: str, data: List[Dict]) -> None:
        self.set_compound(
            "udc_price_cache", ["ts_code", "trade_date"], [ts_code, trade_date], data
        )

    def get_indicator(self, ts_code: str, trade_date: str) -> Optional[Dict]:
        return self.get_compound(
            "udc_indicator_cache", ["ts_code", "trade_date"], [ts_code, trade_date]
        )

    def set_indicator(self, ts_code: str, trade_date: str, data: Dict) -> None:
        self.set_compound(
            "udc_indicator_cache", ["ts_code", "trade_date"], [ts_code, trade_date], data
        )

    def get_impulse(self, ts_code: str) -> Optional[Dict]:
        return self.get("udc_impulse_cache", "ts_code", ts_code)

    def set_impulse(self, ts_code: str, data: Dict) -> None:
        self.set("udc_impulse_cache", "ts_code", ts_code, data)

    def get_downtrend(self, ts_code: str) -> Optional[Dict]:
        return self.get("udc_downtrend_cache", "ts_code", ts_code)

    def set_downtrend(self, ts_code: str, data: Dict) -> None:
        self.set("udc_downtrend_cache", "ts_code", ts_code, data)

    def get_post_base(self, ts_code: str) -> Optional[Dict]:
        return self.get("udc_post_base_cache", "ts_code", ts_code)

    def set_post_base(self, ts_code: str, data: Dict) -> None:
        self.set("udc_post_base_cache", "ts_code", ts_code, data)

    def get_breakout(self, ts_code: str) -> Optional[Dict]:
        return self.get("udc_breakout_cache", "ts_code", ts_code)

    def set_breakout(self, ts_code: str, data: Dict) -> None:
        self.set("udc_breakout_cache", "ts_code", ts_code, data)

    def get_pullback(self, ts_code: str) -> Optional[Dict]:
        return self.get("udc_pullback_cache", "ts_code", ts_code)

    def set_pullback(self, ts_code: str, data: Dict) -> None:
        self.set("udc_pullback_cache", "ts_code", ts_code, data)

    def get_market(self, key: str = "market_data") -> Optional[Dict]:
        return self.get("udc_market_cache", "cache_key", key, ttl_seconds=3600)

    def set_market(self, data: Dict, key: str = "market_data") -> None:
        self.set("udc_market_cache", "cache_key", key, data, ttl_seconds=3600)

    def get_theme(self, key: str) -> Optional[Dict]:
        return self.get("udc_theme_cache", "cache_key", key, ttl_seconds=3600)

    def set_theme(self, key: str, data: Dict) -> None:
        self.set("udc_theme_cache", "cache_key", key, data, ttl_seconds=3600)

    def get_risk(self, ts_code: str) -> Optional[Dict]:
        return self.get("udc_risk_cache", "ts_code", ts_code)

    def set_risk(self, ts_code: str, data: Dict) -> None:
        self.set("udc_risk_cache", "ts_code", ts_code, data)

    def get_result(self, date: str) -> Optional[List[Dict]]:
        return self.get("udc_result_cache", "date", date)

    def set_result(self, date: str, data: List[Dict]) -> None:
        self.set("udc_result_cache", "date", date, data, ttl_seconds=604800)

    # ─── 清理 ────────────────────────────────
    def clear_expired(self) -> int:
        """清除所有过期缓存。"""
        tables = [
            "udc_price_cache", "udc_indicator_cache",
            "udc_impulse_cache", "udc_downtrend_cache",
            "udc_post_base_cache", "udc_breakout_cache",
            "udc_pullback_cache", "udc_market_cache",
            "udc_theme_cache", "udc_risk_cache",
            "udc_result_cache", "udc_backtest_cache",
        ]
        count = 0
        cutoff = datetime.now() - timedelta(seconds=86400 * 7)
        with self._lock:
            conn = self._get_conn()
            for table in tables:
                try:
                    cur = conn.execute(
                        f"DELETE FROM {table} WHERE created_at < ?",
                        (cutoff.strftime("%Y-%m-%d %H:%M:%S"),)
                    )
                    count += cur.rowcount
                except Exception:
                    pass
            conn.commit()
        self._mem.clear()
        return count

    def close(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
