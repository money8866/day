# -*- coding: utf-8 -*-
"""CSI2000 Position Gate —— 数据层

硬约束(§2)：唯一数据源 = 本地 Tushare 缓存 D:\\mystock\\cache_daily\\stock_data.db。
不使用 TDX / AkShare / Wind / 同花顺 / 临时外部行情源。

用到的表：
  daily_cache        个股日线
  daily_basic_cache  每日基本面(total_mv / turnover_rate)，用于动态小盘池
  index_daily_cache  指数日线(932000.CSI / 000985.CSI / ...)
  index_member_cache 中证2000 成分股月度快照（本模块新建，Tushare index_weight 落盘）
"""
import os
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from position_gate import config as C  # noqa: E402

INDEX_COLS = ('ts_code', 'trade_date', 'open', 'high', 'low', 'close',
              'pre_close', 'pct_chg', 'vol', 'amount')


def _conn(db_path=None):
    return sqlite3.connect(db_path or C.DB_PATH, timeout=120.0)


class GateData:
    """Gate 数据入口（进程内缓存）"""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or C.DB_PATH
        self._cache = {}

    # ---------------- 基础 ----------------

    def trade_dates(self, start: str, end: str) -> list:
        key = ('td', start, end)
        if key not in self._cache:
            sql = ("SELECT DISTINCT trade_date FROM daily_cache "
                   "WHERE trade_date>=? AND trade_date<=? AND ts_code='000001.SZ' "
                   "ORDER BY trade_date")
            with _conn(self.db_path) as c:
                rows = c.execute(sql, (str(start), str(end))).fetchall()
            self._cache[key] = [str(r[0]) for r in rows]
        return self._cache[key]

    # ---------------- 指数 ----------------

    def index_df(self, code: str, start: str = '', end: str = '29991231') -> pd.DataFrame:
        key = ('idx', code, start, end)
        if key in self._cache:
            return self._cache[key]
        cols = ','.join(INDEX_COLS)
        sql = (f"SELECT {cols} FROM index_daily_cache "
               "WHERE ts_code=? AND trade_date>=? AND trade_date<=? ORDER BY trade_date")
        with _conn(self.db_path) as c:
            df = pd.read_sql(sql, c, params=(code, str(start), str(end)))
        for col in ('open', 'high', 'low', 'close', 'pre_close', 'pct_chg', 'vol', 'amount'):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(subset=['close']).reset_index(drop=True)
        self._cache[key] = df
        return df

    def index_close(self, code: str, start: str = '', end: str = '29991231') -> pd.Series:
        """按 trade_date 升序的收盘 Series"""
        df = self.index_df(code, start, end)
        if df is None or df.empty:
            return pd.Series(dtype='float64')
        return df.set_index('trade_date')['close'].astype('float64')

    def index_ohlc(self, code: str) -> pd.DataFrame:
        """index=trade_date，列 close/high/low/vol/amount/pct_chg"""
        df = self.index_df(code)
        if df is None or df.empty:
            return pd.DataFrame()
        out = df.set_index('trade_date')[['close', 'high', 'low', 'vol', 'amount', 'pct_chg']]
        return out.astype('float64').sort_index()

    def available_indices(self) -> list:
        with _conn(self.db_path) as c:
            rows = c.execute("SELECT DISTINCT ts_code FROM index_daily_cache ORDER BY ts_code").fetchall()
        return [r[0] for r in rows]

    # ---------------- 个股面板 ----------------

    def market_panel(self, start: str, end: str) -> pd.DataFrame:
        """全市场（沪深A，剔北交所）日线长表

        Returns: DataFrame[ts_code, trade_date, close, high, low, open, vol, amount, pct_chg]
        """
        key = ('panel', start, end)
        if key in self._cache:
            return self._cache[key]
        sql = ("SELECT ts_code, trade_date, close, high, low, open, vol, amount, pct_chg "
               "FROM daily_cache WHERE trade_date>=? AND trade_date<=? "
               "AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ')")
        with _conn(self.db_path) as c:
            df = pd.read_sql(sql, c, params=(str(start), str(end)))
        df['trade_date'] = df['trade_date'].astype(str)
        for col in ('close', 'high', 'low', 'open', 'vol', 'amount', 'pct_chg'):
            df[col] = pd.to_numeric(df[col], errors='coerce').astype('float32')
        df = df[df['close'] > 0]
        self._cache[key] = df
        return df

    def mv_panel(self, start: str, end: str) -> pd.DataFrame:
        """全市场 total_mv 长表（用于动态小盘池）"""
        key = ('mv', start, end)
        if key in self._cache:
            return self._cache[key]
        sql = ("SELECT ts_code, trade_date, total_mv FROM daily_basic_cache "
               "WHERE trade_date>=? AND trade_date<=? AND total_mv IS NOT NULL")
        with _conn(self.db_path) as c:
            df = pd.read_sql(sql, c, params=(str(start), str(end)))
        df['trade_date'] = df['trade_date'].astype(str)
        df['total_mv'] = pd.to_numeric(df['total_mv'], errors='coerce').astype('float32')
        df = df[df['total_mv'] > 0]
        self._cache[key] = df
        return df

    def stock_basic(self) -> pd.DataFrame:
        """ts_code / name / list_date（用于 ST 剔除与上市天数过滤）"""
        if 'basic' in self._cache:
            return self._cache['basic']
        try:
            import stock_cache as sc
            sb = sc.load_stock_basic()
        except Exception:
            sb = None
        if sb is None or sb.empty:
            sb = pd.DataFrame(columns=['ts_code', 'name', 'list_date'])
        sb = sb[['ts_code', 'name', 'list_date']].copy() if 'list_date' in sb.columns \
            else sb.assign(list_date='')
        sb['list_date'] = sb['list_date'].fillna('').astype(str)
        self._cache['basic'] = sb
        return sb

    # ---------------- 中证2000 成分股快照 ----------------

    def members(self) -> pd.DataFrame:
        """index_member_cache: [index_code, trade_date, con_code, weight]"""
        if 'members' in self._cache:
            return self._cache['members']
        if not self._table_exists(C.MEMBER_TABLE):
            self._cache['members'] = pd.DataFrame(columns=['index_code', 'trade_date', 'con_code', 'weight'])
            return self._cache['members']
        with _conn(self.db_path) as c:
            df = pd.read_sql(f"SELECT * FROM {C.MEMBER_TABLE}", c)
        if not df.empty:
            df['trade_date'] = df['trade_date'].astype(str)
        self._cache['members'] = df
        return df

    def member_snapshot_map(self, index_code: str = C.CORE_INDEX) -> dict:
        """{快照日: set(con_code)}"""
        m = self.members()
        if m.empty:
            return {}
        m = m[m['index_code'] == index_code]
        return {d: set(g['con_code'].astype(str)) for d, g in m.groupby('trade_date')}

    def _table_exists(self, name: str) -> bool:
        with _conn(self.db_path) as c:
            row = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        return row is not None

    # ---------------- 拉取成分股快照（唯一联网入口） ----------------

    def fetch_members(self, index_code: str = C.CORE_INDEX,
                      start: str = '20200101', end: str = None, silent: bool = False) -> int:
        """逐月拉取指数成分股权重快照并落盘到 index_member_cache（幂等）

        中证2000 官方成分快照仅在指数发布后可见，本方法逐月探测：
        有数据的月份写入，无数据的月份跳过（不伪造、不留空行）。
        """
        import stock_cache as sc
        pro = sc._get_pro()
        end = str(end or C.DATA_END)
        # 已落盘的快照月直接跳过（幂等续跑）
        existing = set(self.member_snapshot_map(index_code).keys())
        months = pd.date_range(start=f'{start[:4]}-{start[4:6]}-01',
                               end=f'{end[:4]}-{end[4:6]}-01', freq='MS')
        frames, n_new = [], 0
        for ms in months:
            m_start = ms.strftime('%Y%m%d')
            m_end = (ms + pd.offsets.MonthEnd(0)).strftime('%Y%m%d')
            if any(m_start <= d <= m_end for d in existing):
                continue
            try:
                df = pro.index_weight(index_code=index_code,
                                      start_date=m_start, end_date=m_end)
                time.sleep(0.12)
            except Exception as e:
                if not silent:
                    print(f'[members] {m_start} 拉取失败: {e}')
                continue
            if df is None or df.empty:
                continue
            df = df.rename(columns={'con_code': 'con_code'})
            df['trade_date'] = df['trade_date'].astype(str)
            df = df[df['trade_date'] >= m_start]
            df = df[df['trade_date'] <= m_end]
            if df.empty:
                continue
            frames.append(df[['index_code', 'trade_date', 'con_code', 'weight']])
            n_new += 1
            if not silent:
                print(f'[members] {df["trade_date"].min()} 快照 {len(df)} 只')
        if frames:
            allnew = pd.concat(frames, ignore_index=True)
            self._write_members(allnew)
            self._cache.pop('members', None)
        if not silent:
            print(f'[members] 新增快照月 {n_new} 个，累计快照 '
                  f'{len(self.member_snapshot_map(index_code))} 期')
        return n_new

    def _write_members(self, df: pd.DataFrame):
        import stock_cache as sc
        sc._ensure_table_from_df(df, C.MEMBER_TABLE,
                                 pk_cols=('index_code', 'trade_date', 'con_code'))
        cols = ['index_code', 'trade_date', 'con_code', 'weight']
        placeholders = ','.join(['?'] * len(cols))
        sql = (f'INSERT OR REPLACE INTO "{C.MEMBER_TABLE}" '
               f'({",".join(cols)}) VALUES ({placeholders})')
        vals = [[None if pd.isna(v) else v for v in row] for row in df[cols].values.tolist()]
        with _conn(self.db_path) as c:
            c.executemany(sql, vals)
            c.commit()

    # ---------------- 覆盖率自检 ----------------

    def coverage_report(self) -> dict:
        """数据完整性报告（§2 DATA_INCOMPLETE 判定依据）"""
        out = {}
        with _conn(self.db_path) as c:
            row = c.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM daily_cache").fetchone()
            out['daily_cache'] = {'min': str(row[0]), 'max': str(row[1]), 'rows': int(row[2])}
            row = c.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*) "
                            "FROM daily_basic_cache").fetchone()
            out['daily_basic_cache'] = {'min': str(row[0]), 'max': str(row[1]), 'rows': int(row[2])}
            rows = c.execute("SELECT ts_code, MIN(trade_date), MAX(trade_date), COUNT(*) "
                             "FROM index_daily_cache GROUP BY ts_code").fetchall()
            out['index_daily_cache'] = {r[0]: {'min': str(r[1]), 'max': str(r[2]), 'rows': int(r[3])}
                                        for r in rows}
        snaps = self.member_snapshot_map()
        out['member_snapshots'] = {
            'n': len(snaps),
            'first': min(snaps) if snaps else None,
            'last': max(snaps) if snaps else None,
        }
        # DATA_INCOMPLETE 判定
        gaps = []
        if out['daily_cache']['min'] > C.TARGET_START:
            gaps.append(
                f"个股日线本地缓存起点 {out['daily_cache']['min']}，"
                f"§22 要求的 2018~2020 无数据（{C.TARGET_START}~{C.DATA_START} 缺口，无法回补）")
        if not snaps:
            gaps.append('中证2000 成分股快照缺失，宽度无法按官方成分计算')
        elif min(snaps) > C.DATA_START:
            gaps.append(
                f"中证2000 官方成分快照最早仅 {min(snaps)}（指数 2023-08 才发布），"
                f"{C.DATA_START}~{min(snaps)} 宽度改用动态小盘池（universe_src=DYNAMIC）")
        out['gaps'] = gaps
        out['data_incomplete'] = bool(gaps)
        return out


_shared = None


def shared() -> GateData:
    global _shared
    if _shared is None:
        _shared = GateData()
    return _shared


if __name__ == '__main__':
    import json
    print(json.dumps(shared().coverage_report(), ensure_ascii=False, indent=2))
