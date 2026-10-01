# -*- coding: utf-8 -*-
"""HVE V1 数据层（§二/§三：只复用现有 Tushare 缓存，不新增下载通道）

唯一数据源：D:\\mystock\\cache_daily\\stock_data.db
  - daily_cache / daily_basic_cache / adj_factor_cache → 复用 hvt_bull.data_loader.HvtDataLoader
  - index_daily_cache                                   → 本模块自带只读查询（§31 市场环境）
  - stock_basic（stock_cache）                          → 名称 / 上市日

硬约束：本文件（及 hve_v1/ 全目录）不得 import requests / tushare / akshare / 爬虫。
"""
import os
import sys
import sqlite3

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from hvt_bull.data_loader import HvtDataLoader, DB_PATH  # noqa: E402  只读复用

INDEX_COLS = ('ts_code', 'trade_date', 'open', 'high', 'low', 'close',
              'pre_close', 'change', 'pct_chg', 'vol', 'amount')


class HveData:
    """HVE 数据入口（进程内缓存）"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.loader = HvtDataLoader(db_path)
        self._index_cache = {}
        self._breadth_cache = {}
        self._sb = None

    # ---------------- 单股时序 ----------------

    def load(self, ts_code: str, start: str, end: str):
        """单股日线时序（daily + daily_basic + adj_factor 三表 JOIN，按 trade_date 升序）"""
        return self.loader.load(ts_code, start, end)

    def trade_dates(self, start: str, end: str) -> list:
        return self.loader.trade_dates(start, end)

    def cross_section(self, trade_date: str, fields=('ts_code', 'close', 'amount', 'total_mv', 'pct_chg')):
        return self.loader.query_cross_section(trade_date, fields=fields)

    def get_name(self, ts_code: str) -> str:
        return self.loader.get_name(ts_code)

    # ---------------- 股票基础信息 ----------------

    def stock_basic(self) -> pd.DataFrame:
        if self._sb is None:
            import stock_cache as sc
            sb = sc.load_stock_basic()
            self._sb = sb if sb is not None else pd.DataFrame(columns=['ts_code', 'name', 'list_date'])
        return self._sb

    # ---------------- 指数序列（§31） ----------------

    def index_daily(self, index_code: str, end_date: str, start_date: str = '') -> pd.DataFrame:
        """指数日线（只读 index_daily_cache，按 trade_date 升序）"""
        key = (index_code, start_date, end_date)
        if key in self._index_cache:
            return self._index_cache[key]
        cols = ','.join(INDEX_COLS)
        sql = (f"SELECT {cols} FROM index_daily_cache "
               "WHERE ts_code=? AND trade_date>=? AND trade_date<=? "
               "ORDER BY trade_date")
        try:
            with sqlite3.connect(self.db_path, timeout=60.0) as conn:
                df = pd.read_sql(sql, conn, params=(index_code, start_date or '', end_date))
        except Exception:
            df = pd.DataFrame(columns=list(INDEX_COLS))
        if df is None or df.empty:
            self._index_cache[key] = None
            return None
        for c in ('open', 'high', 'low', 'close', 'pre_close', 'pct_chg', 'vol', 'amount'):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.dropna(subset=['close']).reset_index(drop=True)
        self._index_cache[key] = df
        return df

    def index_close_series(self, index_code: str, end_date: str, lookback: int = 400) -> pd.Series:
        """指数收盘序列（截至 end_date），用于 MA 计算；返回按日期升序的 Series"""
        df = self.index_daily(index_code, end_date)
        if df is None or df.empty:
            return pd.Series(dtype=float)
        s = df.set_index('trade_date')['close']
        return s.tail(lookback)

    # ---------------- 市场宽度（§31 成交额 / Breadth） ----------------

    def breadth(self, trade_date: str) -> dict:
        """当日截面宽度：上涨家数占比 + 成交额合计（仅沪深A股，剔北交所）"""
        if trade_date in self._breadth_cache:
            return self._breadth_cache[trade_date]
        out = {'up_ratio': np.nan, 'amount_sum': np.nan, 'n': 0}
        try:
            cs = self.cross_section(trade_date, fields=('ts_code', 'close', 'amount', 'pct_chg'))
            if cs is not None and not cs.empty:
                cs = cs[cs['ts_code'].astype(str).str.endswith(('.SH', '.SZ'))]
                cs = cs[~cs['ts_code'].astype(str).str.startswith(('8', '4', '9'))]
                pc = pd.to_numeric(cs.get('pct_chg', pd.Series(dtype=float)), errors='coerce').dropna()
                if len(pc) > 0:
                    out['up_ratio'] = float((pc > 0).sum()) / float(len(pc))
                    out['n'] = int(len(pc))
                amt = pd.to_numeric(cs.get('amount', pd.Series(dtype=float)), errors='coerce').dropna()
                if len(amt) > 0:
                    out['amount_sum'] = float(amt.sum())
        except Exception:
            pass
        self._breadth_cache[trade_date] = out
        return out

    def prefetch_breadth(self, start: str, end: str) -> int:
        """批量预取区间每日市场宽度（一次 GROUP BY 取代逐日截面查询）

        与 breadth() 口径完全一致：只统计沪深 A 股（剔 4/8/9 开头）、
        以「pct_chg 非空」为分母、up_ratio = 上涨家数 / 分母。
        回测遍历全市场时逐日 query_cross_section（每日 ~5200 行 × 1150 日）是主要瓶颈，
        故提供本方法；scanner 单日路径不需要。
        """
        sql = ("SELECT trade_date, COUNT(pct_chg), "
               "SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END), SUM(amount) "
               "FROM daily_cache WHERE trade_date >= ? AND trade_date <= ? "
               "AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ') "
               "AND substr(ts_code, 1, 1) NOT IN ('4', '8', '9') "
               "GROUP BY trade_date")
        try:
            with sqlite3.connect(self.db_path, timeout=60.0) as conn:
                rows = conn.execute(sql, (str(start), str(end))).fetchall()
        except Exception as e:
            print(f'[HVE] breadth 批量预取失败（回退逐日查询）: {e}')
            return 0
        for td, n, up, amt in rows:
            n = int(n or 0)
            self._breadth_cache[str(td)] = {
                'up_ratio': (int(up or 0) / n) if n > 0 else np.nan,
                'amount_sum': float(amt) if amt is not None else np.nan,
                'n': n,
            }
        return len(rows)

    # ---------------- 复权收益（§1.3） ----------------

    @staticmethod
    def adjusted_return(close_t1, adj_t1, close_t2, adj_t2) -> float:
        """区间复权收益 = (close_t2 × adj_t2) / (close_t1 × adj_t1) − 1

        只用区间两端各自的复权因子，不引入 t2 之后的任何基准，故无未来函数。
        """
        try:
            a = float(close_t1) * float(adj_t1)
            b = float(close_t2) * float(adj_t2)
            if not (np.isfinite(a) and np.isfinite(b)) or a <= 0:
                return np.nan
            return b / a - 1.0
        except Exception:
            return np.nan


_shared = None


def shared(db_path: str = DB_PATH) -> HveData:
    """进程内共享数据入口"""
    global _shared
    if _shared is None or _shared.db_path != db_path:
        _shared = HveData(db_path)
    return _shared
