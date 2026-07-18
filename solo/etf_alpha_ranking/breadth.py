#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Module F - Breadth Features
===========================
For each theme, measure participation breadth across its constituents.

Features per theme:
  theme_stock_count       : number of stocks
  above_MA20_ratio        : fraction of stocks above their MA20
  above_MA60_ratio        : fraction of stocks above their MA60
  strong_stock_ratio      : fraction with return >= threshold
  RS_top30_ratio          : fraction in top 30% of cross-sectional RS
  limit_up_count          : count of strong daily moves (>9.5%)
  breadth_acceleration    : change in above_MA20_ratio (recent vs prior)

Output: breadth_score (0-100) per theme, used by Module D.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List

import numpy as np
import pandas as pd

from . import indicators as ind

LOG = logging.getLogger("etf_alpha_ranking.breadth")


@dataclass
class BreadthResult:
    theme: str = ""
    theme_stock_count: int = 0
    above_MA20_ratio: float = 0.0
    above_MA60_ratio: float = 0.0
    strong_stock_ratio: float = 0.0
    RS_top30_ratio: float = 0.0
    limit_up_count: int = 0
    breadth_acceleration: float = 0.0
    breadth_score: float = 0.0


class BreadthEngine:
    def __init__(self, config: dict):
        cfg = config.get("breadth", {})
        self.ma_short = cfg.get("ma_short", 20)
        self.ma_long = cfg.get("ma_long", 60)
        self.rs_top_pct = cfg.get("rs_top_pct", 0.30)
        self.strong_ret = cfg.get("strong_return_threshold", 0.03)
        self.accel_period = cfg.get("breadth_accel_period", 10)
        self._benchmark: np.ndarray = np.array([])

    def set_benchmark(self, bench_close: np.ndarray):
        self._benchmark = np.asarray(bench_close, dtype=float)

    def _rs_vs_bench(self, close: np.ndarray) -> float:
        if len(self._benchmark) < 20 or len(close) < 20:
            return 0.0
        b = self._benchmark[-21:]
        e = close[-21:]
        if abs(b[0]) < 1e-9 or abs(e[0]) < 1e-9:
            return 0.0
        return (e[-1] / e[0] - 1.0) - (b[-1] / b[0] - 1.0)

    def score(self, theme: str, stock_data: Dict[str, pd.DataFrame],
              all_rs: np.ndarray = None) -> BreadthResult:
        r = BreadthResult(theme=theme)
        if not stock_data:
            return r

        rs_values = []
        above20, above60, strong, limit_up = 0, 0, 0, 0
        accel_recent, accel_prior = [], []
        count = 0
        for code, df in stock_data.items():
            if df is None or df.empty or len(df) < self.ma_long + 1:
                continue
            count += 1
            close = df["close"].values.astype(float)
            ma20 = ind.sma(close, self.ma_short)
            ma60 = ind.sma(close, self.ma_long)
            if np.isfinite(ma20[-1]):
                above20 += int(close[-1] > ma20[-1])
            if np.isfinite(ma60[-1]):
                above60 += int(close[-1] > ma60[-1])
            # strong stock: 20d return >= threshold
            if len(close) > 20:
                ret20 = close[-1] / close[-21] - 1.0
                if ret20 >= self.strong_ret:
                    strong += 1
            # limit-up proxy: daily pct >= 9.5%
            if "pct_chg" in df.columns and len(df) > 0:
                if float(df["pct_chg"].iloc[-1]) >= 9.5:
                    limit_up += 1
            # RS
            rs = self._rs_vs_bench(close)
            rs_values.append(rs)
            # breadth acceleration: above-MA20 today vs accel_period ago
            if len(close) > self.ma_short + self.accel_period:
                ma20_full = ind.sma(close, self.ma_short)
                now_above = close[-1] > ma20_full[-1]
                prev_above = close[-1 - self.accel_period] > ma20_full[-1 - self.accel_period]
                accel_recent.append(int(now_above))
                accel_prior.append(int(prev_above))

        r.theme_stock_count = count
        if count == 0:
            return r
        r.above_MA20_ratio = above20 / count
        r.above_MA60_ratio = above60 / count
        r.strong_stock_ratio = strong / count
        r.limit_up_count = limit_up

        if rs_values:
            arr = np.array(rs_values)
            # fraction in top 30% of THIS theme's RS distribution
            cutoff = np.quantile(arr, 1.0 - self.rs_top_pct) if len(arr) > 1 else arr[0]
            r.RS_top30_ratio = float(np.mean(arr >= cutoff)) if len(arr) > 1 else 0.0
            # cross-sectional comparison if provided
            if all_rs is not None and len(all_rs) > 1:
                cs_cut = np.quantile(all_rs, 1.0 - self.rs_top_pct)
                r.RS_top30_ratio = float(np.mean(arr >= cs_cut))

        if accel_recent and accel_prior:
            r.breadth_acceleration = (np.mean(accel_recent) - np.mean(accel_prior))

        # breadth_score: weighted combination
        r.breadth_score = float(np.clip(
            30.0 * r.above_MA20_ratio
            + 20.0 * r.above_MA60_ratio
            + 20.0 * r.strong_stock_ratio
            + 15.0 * r.RS_top30_ratio
            + 15.0 * float(r.limit_up_count) / max(count, 1)
            + 10.0 * max(r.breadth_acceleration, 0), 0, 100))
        return r

    def score_all(self, theme_stocks: Dict[str, List[str]],
                  all_stock_data: Dict[str, pd.DataFrame]) -> Dict[str, BreadthResult]:
        # cross-sectional RS for top-30% comparison
        all_rs = []
        for df in all_stock_data.values():
            if df is None or df.empty or len(df) < 21:
                continue
            all_rs.append(self._rs_vs_bench(df["close"].values.astype(float)))
        all_rs_arr = np.array(all_rs) if all_rs else None

        out: Dict[str, BreadthResult] = {}
        for theme, stocks in theme_stocks.items():
            sd = {c: all_stock_data[c] for c in stocks if c in all_stock_data}
            out[theme] = self.score(theme, sd, all_rs_arr)
        return out
