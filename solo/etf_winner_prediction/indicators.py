#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ETF Winner Prediction - 技术指标工具模块
===========================================
复用 etf_alpha_engine 的指标模块，直接导入。
"""
import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 直接复用 etf_alpha_engine 的指标模块
from etf_alpha_engine.indicators import (
    sma, ema, rma,
    roc, returns, relative_strength,
    atr, natr, volatility, max_drawdown, ulcer_index,
    adx, rsi, slope, hurst_exponent,
    breakout_pct, new_high_count,
    normalize, percentile_rank, winsorize, zscore,
    sharpe_ratio, sortino_ratio, calmar_ratio,
    beta, rolling_beta, rolling_corr,
    volume_ratio, consecutive_up_days, above_ema_days,
    consecutive_count,
)

__all__ = [
    "sma", "ema", "rma",
    "roc", "returns", "relative_strength",
    "atr", "natr", "volatility", "max_drawdown", "ulcer_index",
    "adx", "rsi", "slope", "hurst_exponent",
    "breakout_pct", "new_high_count",
    "normalize", "percentile_rank", "winsorize", "zscore",
    "sharpe_ratio", "sortino_ratio", "calmar_ratio",
    "beta", "rolling_beta", "rolling_corr",
    "volume_ratio", "consecutive_up_days", "above_ema_days",
    "consecutive_count",
]