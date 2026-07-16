#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ETF Alpha Engine - 数据加载模块
================================
复用现有缓存接口：
  - 日线数据: d:/mystock/cache_daily/{code}.csv
  - 主题映射: theme_stock_map_latest.json
  - DC热度: cache_backbone_tushare/dc_hot/dc_hot_{date}.csv
  - moneyflow: theme_alpha_v6/cache/parquet/moneyflow_{date}.parquet
  - 涨停: theme_alpha_v6/cache/parquet/limit_list_{date}.parquet
  - 龙虎榜: theme_alpha_v6/cache/parquet/top_list_{date}.parquet
  - 指数行情: cache_daily/{code}.csv
"""
import os
import re
import sys
import time
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yaml

warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config(config_path: str = None) -> dict:
    """加载YAML配置"""
    if config_path is None:
        config_path = os.path.join(BASE_DIR, "config.yaml")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class DataLoader:
    """统一数据加载器，复用现有缓存接口"""

    def __init__(self, config: dict):
        self.cfg = config.get("data", {})
        self.daily_cache = self.cfg.get("daily_cache_path", "d:/mystock/cache_daily")
        self.theme_map_json = self.cfg.get("theme_map_json",
                                          "d:/mystock/cache_daily/theme_stock_map_latest.json")
        self.dc_hot_dir = self.cfg.get("dc_hot_cache_dir",
                                       "d:/mystock/solo/cache_backbone_tushare/dc_hot")
        self.parquet_dir = self.cfg.get("parquet_dir",
                                        "d:/mystock/solo/theme_alpha_v6/cache/parquet")
        self.moneyflow_dir = self.cfg.get("moneyflow_dir",
                                          "d:/mystock/solo/theme_alpha_v6/cache/moneyflow")
        self.mf_lookback = self.cfg.get("moneyflow_lookback_days", 15)
        os.makedirs(self.parquet_dir, exist_ok=True)
        os.makedirs(self.moneyflow_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # 交易日
    # ------------------------------------------------------------------
    def get_last_trade_date(self) -> str:
        """获取最新交易日（16点前用上一交易日）"""
        now = datetime.now()
        if now.hour < 16:
            target = now - timedelta(days=1)
        else:
            target = now
        while target.weekday() >= 5:
            target -= timedelta(days=1)
        return target.strftime("%Y%m%d")

    def get_trade_dates(self, start_date: str, end_date: str) -> List[str]:
        """从缓存日线推断交易日序列"""
        return sorted(self._load_all_dates_from_cache(start_date, end_date))

    def _load_all_dates_from_cache(self, start_date: str, end_date: str) -> set:
        """从缓存日线扫描交易日（用第一只可用股票）"""
        dates = set()
        files = [f for f in os.listdir(self.daily_cache)
                 if f.endswith(".csv") and not f.startswith("daily_basic") and re.match(r'^\d{6}\.(SH|SZ|BJ)\.csv$', f) and not re.match(r'^(000\d{3}\.SH|399\d{3}\.SZ)\.csv$', f)]
        for fname in files[:30]:
            try:
                df = pd.read_csv(os.path.join(self.daily_cache, fname), nrows=5000)
                if "trade_date" in df.columns:
                    df["trade_date"] = df["trade_date"].astype(str)
                    mask = (df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)
                    dates.update(df.loc[mask, "trade_date"].tolist())
                if len(dates) > 200:
                    break
            except Exception:
                continue
        return dates

    # ------------------------------------------------------------------
    # 日线数据
    # ------------------------------------------------------------------
    def load_daily(self, codes: List[str], start_date: str, end_date: str) -> pd.DataFrame:
        """批量加载日线数据（复用 cache_daily CSV缓存）"""
        frames = []
        for code in codes:
            fp = os.path.join(self.daily_cache, f"{code}.csv")
            if not os.path.exists(fp):
                continue
            try:
                df = pd.read_csv(fp)
                df["trade_date"] = df["trade_date"].astype(str)
                mask = (df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)
                cols = ["ts_code", "trade_date", "open", "high", "low",
                        "close", "vol", "amount", "pct_chg"]
                cols = [c for c in cols if c in df.columns]
                df = df.loc[mask, cols].copy()
                if len(df) >= 20:
                    frames.append(df)
            except Exception:
                continue
        if not frames:
            return pd.DataFrame()
        full = pd.concat(frames, ignore_index=True)
        return full.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    def load_single_daily(self, code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """加载单只标的日线"""
        fp = os.path.join(self.daily_cache, f"{code}.csv")
        if not os.path.exists(fp):
            return pd.DataFrame()
        try:
            df = pd.read_csv(fp)
            df["trade_date"] = df["trade_date"].astype(str)
            mask = (df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)
            return df.loc[mask].sort_values("trade_date").reset_index(drop=True)
        except Exception:
            return pd.DataFrame()

    def load_etf_data(self, etf_codes: List[str],
                      start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
        """加载ETF日线，返回 {code: df}"""
        result = {}
        for code in etf_codes:
            df = self.load_single_daily(code, start_date, end_date)
            if not df.empty:
                result[code] = df
        return result

    # ------------------------------------------------------------------
    # 主题-股票映射
    # ------------------------------------------------------------------
    def load_theme_universe(self) -> Dict[str, List[str]]:
        """加载主题-股票映射（从 theme_stock_map_latest.json）"""
        if not os.path.exists(self.theme_map_json):
            return {}
        with open(self.theme_map_json, "r", encoding="utf-8") as f:
            raw = json_load(f)
        themes_raw = raw.get("themes", {})
        universe = {}
        min_stocks = 5
        for tname, stk_list in themes_raw.items():
            codes = []
            for s in stk_list:
                code = s.get("code") if isinstance(s, dict) else str(s)
                if code:
                    codes.append(code)
            if len(codes) >= min_stocks:
                universe[tname] = codes
        return universe

    def load_etf_theme_map(self) -> Dict[str, str]:
        """加载ETF-主题映射（从config.yaml的etf_universe）"""
        config = load_config()
        return config.get("etf_universe", {})

    # ------------------------------------------------------------------
    # 指数行情
    # ------------------------------------------------------------------
    def load_index(self, ts_code: str = "000300.SH",
                   start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """加载指数日线"""
        fp = os.path.join(self.daily_cache, f"{ts_code.replace('.', '_')}.csv")
        if os.path.exists(fp):
            try:
                df = pd.read_csv(fp)
                df["trade_date"] = df["trade_date"].astype(str)
                if start_date and end_date:
                    mask = (df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)
                    df = df.loc[mask]
                return df.sort_values("trade_date").reset_index(drop=True)
            except Exception:
                pass
        return pd.DataFrame()

    # ------------------------------------------------------------------
    # 全市场日线（用于市场宽度计算）
    # ------------------------------------------------------------------
    def load_market_daily(self, trade_date: str) -> pd.DataFrame:
        """加载某交易日全市场日线快照（用于宽度计算）
        从cache_daily扫描所有股票当日数据
        """
        rows = []
        files = [f for f in os.listdir(self.daily_cache)
                 if f.endswith(".csv")
                 and not f.startswith("daily_basic")
                 and not f.startswith("0003")  # 排除指数
                 and not f.startswith("0000")]
        for fname in files:
            try:
                fp = os.path.join(self.daily_cache, fname)
                df = pd.read_csv(fp)
                if "trade_date" not in df.columns:
                    continue
                df["trade_date"] = df["trade_date"].astype(str)
                day_df = df[df["trade_date"] == trade_date]
                if not day_df.empty:
                    rows.append(day_df)
            except Exception:
                continue
            if len(rows) % 500 == 0 and rows:
                pass
        if not rows:
            return pd.DataFrame()
        full = pd.concat(rows, ignore_index=True)
        return full

    def load_market_daily_recent(self, n_days: int = 5, trade_date: str = None) -> pd.DataFrame:
        """加载最近N个交易日的全市场日线（用于市场趋势/宽度计算）

        性能优化：
          1. 优先读取 parquet 快照缓存（按日期切片，秒级加载）
          2. 仅在缓存不存在或过期时回退到全量CSV扫描
          3. 扫描结果写回 parquet 缓存，供下次使用

        Args:
            n_days: 加载最近N个交易日
            trade_date: 基准日期，None=当前最新。指定时取该日期及之前的最近N个交易日
        """
        cache_dir = os.path.join(self.daily_cache, "_snapshot_cache")
        os.makedirs(cache_dir, exist_ok=True)

        # 1) 先从少量文件推断交易日列表
        all_dates = set()
        files = [f for f in os.listdir(self.daily_cache)
                 if f.endswith(".csv") and not f.startswith("daily_basic") and re.match(r'^\d{6}\.(SH|SZ|BJ)\.csv$', f) and not re.match(r'^(000\d{3}\.SH|399\d{3}\.SZ)\.csv$', f)]
        for fname in files[:50]:
            try:
                df = pd.read_csv(os.path.join(self.daily_cache, fname),
                                 usecols=["trade_date"], nrows=10000)
                if "trade_date" in df.columns:
                    all_dates.update(df["trade_date"].astype(str).tolist())
            except Exception:
                continue
        if not all_dates:
            return pd.DataFrame()
        sorted_dates = sorted(all_dates)
        if trade_date:
            # 取trade_date及之前的最近N个交易日
            candidates = [d for d in sorted_dates if d <= trade_date]
            if not candidates:
                return pd.DataFrame()
            recent_dates = candidates[-n_days:]
        else:
            recent_dates = sorted_dates[-n_days:]
        recent_set = set(recent_dates)

        # 2) 尝试 parquet 快照缓存：检查是否覆盖所需日期
        cached_frames = []
        missing_dates = set(recent_set)
        for d in recent_dates:
            fp = os.path.join(cache_dir, f"snapshot_{d}.parquet")
            if os.path.exists(fp):
                try:
                    df = pd.read_parquet(fp)
                    if not df.empty:
                        # 去重（旧缓存可能有重复数据）
                        if "ts_code" in df.columns and "trade_date" in df.columns:
                            df = df.drop_duplicates(subset=["ts_code", "trade_date"])
                        cached_frames.append(df)
                        missing_dates.discard(d)
                except Exception:
                    pass
        # 所有日期都有缓存 -> 直接返回
        if not missing_dates and len(cached_frames) == len(recent_dates):
            result = pd.concat(cached_frames, ignore_index=True)
            if "ts_code" in result.columns:
                result = result.drop_duplicates(subset=["ts_code", "trade_date"])
                result = result[~result["ts_code"].str.match(r'^(000\d{3}\.SH|399\d{3}\.SZ)$')]
            return result

        # 3) 缓存不完整：仅扫描缺失日期的CSV，按日期切片并写回 parquet
        # 历史日期扫描全部文件太慢（17239个CSV），限制扫描数量取样
        per_date_frames = {d: [] for d in missing_dates}
        scan_files = files
        if trade_date and missing_dates:
            # 历史日期（>5天前）：限制扫描文件数以提速（取样500个文件近似市场宽度）
            from datetime import datetime
            try:
                days_ago = (datetime.now() - datetime.strptime(trade_date, "%Y%m%d")).days
            except Exception:
                days_ago = 0
            if days_ago > 5:
                max_scan = 500
                if len(files) > max_scan:
                    import random
                    random.seed(42)
                    scan_files = random.sample(files, max_scan)
        for fname in scan_files:
            try:
                df = pd.read_csv(os.path.join(self.daily_cache, fname))
                if "trade_date" not in df.columns:
                    continue
                df["trade_date"] = df["trade_date"].astype(str)
                df = df[df["trade_date"].isin(missing_dates)]
                if not df.empty:
                    for d, sub in df.groupby("trade_date"):
                        if d in per_date_frames:
                            per_date_frames[d].append(sub)
            except Exception:
                continue

        # 合并并写回缓存（cached_frames + 新扫描的missing_dates）
        result_frames = []
        for d, frames in per_date_frames.items():
            if not frames:
                continue
            day_df = pd.concat(frames, ignore_index=True)
            # 去重：同一股票同日只保留一条
            day_df = day_df.drop_duplicates(subset=["ts_code", "trade_date"])
            result_frames.append(day_df)
            # 写入 parquet 缓存
            try:
                fp = os.path.join(cache_dir, f"snapshot_{d}.parquet")
                day_df.to_parquet(fp, index=False)
            except Exception:
                pass
        # 追加已有缓存帧（也需去重，旧缓存可能含重复数据）
        for df in cached_frames:
            if "ts_code" in df.columns and "trade_date" in df.columns:
                df = df.drop_duplicates(subset=["ts_code", "trade_date"])
            result_frames.append(df)

        if not result_frames:
            return pd.DataFrame()
        result = pd.concat(result_frames, ignore_index=True)
        # 排除指数代码（000xxx.SH / 399xxx.SZ），避免成交额重复计算
        if "ts_code" in result.columns:
            result = result[~result["ts_code"].str.match(r'^(000\d{3}\.SH|399\d{3}\.SZ)$')]
        return result

    # ------------------------------------------------------------------
    # DC热度
    # ------------------------------------------------------------------
    def load_dc_hot(self, trade_date: str) -> pd.DataFrame:
        """加载东方财富人气榜数据"""
        fp = os.path.join(self.dc_hot_dir, f"dc_hot_{trade_date}.csv")
        if os.path.exists(fp):
            try:
                return pd.read_csv(fp)
            except Exception:
                pass
        return pd.DataFrame()

    # ------------------------------------------------------------------
    # 涨停数据
    # ------------------------------------------------------------------
    def load_limit_list(self, trade_date: str) -> pd.DataFrame:
        """加载涨停数据"""
        fp = os.path.join(self.parquet_dir, f"limit_list_{trade_date}.parquet")
        if os.path.exists(fp):
            try:
                return pd.read_parquet(fp)
            except Exception:
                pass
        return pd.DataFrame()

    # ------------------------------------------------------------------
    # 龙虎榜
    # ------------------------------------------------------------------
    def load_top_list(self, trade_date: str) -> pd.DataFrame:
        fp = os.path.join(self.parquet_dir, f"top_list_{trade_date}.parquet")
        if os.path.exists(fp):
            try:
                return pd.read_parquet(fp)
            except Exception:
                pass
        return pd.DataFrame()

    def load_top_inst(self, trade_date: str) -> pd.DataFrame:
        fp = os.path.join(self.parquet_dir, f"top_inst_{trade_date}.parquet")
        if os.path.exists(fp):
            try:
                return pd.read_parquet(fp)
            except Exception:
                pass
        return pd.DataFrame()

    # ------------------------------------------------------------------
    # moneyflow（按日期批量）
    # ------------------------------------------------------------------
    def load_moneyflow_by_date(self, trade_date: str, n_days: int = None) -> pd.DataFrame:
        """按日期批量加载moneyflow（复用parquet缓存）"""
        if n_days is None:
            n_days = self.mf_lookback
        dt = datetime.strptime(trade_date, "%Y%m%d")
        frames = []
        for i in range(n_days + 4):
            d = (dt - timedelta(days=i)).strftime("%Y%m%d")
            fp = os.path.join(self.parquet_dir, f"moneyflow_{d}.parquet")
            if os.path.exists(fp):
                try:
                    df = pd.read_parquet(fp)
                    if df is not None and not df.empty:
                        df["trade_date"] = df["trade_date"].astype(str)
                        frames.append(df)
                except Exception:
                    pass
            if len(frames) >= n_days:
                break
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    # ------------------------------------------------------------------
    # daily_basic
    # ------------------------------------------------------------------
    def load_daily_basic(self, trade_date: str) -> pd.DataFrame:
        fp = os.path.join(self.daily_cache, f"daily_basic_{trade_date}.csv")
        if os.path.exists(fp):
            try:
                return pd.read_csv(fp)
            except Exception:
                pass
        return pd.DataFrame()


def json_load(f):
    import json
    return json.load(f)
