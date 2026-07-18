#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Model Explainer
===============
SHAP-based explanation with a LightGBM gain-importance fallback when
the `shap` package is not installed.

Outputs:
  - Global feature importance (gain)
  - Per-prediction SHAP values (when available)
  - A human-readable top-contributors summary for the top-ranked ETF
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

LOG = logging.getLogger("etf_alpha_ranking.explainer")

try:
    import shap  # type: ignore
    _HAS_SHAP = True
except Exception:
    _HAS_SHAP = False


class Explainer:
    def __init__(self, ranker):
        self.ranker = ranker
        self._explainer = None
        if _HAS_SHAP and ranker.model is not None:
            try:
                self._explainer = shap.TreeExplainer(ranker.model)
            except Exception as e:
                LOG.info("shap TreeExplainer init failed: %s", e)
                self._explainer = None

    # ------------------------------------------------------------------
    # Global importance
    # ------------------------------------------------------------------
    def global_importance(self, top_n: int = 20) -> pd.DataFrame:
        df = self.ranker.feature_importance("gain")
        if df.empty:
            return df
        df = df.head(top_n).copy()
        df["rank"] = np.arange(1, len(df) + 1)
        return df

    # ------------------------------------------------------------------
    # Per-row SHAP
    # ------------------------------------------------------------------
    def explain(self, X: pd.DataFrame) -> Optional[np.ndarray]:
        """Return SHAP values for the given feature rows (or None)."""
        if self._explainer is None:
            return None
        feats = [c for c in self.ranker.feature_names if c in X.columns]
        for c in self.ranker.feature_names:
            if c not in X.columns:
                X[c] = 0.0
        Xf = X[self.ranker.feature_names].astype(float).fillna(0.0)
        try:
            return self._explainer.shap_values(Xf)
        except Exception as e:
            LOG.warning("shap values failed: %s", e)
            return None

    def top_contributors(self, X: pd.DataFrame, top_n: int = 5) -> List[Dict]:
        """Top positive contributors for a single row."""
        shap_vals = self.explain(X)
        if shap_vals is None:
            return []
        # for ranker, shap returns a 2D array (n_samples, n_features)
        arr = np.asarray(shap_vals)
        if arr.ndim == 3:
            arr = arr[:, :, 0]  # take first class for multi-output
        if arr.ndim != 2 or arr.shape[0] == 0:
            return []
        row = arr[0]
        feats = self.ranker.feature_names
        order = np.argsort(-row)[:top_n]
        return [{"feature": feats[i], "contribution": float(row[i])} for i in order]
