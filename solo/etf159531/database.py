"""SQLite 存储层：行情、日历、运行日志、每日信号、超卖/危机事件。

设计要点
- 行情唯一键 (ts_code, trade_date)，增量写入用 UPSERT，天然去重。
- signals 以 trade_date 为主键，重复运行同一交易日只会覆盖、不会重复。
- 超卖/危机事件每次全量重算并覆盖，保证「同一交易日重复执行不改变事件状态」。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS etf_daily (
    ts_code     TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    pre_close   REAL,
    pct_chg     REAL,
    vol         REAL,
    amount      REAL,
    adj_factor  REAL,
    close_adj   REAL,
    PRIMARY KEY (ts_code, trade_date)
);

CREATE TABLE IF NOT EXISTS index_daily (
    ts_code     TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    pre_close   REAL,
    pct_chg     REAL,
    vol         REAL,
    amount      REAL,
    PRIMARY KEY (ts_code, trade_date)
);

CREATE TABLE IF NOT EXISTS trade_calendar (
    cal_date       TEXT PRIMARY KEY,
    is_open        INTEGER NOT NULL,
    pretrade_date  TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at       TEXT NOT NULL,
    mode         TEXT NOT NULL,
    target_date  TEXT,
    data_date    TEXT,
    status       TEXT NOT NULL,
    version      TEXT,
    message      TEXT
);

CREATE TABLE IF NOT EXISTS signals (
    trade_date    TEXT PRIMARY KEY,
    generated_at  TEXT NOT NULL,
    executable    INTEGER NOT NULL,
    final_target  REAL,
    payload       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oversold_events (
    event_id        TEXT PRIMARY KEY,
    trigger_date    TEXT,
    trigger_price   REAL,
    lowest_close    REAL,
    state           TEXT,
    tactical_weight REAL,
    activate_date   TEXT,
    exit_date       TEXT,
    exit_reason     TEXT
);

CREATE TABLE IF NOT EXISTS crisis_events (
    event_id       TEXT PRIMARY KEY,
    trigger_date   TEXT,
    release_date   TEXT,
    probe_date     TEXT,
    probe_fail_date TEXT,
    status         TEXT
);

CREATE INDEX IF NOT EXISTS idx_etf_date ON etf_daily(trade_date);
CREATE INDEX IF NOT EXISTS idx_index_date ON index_daily(trade_date);
"""

ETF_COLS = [
    "ts_code", "trade_date", "open", "high", "low", "close", "pre_close",
    "pct_chg", "vol", "amount", "adj_factor", "close_adj",
]
INDEX_COLS = [
    "ts_code", "trade_date", "open", "high", "low", "close", "pre_close",
    "pct_chg", "vol", "amount",
]


