# -*- coding: utf-8 -*-
"""MSW 数据适配层（任务书 §11 data_adapter.py）

硬约束（与 hve_v1 同）：
  - 唯一数据源：D:\\mystock\\cache_daily\\stock_data.db（只读，零网络、零写入）
  - 不得重建/覆盖/清空现有缓存；不得 import requests/tushare/akshare
  - 不 import 任何既有策略模块（隔离原则 §2.3），DB 路径在本模块内自行定义

价格基准：
  hfq（后复权）= 原始价 × adj_factor（已验证与 stk_factor_pro.close_hfq 完全一致）。
  adj_factor_cache 起于 2023-01-03；更早的日期 adj 缺失，hfq 回落为不复权价
  （仅影响 2021-2022 热身期，不产生信号，已在实施计划中披露）。
"""
import os
import sqlite3

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.environ.get('MSTOCK_CACHE', r'D:\mystock\cache_daily')
DB_PATH = os.path.join(CACHE_DIR, 'stock_data.db')
BASIC_PATH = os.path.join(CACHE_DIR, 'stock_basic.csv')

BJ_PREFIX = ('8', '4', '9')

# daily_cache.amount 单位=千元；total_mv 单位=万元
AMOUNT_UNIT_WAN = 10.0          # 千元 → 万元


def norm_date(x) -> str:
    """日期归一化为 YYYYMMDD 文本（库内 trade_date/ann_date 均为该格式文本）"""
    if x is None:
        return ''
    s = str(x).strip()
    if not s or s.lower() in ('nan', 'none'):
        return ''
    return s.replace('-', '').replace('/', '')[:8]


def _to_float(s):
    return pd.to_numeric(s, errors='coerce')


