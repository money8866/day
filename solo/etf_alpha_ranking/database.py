#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Local Database (SQLite)
=======================
Schema + CRUD for all persisted data. SQLite is used (built-in, zero-install).

Tables:
  etf_basic         (ts_code, name, exchange, theme, industry)
  daily_price       (date, code, open, high, low, close, volume, amount)
  theme_mapping     (stock, theme)
  theme_features    (date, theme, theme_persistence, theme_rank, breadth,
                     leader_score, ...)
  etf_features      (date, ETF, all calculated features)  -- flexible JSON blob
  prediction_result (date, ETF, rank, score)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional

import pandas as pd

LOG = logging.getLogger("etf_alpha_ranking.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS etf_basic (
    ts_code   TEXT PRIMARY KEY,
    name      TEXT,
    exchange  TEXT,
    theme     TEXT,
    industry  TEXT,
    updated   TEXT
);

CREATE TABLE IF NOT EXISTS daily_price (
    date    TEXT,
    code    TEXT,
    open    REAL,
    high    REAL,
    low     REAL,
    close   REAL,
    volume  REAL,
    amount  REAL,
    PRIMARY KEY (date, code)
);
CREATE INDEX IF NOT EXISTS idx_daily_code ON daily_price(code);
CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_price(date);

CREATE TABLE IF NOT EXISTS theme_mapping (
    stock   TEXT,
    theme   TEXT,
    PRIMARY KEY (stock, theme)
);
CREATE INDEX IF NOT EXISTS idx_theme_theme ON theme_mapping(theme);

CREATE TABLE IF NOT EXISTS theme_features (
    date                TEXT,
    theme               TEXT,
    theme_persistence   REAL,
    theme_rank          INTEGER,
    breadth             REAL,
    leader_score        REAL,
    theme_state         TEXT,
    expected_duration   REAL,
    rotation_probability REAL,
    extra               TEXT,
    PRIMARY KEY (date, theme)
);
CREATE INDEX IF NOT EXISTS idx_tf_date ON theme_features(date);

CREATE TABLE IF NOT EXISTS etf_features (
    date      TEXT,
    etf       TEXT,
    features  TEXT,
    PRIMARY KEY (date, etf)
);
CREATE INDEX IF NOT EXISTS idx_ef_date ON etf_features(date);

