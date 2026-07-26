"""Theme Lifecycle Transition Detector V2 — 6因子评分 + 2修正项.

设计目标: 判断未来3~5个交易日最可能发生的阶段迁移,
结合资金与市场环境给出最优交易动作。

6因子评分:
  1. Proximity (25%): 距下一生命周期阈值还有多远
  2. Momentum (20%): 趋势加速度是否支持迁移方向
  3. Confirmation (15%): 是否由板块整体而非个股推动
  4. Money Resonance (15%): 成交额、主力资金、ETF资金是否同步改善
  5. Leader Health (15%): 龙头趋势、相对强度、创新高能力是否支持
  6. Regime Compatibility (10%): 当前市场风格是否有利于该主题

2修正项:
  - Age Penalty: 热点持续时间过长时降低迁移概率
  - Macro Filter: 对黄金、券商、周期、有色等宏观敏感主题修正

8种迁移方向: ACCELERATING/PEAKING/DECELERATING/DECLINING/BOTTOMING/RECOVERING/STABLE/STALLING
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

from theme_engine.score_v3.config import load_config

logger = logging.getLogger(__name__)

DIRECTION_MAP = {
    "ACCELERATING": "加速上行",
    "PEAKING": "见顶冲顶",
    "DECELERATING": "动能衰减",
    "DECLINING": "衰退下行",
    "BOTTOMING": "筑底企稳",
    "RECOVERING": "复苏回暖",
    "STABLE": "稳定运行",
    "STALLING": "低位停滞",
}

_STAGE_NEXT = {
    "birth": "growth",
    "growth": "main_up",
    "main_up": "late",
    "late": "decline",
    "decline": "birth",
}

# 宏观敏感主题映射
_MACRO_SENSITIVE = {
    "GOLD": ("黄金", "贵金属"),
    "SECURITIES": ("证券", "券商"),
    "CYCLICAL": ("煤炭", "化工", "有色金属", "钢铁", "建材", "地产"),
    "ENERGY": ("石油", "天然气"),
    "AGRICULTURE": ("农业", "猪肉"),
}

# 主题热点老化阈值 (days_since_birth)
_HOT_AGE_THRESHOLDS = {
    "birth": 15,     # 萌芽期超过15天未升级 → 可能死胎
    "growth": 30,     # 成长期超过30天未进入主升 → 动能衰减
    "main_up": 20,    # 主升浪超过20天 → 注意见顶风险
    "late": 10,       # 末期超过10天 → 加速衰退
    "decline": 40,    # 衰退期超过40天 → 可能筑底
}


def _proximity_score(
    current_stage: str,
    etf_trend: float,
    etf_accel: float,
    breadth: float,
    leader: float,
    thresholds: Dict[str, float],
) -> float:
    """距下一阶段阈值的距离分 (0~100)."""
    next_stage = _STAGE_NEXT.get(current_stage, "")

    if next_stage == "growth":
        target = thresholds.get("growth_etf_threshold", 45)
        return min(100.0, max(0.0, etf_trend / target * 100))

    elif next_stage == "main_up":
        main_etf = thresholds.get("main_up_etf_threshold", 60)
        main_accel = thresholds.get("main_up_accel_threshold", 50)
        main_breadth = thresholds.get("main_up_breadth_threshold", 30)
        main_leader = thresholds.get("main_up_leader_threshold", 40)
        scores = [
            min(100.0, max(0.0, etf_trend / main_etf * 100)),
            min(100.0, max(0.0, etf_accel / main_accel * 100)),
            min(100.0, max(0.0, breadth / main_breadth * 100)),
            min(100.0, max(0.0, leader / main_leader * 100)),
        ]
        return sum(scores) / len(scores)

    elif next_stage == "late":
        late_etf = thresholds.get("late_etf_threshold", 55)
        if etf_accel <= 0:
            return min(100.0, max(0.0, etf_trend / late_etf * 100))
        return max(0.0, (100 - etf_accel) / 100 * 50)

    elif next_stage == "decline":
        decline_etf = thresholds.get("decline_etf_threshold", 20)
        if etf_trend <= decline_etf:
            return 100.0
        return max(0.0, 100 - (etf_trend - decline_etf) / 30 * 100)

    elif next_stage == "birth":
        birth_etf = thresholds.get("birth_etf_threshold", 25)
        if etf_trend >= birth_etf:
            return 100.0
        return min(100.0, max(0.0, etf_trend / birth_etf * 100))

    return 50.0


def _momentum_score(current_stage: str, etf_accel: float) -> float:
    """加速度方向是否支持迁移 (0~100)."""
    if current_stage in ("decline", "birth"):
        return max(0.0, min(100.0, etf_accel * 1.5))
    elif current_stage in ("growth",):
        return max(0.0, min(100.0, etf_accel))
    elif current_stage in ("main_up", "late"):
        if etf_accel <= 0:
            return min(100.0, abs(etf_accel) * 1.5)
        return max(0.0, 100 - etf_accel)
    return 50.0


def _confirmation_score(
    current_stage: str,
    breadth: float,
    etf_trend: float,
) -> float:
    """扩散度/成交量确认分 (0~100)."""
    if current_stage in ("decline", "birth"):
        if breadth >= 30:
            return 80.0 + min(20.0, (breadth - 30) / 70 * 20)
        return max(0.0, breadth / 30 * 50 + 30.0)
    elif current_stage in ("growth",):
        if breadth >= 30:
            return 70.0 + min(30.0, (breadth - 30) / 70 * 30)
        return max(0.0, breadth / 30 * 50 + 20.0)
    elif current_stage in ("main_up", "late"):
        if breadth < 25:
            return 70.0 + min(30.0, (25 - breadth) / 25 * 30)
        return max(0.0, (100 - breadth) / 80 * 50 + 20.0)
    return 50.0


def _money_resonance_score(
    current_stage: str,
    money: float,
    leader_expand: float,
    etf_accel: float,
) -> float:
    """资金共振分 (0~100) — 成交额、主力资金、ETF资金是否同步改善.

    向上迁移: 资金流入 + 扩散扩大 → 高置信度
    向下迁移: 资金流出 + 扩散收窄 → 高置信度
    """
    if current_stage in ("decline", "birth"):
        if money >= 55 and leader_expand >= 15:
            return 85.0 + min(15.0, (money - 55) / 45 * 15)
        elif money >= 45 and etf_accel > 20:
            return 65.0 + min(20.0, (money - 45) / 10 * 10)
        return max(0.0, money / 50 * 50 + 10.0)
    elif current_stage in ("growth", "main_up"):
        if money >= 60 and leader_expand >= 20:
            return 90.0 + min(10.0, (money - 60) / 40 * 10)
        elif money >= 45:
            return 60.0 + min(30.0, (money - 45) / 15 * 15)
        return max(0.0, money / 50 * 50 + 10.0)
    elif current_stage in ("late",):
        if money < 35:
            return 70.0 + min(30.0, (35 - money) / 35 * 30)
        return max(0.0, (100 - money) / 80 * 50 + 20.0)
    return 50.0


def _leader_health_score(
    current_stage: str,
    leader: float,
    leader_expand: float,
    etf_accel: float,
) -> float:
    """龙头健康度分 (0~100) — 龙头趋势、相对强度、创新高能力是否支持.

    向上迁移: 龙头质量高 + 龙头扩散 + 加速度好 → 健康
    向下迁移: 龙头质量低 + 龙头收窄 → 不健康
    """
    if current_stage in ("decline", "birth"):
        if leader >= 60 and etf_accel > 30:
            return 80.0 + min(20.0, (leader - 60) / 40 * 20)
        elif leader >= 50:
            return 55.0 + min(25.0, (leader - 50) / 10 * 10)
        return max(0.0, leader / 50 * 50 + 10.0)
    elif current_stage in ("growth", "main_up"):
        if leader >= 60 and leader_expand >= 20:
            return 85.0 + min(15.0, (leader - 60) / 40 * 15)
        elif leader >= 50:
            return 55.0 + min(30.0, (leader - 50) / 10 * 10)
        return max(0.0, leader / 50 * 50 + 10.0)
    elif current_stage in ("late",):
        if leader < 45:
            return 70.0 + min(30.0, (45 - leader) / 45 * 30)
        return max(0.0, (100 - leader) / 80 * 50 + 20.0)
    return 50.0


def _regime_compat_score(
    current_stage: str,
    market_regime: str,
    theme_name: str,
) -> float:
    """市场适配分 (0~100) — 当前市场风格是否有利于该主题.

    Risk-On: 成长股、科技股、券商有利
    Weak/Risk-Off: 防御股、银行、红利有利
    Neutral: 全面中性
    """
    if market_regime in ("risk_on",):
        if current_stage in ("growth", "main_up"):
            return 80.0
        elif current_stage == "birth":
            return 65.0
        return 40.0
    elif market_regime in ("neutral",):
        return 70.0
    elif market_regime in ("weak",):
        if any(kw in theme_name for kw in ("银行", "电力", "煤炭", "消费", "红利")):
            return 75.0
        elif current_stage in ("decline", "late"):
            return 30.0
        return 50.0
    elif market_regime in ("risk_off", "panic"):
        if any(kw in theme_name for kw in ("银行", "电力", "黄金", "红利")):
            return 70.0
        return 25.0
    return 60.0


def _age_penalty(
    current_stage: str,
    days_in_stage: int,
    etf_accel: float,
) -> tuple[float, str]:
    """热点老化惩罚 — 持续时间过长时降低迁移概率.

    Returns:
        (penalty, reason) — penalty为负值, 0表示无惩罚
    """
    threshold = _HOT_AGE_THRESHOLDS.get(current_stage, 30)
    if days_in_stage <= 0:
        return 0.0, ""

    ratio = days_in_stage / threshold if threshold > 0 else 0

    if ratio < 0.5:
        return 0.0, ""
    elif ratio < 0.8:
        # 轻微老化
        penalty = -5.0 * ratio
        return round(penalty, 1), f"热点已持续{days_in_stage}天(阈值{threshold}天), 轻微老化"
    elif ratio < 1.2:
        # 中度老化
        penalty = -10.0 - 5.0 * (ratio - 0.8) / 0.4
        return round(penalty, 1), f"热点已持续{days_in_stage}天(阈值{threshold}天), 中度老化"
    else:
        # 严重老化 — 但如果有强加速度, 缓和一部分
        penalty = -15.0 - 5.0 * (ratio - 1.2) / 0.8
        if etf_accel > 50:
            penalty *= 0.5  # 强加速度减缓老化
        return round(penalty, 1), f"热点已持续{days_in_stage}天(阈值{threshold}天), 严重老化{' (加速度缓解)' if etf_accel > 50 else ''}"


def _macro_filter(
    theme_name: str,
    current_stage: str,
    market_regime: str,
    etf_trend: float,
    money: float,
) -> tuple[float, str]:
    """宏观过滤 — 对宏观敏感主题进行修正.

    Returns:
        (adjustment, reason) — 正值加分, 负值减分
    """
    macro_type = None
    for mtype, keywords in _MACRO_SENSITIVE.items():
        if any(kw in theme_name for kw in keywords):
            macro_type = mtype
            break

    if macro_type is None:
        return 0.0, ""

    if macro_type == "GOLD":
        if market_regime in ("risk_off", "panic"):
            if etf_trend > 50 and money > 50:
                return 8.0, "黄金: 避险情绪+资金流入共振"
            return 5.0, "黄金: 避险环境有利"
        elif market_regime in ("risk_on",):
            return -5.0, "黄金: Risk-On环境不利"
        return 0.0, ""

    elif macro_type == "SECURITIES":
        if market_regime in ("risk_on", "neutral"):
            if current_stage in ("growth", "main_up") and money > 55:
                return 10.0, "券商: 牛市环境+资金共振"
            return 5.0, "券商: 中性偏多环境"
        elif market_regime in ("risk_off", "panic"):
            return -8.0, "券商: 避险环境不利"
        return 0.0, ""

    elif macro_type == "CYCLICAL":
        if current_stage in ("growth", "main_up") and money > 55:
            return 5.0, "周期: 资金流入+趋势向上"
        elif market_regime in ("weak", "risk_off"):
            return -3.0, "周期: 弱市环境抑制"
        return 0.0, ""

    elif macro_type == "ENERGY":
        if money > 60 and etf_trend > 55:
            return 5.0, "能源: 资金+趋势共振"
        return 0.0, ""

    elif macro_type == "AGRICULTURE":
        return 0.0, ""

    return 0.0, ""


def _determine_direction_v2(
    current_stage: str,
    composite: float,
    etf_trend: float,
    etf_accel: float,
    breadth: float,
    money_resonance: float,
    leader_health: float,
) -> str:
    """综合判定迁移方向 (V2 — 6因子版)."""
    if current_stage == "birth":
        if composite >= 65:
            return "ACCELERATING"
        elif composite >= 40 and etf_accel > 30:
            return "RECOVERING"
        elif etf_trend < 15 and etf_accel < 10:
            return "STALLING"
        return "STABLE"

    elif current_stage == "growth":
        if composite >= 70:
            return "PEAKING"
        elif composite >= 50:
            return "ACCELERATING"
        return "STABLE"

    elif current_stage == "main_up":
        if etf_accel < 0 and money_resonance < 40:
            return "DECELERATING"
        elif composite >= 65:
            return "PEAKING"
        return "STABLE"

    elif current_stage == "late":
        if composite >= 60 and etf_accel < 0:
            return "DECLINING"
        elif composite >= 50:
            return "DECELERATING"
        return "STABLE"

    elif current_stage == "decline":
        if etf_accel > 20 and leader_health >= 50:
            return "BOTTOMING"
        elif etf_accel > 10 and money_resonance >= 45:
            return "RECOVERING"
        elif etf_trend < 10 and etf_accel < 5:
            return "DECLINING"
        return "STABLE"

    return "STABLE"


def _estimate_days_v2(
    current_stage: str,
    direction: str,
    composite: float,
    etf_accel: float,
    age_penalty: float,
) -> int:
    """估算距下一阶段的天数 (V2 — 含老化修正)."""
    if direction == "STABLE":
        return 0

    gap = max(5, 100 - composite)
    accel_factor = max(0.3, min(3.0, abs(etf_accel) / 30))
    base_days = gap / 10 * 5 / accel_factor

    # 老化修正: 越老化, 即使迁移也需要更长时间
    if age_penalty < -5:
        base_days *= 1.3

    if direction in ("ACCELERATING", "RECOVERING", "BOTTOMING"):
        return max(3, min(15, int(base_days)))
    elif direction in ("PEAKING", "DECELERATING", "DECLINING"):
        return max(2, min(10, int(base_days * 0.7)))
    return 5


def detect_transition(
    theme_code: str,
    theme_name: str,
    current_stage: str,
    etf_trend: float,
    etf_accel: float,
    breadth: float,
    leader: float,
    prev_stage: Optional[str] = None,
    money: float = 50.0,
    leader_expand: float = 0.0,
    market_regime: str = "",
    days_in_stage: int = 0,
) -> "TransitionResult":
    """执行生命周期迁移检测 V2.

    Args:
        theme_code: 主题代码
        theme_name: 主题名称 (用于宏观过滤)
        current_stage: 当前生命周期阶段
        etf_trend: ETF趋势分 0~100
        etf_accel: ETF加速度分 0~100
        breadth: 扩散度分 0~100
        leader: 龙头质量分 0~100
        prev_stage: 上一日阶段
        money: 资金流分 0~100 (截面标准化后)
        leader_expand: 龙头扩散分 0~100
        market_regime: 市场状态 (risk_on/neutral/weak/risk_off/panic)
        days_in_stage: 当前阶段已持续天数

    Returns:
        TransitionResult with 6-factor scores + corrections
    """
    from theme_engine.score_v3.models import TransitionResult

    cfg = load_config().get("lifecycle", {})
    thresholds = {
        "birth_etf_threshold": cfg.get("birth_etf_threshold", 25),
        "growth_etf_threshold": cfg.get("growth_etf_threshold", 45),
        "main_up_etf_threshold": cfg.get("main_up_etf_threshold", 60),
        "main_up_accel_threshold": cfg.get("main_up_accel_threshold", 50),
        "main_up_breadth_threshold": cfg.get("main_up_breadth_threshold", 30),
        "main_up_leader_threshold": cfg.get("main_up_leader_threshold", 40),
        "late_etf_threshold": cfg.get("late_etf_threshold", 55),
        "late_accel_threshold": cfg.get("late_accel_threshold", -20),
        "decline_etf_threshold": cfg.get("decline_etf_threshold", 20),
    }

    # ── 6因子评分 ──
    # 1. Proximity (25%)
    proximity = _proximity_score(
        current_stage, etf_trend, etf_accel, breadth, leader, thresholds,
    )

    # 2. Momentum (20%)
    momentum = _momentum_score(current_stage, etf_accel)

    # 3. Confirmation (15%)
    confirmation = _confirmation_score(current_stage, breadth, etf_trend)

    # 4. Money Resonance (15%)
    money_resonance = _money_resonance_score(
        current_stage, money, leader_expand, etf_accel,
    )

    # 5. Leader Health (15%)
    leader_health = _leader_health_score(
        current_stage, leader, leader_expand, etf_accel,
    )

    # 6. Regime Compatibility (10%)
    regime_compat = _regime_compat_score(current_stage, market_regime, theme_name)

    # ── 加权综合 ──
    weights = {
        "proximity": 0.25,
        "momentum": 0.20,
        "confirmation": 0.15,
        "money_resonance": 0.15,
        "leader_health": 0.15,
        "regime_compat": 0.10,
    }
    composite = (
        proximity * weights["proximity"]
        + momentum * weights["momentum"]
        + confirmation * weights["confirmation"]
        + money_resonance * weights["money_resonance"]
        + leader_health * weights["leader_health"]
        + regime_compat * weights["regime_compat"]
    )

    # ── 修正项 ──
    age_penalty, age_reason = _age_penalty(current_stage, days_in_stage, etf_accel)
    macro_adj, macro_reason = _macro_filter(
        theme_name, current_stage, market_regime, etf_trend, money,
    )

    composite_corrected = composite + age_penalty + macro_adj
    composite_corrected = max(0.0, min(100.0, composite_corrected))

    # ── 判定方向 ──
    direction = _determine_direction_v2(
        current_stage, composite_corrected, etf_trend, etf_accel,
        breadth, money_resonance, leader_health,
    )

    # ── 置信度 (6因子一致性) ──
    scores = [proximity, momentum, confirmation, money_resonance, leader_health, regime_compat]
    consistency = 1.0 - (max(scores) - min(scores)) / 100.0 * 0.4
    confidence = max(0.2, min(0.95, consistency * 0.6 + (composite_corrected / 100) * 0.4))

    # ── PRE_ROTATE V2 (6因子版本) ──
    pre_rotate = (
        composite_corrected >= 55
        and direction in ("ACCELERATING", "RECOVERING", "BOTTOMING", "PEAKING")
        and money_resonance >= 45
        and leader_health >= 45
    )

    # ── 预计天数 ──
    days_estimate = _estimate_days_v2(
        current_stage, direction, composite_corrected, etf_accel, age_penalty,
    )

    result = TransitionResult(
        direction=direction,
        direction_cn=DIRECTION_MAP.get(direction, "稳定"),
        strength=round(composite_corrected, 1),
        confidence=round(confidence, 2),
        from_stage=current_stage,
        to_stage=_STAGE_NEXT.get(current_stage, ""),
        days_estimate=days_estimate,
        pre_rotate=pre_rotate,
        proximity_score=round(proximity, 1),
        momentum_score=round(momentum, 1),
        confirmation_score=round(confirmation, 1),
        money_resonance_score=round(money_resonance, 1),
        leader_health_score=round(leader_health, 1),
        regime_compat_score=round(regime_compat, 1),
        age_penalty=round(age_penalty, 1),
        macro_filter=round(macro_adj, 1),
        age_penalty_reason=age_reason,
        macro_filter_reason=macro_reason,
        acceleration_trend=round(etf_accel, 1),
        breadth_trend=round(breadth, 1),
        details={
            "theme_code": theme_code,
            "theme_name": theme_name,
            "current_stage": current_stage,
            "prev_stage": prev_stage,
            "direction": direction,
            "direction_cn": DIRECTION_MAP.get(direction, "稳定"),
            "scores": {
                "proximity": round(proximity, 1),
                "momentum": round(momentum, 1),
                "confirmation": round(confirmation, 1),
                "money_resonance": round(money_resonance, 1),
                "leader_health": round(leader_health, 1),
                "regime_compat": round(regime_compat, 1),
            },
            "weights": weights,
            "composite": round(composite, 1),
            "composite_corrected": round(composite_corrected, 1),
            "age_penalty": round(age_penalty, 1),
            "age_penalty_reason": age_reason,
            "macro_filter": round(macro_adj, 1),
            "macro_filter_reason": macro_reason,
            "pre_rotate": pre_rotate,
            "days_estimate": days_estimate,
            "days_in_stage": days_in_stage,
        },
    )

    if pre_rotate:
        logger.info(
            "PRE_ROTATE(V2): %s(%s) 当前=%s → 目标=%s, 方向=%s, 强度=%.0f, 置信度=%.0f%%, 预计%d天 | "
            "6因子: P=%.0f M=%.0f C=%.0f $=%.0f L=%.0f R=%.0f | 修正: 老化=%.0f 宏观=%.0f",
            theme_name, theme_code, current_stage, result.to_stage,
            result.direction_cn, composite_corrected, confidence * 100, days_estimate,
            proximity, momentum, confirmation, money_resonance, leader_health, regime_compat,
            age_penalty, macro_adj,
        )

    return result