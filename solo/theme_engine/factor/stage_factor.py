"""StageFactor — 生命周期阶段判定因子.

使用状态机逻辑综合 ETF 强度、扩散度、龙头强度、
共振和资金流评分，判定主题当前所处生命周期阶段：
  birth → growth → expansion → main_trend → distribution → death
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from theme_engine.config.settings import load_weights
from theme_engine.factor.base import BaseFactor
from theme_engine.models.dataclasses import FactorResult, StageResult

logger = logging.getLogger(__name__)

# 生命周期阶段顺序
_STAGE_ORDER = [
    "birth",
    "growth",
    "expansion",
    "main_trend",
    "distribution",
    "death",
]


class StageFactor(BaseFactor):
    """生命周期阶段判定因子."""

    name: str = "stage_factor"
    version: str = "1.0.0"
    weight_key: str = ""

    async def calculate(
        self,
        theme_code: str,
        trade_date: str,
        **kwargs: Any,
    ) -> FactorResult:
        """判定主题生命周期阶段.

        kwargs 需要传入:
            etf_strength: float      — ETF 强度 (0~100)
            breadth_score: float     — 扩散度 (0~100)
            leader_score: float      — 龙头强度 (0~100)
            resonance_score: float   — 共振评分 (0~100)
            flow_score: float        — 资金流评分 (0~100)
            days_in_stage: int       — 当前阶段已持续天数
            current_stage: str       — 当前阶段（可选）
        """
        await asyncio.sleep(0)

        etf_strength: float = kwargs.get("etf_strength", 50.0)
        breadth_score: float = kwargs.get("breadth_score", 50.0)
        leader_score: float = kwargs.get("leader_score", 50.0)
        resonance_score: float = kwargs.get("resonance_score", 50.0)
        flow_score: float = kwargs.get("flow_score", 50.0)
        days_in_stage: int = kwargs.get("days_in_stage", 0) or 0
        current_stage: str = kwargs.get("current_stage", "birth")

        # 加载生命周期阈值
        cfg = load_weights()
        stage_cfg = cfg.get("stage", {})

        # ── 计算阶段倾向证据分数 ──────────────────────────────
        # 每个阶段定义一组判定规则，计算"证据分数"
        stage_evidence: Dict[str, float] = {}

        # birth: 成分股数量达标，ETF 初现强度
        stage_evidence["birth"] = _birth_evidence(
            etf_strength, stage_cfg
        )

        # growth: ETF 和涨幅扩散初具规模
        stage_evidence["growth"] = _growth_evidence(
            etf_strength, breadth_score, stage_cfg
        )

        # expansion: 扩散度扩大，龙头显现
        stage_evidence["expansion"] = _expansion_evidence(
            etf_strength, breadth_score, leader_score, stage_cfg
        )

        # main_trend: 全面共振，各项指标高位
        stage_evidence["main_trend"] = _main_trend_evidence(
            etf_strength, breadth_score, leader_score,
            resonance_score, flow_score, stage_cfg
        )

        # distribution: 扩散度回落，龙头乏力
        stage_evidence["distribution"] = _distribution_evidence(
            etf_strength, breadth_score, leader_score, stage_cfg
        )

        # death: 全面衰减
        stage_evidence["death"] = _death_evidence(
            etf_strength, breadth_score, days_in_stage, stage_cfg
        )

        # ── 状态机：基于当前阶段和证据分数做决策 ──────────────
        current_idx = (
            _STAGE_ORDER.index(current_stage)
            if current_stage in _STAGE_ORDER
            else 0
        )

        # 检查是否可以前进到下一阶段
        next_stage: Optional[str] = None
        if current_idx < len(_STAGE_ORDER) - 1:
            next_candidate = _STAGE_ORDER[current_idx + 1]
            # 下一阶段的证据分数需高于当前阶段
            if stage_evidence.get(next_candidate, 0) > stage_evidence.get(
                current_stage, 0
            ):
                next_stage = next_candidate

        # 检查是否可以回退
        prev_stage: Optional[str] = None
        if current_idx > 0:
            prev_candidate = _STAGE_ORDER[current_idx - 1]
            if stage_evidence.get(prev_candidate, 0) > stage_evidence.get(
                current_stage, 0
            ) + 10:
                prev_stage = prev_candidate

        # ── 确定当前阶段 ──────────────────────────────────────
        # 取证据分数最高的阶段作为最终判定
        best_stage = max(
            stage_evidence, key=lambda k: stage_evidence[k]
        )

        # 置信度：基于最佳阶段与次佳阶段的分数差距
        sorted_evidences = sorted(
            stage_evidence.values(), reverse=True
        )
        if len(sorted_evidences) >= 2:
            gap = sorted_evidences[0] - sorted_evidences[1]
            confidence = min(100.0, max(0.0, gap * 10))
        else:
            confidence = 50.0

        # 阶段内进度：基于证据分数与阶段阈值的比例
        stage_progress = min(1.0, stage_evidence.get(best_stage, 0) / 100.0)

        # ── 构建结果 ──────────────────────────────────────────
        stage_result = StageResult(
            theme_code=theme_code,
            trade_date=trade_date,
            current_stage=best_stage,
            stage_confidence=confidence,
            days_in_stage=days_in_stage,
            stage_progress=stage_progress,
            next_stage=next_stage,
            indicators={
                "etf_strength": etf_strength,
                "breadth_score": breadth_score,
                "leader_score": leader_score,
                "resonance_score": resonance_score,
                "flow_score": flow_score,
                "previous_stage": prev_stage,
            },
            details={
                "stage_evidence": stage_evidence,
                "stage_cfg": stage_cfg,
            },
        )

        # StageFactor 没有 weights 配置，直接返回
        score = float(
            _STAGE_ORDER.index(best_stage) / (len(_STAGE_ORDER) - 1) * 100
        )

        return FactorResult(
            factor_name=self.name,
            version=self.version,
            score=score,
            weight=0.0,
            contribution=0.0,
            details={"stage_result": stage_result.__dict__},
        )


def _birth_evidence(
    etf_strength: float,
    cfg: Dict[str, Any],
) -> float:
    """计算 birth 阶段证据分数."""
    # birth 阶段特征：ETF 强度低，但开始出现
    evidence = 0.0
    min_stocks = float(cfg.get("birth_min_stocks", 3))

    # ETF 强度适中偏低时最有可能是 birth
    if etf_strength < 30:
        evidence += 40
    elif etf_strength < 50:
        evidence += 20

    evidence += min_stocks * 5

    return evidence


def _growth_evidence(
    etf_strength: float,
    breadth_score: float,
    cfg: Dict[str, Any],
) -> float:
    """计算 growth 阶段证据分数."""
    evidence = 0.0
    min_etf = float(cfg.get("growth_min_etf_strength", 40))

    if etf_strength >= min_etf:
        evidence += 30
    if breadth_score > 30:
        evidence += 30
    if etf_strength < 70:
        evidence += 20

    return evidence


def _expansion_evidence(
    etf_strength: float,
    breadth_score: float,
    leader_score: float,
    cfg: Dict[str, Any],
) -> float:
    """计算 expansion 阶段证据分数."""
    evidence = 0.0
    min_breadth = float(cfg.get("expansion_min_breadth", 50))

    if breadth_score >= min_breadth:
        evidence += 30
    if leader_score > 50:
        evidence += 25
    if etf_strength > 50:
        evidence += 20
    if breadth_score > 40 and leader_score > 40:
        evidence += 25

    return evidence


def _main_trend_evidence(
    etf_strength: float,
    breadth_score: float,
    leader_score: float,
    resonance_score: float,
    flow_score: float,
    cfg: Dict[str, Any],
) -> float:
    """计算 main_trend 阶段证据分数."""
    evidence = 0.0
    min_resonance = float(cfg.get("main_trend_min_resonance", 70))

    if resonance_score >= min_resonance:
        evidence += 30
    if etf_strength > 70:
        evidence += 20
    if breadth_score > 60:
        evidence += 15
    if leader_score > 70:
        evidence += 15
    if flow_score > 60:
        evidence += 10

    # 全面共振加分
    high_count = sum(
        1 for s in [etf_strength, breadth_score, leader_score]
        if s > 60
    )
    evidence += high_count * 10

    return evidence


def _distribution_evidence(
    etf_strength: float,
    breadth_score: float,
    leader_score: float,
    cfg: Dict[str, Any],
) -> float:
    """计算 distribution 阶段证据分数."""
    evidence = 0.0
    max_breadth = float(cfg.get("distribution_max_breadth", 60))

    if breadth_score < max_breadth:
        evidence += 20
    if leader_score < 50:
        evidence += 20
    if etf_strength < 60:
        evidence += 15
    if breadth_score < 40:
        evidence += 15

    return evidence


def _death_evidence(
    etf_strength: float,
    breadth_score: float,
    days_in_stage: int,
    cfg: Dict[str, Any],
) -> float:
    """计算 death 阶段证据分数."""
    evidence = 0.0
    max_etf = float(cfg.get("death_max_etf_strength", 20))
    death_days = int(cfg.get("death_days_threshold", 10))

    if etf_strength <= max_etf:
        evidence += 40
    if breadth_score < 20:
        evidence += 30
    if days_in_stage >= death_days:
        evidence += 20

    return evidence
