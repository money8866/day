"""
ELD V2 缓存层

SQLite + CSV 双重缓存，支持自动过期、线程安全。
"""

from __future__ import annotations

import csv
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .config import CacheConfig
from .constants import CACHE_DEFAULT_EXPIRE_HOURS
from .utils import eld_json_dumps, safe_float


# ──────────────────────────────────────────────
# 缓存表结构定义
# ──────────────────────────────────────────────

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS forecast_cache (
    cache_date TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS financial_cache (
    ts_code TEXT NOT NULL,
    end_date TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (ts_code, end_date)
);

CREATE TABLE IF NOT EXISTS price_cache (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (ts_code, trade_date)
);

CREATE TABLE IF NOT EXISTS price_batch_cache (
    ts_code TEXT NOT NULL,
    date_range TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (ts_code, date_range)
);

CREATE TABLE IF NOT EXISTS daily_basic_cache (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (ts_code, trade_date)
);

CREATE TABLE IF NOT EXISTS moneyflow_cache (
    ts_code TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (ts_code, trade_date)
);

CREATE TABLE IF NOT EXISTS cyq_cache (
    ts_code TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS market_cache (
    cache_key TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS result_cache (
    date TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS stock_basic_cache (
    ts_code TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS industry_cache (
    cache_key TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS announcement_cache (
    ts_code TEXT NOT NULL,
    announce_date TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (ts_code, announce_date)
);
"""


# ──────────────────────────────────────────────
# EldCache
# ──────────────────────────────────────────────


class EldCache:
    """ELD 系统缓存层。

    特性：
      - SQLite 持久化（WAL 模式，线程安全）
      - 可选 CSV 导出
      - 自动过期清理
      - 统一读写接口
    """

    def __init__(self, config: CacheConfig) -> None:
        """初始化缓存。

        Args:
            config: 缓存配置。
        """
        self.config = config
        self._lock = threading.Lock()
        self._logger: Optional[Any] = None

        # 创建缓存目录
        sqlite_dir = os.path.abspath(config.sqlite_cache_dir)
        csv_dir = os.path.abspath(config.csv_cache_dir)
        os.makedirs(sqlite_dir, exist_ok=True)
        os.makedirs(csv_dir, exist_ok=True)

        # SQLite 连接
        db_path = os.path.join(sqlite_dir, config.sqlite_db)
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

        if config.sqlite_enabled:
            self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """获取线程安全的数据库连接。"""
        if self._conn is None:
            self._conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                timeout=30,
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        """初始化数据库表结构。"""
        conn = self._get_conn()
        conn.executescript(_CREATE_TABLES_SQL)
        conn.commit()

    def _now_str(self) -> str:
        """获取当前本地时间字符串。"""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _is_expired(self, created_at: str, expire_hours: int) -> bool:
        """检查缓存是否过期。"""
        try:
            ctime = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
            return (datetime.now() - ctime) > timedelta(hours=expire_hours)
        except (ValueError, TypeError):
            return True

    def _log(self, msg: str, level: str = "DEBUG") -> None:
        """内部日志。"""
        if self._logger is None:
            import logging

            self._logger = logging.getLogger("eld.cache")
        getattr(self._logger, level.lower(), self._logger.debug)(msg)

    # ─── 通用 SQLite 读写 ────────────────────

    def _get_cached(
        self, table: str, key_col: str, key_val: str, expire_hours: Optional[int] = None
    ) -> Optional[list[dict[str, Any]]]:
        """从 SQLite 读取缓存。"""
        if not self.config.sqlite_enabled:
            return None
        expire = expire_hours if expire_hours is not None else self.config.expire_hours
        try:
            with self._lock:
                conn = self._get_conn()
                cursor = conn.execute(
                    f"SELECT data_json, created_at FROM {table} WHERE {key_col} = ?",
                    (key_val,),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                created_at = row["created_at"]
                if self._is_expired(created_at, expire):
                    conn.execute(
                        f"DELETE FROM {table} WHERE {key_col} = ?", (key_val,)
                    )
                    conn.commit()
                    return None
                return json.loads(row["data_json"])
        except Exception as exc:
            self._log(f"缓存读取失败 [{table}.{key_col}={key_val}]: {exc}", "WARNING")
            return None

    def _set_cached(
        self, table: str, key_col: str, key_val: str, data: Any
    ) -> None:
        """写入 SQLite 缓存。"""
        if not self.config.sqlite_enabled:
            return
        try:
            data_json = eld_json_dumps(data) if not isinstance(data, str) else data
            with self._lock:
                conn = self._get_conn()
                conn.execute(
                    f"INSERT OR REPLACE INTO {table} ({key_col}, data_json, created_at) VALUES (?, ?, ?)",
                    (key_val, data_json, self._now_str()),
                )
                conn.commit()
        except Exception as exc:
            self._log(f"缓存写入失败 [{table}.{key_col}={key_val}]: {exc}", "WARNING")

    def _get_compound_cached(
        self, table: str, key_cols: list[str], key_vals: list[str],
        expire_hours: Optional[int] = None,
    ) -> Optional[list[dict[str, Any]]]:
        """复合主键读取。"""
        if not self.config.sqlite_enabled:
            return None
        expire = expire_hours if expire_hours is not None else self.config.expire_hours
        where_clause = " AND ".join(f"{c} = ?" for c in key_cols)
        try:
            with self._lock:
                conn = self._get_conn()
                cursor = conn.execute(
                    f"SELECT data_json, created_at FROM {table} WHERE {where_clause}",
                    key_vals,
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                created_at = row["created_at"]
                if self._is_expired(created_at, expire):
                    conn.execute(
                        f"DELETE FROM {table} WHERE {where_clause}", key_vals
                    )
                    conn.commit()
                    return None
                return json.loads(row["data_json"])
        except Exception as exc:
            self._log(f"缓存读取失败 [{table}]: {exc}", "WARNING")
            return None

    def _set_compound_cached(
        self, table: str, key_cols: list[str], key_vals: list[str], data: Any
    ) -> None:
        """复合主键写入。"""
        if not self.config.sqlite_enabled:
            return
        cols = ", ".join(key_cols + ["data_json", "created_at"])
        placeholders = ", ".join(["?"] * (len(key_cols) + 2))
        conflict_cols = ", ".join(key_cols)
        update_set = "data_json = excluded.data_json, created_at = excluded.created_at"
        sql = (
            f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT({conflict_cols}) DO UPDATE SET {update_set}"
        )
        try:
            data_json = eld_json_dumps(data) if not isinstance(data, str) else data
            with self._lock:
                conn = self._get_conn()
                conn.execute(sql, key_vals + [data_json, self._now_str()])
                conn.commit()
        except Exception as exc:
            self._log(f"缓存写入失败 [{table}]: {exc}", "WARNING")

    # ─── CSV 读写（辅助） ────────────────────

    def _csv_path(self, sub_dir: str, filename: str) -> str:
        """获取 CSV 文件路径。"""
        return os.path.join(self.config.csv_cache_dir, sub_dir, filename)

    def _write_csv(self, filepath: str, data: list[dict[str, Any]]) -> None:
        """写入 CSV 文件。"""
        if not self.config.csv_enabled or not data:
            return
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        try:
            fieldnames = list(data[0].keys())
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(data)
        except Exception as exc:
            self._log(f"CSV写入失败 [{filepath}]: {exc}", "WARNING")

    def _read_csv(self, filepath: str) -> list[dict[str, str]]:
        """读取 CSV 文件。"""
        if not os.path.exists(filepath):
            return []
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return list(csv.DictReader(f))
        except Exception as exc:
            self._log(f"CSV读取失败 [{filepath}]: {exc}", "WARNING")
            return []

    # ─── 专用缓存接口 ────────────────────────

    # --- 业绩预告 ---
    def get_forecast_cache(self) -> Optional[list[dict[str, Any]]]:
        """获取缓存的业绩预告数据。"""
        key = self._get_cache_date_key()
        return self._get_cached("forecast_cache", "cache_date", key)

    def set_forecast_cache(self, forecast_data: list[dict[str, Any]]) -> None:
        """缓存业绩预告数据。"""
        key = self._get_cache_date_key()
        self._set_cached("forecast_cache", "cache_date", key, forecast_data)

    # --- 财务数据 ---
    def get_financial_cache(self, ts_code: str) -> Optional[list[dict[str, Any]]]:
        """获取缓存的财务数据。"""
        return self._get_compound_cached(
            "financial_cache", ["ts_code", "end_date"],
            [ts_code, self._get_latest_end_date()],
        )

    def set_financial_cache(
        self, ts_code: str, data: list[dict[str, Any]]
    ) -> None:
        """缓存财务数据。"""
        self._set_compound_cached(
            "financial_cache", ["ts_code", "end_date"],
            [ts_code, self._get_latest_end_date()], data,
        )

    # --- 日线价格 ---
    def get_price_cache(self, ts_code: str) -> Optional[list[dict[str, Any]]]:
        """获取缓存的日线价格数据。"""
        return self._get_cached("price_batch_cache", "ts_code", ts_code)

    def set_price_cache(self, ts_code: str, data: list[dict[str, Any]]) -> None:
        """缓存日线价格数据。"""
        self._set_cached("price_batch_cache", "ts_code", ts_code, data)

    # --- 资金流向 ---
    def get_moneyflow_cache(self, ts_code: str) -> Optional[list[dict[str, Any]]]:
        """获取缓存的资金流向数据。"""
        return self._get_cached("moneyflow_cache", "ts_code", ts_code)

    def set_moneyflow_cache(
        self, ts_code: str, data: list[dict[str, Any]]
    ) -> None:
        """缓存资金流向数据。"""
        self._set_cached("moneyflow_cache", "ts_code", ts_code, data)

    # --- 筹码分布 ---
    def get_cyq_cache(self, ts_code: str) -> Optional[list[dict[str, Any]]]:
        """获取缓存的筹码分布数据。"""
        return self._get_cached("cyq_cache", "ts_code", ts_code)

    def set_cyq_cache(self, ts_code: str, data: list[dict[str, Any]]) -> None:
        """缓存筹码分布数据。"""
        self._set_cached("cyq_cache", "ts_code", ts_code, data)

    # --- 市场数据 ---
    def get_market_cache(self) -> Optional[list[dict[str, Any]]]:
        """获取缓存的市场数据。"""
        return self._get_cached("market_cache", "cache_key", "market_data")

    def set_market_cache(self, data: list[dict[str, Any]]) -> None:
        """缓存市场数据。"""
        self._set_cached("market_cache", "cache_key", "market_data", data)

    # --- 评分结果 ---
    def get_result_cache(self, date: str) -> Optional[list[dict[str, Any]]]:
        """获取缓存的评分结果。"""
        return self._get_cached("result_cache", "date", date)

    def set_result_cache(
        self, date: str, results: list[dict[str, Any]]
    ) -> None:
        """缓存评分结果。"""
        self._set_cached("result_cache", "date", date, results)

    # --- 股票基本信息 ---
    def get_stock_basic_cache(self, ts_code: str) -> Optional[list[dict[str, Any]]]:
        """获取缓存的股票基本信息。"""
        return self._get_cached("stock_basic_cache", "ts_code", ts_code)

    def set_stock_basic_cache(
        self, ts_code: str, data: list[dict[str, Any]]
    ) -> None:
        """缓存股票基本信息。"""
        self._set_cached("stock_basic_cache", "ts_code", ts_code, data)

    # --- 行业数据 ---
    def get_industry_cache(self) -> Optional[list[dict[str, Any]]]:
        """获取缓存的行业数据。"""
        return self._get_cached("industry_cache", "cache_key", "industry_data")

    def set_industry_cache(self, data: list[dict[str, Any]]) -> None:
        """缓存行业数据。"""
        self._set_cached("industry_cache", "cache_key", "industry_data", data)

    # --- 公告数据 ---
    def get_announcement_cache(
        self, ts_code: str, announce_date: str
    ) -> Optional[list[dict[str, Any]]]:
        """获取缓存的公告数据。"""
        return self._get_compound_cached(
            "announcement_cache", ["ts_code", "announce_date"],
            [ts_code, announce_date],
        )

    def set_announcement_cache(
        self, ts_code: str, announce_date: str, data: list[dict[str, Any]]
    ) -> None:
        """缓存公告数据。"""
        self._set_compound_cached(
            "announcement_cache", ["ts_code", "announce_date"],
            [ts_code, announce_date], data,
        )

    # ─── 过期清理 ────────────────────────────

    def clear_expired(self, expire_hours: Optional[int] = None) -> int:
        """清除所有过期的缓存记录。

        Args:
            expire_hours: 过期阈值（小时），默认使用 config.expire_hours。

        Returns:
            清除的记录数。
        """
        if not self.config.sqlite_enabled:
            return 0
        expire = expire_hours if expire_hours is not None else self.config.expire_hours
        cutoff = (datetime.now() - timedelta(hours=expire)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        tables = [
            "forecast_cache",
            "financial_cache",
            "price_cache",
            "price_batch_cache",
            "daily_basic_cache",
            "moneyflow_cache",
            "cyq_cache",
            "market_cache",
            "result_cache",
            "stock_basic_cache",
            "industry_cache",
            "announcement_cache",
        ]
        total_deleted = 0
        with self._lock:
            conn = self._get_conn()
            for table in tables:
                try:
                    cursor = conn.execute(
                        f"DELETE FROM {table} WHERE created_at < ?", (cutoff,)
                    )
                    total_deleted += cursor.rowcount
                except Exception:
                    pass
            conn.commit()
        if total_deleted > 0:
            self._log(f"缓存清理：清除 {total_deleted} 条过期记录 (>{expire}h)")
        return total_deleted

    def clear_all(self) -> None:
        """清空所有缓存。"""
        if not self.config.sqlite_enabled:
            return
        tables = [
            "forecast_cache",
            "financial_cache",
            "price_cache",
            "price_batch_cache",
            "daily_basic_cache",
            "moneyflow_cache",
            "cyq_cache",
            "market_cache",
            "result_cache",
            "stock_basic_cache",
            "industry_cache",
            "announcement_cache",
        ]
        with self._lock:
            conn = self._get_conn()
            for table in tables:
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    pass
            conn.commit()
        self._log("已清空所有缓存")

    # ─── 内部辅助 ────────────────────────────

    @staticmethod
    def _get_cache_date_key() -> str:
        """获取缓存日期键（用于按日期缓存的表）。"""
        return datetime.now().strftime("%Y%m%d")

    @staticmethod
    def _get_latest_end_date() -> str:
        """获取最新的财报截止日期（用于财务数据缓存键）。"""
        now = datetime.now()
        year = now.year
        month = now.month
        if month <= 4:
            return f"{year - 1}1231"
        elif month <= 8:
            return f"{year}0630"
        elif month <= 10:
            return f"{year}0930"
        else:
            return f"{year}1231"

    def close(self) -> None:
        """关闭数据库连接。"""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __enter__(self) -> "EldCache":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
