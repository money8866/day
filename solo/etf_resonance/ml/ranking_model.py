"""ML Ranking Model Interface for the Resonance System.

Supports future integration of:
- XGBoost
- LightGBM
- CatBoost
- Transformer Ranking

Currently provides a placeholder interface with sklearn-style API.
"""

import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class MLConfig:
    """Machine Learning model configuration."""
    enabled: bool = False
    model_type: str = "xgboost"
    feature_cols: List[str] = None
    target_col: str = "future_return_60d"
    validation_split: float = 0.2
    early_stopping_rounds: int = 50

    def __post_init__(self):
        if self.feature_cols is None:
            self.feature_cols = [
                "trend_score", "persistence_score", "leader_score",
                "resonance_score", "leader_persistence", "risk_score", "buy_score",
            ]


class MLRankingModel:
    """ML-based ranking model interface.

    This is a placeholder that accepts real model backends.
    Actual ML models (XGBoost, LightGBM, etc.) can be plugged in
    via the set_model() method.

    Usage:
        model = MLRankingModel()
        model.fit(X_train, y_train)
        predictions = model.predict(X_test)
    """

    def __init__(self, config: Optional[MLConfig] = None):
        self.config = config or MLConfig()
        self._model = None
        self._feature_importance: Optional[Dict[str, float]] = None
        self._is_fitted = False

    def set_model(self, model: Any) -> None:
        """Set a real ML model (XGBoost, LightGBM, etc.)."""
        self._model = model
        logger.info(f"ML model set: {type(model).__name__}")

    def fit(self, features: pd.DataFrame, target: pd.Series,
            eval_set: Optional[Tuple[pd.DataFrame, pd.Series]] = None,
            **kwargs) -> "MLRankingModel":
        """Train the ranking model.

        Args:
            features: Feature DataFrame
            target: Target Series (future returns or rank)
            eval_set: Optional validation set (X_val, y_val)
            **kwargs: Additional model-specific parameters
        """
        if self._model is None:
            logger.warning("No underlying ML model set. "
                          "Using simple average as fallback.")
            self._is_fitted = True
            return self

        try:
            eval_set_param = {}
            if eval_set is not None:
                eval_set_param["eval_set"] = [eval_set]
                if self.config.early_stopping_rounds:
                    eval_set_param["early_stopping_rounds"] = \
                        self.config.early_stopping_rounds

            self._model.fit(features, target, **eval_set_param, **kwargs)
            self._is_fitted = True

            # Extract feature importance if available
            if hasattr(self._model, "feature_importances_"):
                self._feature_importance = {
                    col: imp for col, imp
                    in zip(features.columns, self._model.feature_importances_)
                }

            logger.info(f"ML model trained on {len(features)} samples")
        except Exception as e:
            logger.error(f"ML model training failed: {e}")
            raise

        return self

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        """Predict future performance scores.

        Args:
            features: Feature DataFrame with same columns as training

        Returns:
            Predicted scores (higher = better expected performance)
        """
        if not self._is_fitted:
            logger.warning("Model not fitted. Returning average scores.")
            return np.full(len(features), 50.0)

        if self._model is None:
            # Fallback: simple weighted average
            weights = {
                "trend_score": 0.25, "leader_score": 0.25,
                "resonance_score": 0.20, "risk_score": -0.10,
            }
            result = np.zeros(len(features))
            for col, w in weights.items():
                if col in features.columns:
                    result += w * features[col].values
            return np.clip(result, 0, 100)

        try:
            return self._model.predict(features)
        except Exception as e:
            logger.error(f"ML prediction failed: {e}")
            return np.full(len(features), 50.0)

    def predict_rank(self, features: pd.DataFrame) -> np.ndarray:
        """Predict and sort by score to produce ranks."""
        scores = self.predict(features)
        ranks = np.argsort(-scores) + 1  # 1-based rank
        return ranks

    @property
    def feature_importance(self) -> Dict[str, float]:
        """Get feature importance from trained model."""
        if self._feature_importance:
            return dict(sorted(
                self._feature_importance.items(),
                key=lambda x: -x[1]
            ))
        return {}

    def prepare_features(self, ranking_df: pd.DataFrame,
                         ml_config: Optional[MLConfig] = None) -> pd.DataFrame:
        """Extract ML features from the ranking DataFrame."""
        cfg = ml_config or self.config
        available = [c for c in cfg.feature_cols if c in ranking_df.columns]
        missing = [c for c in cfg.feature_cols if c not in ranking_df.columns]
        if missing:
            logger.warning(f"Missing ML features: {missing}")

        return ranking_df[available].copy()

    def prepare_target(self, ranking_df: pd.DataFrame,
                       target_col: Optional[str] = None) -> pd.Series:
        """Extract target column from DataFrame."""
        col = target_col or self.config.target_col
        if col not in ranking_df.columns:
            raise ValueError(f"Target column '{col}' not found")
        return ranking_df[col]
