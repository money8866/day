# -*- coding: utf-8 -*-
"""概率预测模型 — Logistic Regression + 特征工程

核心逻辑：
  - 使用 Logistic Regression 对回调标的进行成功率预测
  - 特征涵盖趋势健康度、资金流、技术指标、市场状态、主题质量
  - 输出 P(success) ∈ [0, 1]，取代/辅助硬阈值过滤
  - 支持增量训练（在线学习）和历史数据回测

设计思路：
  传统量化系统使用固定阈值（如回撤<15%、量比>1.3等）做二分类，
  本模块用概率模型替代硬阈值，实现更精细的排序和筛选。
"""

import os
import sys
import json
import pickle
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Any
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import stock_cache as sc
except ImportError:
    sc = None

# sklearn 可选导入（如果环境无 sklearn 则回退到启发式概率）
try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import roc_auc_score, accuracy_score, brier_score_loss
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


# ──────────────────────────────────────────────
# 数据结构
# ──────────────────────────────────────────────

@dataclass
class ProbabilityFeatures:
    """概率模型输入特征向量"""
    # 趋势特征
    trend_quality: float = 0.0         # 趋势质量分 0~100
    ma_alignment: int = 0              # 均线排列（多头=1, 空头=-1, 震荡=0）
    distance_ma20: float = 0.0         # 距MA20百分比
    distance_ma60: float = 0.0         # 距MA60百分比
    ret_20d: float = 0.0              # 20日收益%
    ret_60d: float = 0.0              # 60日收益%
    drawdown_from_high: float = 0.0    # 从高点回撤%

    # 资金特征
    capital_flow_score: float = 50.0   # 资金流评分 0~100
    northbound_change: float = 0.0     # 北向资金5日变化
    volume_ratio: float = 1.0          # 量比

    # 技术特征
    rsi_14: float = 50.0              # RSI
    macd_hist: float = 0.0            # MACD柱值
    volatility_20d: float = 0.0       # 20日波动率

    # 市场/主题特征
    market_state_score: float = 50.0   # 市场状态评分
    theme_quality: float = 50.0        # 主题质量分
    is_leader: int = 0                 # 是否龙头 0/1
    leader_score: float = 0.0          # 龙头评分

    # 微观结构
    turnover_cv: float = 1.0           # 换手率稳定性
    volume_shrink: float = 1.0         # 缩量比例
    consecutive_limit_ups: int = 0     # 历史最大连板数


@dataclass
class ProbabilityResult:
    """概率预测结果"""
    ts_code: str
    name: str = ''
    theme: str = ''
    probability: float = 0.5           # P(success) ∈ [0, 1]
    confidence: str = 'low'            # high / medium / low
    features: ProbabilityFeatures = field(default_factory=ProbabilityFeatures)
    signal: str = 'neutral'            # buy / watch / neutral / avoid


# ──────────────────────────────────────────────
# 特征工程
# ──────────────────────────────────────────────

