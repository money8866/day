#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Engine V6.0 - 数据加载模块

学习 theme_trend_sentiment_score.py 的缓存方法：
  - 日线数据：从 d:/mystock/cache_daily/{code}.csv 读取
  - DC热度：从 cache_backbone_tushare/dc_hot/dc_hot_{date}.csv 读取
  - 指数行情：从 cache_daily 读取
  - 涨停/龙虎榜：通过 tushare API 获取并缓存到 Parquet
  - moneyflow：通过 tushare API 获取并缓存到 Parquet
"""
import os, sys, time, warnings
from datetime import datetime, timedelta
import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
warnings.filterwarnings("ignore")
import config

# ==================== Tushare 初始化 ====================
_orig_expanduser = os.path.expanduser

def _safe_expanduser(path):
    if "tk.csv" in path:
        return os.path.join(config.CACHE_DIR, "tk.csv")
    return _orig_expanduser(path)

os.path.expanduser = _safe_expanduser

from dotenv import load_dotenv
load_dotenv("d:/mystock/config/.env")
import tushare as ts
ts.set_token(os.getenv("TUSHARE_TOKEN", ""))
pro = ts.pro_api()


def get_last_trade_date():
    """获取最新交易日"""
    now = datetime.now()
    query_date = now.strftime("%Y%m%d")
    if now.hour < 15:
        query_date = (now - timedelta(days=1)).strftime("%Y%m%d")
    try:
        cal = pro.trade_cal(exchange='', start_date='20250101', end_date=query_date)
        cal = cal[cal['is_open'] == 1].sort_values('cal_date', ascending=False)
        return cal.iloc[0]['cal_date']
    except Exception:
        for i in range(7):
            dt = now - timedelta(days=i)
            if dt.weekday() < 5:
                return dt.strftime("%Y%m%d")
        return query_date


# ==================== 日线数据 ====================
def load_daily(codes, start_date, end_date):
    """从本地CSV缓存批量加载日线数据"""
    frames = []
    for code in codes:
        fp = os.path.join(config.DAILY_CACHE_PATH, f"{code}.csv")
        if os.path.exists(fp):
            try:
                df = pd.read_csv(fp)
                df["trade_date"] = df["trade_date"].astype(str)
                mask = (df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)
                df = df.loc[mask, ["ts_code", "trade_date", "open", "high", "low",
                                   "close", "vol", "amount", "pct_chg"]].copy()
                if len(df) >= 20:
                    frames.append(df)
            except Exception:
                pass
    if not frames:
        return pd.DataFrame()
    full = pd.concat(frames, ignore_index=True)
    return full.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


# ==================== daily_basic ====================
def load_daily_basic(trade_date):
    """加载每日基本面数据（换手率等）"""
    fp = os.path.join(config.DAILY_CACHE_PATH, f"daily_basic_{trade_date}.csv")
    if os.path.exists(fp):
        return pd.read_csv(fp)
    try:
        df = pro.daily_basic(trade_date=trade_date,
                             fields="ts_code,turnover_rate,volume_ratio,total_mv,circ_mv,pe,pb")
        if df is not None and not df.empty:
            df.to_csv(fp, index=False, encoding="utf-8-sig")
            return df
    except Exception as e:
        print(f"[DataLoader] daily_basic 失败: {e}")
    return pd.DataFrame()


# ==================== DC 热度 ====================
def load_dc_hot(trade_date):
    """加载东方财富人气榜数据"""
    fp = os.path.join(config.DC_HOT_CACHE_DIR, f"dc_hot_{trade_date}.csv")
    if os.path.exists(fp):
        try:
            return pd.read_csv(fp)
        except Exception:
            pass
    return pd.DataFrame()


# ==================== 涨停数据 ====================
def load_limit_list(trade_date):
    """加载涨停数据"""
    fp = os.path.join(config.PARQUET_DIR, f"limit_list_{trade_date}.parquet")
    if os.path.exists(fp):
        try:
            return pd.read_parquet(fp)
        except Exception:
            pass
    try:
        df = pro.limit_list_d(trade_date=trade_date)
        if df is not None and not df.empty:
            df.to_parquet(fp)
            return df
    except Exception as e:
        print(f"[DataLoader] limit_list_d 失败: {e}")
    return pd.DataFrame()


# ==================== 龙虎榜数据 ====================
def load_top_list(trade_date):
    """加载龙虎榜数据"""
    fp = os.path.join(config.PARQUET_DIR, f"top_list_{trade_date}.parquet")
    if os.path.exists(fp):
        try:
            return pd.read_parquet(fp)
        except Exception:
            pass
    try:
        df = pro.top_list(trade_date=trade_date)
        if df is not None and not df.empty:
            df.to_parquet(fp)
            return df
    except Exception:
        pass
    return pd.DataFrame()


def load_top_inst(trade_date):
    """加载机构龙虎榜数据"""
    fp = os.path.join(config.PARQUET_DIR, f"top_inst_{trade_date}.parquet")
    if os.path.exists(fp):
        try:
            return pd.read_parquet(fp)
        except Exception:
            pass
    try:
        df = pro.top_inst(trade_date=trade_date)
        if df is not None and not df.empty:
            df.to_parquet(fp)
            return df
    except Exception:
        pass
    return pd.DataFrame()


# ==================== 指数行情 ====================
def load_index(ts_code="000300.SH", start_date=None, end_date=None):
    """加载指数日线"""
    fp = os.path.join(config.DAILY_CACHE_PATH, f"{ts_code.replace('.', '_')}.csv")
    if os.path.exists(fp):
        df = pd.read_csv(fp)
        df["trade_date"] = df["trade_date"].astype(str)
        if start_date and end_date:
            mask = (df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)
            df = df.loc[mask]
        return df.sort_values("trade_date").reset_index(drop=True)
    try:
        df = pro.index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        return df if df is not None else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


# ==================== moneyflow ====================
def load_moneyflow(codes, start_date, end_date):
    """加载资金流数据（按需获取，缓存到 Parquet）"""
    frames = []
    for code in codes:
        fp = os.path.join(config.MONEYFLOW_DIR, f"{code}.parquet")
        if os.path.exists(fp):
            try:
                df = pd.read_parquet(fp)
                df["trade_date"] = df["trade_date"].astype(str)
                mask = (df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)
                df = df.loc[mask]
                if not df.empty:
                    frames.append(df)
            except Exception:
                pass
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# ==================== stk_factor ====================
def load_stk_factor(trade_date):
    """加载每日因子数据"""
    fp = os.path.join(config.PARQUET_DIR, f"stk_factor_{trade_date}.parquet")
    if os.path.exists(fp):
        try:
            return pd.read_parquet(fp)
        except Exception:
            pass
    try:
        df = pro.stk_factor(trade_date=trade_date)
        if df is not None and not df.empty:
            df.to_parquet(fp)
            return df
    except Exception:
        pass
    return pd.DataFrame()


# ==================== 交易日历 ====================
def get_trade_cal(start_date, end_date):
    """获取交易日历"""
    fp = os.path.join(config.PARQUET_DIR, f"trade_cal_{start_date}_{end_date}.parquet")
    if os.path.exists(fp):
        try:
            return pd.read_parquet(fp)
        except Exception:
            pass
    try:
        df = pro.trade_cal(exchange='', start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            df = df[df['is_open'] == 1].copy()
            df.to_parquet(fp)
            return df
    except Exception:
        pass
    return pd.DataFrame()
