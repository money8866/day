# -*- coding: utf-8 -*-
"""
BTS 数据层

数据源优先级（生产扫描用最新数据，回测用长历史）：
  1. SQLite daily_cache 表（D:/mystock/cache_daily/stock_data.db，20250102~最新，Tushare 前复权口径）
  2. TDX 本地 .day 文件（C:/new_tdx，2021~最近一次启动TDX的日期，不复权）

合并策略：以日期为键，DB 优先（最新+复权），TDX 补历史；信号计算前再按 end_date 截断，
严格保证 T 日信号只用 <=T 的数据。
"""
import os
import struct
import sqlite3
from typing import Optional

import numpy as np
import pandas as pd

from .config import CACHE_DB_PATH, STOCK_BASIC_CSV, TDX_PATH

_COLS = ["trade_date", "open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"]


def _conn():
    conn = sqlite3.connect(CACHE_DB_PATH, timeout=10.0)
    return conn


def load_stock_basic() -> pd.DataFrame:
    """股票基础信息（ts_code/name/industry/list_date）"""
    if os.path.exists(STOCK_BASIC_CSV):
        sb = pd.read_csv(STOCK_BASIC_CSV, dtype={"ts_code": str, "list_date": str})
        return sb
    return pd.DataFrame()


def get_stock_pool(exclude_st: bool = True) -> pd.DataFrame:
    """A股票池：沪深主板/创业板/科创板（排除北交所、退市、ST）"""
    sb = load_stock_basic()
    if sb.empty:
        return sb
    pool = sb[sb["ts_code"].str.match(r"^(60|68|00|30)")].copy()
    pool = pool[~pool["ts_code"].str.endswith(".BJ")]
    if exclude_st and "name" in pool.columns:
        pool = pool[~pool["name"].astype(str).str.contains("ST|退", na=False)]
    return pool.reset_index(drop=True)


def get_name_map() -> dict:
    sb = load_stock_basic()
    if sb.empty:
        return {}
    return dict(zip(sb["ts_code"], sb["name"]))


