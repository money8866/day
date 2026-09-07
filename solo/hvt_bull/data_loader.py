# -*- coding: utf-8 -*-
"""HVT-BULL 数据加载层

统一从本地 SQLite 三张普通日线缓存表 JOIN 读取单股时间序列（不再依赖 stk_factor_pro 宽表）：
  daily_cache       (pro.daily)       → ts_code/trade_date/open/high/low/close/pre_close/pct_chg/vol/amount
  daily_basic_cache (pro.daily_basic) → turnover_rate/turnover_rate_f/volume_ratio/total_mv/circ_mv
  adj_factor_cache  (pro.adj_factor)  → adj_factor
"""

import os
import sys
import sqlite3
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

DB_PATH = r'D:\mystock\cache_daily\stock_data.db'

_TS_COLS = ('ts_code', 'trade_date', 'open', 'high', 'low', 'close',
            'pre_close', 'pct_chg', 'vol', 'amount',
            'turnover_rate', 'turnover_rate_f', 'volume_ratio',
            'total_mv', 'circ_mv', 'adj_factor')

# 各列来源：daily_cache=d / daily_basic_cache=b / adj_factor_cache=a
_B_FIELDS = {'turnover_rate', 'turnover_rate_f', 'volume_ratio', 'total_mv', 'circ_mv'}
_A_FIELDS = {'adj_factor'}

_JOIN_SQL = (
    'FROM daily_cache d '
    'LEFT JOIN daily_basic_cache b ON b.ts_code=d.ts_code AND b.trade_date=d.trade_date '
    'LEFT JOIN adj_factor_cache a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date'
)

# 与旧 loader 相同的 numeric 强转列集合（ts_code/trade_date/turnover_rate_f/volume_ratio 保持原样）
_NUM_COLS = ('open', 'high', 'low', 'close', 'vol', 'amount',
             'turnover_rate', 'pct_chg', 'total_mv', 'circ_mv', 'adj_factor')


def _field_sql(field: str) -> str:
    if field in _B_FIELDS:
        return f'b."{field}"'
    if field in _A_FIELDS:
        return f'a."{field}"'
    return f'd."{field}"'


def _ensure_indexes(db_path: str) -> None:
    """补齐截面查询所需索引：daily_cache 仅有 (ts_code,trade_date) 主键，
    按 trade_date 反查整市场需单列索引，否则全表扫描 7M 行。"""
    try:
        with sqlite3.connect(db_path, timeout=60.0) as conn:
            conn.execute('CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_cache (trade_date)')
    except Exception:
        pass


class HvtDataLoader:
    """单股时间序列加载器（进程内缓存）"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._cache = {}
        _ensure_indexes(db_path)

    def _read(self, sql: str, params: tuple):
        try:
            with sqlite3.connect(self.db_path, timeout=60.0) as conn:
                return pd.read_sql(sql, conn, params=params)
        except Exception:
            return None

    def load(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        key = (ts_code, start_date, end_date)
        if key in self._cache:
            return self._cache[key]
        cols = ','.join(_field_sql(c) for c in _TS_COLS)
        sql = (f"SELECT {cols} {_JOIN_SQL} "
               "WHERE d.ts_code=? AND d.trade_date>=? AND d.trade_date<=? "
               "ORDER BY d.trade_date")
        df = self._read(sql, (ts_code, start_date, end_date))
        if df is None or df.empty:
            self._cache[key] = None
            return None
        df = df.dropna(subset=['close', 'vol'])
        for c in _NUM_COLS:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.reset_index(drop=True)
        self._cache[key] = df
        return df

    def prefill(self, ts_code: str, start_date: str, end_date: str) -> None:
        self.load(ts_code, start_date, end_date)

    def query_cross_section(self, trade_date: str, fields=('ts_code', 'turnover_rate', 'amount', 'total_mv')) -> pd.DataFrame:
        sel = ','.join(_field_sql(c) for c in fields)
        sql = f"SELECT {sel} {_JOIN_SQL} WHERE d.trade_date=?"
        df = self._read(sql, (trade_date,))
        # 与旧 loader 一致：无数据返回空 DataFrame 而非 None
        return df if df is not None else pd.DataFrame(columns=list(fields))

    def trade_dates(self, start_date: str, end_date: str) -> list:
        sql = ("SELECT DISTINCT trade_date FROM daily_cache "
               "WHERE trade_date>=? AND trade_date<=? AND ts_code='000001.SZ' "
               "ORDER BY trade_date")
        df = self._read(sql, (start_date or '', end_date))
        return df['trade_date'].tolist() if df is not None and not df.empty else []

    def get_name(self, ts_code: str) -> str:
        try:
            import stock_cache as sc
            sb = sc.load_stock_basic()
            if sb is not None and not sb.empty:
                row = sb[sb['ts_code'] == ts_code]
                if not row.empty:
                    return str(row['name'].values[0])
        except Exception:
            pass
        return ts_code
