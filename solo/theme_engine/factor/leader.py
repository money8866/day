"""LeaderStrengthFactor — 龙头/核心强度评分因子.

通过外部传入的 theme_stocks 数据，动态识别龙头股和核心股，
计算龙头趋势、Alpha、相对强度、成交量、资金流、机构评分等子因子。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from theme_engine.factor.base import BaseFactor
from theme_engine.models.dataclasses import FactorResult, LeaderResult

logger = logging.getLogger(__name__)


class LeaderStrengthFactor(BaseFactor):
    """龙头/核心强度评分因子."""

    name: str = "leader"
    version: str = "1.0.0"
    weight_key: str = "leader"

    async def calculate(
        self,
        theme_code: str,
        trade_date: str,
        **kwargs: Any,
    ) -> FactorResult:
        """计算龙头/核心强度评分.

        kwargs 需要传入:
            theme_stocks: List[Dict[str, Any]] — 主题成分股列表
                每只股票含以下字段：
                - code: str              股票代码
                - name: str              股票名称
                - pct_chg: float         涨幅%
                - amount: float          成交额
                - total_mv: float        总市值
                - alpha: float           Alpha 值
                - relative_strength: float 相对强度
                - volume_ratio: float    量比
                - money_flow: float      资金净流入
                - institution_holding: float 机构持仓比例
                - macd: float            MACD 值
                - ma_trend: float        MA 趋势值
        """
        await asyncio.sleep(0)

        theme_stocks: List[Dict[str, Any]] = kwargs.get("theme_stocks", [])
        weights = self.get_weights()

        if not theme_stocks:
            logger.info("主题 %s 无成分股数据，返回默认分 50", theme_code)
            default_result = LeaderResult(
                theme_code=theme_code,
                trade_date=trade_date,
                leader_strength=50.0,
            )
            return FactorResult(
                factor_name=self.name,
                version=self.version,
                score=50.0,
                weight=0.0,
                contribution=0.0,
                details={"leader_result": default_result.__dict__},
                error="成分股数据缺失，使用默认分",
            )

        # ── 动态识别龙头和核心 ────────────────────────────────
        # 按涨幅降序排列
        sorted_by_return = sorted(
            theme_stocks,
            key=lambda s: s.get("pct_chg", 0) or 0,
            reverse=True,
        )

        # 龙头：涨幅最高的 3 只
        leaders = sorted_by_return[:3]
        leader_codes = [s.get("code", "") for s in leaders]

        # 核心：市值大 + 涨幅稳（涨幅在前 50% 中按市值排序，排除已选龙头）
        threshold_idx = max(1, len(sorted_by_return) // 2)
        candidates = [
            s
            for s in sorted_by_return[:threshold_idx]
            if s.get("code", "") not in leader_codes
        ]
        cores = sorted(
            candidates,
            key=lambda s: abs(s.get("pct_chg", 0) or 0) + (
                s.get("total_mv", 0) or 0
            ) / 1e10,
            reverse=True,
        )[:3]

        follower_count = max(
            0, len(theme_stocks) - len(leaders) - len(cores)
        )

        # ── 计算龙头相关子因子 ────────────────────────────────
        if leaders:
            # leader_trend: 龙头股平均涨幅
            leader_trend = sum(
                s.get("pct_chg", 0) or 0 for s in leaders
            ) / len(leaders)

            # leader_alpha: 龙头股平均 Alpha
            leader_alpha = sum(
                s.get("alpha", 0) or 0 for s in leaders
            ) / len(leaders)

            # relative_strength: 龙头股平均相对强度
            relative_strength = sum(
                s.get("relative_strength", 0) or 0 for s in leaders
            ) / len(leaders)

            # volume_score: 量比（归一化到 0~100）
            avg_volume_ratio = sum(
                s.get("volume_ratio", 1) or 1 for s in leaders
            ) / len(leaders)
            volume_score_val = self.sigmoid_normalize(
                avg_volume_ratio, midpoint=1.5, steepness=2
            )

            # money_flow_score: 资金流向
            avg_money_flow = sum(
                s.get("money_flow", 0) or 0 for s in leaders
            ) / len(leaders)

            total_amount = sum(
                s.get("amount", 0) or 0 for s in leaders
            )
            money_flow_ratio = (
                avg_money_flow / total_amount if total_amount > 0 else 0
            )
            money_flow_score_val = self.normalize(money_flow_ratio, -0.1, 0.1)

            # institution_score: 机构持仓比例
            avg_institution = sum(
                s.get("institution_holding", 0) or 0 for s in leaders
            ) / len(leaders)
            institution_score_val = self.normalize(
                avg_institution, 0, 0.8
            )

            # macd_score: MACD 信号（>0 为正）
            avg_macd = sum(
                s.get("macd", 0) or 0 for s in leaders
            ) / len(leaders)
            macd_score_val = self.sigmoid_normalize(
                avg_macd, midpoint=0, steepness=2
            )

            # ma_trend_score: MA 趋势
            avg_ma_trend = sum(
                s.get("ma_trend", 0) or 0 for s in leaders
            ) / len(leaders)
            ma_trend_score_val = self.normalize(avg_ma_trend, -5, 5)
        else:
            leader_trend = 0.0
            leader_alpha = 0.0
            relative_strength = 0.0
            volume_score_val = 50.0
            money_flow_score_val = 50.0
            institution_score_val = 50.0
            macd_score_val = 50.0
            ma_trend_score_val = 50.0

        # ── 加权总分 ──────────────────────────────────────────
        sub_scores = {
            "leader_trend": self.normalize(leader_trend, -5, 5),
            "leader_alpha": self.normalize(leader_alpha, -3, 3),
            "relative_strength": self.normalize(relative_strength, -3, 3),
            "volume": volume_score_val,
            "money_flow": money_flow_score_val,
            "institution_score": institution_score_val,
            "macd": macd_score_val,
            "ma_trend": ma_trend_score_val,
        }

        leader_strength = 0.0
        total_weight = sum(weights.values())
        if total_weight > 0:
            for key, w in weights.items():
                leader_strength += sub_scores.get(key, 50.0) * w

        leader_strength = max(0.0, min(100.0, leader_strength))

        # ── 构建结果 ──────────────────────────────────────────
        leader_result = LeaderResult(
            theme_code=theme_code,
            trade_date=trade_date,
            leader_count=len(leaders),
            core_count=len(cores),
            follower_count=follower_count,
            leader_trend=leader_trend,
            leader_alpha=leader_alpha,
            relative_strength=relative_strength,
            volume_score=volume_score_val,
            money_flow_score=money_flow_score_val,
            institution_score=institution_score_val,
            macd_score=macd_score_val,
            ma_trend_score=ma_trend_score_val,
            leader_strength=leader_strength,
            leaders=[
                {"code": s.get("code", ""), "name": s.get("name", ""),
                 "pct_chg": s.get("pct_chg", 0)}
                for s in leaders
            ],
            cores=[
                {"code": s.get("code", ""), "name": s.get("name", ""),
                 "pct_chg": s.get("pct_chg", 0)}
                for s in cores
            ],
            details={"total_stocks": len(theme_stocks)},
        )

        contribution = leader_strength * total_weight / 100.0 if total_weight > 0 else 0.0

        return FactorResult(
            factor_name=self.name,
            version=self.version,
            score=leader_strength,
            weight=total_weight,
            contribution=contribution,
            details={"leader_result": leader_result.__dict__},
        )