class FeatureExtractor:
    """从原始数据提取概率模型特征"""

    @staticmethod
    def extract(pb_result: Any,           # PullbackDetector 结果
                df: pd.DataFrame,          # stk_factor_pro 数据
                market_score: float = 50.0,
                theme_quality: float = 50.0,
                leader_score: float = 0.0,
                capital_flow_score: float = 50.0) -> ProbabilityFeatures:
        """提取特征向量"""
        features = ProbabilityFeatures()

        if df is None or df.empty:
            return features

        try:
            close_hfq = df['close_hfq'].values if 'close_hfq' in df.columns else df['close'].values
            ma20 = df['ma_bfq_20'].values if 'ma_bfq_20' in df.columns else None
            ma60 = df['ma_bfq_60'].values if 'ma_bfq_60' in df.columns else None
            latest_close = close_hfq[-1]

            # 趋势特征
            if ma20 is not None and len(ma20) > 0:
                features.distance_ma20 = (latest_close / ma20[-1] - 1) * 100 if ma20[-1] > 0 else 0
            if ma60 is not None and len(ma60) > 0:
                features.distance_ma60 = (latest_close / ma60[-1] - 1) * 100 if ma60[-1] > 0 else 0

            # 均线排列
            ma5 = df['ma_bfq_5'].values if 'ma_bfq_5' in df.columns else None
            if ma5 is not None and ma20 is not None and ma60 is not None:
                if ma5[-1] > ma20[-1] > ma60[-1]:
                    features.ma_alignment = 1
                elif ma5[-1] < ma20[-1] < ma60[-1]:
                    features.ma_alignment = -1
                else:
                    features.ma_alignment = 0

            # 收益率
            if len(close_hfq) >= 21:
                features.ret_20d = (close_hfq[-1] / close_hfq[-21] - 1) * 100
            if len(close_hfq) >= 61:
                features.ret_60d = (close_hfq[-1] / close_hfq[-61] - 1) * 100

            # 回撤（支持 dict 或 object）
            if pb_result is not None:
                if isinstance(pb_result, dict):
                    features.drawdown_from_high = pb_result.get('drawdown', pb_result.get('drawdown_from_high', 0.0))
                else:
                    features.drawdown_from_high = getattr(pb_result, 'drawdown_from_high', 0.0)

            # 技术特征
            rsi = df['rsi_bfq_6'].values if 'rsi_bfq_6' in df.columns else None
            if rsi is not None and len(rsi) > 0:
                features.rsi_14 = float(rsi[-1]) if pd.notna(rsi[-1]) else 50.0

            if 'macd_dif_bfq' in df.columns and 'macd_dea_bfq' in df.columns:
                dif = float(df['macd_dif_bfq'].iloc[-1]) if pd.notna(df['macd_dif_bfq'].iloc[-1]) else 0
                dea = float(df['macd_dea_bfq'].iloc[-1]) if pd.notna(df['macd_dea_bfq'].iloc[-1]) else 0
                features.macd_hist = dif - dea

            # 波动率
            if len(close_hfq) > 20:
                rets = np.diff(close_hfq[-21:]) / close_hfq[-21:-1]
                features.volatility_20d = float(np.std(rets)) if len(rets) > 0 else 0

            # 量比
            if 'vol' in df.columns and len(df) > 21:
                current_vol = float(df['vol'].iloc[-1])
                avg_vol = df['vol'].iloc[-21:-1].mean()
                features.volume_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

            # 缩量
            features.volume_shrink = features.volume_ratio

            # 换手率稳定性
            if 'turnover' in df.columns:
                recent_t = df['turnover'].tail(20)
                if len(recent_t) > 0 and recent_t.mean() > 0:
                    features.turnover_cv = recent_t.std() / recent_t.mean()

            # 外部特征
            features.market_state_score = market_score
            features.theme_quality = theme_quality
            features.leader_score = leader_score
            features.is_leader = 1 if leader_score >= 80 else 0
            features.capital_flow_score = capital_flow_score

        except Exception:
            pass

        return features


# ──────────────────────────────────────────────
# 概率模型
# ──────────────────────────────────────────────

