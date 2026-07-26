"""BreadthFactor — 主题扩散度评分因子.

通过外部传入的 theme_stocks 数据，
计算上涨比例、涨停比例、创新高比例、均线占比、
成交额扩散度、收益中位数、Alpha 均值等扩散度指标。
"""

from __future__ import annotations

import asyncio
import logging
from statistics import median
from typing import Any, Dict, List

from theme_engine.factor.base import BaseFactor
from theme_engine.models.dataclasses import BreadthResult, FactorResult

logger = logging.getLogger(__name__)


class BreadthFactor(BaseFactor):
    """主题扩散度评分因子."""

    name: str = "breadth"
    version: str = "1.0.0"
    weight_key: str = "breadth"

    async def calculate(
        self,
        theme_code: str,
        trade_date: str,
        **kwargs: Any,
    ) -> FactorResult:
        """计算主题扩散度评分.

        kwargs 需要传入:
            theme_stocks: List[Dict[str, Any]] — 主题成分股列表
                每只股票含以下字段（均为可选）：
                - pct_chg: float         涨幅%
                - limit_up: bool         是否涨停
                - new_high_20d: bool     是否20日新高
                - above_ma20: bool       是否站上20日线
                - above_ma60: bool       是否站上60日线
                - above_ma120: bool      是否站上120日线
                - amount: float          成交额
                - alpha: float           Alpha 值
                - relative_alpha: float  相对 Alpha 值
        """
        await asyncio.sleep(0)

        theme_stocks: List[Dict[str, Any]] = kwargs.get("theme_stocks", [])
        weights = self.get_weights()

        if not theme_stocks:
            logger.info("主题 %s 无成分股数据，返回默认分 50", theme_code)
            default_result = BreadthResult(
                theme_code=theme_code,
                trade_date=trade_date,
                breadth_score=50.0,
            )
            return FactorResult(
                factor_name=self.name,
                version=self.version,
                score=50.0,
                weight=0.0,
                contribution=0.0,
                details={"breadth_result": default_result.__dict__},
                error="成分股数据缺失，使用默认分",
            )

        total = len(theme_stocks)

        # ── 计算基础指标 ──────────────────────────────────────
        up_count = sum(
            1 for s in theme_stocks if (s.get("pct_chg") or 0) > 0
        )
        limit_up_count = sum(
            1 for s in theme_stocks if s.get("limit_up", False)
        )
        new_high_20d_count = sum(
            1 for s in theme_stocks if s.get("new_high_20d", False)
        )
        above_ma20_count = sum(
            1 for s in theme_stocks if s.get("above_ma20", False)
        )
        above_ma60_count = sum(
            1 for s in theme_stocks if s.get("above_ma60", False)
        )
        above_ma120_count = sum(
            1 for s in theme_stocks if s.get("above_ma120", False)
        )

        up_ratio = up_count / total if total > 0 else 0.0
        limit_up_ratio = limit_up_count / total if total > 0 else 0.0
        new_high_20d_ratio = new_high_20d_count / total if total > 0 else 0.0
        above_ma20_ratio = above_ma20_count / total if total > 0 else 0.0
        above_ma60_ratio = above_ma60_count / total if total > 0 else 0.0
        above_ma120_ratio = above_ma120_count / total if total > 0 else 0.0

        # ── 成交额扩散度 ──────────────────────────────────────
        amounts = [s.get("amount", 0) or 0 for s in theme_stocks]
        total_amount = sum(amounts)
        if total_amount > 0 and total > 1:
            # 计算成交额集中度：前 20% 股票的成交额占比
            sorted_amounts = sorted(amounts, reverse=True)
            top_n = max(1, total // 5)
            top_amount = sum(sorted_amounts[:top_n])
            amount_diffusion = 1.0 - (top_amount / total_amount)
        else:
            amount_diffusion = 0.5

        # ── 收益率中位数 ──────────────────────────────────────
        returns = [s.get("pct_chg", 0) or 0 for s in theme_stocks]
        return_median_val = median(returns) if returns else 0.0

        # ── Alpha 均值 ────────────────────────────────────────
        alphas = [s.get("alpha", 0) or 0 for s in theme_stocks]
        avg_alpha = sum(alphas) / len(alphas) if alphas else 0.0

        relative_alphas = [
            s.get("relative_alpha", 0) or 0 for s in theme_stocks
        ]
        avg_relative_alpha = (
            sum(relative_alphas) / len(relative_alphas)
            if relative_alphas
            else 0.0
        )

        # ── 归一化到 0~100 ────────────────────────────────────
        up_ratio_score = self.normalize(up_ratio, 0, 0.8)
        limit_up_ratio_score = self.normalize(limit_up_ratio, 0, 0.3)
        new_high_20d_ratio_score = self.normalize(new_high_20d_ratio, 0, 0.5)
        above_ma20_ratio_score = self.normalize(above_ma20_ratio, 0, 1.0)
        above_ma60_ratio_score = self.normalize(above_ma60_ratio, 0, 1.0)
        above_ma120_ratio_score = self.normalize(above_ma120_ratio, 0, 1.0)
        amount_diffusion_score = amount_diffusion * 100.0
        return_median_score = self.normalize(return_median_val, -5, 5)
        avg_alpha_score = self.normalize(avg_alpha, -3, 3)
        avg_relative_alpha_score = self.normalize(avg_relative_alpha, -3, 3)

        # ── 加权总分 ──────────────────────────────────────────
        sub_scores = {
            "up_ratio": up_ratio_score,
            "limit_up_ratio": limit_up_ratio_score,
            "new_high_20d_ratio": new_high_20d_ratio_score,
            "above_ma20_ratio": above_ma20_ratio_score,
            "above_ma60_ratio": above_ma60_ratio_score,
            "above_ma120_ratio": above_ma120_ratio_score,
            "amount_diffusion": amount_diffusion_score,
            "return_median": return_median_score,
            "avg_alpha": avg_alpha_score,
            "avg_relative_alpha": avg_relative_alpha_score,
        }

        breadth_score = 0.0
        total_weight = sum(weights.values())
        if total_weight > 0:
            for key, w in weights.items():
                breadth_score += sub_scores.get(key, 50.0) * w

        breadth_score = max(0.0, min(100.0, breadth_score))

        # ── 构建结果 ──────────────────────────────────────────
        breadth_result = BreadthResult(
            theme_code=theme_code,
            trade_date=trade_date,
            total_stocks=total,
            up_ratio=up_ratio,
            limit_up_ratio=limit_up_ratio,
            new_high_20d_ratio=new_high_20d_ratio,
            above_ma20_ratio=above_ma20_ratio,
            above_ma60_ratio=above_ma60_ratio,
            above_ma120_ratio=above_ma120_ratio,
            amount_diffusion=amount_diffusion,
            return_median=return_median_val,
            avg_alpha=avg_alpha,
            avg_relative_alpha=avg_relative_alpha,
            breadth_score=breadth_score,
            details={"stock_count": total},
        )

        contribution = breadth_score * total_weight / 100.0 if total_weight > 0 else 0.0

        return FactorResult(
            factor_name=self.name,
            version=self.version,
            score=breadth_score,
            weight=total_weight,
            contribution=contribution,
            details={"breadth_result": breadth_result.__dict__},
        )
