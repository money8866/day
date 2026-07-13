#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Theme Alpha Researcher V10 - 全球顶级量化研究员风格
================================================
AQR / Two Sigma / Point72 Cubist / 幻方 / 九坤 / BlackRock Systematic

目标不是解释今天市场。
目标是：
    预测未来5个交易日，哪个一级主题最可能成为市场主线，
    并产生未来20个交易日超额收益。

所有设计围绕 Future Alpha，而不是 Current Heat。

V10 最终7因子加权几何平均模型:
    Theme Alpha = Rotation Timing (25%)        <- 核心: 是否进入轮动窗口
                  x Capital Persistence (20%)  <- 连续资金流入
                  x Trend Quality (20%)        <- 趋势质量而非涨跌
                  x Leader Ecology (15%)       <- 龙头+中军+补涨梯队
                  x Expectation Gap (10%)      <- 市场预期差
                  x Catalyst (5%)              <- 政策/产业/事件催化
                  x Beta Adjustment (5%)       <- 风格差异化调整

    Theme Strength = 0.25 Trend + 0.25 Capital + 0.20 Breadth
                     + 0.15 Leader + 0.15 ETF      (今日有多强)

Theme Watchlist 观察池:
    Buy List:    Alpha >= 65 且 Confidence >= 70
    Watch List:  Alpha 55~65, Rotation 正在提升 (待启动)
    Avoid List:  Alpha < 50 或 Lifecycle=Distribution
    核心价值: Watch List中的主题往往会在2-5天后进入Buy List

用法:
    python theme_alpha_researcher.py                    # 盘后扫描输出TOP15 + Watchlist
    python theme_alpha_researcher.py --top 20           # 输出TOP20
    python theme_alpha_researcher.py --debug "光通信"   # 调试单个主题
    python theme_alpha_researcher.py --backtest 60      # Walk-Forward回测60天
    python theme_alpha_researcher.py --no-cache         # 不使用缓存
