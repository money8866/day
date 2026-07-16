#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
LightGBM Predictor - ETF未来收益预测模型
===========================================
用 LightGBM 替代规则引擎的 Step 6 (Expected Return) 和 Step 7 (Expected Rank)。

核心流程:
  1. Feature Engineering: 从ETF日线 + 各模块输出中构建50+特征
  2. Target Construction: 计算未来20/40/60天实际收益 (有监督学习标签)
  3. Training: Walk-Forward 滚动训练 + Optuna超参调优
  4. Inference: 预测当日ETF的未来收益和排名
  5. Feature Importance: 分析哪些因子对预测贡献最大
  6. Backtest: 回测评估策略表现

训练数据: 每个ETF在每个交易日作为一条样本
  - 特征: 当日可观测的所有量化指标
  - 标签: 未来20/40/60天实际收益率
  - 样本量: 35 ETF × 200天 ≈ 7000条

使用方式:
  from etf_winner_prediction.lightgbm_predictor import LightGBMPredictor
  pred = LightGBMPredictor(config)
  pred.train(start_date, end_date)     # 滚动训练
  results = pred.predict(trade_date)   # 预测当日
  pred.backtest(start_date, end_date)  # 回测
"""
from __future__ import annotations

import os
import sys
import warnings
import json
import pickle
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from etf_winner_prediction.data_loader import DataLoader, load_config
from etf_winner_prediction.indicators import (
    ema, slope, rsi, adx, volatility, max_drawdown, sharpe_ratio,
    sortino_ratio, ulcer_index, beta as beta_coef, rolling_corr,
    hurst_exponent, breakout_pct, volume_ratio,
    consecutive_up_days, above_ema_days, natr, calmar_ratio,
    returns as compute_returns,
)

# ============================================================
# 数据类
# ============================================================
@dataclass
class LightGBMPrediction:
    """LightGBM 单只ETF预测结果"""
    etf_code: str = ""
    expected_20d: float = 0.0
    expected_40d: float = 0.0
    expected_60d: float = 0.0
    expected_return: float = 0.0
    predicted_rank: int = 0
    probability_top1: float = 0.0
    probability_top3: float = 0.0
    probability_top5: float = 0.0
    confidence: float = 0.0
    raw_pred_20d: float = 0.0
    raw_pred_40d: float = 0.0
    raw_pred_60d: float = 0.0

@dataclass
class BacktestResult:
    """回测结果"""
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    avg_holding_days: float = 0.0
    num_trades: int = 0
    top1_returns: list = field(default_factory=list)
    top3_returns: list = field(default_factory=list)
    daily_returns: list = field(default_factory=list)


# ============================================================
# 特征工程
# ============================================================
class FeatureBuilder:
    """从ETF日线数据中构建特征矩阵"""

    def __init__(self, config: dict):
        self.ma_short = config.get("etf_trend", {}).get("ema_fast", 20)
        self.ma_mid = config.get("etf_trend", {}).get("ema_mid", 60)
        self.ma_long = config.get("etf_trend", {}).get("ema_slow", 120)

    def build(self, close: np.ndarray, high: np.ndarray, low: np.ndarray,
              vol: np.ndarray, amount: np.ndarray, pct_chg: np.ndarray) -> Dict[str, float]:
        """从价格序列构建特征向量"""
        n = len(close)
        feats = {}

        if n < 30:
            return feats

        # ---- 价格特征 ----
        feats["close"] = float(close[-1])
        feats["ret_1d"] = float(pct_chg[-1]) if len(pct_chg) > 0 else 0
        feats["ret_5d"] = float(close[-1] / close[-6] - 1) if n > 5 else 0
        feats["ret_10d"] = float(close[-1] / close[-11] - 1) if n > 10 else 0
        feats["ret_20d"] = float(close[-1] / close[-21] - 1) if n > 20 else 0
        feats["ret_40d"] = float(close[-1] / close[-41] - 1) if n > 40 else 0
        feats["ret_60d"] = float(close[-1] / close[-61] - 1) if n > 60 else 0

        # ---- EMA 距离 ----
        ema20 = ema(close, self.ma_short)
        ema60 = ema(close, self.ma_mid)
        ema120 = ema(close, self.ma_long)
        feats["dist_ema20"] = float((close[-1] - ema20[-1]) / max(ema20[-1], 1e-6))
        feats["dist_ema60"] = float((close[-1] - ema60[-1]) / max(ema60[-1], 1e-6))
        feats["dist_ema120"] = float((close[-1] - ema120[-1]) / max(ema120[-1], 1e-6))
        feats["ema20_slope"] = float(slope(ema20, 10))
        feats["ema60_slope"] = float(slope(ema60, 20))
        feats["ema_alignment"] = float(
            (ema20[-1] > ema60[-1] > ema120[-1]) * 2 +
            (close[-1] > ema20[-1]) * 1
        )

        # ---- 动量与加速度 ----
        ret_20_prev = float(close[-21] / close[-41] - 1) if n > 40 else 0
        feats["momentum_accel"] = float(feats["ret_20d"] - ret_20_prev)

        # ---- 波动率 ----
        feats["vol_20d"] = float(volatility(close, 20))
        feats["vol_60d"] = float(volatility(close, 60)) if n > 60 else feats["vol_20d"]
        feats["vol_ratio"] = float(feats["vol_20d"] / max(feats["vol_60d"], 1e-6))

        # ---- 回撤 ----
        feats["max_dd_20"] = float(max_drawdown(close[-20:])) if n > 20 else 0
        feats["max_dd_60"] = float(max_drawdown(close[-60:])) if n > 60 else feats["max_dd_20"]
        feats["ulcer_60"] = float(ulcer_index(close, 60)) if n > 60 else 0

        # ---- 风险调整 ----
        feats["sharpe_60"] = float(sharpe_ratio(close, 60)) if n > 60 else 0
        feats["sortino_60"] = float(sortino_ratio(close, 60)) if n > 60 else 0
        feats["calmar_60"] = float(calmar_ratio(close, 252)) if n > 252 else 0

        # ---- 趋势指标 ----
        feats["adx_14"] = float(np.clip(adx(high, low, close, 14), 0, 100))
        feats["rsi_14"] = float(rsi(close, 14))
        feats["hurst"] = float(hurst_exponent(close[-min(n, 240):]))
        feats["above_ema20_days"] = float(above_ema_days(close, 20))
        feats["consec_up"] = float(consecutive_up_days(pct_chg))
        feats["natr_14"] = float(natr(high, low, close, 14)[-1]) if n > 15 else 0

        # ---- 成交量和资金流 ----
        feats["vol_ratio"] = float(volume_ratio(vol, 20))
        feats["amt_5d_avg"] = float(np.mean(amount[-5:])) if n > 5 else 0
        feats["amt_20d_avg"] = float(np.mean(amount[-20:])) if n > 20 else 0
        feats["amt_trend"] = float(feats["amt_5d_avg"] / max(feats["amt_20d_avg"], 1e-6))
        if n > 20:
            feats["amt_growth"] = float(
                (np.mean(amount[-5:]) - np.mean(amount[-20:-5])) / max(np.mean(amount[-20:-5]), 1e-6)
            )
        else:
            feats["amt_growth"] = 0.0

        # ---- 突破 ----
        feats["breakout_60"] = float(breakout_pct(close, high, 60) / 100)

        # ---- 价格位置 ----
        if n > 60:
            h60 = float(np.max(high[-60:]))
            l60 = float(np.min(low[-60:]))
            feats["price_position_60"] = float((close[-1] - l60) / max(h60 - l60, 1e-6))
        else:
            feats["price_position_60"] = 0.5

        # ---- 成交量波动 ----
        if n > 20:
            feats["vol_cv"] = float(np.std(vol[-20:]) / max(np.mean(vol[-20:]), 1e-6))
        else:
            feats["vol_cv"] = 0.0

        # ---- 强者恒强特征 ----
        # 1. 新高信号：当日收盘价是否创N日新高
        if n >= 20:
            feats["new_high_20d"] = 1.0 if close[-1] >= np.max(high[-20:]) else 0.0
        else:
            feats["new_high_20d"] = 0.0
        if n >= 60:
            feats["new_high_60d"] = 1.0 if close[-1] >= np.max(high[-60:]) else 0.0
        else:
            feats["new_high_60d"] = 0.0

        # 2. 趋势持续性：近20日上涨天数占比
        if n >= 20 and len(pct_chg) >= 20:
            feats["up_ratio_20d"] = float(np.sum(pct_chg[-20:] > 0) / 20.0)
        else:
            feats["up_ratio_20d"] = 0.5

        # 3. 量价配合度：近20日成交量与涨幅的相关性（正相关=资金流入）
        if n >= 20 and len(pct_chg) >= 20:
            v20 = vol[-20:].astype(float)
            p20 = pct_chg[-20:].astype(float)
            if np.std(v20) > 1e-6 and np.std(p20) > 1e-6:
                feats["vol_price_corr_20d"] = float(np.corrcoef(v20, p20)[0, 1])
            else:
                feats["vol_price_corr_20d"] = 0.0
        else:
            feats["vol_price_corr_20d"] = 0.0

        # 4. 均线扩展强度：EMA20与EMA60的间距比例（正值=多头排列且扩展中）
        if n > 60:
            feats["ma_spread"] = float((ema20[-1] - ema60[-1]) / max(ema60[-1], 1e-6))
        else:
            feats["ma_spread"] = 0.0

        # 5. 突破强度：收盘价超出近20日最高价的幅度（正值=突破箱顶）
        if n >= 21:
            recent_high = float(np.max(high[-21:-1]))
            feats["breakout_strength"] = float((close[-1] - recent_high) / max(recent_high, 1e-6))
        else:
            feats["breakout_strength"] = 0.0

        # ---- 动量交互特征 ----
        # 6. 动量反转强度：短期收益减长期收益（正=加速恢复）
        feats["momentum_reversal"] = float(feats["ret_20d"] - feats["ret_60d"])
        # 7. 恢复动能：近20日涨幅 × 上涨天数占比（动量×一致性）
        feats["recovery_power"] = float(feats["ret_20d"] * feats["up_ratio_20d"])
        # 8. 量价共振：量价相关性 × 20日涨幅（资金流入+上涨=强势）
        feats["vol_price_momentum"] = float(feats["vol_price_corr_20d"] * max(feats["ret_20d"], 0))

        return feats

    def add_engine_features(self, feats: dict, market_score: float,
                            theme_score: float, theme_rank: int,
                            lifecycle_signal: float, remaining_days: int,
                            leader_score: float, leader_health: float,
                            etf_trend_score: float, risk_score: float,
                            rotation_prob: float) -> dict:
        """追加引擎模块特征"""
        feats["market_score"] = market_score
        feats["theme_score"] = theme_score
        feats["theme_rank"] = float(theme_rank)
        feats["lifecycle_signal"] = lifecycle_signal
        feats["remaining_days"] = float(remaining_days)
        feats["leader_score"] = leader_score
        feats["leader_health"] = leader_health
        feats["etf_trend_score"] = etf_trend_score
        feats["risk_score"] = risk_score
        feats["rotation_prob"] = rotation_prob
        # 交互特征
        feats["leader_trend_product"] = leader_score * etf_trend_score / 100.0
        feats["risk_reward_ratio"] = (etf_trend_score / max(risk_score, 1)) * 100
        feats["theme_lifecycle_product"] = theme_score * lifecycle_signal / 100.0
        feats["market_theme_align"] = market_score * theme_score / 100.0
        return feats


# ============================================================
# 目标构建
# ============================================================
class TargetBuilder:
    """构建监督学习标签：未来N天实际收益"""

    def __init__(self, horizons: List[int] = None):
        self.horizons = horizons or [20, 40, 60]

    def build(self, close: np.ndarray) -> Dict[str, float]:
        """计算未来N天收益"""
        targets = {}
        n = len(close)
        for h in self.horizons:
            if n > h:
                targets[f"fwd_{h}d"] = float(close[-1] / close[-h - 1] - 1)
            else:
                targets[f"fwd_{h}d"] = np.nan
        return targets


# ============================================================
# LightGBM 预测器
# ============================================================
class LightGBMPredictor:
    """LightGBM 多周期预测器"""

    def __init__(self, config: dict, model_dir: str = None):
        self.config = config
        self.horizons = config.get("expected_return", {}).get("horizons", [20, 40, 60])
        self.model_dir = model_dir or os.path.join(BASE_DIR, "output", "models")
        os.makedirs(self.model_dir, exist_ok=True)
        self.feature_builder = FeatureBuilder(config)
        self.target_builder = TargetBuilder(self.horizons)
        self.models: Dict[int, object] = {}
        self.feature_names: List[str] = []
        self.feature_importance: Dict[int, pd.DataFrame] = {}

    # ------------------------------------------------------------------
    # 训练数据构建
    # ------------------------------------------------------------------
    def build_training_data(self, etf_data: Dict[str, pd.DataFrame],
                            start_date: str, end_date: str,
                            benchmark_close: np.ndarray = None) -> pd.DataFrame:
        """构建训练数据集：每个ETF每天一条样本"""
        rows = []
        for code, df in etf_data.items():
            df = df.sort_values("trade_date").reset_index(drop=True)
            close = df["close"].values.astype(float)
            high = df["high"].values.astype(float) if "high" in df.columns else close
            low = df["low"].values.astype(float) if "low" in df.columns else close
            vol = df["vol"].values.astype(float) if "vol" in df.columns else np.ones_like(close)
            amount = df["amount"].values.astype(float) if "amount" in df.columns else vol * close
            pct_chg = df["pct_chg"].values.astype(float) if "pct_chg" in df.columns else np.zeros_like(close)
            dates = df["trade_date"].values

            n = len(close)
            for i in range(120, n - max(self.horizons)):
                # 前120天用于特征计算，后面留出target空间
                c = close[:i + 1]
                h = high[:i + 1]
                l = low[:i + 1]
                v = vol[:i + 1]
                a = amount[:i + 1]
                p = pct_chg[:i + 1]

                feats = self.feature_builder.build(c, h, l, v, a, p)
                if not feats:
                    continue

                # 目标
                targets = self.target_builder.build(close[:i + max(self.horizons) + 1])
                if any(np.isnan(targets.get(f"fwd_{h}d", np.nan)) for h in self.horizons):
                    continue

                row = {
                    "ts_code": code,
                    "trade_date": str(dates[i]),
                }
                row.update(feats)
                row.update(targets)
                rows.append(row)

        df = pd.DataFrame(rows)
        return df

    def prepare_features(self, df: pd.DataFrame) -> Tuple[pd.DataFrame, List[str]]:
        """准备特征矩阵（返回DataFrame以保留特征名）"""
        exclude = ["ts_code", "trade_date"] + [f"fwd_{h}d" for h in self.horizons]
        feat_cols = [c for c in df.columns if c not in exclude]
        X = df[feat_cols].fillna(0).astype(np.float32)
        return X, feat_cols

    def prepare_ranking_data(self, df: pd.DataFrame, horizon: int,
                             n_bins: int = 5) -> Tuple[pd.DataFrame, np.ndarray, np.ndarray, List[str]]:
        """准备排序学习数据
        - 按trade_date分组，每个交易日所有ETF作为一个query group
        - label = 未来N日收益的截面排名分桶(0~n_bins-1, n_bins-1=最好)
        - 返回 X, y(group内排名label), group(每个group的样本数), feat_cols
        """
        exclude = ["ts_code", "trade_date"] + [f"fwd_{h}d" for h in self.horizons]
        feat_cols = [c for c in df.columns if c not in exclude]

        # 过滤无效target
        df_valid = df.dropna(subset=[f"fwd_{horizon}d"]).copy()
        if df_valid.empty:
            return pd.DataFrame(), np.array([]), np.array([]), feat_cols

        # 按trade_date分组，计算每日内的排名label
        labels = []
        groups = []
        for date, group in df_valid.groupby("trade_date"):
            if len(group) < 5:  # 少于5个ETF的日期跳过
                continue
            rets = group[f"fwd_{horizon}d"].values
            # 排名: 0=最差, n_bins-1=最好
            # 使用百分位排名，避免相同收益
            ranks = pd.Series(rets).rank(method="first").values.astype(int)
            # 归一化到 0~n_bins-1
            n = len(ranks)
            binned = (ranks - 1) * n_bins // n
            binned = np.clip(binned, 0, n_bins - 1)
            labels.extend(binned.tolist())
            groups.append(len(group))

        # 构建特征矩阵（保持分组顺序）
        df_sorted = df_valid.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
        # 重新计算label以匹配排序后的顺序
        labels_sorted = []
        groups_sorted = []
        for date, group in df_sorted.groupby("trade_date"):
            if len(group) < 5:
                continue
            rets = group[f"fwd_{horizon}d"].values
            ranks = pd.Series(rets).rank(method="first").values.astype(int)
            n = len(ranks)
            binned = (ranks - 1) * n_bins // n
            binned = np.clip(binned, 0, n_bins - 1)
            labels_sorted.extend(binned.tolist())
            groups_sorted.append(len(group))

        X = df_sorted[feat_cols].fillna(0).astype(np.float32)
        y = np.array(labels_sorted, dtype=np.int32)
        group = np.array(groups_sorted, dtype=np.int32)
        return X, y, group, feat_cols

    # ------------------------------------------------------------------
    # 训练
    # ------------------------------------------------------------------
    def train(self, etf_data: Dict[str, pd.DataFrame],
              start_date: str, end_date: str,
              benchmark_close: np.ndarray = None,
              use_optuna: bool = False, n_trials: int = 50):
        """训练 LightGBM 模型"""
        try:
            import lightgbm as lgb
        except ImportError:
            print("[LightGBM] lightgbm not installed, run: pip install lightgbm")
            return

        print("=" * 60)
        print("  LightGBM Training")
        print("=" * 60)

        print("[1] Building training data...")
        df = self.build_training_data(etf_data, start_date, end_date, benchmark_close)
        X, self.feature_names = self.prepare_features(df)
        print(f"    Samples: {len(X)}, Features: {len(self.feature_names)}")

        for h in self.horizons:
            print(f"\n[2] Training LightGBM for {h}D horizon...")
            y = df[f"fwd_{h}d"].fillna(0).values.astype(np.float32)

            if use_optuna:
                params = self._optimize_optuna(X, y, h, n_trials)
            else:
                params = self._default_params()

            print(f"    Params: {params}")
            model = lgb.LGBMRegressor(**params, n_jobs=-1, verbose=-1, force_col_wise=True)
            model.fit(X, y)
            self.models[h] = model

            # 特征重要性
            imp = pd.DataFrame({
                "feature": self.feature_names,
                "importance": model.feature_importances_,
                "gain": model.booster_.feature_importance(importance_type="gain"),
            }).sort_values("importance", ascending=False)
            self.feature_importance[h] = imp
            print(f"    Top 5 features for {h}D:")
            for _, row in imp.head(5).iterrows():
                print(f"      {row['feature']}: {row['importance']:.0f}")

        # 保存模型
        self._save_models()

    def train_ranker(self, etf_data: Dict[str, pd.DataFrame],
                     start_date: str, end_date: str,
                     n_bins: int = 5, use_optuna: bool = False):
        """训练 Learning-to-Rank 模型（LGBMRanker）
        - 每个交易日所有ETF作为一个query group
        - label = 未来N日收益的截面排名分桶
        - 模型学习"谁比谁强"而不是"涨多少"
        """
        try:
            import lightgbm as lgb
        except ImportError:
            print("[LightGBM] lightgbm not installed")
            return

        print("=" * 60)
        print("  LightGBM Ranker Training (Learning-to-Rank)")
        print("=" * 60)

        print("[1] Building training data...")
        df = self.build_training_data(etf_data, start_date, end_date)
        print(f"    Total samples: {len(df)}")

        self.models = {}  # 清空旧模型
        self.feature_importance = {}

        for h in self.horizons:
            print(f"\n[2] Preparing ranking data for {h}D horizon...")
            X, y, group, feat_cols = self.prepare_ranking_data(df, h, n_bins)
            self.feature_names = feat_cols
            print(f"    Samples: {len(X)}, Query groups: {len(group)}, "
                  f"Features: {len(feat_cols)}")
            if len(X) == 0 or len(group) == 0:
                print(f"    [Skip] No valid ranking data for {h}D")
                continue

            params = self._default_params()
            params["objective"] = "lambdarank"
            params["metric"] = "ndcg"
            params["label_gain"] = list(range(n_bins))
            params["eval_at"] = [3, 5, 10]
            print(f"    Params: {params}")

            model = lgb.LGBMRanker(**params, n_jobs=-1, verbose=-1, force_col_wise=True)
            model.fit(X, y, group=group)
            self.models[h] = model

            # 特征重要性
            imp = pd.DataFrame({
                "feature": feat_cols,
                "importance": model.feature_importances_,
                "gain": model.booster_.feature_importance(importance_type="gain"),
            }).sort_values("importance", ascending=False)
            self.feature_importance[h] = imp
            print(f"    Top 5 features for {h}D:")
            for _, row in imp.head(5).iterrows():
                print(f"      {row['feature']}: {row['importance']:.0f}")

        # 保存模型
        self._save_models()
        print(f"\nRanker training done. Models: {list(self.models.keys())}")

    def _default_params(self) -> dict:
        return {
            "n_estimators": 100,
            "max_depth": 4,
            "num_leaves": 15,
            "learning_rate": 0.05,
            "subsample": 0.7,
            "colsample_bytree": 0.7,
            "reg_alpha": 0.5,
            "reg_lambda": 1.0,
            "min_child_samples": 30,
            "random_state": 42,
        }

    def _optimize_optuna(self, X, y, horizon, n_trials):
        try:
            import optuna
            import lightgbm as lgb
        except ImportError:
            return self._default_params()

        def objective(trial):
            params = {
                "n_estimators": trial.suggest_int("n_estimators", 100, 500),
                "max_depth": trial.suggest_int("max_depth", 3, 10),
                "num_leaves": trial.suggest_int("num_leaves", 15, 127),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
                "subsample": trial.suggest_float("subsample", 0.5, 1.0),
                "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
                "reg_alpha": trial.suggest_float("reg_alpha", 1e-3, 10.0, log=True),
                "reg_lambda": trial.suggest_float("reg_lambda", 1e-3, 10.0, log=True),
                "min_child_samples": trial.suggest_int("min_child_samples", 5, 50),
                "random_state": 42,
            }
            model = lgb.LGBMRegressor(**params, n_jobs=-1, verbose=-1, force_col_wise=True)
            model.fit(X, y)
            y_pred = model.predict(X)
            # 用Spearman秩相关（排名预测更重要）
            from scipy.stats import spearmanr
            corr, _ = spearmanr(y, y_pred)
            return corr

        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
        best = study.best_params
        print(f"    Optuna best Spearman: {study.best_value:.4f}")
        best["n_estimators"] = best.get("n_estimators", 200)
        return best

    # ------------------------------------------------------------------
    # 推理
    # ------------------------------------------------------------------
    def predict(self, etf_code: str, etf_df: pd.DataFrame,
                market_score: float = 50.0, theme_score: float = 50.0,
                theme_rank: int = 99, lifecycle_signal: float = 50.0,
                remaining_days: int = 20, leader_score: float = 50.0,
                leader_health: float = 50.0, etf_trend_score: float = 50.0,
                risk_score: float = 50.0, rotation_prob: float = 30.0,
                ranker_score: float = 0.0) -> LightGBMPrediction:
        """预测单只ETF
        ranker_score: 由predict_batch传入的ranker输出分数（已归一化到0-1）
        """
        r = LightGBMPrediction(etf_code=etf_code)

        if etf_df is None or etf_df.empty or len(etf_df) < 30:
            return r

        close = etf_df["close"].values.astype(float)
        high = etf_df["high"].values.astype(float) if "high" in etf_df.columns else close
        low = etf_df["low"].values.astype(float) if "low" in etf_df.columns else close
        vol = etf_df["vol"].values.astype(float) if "vol" in etf_df.columns else np.ones_like(close)
        amount = etf_df["amount"].values.astype(float) if "amount" in etf_df.columns else vol * close
        pct_chg = etf_df["pct_chg"].values.astype(float) if "pct_chg" in etf_df.columns else np.zeros_like(close)

        feats = self.feature_builder.build(close, high, low, vol, amount, pct_chg)

        if not self.models:
            return r

        # 构建特征向量
        X = np.array([feats.get(f, 0.0) for f in self.feature_names]).reshape(1, -1).astype(np.float32)

        # 判断模型类型：LGBMRanker vs LGBMRegressor
        is_ranker = any("LGBMRanker" in type(m).__name__ for m in self.models.values())

        if is_ranker:
            # Ranker模式：输出相对排名分数，分数越高=预期排名越靠前
            for h in self.horizons:
                model = self.models.get(h)
                if model is None:
                    continue
                try:
                    pred = float(model.predict(X)[0])
                except Exception:
                    pred = 0.0
                setattr(r, f"raw_pred_{h}d", pred)
                # ranker分数本身不是收益率，暂存用于predict_batch归一化
                setattr(r, f"expected_{h}d", pred)
        else:
            # 回归模式（兼容旧模型）
            for h in self.horizons:
                model = self.models.get(h)
                if model is None:
                    continue
                try:
                    pred = float(model.predict(X)[0])
                except Exception:
                    pred = 0.0
                setattr(r, f"raw_pred_{h}d", pred)
                setattr(r, f"expected_{h}d", float(np.clip(pred, -0.15, 0.40)))

        # ---- 引擎分数融合 ----
        engine_composite = (
            leader_score * 0.35 +
            theme_score * 0.20 +
            lifecycle_signal * 0.15 +
            etf_trend_score * 0.15 +
            (100 - risk_score) * 0.15
        )
        engine_boost = (engine_composite - 50) / 50 * 0.12
        rank_bonus = max(0, (4 - theme_rank)) * 0.02

        if is_ranker:
            # Ranker模式：ranker_score已由predict_batch归一化到0-1
            # 映射到收益区间：0=-5%，1=+20%
            ranker_contribution = ranker_score * 0.25 - 0.05
            r.expected_return = float(np.clip(
                ranker_contribution + engine_boost + rank_bonus,
                0.0, 0.50
            ))
        else:
            # 回归模式：正值贡献
            model_pred = r.expected_20d * 0.3 + r.expected_40d * 0.4 + r.expected_60d * 0.3
            model_contribution = max(model_pred, 0.0) * 0.30
            r.expected_return = float(np.clip(
                model_contribution + engine_boost + rank_bonus,
                0.0, 0.50
            ))
        return r

    def predict_batch(self, etf_data: Dict[str, pd.DataFrame],
                      engine_scores: Dict[str, dict]) -> Dict[str, LightGBMPrediction]:
        """批量预测（含20日动量截面排名信号 + Ranker归一化）"""
        is_ranker = (self.models and
                     any("LGBMRanker" in type(m).__name__ for m in self.models.values()))

        # ---- Ranker模式：先批量计算raw score，再做min-max归一化 ----
        ranker_scores_norm = {}
        if is_ranker:
            # 取60D ranker的raw score做截面归一化（60D最具趋势性）
            model = self.models.get(60) or self.models.get(40) or self.models.get(20)
            if model is not None:
                raw_scores = {}
                for code, df in etf_data.items():
                    if len(df) < 30:
                        continue
                    close = df["close"].values.astype(float)
                    high = df["high"].values.astype(float) if "high" in df.columns else close
                    low = df["low"].values.astype(float) if "low" in df.columns else close
                    vol = df["vol"].values.astype(float) if "vol" in df.columns else np.ones_like(close)
                    amount = df["amount"].values.astype(float) if "amount" in df.columns else vol * close
                    pct_chg = df["pct_chg"].values.astype(float) if "pct_chg" in df.columns else np.zeros_like(close)
                    feats = self.feature_builder.build(close, high, low, vol, amount, pct_chg)
                    X = np.array([feats.get(f, 0.0) for f in self.feature_names]).reshape(1, -1).astype(np.float32)
                    try:
                        raw_scores[code] = float(model.predict(X)[0])
                    except Exception:
                        raw_scores[code] = 0.0
                # min-max归一化到0-1
                if raw_scores:
                    s_min = min(raw_scores.values())
                    s_max = max(raw_scores.values())
                    s_range = max(s_max - s_min, 1e-6)
                    ranker_scores_norm = {c: (s - s_min) / s_range for c, s in raw_scores.items()}
                    print(f"  [Ranker] Raw score range: [{s_min:.2f}, {s_max:.2f}]")
                    # 显示Top5
                    top5 = sorted(ranker_scores_norm.items(), key=lambda x: x[1], reverse=True)[:5]
                    print(f"  [Ranker] Top5 normalized:")
                    for c, s in top5:
                        print(f"    {c}: {s:.3f}")

        # ---- 单只预测 ----
        results = {}
        for code, df in etf_data.items():
            es = engine_scores.get(code, {})
            results[code] = self.predict(
                etf_code=code, etf_df=df,
                market_score=es.get("market_score", 50.0),
                theme_score=es.get("theme_score", 50.0),
                theme_rank=es.get("theme_rank", 99),
                lifecycle_signal=es.get("lifecycle_signal", 50.0),
                remaining_days=es.get("remaining_days", 20),
                leader_score=es.get("leader_score", 50.0),
                leader_health=es.get("leader_health", 50.0),
                etf_trend_score=es.get("etf_trend_score", 50.0),
                risk_score=es.get("risk_score", 50.0),
                rotation_prob=es.get("rotation_prob", 30.0),
                ranker_score=ranker_scores_norm.get(code, 0.0),
            )

        # ---- 20日动量截面排名信号（强者恒强）----
        momentum_list = []
        for code, df in etf_data.items():
            if len(df) < 21:
                continue
            close = df["close"].values.astype(float)
            mom_20d = close[-1] / close[-21] - 1
            momentum_list.append((code, mom_20d))

        momentum_list.sort(key=lambda x: x[1], reverse=True)
        momentum_rank_map = {code: rank + 1 for rank, (code, _) in enumerate(momentum_list)}

        for code, mom_20d in momentum_list:
            if code not in results:
                continue
            rank = momentum_rank_map[code]
            r = results[code]

            if rank == 1:
                rank_boost = 0.12
            elif rank == 2:
                rank_boost = 0.08
            elif rank == 3:
                rank_boost = 0.05
            elif rank <= 5:
                rank_boost = 0.02
            else:
                rank_boost = 0.0

            mom_strength = 0.0
            if mom_20d > 0.20:
                mom_strength = 0.05
            elif mom_20d > 0.10:
                mom_strength = 0.02

            if mom_20d < 0:
                rank_boost = max(rank_boost - min(abs(mom_20d) * 0.3, 0.08), -0.08)

            r.expected_return = float(np.clip(r.expected_return + rank_boost + mom_strength, 0.0, 0.60))

        # 排名
        sorted_results = sorted(results.values(), key=lambda x: x.expected_return, reverse=True)
        for i, r in enumerate(sorted_results):
            r.predicted_rank = i + 1
        # 概率
        max_ret = max(r.expected_return for r in sorted_results) if sorted_results else 1.0
        for r in sorted_results:
            gap = (max_ret - r.expected_return) / max(max_ret, 0.01)
            r.probability_top1 = float(np.clip(1.0 - gap * 0.8, 0.02, 0.80))
            r.probability_top3 = float(np.clip(1.0 - gap * 0.5, 0.05, 0.95))
            r.probability_top5 = float(np.clip(1.0 - gap * 0.3, 0.10, 0.98))
            r.confidence = float(np.clip(100.0 - gap * 40, 10, 90))
        return results

    # ------------------------------------------------------------------
    # 回测
    # ------------------------------------------------------------------
    def backtest(self, etf_data: Dict[str, pd.DataFrame],
                 start_date: str, end_date: str,
                 engine_scores: Dict[str, Dict[str, dict]] = None,
                 top_k: int = 1, hold_days: int = 40) -> BacktestResult:
        """Walk-Forward 回测"""
        bt = BacktestResult()

        if not etf_data:
            return bt

        print("=" * 60)
        print("  LightGBM Backtest (Walk-Forward)")
        print("=" * 60)

        # 获取交易日序列
        sample_df = next(iter(etf_data.values()))
        all_dates = sorted(sample_df["trade_date"].unique())
        all_dates = [d for d in all_dates if start_date <= str(d) <= end_date]
        if len(all_dates) < 60:
            print("Not enough dates for backtest")
            return bt

        step = 20  # 每20天滚动一次
        test_dates = all_dates[::step]
        daily_returns = []

        for i, test_date in enumerate(test_dates):
            if i < 3:  # 前3个观测点跳过（特征不足）
                continue
            train_end = test_dates[i - 1]

            # 训练
            self._walk_forward_train(etf_data, train_end)

            # 预测
            es = engine_scores.get(str(test_date), {}) if engine_scores else {}
            preds = self.predict_batch(etf_data, es)
            top = sorted(preds.values(), key=lambda x: x.expected_return, reverse=True)[:top_k]

            if top and top[0].expected_return > 0.05:
                code = top[0].etf_code
                df = etf_data.get(code)
                if df is not None and not df.empty:
                    close = df["close"].values.astype(float)
                    idx = np.where(df["trade_date"].values == str(test_date))[0]
                    if len(idx) > 0 and idx[0] + hold_days < len(close):
                        ret = close[idx[0] + hold_days] / close[idx[0]] - 1
                        bt.top1_returns.append(ret)
                        daily_returns.append(ret / hold_days)

        if bt.top1_returns:
            rets = np.array(bt.top1_returns)
            bt.total_return = float(np.prod(1 + rets) - 1)
            bt.annual_return = float((1 + bt.total_return) ** (252 / len(rets) / hold_days) - 1)
            bt.sharpe = float(np.mean(rets) / max(np.std(rets), 1e-6) * np.sqrt(252 / hold_days))
            bt.max_drawdown = float(max_drawdown(rets))
            bt.win_rate = float(np.mean(np.array(rets) > 0))
            bt.num_trades = len(rets)

        print(f"  Trades: {bt.num_trades}, WinRate: {bt.win_rate:.1%}")
        print(f"  TotalReturn: {bt.total_return*100:.1f}%, Annual: {bt.annual_return*100:.1f}%")
        print(f"  Sharpe: {bt.sharpe:.2f}, MaxDD: {bt.max_drawdown*100:.1f}%")
        return bt

    def _walk_forward_train(self, etf_data, train_end: str):
        """滚动训练"""
        try:
            import lightgbm as lgb
        except ImportError:
            return

        df = self.build_training_data(etf_data, "20250101", str(train_end))
        if len(df) < 100:
            return
        X, self.feature_names = self.prepare_features(df)
        for h in self.horizons:
            y = df[f"fwd_{h}d"].fillna(0).values.astype(np.float32)
            model = lgb.LGBMRegressor(**self._default_params(), n_jobs=-1, verbose=-1, force_col_wise=True)
            model.fit(X, y)
            self.models[h] = model

    # ------------------------------------------------------------------
    # 模型持久化
    # ------------------------------------------------------------------
    def _save_models(self):
        for h, model in self.models.items():
            fp = os.path.join(self.model_dir, f"lgbm_{h}d.pkl")
            with open(fp, "wb") as f:
                pickle.dump(model, f)
        # 保存特征名
        fp = os.path.join(self.model_dir, "feature_names.json")
        with open(fp, "w") as f:
            json.dump(self.feature_names, f)
        print(f"Models saved to {self.model_dir}")

    def load_models(self):
        for h in self.horizons:
            fp = os.path.join(self.model_dir, f"lgbm_{h}d.pkl")
            if os.path.exists(fp):
                with open(fp, "rb") as f:
                    self.models[h] = pickle.load(f)
        fp = os.path.join(self.model_dir, "feature_names.json")
        if os.path.exists(fp):
            with open(fp, "r") as f:
                self.feature_names = json.load(f)
        return len(self.models) > 0


# ============================================================
# 命令行入口
# ============================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="LightGBM ETF Predictor")
    parser.add_argument("--date", default=None)
    parser.add_argument("--train", action="store_true")
    parser.add_argument("--ranker", action="store_true", help="Train Learning-to-Rank model")
    parser.add_argument("--optuna", action="store_true")
    parser.add_argument("--backtest", action="store_true")
    parser.add_argument("--top_k", type=int, default=1)
    args = parser.parse_args()

    config = load_config(os.path.join(BASE_DIR, "config.yaml"))
    dl = DataLoader(config)

    if args.date is None:
        args.date = dl.get_last_trade_date()

    dt = datetime.strptime(args.date, "%Y%m%d")
    start_date = (dt - timedelta(days=1200)).strftime("%Y%m%d")

    etf_list = list(config.get("etf_universe", {}).keys())
    print(f"Loading {len(etf_list)} ETFs: {start_date} ~ {args.date}")
    etf_data = dl.load_etf_data(etf_list, start_date, args.date)

    predictor = LightGBMPredictor(config)

    # 尝试加载已有模型
    if not args.train and not args.ranker:
        loaded = predictor.load_models()
        if loaded:
            print(f"Loaded models for {list(predictor.models.keys())} horizons")
        else:
            print("No pre-trained models found, predictions will be empty.")

    if args.train:
        predictor.train(etf_data, start_date, args.date, use_optuna=args.optuna)

    if args.ranker:
        predictor.train_ranker(etf_data, start_date, args.date)

    if args.backtest:
        predictor.backtest(etf_data, start_date, args.date, top_k=args.top_k)

    if not args.train and not args.backtest:
        # 仅推理
        preds = predictor.predict_batch(etf_data, {})
        for code, r in sorted(preds.items(), key=lambda x: x[1].expected_return, reverse=True)[:10]:
            print(f"  #{r.predicted_rank} {code}: "
                  f"20D={r.expected_20d*100:.1f}% "
                  f"40D={r.expected_40d*100:.1f}% "
                  f"60D={r.expected_60d*100:.1f}% "
                  f"Return={r.expected_return*100:.1f}% "
                  f"Top3={r.probability_top3:.0%} "
                  f"Conf={r.confidence:.0f}%")


if __name__ == "__main__":
    main()