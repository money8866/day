#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Trainer
=======
Time-series split training for the LGBMRanker.

Default split (configurable):
  Train : 2018-01-01 ~ 2023-12-31
  Valid : 2024-01-01 ~ 2024-12-31
  Test  : 2025-01-01 ~ current

NO random shuffle. The split is strictly chronological to avoid future
leakage. Walk-forward retraining is supported via ``retrain_interval``.

The trainer also supports building the full training panel from TDX
history: it iterates historical dates, computes features + labels, and
produces one row per (date, ETF).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .labels import LabelBuilder
from .ranker import LGBRankerModel

LOG = logging.getLogger("etf_alpha_ranking.trainer")


@dataclass
class TrainResult:
    n_train: int = 0
    n_valid: int = 0
    n_features: int = 0
    best_iteration: int = 0
    valid_ndcg_5: float = 0.0
    feature_importance_top: list = None


class Trainer:
    def __init__(self, config: dict):
        self.config = config
        tcfg = config.get("training", {})
        self.train_start = tcfg.get("train_start", "20180101")
        self.train_end = tcfg.get("train_end", "20231231")
        self.valid_start = tcfg.get("valid_start", "20240101")
        self.valid_end = tcfg.get("valid_end", "20241231")
        self.test_start = tcfg.get("test_start", "20250101")
        self.test_end = tcfg.get("test_end", "")
        self.retrain_interval = tcfg.get("retrain_interval", 60)
        self.label_builder = LabelBuilder(config)
        self.ranker = LGBRankerModel(config)

    # ------------------------------------------------------------------
    # Split
    # ------------------------------------------------------------------
    def split(self, panel: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Chronological split. NO shuffle.

        If the configured date ranges yield an empty training partition (e.g.
        the available history is shorter than the configured split), fall back
        to a chronological 70/15/15 split of the actually-available dates.
        """
        df = panel.copy()
        df["date"] = df["date"].astype(str)
        train = df[(df["date"] >= self.train_start) & (df["date"] <= self.train_end)]
        valid = df[(df["date"] >= self.valid_start) & (df["date"] <= self.valid_end)]
        test = df[(df["date"] >= self.test_start)]
        if self.test_end:
            test = test[test["date"] <= self.test_end]

        # Auto-fallback: if the configured train split is empty, use a
        # chronological quantile split over the available dates.
        if train.empty and not df.empty:
            dates = sorted(df["date"].unique())
            n = len(dates)
            # reserve last 15% as test, prior 15% as valid, rest as train
            n_test = max(1, int(n * 0.15))
            n_valid = max(1, int(n * 0.15))
            n_train = n - n_test - n_valid
            if n_train < 5:
                # very small dataset: 70/30 train/valid, no test
                n_train = max(1, int(n * 0.7))
                n_valid = n - n_train
                train_dates = set(dates[:n_train])
                valid_dates = set(dates[n_train:n_train + n_valid])
                test_dates = set()
            else:
                train_dates = set(dates[:n_train])
                valid_dates = set(dates[n_train:n_train + n_valid])
                test_dates = set(dates[n_train + n_valid:])
            train = df[df["date"].isin(train_dates)]
            valid = df[df["date"].isin(valid_dates)]
            test = df[df["date"].isin(test_dates)]
            LOG.warning("configured split empty -> chronological fallback: "
                        "train=%d valid=%d test=%d (dates=%d)",
                        len(train), len(valid), len(test), n)
        LOG.info("split: train=%d valid=%d test=%d", len(train), len(valid), len(test))
        return train, valid, test

    # ------------------------------------------------------------------
    # Train
    # ------------------------------------------------------------------
    def train(self, panel: pd.DataFrame, target_col: str = "rank_label") -> TrainResult:
        train_df, valid_df, _ = self.split(panel)
        res = TrainResult()
        if train_df.empty:
            LOG.error("train panel empty")
            return res
        # drop dates with too few ETFs (cannot rank meaningfully)
        train_df = self._filter_sparse_dates(train_df)
        valid_df = self._filter_sparse_dates(valid_df)
        info = self.ranker.train(train_df, target_col=target_col, val_df=valid_df)
        res.n_train = len(train_df)
        res.n_valid = len(valid_df)
        res.n_features = len(self.ranker.feature_names)
        res.best_iteration = int(getattr(self.ranker.model, "best_iteration_", 0) or 0)
        res.valid_ndcg_5 = float(info.get("best_ndcg_5", 0.0))
        imp = self.ranker.feature_importance().head(20)
        res.feature_importance_top = imp.to_dict("records")
        self.ranker.save()
        return res

    @staticmethod
    def _filter_sparse_dates(df: pd.DataFrame, min_etfs: int = 5) -> pd.DataFrame:
        if df.empty:
            return df
        counts = df.groupby("date")["etf"].nunique()
        ok_dates = counts[counts >= min_etfs].index
        return df[df["date"].isin(ok_dates)]

    # ------------------------------------------------------------------
    # Walk-forward retraining helper
    # ------------------------------------------------------------------
    def get_retrain_dates(self, all_dates: List[str]) -> List[str]:
        """Return the dates on which the model should be retrained."""
        all_dates = sorted(set(str(d) for d in all_dates))
        if not all_dates:
            return []
        retrain = [all_dates[0]]
        for d in all_dates[1:]:
            if len(retrain) == 0:
                retrain.append(d)
                continue
            last = retrain[-1]
            # count trading days between last retrain and now
            n = sum(1 for x in all_dates if last < x <= d)
            if n >= self.retrain_interval:
                retrain.append(d)
        return retrain