"""
from __future__ import annotations

import os
import sys
import json
import argparse
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "theme_alpha_v6"))

try:
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
except Exception:
    pass

import config as v6config
import data_loader as dl


# =========================================================
# 配置参数 (独立调参区，支持Walk-Forward优化)
# =========================================================
class Config:
    # ===== 主题配置 =====
    THEME_JSON = os.path.join(BASE_DIR, "theme.json")
    THEME_MAP_JSON = os.path.join(v6config.DAILY_CACHE_PATH, "theme_stock_map_latest.json")
    MIN_THEME_STOCKS = 5
    LOOKBACK_DAYS = 120

    # ===== ETF 领先权重 (≥40%) =====
    ETF_WEIGHT_IN_MODEL = 0.42       # ETF信息占模型总权重
    ETF_LOOKBACK = 90                # ETF回看天数
    ETF_BENCHMARK = "000300.SH"      # 基准指数

    # ===== V10 最终7因子权重 (加权几何平均) =====
    # Rotation Timing 25% + Capital Persistence 20% + Trend Quality 20%
    # + Leader Ecology 15% + Expectation Gap 10% + Catalyst 5% + Theme Beta Adj 5%
    ALPHA_W_ROTATION = 0.25       # 核心: 是否进入轮动窗口
    ALPHA_W_CAPITAL = 0.20        # 连续资金流入
    ALPHA_W_TREND = 0.20          # 趋势质量而非涨跌
    ALPHA_W_LEADER = 0.15         # 龙头+中军+补涨梯队
    ALPHA_W_EXPECTATION = 0.10    # 市场预期差
    ALPHA_W_CATALYST = 0.05      # 政策/产业/事件催化
    ALPHA_W_BETA_ADJ = 0.05       # 风格差异化调整

    # ===== Theme Strength 加法权重 (今日有多强) =====
    STRENGTH_W_TREND = 0.25
    STRENGTH_W_CAPITAL = 0.25
    STRENGTH_W_BREADTH = 0.20
    STRENGTH_W_LEADER = 0.15
    STRENGTH_W_ETF = 0.15

    # ===== Watchlist 观察池阈值 =====
    BUYLIST_ALPHA = 65            # Buy List: Alpha ≥ 65 且 Confidence ≥ 70
    BUYLIST_CONF = 70
    WATCHLIST_ALPHA_MIN = 55      # Watch List: Alpha 55~65 且 Rotation 提升
    WATCHLIST_ALPHA_MAX = 65
    WATCHLIST_ROTATION_TREND = 0  # Rotation 5日趋势 > 0
    AVOIDLIST_ALPHA = 50           # Avoid List: Alpha < 50 或 Lifecycle=Distribution

    # ===== 生命周期阈值 =====
    LIFECYCLE_OVERHEATED_DAYS = 5       # 高潮阶段最大持续天数
    LIFECYCLE_DISTRIBUTION_DROP = -0.08 # 派发阶段跌幅阈值
    LIFECYCLE_BIRTH_GAIN = 0.05         # 启动阶段最小涨幅
    LIFECYCLE_EXPANSION_BREADTH = 0.60 # 扩张阶段最小广度

    # ===== 信号阈值 =====
    # 调整后映射: 中等输入约50分，强输入约80分
    SIGNAL_STRONG_BUY = 75
    SIGNAL_BUY = 65
    SIGNAL_WATCH = 50
    SIGNAL_REDUCE = 40
    # < SIGNAL_REDUCE -> Avoid

    # ===== 输出路径 =====
    OUTPUT_DIR = os.path.join(BASE_DIR, "trend_feature_output")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    OUTPUT_JSON = os.path.join(OUTPUT_DIR, "theme_alpha_researcher.json")
    OUTPUT_CSV = os.path.join(OUTPUT_DIR, "theme_alpha_researcher.csv")


CFG = Config()


# =========================================================
# 工具函数
# =========================================================
def _safe_float(x, default=0.0):
    try:
        v = float(x)
        if np.isnan(v) or np.isinf(v):
            return default
        return v
    except Exception:
        return default


def _percentile_rank(value, all_values):
    """全主题百分位排名 (0-100)"""
    arr = np.array([_safe_float(v) for v in all_values])
    if len(arr) == 0:
        return 50.0
    rank = np.sum(arr <= _safe_float(value)) / len(arr) * 100
    return float(rank)


def _normalize_01(x, floor=0.05, ceil=0.95):
    """将任意分数 clip 到 [floor, ceil]"""
    return float(np.clip(_safe_float(x), floor, ceil))


def _sigmoid(x, k=1.0, x0=0.0):
    """sigmoid 映射，用于非线性变换"""
    x = _safe_float(x)
    return 1.0 / (1.0 + np.exp(-k * (x - x0)))


# =========================================================
# 主题配置加载
# =========================================================
def load_theme_config():
    """从 theme.json 加载一级主题配置"""
    if not os.path.exists(CFG.THEME_JSON):
        print(f"[Error] theme.json 不存在: {CFG.THEME_JSON}")
        return {}
    with open(CFG.THEME_JSON, "r", encoding="utf-8") as f:
        raw = json.load(f)
    themes = raw.get("HOT_THEMES", {})
    print(f"[ThemeConfig] 加载 {len(themes)} 个一级主题")
    return themes


def load_theme_universe():
    """加载主题-股票映射 (来自 theme_stock_map_latest.json)"""
    if not os.path.exists(CFG.THEME_MAP_JSON):
        print(f"[Error] 主题股票映射不存在: {CFG.THEME_MAP_JSON}")
        return {}, {}
    with open(CFG.THEME_MAP_JSON, "r", encoding="utf-8") as f:
        raw = json.load(f)
    themes_raw = raw.get("themes", {})
    universe = {}
    name_map = {}
    for tname, stk_list in themes_raw.items():
        codes = []
        for s in stk_list:
            code = s.get("code") if isinstance(s, dict) else str(s)
            if code:
                codes.append(code)
                if isinstance(s, dict) and s.get("name"):
                    name_map[code] = s["name"]
        if len(codes) >= CFG.MIN_THEME_STOCKS:
            universe[tname] = codes
    print(f"[ThemeUniverse] 加载 {len(universe)} 个主题, {len(name_map)} 只股票")
    return universe, name_map


# =========================================================
# ETF 数据加载 (独立通道，权重≥40%)
# =========================================================
def _normalize_etf_tscode(etf_code: str) -> str:
    """ETF代码后缀智能判断
    15/16开头 = 深交所 .SZ
    51/56/58/50开头 = 上交所 .SH
    """
    if not etf_code:
        return ""
    if "." in etf_code:
        return etf_code
    code = etf_code.strip()
    if code.startswith(("15", "16")):
        return f"{code}.SZ"
    if code.startswith(("51", "56", "58", "50")):
        return f"{code}.SH"
    return f"{code}.SH"


def load_etf_daily(etf_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """加载ETF日线数据 - 复用 tushare fund_daily + cache

    修复: 15/16开头是深交所.SZ (原代码全部默认.SH导致20个深市ETF失败)
    """
    if not etf_code:
        return pd.DataFrame()

    cache_fp = os.path.join(v6config.CACHE_DIR, "etf_daily", f"{etf_code}.parquet")
    os.makedirs(os.path.dirname(cache_fp), exist_ok=True)

    # 优先读取本地缓存 (全量)
    if os.path.exists(cache_fp):
        try:
            df = pd.read_parquet(cache_fp)
            df["trade_date"] = df["trade_date"].astype(str)
            mask = (df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)
            sub = df.loc[mask].sort_values("trade_date").reset_index(drop=True)
            if not sub.empty:
                return sub
            # 缓存但无所需范围数据，尝试补充拉取
            latest_in_cache = df["trade_date"].max() if not df.empty else "0"
            if latest_in_cache >= end_date:
                return sub
        except Exception:
            pass

    # 智能判断交易所后缀
    ts_code = _normalize_etf_tscode(etf_code)
    try:
        df = dl.pro.fund_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            # 重试: 切换后缀
            alt_suffix = ".SZ" if ts_code.endswith(".SH") else ".SH"
            alt_code = etf_code + alt_suffix if "." not in etf_code else etf_code.replace(".SH", ".SZ")
            try:
                df = dl.pro.fund_daily(ts_code=alt_code, start_date=start_date, end_date=end_date)
            except Exception:
                pass
        if df is None or df.empty:
            return pd.DataFrame()
        df["trade_date"] = df["trade_date"].astype(str)
        # 合并已有缓存
        if os.path.exists(cache_fp):
            try:
                old = pd.read_parquet(cache_fp)
                old["trade_date"] = old["trade_date"].astype(str)
                df = pd.concat([old, df], ignore_index=True).drop_duplicates(subset=["trade_date"])
            except Exception:
                pass
        df.to_parquet(cache_fp)
        return df.sort_values("trade_date").reset_index(drop=True)
    except Exception as e:
        print(f"[ETFLoader] {etf_code} ({ts_code}) 失败: {e}")
        return pd.DataFrame()


def load_etf_share_change(etf_code: str) -> float:
    """ETF份额变化 - 通过 pro.fund_share 获取"""
    if not etf_code:
        return 0.0
    try:
        ts_code = _normalize_etf_tscode(etf_code)
        df = dl.pro.fund_share(ts_code=ts_code)
        if df is None or df.empty:
            # 切换后缀重试
            alt_suffix = ".SZ" if ts_code.endswith(".SH") else ".SH"
            alt_code = etf_code + alt_suffix if "." not in etf_code else etf_code.replace(".SH", ".SZ")
            df = dl.pro.fund_share(ts_code=alt_code)
        if df is None or df.empty:
            return 0.0
        df = df.sort_values("trade_date")
        if len(df) < 2:
            return 0.0
        recent = df.tail(5)
        if len(recent) >= 2:
            share_change = (recent.iloc[-1]["trade_sh"] - recent.iloc[0]["trade_sh"]) / max(recent.iloc[0]["trade_sh"], 1)
            return _safe_float(share_change)
    except Exception:
        pass
    return 0.0


# =========================================================
# 模块1: Trend Quality (趋势质量)
# =========================================================
def calc_trend_quality(etf_daily: pd.DataFrame, stock_daily: pd.DataFrame,
                       codes: List[str], bench_daily: pd.DataFrame = None) -> Dict[str, Any]:
    """计算趋势质量评分

    要求：趋势连续，不是今日涨停。
    输入：ETF(5/10/20/60 MA), Slope, MACD, ADX, Relative Strength, Sharpe(20), MDD

    返回:
        {
            "score": 0-100,
            "sub": {ma_breadth, slope, macd, adx, rs, sharpe, mdd},
            "details": {...}
        }
    """
    result = {"score": 50.0, "sub": {}, "details": {}}

    # ----- ETF 通道 (主权重) -----
    if etf_daily is None or etf_daily.empty or len(etf_daily) < 30:
        return result

    close = etf_daily["close"].values.astype(float)
    high = etf_daily["high"].values.astype(float) if "high" in etf_daily.columns else close
    low = etf_daily["low"].values.astype(float) if "low" in etf_daily.columns else close
    vol = etf_daily["vol"].values.astype(float) if "vol" in etf_daily.columns else np.ones_like(close)
    n = len(close)

    # 1) MA Breadth - 站上 MA5/10/20/60 的比例
    ma5 = pd.Series(close).rolling(5, min_periods=1).mean().values
    ma10 = pd.Series(close).rolling(10, min_periods=1).mean().values
    ma20 = pd.Series(close).rolling(20, min_periods=1).mean().values
    ma60 = pd.Series(close).rolling(60, min_periods=1).mean().values
    p = close[-1]
    ma_count = (int(p > ma5[-1]) + int(p > ma10[-1]) + int(p > ma20[-1]) + int(p > ma60[-1]))
    ma_breadth = ma_count / 4.0 * 100

    # 2) Slope - 20日均线斜率
    if n >= 25 and ma20[-1] > 0:
        slope = (ma20[-1] / ma20[-6] - 1) * 100
    else:
        slope = 0.0
    slope_score = float(np.clip(50 + slope * 30, 0, 100))

    # 3) MACD
    if n >= 35:
        ema12 = pd.Series(close).ewm(span=12, adjust=False).mean().values
        ema26 = pd.Series(close).ewm(span=26, adjust=False).mean().values
        dif = ema12 - ema26
        dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
        macd_hist = (dif - dea) * 2
        if macd_hist[-1] > 0 and macd_hist[-1] > macd_hist[-3]:
            macd_score = 85.0
        elif macd_hist[-1] > 0:
            macd_score = 70.0
        elif macd_hist[-1] > macd_hist[-3]:
            macd_score = 45.0
        else:
            macd_score = 25.0
    else:
        macd_score = 50.0
        macd_hist = np.array([0])

    # 4) ADX (简化版趋势强度)
    if n >= 20:
        plus_dm = np.maximum(np.diff(high), 0)
        minus_dm = np.maximum(-np.diff(low), 0)
        tr = np.maximum(np.abs(np.diff(high)),
                        np.maximum(np.abs(np.diff(low)), np.abs(np.diff(high) - np.diff(low))))
        atr = pd.Series(tr).rolling(14, min_periods=1).mean().values
        atr = np.where(atr == 0, 1e-9, atr)
        plus_di = 100 * pd.Series(plus_dm).rolling(14, min_periods=1).mean().values / atr
        minus_di = 100 * pd.Series(minus_dm).rolling(14, min_periods=1).mean().values / atr
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-9)
        adx = pd.Series(dx).rolling(14, min_periods=1).mean().values[-1]
        adx_score = float(np.clip(adx * 2.0, 0, 100))
    else:
        adx = 0
        adx_score = 50.0

    # 5) Relative Strength (vs 基准)
    rs = 0.0
    rs_score = 50.0
    if bench_daily is not None and not bench_daily.empty and len(bench_daily) >= 20:
        bench_close = bench_daily["close"].values.astype(float)
        if len(bench_close) >= 20 and len(close) >= 20:
            etf_r20 = close[-1] / close[-21] - 1 if len(close) > 20 else 0
            bench_r20 = bench_close[-1] / bench_close[-21] - 1 if len(bench_close) > 20 else 0
            rs = (etf_r20 - bench_r20) * 100
            rs_score = float(np.clip(50 + rs * 8, 0, 100))

    # 6) Sharpe(20)
    if n >= 21:
        rets = np.diff(np.log(close[-21:]))
        if len(rets) > 0 and np.std(rets) > 0:
            sharpe = float(np.mean(rets) / np.std(rets) * np.sqrt(252))
            sharpe_score = float(np.clip(50 + sharpe * 10, 0, 100))
        else:
            sharpe_score = 50.0
    else:
        sharpe_score = 50.0

    # 7) Maximum Drawdown (20日)
    if n >= 20:
        prices = close[-20:]
        running_max = np.maximum.accumulate(prices)
        dd = (running_max - prices) / running_max
        mdd = float(np.max(dd))
        mdd_score = float(np.clip((1 - mdd) * 100, 0, 100))
    else:
        mdd = 0
        mdd_score = 50.0

    # ----- 个股通道辅助 (广度验证) -----
    stock_ma_breadth = ma_breadth  # 默认沿用ETF
    if stock_daily is not None and not stock_daily.empty and len(codes) >= 3:
        sub = stock_daily[stock_daily["ts_code"].isin(codes)]
        if not sub.empty:
            latest_day = sub["trade_date"].max()
            latest = sub[sub["trade_date"] == latest_day]
            sorted_sub = sub.sort_values(["ts_code", "trade_date"])
            ma_counts = []
            for c, grp in sorted_sub.groupby("ts_code"):
                arr = grp["close"].values
                if len(arr) >= 60:
                    p_s = arr[-1]
                    s = ((p_s > arr[-5:].mean()) + (p_s > arr[-10:].mean()) +
                         (p_s > arr[-20:].mean()) + (p_s > arr[-60:].mean()))
                    ma_counts.append(s)
            if ma_counts:
                stock_ma_breadth = float(np.mean(ma_counts) / 4.0 * 100)

    # ----- 加权合成 (ETF主权重) -----
    score = (
        ma_breadth * 0.15 + stock_ma_breadth * 0.05 +
        slope_score * 0.15 + macd_score * 0.15 + adx_score * 0.15 +
        rs_score * 0.15 + sharpe_score * 0.10 + mdd_score * 0.10
    )
    score = float(np.clip(score, 0, 100))

    result["score"] = score
    result["sub"] = {
        "ma_breadth": round(ma_breadth, 1),
        "slope": round(slope_score, 1),
        "macd": round(macd_score, 1),
        "adx": round(adx_score, 1),
        "rs": round(rs_score, 1),
        "sharpe": round(sharpe_score, 1),
        "mdd": round(mdd_score, 1),
    }
    result["details"] = {
        "adx_raw": round(float(adx), 1),
        "slope_raw": round(slope, 2),
        "rs_raw": round(rs, 2),
        "mdd_raw": round(mdd * 100, 2),
    }
    return result


# =========================================================
# 模块2: Capital Persistence (资金持续性)
# =========================================================
def calc_capital_persistence(etf_daily: pd.DataFrame, stock_daily: pd.DataFrame,
                             codes: List[str], moneyflow: pd.DataFrame = None,
                             etf_share_change: float = 0.0,
                             top_df: pd.DataFrame = None) -> Dict[str, Any]:
    """资金连续性评分

    输入: ETF份额变化, 成交额, 北向, 龙虎榜, 大单, 连续放量天数, 资金连续流入天数, 资金集中度
    """
    result = {"score": 50.0, "sub": {}, "details": {}}

    # 1) ETF 份额变化
    share_score = 50.0
    if etf_share_change != 0:
        share_score = float(np.clip(50 + etf_share_change * 500, 0, 100))

    # 2) ETF 成交额连续放量天数
    etf_amt_streak = 0
    etf_amt_score = 50.0
    if etf_daily is not None and not etf_daily.empty and len(etf_daily) >= 20:
        amt = etf_daily["amount"].values if "amount" in etf_daily.columns else None
        if amt is None and "vol" in etf_daily.columns:
            amt = etf_daily["vol"].values * etf_daily["close"].values
        if amt is not None and len(amt) >= 20:
            amt_ma20 = pd.Series(amt).rolling(20, min_periods=1).mean().values
            for i in range(len(amt) - 1, 0, -1):
                if amt[i] > amt_ma20[i]:
                    etf_amt_streak += 1
                else:
                    break
            etf_amt_score = float(np.clip(50 + etf_amt_streak * 8, 0, 100))

    # 3) 个股资金流连续性 (moneyflow)
    mf_streak = 0
    mf_concentration = 0.0
    mf_score = 50.0
    if moneyflow is not None and not moneyflow.empty:
        mf = moneyflow[moneyflow["ts_code"].isin(codes)].copy()
        if not mf.empty and "net_amount" in mf.columns:
            mf_dates = sorted(mf["trade_date"].unique())
            for d in reversed(mf_dates[-10:]):
                day_mf = mf[mf["trade_date"] == d]
                net = day_mf["net_amount"].sum()
                if net > 0:
                    mf_streak += 1
                else:
                    break
            mf_score = float(np.clip(50 + mf_streak * 8, 0, 100))

            # 资金集中度: Top3 净流入占比
            day_latest = mf_dates[-1]
            day_mf = mf[mf["trade_date"] == day_latest]
            if not day_mf.empty:
                top3 = day_mf.nlargest(3, "net_amount")["net_amount"].sum()
                total = day_mf["net_amount"].abs().sum()
                mf_concentration = _safe_float(top3 / total) if total > 0 else 0

    # 4) 龙虎榜活跃度
    lb_score = 50.0
    lb_count = 0
    if top_df is not None and not top_df.empty:
        lb_in_theme = top_df[top_df["ts_code"].isin(codes)] if "ts_code" in top_df.columns else pd.DataFrame()
        lb_count = len(lb_in_theme)
        lb_score = float(np.clip(50 + lb_count * 4, 0, 100))

    # 5) 个股成交额广度 (连续放量股票比例)
    breadth_vol = 50.0
    if stock_daily is not None and not stock_daily.empty and len(codes) >= 3:
        sub = stock_daily[stock_daily["ts_code"].isin(codes)]
        if not sub.empty:
            days = sorted(sub["trade_date"].unique())
            if len(days) >= 6:
                latest_day = days[-1]
                prev_5d = days[-6:-1]
                latest_amt = sub[sub["trade_date"] == latest_day].set_index("ts_code")["amount"]
                prev_amt = sub[sub["trade_date"].isin(prev_5d)].groupby("ts_code")["amount"].mean()
                common = latest_amt.index.intersection(prev_amt.index)
                if len(common) >= 3:
                    vol_ratio = latest_amt.loc[common] / prev_amt.loc[common].replace(0, np.nan)
                    vol_ratio = vol_ratio.dropna()
                    up_count = (vol_ratio > 1.0).sum()
                    breadth_vol = float(np.clip(up_count / len(vol_ratio) * 100, 0, 100))

    # 加权合成
    score = (
        share_score * 0.15 + etf_amt_score * 0.20 + mf_score * 0.30 +
        breadth_vol * 0.25 + lb_score * 0.10
    )
    score = float(np.clip(score, 0, 100))

    result["score"] = score
    result["sub"] = {
        "etf_share": round(share_score, 1),
        "etf_amount": round(etf_amt_score, 1),
        "moneyflow": round(mf_score, 1),
        "breadth_vol": round(breadth_vol, 1),
        "lb_active": round(lb_score, 1),
    }
    result["details"] = {
        "etf_share_change": round(etf_share_change * 100, 2),
        "etf_amt_streak": etf_amt_streak,
        "mf_streak": mf_streak,
        "mf_concentration": round(mf_concentration * 100, 1),
        "lb_count": lb_count,
    }
    return result


# =========================================================
# 模块3: Rotation Timing (轮动时机 - 最高权重)
# =========================================================
def calc_rotation_timing(etf_daily: pd.DataFrame, stock_daily: pd.DataFrame,
                         codes: List[str], all_theme_returns: List[float] = None,
                         theme_index: pd.DataFrame = None) -> Dict[str, Any]:
    """轮动时机评分 - 最高权重模块

    目标：预测什么时候轮到它。
    评分:
        - 最近是否横盘
        - ETF是否回踩20MA
        - 是否缩量整理
        - 距历史高点 5%/10%/15%
        - 主题最近是否冷却
        - 补涨股数量
        - ETF领先指数
        - 市场风格切换
        - 主线拥挤度
    """
    result = {"score": 50.0, "sub": {}, "details": {}}

    if etf_daily is None or etf_daily.empty or len(etf_daily) < 30:
        return result

    close = etf_daily["close"].values.astype(float)
    vol = etf_daily["vol"].values.astype(float) if "vol" in etf_daily.columns else np.ones_like(close)
    n = len(close)
    p = close[-1]

    # 1) 横盘整理检测 (近20日振幅)
    amplitude = 0.0
    if n >= 20:
        recent = close[-20:]
        amplitude = (np.max(recent) - np.min(recent)) / np.mean(recent)
        if amplitude < 0.05:
            consolidation_score = 85.0  # 横盘
        elif amplitude < 0.08:
            consolidation_score = 72.0
        elif amplitude < 0.12:
            consolidation_score = 60.0
        else:
            consolidation_score = 40.0
    else:
        consolidation_score = 50.0

    # 2) ETF回踩20MA
    ma20 = pd.Series(close).rolling(20, min_periods=1).mean().values
    dist_ma20 = (p - ma20[-1]) / ma20[-1] if ma20[-1] > 0 else 0
    if abs(dist_ma20) < 0.01:
        pullback_score = 88.0  # 回踩20MA附近
    elif dist_ma20 < -0.03:
        pullback_score = 75.0  # 跌破20MA较深
    elif dist_ma20 < 0:
        pullback_score = 65.0
    elif dist_ma20 > 0.05:
        pullback_score = 35.0  # 远离20MA
    else:
        pullback_score = 55.0

    # 3) 缩量整理
    vol_ratio = 1.0
    if n >= 25:
        vol_recent = np.mean(vol[-5:])
        vol_ma20 = np.mean(vol[-20:])
        vol_ratio = vol_recent / vol_ma20 if vol_ma20 > 0 else 1
        if vol_ratio < 0.7:
            shrink_score = 85.0  # 明显缩量
        elif vol_ratio < 0.9:
            shrink_score = 72.0
        elif vol_ratio > 1.3:
            shrink_score = 40.0  # 放量过大
        else:
            shrink_score = 60.0
    else:
        shrink_score = 50.0

    # 4) 距历史高点
    lookback = min(n, 60)
    high_60 = np.max(close[-lookback:])
    dist_high = (high_60 - p) / high_60 if high_60 > 0 else 0
    if dist_high < 0.03:
        high_score = 35.0  # 太接近高点
    elif dist_high < 0.05:
        high_score = 55.0
    elif dist_high < 0.10:
        high_score = 80.0  # 回调5-10%最佳
    elif dist_high < 0.15:
        high_score = 88.0  # 回调10-15%
    elif dist_high < 0.20:
        high_score = 72.0
    else:
        high_score = 50.0  # 过深回调

    # 5) 主题冷却度 (近5日 vs 前20日动量)
    cooldown_score = 50.0
    if n >= 25:
        r5 = close[-1] / close[-6] - 1
        r20_prev = close[-6] / close[-26] - 1 if n >= 26 else close[-1] / close[-21] - 1
        cooldown = r20_prev - r5
        if cooldown > 0.05:
            cooldown_score = 82.0  # 充分冷却
        elif cooldown > 0.02:
            cooldown_score = 72.0
        elif cooldown > -0.02:
            cooldown_score = 55.0
        else:
            cooldown_score = 35.0  # 仍在加速

    # 6) 补涨股数量
    catchup_score = 50.0
    catchup_count = 0
    if stock_daily is not None and not stock_daily.empty and len(codes) >= 5:
        sub = stock_daily[stock_daily["ts_code"].isin(codes)]
        if not sub.empty:
            days = sorted(sub["trade_date"].unique())
            if len(days) >= 25:
                latest_day = days[-1]
                prev_5d = days[-6]
                prev_20d = days[-21]
                r5_ret = sub[sub["trade_date"].isin([prev_5d, latest_day])].groupby("ts_code").apply(
                    lambda g: (g.iloc[-1]["close"] / g.iloc[0]["close"] - 1) * 100 if len(g) >= 2 else 0
                )
                r20_ret = sub[sub["trade_date"].isin([prev_20d, latest_day])].groupby("ts_code").apply(
                    lambda g: (g.iloc[-1]["close"] / g.iloc[0]["close"] - 1) * 100 if len(g) >= 2 else 0
                )
                common = r5_ret.index.intersection(r20_ret.index)
                if len(common) >= 3:
                    catchup_mask = (r20_ret.loc[common] < 0) & (r5_ret.loc[common] > 0)
                    catchup_count = int(catchup_mask.sum())
                    catchup_score = float(np.clip(catchup_count / len(common) * 100 * 3, 0, 100))

    # 7) ETF 领先指数 (相对强度趋势)
    etf_lead_score = 50.0
    if theme_index is not None and not theme_index.empty and len(theme_index) >= 20:
        idx_close = theme_index["close"].values.astype(float)
        if len(idx_close) >= 20 and len(close) >= 20:
            etf_r5 = close[-1] / close[-6] - 1
            idx_r5 = idx_close[-1] / idx_close[-6] - 1 if len(idx_close) >= 6 else 0
            lead = (etf_r5 - idx_r5) * 100
            if lead > 1.5:
                etf_lead_score = 85.0  # ETF明显领先
            elif lead > 0.5:
                etf_lead_score = 70.0
            elif lead > -0.5:
                etf_lead_score = 50.0
            else:
                etf_lead_score = 30.0

    # 8) 主线拥挤度 (全主题动量排名)
    crowd_score = 50.0
    pct_rank = 50.0
    if all_theme_returns is not None and len(all_theme_returns) > 0:
        etf_r20 = close[-1] / close[-21] - 1 if n >= 21 else 0
        pct_rank = _percentile_rank(etf_r20, all_theme_returns)
        if pct_rank > 90:
            crowd_score = 30.0  # 过度拥挤
        elif pct_rank > 75:
            crowd_score = 45.0
        elif pct_rank > 50:
            crowd_score = 60.0
        elif pct_rank > 25:
            crowd_score = 80.0  # 中低位，轮动机会大
        else:
            crowd_score = 70.0  # 底部

    # 加权合成 (回踩+缩量+距高点 是核心)
    score = (
        consolidation_score * 0.12 + pullback_score * 0.20 + shrink_score * 0.15 +
        high_score * 0.20 + cooldown_score * 0.13 + catchup_score * 0.08 +
        etf_lead_score * 0.07 + crowd_score * 0.05
    )
    score = float(np.clip(score, 0, 100))

    result["score"] = score
    result["sub"] = {
        "consolidation": round(consolidation_score, 1),
        "pullback_ma20": round(pullback_score, 1),
        "shrink": round(shrink_score, 1),
        "dist_high": round(high_score, 1),
        "cooldown": round(cooldown_score, 1),
        "catchup": round(catchup_score, 1),
        "etf_lead": round(etf_lead_score, 1),
        "crowd": round(crowd_score, 1),
    }
    result["details"] = {
        "amplitude": round(amplitude * 100, 2),
        "dist_ma20": round(dist_ma20 * 100, 2),
        "vol_ratio": round(vol_ratio, 2),
        "dist_high": round(dist_high * 100, 2),
        "catchup_count": catchup_count,
        "pct_rank": round(pct_rank, 1),
    }
    return result


# =========================================================
# 模块4: Leader Ecology (龙头生态)
# =========================================================
def calc_leader_ecology(stock_daily: pd.DataFrame, codes: List[str],
                        top_df: pd.DataFrame = None, top_inst: pd.DataFrame = None,
                        theme_config: Dict = None) -> Dict[str, Any]:
    """龙头生态评分

    统计: Leader / Middle Leader / Follower
    计算: Leader Persistence, Leader Stability, Follower Expansion, Bull Score Top10
    主题龙头是否频繁变化
    """
    result = {"score": 50.0, "sub": {}, "details": {}}

    if stock_daily is None or stock_daily.empty or len(codes) < 3:
        return result

    sub = stock_daily[stock_daily["ts_code"].isin(codes)].copy()
    if sub.empty:
        return result

    days = sorted(sub["trade_date"].unique())
    if len(days) < 20:
        return result

    latest_day = days[-1]
    prev_20d = days[-21] if len(days) >= 21 else days[0]

    # 1) 计算每只股票的20日涨幅
    stock_returns = []
    for c, grp in sub.groupby("ts_code"):
        if len(grp) >= 2:
            r20 = (grp.iloc[-1]["close"] / grp.iloc[0]["close"] - 1) * 100
            stock_returns.append({"code": c, "r20": r20, "latest_amt": grp.iloc[-1].get("amount", 0)})

    if len(stock_returns) < 3:
        return result

    df_ret = pd.DataFrame(stock_returns).sort_values("r20", ascending=False).reset_index(drop=True)
    n_stocks = len(df_ret)

    # 2) 分层: Leader (Top20%) / Middle (Top20-50%) / Follower (Bottom 50%)
    n_leader = max(1, int(n_stocks * 0.20))
    n_middle = max(1, int(n_stocks * 0.30))
    leaders = df_ret.head(n_leader)
    middles = df_ret.iloc[n_leader:n_leader + n_middle]
    followers = df_ret.iloc[n_leader + n_middle:]

    leader_ret = float(leaders["r20"].mean())
    middle_ret = float(middles["r20"].mean()) if not middles.empty else 0
    follower_ret = float(followers["r20"].mean()) if not followers.empty else 0

    # 3) Leader Persistence (龙头持续性 - 近3天涨幅)
    leader_persist_score = 50.0
    if len(days) >= 4:
        prev_3d = days[-4]
        leader_codes = leaders["code"].tolist()
        leader_3d = sub[(sub["ts_code"].isin(leader_codes)) & (sub["trade_date"].isin([prev_3d, latest_day]))]
        if not leader_3d.empty:
            r3_by_code = leader_3d.groupby("ts_code").apply(
                lambda g: (g.iloc[-1]["close"] / g.iloc[0]["close"] - 1) * 100 if len(g) >= 2 else 0
            )
            leader_persist_score = float(np.clip(50 + r3_by_code.mean() * 3, 0, 100))

    # 4) Leader Stability (龙头是否稳定 - 与5天前的Top对比)
    leader_stab_score = 50.0
    if len(days) >= 7:
        prev_5d = days[-6]
        prev_5d_ret = []
        for c, grp in sub.groupby("ts_code"):
            grp_5d = grp[grp["trade_date"].isin([prev_5d, latest_day])]
            if len(grp_5d) >= 2:
                prev_5d_ret.append({"code": c, "r": (grp_5d.iloc[-1]["close"] / grp_5d.iloc[0]["close"] - 1) * 100})
        if prev_5d_ret:
            df_prev = pd.DataFrame(prev_5d_ret).sort_values("r", ascending=False).reset_index(drop=True)
            prev_leaders = set(df_prev.head(n_leader)["code"].tolist())
            curr_leaders = set(leaders["code"].tolist())
            overlap = len(prev_leaders & curr_leaders) / max(len(curr_leaders), 1)
            leader_stab_score = float(np.clip(overlap * 100, 0, 100))

    # 5) Follower Expansion (补涨扩张 - Follower 5日涨幅 > Leader 5日涨幅)
    follower_exp_score = 50.0
    leader_r5 = 0.0
    follower_r5 = 0.0
    if len(days) >= 7:
        prev_5d = days[-6]
        for label, group_df in [("leader", leaders), ("follower", followers)]:
            grp_data = sub[(sub["ts_code"].isin(group_df["code"].tolist())) &
                           (sub["trade_date"].isin([prev_5d, latest_day]))]
            if not grp_data.empty:
                r5 = grp_data.groupby("ts_code").apply(
                    lambda g: (g.iloc[-1]["close"] / g.iloc[0]["close"] - 1) * 100 if len(g) >= 2 else 0
                )
                if label == "leader":
                    leader_r5 = float(r5.mean())
                else:
                    follower_r5 = float(r5.mean()) if not r5.empty else 0
        if follower_r5 > leader_r5 and follower_r5 > 0:
            follower_exp_score = 82.0  # 补涨扩张明显
        elif follower_r5 > 0:
            follower_exp_score = 65.0
        else:
            follower_exp_score = 35.0

    # 6) Bull Score Top10 (Top10 强势股评分)
    bull_score_val = 50.0
    top10 = df_ret.head(10)
    if not top10.empty:
        up_count = (top10["r20"] > 0).sum()
        avg_top_ret = float(top10["r20"].mean())
        bull_score_val = float(np.clip(up_count / 10 * 60 + avg_top_ret * 2, 0, 100))

    # 7) 龙头变化频率 (近20天Top1是否变化)
    leader_change_score = 50.0
    if len(days) >= 40:
        prev_20d = days[-21] if len(days) >= 21 else days[0]
        days_idx_20 = days[-20] if len(days) >= 20 else days[0]
        prev_top1_ret = []
        for c, grp in sub.groupby("ts_code"):
            grp_20 = grp[grp["trade_date"].isin([prev_20d, days_idx_20])]
            if len(grp_20) >= 2:
                prev_top1_ret.append({"code": c, "r": (grp_20.iloc[-1]["close"] / grp_20.iloc[0]["close"] - 1) * 100})
        if prev_top1_ret:
            df_prev20 = pd.DataFrame(prev_top1_ret).sort_values("r", ascending=False).reset_index(drop=True)
            prev_top1 = set(df_prev20.head(3)["code"].tolist())
            curr_top1 = set(leaders.head(3)["code"].tolist())
            overlap = len(prev_top1 & curr_top1) / 3
            if overlap >= 0.67:
                leader_change_score = 80.0  # 龙头稳定
            elif overlap >= 0.33:
                leader_change_score = 60.0
            else:
                leader_change_score = 35.0  # 龙头频繁变化

    # 8) 龙虎榜加分
    lb_bonus = 0.0
    lb_count = 0
    if top_df is not None and not top_df.empty:
        lb_in_theme = top_df[top_df["ts_code"].isin(codes)] if "ts_code" in top_df.columns else pd.DataFrame()
        lb_count = len(lb_in_theme)
        lb_bonus = min(15.0, lb_count * 3)

    # 加权合成
    score = (
        leader_persist_score * 0.20 + leader_stab_score * 0.20 +
        follower_exp_score * 0.20 + bull_score_val * 0.15 +
        leader_change_score * 0.15
    ) + lb_bonus
    score = float(np.clip(score, 0, 100))

    # 识别龙头
    leader_code = leaders.iloc[0]["code"] if not leaders.empty else ""
    leader_r20_val = float(leaders.iloc[0]["r20"]) if not leaders.empty else 0

    result["score"] = score
    result["sub"] = {
        "leader_persist": round(leader_persist_score, 1),
        "leader_stab": round(leader_stab_score, 1),
        "follower_exp": round(follower_exp_score, 1),
        "bull_top10": round(bull_score_val, 1),
        "leader_change": round(leader_change_score, 1),
    }
    result["details"] = {
        "leader_code": leader_code,
        "leader_r20": round(leader_r20_val, 2),
        "leader_ret_avg": round(leader_ret, 2),
        "middle_ret": round(middle_ret, 2),
        "follower_ret": round(follower_ret, 2),
        "n_leader": n_leader,
        "n_middle": len(middles),
        "n_follower": len(followers),
        "lb_count": lb_count,
        "follower_r5": round(follower_r5, 2),
        "leader_r5": round(leader_r5, 2),
    }
    return result


# =========================================================
# 模块5: Expectation Gap (预期差)
# =========================================================
def calc_expectation_gap(etf_daily: pd.DataFrame, stock_daily: pd.DataFrame,
                         codes: List[str], daily_basic: pd.DataFrame = None,
                         dc_hot: pd.DataFrame = None) -> Dict[str, Any]:
    """预期差评分 - 寻找预期差

    包括:
        - ETF是否刚突破
        - 估值
        - 60日涨幅
        - 龙头涨幅
        - 补涨涨幅
        - 机构覆盖率
        - 一致性预期 (越一致扣分)
    """
    result = {"score": 50.0, "sub": {}, "details": {}}

    if etf_daily is None or etf_daily.empty or len(etf_daily) < 30:
        return result

    close = etf_daily["close"].values.astype(float)
    n = len(close)

    # 1) ETF 刚突破检测
    breakout_score = 50.0
    if n >= 30:
        ma20 = pd.Series(close).rolling(20, min_periods=1).mean().values
        ma60 = pd.Series(close).rolling(60, min_periods=1).mean().values
        if (close[-1] > ma20[-1] and
            close[-2] > ma20[-2] and
            close[-3] <= ma20[-3]):
            breakout_score = 90.0  # 刚突破
        elif close[-1] > ma20[-1]:
            if close[-1] > ma60[-1] * 1.05:
                breakout_score = 50.0  # 已远离
            else:
                breakout_score = 70.0
        else:
            breakout_score = 35.0

    # 2) 60日涨幅 (位置)
    r60_score = 50.0
    r60_val = 0.0
    if n >= 61:
        r60_val = close[-1] / close[-61] - 1
        if r60_val < 0:
            r60_score = 80.0  # 60日下跌，反转预期高
        elif r60_val < 0.10:
            r60_score = 75.0  # 低位
        elif r60_val < 0.25:
            r60_score = 55.0
        elif r60_val < 0.40:
            r60_score = 40.0
        else:
            r60_score = 25.0  # 高位

    # 3) 龙头涨幅 vs 补涨涨幅 (预期差)
    gap_score = 50.0
    if stock_daily is not None and not stock_daily.empty and len(codes) >= 5:
        sub = stock_daily[stock_daily["ts_code"].isin(codes)]
        days = sorted(sub["trade_date"].unique())
        if len(days) >= 21:
            latest_day = days[-1]
            prev_20d = days[-21]
            rets = []
            for c, grp in sub.groupby("ts_code"):
                grp_20 = grp[grp["trade_date"].isin([prev_20d, latest_day])]
                if len(grp_20) >= 2:
                    r = (grp_20.iloc[-1]["close"] / grp_20.iloc[0]["close"] - 1) * 100
                    rets.append(r)
            if len(rets) >= 5:
                rets = sorted(rets)
                top_avg = np.mean(rets[-3:])
                bottom_avg = np.mean(rets[:3])
                gap = top_avg - bottom_avg
                if gap > 30:
                    gap_score = 35.0  # 龙头远超补涨，预期一致看多，扣分
                elif gap > 15:
                    gap_score = 50.0
                elif gap > 0:
                    gap_score = 65.0
                else:
                    gap_score = 80.0  # 补涨已起，预期差大

    # 4) 估值位置 (PE分位)
    val_score = 50.0
    if daily_basic is not None and not daily_basic.empty:
        db_in_theme = daily_basic[daily_basic["ts_code"].isin(codes)]
        if not db_in_theme.empty and "pe" in db_in_theme.columns:
            pe_vals = db_in_theme["pe"].dropna()
            pe_vals = pe_vals[(pe_vals > 0) & (pe_vals < 500)]
            if len(pe_vals) >= 3:
                pe_median = float(pe_vals.median())
                if pe_median < 20:
                    val_score = 85.0
                elif pe_median < 35:
                    val_score = 70.0
                elif pe_median < 60:
                    val_score = 55.0
                else:
                    val_score = 35.0

    # 5) 机构覆盖率 (一致性预期 - 越一致扣分)
    consistency_score = 50.0
    if dc_hot is not None and not dc_hot.empty:
        hot_in_theme = dc_hot[dc_hot["ts_code"].isin(codes)] if "ts_code" in dc_hot.columns else pd.DataFrame()
        hot_count = len(hot_in_theme)
        if hot_count > 5:
            consistency_score = 30.0  # 过度一致看多，扣分
        elif hot_count > 2:
            consistency_score = 45.0
        elif hot_count > 0:
            consistency_score = 60.0
        else:
            consistency_score = 75.0  # 低关注度，预期差大

    # 6) ETF 量价背离 (预期差信号)
    div_score = 50.0
    if n >= 20:
        r5 = close[-1] / close[-6] - 1
        vol_arr = etf_daily["vol"].values if "vol" in etf_daily.columns else np.ones_like(close)
        vol_5 = np.mean(vol_arr[-5:])
        vol_20 = np.mean(vol_arr[-20:])
        vol_chg = vol_5 / vol_20 - 1 if vol_20 > 0 else 0
        if r5 > 0.03 and vol_chg < -0.1:
            div_score = 80.0  # 价涨量缩 = 预期差信号
        elif r5 > 0.03 and vol_chg > 0.2:
            div_score = 35.0  # 价涨量增 = 一致看多
        elif r5 < -0.03 and vol_chg > 0.2:
            div_score = 65.0  # 放量下跌 = 出清
        elif r5 < -0.03 and vol_chg < -0.1:
            div_score = 75.0  # 缩量回调 = 健康洗盘

    # 加权合成
    score = (
        breakout_score * 0.20 + r60_score * 0.15 + gap_score * 0.20 +
        val_score * 0.15 + consistency_score * 0.15 + div_score * 0.15
    )
    score = float(np.clip(score, 0, 100))

    result["score"] = score
    result["sub"] = {
        "breakout": round(breakout_score, 1),
        "r60_pos": round(r60_score, 1),
        "leader_gap": round(gap_score, 1),
        "valuation": round(val_score, 1),
        "consistency": round(consistency_score, 1),
        "divergence": round(div_score, 1),
    }
    result["details"] = {
        "breakout": "刚突破" if breakout_score >= 85 else ("突破" if breakout_score >= 65 else "未突破"),
        "r60": round(r60_val * 100, 2),
    }
    return result


# =========================================================
# 模块6: Catalyst (催化因子) - 政策/产业/事件催化
# =========================================================
def calc_catalyst(etf_daily: pd.DataFrame, stock_daily: pd.DataFrame,
                  codes: List[str], top_df: pd.DataFrame = None,
                  moneyflow: pd.DataFrame = None, etf_share_change: float = 0.0,
                  theme_config: Dict = None) -> Dict[str, Any]:
    """催化因子评分 - 政策、产业、事件催化

    检测:
        1. 龙虎榜异常激增 (机构介入信号)
        2. 涨停数量爆发 (情绪催化)
        3. 资金异动 (大单净流入突变)
        4. ETF份额突变 (机构加仓)
        5. 龙头股放量突破 (事件催化)
        6. 主题关键词热度 (DC热榜)

    返回: 0-100
    """
    result = {"score": 50.0, "sub": {}, "details": {}}

    # 1) 龙虎榜异常激增 (最近3天 vs 前10天)
    lb_score = 50.0
    lb_recent = 0
    if top_df is not None and not top_df.empty and "ts_code" in top_df.columns:
        lb_in_theme = top_df[top_df["ts_code"].isin(codes)]
        lb_recent = len(lb_in_theme)
        if lb_recent >= 8:
            lb_score = 90.0  # 大量上榜=机构强烈介入
        elif lb_recent >= 5:
            lb_score = 75.0
        elif lb_recent >= 3:
            lb_score = 65.0
        elif lb_recent >= 1:
            lb_score = 55.0
        else:
            lb_score = 45.0

    # 2) 涨停数量爆发 (龙头股+板块涨停)
    limit_score = 50.0
    limit_count = 0
    if stock_daily is not None and not stock_daily.empty:
        sub = stock_daily[stock_daily["ts_code"].isin(codes)]
        if not sub.empty and "pct_chg" in sub.columns:
            latest_day = sub["trade_date"].max()
            latest_data = sub[sub["trade_date"] == latest_day]
            # 涨停判定: 涨幅>=9.5% (科创/创业10%放宽到19%)
            if not latest_data.empty:
                limit_mask = latest_data["pct_chg"] >= 9.5
                limit_count = int(limit_mask.sum())
                if limit_count >= 5:
                    limit_score = 92.0  # 多股涨停=强催化
                elif limit_count >= 3:
                    limit_score = 80.0
                elif limit_count >= 1:
                    limit_score = 68.0
                else:
                    limit_score = 48.0

    # 3) 资金异动 (大单净流入突变)
    fund_score = 50.0
    fund_change = 0.0
    if moneyflow is not None and not moneyflow.empty and "net_amount" in moneyflow.columns:
        mf = moneyflow[moneyflow["ts_code"].isin(codes)]
        if not mf.empty:
            mf_dates = sorted(mf["trade_date"].unique())
            if len(mf_dates) >= 2:
                latest_mf = mf[mf["trade_date"] == mf_dates[-1]]["net_amount"].sum()
                if len(mf_dates) >= 3:
                    prev_mf = mf[mf["trade_date"].isin(mf_dates[-3:-1])]["net_amount"].mean()
                    if prev_mf != 0:
                        fund_change = (latest_mf / abs(prev_mf) - 1) * 100
                        if fund_change > 200:
                            fund_score = 88.0  # 资金净流入暴增
                        elif fund_change > 100:
                            fund_score = 75.0
                        elif fund_change > 0:
                            fund_score = 60.0
                        else:
                            fund_score = 40.0

    # 4) ETF份额突变 (机构加仓)
    share_score = 50.0
    if etf_share_change != 0:
        share_chg_pct = etf_share_change * 100
        if share_chg_pct > 5:
            share_score = 88.0  # 份额大增=机构看好
        elif share_chg_pct > 2:
            share_score = 75.0
        elif share_chg_pct > 0:
            share_score = 60.0
        elif share_chg_pct < -5:
            share_score = 35.0  # 份额大减=机构撤离
        else:
            share_score = 45.0

    # 5) 龙头股放量突破 (事件催化信号)
    breakout_score = 50.0
    if stock_daily is not None and not stock_daily.empty and len(codes) >= 3:
        sub = stock_daily[stock_daily["ts_code"].isin(codes)]
        if not sub.empty:
            days = sorted(sub["trade_date"].unique())
            if len(days) >= 25:
                latest_day = days[-1]
                prev_5d = days[-6]
                latest_data = sub[sub["trade_date"] == latest_day]
                prev_data = sub[sub["trade_date"] == prev_5d]
                if not latest_data.empty and not prev_data.empty:
                    # 放量突破检测: 最新成交额 / 5日前成交额
                    latest_amt = latest_data["amount"].mean() if "amount" in latest_data.columns else 0
                    prev_amt = prev_data["amount"].mean() if "amount" in prev_data.columns else 0
                    if prev_amt > 0:
                        amt_ratio = latest_amt / prev_amt
                        if amt_ratio > 2.0:
                            breakout_score = 88.0  # 放量2倍以上
                        elif amt_ratio > 1.5:
                            breakout_score = 75.0
                        elif amt_ratio > 1.2:
                            breakout_score = 62.0
                        else:
                            breakout_score = 48.0

    # 6) 主题热度 (DC热榜间接体现，通过leader上榜次数)
    heat_score = 50.0
    if theme_config is not None:
        # 主题关键词数量间接反映产业热度
        keywords = theme_config.get("keywords", [])
        if len(keywords) >= 15:
            heat_score = 65.0  # 关键词多=产业链完整
        elif len(keywords) >= 8:
            heat_score = 55.0

    # 加权合成
    score = (
        lb_score * 0.25 + limit_score * 0.25 + fund_score * 0.20 +
        share_score * 0.15 + breakout_score * 0.10 + heat_score * 0.05
    )
    score = float(np.clip(score, 0, 100))

    result["score"] = score
    result["sub"] = {
        "lb_anomaly": round(lb_score, 1),
        "limit_breakout": round(limit_score, 1),
        "fund_anomaly": round(fund_score, 1),
        "etf_share": round(share_score, 1),
        "breakout": round(breakout_score, 1),
        "heat": round(heat_score, 1),
    }
    result["details"] = {
        "lb_count": lb_recent,
        "limit_count": limit_count,
        "fund_change_pct": round(fund_change, 1),
        "etf_share_change_pct": round(etf_share_change * 100, 2),
    }
    return result


# =========================================================
# 模块7: Theme Beta Adjustment (风格差异化调整)
# =========================================================
def calc_theme_beta_adjustment(theme_style: str, market_risk: str,
                                  market_filter: Dict = None) -> Dict[str, Any]:
    """风格差异化调整 - 根据市场风格切换提升不同主题

    逻辑:
        - RiskOn (牛市): 提升成长型(AI/半导体/新能源), 降低红利/防御
        - Normal (震荡): 中性
        - RiskOff (回调): 提升防御型(红利/银行/消费), 降低成长
        - Bear (熊市): 大幅提升红利/防御, 大幅降低成长

    风格分类:
        - 成长型: AI产业链、半导体、新能源、人形机器人、军工
        - 防御型: 红利、银行、保险、必选消费
        - 周期型: 化工、有色、煤炭、钢铁
        - 中性型: 其他

    返回: adjust (0.7~1.3)
    """
    # 风格到类型映射
    style_to_type = {
        # 成长型
        "AI产业链": "growth", "AI硬件链": "growth", "AI应用链": "growth",
        "AI产业链链": "growth", "半导体产业链": "growth", "半导体材料": "growth",
        "半导体设备": "growth", "先进封装": "growth", "功率半导体": "growth",
        "新能源": "growth", "人形机器人": "growth", "智能驾驶": "growth",
        "军工": "growth", "创新医药主线": "growth", "固态电池": "growth",
        "新型储能": "growth", "脑机接口": "growth", "合成生物": "growth",
        "消费电子": "growth", "AI能源链": "growth",
        # 防御型
        "红利": "defensive", "必选消费红利链": "defensive", "银行": "defensive",
        "保险": "defensive", "券商": "defensive",
        # 周期型
        "周期化工": "cycle", "工业金属": "cycle", "贵金属": "cycle",
        "煤炭链": "cycle", "电力链": "cycle", "小金属": "cycle",
        "硫磺磷化工链": "cycle", "氟化工制冷剂": "cycle",
        # 中性
        "": "neutral",
    }

    style_type = style_to_type.get(theme_style, "neutral")

    # 市场环境调整表
    # [growth, defensive, cycle, neutral]
    risk_adjust_table = {
        "RiskOn":  {"growth": 1.15, "defensive": 0.90, "cycle": 1.05, "neutral": 1.00},
        "Normal":  {"growth": 1.00, "defensive": 1.00, "cycle": 1.00, "neutral": 1.00},
        "RiskOff": {"growth": 0.88, "defensive": 1.12, "cycle": 0.95, "neutral": 1.00},
        "Bear":    {"growth": 0.75, "defensive": 1.20, "cycle": 0.85, "neutral": 0.95},
    }

    adjust = risk_adjust_table.get(market_risk, risk_adjust_table["Normal"]).get(style_type, 1.0)

    # 市场filter合并
    if market_filter is not None:
        market_adj = market_filter.get("adjust", 1.0)
        # 避免双重调整过激，取几何平均
        final_adjust = (adjust * market_adj) ** 0.5
    else:
        final_adjust = adjust

    return {
        "adjust": round(float(np.clip(final_adjust, 0.7, 1.3)), 3),
        "style_type": style_type,
        "raw_style_adjust": round(adjust, 3),
        "market_risk": market_risk,
    }


# =========================================================
# 模块8: Market Filter (市场环境过滤)
# =========================================================
def calc_market_filter(hs300: pd.DataFrame, zz1000: pd.DataFrame = None,
                       all_amount: float = 0, limit_df: pd.DataFrame = None) -> Dict[str, Any]:
    """市场环境评分 - 调整 Theme Alpha

    包括: 沪深300, 中证1000, 成交额, 涨跌停比, 赚钱效应, 风险偏好, 指数趋势
    """
    result = {"score": 50.0, "risk": "Normal", "sub": {}, "adjust": 1.0, "style_adjust": {}, "details": {}}

    if hs300 is None or hs300.empty:
        return result

    close = hs300["close"].values.astype(float)
    n = len(close)
    if n < 20:
        return result

    # 1) 指数趋势
    ma20 = pd.Series(close).rolling(20, min_periods=1).mean().values
    ma60 = pd.Series(close).rolling(60, min_periods=1).mean().values
    if close[-1] > ma20[-1] and ma20[-1] > ma60[-1]:
        trend_score = 80.0
    elif close[-1] > ma20[-1]:
        trend_score = 65.0
    elif close[-1] > ma60[-1]:
        trend_score = 45.0
    else:
        trend_score = 25.0

    # 2) 沪深300动量
    r5 = close[-1] / close[-6] - 1 if n >= 6 else 0
    r20 = close[-1] / close[-21] - 1 if n >= 21 else 0
    mom_score = float(np.clip(50 + (r5 * 5 + r20 * 3) * 10, 0, 100))

    # 3) 中证1000 (小盘股风险偏好)
    zz_score = 50.0
    if zz1000 is not None and not zz1000.empty and len(zz1000) >= 20:
        zz_close = zz1000["close"].values.astype(float)
        zz_r5 = zz_close[-1] / zz_close[-6] - 1 if len(zz_close) >= 6 else 0
        zz_score = float(np.clip(50 + zz_r5 * 8, 0, 100))

    # 4) 成交额水平 (万亿为单位)
    amt_score = 50.0
    if all_amount > 0:
        amt_b = all_amount / 1e8
        if amt_b > 15000:
            amt_score = 85.0
        elif amt_b > 10000:
            amt_score = 70.0
        elif amt_b > 7000:
            amt_score = 55.0
        else:
            amt_score = 35.0

    # 5) 涨跌停比 (赚钱效应)
    limit_score = 50.0
    if limit_df is not None and not limit_df.empty:
        limit_score = float(np.clip(50 + len(limit_df) * 2, 30, 90))

    # 6) 风险偏好合成
    risk_score = (
        trend_score * 0.30 + mom_score * 0.25 + zz_score * 0.20 +
        amt_score * 0.15 + limit_score * 0.10
    )

    # 7) 市场环境分类
    if risk_score >= 70:
        risk_label = "RiskOn"
        adjust = 1.10  # 风险偏好高，提升Alpha
    elif risk_score >= 55:
        risk_label = "Normal"
        adjust = 1.0
    elif risk_score >= 40:
        risk_label = "RiskOff"
        adjust = 0.90  # 风险偏好低，降低Alpha
    else:
        risk_label = "Bear"
        adjust = 0.80  # 熊市，大幅降低Alpha

    # 8) 熊市特殊调整: 降低成长，提高红利
    style_adjust = {}
    if risk_label == "Bear":
        style_adjust = {"AI产业链": 0.85, "AI硬件链": 0.85, "半导体产业链": 0.90,
                        "周期化工": 1.10, "红利": 1.15, "新能源": 0.90,
                        "必选消费": 1.10, "防御": 1.10}

    result["score"] = float(np.clip(risk_score, 0, 100))
    result["risk"] = risk_label
    result["adjust"] = adjust
    result["style_adjust"] = style_adjust
    result["sub"] = {
        "trend": round(trend_score, 1),
        "momentum": round(mom_score, 1),
        "zz1000": round(zz_score, 1),
        "amount": round(amt_score, 1),
        "limit": round(limit_score, 1),
    }
    result["details"] = {
        "hs300_r5": round(r5 * 100, 2),
        "hs300_r20": round(r20 * 100, 2),
        "amount_b": round(all_amount / 1e8, 0) if all_amount > 0 else 0,
    }
    return result


# =========================================================
# 模块9: Theme Alpha (V10 7因子加权几何平均模型)
# =========================================================
def calc_theme_alpha(trend_q: Dict, capital_p: Dict, rotation_t: Dict,
                     leader_e: Dict, expectation_g: Dict,
                     catalyst: Dict = None, beta_adj: Dict = None) -> Dict[str, Any]:
    """计算 Theme Alpha - V10 最终7因子加权几何平均模型

    Alpha = Rotation^0.25 × Capital^0.20 × Trend^0.20 × Leader^0.15
            × Expectation^0.10 × Catalyst^0.05 × BetaAdj^0.05

    采用加权几何平均: 任一因子差都会拉低整体，但权重反映重要性
    最终 Alpha = (乘积)^(1/总权重) × 100

    BetaAdj 作为调整因子，不参与几何平均，最后乘上
    """
    # 归一化到 0~1 (clip避免0值导致几何平均为0)
    t = _normalize_01(trend_q["score"] / 100, 0.1, 0.95)
    c = _normalize_01(capital_p["score"] / 100, 0.1, 0.95)
    r = _normalize_01(rotation_t["score"] / 100, 0.1, 0.95)
    l = _normalize_01(leader_e["score"] / 100, 0.1, 0.95)
    e = _normalize_01(expectation_g["score"] / 100, 0.1, 0.95)
    cat = _normalize_01(catalyst["score"] / 100, 0.1, 0.95) if catalyst else 0.5

    # 加权几何平均 (6个评分因子)
    # log(Alpha) = Σ wi * log(xi) / Σ wi
    total_w = (CFG.ALPHA_W_ROTATION + CFG.ALPHA_W_CAPITAL + CFG.ALPHA_W_TREND +
               CFG.ALPHA_W_LEADER + CFG.ALPHA_W_EXPECTATION + CFG.ALPHA_W_CATALYST)

    log_alpha = (
        CFG.ALPHA_W_ROTATION * np.log(r) +
        CFG.ALPHA_W_CAPITAL * np.log(c) +
        CFG.ALPHA_W_TREND * np.log(t) +
        CFG.ALPHA_W_LEADER * np.log(l) +
        CFG.ALPHA_W_EXPECTATION * np.log(e) +
        CFG.ALPHA_W_CATALYST * np.log(cat)
    ) / total_w

    raw_alpha = np.exp(log_alpha)  # 0~1

    # 非线性映射到 0-100 (幂函数让中等输入也能输出合理分数)
    # raw=0.1 -> 55, raw=0.3 -> 68, raw=0.5 -> 82
    alpha_score = (raw_alpha ** 0.28) * 100

    # Beta Adjustment (风格差异化调整)
    beta_adjust = 1.0
    style_type = "neutral"
    if beta_adj is not None:
        beta_adjust = beta_adj.get("adjust", 1.0)
        style_type = beta_adj.get("style_type", "neutral")

    alpha_score = float(np.clip(alpha_score * beta_adjust, 0, 100))

    return {
        "score": round(alpha_score, 1),
        "raw_alpha": round(raw_alpha * 100, 2),
        "beta_adjust": round(beta_adjust, 3),
        "style_type": style_type,
        "factors": {
            "trend": round(t * 100, 1),
            "capital": round(c * 100, 1),
            "rotation": round(r * 100, 1),
            "leader": round(l * 100, 1),
            "expectation": round(e * 100, 1),
            "catalyst": round(cat * 100, 1),
        },
    }


# =========================================================
# 模块8: Theme Strength (今日有多强 - 加法模型)
# =========================================================
def calc_theme_strength(trend_q: Dict, capital_p: Dict, rotation_t: Dict,
                        leader_e: Dict, etf_score: float) -> Dict[str, Any]:
    """Theme Strength = 0.25 Trend + 0.25 Capital + 0.20 Breadth
                        + 0.15 Leader + 0.15 ETF
    """
    breadth_score = trend_q.get("sub", {}).get("ma_breadth", 50.0)

    score = (
        trend_q["score"] * CFG.STRENGTH_W_TREND +
        capital_p["score"] * CFG.STRENGTH_W_CAPITAL +
        breadth_score * CFG.STRENGTH_W_BREADTH +
        leader_e["score"] * CFG.STRENGTH_W_LEADER +
        etf_score * CFG.STRENGTH_W_ETF
    )
    return {"score": float(np.clip(score, 0, 100))}


# =========================================================
# 模块9: Lifecycle Detection (生命周期检测)
# =========================================================
def detect_lifecycle(etf_daily: pd.DataFrame, trend_q: Dict, capital_p: Dict,
                     rotation_t: Dict, leader_e: Dict) -> Dict[str, Any]:
    """生命周期检测 - 不是评分，是交易过滤

    阶段: Birth / Expansion / MainUptrend / Overheated / Distribution
    """
    if etf_daily is None or etf_daily.empty:
        return {"stage": "Unknown", "filter": 1.0, "reason": "数据不足", "risk": ""}

    close = etf_daily["close"].values.astype(float)
    vol = etf_daily["vol"].values.astype(float) if "vol" in etf_daily.columns else np.ones_like(close)
    n = len(close)
    if n < 30:
        return {"stage": "Unknown", "filter": 1.0, "reason": "数据不足", "risk": ""}

    r5 = close[-1] / close[-6] - 1 if n >= 6 else 0

    ma20 = pd.Series(close).rolling(20, min_periods=1).mean().values
    ma60 = pd.Series(close).rolling(60, min_periods=1).mean().values

    vol_5 = np.mean(vol[-5:]) if n >= 5 else 0
    vol_20 = np.mean(vol[-20:]) if n >= 20 else 1
    vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 1

    # 距60日高点
    lookback = min(n, 60)
    high_60 = np.max(close[-lookback:])
    dist_high = (high_60 - close[-1]) / high_60 if high_60 > 0 else 0

    # 阶段判断逻辑
    trend_score = trend_q["score"]
    capital_score = capital_p["score"]

    # 1) Overheated: 趋势很高 + 远离均线 + 放量
    if (r5 > 0.10 and dist_high < 0.03 and vol_ratio > 1.5):
        return {"stage": "Overheated", "filter": 0.5, "reason": "短期暴涨+接近高点+放量",
                "risk": "高位追涨风险"}

    # 2) Distribution: 跌破MA20 + 放量下跌
    if (close[-1] < ma20[-1] * 0.97 and r5 < CFG.LIFECYCLE_DISTRIBUTION_DROP and vol_ratio > 1.2):
        return {"stage": "Distribution", "filter": 0.3, "reason": "跌破MA20+放量下跌",
                "risk": "派发出货"}

    # 3) MainUptrend: 站上所有均线 + 趋势分高
    if (close[-1] > ma20[-1] and close[-1] > ma60[-1] * 1.02 and trend_score > 65):
        if vol_ratio > 1.3 and r5 > 0.05:
            return {"stage": "Overheated", "filter": 0.7, "reason": "主升中段+放量加速",
                    "risk": "关注见顶信号"}
        return {"stage": "MainUptrend", "filter": 1.0, "reason": "主升浪中",
                "risk": "趋势中"}

    # 4) Expansion: 突破MA20 + 量能放大 + 趋势分中等
    if (close[-1] > ma20[-1] * 1.01 and trend_score > 55 and
        capital_score > 55 and vol_ratio > 1.1):
        return {"stage": "Expansion", "filter": 1.1, "reason": "扩张阶段+量能放大",
                "risk": "启动确认"}

    # 5) Birth: 刚突破 + 量能温和
    if (close[-1] > ma20[-1] and close[-2] <= ma20[-2] and
        trend_score > 45 and r5 > CFG.LIFECYCLE_BIRTH_GAIN):
        return {"stage": "Birth", "filter": 1.15, "reason": "刚突破MA20+温和启动",
                "risk": "底部启动"}

    # 6) 底部蓄势
    if (dist_high > 0.10 and trend_score < 50 and
        abs(r5) < 0.03 and vol_ratio < 0.9):
        return {"stage": "Birth", "filter": 1.05, "reason": "底部缩量蓄势",
                "risk": "等待突破"}

    # 7) 默认
    if trend_score > 60:
        return {"stage": "MainUptrend", "filter": 0.95, "reason": "趋势向上",
                "risk": "温和上行"}
    elif trend_score > 45:
        return {"stage": "Expansion", "filter": 1.0, "reason": "震荡上行",
                "risk": "中性"}
    else:
        return {"stage": "Distribution", "filter": 0.7, "reason": "趋势走弱",
                "risk": "谨慎"}


# =========================================================
# 模块10: Future Return Prediction (未来收益预测)
# =========================================================
def predict_future_return(alpha_score: float, lifecycle: Dict, rotation_t: Dict,
                          expectation_g: Dict, trend_q: Dict) -> Dict[str, Any]:
    """预测未来 5/10/20 日超额收益概率"""
    base = alpha_score / 100  # 0~1

    lc_filter = lifecycle.get("filter", 1.0)
    rot_score = rotation_t["score"] / 100
    eg_score = expectation_g["score"] / 100
    tq_score = trend_q["score"] / 100

    # 时间衰减预测 (越长期，不确定性越高)
    p5 = float(np.clip(base * 0.7 + rot_score * 0.20 + eg_score * 0.10, 0, 1)) * lc_filter
    p10 = float(np.clip(base * 0.6 + rot_score * 0.25 + eg_score * 0.15, 0, 1)) * lc_filter
    p20 = float(np.clip(base * 0.5 + rot_score * 0.30 + tq_score * 0.20, 0, 1)) * lc_filter

    future_score = (p5 * 0.4 + p10 * 0.35 + p20 * 0.25) * 100

    return {
        "score": round(float(np.clip(future_score, 0, 100)), 1),
        "p5": round(p5 * 100, 1),
        "p10": round(p10 * 100, 1),
        "p20": round(p20 * 100, 1),
    }


# =========================================================
# 模块11: Signal Generation (信号生成)
# =========================================================
def generate_signal(alpha_score: float, lifecycle: Dict, future_ret: Dict,
                    rotation_t: Dict) -> str:
    """信号分级: Strong Buy / Buy / Watch / Avoid / Reduce

    规则:
        1) Overheated/Distribution 阶段 -> Reduce/Avoid (规避高位派发)
        2) Birth/Expansion + 高Alpha -> Strong Buy/Buy (底部启动)
        3) MainUptrend + 中等Alpha -> Watch (跟随趋势)
        4) Alpha极低 -> Avoid
    """
    stage = lifecycle.get("stage", "Unknown")

    # 1) 高潮阶段: 短期暴涨+接近高点+放量 -> 直接减仓
    if stage == "Overheated":
        if alpha_score > 70:
            return "Reduce"
        return "Avoid"

    # 2) 派发阶段: 跌破MA20+放量下跌 -> 规避
    if stage == "Distribution":
        if alpha_score > 60:
            return "Reduce"
        return "Avoid"

    # 3) Birth/Expansion: 启动/扩张阶段 (最佳介入时机)
    if stage in ["Birth", "Expansion"]:
        if alpha_score >= CFG.SIGNAL_STRONG_BUY and rotation_t["score"] >= 65:
            return "Strong Buy"
        if alpha_score >= CFG.SIGNAL_BUY:
            return "Buy"
        if alpha_score >= CFG.SIGNAL_WATCH:
            return "Watch"
        if alpha_score >= CFG.SIGNAL_REDUCE:
            return "Watch"  # 启动阶段至少Watch, 不轻易Avoid
        return "Avoid"

    # 4) MainUptrend: 主升浪阶段
    if stage == "MainUptrend":
        if alpha_score >= CFG.SIGNAL_STRONG_BUY and rotation_t["score"] >= 65:
            return "Strong Buy"
        if alpha_score >= CFG.SIGNAL_BUY:
            return "Buy"
        if alpha_score >= CFG.SIGNAL_WATCH:
            return "Watch"
        if alpha_score >= CFG.SIGNAL_REDUCE:
            return "Watch"  # 主升阶段中等Alpha也Watch
        return "Reduce"  # 主升但Alpha低=动能减弱, 减仓

    # 5) Unknown
    if alpha_score >= CFG.SIGNAL_BUY:
        return "Buy"
    if alpha_score >= CFG.SIGNAL_WATCH:
        return "Watch"
    if alpha_score >= CFG.SIGNAL_REDUCE:
        return "Reduce"
    return "Avoid"


def calc_confidence(alpha_score: float, future_ret: Dict, lifecycle: Dict,
                    rotation_t: Dict, expectation_g: Dict) -> float:
    """置信度评分 (0-100)"""
    consistency = 1.0 - abs(alpha_score / 100 - future_ret["score"] / 100)
    factor_consistency = 1.0 - abs(rotation_t["score"] / 100 - expectation_g["score"] / 100)
    lc_filter = lifecycle.get("filter", 1.0)

    conf = (alpha_score * 0.40 + future_ret["score"] * 0.30 +
            consistency * 100 * 0.15 + factor_consistency * 100 * 0.15) * lc_filter
    return float(np.clip(conf, 0, 100))


def calculate_all_theme_returns(etf_data_dict: Dict[str, pd.DataFrame]) -> List[float]:
    """计算所有主题的20日收益率，用于拥挤度排名"""
    rets = []
    for tname, etf_df in etf_data_dict.items():
        if etf_df is None or etf_df.empty or len(etf_df) < 21:
            continue
        close = etf_df["close"].values.astype(float)
        r20 = close[-1] / close[-21] - 1
        rets.append(r20)
    return rets


# =========================================================
# 个股和指数数据加载
# =========================================================
def load_index_daily(ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """加载指数日线 (沪深300/中证1000) - 复用 tushare"""
    cache_fp = os.path.join(v6config.CACHE_DIR, "index_daily", f"{ts_code.replace('.', '_')}.parquet")
    os.makedirs(os.path.dirname(cache_fp), exist_ok=True)

    if os.path.exists(cache_fp):
        try:
            df = pd.read_parquet(cache_fp)
            df["trade_date"] = df["trade_date"].astype(str)
            mask = (df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)
            return df.loc[mask].sort_values("trade_date").reset_index(drop=True)
        except Exception:
            pass

    try:
        df = dl.pro.index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty:
            return pd.DataFrame()
        df["trade_date"] = df["trade_date"].astype(str)
        df.to_parquet(cache_fp)
        return df.sort_values("trade_date").reset_index(drop=True)
    except Exception as e:
        print(f"[IndexLoader] {ts_code} 失败: {e}")
        return pd.DataFrame()


def load_stock_daily_batch(codes: List[str], start_date: str, end_date: str) -> pd.DataFrame:
    """批量加载个股日线 - 优先从本地 cache_daily 读取 (快)"""
    all_dfs = []
    cache_dir = v6config.DAILY_CACHE_PATH

    for code in codes:
        cache_fp = os.path.join(cache_dir, f"{code}.csv")
        if os.path.exists(cache_fp):
            try:
                df = pd.read_csv(cache_fp, dtype={"trade_date": str})
                if "ts_code" not in df.columns:
                    df["ts_code"] = code
                mask = (df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)
                df = df.loc[mask]
                if not df.empty:
                    all_dfs.append(df)
            except Exception:
                continue

    if not all_dfs:
        return pd.DataFrame()
    df_all = pd.concat(all_dfs, ignore_index=True)
    if "amount" not in df_all.columns and "vol" in df_all.columns:
        df_all["amount"] = df_all["vol"] * df_all["close"]
    return df_all


def load_dc_hot(trade_date: str) -> pd.DataFrame:
    """加载DC热度榜"""
    fp = os.path.join(v6config.DC_HOT_CACHE_DIR, f"dc_hot_{trade_date}.csv")
    if not os.path.exists(fp):
        return pd.DataFrame()
    try:
        df = pd.read_csv(fp, dtype={"ts_code": str})
        if "ts_code" in df.columns:
            df["ts_code"] = df["ts_code"].astype(str)
        return df
    except Exception:
        return pd.DataFrame()


def build_synthetic_etf_from_stocks(codes: List[str], start_date: str, end_date: str,
                                      benchmark_df: pd.DataFrame = None) -> pd.DataFrame:
    """无ETF主题的回退方案: 用主题成分股构建等权指数作为ETF替代

    逻辑:
        - 加载所有成分股日线
        - 每日等权平均收盘价 = 1000 (基准日)
        - 累积复权得到指数序列
        - 成交量=成分股成交量总和, 成交额=总和
    """
    if not codes:
        return pd.DataFrame()

    cache_fp = os.path.join(v6config.CACHE_DIR, "synthetic_etf", f"{'_'.join(sorted(codes[:5]))}_n{len(codes)}.parquet")
    os.makedirs(os.path.dirname(cache_fp), exist_ok=True)

    # 尝试读缓存
    if os.path.exists(cache_fp):
        try:
            df = pd.read_parquet(cache_fp)
            df["trade_date"] = df["trade_date"].astype(str)
            mask = (df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)
            sub = df.loc[mask]
            if not sub.empty:
                return sub.sort_values("trade_date").reset_index(drop=True)
        except Exception:
            pass

    # 加载个股数据
    stock_df = load_stock_daily_batch(codes, start_date, end_date)
    if stock_df.empty:
        return pd.DataFrame()

    # 按交易日构建等权指数
    trade_dates = sorted(stock_df["trade_date"].unique())
    if len(trade_dates) < 30:
        return pd.DataFrame()

    # 计算每只股票的日收益率
    records = []
    for code, grp in stock_df.groupby("ts_code"):
        grp = grp.sort_values("trade_date").reset_index(drop=True)
        if len(grp) < 2:
            continue
        grp["ret"] = grp["close"].pct_change()
        grp["vol_amt"] = grp.get("amount", grp.get("vol", 0))
        records.append(grp[["trade_date", "ts_code", "close", "vol", "ret", "vol_amt"]])

    if not records:
        return pd.DataFrame()

    df_all = pd.concat(records, ignore_index=True)

    # 按交易日聚合
    daily = df_all.groupby("trade_date").agg(
        avg_ret=("ret", "mean"),
        total_vol=("vol", "sum"),
        total_amt=("vol_amt", "sum"),
        n_stocks=("ts_code", "count"),
    ).reset_index()
    daily = daily.sort_values("trade_date").reset_index(drop=True)

    # 基准日 = 第一个有效日
    base_idx = 0
    for i, row in daily.iterrows():
        if pd.notna(row["avg_ret"]) and row["n_stocks"] >= 3:
            base_idx = i
            break
    daily = daily.iloc[base_idx:].reset_index(drop=True)

    if len(daily) < 30:
        return pd.DataFrame()

    # 等权指数: 基准=1000
    daily["ret"] = daily["avg_ret"].fillna(0)
    daily["close"] = 1000 * (1 + daily["ret"]).cumprod()
    daily["open"] = daily["close"].shift(1, fill_value=1000)
    daily["high"] = daily["close"] * (1 + daily["ret"].abs() * 0.5 + 0.005)
    daily["low"] = daily["close"] * (1 - daily["ret"].abs() * 0.5 - 0.005)
    daily["vol"] = daily["total_vol"]
    daily["amount"] = daily["total_amt"]

    result = daily[["trade_date", "open", "high", "low", "close", "vol", "amount"]].copy()
    result["trade_date"] = result["trade_date"].astype(str)
    result["pre_close"] = result["close"].shift(1, fill_value=1000)
    result["pct_chg"] = result["close"].pct_change().fillna(0) * 100

    try:
        result.to_parquet(cache_fp)
    except Exception:
        pass

    return result


# =========================================================
# 主题评分主流程
# =========================================================
def evaluate_single_theme(tname: str, theme_cfg: Dict, codes: List[str],
                          etf_daily: pd.DataFrame, stock_daily: pd.DataFrame,
                          bench_daily: pd.DataFrame, all_theme_returns: List[float],
                          moneyflow: pd.DataFrame, top_df: pd.DataFrame,
                          daily_basic: pd.DataFrame, dc_hot: pd.DataFrame,
                          theme_index: pd.DataFrame = None,
                          market_filter: Dict = None) -> Dict[str, Any]:
    """评估单个主题 - 调用所有评分模块 (V10 7因子)"""
    etf_code = theme_cfg.get("etf", "")
    theme_style = theme_cfg.get("style", "")

    # ETF份额变化 (单独加载，可能慢)
    etf_share_chg = 0.0
    if etf_code:
        try:
            etf_share_chg = load_etf_share_change(etf_code)
        except Exception:
            etf_share_chg = 0.0

    # 1) Trend Quality
    trend_q = calc_trend_quality(etf_daily, stock_daily, codes, bench_daily)

    # 2) Capital Persistence
    capital_p = calc_capital_persistence(etf_daily, stock_daily, codes,
                                         moneyflow, etf_share_chg, top_df)

    # 3) Rotation Timing (最高权重)
    rotation_t = calc_rotation_timing(etf_daily, stock_daily, codes,
                                       all_theme_returns, theme_index)

    # 4) Leader Ecology
    leader_e = calc_leader_ecology(stock_daily, codes, top_df, None, theme_cfg)

    # 5) Expectation Gap
    expectation_g = calc_expectation_gap(etf_daily, stock_daily, codes,
                                          daily_basic, dc_hot)

    # 6) Catalyst (新)
    catalyst = calc_catalyst(etf_daily, stock_daily, codes, top_df,
                              moneyflow, etf_share_chg, theme_cfg)

    # 7) Theme Beta Adjustment (新)
    market_risk = market_filter.get("risk", "Normal") if market_filter else "Normal"
    beta_adj = calc_theme_beta_adjustment(theme_style, market_risk, market_filter)

    # 8) Theme Alpha (V10 7因子加权几何平均)
    alpha = calc_theme_alpha(trend_q, capital_p, rotation_t, leader_e,
                              expectation_g, catalyst, beta_adj)

    # 9) Theme Strength (加法)
    etf_score = trend_q["sub"].get("ma_breadth", 50.0)
    strength = calc_theme_strength(trend_q, capital_p, rotation_t, leader_e, etf_score)

    # 10) Lifecycle
    lifecycle = detect_lifecycle(etf_daily, trend_q, capital_p, rotation_t, leader_e)

    # 11) Future Return
    future_ret = predict_future_return(alpha["score"], lifecycle, rotation_t,
                                        expectation_g, trend_q)

    # 12) Signal
    signal = generate_signal(alpha["score"], lifecycle, future_ret, rotation_t)

    # 13) Confidence
    confidence = calc_confidence(alpha["score"], future_ret, lifecycle,
                                  rotation_t, expectation_g)

    leader_code = leader_e.get("details", {}).get("leader_code", "")
    leader_r20 = leader_e.get("details", {}).get("leader_r20", 0)

    return {
        "theme": tname,
        "theme_alpha_score": alpha["score"],
        "theme_strength_score": round(strength["score"], 1),
        "future_return_score": future_ret["score"],
        "future_p5": future_ret["p5"],
        "future_p10": future_ret["p10"],
        "future_p20": future_ret["p20"],
        "lifecycle": lifecycle["stage"],
        "lifecycle_reason": lifecycle.get("reason", ""),
        "lifecycle_risk": lifecycle.get("risk", ""),
        "leader": leader_code,
        "leader_score": round(leader_r20, 1),
        "leader_ret_avg": leader_e.get("details", {}).get("leader_ret_avg", 0),
        "follower_ret": leader_e.get("details", {}).get("follower_ret", 0),
        "n_stocks": len(codes),
        "confidence": round(confidence, 1),
        "rotation_timing": rotation_t["score"],
        "trend_quality": trend_q["score"],
        "capital_persistence": capital_p["score"],
        "leader_ecology": leader_e["score"],
        "expectation_gap": expectation_g["score"],
        "catalyst": catalyst["score"],
        "beta_adjust": alpha["beta_adjust"],
        "style_type": alpha["style_type"],
        "signal": signal,
        "raw_alpha": alpha["raw_alpha"],
        "factors": alpha["factors"],
        "etf_code": etf_code,
        "style": theme_style,
        "sub_scores": {
            "trend": trend_q["sub"],
            "capital": capital_p["sub"],
            "rotation": rotation_t["sub"],
            "leader": leader_e["sub"],
            "expectation": expectation_g["sub"],
            "catalyst": catalyst["sub"],
        },
        "details": {
            "trend": trend_q["details"],
            "capital": capital_p["details"],
            "rotation": rotation_t["details"],
            "leader": leader_e["details"],
            "expectation": expectation_g["details"],
            "catalyst": catalyst["details"],
            "beta_adj": beta_adj,
        },
    }


# =========================================================
# Theme Watchlist 观察池 (Buy/Watch/Avoid List)
# =========================================================
def classify_watchlist(results: List[Dict]) -> Dict[str, List[Dict]]:
    """将主题分类到 Buy List / Watch List / Avoid List

    Buy List:    Alpha ≥ 65 且 Confidence ≥ 70
    Watch List:  Alpha 55~65, Rotation 正在提升 (待启动)
    Avoid List:  Alpha < 50 或 Lifecycle=Distribution

    中间地带 (Alpha 50-55 且 非 Distribution): 归入 Watch List 底部

    核心价值: 提前2-5天把即将启动的主题放进Watch List
    """
    buy_list = []
    watch_list = []
    avoid_list = []

    for r in results:
        alpha = r["theme_alpha_score"]
        conf = r["confidence"]
        rotation = r["rotation_timing"]
        lifecycle = r["lifecycle"]

        # Buy List: Alpha高 + 置信度高
        if alpha >= CFG.BUYLIST_ALPHA and conf >= CFG.BUYLIST_CONF:
            r["watchlist_category"] = "Buy List"
            r["watchlist_reason"] = f"Alpha={alpha:.1f}≥{CFG.BUYLIST_ALPHA} + Conf={conf:.1f}≥{CFG.BUYLIST_CONF}"
            buy_list.append(r)
        # Avoid List: Alpha低 或 派发阶段
        elif alpha < CFG.AVOIDLIST_ALPHA or lifecycle == "Distribution":
            r["watchlist_category"] = "Avoid List"
            reason = f"Alpha={alpha:.1f}<{CFG.AVOIDLIST_ALPHA}" if alpha < CFG.AVOIDLIST_ALPHA else f"Lifecycle={lifecycle}"
            r["watchlist_reason"] = reason
            avoid_list.append(r)
        # Watch List: 中间地带 (Alpha 50-65)
        else:
            r["watchlist_category"] = "Watch List"
            # 判断Rotation是否在提升 (用rotation分数趋势)
            rotation_trend = rotation - 50  # 简化: >50视为上升趋势
            if CFG.WATCHLIST_ALPHA_MIN <= alpha < CFG.WATCHLIST_ALPHA_MAX:
                r["watchlist_reason"] = f"Alpha={alpha:.1f}∈[55,65) + Rotation={rotation:.1f} (待启动)"
            else:
                r["watchlist_reason"] = f"Alpha={alpha:.1f}∈[50,55) + Rotation={rotation:.1f} (观察)"
            watch_list.append(r)

    # 各列表内部按Alpha排序
    buy_list.sort(key=lambda x: x["theme_alpha_score"], reverse=True)
    watch_list.sort(key=lambda x: x["theme_alpha_score"], reverse=True)
    avoid_list.sort(key=lambda x: x["theme_alpha_score"], reverse=True)

    return {
        "buy_list": buy_list,
        "watch_list": watch_list,
        "avoid_list": avoid_list,
    }


def print_watchlist(watchlist: Dict[str, List[Dict]]):
    """打印 Watchlist 观察池"""
    buy_list = watchlist["buy_list"]
    watch_list = watchlist["watch_list"]
    avoid_list = watchlist["avoid_list"]

    total = len(buy_list) + len(watch_list) + len(avoid_list)

    print("\n" + "=" * 130)
    print(f"  Theme Watchlist 观察池 (共{total}个主题)")
    print(f"  Buy List: {len(buy_list)} | Watch List: {len(watch_list)} | Avoid List: {len(avoid_list)}")
    print("  核心价值: Watch List中的主题往往会在2-5天后进入Buy List")
    print("=" * 130)

    # Buy List
    print(f"\n  ★★★ Buy List ({len(buy_list)}个) - Alpha≥{CFG.BUYLIST_ALPHA} 且 Confidence≥{CFG.BUYLIST_CONF}")
    print("  " + "-" * 127)
    if buy_list:
        for i, r in enumerate(buy_list, 1):
            print(f"  {i:<3} {r['theme'][:20]:<22} Alpha={r['theme_alpha_score']:>5.1f} | "
                  f"Conf={r['confidence']:>5.1f} | {r['lifecycle']:<14} | "
                  f"Leader={r['leader']:<10} | {r['watchlist_reason']}")
    else:
        print(f"  (空) - 当前无主题同时满足Alpha≥{CFG.BUYLIST_ALPHA}和Confidence≥{CFG.BUYLIST_CONF}")

    # Watch List (重点)
    print(f"\n  ★★ Watch List ({len(watch_list)}个) - Alpha∈[{CFG.AVOIDLIST_ALPHA},{CFG.BUYLIST_ALPHA}) 待启动主题")
    print("  " + "-" * 127)
    if watch_list:
        hdr = f"  {'#':<3} {'Theme':<22} {'Alpha':>6} {'Conf':>5} {'Rotation':>9} {'Lifecycle':<14} {'Leader':<12} {'Reason'}"
        print(hdr)
        for i, r in enumerate(watch_list[:15], 1):  # 最多显示15个
            print(f"  {i:<3} {r['theme'][:20]:<22} {r['theme_alpha_score']:>6.1f} {r['confidence']:>5.1f} "
                  f"{r['rotation_timing']:>9.1f} {r['lifecycle'][:14]:<14} {r['leader']:<12} {r['watchlist_reason']}")
        if len(watch_list) > 15:
            print(f"  ... 还有 {len(watch_list) - 15} 个主题")
    else:
        print(f"  (空) - 当前无主题处于待启动区间")

    # Avoid List
    print(f"\n  ✗ Avoid List ({len(avoid_list)}个) - Alpha<{CFG.AVOIDLIST_ALPHA} 或 Lifecycle=Distribution")
    print("  " + "-" * 127)
    if avoid_list:
        for i, r in enumerate(avoid_list[:10], 1):  # 最多显示10个
            print(f"  {i:<3} {r['theme'][:20]:<22} Alpha={r['theme_alpha_score']:>5.1f} | "
                  f"{r['lifecycle']:<14} | {r['watchlist_reason']}")
        if len(avoid_list) > 10:
            print(f"  ... 还有 {len(avoid_list) - 10} 个主题")
    else:
        print(f"  (空)")


# =========================================================
# 输出格式化
# =========================================================
def print_top_results(results: List[Dict], market_filter: Dict, top_n: int = 15):
    """打印 TOP N 主题排名"""
    print("\n" + "=" * 130)
    print(f"  Theme Alpha Researcher - 未来5日主题主线预测 (Top {top_n})")
    print(f"  Market Filter: {market_filter['risk']} (adjust={market_filter['adjust']}) | "
          f"Score={market_filter['score']:.1f}")
    print("=" * 130)

    hdr = (f"  {'#':<3} {'Theme':<22} {'Alpha':>6} {'Strength':>9} {'Future':>7} "
           f"{'Conf':>5} {'Lifecycle':<14} {'Signal':<11} {'Leader':<12} "
           f"{'L_Score':>7} {'Rot':>5} {'Trd':>5} {'Cap':>5} {'Ldr_E':>6} {'Exp':>5} {'Cat':>5} {'Style':<8} {'Src':<3}")
    print(hdr)
    print("  " + "-" * 150)

    for i, r in enumerate(results[:top_n], 1):
        signal_mark = ""
        if r["signal"] == "Strong Buy":
            signal_mark = " ★★★"
        elif r["signal"] == "Buy":
            signal_mark = " ★★"
        elif r["signal"] == "Watch":
            signal_mark = " ★"
        elif r["signal"] == "Reduce":
            signal_mark = " ↓"
        elif r["signal"] == "Avoid":
            signal_mark = " ✗"

        src = r.get("etf_source", "")
        src_mark = "ETF" if src == "ETF" else ("自构" if src == "Synthetic" else "")
        style = r.get("style_type", "neutral")
        style_short = {"growth": "成长", "defensive": "防御", "cycle": "周期", "neutral": "中性"}.get(style, "中性")

        line = (
            f"  {i:<3} {r['theme'][:20]:<22} {r['theme_alpha_score']:>6.1f} "
            f"{r['theme_strength_score']:>9.1f} {r['future_return_score']:>7.1f} "
            f"{r['confidence']:>5.1f} {r['lifecycle'][:14]:<14} "
            f"{r['signal'][:11]:<11}{signal_mark} {r['leader']:<12} "
            f"{r['leader_score']:>7.1f} {r['rotation_timing']:>5.1f} "
            f"{r['trend_quality']:>5.1f} {r['capital_persistence']:>5.1f} "
            f"{r['leader_ecology']:>6.1f} {r['expectation_gap']:>5.1f} "
            f"{r.get('catalyst', 50):>5.1f} {style_short:<8} {src_mark:<3}"
        )
        print(line)

    print("  " + "-" * 150)
    print(f"  Signal: Strong Buy(★★★) / Buy(★★) / Watch(★) / Reduce(↓) / Avoid(✗)")
    print(f"  Src: ETF=真实ETF | 自构=无ETF时用成分股等权指数回退")
    print(f"  Style: 成长/防御/周期/中性 - 不同市场环境下差异化调整")
    print(f"  排序: 按 Theme Alpha Score (Future Alpha, 非Current Heat)")
    print(f"  V10 7因子: Rotation(25%)+Capital(20%)+Trend(20%)+Leader(15%)+Expectation(10%)+Catalyst(5%)+BetaAdj(5%)")
    print(f"  加权几何平均: 任一因子差都会拉低整体, 权重反映重要性")


def print_detail(r: Dict, name_map: Dict[str, str]):
    """打印单个主题详细分解"""
    print("\n" + "=" * 100)
    print(f"  主题详情: {r['theme']}")
    print("=" * 100)
    print(f"  ETF代码: {r.get('etf_code', '无')} | 风格: {r.get('style', '')} | 股票数: {r['n_stocks']}")
    print(f"  龙头: {r['leader']} ({name_map.get(r['leader'], '')}) | 龙头20日涨幅: {r['leader_score']}%")
    print(f"  中军平均涨幅: {r.get('leader_ret_avg', 0)}% | 补涨平均涨幅: {r.get('follower_ret', 0)}%")

    print(f"\n  【核心评分】")
    print(f"    Theme Alpha Score:    {r['theme_alpha_score']:.1f}  (raw_alpha={r['raw_alpha']}, beta_adjust={r.get('beta_adjust', 1.0)})")
    print(f"    Theme Strength Score: {r['theme_strength_score']:.1f}")
    print(f"    Future Return Score:  {r['future_return_score']:.1f} (5d={r['future_p5']}%, 10d={r['future_p10']}%, 20d={r['future_p20']}%)")
    print(f"    Confidence:           {r['confidence']:.1f}")

    print(f"\n  【Alpha因子分解 (V10 7因子加权几何平均)】")
    f = r["factors"]
    print(f"    Rotation Timing:      {r['rotation_timing']:.1f}  (factor={f['rotation']}) [权重25% 核心]")
    print(f"    Capital Persistence:  {r['capital_persistence']:.1f}  (factor={f['capital']}) [权重20%]")
    print(f"    Trend Quality:        {r['trend_quality']:.1f}  (factor={f['trend']}) [权重20%]")
    print(f"    Leader Ecology:       {r['leader_ecology']:.1f}  (factor={f['leader']}) [权重15%]")
    print(f"    Expectation Gap:      {r['expectation_gap']:.1f}  (factor={f['expectation']}) [权重10%]")
    print(f"    Catalyst:             {r.get('catalyst', 50):.1f}  (factor={f.get('catalyst', 50)}) [权重5%]")
    print(f"    Beta Adjustment:      {r.get('beta_adjust', 1.0):.3f}  (style={r.get('style_type', 'neutral')}) [权重5%]")

    print(f"\n  【子分数】")
    for mod_name, subs in r["sub_scores"].items():
        sub_str = " | ".join([f"{k}={v}" for k, v in subs.items()])
        print(f"    {mod_name:<14}: {sub_str}")

    print(f"\n  【生命周期】")
    print(f"    Stage: {r['lifecycle']} | {r['lifecycle_reason']} | 风险: {r['lifecycle_risk']}")

    print(f"\n  【信号】")
    print(f"    Signal: {r['signal']}")

    print(f"\n  【详情】")
    for mod_name, det in r["details"].items():
        if det:
            det_str = " | ".join([f"{k}={v}" for k, v in det.items() if not isinstance(v, (list, dict))])
            print(f"    {mod_name:<14}: {det_str}")


def save_results(results: List[Dict], market_filter: Dict, trade_date: str):
    """保存结果到 JSON 和 CSV"""
    output = {
        "trade_date": trade_date,
        "market_filter": {
            "risk": market_filter["risk"],
            "score": market_filter["score"],
            "adjust": market_filter["adjust"],
        },
        "top_themes": [{k: v for k, v in r.items() if k not in ("sub_scores", "details")}
                       for r in results],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    with open(CFG.OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    df_data = []
    for r in results:
        df_data.append({
            "trade_date": trade_date,
            "theme": r["theme"],
            "theme_alpha_score": r["theme_alpha_score"],
            "theme_strength_score": r["theme_strength_score"],
            "future_return_score": r["future_return_score"],
            "future_p5": r["future_p5"],
            "future_p10": r["future_p10"],
            "future_p20": r["future_p20"],
            "confidence": r["confidence"],
            "lifecycle": r["lifecycle"],
            "leader": r["leader"],
            "leader_score": r["leader_score"],
            "rotation_timing": r["rotation_timing"],
            "trend_quality": r["trend_quality"],
            "capital_persistence": r["capital_persistence"],
            "leader_ecology": r["leader_ecology"],
            "expectation_gap": r["expectation_gap"],
            "catalyst": r.get("catalyst", 50),
            "beta_adjust": r.get("beta_adjust", 1.0),
            "style_type": r.get("style_type", "neutral"),
            "signal": r["signal"],
            "watchlist_category": r.get("watchlist_category", ""),
            "watchlist_reason": r.get("watchlist_reason", ""),
            "n_stocks": r["n_stocks"],
        })
    pd.DataFrame(df_data).to_csv(CFG.OUTPUT_CSV, index=False, encoding="utf-8-sig")

    print(f"\n  [保存] 结果已保存:")
    print(f"    JSON: {CFG.OUTPUT_JSON}")
    print(f"    CSV:  {CFG.OUTPUT_CSV}")


# =========================================================
# Walk-Forward 回测框架
# =========================================================
def walk_forward_backtest(n_days: int = 60, top_k: int = 5):
    """Walk-Forward 回测 - 验证未来5/20日超额收益

    对过去 n_days 天，每天计算TOP-K主题的 Alpha 排名，
    然后统计 TOP-K 主题未来5/20日的实际收益 vs 全市场平均。
    """
    print(f"\n{'='*80}")
    print(f"  Walk-Forward 回测 ({n_days}天, TOP-{top_k})")
    print(f"{'='*80}")

    today = datetime.now().strftime("%Y%m%d")
    end_dt = datetime.now() - timedelta(days=1)
    start_dt = end_dt - timedelta(days=n_days + 60)

    # 加载HS300作为基准
    hs300_full = load_index_daily(CFG.ETF_BENCHMARK,
                                   start_dt.strftime("%Y%m%d"),
                                   end_dt.strftime("%Y%m%d"))

    if hs300_full.empty:
        print("  [Error] 无法加载HS300数据")
        return

    trade_dates = sorted(hs300_full["trade_date"].unique())
    test_dates = trade_dates[-n_days:] if len(trade_dates) >= n_days else trade_dates

    print(f"  测试日期: {test_dates[0]} ~ {test_dates[-1]} ({len(test_dates)}天)")
    print(f"  评估指标: TOP-{top_k} 主题ETF未来5/20日收益 vs HS300")

    results = []
    for i, test_date in enumerate(test_dates[:-20]):
        if i % 10 == 0:
            print(f"  进度: {i}/{len(test_dates)-20} ({test_date})")

        try:
            # 模拟历史评分
            hist_end = (datetime.strptime(test_date, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
            hist_start = (datetime.strptime(test_date, "%Y%m%d") - timedelta(days=180)).strftime("%Y%m%d")

            theme_cfg_dict = load_theme_config()
            universe, name_map = load_theme_universe()

            hist_scores = []
            for tname, tcfg in list(theme_cfg_dict.items())[:30]:  # 限制30个主题加速
                codes = universe.get(tname, [])
                if len(codes) < 5:
                    continue
                etf_code = tcfg.get("etf", "")
                etf_df = load_etf_daily(etf_code, hist_start, hist_end) if etf_code else pd.DataFrame()
                if etf_df.empty or len(etf_df) < 30:
                    continue
                stock_df = load_stock_daily_batch(codes[:20], hist_start, hist_end)

                bench_df = hs300_full[hs300_full["trade_date"] <= test_date].tail(120)

                trend_q = calc_trend_quality(etf_df, stock_df, codes, bench_df)
                capital_p = calc_capital_persistence(etf_df, stock_df, codes, pd.DataFrame(), 0, pd.DataFrame())
                rotation_t = calc_rotation_timing(etf_df, stock_df, codes)
                leader_e = calc_leader_ecology(stock_df, codes, pd.DataFrame(), None, tcfg)
                expectation_g = calc_expectation_gap(etf_df, stock_df, codes)

                alpha = calc_theme_alpha(trend_q, capital_p, rotation_t, leader_e, expectation_g)

                # 计算未来5/20日收益
                idx_pos = etf_df[etf_df["trade_date"] == test_date].index
                if len(idx_pos) == 0:
                    continue
                etf_idx = idx_pos[0]
                future_5d = (etf_df.iloc[min(etf_idx + 5, len(etf_df) - 1)]["close"] /
                             etf_df.iloc[etf_idx]["close"] - 1) * 100
                future_20d = (etf_df.iloc[min(etf_idx + 20, len(etf_df) - 1)]["close"] /
                              etf_df.iloc[etf_idx]["close"] - 1) * 100

                hist_scores.append({
                    "theme": tname,
                    "alpha": alpha["score"],
                    "future_5d": future_5d,
                    "future_20d": future_20d,
                })

            if len(hist_scores) < top_k:
                continue

            df_h = pd.DataFrame(hist_scores).sort_values("alpha", ascending=False)
            top_k_themes = df_h.head(top_k)

            results.append({
                "date": test_date,
                "top_k_avg_alpha": round(top_k_themes["alpha"].mean(), 1),
                "top_k_avg_5d": round(top_k_themes["future_5d"].mean(), 2),
                "top_k_avg_20d": round(top_k_themes["future_20d"].mean(), 2),
                "all_avg_5d": round(df_h["future_5d"].mean(), 2),
                "all_avg_20d": round(df_h["future_20d"].mean(), 2),
                "excess_5d": round(top_k_themes["future_5d"].mean() - df_h["future_5d"].mean(), 2),
                "excess_20d": round(top_k_themes["future_20d"].mean() - df_h["future_20d"].mean(), 2),
            })
        except Exception as e:
            print(f"  [Skip] {test_date}: {e}")
            continue

    if not results:
        print("  无回测结果")
        return

    df_r = pd.DataFrame(results)
    print(f"\n  【回测统计】({len(df_r)}天)")
    print(f"  TOP-{top_k} 平均Alpha:      {df_r['top_k_avg_alpha'].mean():.1f}")
    print(f"  TOP-{top_k} 5日收益:        {df_r['top_k_avg_5d'].mean():.2f}%  (全市场 {df_r['all_avg_5d'].mean():.2f}%)  超额 {df_r['excess_5d'].mean():.2f}%")
    print(f"  TOP-{top_k} 20日收益:       {df_r['top_k_avg_20d'].mean():.2f}%  (全市场 {df_r['all_avg_20d'].mean():.2f}%)  超额 {df_r['excess_20d'].mean():.2f}%")
    print(f"  5日胜率: {(df_r['excess_5d'] > 0).sum() / len(df_r) * 100:.1f}%")
    print(f"  20日胜率: {(df_r['excess_20d'] > 0).sum() / len(df_r) * 100:.1f}%")
    print(f"  5日 IC: {df_r['top_k_avg_alpha'].corr(df_r['top_k_avg_5d']):.3f}")
    print(f"  20日 IC: {df_r['top_k_avg_alpha'].corr(df_r['top_k_avg_20d']):.3f}")


# =========================================================
# 主函数
# =========================================================
def main():
    parser = argparse.ArgumentParser(description="Theme Alpha Researcher - 未来Alpha预测")
    parser.add_argument("--top", type=int, default=15, help="输出TOP N (默认15)")
    parser.add_argument("--debug", type=str, default="", help="调试单个主题")
    parser.add_argument("--backtest", type=int, default=0, help="Walk-Forward回测天数")
    parser.add_argument("--no-cache", action="store_true", help="不使用缓存")
    parser.add_argument("--max-themes", type=int, default=0, help="限制主题数(测试用)")
    args = parser.parse_args()

    # 回测模式
    if args.backtest > 0:
        walk_forward_backtest(n_days=args.backtest, top_k=5)
        return

    print("=" * 100)
    print("  Theme Alpha Researcher V10 - 全球顶级量化研究员风格")
    print("  目标: 预测未来5日主线主题 + 未来20日超额收益")
    print("  模型: V10 7因子加权几何平均 + Theme Watchlist观察池")
    print("  7因子: Rotation(25%)+Capital(20%)+Trend(20%)+Leader(15%)")
    print("        +Expectation(10%)+Catalyst(5%)+BetaAdj(5%)")
    print("=" * 100)

    # 1) 加载主题配置
    theme_cfg_dict = load_theme_config()
    if not theme_cfg_dict:
        return

    universe, name_map = load_theme_universe()
    if not universe:
        return

    # 2) 确定交易日
    today_str = datetime.now().strftime("%Y%m%d")
    end_date = today_str
    start_date = (datetime.now() - timedelta(days=CFG.LOOKBACK_DAYS)).strftime("%Y%m%d")
    print(f"\n[数据范围] {start_date} ~ {end_date}")

    # 3) 加载市场基准 (HS300 + ZZ1000)
    print("\n[加载市场基准]")
    hs300 = load_index_daily(CFG.ETF_BENCHMARK, start_date, end_date)
    zz1000 = load_index_daily("000852.SH", start_date, end_date)
    print(f"  HS300: {len(hs300)}天 | ZZ1000: {len(zz1000)}天")

    # 4) 加载龙虎榜 + 资金流 (最近10天)
    print("\n[加载龙虎榜/资金流]")
    recent_dates = []
    if not hs300.empty:
        recent_dates = sorted(hs300["trade_date"].unique())[-10:]

    top_df = pd.DataFrame()
    moneyflow_df = pd.DataFrame()
    for d in recent_dates[-3:]:  # 最近3天
        try:
            top_list = dl.load_top_list(d)
            if top_list is not None and not top_list.empty:
                top_df = pd.concat([top_df, top_list], ignore_index=True)
        except Exception:
            pass

    # 资金流按日期加载
    for d in recent_dates[-2:]:  # 最近2天
        try:
            mf = dl.load_moneyflow_by_date(d)
            if mf is not None and not mf.empty:
                moneyflow_df = pd.concat([moneyflow_df, mf], ignore_index=True)
        except Exception:
            pass
    print(f"  龙虎榜: {len(top_df)}条 | 资金流: {len(moneyflow_df)}条")

    # 5) DC热度榜
    dc_hot = pd.DataFrame()
    if recent_dates:
        dc_hot = load_dc_hot(recent_dates[-1])
        print(f"  DC热度: {len(dc_hot)}条")

    # 6) Market Filter
    print("\n[计算市场环境过滤]")
    market_filter = calc_market_filter(hs300, zz1000, all_amount=0, limit_df=top_df)
    print(f"  Market Risk: {market_filter['risk']} | Score: {market_filter['score']:.1f} | Adjust: {market_filter['adjust']}")

    # 7) 预加载所有ETF数据 + 无ETF主题自构指数回退
    print("\n[预加载主题ETF数据]")
    etf_data_dict = {}
    etf_source_dict = {}  # 记录每个主题ETF数据来源: "ETF" / "Synthetic"
    theme_list = list(theme_cfg_dict.keys())
    if args.max_themes > 0:
        theme_list = theme_list[:args.max_themes]
    if args.debug:
        theme_list = [t for t in theme_list if t == args.debug] or [args.debug]

    for tname in theme_list:
        tcfg = theme_cfg_dict.get(tname, {})
        etf_code = tcfg.get("etf", "")
        if etf_code:
            etf_df = load_etf_daily(etf_code, start_date, end_date)
            if not etf_df.empty:
                etf_data_dict[tname] = etf_df
                etf_source_dict[tname] = "ETF"
                continue
        # 无ETF或ETF加载失败: 自构等权指数回退
        codes_for_synth = universe.get(tname, [])
        if len(codes_for_synth) >= 3:
            synth_df = build_synthetic_etf_from_stocks(codes_for_synth, start_date, end_date, hs300)
            if not synth_df.empty and len(synth_df) >= 30:
                etf_data_dict[tname] = synth_df
                etf_source_dict[tname] = "Synthetic"
    n_etf = sum(1 for v in etf_source_dict.values() if v == "ETF")
    n_synth = sum(1 for v in etf_source_dict.values() if v == "Synthetic")
    print(f"  有效ETF主题: {len(etf_data_dict)}/{len(theme_list)} (ETF={n_etf}, 自构指数={n_synth})")

    # 8) 计算全主题20日收益率 (拥挤度)
    all_theme_returns = calculate_all_theme_returns(etf_data_dict)
    print(f"  全主题20日收益样本: {len(all_theme_returns)}")

    # 9) 逐主题评分
    print(f"\n[评分进度]")
    results = []
    for idx, tname in enumerate(theme_list, 1):
        if tname not in theme_cfg_dict:
            continue
        tcfg = theme_cfg_dict[tname]
        codes = universe.get(tname, [])
        if len(codes) < CFG.MIN_THEME_STOCKS:
            continue

        etf_df = etf_data_dict.get(tname, pd.DataFrame())
        if etf_df.empty:
            continue

        if idx % 10 == 0 or idx <= 3:
            print(f"  [{idx}/{len(theme_list)}] {tname}...")

        try:
            # 加载个股数据 (限制20只避免过慢)
            sample_codes = codes[:30]
            stock_df = load_stock_daily_batch(sample_codes, start_date, end_date)

            r = evaluate_single_theme(
                tname=tname, theme_cfg=tcfg, codes=sample_codes,
                etf_daily=etf_df, stock_daily=stock_df,
                bench_daily=hs300, all_theme_returns=all_theme_returns,
                moneyflow=moneyflow_df, top_df=top_df,
                daily_basic=pd.DataFrame(), dc_hot=dc_hot,
                theme_index=None,
                market_filter=market_filter
            )
            r["etf_source"] = etf_source_dict.get(tname, "")
            results.append(r)
        except Exception as e:
            print(f"  [Skip] {tname}: {e}")
            continue

    if not results:
        print("\n[Error] 无评分结果")
        return

    # 10) 按Theme Alpha排序
    results.sort(key=lambda x: x["theme_alpha_score"], reverse=True)

    # 11) 输出
    print_top_results(results, market_filter, top_n=args.top)

    # 11.5) Theme Watchlist 观察池
    watchlist = classify_watchlist(results)
    print_watchlist(watchlist)

    # 调试模式: 打印详情
    if args.debug and results:
        debug_theme = next((r for r in results if r["theme"] == args.debug), None)
        if debug_theme:
            print_detail(debug_theme, name_map)

    # 12) 保存
    save_results(results, market_filter, end_date)

    # 13) 信号分布
    print(f"\n  【信号分布】")
    sig_counts = {}
    for r in results:
        sig_counts[r["signal"]] = sig_counts.get(r["signal"], 0) + 1
    for sig, cnt in sorted(sig_counts.items(), key=lambda x: -x[1]):
        print(f"    {sig:<12}: {cnt} ({cnt/len(results)*100:.1f}%)")

    print(f"\n{'='*100}")
    print(f"  Theme Alpha Researcher 完成")
    print(f"  Top {args.top}: 按 Future Alpha 排序 (非Current Heat)")
    print(f"{'='*100}")


if __name__ == "__main__":
    main()

