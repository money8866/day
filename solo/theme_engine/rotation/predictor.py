"""TERE V1 主线轮动概率预测器.

基于多维度动量、广度趋势、共振趋势、历史数据，
预测未来3日/5日/10日该主题继续作为主线的概率。
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from theme_engine.config.settings import get_factor_weights, load_weights
from theme_engine.models.dataclasses import RotationResult

logger = logging.getLogger(__name__)


class RotationPredictor:
    """主线轮动概率预测器.

    维护每个主题的历史指标快照（按交易日），
    从中计算动量、趋势等特征，结合 weights.yaml 中的 rotation 子因子权重，
    使用 sigmoid 将分数映射到 0~100 概率。
    """

    # 用于动量计算的周期
    _MOMENTUM_PERIODS = {
        "short": 3,
        "medium": 5,
        "long": 10,
    }

    def __init__(self, max_history_days: int = 60) -> None:
        # theme_code -> [(trade_date, indicators_dict), ...]  按时间升序
        self._history: Dict[str, List[Tuple[str, Dict[str, float]]]] = defaultdict(list)
        self._max_history_days = max_history_days
        self._rotation_weights: Dict[str, float] = {}

    # ── 历史记录管理 ────────────────────────────────────────

    def record(self, theme_code: str, trade_date: str, indicators: Dict[str, float]) -> None:
        """记录某日指标快照."""
        hist = self._history[theme_code]
        # 避免重复插入同一交易日
        if hist and hist[-1][0] == trade_date:
            hist[-1] = (trade_date, dict(indicators))
        else:
            hist.append((trade_date, dict(indicators)))
        # 裁剪超期数据
        if len(hist) > self._max_history_days:
            self._history[theme_code] = hist[-self._max_history_days:]

    def get_history(
        self,
        theme_code: str,
        days: int = 20,
    ) -> List[Tuple[str, Dict[str, float]]]:
        """获取最近 N 天的历史快照."""
        hist = self._history.get(theme_code, [])
        return hist[-days:] if hist else []

    # ── 核心预测 ────────────────────────────────────────────

    async def predict(
        self,
        theme_code: str,
        trade_date: str,
        indicators: Dict[str, float],
        history_days: int = 20,
    ) -> RotationResult:
        """执行轮动概率预测.

        会先调用 record() 记录当日数据，然后基于历史数据计算动量。

        Args:
            theme_code: 主题代码
            trade_date: 交易日 YYYYMMDD
            indicators: 包含当前指标的字典，至少应含:
                etf_strength, breadth, leader, resonance, flow
            history_days: 用于计算的回溯天数

        Returns:
            RotationResult 包含各周期概率和明细
        """
        await asyncio.sleep(0)

        # 加载 rotation 权重配置
        self._load_rotation_weights()

        # 记录当日数据
        self.record(theme_code, trade_date, indicators)

        # 获取历史数据
        hist = self.get_history(theme_code, history_days)

        # 计算各维度动量/趋势
        momentum = self._calc_momentum(hist, indicators)

        # 计算各周期概率
        prob_3d = self._calc_prob_3d(momentum, indicators)
        prob_5d = self._calc_prob_5d(momentum, indicators, prob_3d)
        prob_10d = self._calc_prob_10d(momentum, indicators, prob_3d, prob_5d)

        # 综合轮动评分
        rotation_score = (prob_3d + prob_5d + prob_10d) / 3.0

        result = RotationResult(
            theme_code=theme_code,
            trade_date=trade_date,
            prob_3d=round(prob_3d, 2),
            prob_5d=round(prob_5d, 2),
            prob_10d=round(prob_10d, 2),
            etf_momentum=round(momentum.get("etf_momentum_3d", 0.0), 4),
            leader_momentum=round(momentum.get("leader_momentum_3d", 0.0), 4),
            breadth_trend=round(momentum.get("breadth_trend_3d", 0.0), 4),
            resonance_trend=round(momentum.get("resonance_trend_5d", 0.0), 4),
            rotation_score=round(rotation_score, 2),
            details={
                "momentum": {k: round(v, 4) for k, v in momentum.items()},
                "prob_3d_factors": self._factor_details(momentum, indicators, 3),
                "prob_5d_factors": self._factor_details(momentum, indicators, 5),
                "prob_10d_factors": self._factor_details(momentum, indicators, 10),
                "history_days": len(hist),
            },
        )

        return result

    # ── 各周期概率计算 ──────────────────────────────────────

    def _calc_prob_3d(
        self,
        momentum: Dict[str, float],
        indicators: Dict[str, float],
    ) -> float:
        """计算 3 日轮动概率.

        因子: etf_momentum(0.20) + leader_momentum(0.15) + breadth_trend(0.15)
        """
        weights = self._rotation_weights
        factors: Dict[str, float] = {}

        factors["etf_momentum"] = momentum.get("etf_momentum_3d", 0.0)
        factors["leader_momentum"] = momentum.get("leader_momentum_3d", 0.0)
        factors["breadth_trend"] = momentum.get("breadth_trend_3d", 0.0)

        # 当前值也作为参考
        etf_current = indicators.get("etf_strength", 50)
        factors["etf_current"] = (etf_current - 50) / 50.0 * 100.0  # 归一化到 -100~100

        # 选用的因子及其权重
        factor_keys = ["etf_momentum", "leader_momentum", "breadth_trend", "etf_current"]
        selected_weights = {
            k: weights.get(k, 0.0)
            for k in factor_keys
        }
        # 手动调整 etf_current 权重
        selected_weights["etf_current"] = 0.10

        return self._weighted_score(factors, selected_weights)

    def _calc_prob_5d(
        self,
        momentum: Dict[str, float],
        indicators: Dict[str, float],
        prob_3d: float,
    ) -> float:
        """计算 5 日轮动概率.

        在 prob_3d 基础上追加:
            resonance_trend(0.15) + historical_prob_3d(0.15)
        """
        weights = self._rotation_weights
        factors: Dict[str, float] = {}

        # 短周期因子
        factors["etf_momentum"] = momentum.get("etf_momentum_3d", 0.0)
        factors["leader_momentum"] = momentum.get("leader_momentum_3d", 0.0)
        factors["breadth_trend"] = momentum.get("breadth_trend_3d", 0.0)

        # 中周期因子
        factors["resonance_trend"] = momentum.get("resonance_trend_5d", 0.0)
        # 将 prob_3d 映射到 -100~100 范围
        factors["historical_prob_3d"] = (prob_3d - 50.0) * 2.0

        # 5日ETF动量
        factors["etf_momentum_5d"] = momentum.get("etf_momentum_5d", 0.0)

        factor_keys = [
            "etf_momentum", "leader_momentum", "breadth_trend",
            "resonance_trend", "historical_prob_3d", "etf_momentum_5d",
        ]
        selected_weights = {
            k: weights.get(k, 0.0)
            for k in factor_keys
        }

        return self._weighted_score(factors, selected_weights)

    def _calc_prob_10d(
        self,
        momentum: Dict[str, float],
        indicators: Dict[str, float],
        prob_3d: float,
        prob_5d: float,
    ) -> float:
        """计算 10 日轮动概率.

        使用全部 rotation 子因子加权:
            etf_momentum + leader_momentum + breadth_trend +
            resonance_trend + historical_prob_3d +
            historical_prob_5d + historical_prob_10d
        """
        weights = self._rotation_weights
        factors: Dict[str, float] = {}

        # 短周期因子
        factors["etf_momentum"] = momentum.get("etf_momentum_3d", 0.0)
        factors["leader_momentum"] = momentum.get("leader_momentum_3d", 0.0)
        factors["breadth_trend"] = momentum.get("breadth_trend_3d", 0.0)

        # 中周期因子
        factors["resonance_trend"] = momentum.get("resonance_trend_5d", 0.0)
        factors["historical_prob_3d"] = (prob_3d - 50.0) * 2.0

        # 长周期因子
        factors["historical_prob_5d"] = (prob_5d - 50.0) * 2.0
        factors["historical_prob_10d"] = momentum.get("breadth_trend_10d", 0.0)

        # 10日动量
        factors["etf_momentum_10d"] = momentum.get("etf_momentum_10d", 0.0)

        factor_keys = [
            "etf_momentum", "leader_momentum", "breadth_trend",
            "resonance_trend", "historical_prob_3d",
            "historical_prob_5d", "historical_prob_10d",
            "etf_momentum_10d",
        ]
        selected_weights = {
            k: weights.get(k, 0.0)
            for k in factor_keys
        }

        return self._weighted_score(factors, selected_weights)

    # ── 动量/趋势计算 ───────────────────────────────────────

    def _calc_momentum(
        self,
        history: List[Tuple[str, Dict[str, float]]],
        current: Dict[str, float],
    ) -> Dict[str, float]:
        """从历史数据计算动量/趋势指标."""
        momentum: Dict[str, float] = {}

        if not history:
            # 无历史数据时使用当前值的近似动量
            etf = current.get("etf_strength", 50)
            momentum["etf_momentum_3d"] = (etf - 50) * 0.5
            momentum["etf_momentum_5d"] = (etf - 50) * 0.3
            momentum["etf_momentum_10d"] = (etf - 50) * 0.2
            momentum["leader_momentum_3d"] = (current.get("leader", 50) - 50) * 0.5
            momentum["breadth_trend_3d"] = (current.get("breadth", 50) - 50) * 0.5
            momentum["breadth_trend_10d"] = (current.get("breadth", 50) - 50) * 0.3
            momentum["resonance_trend_5d"] = (current.get("resonance", 50) - 50) * 0.4
            return momentum

        # 提取最近的历史数据
        recent = [v for _, v in history]

        # 3日动量: 当前值 - 3日前值
        if len(recent) >= 3:
            d3 = recent[-3]
            momentum["etf_momentum_3d"] = (
                current.get("etf_strength", 50) - d3.get("etf_strength", 50)
            )
            momentum["leader_momentum_3d"] = (
                current.get("leader", 50) - d3.get("leader", 50)
            )
            momentum["breadth_trend_3d"] = (
                current.get("breadth", 50) - d3.get("breadth", 50)
            )
        else:
            # 不足3天用可用数据
            oldest = recent[0]
            momentum["etf_momentum_3d"] = (
                current.get("etf_strength", 50) - oldest.get("etf_strength", 50)
            )
            momentum["leader_momentum_3d"] = (
                current.get("leader", 50) - oldest.get("leader", 50)
            )
            momentum["breadth_trend_3d"] = (
                current.get("breadth", 50) - oldest.get("breadth", 50)
            )

        # 5日动量
        if len(recent) >= 5:
            d5 = recent[-5]
            momentum["etf_momentum_5d"] = (
                current.get("etf_strength", 50) - d5.get("etf_strength", 50)
            )
            momentum["resonance_trend_5d"] = (
                current.get("resonance", 50) - d5.get("resonance", 50)
            )
        else:
            oldest = recent[0]
            momentum["etf_momentum_5d"] = (
                current.get("etf_strength", 50) - oldest.get("etf_strength", 50)
            )
            momentum["resonance_trend_5d"] = (
                current.get("resonance", 50) - oldest.get("resonance", 50)
            )

        # 10日动量
        if len(recent) >= 10:
            d10 = recent[-10]
            momentum["etf_momentum_10d"] = (
                current.get("etf_strength", 50) - d10.get("etf_strength", 50)
            )
            momentum["breadth_trend_10d"] = (
                current.get("breadth", 50) - d10.get("breadth", 50)
            )
        else:
            oldest = recent[0]
            momentum["etf_momentum_10d"] = (
                current.get("etf_strength", 50) - oldest.get("etf_strength", 50)
            )
            momentum["breadth_trend_10d"] = (
                current.get("breadth", 50) - oldest.get("breadth", 50)
            )

        return momentum

    # ── 工具方法 ────────────────────────────────────────────

    def _weighted_score(
        self,
        factors: Dict[str, float],
        weights: Dict[str, float],
    ) -> float:
        """加权计算后通过 sigmoid 映射到 0~100.

        1. 因子值 * 权重 求和得 raw_score
        2. 总权重归一化
        3. sigmoid 映射到 0~100
        """
        total_weight = sum(weights.values())
        if total_weight <= 0:
            return 50.0

        raw_score = 0.0
        for key, weight in weights.items():
            factor_val = factors.get(key, 0.0)
            raw_score += factor_val * weight

        # 归一化到 -100~100 范围
        normalized = raw_score / total_weight
        # sigmoid 映射到 0~100
        probability = self._sigmoid(normalized, midpoint=0.0, steepness=0.05)

        return max(0.0, min(100.0, probability))

    @staticmethod
    def _sigmoid(value: float, midpoint: float = 0.0, steepness: float = 0.05) -> float:
        """使用 sigmoid 将值映射到 0~100 区间.

        Args:
            value: 输入值
            midpoint: 中心点（默认0），值为0时输出50%
            steepness: 陡峭度，值越大 sigmoid 曲线越陡

        Returns:
            0~100 的概率值
        """
        return 100.0 / (1.0 + math.exp(-steepness * (value - midpoint)))

    def _factor_details(
        self,
        momentum: Dict[str, float],
        indicators: Dict[str, float],
        period: int,
    ) -> Dict[str, float]:
        """生成因子明细，用于 details 输出."""
        details: Dict[str, float] = {}
        weights = self._rotation_weights

        prefix_map = {3: "3d", 5: "5d", 10: "10d"}
        suffix = prefix_map.get(period, "3d")

        for key, weight in weights.items():
            # 尝试从 momentum 获取
            if key.startswith("historical"):
                continue  # 历史概率在计算时动态生成
            m_key = f"{key}_{suffix}"
            if m_key in momentum:
                details[f"{key}_{suffix}"] = round(momentum[m_key] * weight, 4)
            else:
                # 尝试从 momentum 直接取
                for m_k, m_v in momentum.items():
                    if m_k.startswith(key):
                        details[m_k] = round(m_v * weight, 4)

        return details

    def _load_rotation_weights(self) -> None:
        """从 weights.yaml 加载 rotation 子因子权重."""
        try:
            self._rotation_weights = get_factor_weights("rotation")
        except Exception as e:
            logger.warning("加载 rotation 权重失败: %s", e)
            self._rotation_weights = {
                "etf_momentum": 0.20,
                "leader_momentum": 0.15,
                "breadth_trend": 0.15,
                "resonance_trend": 0.15,
                "historical_prob_3d": 0.15,
                "historical_prob_5d": 0.10,
                "historical_prob_10d": 0.10,
            }
