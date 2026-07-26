"""FlowFactor — 资金流评分因子.

综合 ETF 资金净流入、主题总成交额、龙头成交额、
成交额变化率、成交量变化率、资金流扩散度等指标，
评估主题的资金活跃度和参与度。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from theme_engine.factor.base import BaseFactor
from theme_engine.models.dataclasses import FactorResult, FlowResult

logger = logging.getLogger(__name__)


class FlowFactor(BaseFactor):
    """资金流评分因子."""

    name: str = "flow"
    version: str = "1.0.0"
    weight_key: str = "flow"

    async def calculate(
        self,
        theme_code: str,
        trade_date: str,
        **kwargs: Any,
    ) -> FactorResult:
        """计算资金流评分.

        kwargs 需要传入:
            theme_stocks: List[Dict[str, Any]] — 主题成分股列表
                每只股票含以下字段：
                - code: str          股票代码
                - name: str          股票名称
                - amount: float      成交额
                - money_flow: float  资金净流入
                - volume: float      成交量
                - pct_chg: float     涨幅%
            etf_net_flow: float       — ETF 资金净流入
            prev_amount: float        — 前一交易日主题总成交额
            prev_volume: float        — 前一交易日主题总成交量
            leader_codes: List[str]   — 龙头股代码列表
        """
        await asyncio.sleep(0)

        theme_stocks: List[Dict[str, Any]] = kwargs.get("theme_stocks", [])
        etf_net_flow: float = kwargs.get("etf_net_flow", 0) or 0
        prev_amount: float = kwargs.get("prev_amount", 0) or 0
        prev_volume: float = kwargs.get("prev_volume", 0) or 0
        leader_codes: List[str] = kwargs.get("leader_codes", [])

        weights = self.get_weights()

        if not theme_stocks:
            logger.info("主题 %s 无成分股数据，返回默认分 50", theme_code)
            default_result = FlowResult(
                theme_code=theme_code,
                trade_date=trade_date,
                flow_score=50.0,
            )
            return FactorResult(
                factor_name=self.name,
                version=self.version,
                score=50.0,
                weight=0.0,
                contribution=0.0,
                details={"flow_result": default_result.__dict__},
                error="成分股数据缺失，使用默认分",
            )

        # ── 主题总成交额 ──────────────────────────────────────
        theme_total_amount = sum(
            s.get("amount", 0) or 0 for s in theme_stocks
        )

        # ── 龙头成交额 ────────────────────────────────────────
        leader_amount = sum(
            s.get("amount", 0) or 0
            for s in theme_stocks
            if s.get("code", "") in leader_codes
        )

        # ── 成交额变化率 ─────────────────────────────────────
        amount_change_pct = (
            (theme_total_amount - prev_amount) / prev_amount
            if prev_amount > 0
            else 0.0
        )

        # ── 成交量变化率 ─────────────────────────────────────
        total_volume = sum(
            s.get("volume", 0) or 0 for s in theme_stocks
        )
        volume_change_pct = (
            (total_volume - prev_volume) / prev_volume
            if prev_volume > 0
            else 0.0
        )

        # ── 资金流扩散度 ─────────────────────────────────────
        # 衡量资金在各个股票间的分布均匀度
        money_flows = [
            s.get("money_flow", 0) or 0 for s in theme_stocks
        ]
        total_abs_flow = sum(abs(f) for f in money_flows)
        if total_abs_flow > 0 and len(money_flows) > 1:
            sorted_flows = sorted(money_flows, key=abs, reverse=True)
            top_n = max(1, len(sorted_flows) // 5)
            top_flow = sum(abs(f) for f in sorted_flows[:top_n])
            flow_diffusion = 1.0 - (top_flow / total_abs_flow)
        else:
            flow_diffusion = 0.5

        # ── 归一化到 0~100 ────────────────────────────────────
        etf_flow_score = self.normalize(etf_net_flow, -1e8, 1e8)
        theme_amount_score = self.normalize(
            theme_total_amount, 0, 1e10
        )
        leader_amount_score = self.normalize(leader_amount, 0, 1e9)
        amount_change_score = self.sigmoid_normalize(
            amount_change_pct, midpoint=0, steepness=3
        )
        volume_change_score = self.sigmoid_normalize(
            volume_change_pct, midpoint=0, steepness=3
        )
        flow_diffusion_score = flow_diffusion * 100.0

        # ── 加权总分 ──────────────────────────────────────────
        sub_scores = {
            "etf_flow": etf_flow_score,
            "theme_amount": theme_amount_score,
            "leader_amount": leader_amount_score,
            "amount_change": amount_change_score,
            "volume_change": volume_change_score,
            "flow_diffusion": flow_diffusion_score,
        }

        flow_score = 0.0
        total_weight = sum(weights.values())
        if total_weight > 0:
            for key, w in weights.items():
                flow_score += sub_scores.get(key, 50.0) * w

        flow_score = max(0.0, min(100.0, flow_score))

        # ── 构建结果 ──────────────────────────────────────────
        flow_result = FlowResult(
            theme_code=theme_code,
            trade_date=trade_date,
            etf_net_flow=etf_net_flow,
            theme_total_amount=theme_total_amount,
            leader_amount=leader_amount,
            amount_change_pct=amount_change_pct,
            volume_change_pct=volume_change_pct,
            flow_diffusion=flow_diffusion,
            flow_score=flow_score,
            details={"total_stocks": len(theme_stocks)},
        )

        contribution = flow_score * total_weight / 100.0 if total_weight > 0 else 0.0

        return FactorResult(
            factor_name=self.name,
            version=self.version,
            score=flow_score,
            weight=total_weight,
            contribution=contribution,
            details={"flow_result": flow_result.__dict__},
        )
