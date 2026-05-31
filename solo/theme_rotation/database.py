# -*- coding: utf-8 -*-
"""SQLite 数据层：主题轮动缓存"""
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional

from .config import PORTFOLIO_DB, ROTATION_DB


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_rotation_db():
    conn = _connect(ROTATION_DB)
    c = conn.cursor()

    c.executescript("""
    CREATE TABLE IF NOT EXISTS theme_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT NOT NULL,
        theme_name TEXT NOT NULL,
        score REAL,
        strength REAL,
        momentum REAL,
        acceleration REAL,
        zt_count INTEGER,
        zt_ratio REAL,
        max_lb INTEGER,
        state TEXT,
        rank INTEGER,
        UNIQUE(trade_date, theme_name)
    );

    CREATE TABLE IF NOT EXISTS leader_daily (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT NOT NULL,
        theme_name TEXT NOT NULL,
        ts_code TEXT NOT NULL,
        name TEXT,
        layer TEXT,
        leader_prob REAL,
        starter_prob REAL,
        pct_chg REAL,
        turnover REAL,
        amount REAL,
        is_limit_up INTEGER DEFAULT 0,
        lb_height INTEGER DEFAULT 0,
        is_starter INTEGER DEFAULT 0,
        UNIQUE(trade_date, theme_name, ts_code)
    );

    CREATE TABLE IF NOT EXISTS theme_state (
        theme_name TEXT PRIMARY KEY,
        state TEXT,
        score REAL,
        strength REAL,
        momentum REAL,
        history TEXT,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS daily_plan (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT NOT NULL UNIQUE,
        mainline_theme TEXT,
        backup_theme TEXT,
        starter_ts_code TEXT,
        starter_name TEXT,
        starter_theme TEXT,
        starter_prob REAL,
        plan_json TEXT,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS alert_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date TEXT,
        alert_time TEXT,
        alert_type TEXT,
        theme_name TEXT,
        ts_code TEXT,
        name TEXT,
        message TEXT,
        sent INTEGER DEFAULT 0
    );

    CREATE INDEX IF NOT EXISTS idx_theme_daily_date ON theme_daily(trade_date);
    CREATE INDEX IF NOT EXISTS idx_leader_daily_date ON leader_daily(trade_date);
    CREATE INDEX IF NOT EXISTS idx_alert_log_date ON alert_log(trade_date);
    """)
    conn.commit()
    conn.close()


def load_portfolio() -> List[Dict]:
    if not __import__("os").path.exists(PORTFOLIO_DB):
        raise FileNotFoundError(
            f"未找到 {PORTFOLIO_DB}，请先运行 theme_portfolio_strategy_cached.py"
        )
    conn = _connect(PORTFOLIO_DB)
    rows = conn.execute(
        "SELECT ts_code, name, theme_name, layer, mcap, turnover, amount, "
        "purity, trend, volatility, trade_date FROM portfolio"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def load_themes() -> List[Dict]:
    conn = _connect(PORTFOLIO_DB)
    rows = conn.execute(
        "SELECT theme_name, industry, keywords FROM themes"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_theme_daily(trade_date: str, records: List[Dict]):
    conn = _connect(ROTATION_DB)
    c = conn.cursor()
    for r in records:
        c.execute("""
            INSERT OR REPLACE INTO theme_daily
            (trade_date, theme_name, score, strength, momentum, acceleration,
             zt_count, zt_ratio, max_lb, state, rank)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            trade_date, r["theme_name"], r["score"], r["strength"],
            r["momentum"], r["acceleration"], r["zt_count"], r["zt_ratio"],
            r["max_lb"], r["state"], r["rank"],
        ))
    conn.commit()
    conn.close()


def save_leader_daily(trade_date: str, records: List[Dict]):
    conn = _connect(ROTATION_DB)
    c = conn.cursor()
    for r in records:
        c.execute("""
            INSERT OR REPLACE INTO leader_daily
            (trade_date, theme_name, ts_code, name, layer, leader_prob,
             starter_prob, pct_chg, turnover, amount, is_limit_up, lb_height, is_starter)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            trade_date, r["theme_name"], r["ts_code"], r["name"], r["layer"],
            r["leader_prob"], r["starter_prob"], r["pct_chg"], r["turnover"],
            r["amount"], r.get("is_limit_up", 0), r.get("lb_height", 0),
            r.get("is_starter", 0),
        ))
    conn.commit()
    conn.close()


def save_theme_state(records: List[Dict]):
    conn = _connect(ROTATION_DB)
    c = conn.cursor()
    now = datetime.now().isoformat()
    for r in records:
        c.execute("""
            INSERT OR REPLACE INTO theme_state
            (theme_name, state, score, strength, momentum, history, updated_at)
            VALUES (?,?,?,?,?,?,?)
        """, (
            r["theme_name"], r["state"], r["score"], r["strength"],
            r["momentum"], r.get("history", ""), now,
        ))
    conn.commit()
    conn.close()


def save_daily_plan(trade_date: str, plan: Dict):
    import json
    conn = _connect(ROTATION_DB)
    conn.execute("""
        INSERT OR REPLACE INTO daily_plan
        (trade_date, mainline_theme, backup_theme, starter_ts_code, starter_name,
         starter_theme, starter_prob, plan_json, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        trade_date,
        plan.get("mainline_theme"),
        plan.get("backup_theme"),
        plan.get("starter_ts_code"),
        plan.get("starter_name"),
        plan.get("starter_theme"),
        plan.get("starter_prob"),
        json.dumps(plan, ensure_ascii=False),
        datetime.now().isoformat(),
    ))
    conn.commit()
    conn.close()


def get_daily_plan(trade_date: str) -> Optional[Dict]:
    import json
    conn = _connect(ROTATION_DB)
    row = conn.execute(
        "SELECT * FROM daily_plan WHERE trade_date=?", (trade_date,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    if d.get("plan_json"):
        d["plan"] = json.loads(d["plan_json"])
    return d


def get_theme_ranking(trade_date: str, top_n: int = 10) -> List[Dict]:
    conn = _connect(ROTATION_DB)
    rows = conn.execute("""
        SELECT * FROM theme_daily WHERE trade_date=?
        ORDER BY strength DESC LIMIT ?
    """, (trade_date, top_n)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_starter_candidates(trade_date: str) -> List[Dict]:
    conn = _connect(ROTATION_DB)
    rows = conn.execute("""
        SELECT * FROM leader_daily
        WHERE trade_date=? AND is_starter=1
        ORDER BY starter_prob DESC
    """, (trade_date,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def log_alert(trade_date: str, alert_type: str, theme_name: str,
              ts_code: str, name: str, message: str, sent: int = 0):
    conn = _connect(ROTATION_DB)
    conn.execute("""
        INSERT INTO alert_log
        (trade_date, alert_time, alert_type, theme_name, ts_code, name, message, sent)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        trade_date, datetime.now().strftime("%H:%M:%S"),
        alert_type, theme_name, ts_code, name, message, sent,
    ))
    conn.commit()
    conn.close()


def get_recent_alerts(trade_date: str, minutes: int = 30) -> List[str]:
    """返回近期已推送的 ts_code 列表，用于去重"""
    conn = _connect(ROTATION_DB)
    rows = conn.execute("""
        SELECT ts_code FROM alert_log
        WHERE trade_date=? AND sent=1
        ORDER BY id DESC LIMIT 50
    """, (trade_date,)).fetchall()
    conn.close()
    return [r["ts_code"] for r in rows]
