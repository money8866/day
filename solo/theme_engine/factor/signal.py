"""SignalFactor — 交易信号生成因子.

根据各因子综合评分和 weights.yaml 中配置的阈值，
生成 STRONG_BUY / BUY / WATCH / REDUCE / EXIT 信号。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from theme_engine.config.settings import load_weights
from theme_engine.factor.base import BaseFactor
from theme_engine.models.dataclasses import FactorResult, SignalResult

logger = logging.getLogger(__name__)


class SignalFactor(BaseFactor):
    """交易信号生成因子."""

    name: str = "signal_factor"
    version: str = "1.0.0"
    weight_key: str = ""

    async def calculate(
        self,
        theme_code: str,
        trade_date: str,
        **kwargs: Any,
    ) -> FactorResult:
        """生成交易信号.

        kwargs 需要传入:
            total_score: float  — 综合评分 (0~100)
            或者各因子评分（自动汇总）：
            etf_strength: float
            breadth_score: float
            leader_score: float
            purity_score: float
            resonance_score: float
            flow_score: float
        """
        await asyncio.sleep(0)

        # ── 获取总分 ──────────────────────────────────────────
        total_score: float = kwargs.get("total_score", 0)

        # 如果没有直接传入 total_score，尝试从各因子汇总
        if total_score == 0:
            factor_scores = [
                kwargs.get("etf_strength", 0),
                kwargs.get("breadth_score", 0),
                kwargs.get("leader_score", 0),
                kwargs.get("purity_score", 0),
                kwargs.get("resonance_score", 0),
                kwargs.get("flow_score", 0),
            ]
            valid_scores = [s for s in factor_scores if s > 0]
            total_score = (
                sum(valid_scores) / len(valid_scores)
                if valid_scores
                else 0.0
            )

        total_score = max(0.0, min(100.0, total_score))

        # ── 读取阈值配置 ──────────────────────────────────────
        cfg = load_weights()
        thresholds = cfg.get("thresholds", {})
        strong_buy_threshold = float(thresholds.get("strong_buy", 85))
        buy_threshold = float(thresholds.get("buy", 70))
        watch_threshold = float(thresholds.get("watch", 50))
        reduce_threshold = float(thresholds.get("reduce", 35))
        exit_threshold = float(thresholds.get("exit", 20))

        # ── 判定信号 ──────────────────────────────────────────
        signal: str
        reasons: List[str] = []

        if total_score >= strong_buy_threshold:
            signal = "STRONG_BUY"
            reasons.append(f"综合评分 {total_score:.1f} ≥ {strong_buy_threshold}，强烈买入")
        elif total_score >= buy_threshold:
            signal = "BUY"
            reasons.append(f"综合评分 {total_score:.1f} ≥ {buy_threshold}，建议买入")
        elif total_score >= watch_threshold:
            signal = "WATCH"
            reasons.append(f"综合评分 {total_score:.1f} ≥ {watch_threshold}，持续观察")
        elif total_score >= reduce_threshold:
            signal = "REDUCE"
            reasons.append(f"综合评分 {total_score:.1f} ≥ {reduce_threshold}，建议减仓")
        else:
            signal = "EXIT"
            reasons.append(f"综合评分 {total_score:.1f} < {reduce_threshold}，建议离场")

        # ── 补充辅助理由 ──────────────────────────────────────
        etf = kwargs.get("etf_strength")
        if etf is not None and etf >= 80:
            reasons.append("ETF 强度高")

        breadth = kwargs.get("breadth_score")
        if breadth is not None and breadth >= 70:
            reasons.append("扩散度良好")

        resonance = kwargs.get("resonance_score")
        if resonance is not None and resonance >= 70:
            reasons.append("共振效应显著")

        # ── 信号强度：总分映射到 0~100 ───────────────────────
        signal_strength = total_score

        # ── 构建结果 ──────────────────────────────────────────
        signal_result = SignalResult(
            theme_code=theme_code,
            trade_date=trade_date,
            signal=signal,
            signal_strength=signal_strength,
            reasons=reasons,
            details={
                "total_score": total_score,
                "thresholds": {
                    "strong_buy": strong_buy_threshold,
                    "buy": buy_threshold,
                    "watch": watch_threshold,
                    "reduce": reduce_threshold,
                    "exit": exit_threshold,
                },
            },
        )

        # 信号分数映射
        signal_scores = {
            "STRONG_BUY": 100,
            "BUY": 85,
            "WATCH": 60,
            "REDUCE": 35,
            "EXIT": 10,
        }
        score = float(signal_scores.get(signal, 50))

        return FactorResult(
            factor_name=self.name,
            version=self.version,
            score=score,
            weight=0.0,
            contribution=0.0,
            details={"signal_result": signal_result.__dict__},
        )
