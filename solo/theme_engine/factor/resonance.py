"""ResonanceFactor — ETF-Theme 共振评分因子.

综合 ETF 强度、主题扩散度、龙头强度三个维度，
计算一致性、方差惩罚、标准差、相关系数等共振指标。
当三个分数接近且均处于高位时，共振评分最高。
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Dict

from theme_engine.factor.base import BaseFactor
from theme_engine.models.dataclasses import FactorResult, ResonanceResult

logger = logging.getLogger(__name__)


class ResonanceFactor(BaseFactor):
    """ETF-Theme 共振评分因子."""

    name: str = "resonance"
    version: str = "1.0.0"
    weight_key: str = "resonance"

    async def calculate(
        self,
        theme_code: str,
        trade_date: str,
        **kwargs: Any,
    ) -> FactorResult:
        """计算共振评分.

        kwargs 需要传入:
            etf_strength: float   — ETF 强度评分 (0~100)
            breadth_score: float  — 主题扩散度评分 (0~100)
            leader_score: float   — 龙头强度评分 (0~100)
        """
        await asyncio.sleep(0)

        etf_strength: float = kwargs.get("etf_strength", 50.0)
        breadth_score: float = kwargs.get("breadth_score", 50.0)
        leader_score: float = kwargs.get("leader_score", 50.0)

        weights = self.get_weights()

        scores = [etf_strength, breadth_score, leader_score]
        n = len(scores)

        # ── 均值 ──────────────────────────────────────────────
        mean = sum(scores) / n

        # ── 标准差 (std) ──────────────────────────────────────
        variance = sum((s - mean) ** 2 for s in scores) / n
        std = math.sqrt(variance)

        # ── 一致性 (consistency)：三个值越接近越高 ────────────
        # 最大可能差异 = 100, 用平均绝对偏差衡量
        max_diff = 100.0
        mad = sum(abs(s - mean) for s in scores) / n
        consistency_score = max(0.0, 100.0 - (mad / max_diff) * 100.0)

        # ── 方差惩罚：方差越大，惩罚越大 ──────────────────────
        # 方差 0~2500 映射到惩罚 0~50
        variance_penalty = max(0.0, 100.0 - (variance / 2500.0) * 50.0)

        # ── 相关系数近似：三个维度两两相似度 ──────────────────
        # 用 pairwise 差的均值作为"反相关"度量
        pairwise_diffs = []
        for i in range(n):
            for j in range(i + 1, n):
                pairwise_diffs.append(abs(scores[i] - scores[j]))
        avg_pairwise_diff = (
            sum(pairwise_diffs) / len(pairwise_diffs)
            if pairwise_diffs
            else 0.0
        )
        correlation = max(0.0, 100.0 - avg_pairwise_diff)

        # ── 加权总分 ──────────────────────────────────────────
        sub_scores = {
            "consistency_score": consistency_score,
            "variance_penalty": variance_penalty,
            "std": 100.0 - self.normalize(std, 0, 50),
            "correlation": correlation,
        }

        resonance_score = 0.0
        total_weight = sum(weights.values())
        if total_weight > 0:
            for key, w in weights.items():
                resonance_score += sub_scores.get(key, 50.0) * w

        resonance_score = max(0.0, min(100.0, resonance_score))

        # ── 构建结果 ──────────────────────────────────────────
        resonance_result = ResonanceResult(
            theme_code=theme_code,
            trade_date=trade_date,
            etf_strength=etf_strength,
            theme_breadth=breadth_score,
            leader_score=leader_score,
            consistency_score=consistency_score,
            variance_penalty=variance_penalty,
            std=std,
            correlation=correlation,
            resonance_score=resonance_score,
            details={
                "mean": mean,
                "mad": mad,
                "variance": variance,
                "pairwise_diffs": pairwise_diffs,
            },
        )

        contribution = resonance_score * total_weight / 100.0 if total_weight > 0 else 0.0

        return FactorResult(
            factor_name=self.name,
            version=self.version,
            score=resonance_score,
            weight=total_weight,
            contribution=contribution,
            details={"resonance_result": resonance_result.__dict__},
        )