CREATE TABLE IF NOT EXISTS prediction_result (
    date    TEXT,
    etf     TEXT,
    rank    INTEGER,
    score   REAL,
    PRIMARY KEY (date, etf)
);
CREATE INDEX IF NOT EXISTS idx_pr_date ON prediction_result(date);
"""


class Database:
    """Thin SQLite wrapper with pandas-friendly upserts."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_schema()

    # ------------------------------------------------------------------
    # Connection / schema
    # ------------------------------------------------------------------
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.path)
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
        return self._conn

    def _init_schema(self):
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # ETF basic
    # ------------------------------------------------------------------
    def upsert_etf_basic(self, rows: List[Dict[str, Any]]):
        if not rows:
            return
        sql = ("INSERT OR REPLACE INTO etf_basic "
               "(ts_code, name, exchange, theme, industry, updated) "
               "VALUES (:ts_code, :name, :exchange, :theme, :industry, :updated)")
        self.conn.executemany(sql, rows)
        self.conn.commit()

    def get_etf_basic(self) -> pd.DataFrame:
        return pd.read_sql("SELECT * FROM etf_basic", self.conn)

    # ------------------------------------------------------------------
    # Daily price
    # ------------------------------------------------------------------
    def upsert_daily_price(self, df: pd.DataFrame):
        if df is None or df.empty:
            return
        cols = ["trade_date", "ts_code", "open", "high", "low", "close", "vol", "amount"]
        for c in cols:
            if c not in df.columns:
                return
        sql = ("INSERT OR REPLACE INTO daily_price "
               "(date, code, open, high, low, close, volume, amount) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
        data = df[cols].rename(columns={"trade_date": "date", "ts_code": "code",
                                        "vol": "volume"})
        self.conn.executemany(sql, data.values.tolist())
        self.conn.commit()

    def get_daily_price(self, code: str, start_date: str = "",
                        end_date: str = "") -> pd.DataFrame:
        sql = "SELECT date,code,open,high,low,close,volume,amount FROM daily_price WHERE code=?"
        params: List[Any] = [code]
        if start_date:
            sql += " AND date>=?"
            params.append(start_date)
        if end_date:
            sql += " AND date<=?"
            params.append(end_date)
        sql += " ORDER BY date"
        df = pd.read_sql(sql, self.conn, params=params)
        if df.empty:
            return df
        df = df.rename(columns={"date": "trade_date", "code": "ts_code",
                                "volume": "vol"})
        df["pct_chg"] = (df["close"].pct_change() * 100.0).fillna(0.0)
        return df

    def has_date(self, date: str, table: str = "daily_price") -> bool:
        try:
            row = self.conn.execute(
                f"SELECT 1 FROM {table} WHERE date=? LIMIT 1", (date,)).fetchone()
            return row is not None
        except sqlite3.OperationalError:
            return False

    # ------------------------------------------------------------------
    # Theme mapping
    # ------------------------------------------------------------------
    def upsert_theme_mapping(self, theme_stocks: Dict[str, List[str]]):
        rows = []
        for theme, stocks in theme_stocks.items():
            for s in stocks:
                rows.append((s, theme))
        if not rows:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO theme_mapping(stock, theme) VALUES (?,?)", rows)
        self.conn.commit()

    def get_theme_mapping(self) -> Dict[str, List[str]]:
        df = pd.read_sql("SELECT stock, theme FROM theme_mapping", self.conn)
        out: Dict[str, List[str]] = {}
        for _, r in df.iterrows():
            out.setdefault(r["theme"], []).append(r["stock"])
        return out

    # ------------------------------------------------------------------
    # Theme features
    # ------------------------------------------------------------------
    def upsert_theme_features(self, date: str, rows: List[Dict[str, Any]]):
        if not rows:
            return
        sql = ("INSERT OR REPLACE INTO theme_features "
               "(date, theme, theme_persistence, theme_rank, breadth, leader_score, "
               " theme_state, expected_duration, rotation_probability, extra) "
               "VALUES (:date, :theme, :theme_persistence, :theme_rank, :breadth, "
               " :leader_score, :theme_state, :expected_duration, "
               " :rotation_probability, :extra)")
        payload = []
        for r in rows:
            d = {
                "date": date, "theme": r.get("theme", ""),
                "theme_persistence": r.get("theme_persistence", 0.0),
                "theme_rank": int(r.get("theme_rank", 0)),
                "breadth": r.get("breadth", 0.0),
                "leader_score": r.get("leader_score", 0.0),
                "theme_state": r.get("theme_state", ""),
                "expected_duration": r.get("expected_duration", 0.0),
                "rotation_probability": r.get("rotation_probability", 0.0),
                "extra": json.dumps({k: v for k, v in r.items()
                                     if k not in {"date", "theme", "theme_persistence",
                                                  "theme_rank", "breadth", "leader_score",
                                                  "theme_state", "expected_duration",
                                                  "rotation_probability"}},
                                    ensure_ascii=False, default=str),
            }
            payload.append(d)
        self.conn.executemany(sql, payload)
        self.conn.commit()

    def get_theme_features(self, date: str) -> pd.DataFrame:
        return pd.read_sql("SELECT * FROM theme_features WHERE date=?",
                           self.conn, params=[date])

    # ------------------------------------------------------------------
    # ETF features (flexible JSON blob)
    # ------------------------------------------------------------------
    def upsert_etf_features(self, date: str, etf: str, features: Dict[str, Any]):
        self.conn.execute(
            "INSERT OR REPLACE INTO etf_features(date, etf, features) VALUES (?,?,?)",
            (date, etf, json.dumps(features, ensure_ascii=False, default=str)))
        self.conn.commit()

    def get_etf_features(self, date: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT etf, features FROM etf_features WHERE date=?", (date,)).fetchall()
        out = []
        for etf, feat_json in rows:
            try:
                d = json.loads(feat_json)
                d["etf"] = etf
                d["date"] = date
                out.append(d)
            except Exception:
                continue
        return out

    # ------------------------------------------------------------------
    # Prediction results
    # ------------------------------------------------------------------
    def upsert_prediction(self, date: str, etf: str, rank: int, score: float):
        self.conn.execute(
            "INSERT OR REPLACE INTO prediction_result(date, etf, rank, score) "
            "VALUES (?,?,?,?)", (date, etf, int(rank), float(score)))
        self.conn.commit()

    def upsert_predictions_batch(self, date: str,
                                 rows: List[Dict[str, Any]]):
        if not rows:
            return
        payload = [(date, r["etf"], int(r["rank"]), float(r["score"])) for r in rows]
        self.conn.executemany(
            "INSERT OR REPLACE INTO prediction_result(date, etf, rank, score) "
            "VALUES (?,?,?,?)", payload)
        self.conn.commit()

    def get_predictions(self, date: str) -> pd.DataFrame:
        return pd.read_sql("SELECT * FROM prediction_result WHERE date=? ORDER BY rank",
                           self.conn, params=[date])