def db_daily(ts_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """SQLite daily_cache 读取"""
    try:
        with _conn() as conn:
            df = pd.read_sql_query(
                f"SELECT {','.join(_COLS)} FROM daily_cache WHERE ts_code=? "
                "AND trade_date>=? AND trade_date<=? ORDER BY trade_date",
                conn, params=(ts_code, str(start_date), str(end_date)),
            )
    except Exception:
        return None
    if df.empty:
        return None
    df["trade_date"] = df["trade_date"].astype(str)
    for c in _COLS[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.reset_index(drop=True)


def ts_code_to_tdx_file(ts_code: str) -> Optional[str]:
    sym, market = ts_code.split(".")
    if market == "SH":
        return os.path.join(TDX_PATH, "vipdoc", "sh", "lday", f"sh{sym}.day")
    if market == "SZ":
        return os.path.join(TDX_PATH, "vipdoc", "sz", "lday", f"sz{sym}.day")
    return None


def parse_tdx_day_file(filepath: str) -> Optional[pd.DataFrame]:
    """通达信 .day -> DataFrame（与 tail_backtest_tdx.parse_tdx_day_file 兼容的单位口径）"""
    if not os.path.exists(filepath):
        return None
    records = []
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(32)
            if not chunk or len(chunk) < 32:
                break
            date_int = struct.unpack("<i", chunk[0:4])[0]
            open_p = struct.unpack("<i", chunk[4:8])[0] / 100.0
            high_p = struct.unpack("<i", chunk[8:12])[0] / 100.0
            low_p = struct.unpack("<i", chunk[12:16])[0] / 100.0
            close_p = struct.unpack("<i", chunk[16:20])[0] / 100.0
            amount_yuan = struct.unpack("<f", chunk[20:24])[0]
            vol_shares = struct.unpack("<i", chunk[24:28])[0] / 100.0
            records.append({
                "trade_date": str(date_int),
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "vol": vol_shares,
                "amount": round(amount_yuan / 1000, 3),
            })
    if not records:
        return None
    df = pd.DataFrame(records).sort_values("trade_date").reset_index(drop=True)
    df["pre_close"] = df["close"].shift(1)
    df["pct_chg"] = df["close"].pct_change() * 100
    df["pct_chg"] = df["pct_chg"].fillna(0)
    return df


def tdx_daily(ts_code: str) -> Optional[pd.DataFrame]:
    f = ts_code_to_tdx_file(ts_code)
    if not f:
        return None
    return parse_tdx_day_file(f)


def load_total_mv_series(ts_code: str) -> Optional[dict]:
    """该股全历史 {trade_date: total_mv(亿元)}（stk_factor_pro 实时市值，非复权影响市值不变）"""
    try:
        with _conn() as conn:
            df = pd.read_sql_query(
                "SELECT trade_date, total_mv FROM stk_factor_pro WHERE ts_code=? ORDER BY trade_date",
                conn, params=(ts_code,),
            )
    except Exception:
        return None
    if df.empty:
        return None
    df["trade_date"] = df["trade_date"].astype(str)
    mv = pd.to_numeric(df["total_mv"], errors="coerce")
    return {d: float(v) / 10000.0 for d, v in zip(df["trade_date"], mv) if pd.notna(v)}


def load_total_mv(ts_code: str, trade_date: str) -> Optional[float]:
    """某股某日总市值（亿元）"""
    s = load_total_mv_series(ts_code)
    if s is None:
        return None
    return s.get(str(trade_date))


def load_daily(ts_code: str, end_date: str, lookback_bars: int = 260) -> Optional[pd.DataFrame]:
    """加载 <=end_date 的 OHLCV 升序序列（截断到最近 lookback_bars 根）。

    数据源策略（避免复权口径混用导致的接缝跳变）：
      1. SQLite daily_cache 单源足够 → 只用 DB（20250102 起，前复权）
      2. TDX 单源足够 → 只用 TDX（2021 起，不复权；用于 2024 及更早回测）
      3. 都不足 → 合并（DB 优先、TDX 补历史）

    防未来数据：本函数唯一出口处统一做 trade_date <= end_date 过滤。
    """
    end = str(end_date)

    df_db = db_daily(ts_code, "19900101", end)
    if df_db is not None and len(df_db) >= lookback_bars:
        merged = df_db.tail(lookback_bars).reset_index(drop=True)
    else:
        df_td = tdx_daily(ts_code)
        if df_td is not None:
            df_td = df_td[df_td["trade_date"] <= end]
        if df_td is not None and len(df_td) >= lookback_bars:
            merged = df_td.tail(lookback_bars).reset_index(drop=True)
        elif df_td is not None and df_db is not None:
            merged = pd.concat([df_td, df_db]).drop_duplicates(subset="trade_date", keep="last")
            merged = merged.sort_values("trade_date").reset_index(drop=True)
        elif df_td is not None:
            merged = df_td.reset_index(drop=True)
        elif df_db is not None:
            merged = df_db.reset_index(drop=True)
        else:
            return None

    if merged.empty:
        return None
    merged = merged.tail(lookback_bars).reset_index(drop=True)
    for c in ("open", "high", "low", "close", "vol"):
        merged[c] = pd.to_numeric(merged[c], errors="coerce")
    merged = merged.dropna(subset=["open", "high", "low", "close", "vol"])
    if len(merged) < 30:
        return None
    return merged.reset_index(drop=True)


def db_daily_by_date(trade_date: str) -> Optional[pd.DataFrame]:
    """某交易日全市场日线（用于快速预筛）"""
    try:
        with _conn() as conn:
            df = pd.read_sql_query(
                f"SELECT {','.join(_COLS)} FROM daily_cache WHERE trade_date=?",
                conn, params=(str(trade_date),),
            )
    except Exception:
        return None
    if df.empty:
        return None
    df["trade_date"] = df["trade_date"].astype(str)
    return df


def get_trade_dates(start_date: str, end_date: str) -> list:
    """用上证指数 .day 构建交易日历（无网络依赖）"""
    idx = parse_tdx_day_file(os.path.join(TDX_PATH, "vipdoc", "sh", "lday", "sh999999.day"))
    if idx is None:
        idx = parse_tdx_day_file(os.path.join(TDX_PATH, "vipdoc", "sh", "lday", "sh000001.day"))
    if idx is None:
        # 退路：从 DB 全表 distinct
        try:
            with _conn() as conn:
                dates = pd.read_sql_query(
                    "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date",
                    conn, params=(str(start_date), str(end_date)),
                )["trade_date"].tolist()
            return [str(d) for d in dates]
        except Exception:
            return []
    return [d for d in idx["trade_date"].tolist() if str(start_date) <= d <= str(end_date)]


def last_trade_date_on_or_before(date_str: str) -> Optional[str]:
    """<=date_str 的最近交易日"""
    dates = get_trade_dates("19900101", str(date_str))
    return dates[-1] if dates else None


def market_regime(date_str: str) -> str:
    """市场状态（strong/neutral/weak/bear）：上证指数 MA20/MA60 位置 + 20日涨幅

    仅使用 <=date_str 的指数数据，无未来函数。
    """
    idx = parse_tdx_day_file(os.path.join(TDX_PATH, "vipdoc", "sh", "lday", "sh999999.day"))
    if idx is None:
        idx = parse_tdx_day_file(os.path.join(TDX_PATH, "vipdoc", "sh", "lday", "sh000001.day"))
    if idx is None:
        return "neutral"
    idx = idx[idx["trade_date"] <= str(date_str)].reset_index(drop=True)
    if len(idx) < 70:
        return "neutral"
    close = idx["close"]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]
    ret20 = close.iloc[-1] / close.iloc[-21] - 1 if len(idx) >= 21 else 0.0
    now = close.iloc[-1]
    if now > ma20 > ma60 and ret20 > 0.03:
        return "strong"
    if now < ma20 and now < ma60 and ret20 < -0.05:
        return "bear"
    if now < ma20 * 0.99 or ret20 < -0.02:
        return "weak"
    return "neutral"


def to_ts_code(code6: str) -> str:
    code6 = str(code6).strip().zfill(6)
    if code6.startswith(("60", "68", "51", "50")):
        return f"{code6}.SH"
    if code6.startswith(("4", "8")):
        return f"{code6}.BJ"
    return f"{code6}.SZ"
