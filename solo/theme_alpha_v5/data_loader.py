#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V5.0 - 数据加载模块（纯向量化缓存读取）
"""
import os, sys, time, json, warnings
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BASE_DIR))
warnings.filterwarnings("ignore")

import config


_original_expanduser = os.path.expanduser


def _safe_expanduser(path):
    if "tk.csv" in path:
        return os.path.join(config.CACHE_DIR, "tk.csv")
    return _original_expanduser(path)


os.path.expanduser = _safe_expanduser
import tushare as ts

DOTENV_PATH = "d:/mystock/config/.env"
if os.path.exists(DOTENV_PATH):
    from dotenv import load_dotenv
    load_dotenv(DOTENV_PATH)
    token = os.getenv("TUSHARE_TOKEN", "")
    if token:
        ts.set_token(token)
pro = ts.pro_api()


def load_cache_daily(codes, start_date, end_date) -> pd.DataFrame:
    """从 cache_daily 目录批量加载日线数据（向量化读取）"""
    path = config.CACHE_DAILY_PATH
    if not os.path.isdir(path):
        return pd.DataFrame()

    frames = []
    for code in codes:
        fp = os.path.join(path, f"{code}.csv")
        if os.path.exists(fp):
            try:
                df = pd.read_csv(fp, usecols=[
                    "trade_date", "open", "high", "low", "close",
                    "vol", "amount", "pct_chg"
                ])
                df["ts_code"] = code
                df["trade_date"] = df["trade_date"].astype(str)
                mask = (df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)
                frames.append(df.loc[mask])
            except Exception:
                pass

    if not frames:
        return pd.DataFrame()
    full = pd.concat(frames, ignore_index=True)
    full = full.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    return full


def load_moneyflow(codes, start_date, end_date) -> pd.DataFrame:
    """加载资金流数据"""
    mf_dir = os.path.join(config.CACHE_DAILY_PATH, "..", "cache_moneyflow")
    mf_dir = os.path.abspath(mf_dir)
    if not os.path.isdir(mf_dir):
        return pd.DataFrame()
    frames = []
    for code in codes:
        fp = os.path.join(mf_dir, f"{code}.parquet")
        if os.path.exists(fp):
            try:
                df = pd.read_parquet(fp)
                df["ts_code"] = code
                frames.append(df)
            except Exception:
                pass
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_limit_list(trade_date) -> pd.DataFrame:
    """加载涨停数据"""
    fp = os.path.join(config.CACHE_DAILY_PATH, f"limit_list_{trade_date}.parquet")
    if os.path.exists(fp):
        try:
            return pd.read_parquet(fp)
        except Exception:
            pass
    return pd.DataFrame()


def load_index_data(ts_code, start_date, end_date) -> pd.DataFrame:
    """加载指数日线"""
    idx_dir = os.path.join(config.CACHE_DAILY_PATH, "..", "cache_index")
    idx_dir = os.path.abspath(idx_dir)
    fp = os.path.join(idx_dir, f"{ts_code.replace('.', '_')}.csv")
    if os.path.exists(fp):
        df = pd.read_csv(fp)
        df["trade_date"] = df["trade_date"].astype(str)
        mask = (df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)
        return df.loc[mask].sort_values("trade_date").reset_index(drop=True)
    return pd.DataFrame()


def calc_theme_price_series(daily: pd.DataFrame, theme_codes: list) -> pd.Series:
    """向量化计算主题等权价格序列"""
    sub = daily[daily["ts_code"].isin(theme_codes)].copy()
    if sub.empty:
        return pd.Series(dtype=float)
    grouped = sub.groupby("trade_date")["close"].mean()
    return grouped.sort_index()


def calc_theme_return_series(daily: pd.DataFrame, theme_codes: list) -> pd.Series:
    """向量化计算主题等权日收益序列"""
    sub = daily[daily["ts_code"].isin(theme_codes)].copy()
    if sub.empty:
        return pd.Series(dtype=float)
    grouped = sub.groupby("trade_date")["pct_chg"].mean()
    return grouped.sort_index()


if __name__ == "__main__":
    print("[DataLoader] 数据加载模块就绪")
