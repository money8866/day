#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据加载模块 - 复用项目已有缓存数据
数据源优先级: 本地parquet > SQLite(stock_data.db) > Tushare API
"""
import os
import sys
import glob
import time
import json
import sqlite3
import pickle
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


def get_last_trade_date() -> str:
    """获取最近交易日 YYYYMMDD"""
    now = datetime.now()
    if now.hour < 15:
        q = (now - timedelta(days=1)).strftime('%Y%m%d')
    else:
        q = now.strftime('%Y%m%d')
    # 周末回退
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
    """统一数据加载器, 复用项目缓存"""

    def __init__(self):
        self.theme_stocks: Dict[str, List[Tuple[str, str, str]]] = {}  # theme -> [(code, name, layer)]
        self.stock_themes: Dict[str, List[str]] = {}  # code -> [themes]
        self.daily_cache: Dict[str, pd.DataFrame] = {}  # code -> daily df
        self.factor_cache: Dict[str, pd.DataFrame] = {}  # code -> factor df
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
    # 日线数据加载 (parquet优先)
    # ═══════════════════════════════════════════
    def load_daily(self, ts_code: str, start: str = '20240101', end: str = None) -> Optional[pd.DataFrame]:
        """
        加载个股日线数据
        优先从parquet缓存读取, 缺失时从Tushare补充
        返回: DataFrame(trade_date, open, high, low, close, vol, amount, pct_chg, ...)
        """
        if end is None:
            end = get_last_trade_date()

        # 内存缓存
        cache_key = f"{ts_code}_{start}_{end}"
        if cache_key in self.daily_cache:
            return self.daily_cache[cache_key]

        df = self._load_daily_from_parquet(ts_code, start, end)
        if df is None or df.empty:
            df = self._load_daily_from_tushare(ts_code, start, end)

        if df is not None and not df.empty:
            df = df.sort_values('trade_date').reset_index(drop=True)
            self.daily_cache[cache_key] = df
            return df
        return None

    def _load_daily_from_parquet(self, ts_code: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """从parquet缓存加载日线"""
        # 文件名格式: daily_code_000001_SZ_20240101_20260620.parquet
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

    def _load_daily_from_tushare(self, ts_code: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """从Tushare加载日线(带频率控制)"""
        if not TS_AVAILABLE:
            return None
        try:
            time.sleep(0.15)
            df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
            if df is not None and not df.empty:
                return df.sort_values('trade_date').reset_index(drop=True)
        except Exception:
            pass
        return None

    def load_daily_batch(self, codes: List[str], start: str, end: str) -> Dict[str, pd.DataFrame]:
        """批量加载日线数据"""
        result = {}
        for i, code in enumerate(codes):
            df = self.load_daily(code, start, end)
            if df is not None and not df.empty:
                result[code] = df
            if (i + 1) % 100 == 0:
                print(f"  日线加载进度: {i+1}/{len(codes)}")
        return result

    # ═══════════════════════════════════════════
    # 技术因子加载 (stock_data.db)
    # ═══════════════════════════════════════════
    def load_factors(self, ts_code: str, trade_date: str) -> Optional[pd.Series]:
        """加载指定股票指定日期的技术因子"""
        if self._factor_conn is None:
            if not os.path.exists(STOCK_DATA_DB):
                return None
            self._factor_conn = sqlite3.connect(STOCK_DATA_DB, timeout=10.0)

        try:
            df = pd.read_sql_query(
                "SELECT * FROM stk_factor_pro WHERE ts_code=? AND trade_date=?",
                self._factor_conn, params=(ts_code, trade_date)
            )
            if df.empty:
                return None
            return df.iloc[0]
        except Exception:
            return None

    def load_factors_batch(self, trade_date: str, codes: List[str] = None) -> pd.DataFrame:
        """批量加载某日所有股票技术因子"""
        if self._factor_conn is None:
            if not os.path.exists(STOCK_DATA_DB):
                return pd.DataFrame()
            self._factor_conn = sqlite3.connect(STOCK_DATA_DB, timeout=10.0)

        try:
            if codes:
                placeholders = ','.join(['?' for _ in codes])
                df = pd.read_sql_query(
                    f"SELECT * FROM stk_factor_pro WHERE trade_date=? AND ts_code IN ({placeholders})",
                    self._factor_conn, params=[trade_date] + codes
                )
            else:
                df = pd.read_sql_query(
                    "SELECT * FROM stk_factor_pro WHERE trade_date=?",
                    self._factor_conn, params=(trade_date,)
                )
            return df
        except Exception as e:
            print(f"⚠ 技术因子加载失败: {e}")
            return pd.DataFrame()

    def get_factor_dates(self, limit: int = 10) -> List[str]:
        """获取最近N个有因子数据的交易日"""
        if self._factor_conn is None:
            if not os.path.exists(STOCK_DATA_DB):
                return []
            self._factor_conn = sqlite3.connect(STOCK_DATA_DB, timeout=10.0)
        try:
            rows = self._factor_conn.execute(
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
            # daily_code_000001_SZ_20240101_20260620.parquet
            parts = basename.replace('.parquet', '').split('_')
            if len(parts) >= 4:
                code = parts[2]  # 000001
                market = parts[3]  # SZ
                codes.append(f"{code}.{market}")
        return codes

    def close(self):
        """关闭数据库连接"""
        if self._factor_conn:
            self._factor_conn.close()
            self._factor_conn = None