class MswData:
    """MSW 只读数据入口（进程内缓存）"""

    def __init__(self, db_path: str = DB_PATH, basic_path: str = BASIC_PATH):
        self.db_path = db_path
        self.basic_path = basic_path
        self._stock_cache = {}
        self._index_cache = {}
        self._fina_cache = {}
        self._fina_all_cache = {}
        self._dates = None
        self._codes = None
        self._sb = None
        self._breadth = None

    # ---------------- 连接 ----------------

    def _connect(self):
        return sqlite3.connect(f'file:{self.db_path}?mode=ro', uri=True, timeout=60.0)

    # ---------------- 交易日历 / 股票代码 ----------------

    def trade_dates(self, start_date: str = '', end_date: str = '') -> list:
        if self._dates is None:
            with self._connect() as conn:
                df = pd.read_sql(
                    "SELECT DISTINCT trade_date FROM daily_cache "
                    "WHERE ts_code='000001.SZ' ORDER BY trade_date", conn)
            self._dates = [str(x) for x in df['trade_date'].tolist()]
        ds = self._dates
        if start_date:
            ds = [d for d in ds if d >= norm_date(start_date)]
        if end_date:
            ds = [d for d in ds if d <= norm_date(end_date)]
        return ds

    def all_codes(self) -> list:
        """daily_cache 中出现过的全部股票（含已退市，缓解幸存者偏差）"""
        if self._codes is None:
            with self._connect() as conn:
                df = pd.read_sql("SELECT DISTINCT ts_code FROM daily_cache", conn)
            self._codes = sorted(str(x) for x in df['ts_code'].tolist())
        return self._codes

    # ---------------- 单股时序 ----------------

    def load_stock(self, ts_code: str, start_date: str = '', end_date: str = '') -> pd.DataFrame:
        """单股日线（daily + daily_basic + adj_factor 三表 JOIN）+ hfq 价，按 trade_date 升序

        返回列：trade_date/open/high/low/close/pre_close/pct_chg/vol/amount/
                turnover_rate/volume_ratio/total_mv/circ_mv/adj_factor/
                hfq_open/hfq_high/hfq_low/hfq_close
        无数据返回 None。
        """
        key = (ts_code, start_date, end_date)
        if key in self._stock_cache:
            return self._stock_cache[key]
        sql = (
            'SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close, d.pre_close, '
            'd.pct_chg, d.vol, d.amount, '
            'b.turnover_rate, b.volume_ratio, b.total_mv, b.circ_mv, '
            'a.adj_factor '
            'FROM daily_cache d '
            'LEFT JOIN daily_basic_cache b ON b.ts_code=d.ts_code AND b.trade_date=d.trade_date '
            'LEFT JOIN adj_factor_cache a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date '
            'WHERE d.ts_code=? AND d.trade_date>=? AND d.trade_date<=? '
            'ORDER BY d.trade_date'
        )
        try:
            with self._connect() as conn:
                df = pd.read_sql(sql, conn, params=(ts_code, norm_date(start_date) or '0',
                                                    norm_date(end_date) or '9'))
        except Exception:
            df = None
        if df is None or df.empty:
            self._stock_cache[key] = None
            return None
        for c in ('open', 'high', 'low', 'close', 'pre_close', 'pct_chg', 'vol', 'amount',
                  'turnover_rate', 'volume_ratio', 'total_mv', 'circ_mv', 'adj_factor'):
            if c in df.columns:
                df[c] = _to_float(df[c])
        df = df.dropna(subset=['close', 'vol', 'amount'])
        # hfq = 原价 × adj_factor；adj 缺失（2023 前）回落为原价
        adj = df['adj_factor'].where(df['adj_factor'] > 0, 1.0).fillna(1.0)
        for src, dst in (('open', 'hfq_open'), ('high', 'hfq_high'),
                         ('low', 'hfq_low'), ('close', 'hfq_close')):
            df[dst] = df[src] * adj
        df['trade_date'] = df['trade_date'].astype(str)
        df = df.reset_index(drop=True)
        self._stock_cache[key] = df
        return df

    # ---------------- 指数序列 ----------------

    def index_series(self, index_code: str, end_date: str, lookback: int = 300,
                     start_date: str = '') -> pd.Series:
        """指数收盘序列（升序 Series，index=trade_date）。指数点位无需复权。"""
        key = (index_code, start_date, end_date, lookback)
        if key in self._index_cache:
            return self._index_cache[key]
        sql = ('SELECT trade_date, close FROM index_daily_cache '
               'WHERE ts_code=? AND trade_date<=? AND trade_date>=? ORDER BY trade_date')
        try:
            with self._connect() as conn:
                df = pd.read_sql(sql, conn, params=(index_code, norm_date(end_date),
                                                    norm_date(start_date) or '0'))
        except Exception:
            df = None
        if df is None or df.empty:
            self._index_cache[key] = pd.Series(dtype=float)
            return self._index_cache[key]
        df['close'] = _to_float(df['close'])
        df['trade_date'] = df['trade_date'].astype(str)
        s = df.dropna(subset=['close']).set_index('trade_date')['close']
        s = s.tail(int(lookback))
        self._index_cache[key] = s
        return s

    # ---------------- 基本面（PIT） ----------------

    def fundamentals_series(self, ts_code: str) -> list:
        """个股全部已公布财务指标（按 ann_date 升序），供逐日 PIT 查找

        一次查询替代逐日查询（回测 5789 股 × 690 日场景）。
        """
        if ts_code in self._fina_all_cache:
            return self._fina_all_cache[ts_code]
        sql = ('SELECT ann_date, end_date, grossprofit_margin, netprofit_yoy, or_yoy, '
               'netprofit_2yoy, ocf_to_or, ocf_yoy, roe, debt_to_assets '
               'FROM fina_indicator_cache WHERE ts_code=? ORDER BY ann_date')
        out = []
        try:
            with self._connect() as conn:
                rows = conn.execute(sql, (ts_code,)).fetchall()
            for r in rows:
                out.append({'ann_date': str(r[0]), 'end_date': str(r[1]),
                            'grossprofit_margin': r[2], 'netprofit_yoy': r[3],
                            'or_yoy': r[4], 'netprofit_2yoy': r[5],
                            'ocf_to_or': r[6], 'ocf_yoy': r[7],
                            'roe': r[8], 'debt_to_assets': r[9]})
        except Exception:
            out = []
        self._fina_all_cache[ts_code] = out
        return out

    def fundamentals(self, ts_code: str, as_of_date: str) -> dict | None:
        """as_of_date 当时已公布的最近两期财务指标（ann_date <= as_of_date，PIT 安全）"""
        key = (ts_code, as_of_date)
        if key in self._fina_cache:
            return self._fina_cache[key]
        sql = ('SELECT ann_date, end_date, grossprofit_margin, netprofit_yoy, or_yoy, '
               'netprofit_2yoy, ocf_to_or, ocf_yoy, roe, debt_to_assets '
               'FROM fina_indicator_cache WHERE ts_code=? AND ann_date<=? '
               'ORDER BY ann_date DESC LIMIT 2')
        out = None
        try:
            with self._connect() as conn:
                rows = conn.execute(sql, (ts_code, norm_date(as_of_date))).fetchall()
            if rows:
                latest = rows[0]
                prev = rows[1] if len(rows) > 1 else (None,) * len(latest)
                out = {
                    'ann_date': str(latest[0]), 'end_date': str(latest[1]),
                    'grossprofit_margin': latest[2], 'netprofit_yoy': latest[3],
                    'or_yoy': latest[4], 'netprofit_2yoy': latest[5],
                    'ocf_to_or': latest[6], 'ocf_yoy': latest[7],
                    'roe': latest[8], 'debt_to_assets': latest[9],
                    'prev': {'ann_date': str(prev[0]) if prev[0] else None,
                             'end_date': str(prev[1]) if prev[1] else None,
                             'grossprofit_margin': prev[2], 'netprofit_yoy': prev[3],
                             'or_yoy': prev[4], 'netprofit_2yoy': prev[5],
                             'ocf_to_or': prev[6], 'ocf_yoy': prev[7],
                             'roe': prev[8], 'debt_to_assets': prev[9]},
                }
        except Exception:
            out = None
        self._fina_cache[key] = out
        return out

    # ---------------- 股票基础信息 ----------------

    def stock_basic(self) -> pd.DataFrame:
        if self._sb is None:
            try:
                sb = pd.read_csv(self.basic_path, dtype={'ts_code': str, 'list_date': str})
            except Exception:
                sb = pd.DataFrame(columns=['ts_code', 'name', 'industry', 'list_date'])
            self._sb = sb
        return self._sb

    def basic_maps(self) -> tuple:
        """(code→name, code→list_date, code→industry)；退市股不在表中时给默认值"""
        sb = self.stock_basic()
        names, lds, inds = {}, {}, {}
        if sb is not None and not sb.empty:
            names = dict(zip(sb['ts_code'], sb['name'].astype(str)))
            lds = dict(zip(sb['ts_code'], sb['list_date'].astype(str)))
            inds = dict(zip(sb['ts_code'], sb['industry'].astype(str)))
        return names, lds, inds

    # ---------------- 单日截面（匹配对照组用） ----------------

    def snapshot_fields(self, trade_date: str) -> pd.DataFrame:
        """某日全市场截面：ts_code/total_mv/amount/turnover_rate（一次查询）"""
        sql = ('SELECT d.ts_code, b.total_mv, d.amount, b.turnover_rate '
               'FROM daily_cache d '
               'LEFT JOIN daily_basic_cache b ON b.ts_code=d.ts_code AND b.trade_date=d.trade_date '
               'WHERE d.trade_date=?')
        try:
            with self._connect() as conn:
                df = pd.read_sql(sql, conn, params=(norm_date(trade_date),))
            return df
        except Exception:
            return pd.DataFrame(columns=['ts_code', 'total_mv', 'amount', 'turnover_rate'])

    # ---------------- 市场宽度（可选 regime 过滤 / 消融） ----------------

    def prefetch_breadth(self, start_date: str, end_date: str) -> int:
        """批量预取区间每日市场宽度（上涨家数占比，仅沪深A股剔北交所）"""
        if self._breadth is None:
            self._breadth = {}
        sql = ("SELECT trade_date, COUNT(pct_chg), "
               "SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END) "
               "FROM daily_cache WHERE trade_date>=? AND trade_date<=? "
               "AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ') "
               "AND substr(ts_code,1,1) NOT IN ('4','8','9') GROUP BY trade_date")
        try:
            with self._connect() as conn:
                rows = conn.execute(sql, (norm_date(start_date), norm_date(end_date))).fetchall()
        except Exception:
            return 0
        for td, n, up in rows:
            n = int(n or 0)
            self._breadth[norm_date(td)] = (int(up or 0) / n) if n > 0 else float('nan')
        return len(rows)

    def breadth(self, trade_date: str):
        if self._breadth is None:
            return float('nan')
        return self._breadth.get(str(trade_date), float('nan'))


