#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Label Engine
=============
Builds the Learning-To-Rank supervision target.

For each (date, ETF):
  future_return20 / 40 / 60 = realized N-day forward return
  default target horizon = 40

Cross-sectional ranking label (per date):
  Top 10%        -> 3
  10% - 30%      -> 2
  30% - 70%      -> 1
  Bottom 30%     -> 0

No future leakage: labels are only attached to historical samples and
never used as features.
"""
from __future__ import annotations

import logging
from typing import List

import numpy as np
import pandas as pd

LOG = logging.getLogger("etf_alpha_ranking.labels")


class LabelBuilder:
    def __init__(self, config: dict):
        cfg = config.get("label", {})
        self.horizons: List[int] = cfg.get("horizons", [20, 40, 60])
        self.default_horizon: int = cfg.get("default_horizon", 40)
        self.top10 = cfg.get("top10_label", 3)
        self.top30 = cfg.get("top30_label", 2)
        self.middle = cfg.get("middle_label", 1)
        self.bottom = cfg.get("bottom_label", 0)

    def add_future_returns(self, panel: pd.DataFrame,
                           etf_col: str = "etf",
                           date_col: str = "date",
                           close_map=None) -> pd.DataFrame:
        """Add future return columns to a long-format panel.

        Args:
            panel: DataFrame with at least [date, etf]
            close_map: {etf_code: DataFrame[trade_date, close]} for each ETF
        Returns panel with added fwd_{h}d columns.
        """
        if close_map is None:
            return panel
        panel = panel.copy()
        # initialize future-return columns
        for h in self.horizons:
            panel[f"fwd_{h}d"] = np.nan

        # Build a lookup: {(etf, date_str): {h: fwd_return}} from price history
        lookup = {}
        for code, df in close_map.items():
            if df is None or df.empty:
                continue
            df = df.sort_values("trade_date").reset_index(drop=True)
            close = df["close"].values.astype(float)
            dates = df["trade_date"].astype(str).values
            n = len(close)
            for i in range(n):
                key = (code, dates[i])
                vals = {}
                for h in self.horizons:
                    if i + h < n:
                        vals[h] = float(close[i + h] / close[i] - 1.0)
                    else:
                        vals[h] = np.nan
                lookup[key] = vals

        # vectorized assignment via a map
        keys = list(zip(panel[etf_col].astype(str), panel[date_col].astype(str)))
        for h in self.horizons:
            col = f"fwd_{h}d"
            panel[col] = [lookup.get(k, {}).get(h, np.nan) for k in keys]
        return panel

    def add_rank_label(self, panel: pd.DataFrame,
                       horizon: int = None) -> pd.DataFrame:
        """Add a cross-sectional ranking label per date.

        Label = 3 (top 10%), 2 (10-30%), 1 (30-70%), 0 (bottom 30%).
        """
        h = horizon or self.default_horizon
        col = f"fwd_{h}d"
        label_col = "rank_label"
        if col not in panel.columns:
            panel[col] = np.nan
        panel[label_col] = np.nan

        def _label_group(s: pd.Series) -> pd.Series:
            valid = s.dropna()
            if len(valid) < 4:
                return pd.Series(np.nan, index=s.index)
            ranks = valid.rank(pct=True, method="average")
            out = pd.Series(np.nan, index=s.index)
            for idx, pct in ranks.items():
                if pct >= 0.90:
                    out[idx] = self.top10
                elif pct >= 0.70:
                    out[idx] = self.top30
                elif pct >= 0.30:
                    out[idx] = self.middle
                else:
                    out[idx] = self.bottom
            return out

        panel[label_col] = panel.groupby("date")[col].transform(_label_group)
        return panel

    def get_target_col(self) -> str:
        return "rank_label"

    def get_return_col(self, horizon: int = None) -> str:
        return f"fwd_{horizon or self.default_horizon}d"
