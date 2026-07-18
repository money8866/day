#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
LightGBM Ranker
===============
LGBMRanker with lambdarank objective and ndcg metric.

  - train(): fit the model on a labelled panel
  - predict(): score each ETF and produce a daily ranking
  - save()/load(): persist the model + feature names
  - feature_importance(): gain-based importance

LambdaRank requires grouping by query (= date). Each date is one query
with all ETFs as candidates. The model learns the ranking that maximizes
NDCG.
"""
from __future__ import annotations

import json
import logging
import os
import pickle
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

LOG = logging.getLogger("etf_alpha_ranking.ranker")

try:
    import lightgbm as lgb
    _HAS_LGB = True
except Exception:  # pragma: no cover
    _HAS_LGB = False


class LGBRankerModel:
    """Wrapper around lightgbm.LGBMRanker."""

    def __init__(self, config: dict):
        self.config = config
        mcfg = config.get("model", {})
        self.params = {
            "objective": mcfg.get("objective", "lambdarank"),
            "metric": mcfg.get("metric", "ndcg"),
            "n_estimators": mcfg.get("n_estimators", 500),
            "learning_rate": mcfg.get("learning_rate", 0.03),
            "num_leaves": mcfg.get("num_leaves", 31),
            "max_depth": mcfg.get("max_depth", 6),
            "subsample": mcfg.get("subsample", 0.8),
            "colsample_bytree": mcfg.get("colsample_bytree", 0.8),
            "min_child_samples": mcfg.get("min_child_samples", 20),
            "reg_lambda": mcfg.get("reg_lambda", 1.0),
            "ndcg_eval_at": mcfg.get("ndcg_eval_at", [1, 3, 5, 10]),
            "random_state": 42,
            "verbosity": -1,
        }
        self.early_stopping = mcfg.get("early_stopping_rounds", 50)
        self.model_path = mcfg.get("model_path", "./data/models/lgb_ranker.pkl")
        self.feature_names_path = mcfg.get("feature_names_path",
                                           "./data/models/feature_names.json")
        self.model: Optional["lgb.LGBMRanker"] = None
        self.feature_names: List[str] = []
        os.makedirs(os.path.dirname(os.path.abspath(self.model_path)), exist_ok=True)

    # ------------------------------------------------------------------
    # Feature matrix helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _is_feature_col(col: str) -> bool:
        """A feature column is numeric and not a label/id column."""
        if col in {"date", "etf", "rank_label", "trade_date", "ts_code"}:
            return False
        if col.startswith("fwd_"):
            return False
        return True

    def _select_features(self, df: pd.DataFrame) -> List[str]:
        """Pick numeric feature columns and freeze their order."""
        if self.feature_names:
            present = [c for c in self.feature_names if c in df.columns]
            if present:
                return present
        feats = [c for c in df.columns if self._is_feature_col(c)
                 and pd.api.types.is_numeric_dtype(df[c])]
        return feats

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------
    def train(self, df: pd.DataFrame, target_col: str = "rank_label",
              val_df: Optional[pd.DataFrame] = None) -> dict:
        """Train the LambdaRank model.

        Args:
            df: training panel with [date, etf, features..., rank_label]
            val_df: optional validation panel (same schema)
        Returns dict of training info.
        """
        if not _HAS_LGB:
            raise RuntimeError("lightgbm is not installed")
        df = df.dropna(subset=[target_col]).copy()
        if df.empty:
            raise ValueError("training data empty after dropping NaN labels")

        self.feature_names = self._select_features(df)
        X = df[self.feature_names].astype(float)
        y = df[target_col].astype(int)
        groups = df.groupby("date").size().to_numpy()
        # sort by date to align groups
        sort_idx = np.argsort(df["date"].values, kind="stable")
        X = X.iloc[sort_idx].reset_index(drop=True)
        y = y.iloc[sort_idx].reset_index(drop=True)
        groups = df.iloc[sort_idx].groupby("date").size().to_numpy()

        self.model = lgb.LGBMRanker(**self.params)

        valid_sets = []
        valid_group = None
        valid_names = []
        eval_result = {}
        if val_df is not None and not val_df.empty:
            v = val_df.dropna(subset=[target_col]).copy()
            if not v.empty:
                Xv = v[self.feature_names].astype(float)
                yv = v[target_col].astype(int)
                v_idx = np.argsort(v["date"].values, kind="stable")
                Xv = Xv.iloc[v_idx].reset_index(drop=True)
                yv = yv.iloc[v_idx].reset_index(drop=True)
                v_groups = v.iloc[v_idx].groupby("date").size().to_numpy()
                valid_sets = [(Xv, yv)]
                valid_group = [v_groups]
                valid_names = ["valid"]

        fit_kwargs = dict(
            X=X, y=y, group=groups,
            callbacks=[lgb.early_stopping(self.early_stopping, verbose=False),
                       lgb.log_evaluation(period=0)],
        )
        if valid_sets:
            fit_kwargs["eval_set"] = valid_sets
            fit_kwargs["eval_group"] = valid_group
            fit_kwargs["eval_names"] = valid_names
            fit_kwargs["eval_at"] = self.params["ndcg_eval_at"]
        else:
            # no validation -> disable early stopping (no metric to monitor)
            fit_kwargs["callbacks"] = [lgb.log_evaluation(period=0)]
        self.model.fit(**fit_kwargs)
        # capture best ndcg
        try:
            if self.model.best_iteration_:
                ev = self.model.evals_result_
                if "valid" in ev and "ndcg@5" in ev["valid"]:
                    eval_result["best_ndcg_5"] = float(ev["valid"]["ndcg@5"][self.model.best_iteration_ - 1])
        except Exception:
            pass
        LOG.info("LGBMRanker trained: %d samples, %d features, best_iter=%s",
                 len(X), len(self.feature_names), getattr(self.model, "best_iteration_", None))
        self._save_feature_names()
        return eval_result

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------
    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        """Score each row and add a `prediction_score` column.

        The raw model output is a relevance score; we rescale it to 0-100
        within each date group so it matches the report spec (Score>85 etc).
        """
        if self.model is None:
            raise RuntimeError("model not trained/loaded")
        # fill missing feature columns with 0
        for c in self.feature_names:
            if c not in df.columns:
                df[c] = 0.0
        X = df[self.feature_names].astype(float).fillna(0.0)
        raw = self.model.predict(X)
        out = df.copy()
        out["raw_score"] = raw

        # rescale raw score to 0-100 within each date group (vectorized)
        if "date" in out.columns:
            g = out.groupby("date")["raw_score"]
            lo = g.transform("min")
            hi = g.transform("max")
            span = (hi - lo).clip(lower=1e-9)
            out["prediction_score"] = (out["raw_score"] - lo) / span * 100.0
            out.loc[span < 1e-9, "prediction_score"] = 50.0
            out["rank"] = out.groupby("date")["prediction_score"].rank(
                ascending=False, method="first").astype(int)
        else:
            # single date / flat
            lo, hi = float(np.min(raw)), float(np.max(raw))
            span = max(hi - lo, 1e-9)
            out["prediction_score"] = (out["raw_score"] - lo) / span * 100.0
            out["rank"] = out["prediction_score"].rank(
                ascending=False, method="first").astype(int)
        return out

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def save(self, path: str = None):
        path = path or self.model_path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"model": self.model, "feature_names": self.feature_names,
                         "params": self.params}, f)
        self._save_feature_names()
        LOG.info("model saved -> %s", path)

    def load(self, path: str = None) -> bool:
        path = path or self.model_path
        if not os.path.exists(path):
            return False
        try:
            with open(path, "rb") as f:
                blob = pickle.load(f)
            self.model = blob["model"]
            self.feature_names = blob["feature_names"]
            self.params = blob.get("params", self.params)
            LOG.info("model loaded <- %s (%d features)", path, len(self.feature_names))
            return True
        except Exception as e:
            LOG.warning("model load failed: %s", e)
            return False

    def check_quality(self, df: pd.DataFrame = None) -> dict:
        """Check if the model is producing meaningful predictions.

        Returns a dict with:
          - ok: bool   (True = model is trustworthy)
          - n_trees: int
          - best_iter: int
          - raw_nunique: int  (number of distinct raw scores on a sample)
          - reason: str
        """
        if self.model is None:
            return {"ok": False, "n_trees": 0, "best_iter": 0,
                    "raw_nunique": 0, "reason": "no model loaded"}
        n_trees = int(getattr(self.model, "best_iteration_", 0) or 0)
        if n_trees == 0:
            n_trees = int(getattr(self.model, "n_estimators_",
                                   getattr(self.model, "n_estimators", 500)))
        if n_trees <= 2:
            return {"ok": False, "n_trees": n_trees, "best_iter": n_trees,
                    "raw_nunique": 0,
                    "reason": f"only {n_trees} tree(s) — model underfit"}
        # Optionally test on a small sample
        if df is not None and not df.empty:
            for c in self.feature_names:
                if c not in df.columns:
                    df[c] = 0.0
            X = df[self.feature_names].astype(float).fillna(0.0)
            raw = self.model.predict(X)
            nunique = len(np.unique(np.round(raw, 6)))
            if nunique < 5:
                return {"ok": False, "n_trees": n_trees, "best_iter": n_trees,
                        "raw_nunique": nunique,
                        "reason": f"only {nunique} distinct scores — no discrimination"}
        return {"ok": True, "n_trees": n_trees, "best_iter": n_trees,
                "raw_nunique": 0, "reason": "ok"}

    def _save_feature_names(self):
        try:
            with open(self.feature_names_path, "w", encoding="utf-8") as f:
                json.dump(self.feature_names, f, ensure_ascii=False, indent=2)
        except Exception as e:
            LOG.warning("save feature names failed: %s", e)

    # ------------------------------------------------------------------
    # Feature importance
    # ------------------------------------------------------------------
    def feature_importance(self, importance_type: str = "gain") -> pd.DataFrame:
        if self.model is None:
            return pd.DataFrame(columns=["feature", "importance"])
        imp = self.model.feature_importances_
        df = pd.DataFrame({"feature": self.feature_names, "importance": imp})
        return df.sort_values("importance", ascending=False).reset_index(drop=True)