class ProbabilityModel:
    """概率预测模型（Logistic Regression + 启发式回退）

    使用流程：
      1. 首次调用 train() 或 load() 加载预训练模型
      2. 调用 predict() 获取单只股票的成功概率
      3. 调用 predict_batch() 批量预测
    """

    def __init__(self, config: dict):
        cfg = config.get('probability_model', {})
        self.model_path = cfg.get('model_path', '')
        self.use_heuristic = cfg.get('use_heuristic', not HAS_SKLEARN)
        self.default_prob = cfg.get('default_probability', 0.5)
        self.feature_names = [
            'trend_quality', 'ma_alignment', 'distance_ma20', 'distance_ma60',
            'ret_20d', 'ret_60d', 'drawdown_from_high', 'capital_flow_score',
            'northbound_change', 'volume_ratio', 'rsi_14', 'macd_hist',
            'volatility_20d', 'market_state_score', 'theme_quality',
            'is_leader', 'leader_score', 'turnover_cv', 'volume_shrink',
            'consecutive_limit_ups',
        ]

        # 模型对象
        self.model = None
        self.scaler = None
        self.feature_extractor = FeatureExtractor()

        # 尝试加载预训练模型
        if self.model_path and os.path.exists(self.model_path):
            self.load(self.model_path)

    def train(self, X: np.ndarray, y: np.ndarray,
              feature_names: List[str] = None) -> Dict:
        """训练 Logistic Regression 模型

        Args:
            X: 特征矩阵 (n_samples, n_features)
            y: 标签 (0=失败, 1=成功)
            feature_names: 特征名列表

        Returns:
            训练指标
        """
        if not HAS_SKLEARN:
            self.use_heuristic = True
            return {'status': 'fallback_heuristic', 'reason': 'sklearn not available'}

        self.model = LogisticRegression(
            C=1.0, solver='lbfgs', l1_ratio=0,
            max_iter=1000, random_state=42, class_weight='balanced'
        )
        self.scaler = StandardScaler()

        # 标准化
        X_scaled = self.scaler.fit_transform(X)
        self.model.fit(X_scaled, y)

        # 评估
        y_pred = self.model.predict(X_scaled)
        y_prob = self.model.predict_proba(X_scaled)[:, 1]
        metrics = {
            'accuracy': float(accuracy_score(y, y_pred)),
            'auc': float(roc_auc_score(y, y_prob)),
            'brier_score': float(brier_score_loss(y, y_prob)),
            'n_samples': len(y),
            'n_features': X.shape[1],
        }

        # 特征重要性
        if feature_names and hasattr(self.model, 'coef_'):
            metrics['feature_importance'] = dict(zip(
                feature_names, map(float, self.model.coef_[0])
            ))

        self.use_heuristic = False
        return metrics

    def predict(self, features: ProbabilityFeatures) -> ProbabilityResult:
        """预测单只股票的回调成功率"""
        result = ProbabilityResult(
            ts_code='', probability=self.default_prob,
            features=features,
        )

        if self.use_heuristic or self.model is None:
            prob = self._heuristic_predict(features)
        else:
            prob = self._model_predict(features)

        result.probability = round(float(prob), 4)
        result.signal = self._prob_to_signal(prob)
        result.confidence = 'high' if not self.use_heuristic else 'medium'
        if self.use_heuristic and self.model is None:
            result.confidence = 'low'

        return result

    def predict_batch(self, candidates: List[Dict],
                      df_cache: Dict[str, pd.DataFrame] = None,
                      market_score: float = 50.0,
                      theme_qualities: Dict[str, float] = None,
                      leader_scores: Dict[str, float] = None,
                      capital_flow_scores: Dict[str, float] = None) -> List[ProbabilityResult]:
        """批量预测多个标的

        Args:
            candidates: 候选标的列表 [{ts_code, name, theme, pb_result}]
            df_cache: stk_factor_pro 数据缓存 {ts_code: df}
            market_score: 市场状态评分
            theme_qualities: 主题质量评分 {theme: score}
            leader_scores: 龙头评分 {ts_code: score}
            capital_flow_scores: 资金流评分 {ts_code: score}

        Returns:
            List[ProbabilityResult]
        """
        results = []
        theme_qualities = theme_qualities or {}
        leader_scores = leader_scores or {}
        capital_flow_scores = capital_flow_scores or {}

        for c in candidates:
            ts_code = c.get('ts_code', '')
            df = df_cache.get(ts_code) if df_cache else None
            pb_result = c.get('pb_result')

            theme = c.get('theme', '')
            features = self.feature_extractor.extract(
                pb_result=pb_result,
                df=df,
                market_score=market_score,
                theme_quality=theme_qualities.get(theme, 50.0),
                leader_score=leader_scores.get(ts_code, 0),
                capital_flow_score=capital_flow_scores.get(ts_code, 50.0),
            )

            result = self.predict(features)
            result.ts_code = ts_code
            result.name = c.get('name', '')
            result.theme = theme
            results.append(result)

        return results

    def save(self, path: str = None):
        """保存模型"""
        if self.model is None:
            return
        save_path = path or self.model_path
        if not save_path:
            return
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, 'wb') as f:
            pickle.dump({
                'model': self.model,
                'scaler': self.scaler,
                'feature_names': self.feature_names,
            }, f)

    def load(self, path: str = None):
        """加载模型"""
        load_path = path or self.model_path
        if not load_path or not os.path.exists(load_path):
            return
        try:
            with open(load_path, 'rb') as f:
                data = pickle.load(f)
            self.model = data['model']
            self.scaler = data['scaler']
            self.feature_names = data.get('feature_names', self.feature_names)
            self.use_heuristic = False
        except Exception:
            self.use_heuristic = True

    # ──────────────────────────────────────────────
    # 启发式概率估计（回退方案）
    # ──────────────────────────────────────────────

    def _heuristic_predict(self, features: ProbabilityFeatures) -> float:
        """启发式概率估计（当无sklearn或无模型时使用）

        基于经验规则估算回调成功概率，权值可配置
        """
        prob = 0.5

        # 趋势质量加分
        if features.trend_quality > 70:
            prob += 0.08
        elif features.trend_quality > 50:
            prob += 0.03
        elif features.trend_quality < 30:
            prob -= 0.10

        # 均线排列
        if features.ma_alignment == 1:
            prob += 0.08
        elif features.ma_alignment == -1:
            prob -= 0.12

        # 距MA20位置（5~25%为理想回踩区间）
        dist = abs(features.distance_ma20)
        if 5 <= dist <= 25:
            prob += 0.06
        elif dist < 3:
            prob -= 0.05  # 太近了，还没跌到位
        elif dist > 40:
            prob -= 0.10  # 偏离太大，有追高风险

        # 回撤深度（10~20%为理想）
        dd = features.drawdown_from_high
        if 10 <= dd <= 20:
            prob += 0.08
        elif 5 <= dd < 10:
            prob += 0.04
        elif dd > 30:
            prob -= 0.10  # 回撤太大，趋势可能坏了

        # 资金流
        if features.capital_flow_score > 60:
            prob += 0.05
        elif features.capital_flow_score < 40:
            prob -= 0.05

        # 量比（缩量调整为佳）
        if 0.4 <= features.volume_ratio <= 0.8:
            prob += 0.06
        elif features.volume_ratio > 1.5:
            prob -= 0.05  # 放量下跌不好

        # RSI（40~60为中性偏强区间）
        if 40 <= features.rsi_14 <= 60:
            prob += 0.04
        elif features.rsi_14 < 30:
            prob -= 0.03  # 超卖但趋势不明

        # MACD柱值
        if features.macd_hist > 0.3:
            prob += 0.04
        elif features.macd_hist < -0.3:
            prob -= 0.04

        # 市场状态
        if features.market_state_score > 60:
            prob += 0.06
        elif features.market_state_score < 40:
            prob -= 0.06

        # 主题质量
        if features.theme_quality > 70:
            prob += 0.05
        elif features.theme_quality < 40:
            prob -= 0.05

        # 龙头加分
        if features.is_leader:
            prob += 0.06

        # 换手率稳定性
        if features.turnover_cv < 0.5:
            prob += 0.04
        elif features.turnover_cv > 1.0:
            prob -= 0.03

        return max(0.05, min(0.95, prob))

    def _model_predict(self, features: ProbabilityFeatures) -> float:
        """使用 Logistic Regression 模型预测"""
        try:
            vec = self._features_to_vector(features)
            if self.scaler:
                vec = self.scaler.transform(vec.reshape(1, -1))
            if self.model:
                prob = self.model.predict_proba(vec)[0, 1]
                return float(prob)
        except Exception:
            pass
        return self.default_prob

    def _features_to_vector(self, f: ProbabilityFeatures) -> np.ndarray:
        """特征转向量（按 feature_names 顺序）"""
        values = []
        for name in self.feature_names:
            values.append(getattr(f, name, 0.0))
        return np.array(values, dtype=float)

    @staticmethod
    def _prob_to_signal(prob: float) -> str:
        """概率转信号"""
        if prob >= 0.75:
            return 'buy'
        elif prob >= 0.60:
            return 'watch'
        elif prob >= 0.40:
            return 'neutral'
        return 'avoid'

    def generate_synthetic_training_data(self, n_samples: int = 500) -> Tuple[np.ndarray, np.ndarray]:
        """生成合成训练数据（用于原型验证）

        基于合理假设：高趋势质量+合理回撤+龙头=高成功率
        """
        np.random.seed(42)
        X = np.random.rand(n_samples, len(self.feature_names))
        # 标签规则
        y = np.zeros(n_samples)
        for i in range(n_samples):
            trend = X[i, 0] * 100       # trend_quality 0~100
            dist = X[i, 2] * 40         # distance_ma20 0~40
            dd = X[i, 6] * 30           # drawdown 0~30
            flow = X[i, 7] * 100        # capital_flow 0~100
            leader = X[i, 15]           # is_leader 0/1
            market = X[i, 13] * 100     # market_state 0~100

            score = 0.3 * (trend / 100) + 0.2 * (1 - abs(dist - 15) / 40) \
                    + 0.15 * (1 - dd / 30) + 0.1 * (flow / 100) \
                    + 0.15 * leader + 0.1 * (market / 100)
            y[i] = 1 if score > 0.45 + np.random.uniform(-0.1, 0.1) else 0

        return X, y


