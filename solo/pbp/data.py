# -*- coding: utf-8 -*-
"""
PBP 数据层：统一走 UDC（stock_cache.py 的 Unified Daily Cache）

日线行情（load_daily）→ stock_cache.daily()
  UDC 缓存优先（D:/mystock/cache_daily/stock_data.db 的 daily_cache 表），
  缺失/过期时 Tushare API 兜底并自动写回缓存（auto_fill=True），
  因此可覆盖到最新交易日（本地 TDX 未更新的日期）。

交易日历（get_trade_dates / last_trade_date_on_or_before）→ UDC daily_cache 表
  DISTINCT trade_date（Tushare 补写后自动含最新交易日），缓存表为空时退回
  bts.data 的 TDX 指数日历实现。

非日线数据（股票池/名称/市值/代码转换，来自 stock_basic.csv / stk_factor_pro）
  → bts.data 转发（与 UDC 共用同一 DB，无冲突）。

防未来数据：load_daily 唯一出口统一做 trade_date <= end_date 过滤。
"""
import datetime as _dt

import pandas as pd

from bts.config import CACHE_DB_PATH
from bts.data import (
    load_stock_basic,
    get_stock_pool,
    get_name_map,
    db_daily,
    db_daily_by_date,
    to_ts_code,
    load_total_mv,
    load_total_mv_series,
    get_trade_dates as _bts_get_trade_dates,
)
from stock_cache import daily as _udc_daily


def load_daily(ts_code: str, end_date: str, lookback_bars: int = 260):
    """加载 <=end_date 的 OHLCV 升序序列（截断到最近 lookback_bars 根）。

    数据源：UDC（stock_cache.daily）缓存优先 + Tushare API 兜底写回。
    UDC 返回 11 列标准 daily 格式（ts_code/trade_date/open/high/low/close/
    pre_close/change/pct_chg/vol/amount），此处统一数值类型并过滤缺失行。
    防未来数据：唯一出口统一做 trade_date <= end_date 过滤。
    """
    end = str(end_date)
    # 回看窗口起始日：lookback_bars 根 K 线约需 lookback_bars*2.2 自然日（含节假日缓冲）
    try:
        start = (_dt.datetime.strptime(end, "%Y%m%d")
                 - _dt.timedelta(days=int(lookback_bars * 2.2))).strftime("%Y%m%d")
    except Exception:
        start = "19900101"
    df = _udc_daily(ts_code, start, end, auto_fill=True, silent=True)
    if df is None or df.empty:
        return None
    df = df[df["trade_date"].astype(str) <= end].reset_index(drop=True)
    df = df.tail(lookback_bars).reset_index(drop=True)
    for c in ("open", "high", "low", "close", "vol"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close", "vol"])
    if len(df) < 30:
        return None
    return df.reset_index(drop=True)


def get_trade_dates(start_date: str, end_date: str) -> list:
    """交易日历：优先 UDC daily_cache 表 distinct（Tushare 补写后含最新交易日）"""
    import sqlite3
    try:
        conn = sqlite3.connect(CACHE_DB_PATH, timeout=10.0)
        dates = pd.read_sql_query(
            "SELECT DISTINCT trade_date FROM daily_cache "
            "WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date",
            conn, params=(str(start_date), str(end_date)),
        )["trade_date"].tolist()
        conn.close()
    except Exception:
        dates = []
    if dates:
        return [str(d) for d in dates]
    return _bts_get_trade_dates(start_date, end_date)


def last_trade_date_on_or_before(date_str: str):
    """<=date_str 的最近交易日（基于 UDC 缓存日历）"""
    dates = get_trade_dates("19900101", str(date_str))
    return dates[-1] if dates else None
