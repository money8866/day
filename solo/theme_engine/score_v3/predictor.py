"""Rotation Probability Predictor — 轮动概率预测.

预测未来5日成为市场前三主线的概率。
综合: 历史相似行情、主题加速度、ETF趋势、资金、龙头、生命周期。
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Dict, List, Optional

from theme_engine.score_v3.models import ThemeV3Score

logger = logging.getLogger(__name__)


def predict_rotation_probability(
    theme: ThemeV3Score,
    all_themes: List[ThemeV3Score],
    history: Optional[List[Dict[str, Any]]] = None,
) -> float:
    """预测未来5日成为TOP3主线的概率 (0~100%).

    基于以下维度加权:
    - ETF趋势权重 0.25
    - ETF加速度权重 0.25
    - 排名动量权重 0.15
    - 资金流权重 0.10
    - 龙头质量权重 0.10
    - 生命周期权重 0.10
    - 共振乘数权重 0.05
    """
    # 基础概率来自final_score的排名百分位
    if not all_themes:
        return 50.0

    total = len(all_themes)
    rank_percentile = 1.0 - (theme.rank - 1) / total if total > 0 else 0.5

    # 各维度贡献
    trend_norm = theme.etf_trend / 100.0
    accel_norm = theme.etf_accel / 100.0
    rank_mom_norm = theme.rank_momentum / 100.0
    money_norm = theme.money / 100.0
    leader_norm = theme.leader / 100.0
    resonance_boost = theme.resonance_multiplier - 1.0  # 0.3 或 -0.2

    # 生命周期修正
    life_bonus_map = {"main_up": 0.15, "growth": 0.08, "birth": 0.0, "late": -0.10, "decline": -0.20}
    life_adj = life_bonus_map.get(theme.life_stage, 0.0)

    # 综合概率
    prob = (
        rank_percentile * 0.20 +
        trend_norm * 0.20 +
        accel_norm * 0.20 +
        rank_mom_norm * 0.10 +
        money_norm * 0.10 +
        leader_norm * 0.10 +
        resonance_boost * 0.05 +
        life_adj * 0.05
    )

    # sigmoid 映射到 0~100 (带宽3, 避免极端拉开)
    prob = 100.0 / (1.0 + math.exp(-3.0 * (prob - 0.5)))
    return max(0.0, min(100.0, prob))