# ---------------- 股票池判定（向量化，与配置阈值一致） ----------------

def universe_mask(d: pd.DataFrame, ts_code: str, name: str, list_date: str,
                  cfg: dict) -> np.ndarray:
    """逐行向量化股票池判定（回测路径），返回与 d 等长的 bool 数组。

    口径（与 msw_config.json universe 段一致）：
      - 沪深 A 股（.SH/.SZ，剔北交所 8/4/9 前缀）
      - 当前名称不含 ST / 退（已知局限：无 PIT 名称历史）
      - 上市 >= min_listed_days；list_date 缺失时以数据起始日 + min_listed_days 近似
      - 过去 20 日成交额中位数 >= min_avg_amount_20_wan（万元）
      - total_mv >= min_total_mv_yi（亿元）
    """
    uni = cfg.get('universe', {})
    n = len(d)
    ok = np.zeros(n, dtype=bool)
    code = str(ts_code)
    if not code.endswith(('.SH', '.SZ')):
        return ok
    if uni.get('exclude_bj', True) and code.startswith(BJ_PREFIX):
        return ok
    if uni.get('exclude_st_by_current_name', True):
        nm = str(name).upper()
        if 'ST' in nm or '退' in nm:
            return ok

    dates = pd.to_datetime(d['trade_date'].astype(str), format='%Y%m%d')
    listed = np.zeros(n, dtype=bool)
    if list_date and str(list_date) not in ('nan', 'None', ''):
        try:
            ld = pd.to_datetime(str(list_date), format='%Y%m%d')
            listed = ((dates - ld).dt.days >= int(uni.get('min_listed_days', 250))).to_numpy()
        except Exception:
            listed = np.ones(n, dtype=bool)
    else:
        # 退市股无 list_date：以首根K线 + min_listed_days 自然日近似
        first = dates.iloc[0]
        listed = ((dates - first).dt.days >= int(uni.get('min_listed_days', 250))).to_numpy()

    amt = d['amount'].to_numpy(dtype=float) / AMOUNT_UNIT_WAN          # 万元
    win = int(uni.get('amount_median_window', 20))
    med = pd.Series(amt).rolling(win, min_periods=win).median().to_numpy()
    amt_ok = np.isfinite(med) & (med >= float(uni.get('min_avg_amount_20_wan', 3000.0)))

    mv = d['total_mv'].to_numpy(dtype=float)                          # 万元
    mv_w = np.where(mv > 1e6, mv / 10.0, mv)                          # 兼容亿元单位异常
    mv_ok = np.isfinite(mv) & (mv_w >= float(uni.get('min_total_mv_yi', 30.0)) * 10000.0)

    return listed & amt_ok & mv_ok


_shared = None


def shared(db_path: str = DB_PATH) -> MswData:
    global _shared
    if _shared is None or _shared.db_path != db_path:
        _shared = MswData(db_path)
    return _shared
