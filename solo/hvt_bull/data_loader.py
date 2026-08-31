# -*- coding: utf-8 -*-
"""HVT-BULL 数据加载层

统一从本地 SQLite (stk_factor_pro / daily_cache) 读取单股时间序列，
按需提供换手率：2025 之前用 vol/float_share 或成交额代理，2025 起用真实换手率。
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


class HvtDataLoader:
    """单股时间序列加载器（进程内缓存）"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._cache = {}

    def load(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        key = (ts_code, start_date, end_date)
        if key in self._cache:
            return self._cache[key]
        sql = (
            "SELECT {cols} FROM stk_factor_pro WHERE ts_code=? "
            "AND trade_date>=? AND trade_date<=? ORDER BY trade_date"
        ).format(cols=','.join(_TS_COLS))
        with sqlite3.connect(self.db_path, timeout=60.0) as conn:
            df = pd.read_sql(sql, conn, params=(ts_code, start_date, end_date))
        if df is None or df.empty:
            self._cache[key] = None
            return None
        df = df.dropna(subset=['close', 'vol'])
        for c in ('open', 'high', 'low', 'close', 'vol', 'amount',
                  'turnover_rate', 'pct_chg', 'total_mv', 'circ_mv', 'adj_factor'):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.reset_index(drop=True)
        self._cache[key] = df
        return df

    def prefill(self, ts_code: str, start_date: str, end_date: str) -> None:
        self.load(ts_code, start_date, end_date)

    def query_cross_section(self, trade_date: str, fields=('ts_code', 'turnover_rate', 'amount', 'total_mv')) -> pd.DataFrame:
        cols = ','.join(fields)
        sql = f"SELECT {cols} FROM stk_factor_pro WHERE trade_date=?"
        with sqlite3.connect(self.db_path, timeout=60.0) as conn:
            return pd.read_sql(sql, conn, params=(trade_date,))

    def trade_dates(self, start_date: str, end_date: str) -> list:
        sql = ("SELECT DISTINCT trade_date FROM stk_factor_pro "
               "WHERE trade_date>=? AND trade_date<=? AND ts_code='000001.SZ' "
               "ORDER BY trade_date")
        with sqlite3.connect(self.db_path, timeout=60.0) as conn:
            df = pd.read_sql(sql, conn, params=(start_date, end_date))
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