# ──────────────────────────────────────────────
# CLI 测试
# ──────────────────────────────────────────────

if __name__ == '__main__':
    import yaml
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f)

    pm = ProbabilityModel(cfg)
    print(f"\n概率预测模型初始化:")
    print(f"  模式: {'启发式' if pm.use_heuristic else 'Logistic Regression'}")
    print(f"  sklearn可用: {HAS_SKLEARN}")

    # 生成合成数据训练（作为demo）
    if HAS_SKLEARN and not pm.model:
        X, y = pm.generate_synthetic_training_data(500)
        metrics = pm.train(X, y, pm.feature_names)
        print(f"\n  模型训练完成:")
        for k, v in metrics.items():
            if isinstance(v, float):
                print(f"    {k}: {v:.4f}")

    # 测试预测
    features = ProbabilityFeatures(
        trend_quality=75.0,
        ma_alignment=1,
        distance_ma20=12.0,
        distance_ma60=8.0,
        ret_20d=8.5,
        ret_60d=35.0,
        drawdown_from_high=12.0,
        capital_flow_score=65.0,
        volume_ratio=0.55,
        rsi_14=48.0,
        macd_hist=0.35,
        market_state_score=62.0,
        theme_quality=80.0,
        is_leader=1,
        leader_score=85.0,
    )
    result = pm.predict(features)
    print(f"\n  预测示例:")
    print(f"    趋势质量: {features.trend_quality:.0f}")
    print(f"    回撤深度: {features.drawdown_from_high:.1f}%")
    print(f"    龙头: {'是' if features.is_leader else '否'}")
    print(f"    成功率: {result.probability:.1%}")
    print(f"    信号: {result.signal}")