def _to_python(v):
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return None if (np.isnan(f) or np.isinf(f)) else f
    if isinstance(v, float):
        return None if (np.isnan(v) or np.isinf(v)) else v
    if isinstance(v, (np.bool_,)):
        return int(v)
    return v


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.init_schema()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------------ 行情
    def _upsert(self, table: str, df: pd.DataFrame, cols: list[str], key: list[str]) -> int:
        if df is None or df.empty:
            return 0
        rows = [tuple(_to_python(r[c]) for c in cols) for _, r in df.iterrows()]
        placeholders = ",".join(["?"] * len(cols))
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in key)
        sql = (
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT({','.join(key)}) DO UPDATE SET {updates}"
        )
        self.conn.executemany(sql, rows)
        self.conn.commit()
        return len(rows)

    def upsert_etf_daily(self, df: pd.DataFrame) -> int:
        return self._upsert("etf_daily", df, ETF_COLS, ["ts_code", "trade_date"])

    def upsert_index_daily(self, df: pd.DataFrame) -> int:
        return self._upsert("index_daily", df, INDEX_COLS, ["ts_code", "trade_date"])

    def upsert_calendar(self, df: pd.DataFrame) -> int:
        if df is None or df.empty:
            return 0
        d = df.copy()
        d["cal_date"] = d["cal_date"].astype(str)
        d["is_open"] = d["is_open"].astype(int)
        if "pretrade_date" not in d.columns:
            d["pretrade_date"] = None
        rows = [
            (_to_python(r["cal_date"]), int(r["is_open"]), _to_python(r.get("pretrade_date")))
            for _, r in d.iterrows()
        ]
        self.conn.executemany(
            "INSERT INTO trade_calendar (cal_date,is_open,pretrade_date) VALUES (?,?,?) "
            "ON CONFLICT(cal_date) DO UPDATE SET is_open=excluded.is_open,"
            "pretrade_date=excluded.pretrade_date",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def load_etf_daily(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None
    ) -> pd.DataFrame:
        sql = "SELECT * FROM etf_daily WHERE 1=1"
        params: list = []
        if start_date:
            sql += " AND trade_date>=?"
            params.append(str(start_date))
        if end_date:
            sql += " AND trade_date<=?"
            params.append(str(end_date))
        sql += " ORDER BY trade_date ASC"
        df = pd.read_sql_query(sql, self.conn, params=params)
        return self._normalize_dates(df)

    def load_index_daily(
        self, start_date: Optional[str] = None, end_date: Optional[str] = None
    ) -> pd.DataFrame:
        sql = "SELECT * FROM index_daily WHERE 1=1"
        params: list = []
        if start_date:
            sql += " AND trade_date>=?"
            params.append(str(start_date))
        if end_date:
            sql += " AND trade_date<=?"
            params.append(str(end_date))
        sql += " ORDER BY trade_date ASC"
        df = pd.read_sql_query(sql, self.conn, params=params)
        return self._normalize_dates(df)

    @staticmethod
    def _normalize_dates(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        for c in ("trade_date", "cal_date", "pretrade_date"):
            if c in df.columns:
                df[c] = df[c].astype(str)
        return df

    def latest_etf_date(self) -> Optional[str]:
        row = self.conn.execute("SELECT MAX(trade_date) AS d FROM etf_daily").fetchone()
        return row["d"] if row and row["d"] else None

    def latest_index_date(self) -> Optional[str]:
        row = self.conn.execute("SELECT MAX(trade_date) AS d FROM index_daily").fetchone()
        return row["d"] if row and row["d"] else None

    def calendar(self, start_date: Optional[str] = None,
                 end_date: Optional[str] = None) -> pd.DataFrame:
        sql = "SELECT cal_date,is_open,pretrade_date FROM trade_calendar WHERE 1=1"
        params: list = []
        if start_date:
            sql += " AND cal_date>=?"
            params.append(str(start_date))
        if end_date:
            sql += " AND cal_date<=?"
            params.append(str(end_date))
        sql += " ORDER BY cal_date ASC"
        return pd.read_sql_query(sql, self.conn, params=params)

    # ------------------------------------------------------------------ 运行
    def save_run(self, run_at: str, mode: str, target_date: Optional[str],
                 data_date: Optional[str], status: str, version: str,
                 message: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (run_at,mode,target_date,data_date,status,version,message) "
            "VALUES (?,?,?,?,?,?,?)",
            (run_at, mode, target_date, data_date, status, version, message),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def list_runs(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM runs ORDER BY run_id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ 信号
    def upsert_signal(self, trade_date: str, generated_at: str, executable: bool,
                      final_target: Optional[float], payload: dict) -> None:
        self.conn.execute(
            "INSERT INTO signals (trade_date,generated_at,executable,final_target,payload) "
            "VALUES (?,?,?,?,?) ON CONFLICT(trade_date) DO UPDATE SET "
            "generated_at=excluded.generated_at, executable=excluded.executable, "
            "final_target=excluded.final_target, payload=excluded.payload",
            (str(trade_date), generated_at, int(bool(executable)),
             _to_python(final_target),
             json.dumps(payload, ensure_ascii=False, allow_nan=False, default=str)),
        )
        self.conn.commit()

    def load_signal(self, trade_date: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM signals WHERE trade_date=?", (str(trade_date),)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        return d

    def latest_signal_date(self) -> Optional[str]:
        row = self.conn.execute("SELECT MAX(trade_date) AS d FROM signals").fetchone()
        return row["d"] if row and row["d"] else None

    def list_signals(self, start: Optional[str] = None,
                     end: Optional[str] = None) -> pd.DataFrame:
        sql = "SELECT trade_date,generated_at,executable,final_target FROM signals WHERE 1=1"
        params: list = []
        if start:
            sql += " AND trade_date>=?"
            params.append(str(start))
        if end:
            sql += " AND trade_date<=?"
            params.append(str(end))
        sql += " ORDER BY trade_date ASC"
        return pd.read_sql_query(sql, self.conn, params=params)

    # ------------------------------------------------------------------ 事件
    def replace_oversold_events(self, events: Iterable[dict]) -> int:
        rows = list(events)
        self.conn.execute("DELETE FROM oversold_events")
        self.conn.executemany(
            "INSERT INTO oversold_events (event_id,trigger_date,trigger_price,lowest_close,"
            "state,tactical_weight,activate_date,exit_date,exit_reason) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    e.get("event_id"), e.get("trigger_date"), _to_python(e.get("trigger_price")),
                    _to_python(e.get("lowest_close")), e.get("state"),
                    _to_python(e.get("tactical_weight")), e.get("activate_date"),
                    e.get("exit_date"), e.get("exit_reason"),
                )
                for e in rows
            ],
        )
        self.conn.commit()
        return len(rows)

    def replace_crisis_events(self, events: Iterable[dict]) -> int:
        rows = list(events)
        self.conn.execute("DELETE FROM crisis_events")
        self.conn.executemany(
            "INSERT INTO crisis_events (event_id,trigger_date,release_date,probe_date,"
            "probe_fail_date,status) VALUES (?,?,?,?,?,?)",
            [
                (
                    e.get("event_id"), e.get("trigger_date"), e.get("release_date"),
                    e.get("probe_date"), e.get("probe_fail_date"), e.get("status"),
                )
                for e in rows
            ],
        )
        self.conn.commit()
        return len(rows)

    def load_oversold_events(self) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT * FROM oversold_events ORDER BY trigger_date ASC", self.conn
        )

    def load_crisis_events(self) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT * FROM crisis_events ORDER BY trigger_date ASC", self.conn
        )
