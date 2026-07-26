"""V3 Calculator — 机构动态轮动评分主计算器.

将8个一级因子、生命周期加分、共振乘数 综合计算最终评分。
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from theme_engine.score_v3.config import get_layer_weights, get_lifecycle_bonus, load_config
from theme_engine.score_v3.models import (
    EngineV3Result,
    ThemeV3Score,
    ResonanceResult,
)
from theme_engine.score_v3.signal_gen import describe_signal, generate_market_signal
from theme_engine.score_v3.predictor import predict_rotation_probability

logger = logging.getLogger(__name__)


class V3Calculator:
    """V3机构动态轮动评分主计算器."""

    def __init__(self) -> None:
        self._layer_weights: Dict[str, float] = {}

    async def calculate_single(
        self,
        theme_code: str,
        theme_name: str,
        trade_date: str,
        *,
        # 因子结果
        etf_trend: float = 0.0,
        etf_accel: float = 0.0,
        breadth: float = 0.0,
        leader: float = 0.0,
        leader_expand: float = 0.0,
        rank_momentum: float = 0.0,
        money: float = 0.0,
        # 详细结果 (可选)
        etf_trend_result=None,
        etf_accel_result=None,
        breadth_result=None,
        leader_result=None,
        leader_expand_result=None,
        rank_momentum_result=None,
        money_flow_result=None,
        lifecycle_result=None,
        resonance_result=None,
        transition_result=None,
        # 生命周期
        life_stage: str = "birth",
        lifecycle_bonus: float = 0.0,
        resonance_multiplier: float = 1.0,
        pre_rotate: bool = False,
        transition_direction: str = "",
        # 市场环境 (新增)
        market_regime: str = "",
        market_multiplier: float = 1.0,
        recommended_exposure: float = 1.0,
        # 元数据
        top_leaders: Optional[List[str]] = None,
        core_stocks: Optional[List[str]] = None,
        etf_code: str = "",
        etf_name: str = "",
        # 排名历史
        history_ranks: Optional[List[int]] = None,
        current_rank: int = 50,
    ) -> ThemeV3Score:
        """计算单个主题的V3完整评分.

        评分公式 (4层):
        Layer 1: base_score = Σ(因子分 × 权重)        — 主题自身强度
        Layer 2: intrinsic   = (base + lifecycle_bonus) × resonance_multiplier
        Layer 3: tradable    = intrinsic × market_multiplier × lifecycle_adj × resonance_adj
        Layer 4: signal      = f(tradable, market_regime, rank_momentum, life_stage)
        """
        await asyncio.sleep(0)

        self._load_weights()

        # ── Layer 1: 加权基础分 (纯因子分) ──
        base_score = self._calc_weighted_base(
            etf_trend=etf_trend,
            etf_accel=etf_accel,
            breadth=breadth,
            leader=leader,
            leader_expand=leader_expand,
            rank_momentum=rank_momentum,
            money=money,
        )

        # ── Layer 2: IntrinsicScore (主题自身强度) ──
        with_lifecycle = base_score + lifecycle_bonus
        intrinsic_score = with_lifecycle * resonance_multiplier
        intrinsic_score = max(0.0, min(100.0, intrinsic_score))

        # ── Layer 3: TradableScore (市场调整后) ──
        # 生命周期调整
        cfg = load_config()
        lc_adj_map = cfg.get("lifecycle_adjustment", {})
        lifecycle_adj = float(lc_adj_map.get(life_stage, 1.0))

        # 共振调整
        reso_key = f"{resonance_multiplier:.2f}"
        rs_adj_map = cfg.get("resonance_adjustment", {})
        resonance_adj = float(rs_adj_map.get(reso_key, 1.0))

        tradable_score = intrinsic_score * market_multiplier * lifecycle_adj * resonance_adj
        tradable_score = max(0.0, min(100.0, tradable_score))

        # ── Layer 4: 信号 (Market-aware + 迁移检测) ──
        signal = generate_market_signal(
            tradable_score=tradable_score,
            intrinsic_score=intrinsic_score,
            market_regime=market_regime,
            resonance_multiplier=resonance_multiplier,
            rank_momentum=rank_momentum,
            life_stage=life_stage,
            breadth=breadth,
            leader=leader,
            pre_rotate=pre_rotate,
            transition_direction=transition_direction,
        )

        # 预期收益 & 风险
        expected_return, risk = self._estimate_return_risk(
            etf_trend=etf_trend,
            etf_accel=etf_accel,
            breadth=breadth,
            leader=leader,
            life_stage=life_stage,
            resonance_multiplier=resonance_multiplier,
            tradable_score=tradable_score,
            market_regime=market_regime,
            theme_name=theme_name,
        )

        score = ThemeV3Score(
            theme_code=theme_code,
            theme_name=theme_name,
            trade_date=trade_date,
            etf_trend=round(etf_trend, 2),
            etf_accel=round(etf_accel, 2),
            breadth=round(breadth, 2),
            leader=round(leader, 2),
            leader_expand=round(leader_expand, 2),
            money=round(money, 2),
            rank_momentum=round(rank_momentum, 2),
            lifecycle_bonus=round(lifecycle_bonus, 2),
            resonance_multiplier=round(resonance_multiplier, 4),
            intrinsic_score=round(intrinsic_score, 2),
            tradable_score=round(tradable_score, 2),
            final_score=round(tradable_score, 2),
            life_stage=life_stage,
            transition_direction=transition_direction if pre_rotate else "",
            transition_strength=transition_result.strength if transition_result else 0.0,
            pre_rotate=pre_rotate,
            signal=signal,
            market_regime=market_regime,
            market_multiplier=round(market_multiplier, 4),
            recommended_exposure=round(recommended_exposure, 2),
            top_leaders=top_leaders or [],
            core_stocks=core_stocks or [],
            etf_code=etf_code,
            etf_name=etf_name,
            expected_return=expected_return,
            risk=risk,
            etf_trend_result=etf_trend_result,
            etf_accel_result=etf_accel_result,
            breadth_result=breadth_result,
            leader_result=leader_result,
            leader_expand_result=leader_expand_result,
            rank_momentum_result=rank_momentum_result,
            money_flow_result=money_flow_result,
            lifecycle_result=lifecycle_result,
            resonance_result=resonance_result,
            transition_result=transition_result,
        )

        return score

    async def rank_and_predict(
        self,
        themes: List[ThemeV3Score],
    ) -> List[ThemeV3Score]:
        """对主题列表排名并预测轮动概率.

        按 tradable_score 排序 (市场调整后分数).
        """
        sorted_themes = sorted(themes, key=lambda x: x.tradable_score, reverse=True)

        for i, t in enumerate(sorted_themes):
            t.rank = i + 1

        for t in sorted_themes:
            t.rotation_prob_5d = round(
                predict_rotation_probability(t, sorted_themes), 2
            )
            t.confidence = round(
                self._calc_confidence(t), 2
            )

        return sorted_themes

    def _calc_weighted_base(
        self,
        etf_trend: float = 0.0,
        etf_accel: float = 0.0,
        breadth: float = 0.0,
        leader: float = 0.0,
        leader_expand: float = 0.0,
        rank_momentum: float = 0.0,
        money: float = 0.0,
    ) -> float:
        """加权基础分."""
        scores = {
            "etf_trend": etf_trend,
            "etf_accel": etf_accel,
            "breadth": breadth,
            "leader": leader,
            "leader_expand": leader_expand,
            "money": money,
            "rank_momentum": rank_momentum,
        }

        total_weight = sum(self._layer_weights.values())
        if total_weight <= 0:
            return 0.0

        weighted = 0.0
        for key, weight in self._layer_weights.items():
            s = scores.get(key, 0.0)
            weighted += s * weight

        return weighted / total_weight

    def _load_weights(self) -> None:
        """加载权重."""
        self._layer_weights = get_layer_weights()

    # 防御型主题列表 (低弹性, 收益天花板低)
    _DEFENSIVE_THEMES = {"银行", "电力", "煤炭", "黄金", "公用事业", "红利", "高股息"}

    # 高弹性主题列表 (高Beta, 收益弹性大)
    _HIGH_BETA_THEMES = {"AI算力", "半导体", "机器人", "低空经济", "智能驾驶",
                         "信创", "游戏", "创新药", "商业航天", "新能源车"}

    def _estimate_return_risk(
        self,
        etf_trend: float,
        etf_accel: float,
        breadth: float,
        leader: float,
        life_stage: str,
        resonance_multiplier: float,
        *,
        tradable_score: float = 50.0,
        market_regime: str = "",
        theme_name: str = "",
    ) -> Tuple[str, str]:
        """估计预期收益和风险水平 (V2 — 动态调整版).

        考虑因素:
        - 基础: ETF趋势+加速度均值
        - 主题弹性: 防御型/高弹性分类
        - 市场状态: Weak/Risk-Off 抑制收益
        - 生命周期: decline/late 直接封顶
        """
        # ── 基础预期收益 (纯技术面) ──
        score = (etf_trend + etf_accel) / 2
        if score >= 75:
            base_ret = "高 (>15%)"
        elif score >= 60:
            base_ret = "中高 (8%~15%)"
        elif score >= 40:
            base_ret = "中 (3%~8%)"
        else:
            base_ret = "低 (<3%)"

        # ── 主题弹性判断 ──
        is_defensive = any(kw in theme_name for kw in self._DEFENSIVE_THEMES)
        is_high_beta = any(kw in theme_name for kw in self._HIGH_BETA_THEMES)

        # ── 市场状态修正 ──
        regime_down = market_regime in ("weak", "risk_off", "panic")
        regime_up = market_regime in ("risk_on",)

        # ── 预期收益调整矩阵 ──
        ret_order = ["低 (<3%)", "中 (3%~8%)", "中高 (8%~15%)", "高 (>15%)"]

        def _adjust_return(curr: str, direction: int) -> str:
            """方向: -1降级, +1升级, 0不变."""
            try:
                idx = ret_order.index(curr)
                idx = max(0, min(len(ret_order) - 1, idx + direction))
                return ret_order[idx]
            except ValueError:
                return curr

        exp_ret = base_ret

        # 防御型主题: 降1级 (收益天花板低)
        if is_defensive:
            exp_ret = _adjust_return(exp_ret, -1)

        # 弱市/避险: 降1级
        if regime_down:
            exp_ret = _adjust_return(exp_ret, -1)

        # 强市 + 高弹性: 升1级
        if regime_up and is_high_beta and base_ret in ("中高 (8%~15%)", "高 (>15%)"):
            exp_ret = _adjust_return(exp_ret, 1)

        # 衰退/末期: 无论分数多高, 封顶 "低"
        if life_stage in ("decline", "late"):
            exp_ret = "低 (<3%)"

        # ── 风险 (引入市场状态 + 主题弹性) ──
        if life_stage in ("decline", "late"):
            risk = "高"
        elif tradable_score < 25:
            risk = "高"
        elif tradable_score < 35:
            risk = "中高"
        elif regime_down and is_high_beta:
            risk = "高"  # 弱市中的高弹性主题 =高风险
        elif life_stage == "main_up" and not is_defensive:
            risk = "中"
        elif breadth < 20 or leader < 20:
            risk = "中高"
        else:
            risk = "低"

        return exp_ret, risk

    def _calc_confidence(self, theme: ThemeV3Score) -> float:
        """计算置信度 (0~100)."""
        # 分数越极端越自信
        score_conf = abs(theme.final_score - 50) * 2  # 0~100

        # 共振越强越自信
        reso_conf = theme.resonance_multiplier * 50  # 40~65

        # 数据充分性
        data_conf = 70.0
        if theme.breadth > 0 and theme.leader > 0:
            data_conf = 85.0

        return (score_conf * 0.4 + reso_conf * 0.3 + data_conf * 0.3)
