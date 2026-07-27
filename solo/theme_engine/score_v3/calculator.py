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
            etf_trend_result=etf_trend_result,
            theme_name=theme_name,
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

        排序逻辑 (V2 — 迁移优先级):
          1. 计算每个主题的 migration_priority (未来接力潜力)
          2. forward_score = tradable_score × 0.7 + migration_priority × 0.3
          3. 按 forward_score 降序排列
        """
        # 计算迁移优先级
        for t in themes:
            t.migration_priority = round(self._calc_migration_priority(t), 2)
            t.forward_score = round(
                t.tradable_score * 0.70 + t.migration_priority * 0.30, 2
            )

        # 按前瞻评分排序
        sorted_themes = sorted(themes, key=lambda x: x.forward_score, reverse=True)

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

    @staticmethod
    def _calc_migration_priority(theme: ThemeV3Score) -> float:
        """计算迁移优先级 — 主题轮动接力潜力评分.

        公式:
          MigrationScore = 0.40 × 迁移方向分
                         + 0.25 × 先锋分 (龙头健康度+资金共振+扩散确认+动量)
                         + 0.20 × 资金分 (资金共振)
                         + 0.10 × 龙头分 (龙头健康度)
                         + 0.05 × 市场共振 (resonance_multiplier)

        迁移方向分:
          Growth → MainUp: 100  (主升潜力最大)
          Birth  → Growth: 85   (启动初期)
          MainUp → Late:   55   (高位接力有限)
          Late   → Decline: 20  (衰退概率高)
          Decline→ Birth:  70   (筑底反弹)
          STABLE / 无迁移数据: 30
        """
        tr = theme.transition_result
        if not tr:
            return 30.0

        # 1. 迁移方向分 (40%)
        dir_score = {
            ("growth", "main_up"): 100,
            ("birth", "growth"): 85,
            ("decline", "birth"): 70,
            ("main_up", "late"): 55,
            ("late", "decline"): 20,
        }.get((tr.from_stage, tr.to_stage), 30)

        # 2. 先锋分 (25%) — 龙头健康度×0.35 + 资金共振×0.25 + 扩散确认×0.20 + 动量×0.20
        pioneer = (
            tr.leader_health_score * 0.35
            + tr.money_resonance_score * 0.25
            + tr.confirmation_score * 0.20
            + tr.momentum_score * 0.20
        )

        # 3. 资金分 (20%)
        money_score = tr.money_resonance_score

        # 4. 龙头分 (10%)
        leader_score = tr.leader_health_score

        # 5. 市场共振 (5%)
        market_resonance = theme.resonance_multiplier * 50

        # 综合
        migration = (
            dir_score * 0.40
            + pioneer * 0.25
            + money_score * 0.20
            + leader_score * 0.10
            + market_resonance * 0.05
        )

        return max(0.0, min(100.0, migration))

    def _calc_weighted_base(
        self,
        etf_trend: float = 0.0,
        etf_accel: float = 0.0,
        breadth: float = 0.0,
        leader: float = 0.0,
        leader_expand: float = 0.0,
        rank_momentum: float = 0.0,
        money: float = 0.0,
        etf_trend_result=None,
        theme_name: str = "",
    ) -> float:
        """加权基础分 (V2 — 动态因子调整).

        动态调整规则:
        1. 龙头健康度>60 → 从ETF趋势和排名动量转移权重给龙头
           (机构已在布局, 趋势滞后于龙头)
        2. ETF趋势方向弱(<40)但趋势质量高(>50) → 用质量分修正方向分
           (缩量整理/筹码沉淀主题, 避免被低方向分误判)
        3. 防御型主题(银行/电力/煤炭) → ETF趋势权重自动降低
           (防御型趋势强≠进攻能力强)
        """
        scores = {
            "etf_trend": etf_trend,
            "etf_accel": etf_accel,
            "breadth": breadth,
            "leader": leader,
            "leader_expand": leader_expand,
            "money": money,
            "rank_momentum": rank_momentum,
        }

        weights = dict(self._layer_weights)

        # 1. 动态龙头加权: 龙头>60 → 转移权重给龙头
        if leader > 60:
            boost = min(0.05, (leader - 60) / 40 * 0.05)
            etf_reduce = min(boost * 0.6, weights.get("etf_trend", 0.20) - 0.10)
            mom_reduce = min(boost * 0.4, weights.get("rank_momentum", 0.05) - 0.01)
            weights["leader"] = weights.get("leader", 0.20) + etf_reduce + mom_reduce
            weights["etf_trend"] = weights.get("etf_trend", 0.20) - etf_reduce
            weights["rank_momentum"] = weights.get("rank_momentum", 0.05) - mom_reduce

        # 2. ETF趋势质量调整: 方向弱但质量高 → 混合修正
        if etf_trend_result is not None:
            direction = getattr(etf_trend_result, 'trend_direction', 50.0)
            quality = getattr(etf_trend_result, 'trend_quality', 50.0)
            if direction < 40 and quality > 50:
                scores["etf_trend"] = round(direction * 0.40 + quality * 0.60, 1)

        # 3. 防御型主题: ETF趋势权重降权 (防御型趋势强≠进攻能力强)
        _DEFENSIVE = {"银行", "电力", "煤炭", "黄金", "公用事业", "红利"}
        if any(kw in theme_name for kw in _DEFENSIVE):
            if weights.get("etf_trend", 0.20) > 0.12:
                etf_cut = weights["etf_trend"] - 0.12
                weights["etf_trend"] = 0.12
                weights["money"] = weights.get("money", 0.09) + etf_cut * 0.6
                weights["leader"] = weights.get("leader", 0.20) + etf_cut * 0.4

        # 加权总分
        total_weight = sum(weights.values())
        if total_weight <= 0:
            return 0.0

        weighted = 0.0
        for key, weight in weights.items():
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
