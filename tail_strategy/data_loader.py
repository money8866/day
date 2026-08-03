#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据加载模块 - 遵循 daily_cache 统一缓存最佳实践
数据源优先级: daily_cache(stock_cache.py) > Tushare API(写回daily_cache) > parquet兜底

三种调用模式:
  1. load_daily()        单只股票区间查询
  2. load_market_daily() 全市场单日查询
  3. load_daily_batch()  批量多只股票查询
"""
import os
import sys
import glob
import time
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DAILY = os.path.join(PROJECT_ROOT, "cache_daily")
PARQUET_DIR = os.path.join(CACHE_DAILY, "parquet")
STOCK_DATA_DB = os.path.join(CACHE_DAILY, "stock_data.db")
THEME_MAP_JSON = os.path.join(CACHE_DAILY, "theme_stock_map_latest.json")

# 环境变量
from dotenv import load_dotenv
for _env_path in (
    os.path.join(PROJECT_ROOT, 'config', '.env'),
    os.path.join(PROJECT_ROOT, '.env'),
):
    if os.path.exists(_env_path):
        load_dotenv(_env_path)
        break

# Tushare
try:
    import tushare as ts
    pro = ts.pro_api(os.getenv('TUSHARE_TOKEN'))
    TS_AVAILABLE = True
except Exception:
    TS_AVAILABLE = False

# daily_cache 统一缓存 API (solo/stock_cache.py)
_SOLO_DIR = os.path.join(PROJECT_ROOT, 'solo')
if _SOLO_DIR not in sys.path:
    sys.path.insert(0, _SOLO_DIR)
try:
    from stock_cache import (
        get_daily_cache, get_daily_cache_range,
        get_daily_by_date, get_daily_by_date_count,
        batch_insert_daily_cache,
    )
    STOCK_CACHE_AVAILABLE = True
except Exception:
    STOCK_CACHE_AVAILABLE = False


def get_last_trade_date() -> str:
    """获取最近交易日 YYYYMMDD"""
    now = datetime.now()
    if now.hour < 15:
        q = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        q = now.strftime('%Y%m%d')
    dt = datetime.strptime(q, '%Y%m%d')
    while dt.weekday() >= 5:  # 5=Sat, 6=Sun
        dt -= timedelta(days=1)
    return dt.strftime('%Y%m%d')


def get_trade_dates(start: str, end: str) -> List[str]:
    """获取区间内交易日列表"""
    if TS_AVAILABLE:
        try:
            df = pro.trade_cal(exchange='SSE', start_date=start, end_date=end,
                               fields='cal_date,is_open')
            return sorted(df[df['is_open'] == 1]['cal_date'].tolist())
        except Exception:
            pass
    # 回退: 从parquet文件推断
    dates = set()
    sample_files = glob.glob(os.path.join(PARQUET_DIR, "daily_code_000001_SZ_*.parquet"))
    if sample_files:
        df = pd.read_parquet(sample_files[0], columns=['trade_date'])
        dates = set(df['trade_date'].astype(str).tolist())
    return sorted([d for d in dates if start <= d <= end])


class DataLoader:
    """统一数据加载器 - daily_cache 优先, API兜底并写回"""

    def __init__(self):
        self.theme_stocks: Dict[str, List[Tuple[str, str, str]]] = {}  # theme -> [(code, name, layer)]
        self.stock_themes: Dict[str, List[str]] = {}  # code -> [themes]
        self._mem_cache: Dict[str, pd.DataFrame] = {}  # 进程内内存缓存
        self._factor_conn = None

    # ═══════════════════════════════════════════
    # 主题映射加载
    # ═══════════════════════════════════════════
    def load_theme_map(self) -> bool:
        """从 theme_stock_map_latest.json 加载主题-股票映射"""
        if not os.path.exists(THEME_MAP_JSON):
            print(f"❌ 主题映射文件不存在: {THEME_MAP_JSON}")
            return False

        with open(THEME_MAP_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)

        themes_data = data.get('themes', {})
        if not themes_data:
            print("❌ 主题映射为空")
            return False

        for theme_name, stocks in themes_data.items():
            self.theme_stocks[theme_name] = []
            for stock_info in stocks:
                ts_code = stock_info.get('code', '')
                name = stock_info.get('name', '')
                via = stock_info.get('via', '')
                layer = 'leader' if via == 'leader_company' else ('middle' if via == 'core_company' else 'member')
                self.theme_stocks[theme_name].append((ts_code, name, layer))

                if ts_code not in self.stock_themes:
                    self.stock_themes[ts_code] = []
                if theme_name not in self.stock_themes[ts_code]:
                    self.stock_themes[ts_code].append(theme_name)

        total = sum(len(v) for v in self.theme_stocks.values())
        print(f"✅ 主题映射加载: {len(self.theme_stocks)}个主题, {len(self.stock_themes)}只股票, {total}只次")
        return True

    # ═══════════════════════════════════════════
    # 模式1: 单只股票区间查询 (daily_cache优先)
    # ═══════════════════════════════════════════
    def load_daily(self, ts_code: str, start: str = '20240101', end: str = None) -> Optional[pd.DataFrame]:
        """
        加载个股日线数据
        ① daily_cache 表 → ② Tushare API(写回daily_cache) → ③ parquet兜底
        返回: DataFrame(trade_date, open, high, low, close, vol, amount, pct_chg, ...)
        """
        if end is None:
            end = get_last_trade_date()

        cache_key = f"{ts_code}_{start}_{end}"
        if cache_key in self._mem_cache:
            return self._mem_cache[cache_key]

        df = None
        # ① 优先读 daily_cache 表
        if STOCK_CACHE_AVAILABLE:
            try:
                _, max_date = get_daily_cache_range(ts_code)
                if max_date is not None and str(max_date) >= str(end):
                    df = get_daily_cache(ts_code, start, end)
                    if df is not None and not df.empty:
                        df['trade_date'] = df['trade_date'].astype(str)
            except Exception:
                pass

        # ② 缓存未命中 → 调API并写回
        if (df is None or df.empty) and TS_AVAILABLE:
            try:
                time.sleep(0.15)
                api_df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
                if api_df is not None and not api_df.empty:
                    try:
                        batch_insert_daily_cache(api_df)
                    except Exception:
                        pass
                    df = api_df
            except Exception:
                pass

        # ③ parquet 兜底
        if df is None or df.empty:
            df = self._load_daily_from_parquet(ts_code, start, end)

        if df is not None and not df.empty:
            df['trade_date'] = df['trade_date'].astype(str)
            df = df.sort_values('trade_date').reset_index(drop=True)
            df = df[(df['trade_date'] >= start) & (df['trade_date'] <= end)]
            self._mem_cache[cache_key] = df
            return df
        return None

    # ═══════════════════════════════════════════
    # 模式2: 全市场单日查询 (daily_cache优先)
    # ═══════════════════════════════════════════
    def load_market_daily(self, trade_date: str) -> pd.DataFrame:
        """
        加载全市场某日日线 (替代 pro.daily(trade_date=...))
        ① daily_cache 表 → ② Tushare API(写回daily_cache)
        """
        df = None
        if STOCK_CACHE_AVAILABLE:
            try:
                if get_daily_by_date_count(trade_date) > 0:
                    df = get_daily_by_date(trade_date)
            except Exception:
                pass

        if (df is None or df.empty) and TS_AVAILABLE:
            try:
                api_df = pro.daily(trade_date=trade_date)
                if api_df is not None and not api_df.empty:
                    try:
                        batch_insert_daily_cache(api_df)
                    except Exception:
                        pass
                    df = api_df
            except Exception:
                pass

        if df is not None and not df.empty:
            df['trade_date'] = df['trade_date'].astype(str)
        return df if df is not None else pd.DataFrame()

    # ═══════════════════════════════════════════
    # 模式3: 批量多只股票查询
    # ═══════════════════════════════════════════
    def load_daily_batch(self, codes: List[str], start: str, end: str) -> pd.DataFrame:
        """
        批量加载日线: 逐只检查缓存, 未命中合并API一次取回并写回
        返回合并后的 DataFrame
        """
        cached_parts = []
        missing = []

        if STOCK_CACHE_AVAILABLE:
            for code in codes:
                try:
                    _, max_date = get_daily_cache_range(code)
                    if max_date is not None and str(max_date) >= str(end):
                        c = get_daily_cache(code, start, end)
                        if c is not None and not c.empty:
                            cached_parts.append(c)
                            continue
                except Exception:
                    pass
                missing.append(code)

            if missing and TS_AVAILABLE:
                # tushare批量接口单次上限有限, 分批合并
                batch_size = 500
                for i in range(0, len(missing), batch_size):
                    chunk = missing[i:i + batch_size]
                    try:
                        batch_df = pro.daily(ts_code=','.join(chunk),
                                             start_date=start, end_date=end)
                        if batch_df is not None and not batch_df.empty:
                            try:
                                batch_insert_daily_cache(batch_df)
                            except Exception:
                                pass
                            cached_parts.append(batch_df)
                        time.sleep(0.3)
                    except Exception:
                        pass
        else:
            # 无 stock_cache 时逐只走 load_daily
            for code in codes:
                df = self.load_daily(code, start, end)
                if df is not None and not df.empty:
                    cached_parts.append(df)

        if not cached_parts:
            return pd.DataFrame()
        result = pd.concat(cached_parts, ignore_index=True)
        result['trade_date'] = result['trade_date'].astype(str)
        return result.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)

    def _load_daily_from_parquet(self, ts_code: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """从parquet缓存加载日线(兜底)"""
        code_part = ts_code.replace('.', '_')  # 000001_SZ
        pattern = os.path.join(PARQUET_DIR, f"daily_code_{code_part}_*.parquet")
        files = glob.glob(pattern)
        if not files:
            return None
        try:
            df = pd.read_parquet(files[0])
            df['trade_date'] = df['trade_date'].astype(str)
            df = df[(df['trade_date'] >= start) & (df['trade_date'] <= end)]
            return df
        except Exception:
            return None

    # ═══════════════════════════════════════════
    # daily_basic (换手率/市值) - API + 本地表缓存
    # ═══════════════════════════════════════════
    def load_daily_basic(self, trade_date: str) -> pd.DataFrame:
        """
        加载全市场某日 daily_basic (turnover_rate, total_mv等)
        ① stock_data.db daily_basic_cache 表 → ② Tushare API(写回)
        """
        conn = self._get_factor_conn()
        if conn is not None:
            try:
                df = pd.read_sql_query(
                    "SELECT * FROM daily_basic_cache WHERE trade_date=?",
                    conn, params=(str(trade_date),)
                )
                if not df.empty:
                    return df
            except Exception:
                pass

        if TS_AVAILABLE:
            try:
                api_df = pro.daily_basic(trade_date=trade_date,
                                         fields='ts_code,trade_date,turnover_rate,volume_ratio,total_mv,circ_mv')
                if api_df is not None and not api_df.empty:
                    try:
                        self._save_daily_basic(api_df)
                    except Exception:
                        pass
                    return api_df
            except Exception:
                pass
        return pd.DataFrame()

    def _save_daily_basic(self, df: pd.DataFrame):
        """写回 daily_basic_cache 表"""
        conn = self._get_factor_conn()
        if conn is None or df is None or df.empty:
            return
        cols = [c for c in ('ts_code', 'trade_date', 'turnover_rate',
                            'volume_ratio', 'total_mv', 'circ_mv') if c in df.columns]
        df_v = df[cols].copy()
        df_v['trade_date'] = df_v['trade_date'].astype(str)
        col_defs = ', '.join(
            f'{c} {"TEXT" if c in ("ts_code", "trade_date") else "REAL"}' for c in cols
        )
        conn.execute(f"CREATE TABLE IF NOT EXISTS daily_basic_cache ({col_defs}, "
                     f"PRIMARY KEY(ts_code, trade_date))")
        placeholders = ','.join(['?'] * len(cols))
        col_str = ','.join(cols)
        values = [[None if pd.isna(v) else v for v in row]
                  for row in df_v[cols].values.tolist()]
        conn.executemany(
            f"INSERT OR REPLACE INTO daily_basic_cache ({col_str}) VALUES ({placeholders})",
            values
        )
        conn.commit()

    # ═══════════════════════════════════════════
    # stk_factor_pro 因子查询 (已有缓存直接用)
    # ═══════════════════════════════════════════
    def _get_factor_conn(self):
        if self._factor_conn is None:
            if not os.path.exists(STOCK_DATA_DB):
                return None
            self._factor_conn = sqlite3.connect(STOCK_DATA_DB, timeout=10.0)
        return self._factor_conn

    def load_factors(self, ts_code: str, trade_date: str) -> Optional[pd.Series]:
        """加载指定股票指定日期的技术因子(stk_factor_pro)"""
        conn = self._get_factor_conn()
        if conn is None:
            return None
        try:
            df = pd.read_sql_query(
                "SELECT * FROM stk_factor_pro WHERE ts_code=? AND trade_date=?",
                conn, params=(ts_code, str(trade_date))
            )
            if df.empty:
                return None
            return df.iloc[0]
        except Exception:
            return None

    def load_factors_batch(self, trade_date: str, codes: List[str] = None) -> pd.DataFrame:
        """批量加载某日所有股票技术因子(stk_factor_pro)"""
        conn = self._get_factor_conn()
        if conn is None:
            return pd.DataFrame()
        try:
            if codes:
                placeholders = ','.join(['?' for _ in codes])
                df = pd.read_sql_query(
                    f"SELECT * FROM stk_factor_pro WHERE trade_date=? AND ts_code IN ({placeholders})",
                    conn, params=[str(trade_date)] + codes
                )
            else:
                df = pd.read_sql_query(
                    "SELECT * FROM stk_factor_pro WHERE trade_date=?",
                    conn, params=(str(trade_date),)
                )
            return df
        except Exception as e:
            print(f"⚠ 技术因子加载失败: {e}")
            return pd.DataFrame()

    def get_factor_dates(self, limit: int = 10) -> List[str]:
        """获取最近N个有因子数据的交易日"""
        conn = self._get_factor_conn()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT DISTINCT trade_date FROM stk_factor_pro ORDER BY trade_date DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [r[0] for r in rows]
        except Exception:
            return []

    # ═══════════════════════════════════════════
    # 全市场股票列表
    # ═══════════════════════════════════════════
    def get_all_stock_codes(self) -> List[str]:
        """获取所有有parquet缓存的股票代码"""
        files = glob.glob(os.path.join(PARQUET_DIR, "daily_code_*.parquet"))
        codes = []
        for f in files:
            basename = os.path.basename(f)
            parts = basename.replace('.parquet', '').split('_')
            if len(parts) >= 4:
                code = parts[2]
                market = parts[3]
                codes.append(f"{code}.{market}")
        return codes

    def close(self):
        """关闭数据库连接"""
        if self._factor_conn:
            self._factor_conn.close()
            self._factor_conn = None
