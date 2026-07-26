"""Signal Generator — 交易信号系统 (优化版).

STRONG_BUY / BUY / ROTATE_IN / PRE_ROTATE / WATCH / HOLD / REDUCE / EXIT

PRE_ROTATE: 主题生命周期迁移检测触发 — 提前轮动信号
  在主题尚未进入正式上升通道时就发出预警,
  解决"看见上涨才买入"的滞后性问题。
"""

from __future__ import annotations

import logging

from theme_engine.score_v3.config import get_threshold

logger = logging.getLogger(__name__)

# Regime 对信号的上限映射 (优化版: Weak允许ROTATE_IN, PRE_ROTATE不受限)
REGIME_SIGNAL_CAP = {
    "risk_on": "STRONG_BUY",
    "neutral": "BUY",
    "weak": "ROTATE_IN",
    "risk_off": "HOLD",
    "panic": "REDUCE",
    "": "WATCH",
}

# 不同市场状态下的阈值调整系数
REGIME_THRESHOLD_ADJ = {
    "risk_on": 1.00,     # 正常
    "neutral": 0.90,     # 略降
    "weak": 0.80,        # 降低20%
    "risk_off": 0.70,    # 降低30%
    "panic": 0.60,       # 降低40%
    "": 0.85,
}


def generate_signal(
    final_score: float,
    resonance_multiplier: float,
    rank_momentum: float,
    life_stage: str,
) -> str:
    """生成交易信号 (兼容旧接口)."""
    return generate_market_signal(
        tradable_score=final_score,
        intrinsic_score=final_score,
        market_regime="",
        resonance_multiplier=resonance_multiplier,
        rank_momentum=rank_momentum,
        life_stage=life_stage,
        breadth=0.0,
        leader=0.0,
    )


def generate_market_signal(
    tradable_score: float,
    intrinsic_score: float,
    market_regime: str,
    resonance_multiplier: float,
    rank_momentum: float,
    life_stage: str,
    breadth: float = 0.0,
    leader: float = 0.0,
    pre_rotate: bool = False,
    transition_direction: str = "",
) -> str:
    """生成 Market-aware 交易信号 (优化版).

    根据市场状态动态调整阈值:
    - Risk-On: 正常阈值
    - Neutral: 阈值降10%
    - Weak: 阈值降20% (避免全REDUCE)
    - Risk-Off: 阈值降30%
    - Panic: 阈值降40%

    PRE_ROTATE: 迁移检测触发的提前轮动信号, 不受市场上限限制.
    """
    signal_cap = REGIME_SIGNAL_CAP.get(market_regime, "WATCH")

    # 市场状态阈值调整
    adj = REGIME_THRESHOLD_ADJ.get(market_regime, 0.85)

    # 基础阈值 (经过市场调整)
    base_strong_buy = get_threshold("signal_strong_buy", 85) * adj
    base_buy = get_threshold("signal_buy", 70) * adj
    base_rotate_in = get_threshold("signal_rotate_in", 50) * adj
    base_watch = get_threshold("signal_watch", 55) * adj
    base_hold = get_threshold("signal_hold", 40) * adj
    base_reduce = get_threshold("signal_reduce", 25) * adj

    # ── PRE_ROTATE: 迁移检测提前轮动信号 (优先级最高, 不受市场上限) ──
    if pre_rotate and transition_direction in ("ACCELERATING", "RECOVERING", "BOTTOMING"):
        if tradable_score >= base_hold:
            return "PRE_ROTATE"

    # 生命周期加速调整 (衰退期信号降级)
    if life_stage == "decline":
        if tradable_score >= base_buy:
            return _cap_signal("HOLD", signal_cap)
        return _cap_signal("REDUCE", signal_cap)

    if life_stage == "late":
        if tradable_score >= base_buy:
            return _cap_signal("HOLD", signal_cap)

    # ── STRONG_BUY 条件 (放松: Weak市场不要求Risk-On) ──
    if tradable_score >= base_strong_buy:
        if (intrinsic_score >= 60 and breadth >= 50 and leader >= 60
                and resonance_multiplier >= 1.05):
            return "STRONG_BUY"

    # ── BUY 条件 ──
    if tradable_score >= base_buy:
        return _cap_signal("BUY", signal_cap)

    # ── ROTATE_IN 条件 (排名动量改善, 用 moderate 阈值避免弱市下全失效) ──
    rank_mom_th = get_threshold("rank_momentum_moderate", 40) * adj
    if rank_momentum >= rank_mom_th and tradable_score >= base_rotate_in:
        return _cap_signal("ROTATE_IN", signal_cap)

    # ── 分数阈值 (动态调整) ──
    if tradable_score >= base_watch:
        return _cap_signal("WATCH", signal_cap)
    if tradable_score >= base_hold:
        return _cap_signal("HOLD", signal_cap)
    if tradable_score >= base_reduce:
        return _cap_signal("REDUCE", signal_cap)

    return "EXIT"


def _cap_signal(signal: str, cap: str) -> str:
    """按市场状态上限截断信号 (PRE_ROTATE 不受限)."""
    if signal == "PRE_ROTATE":
        return signal
    signal_rank = ["EXIT", "REDUCE", "HOLD", "WATCH", "PRE_ROTATE", "ROTATE_IN", "BUY", "STRONG_BUY"]
    cap_rank = ["EXIT", "REDUCE", "HOLD", "WATCH", "PRE_ROTATE", "ROTATE_IN", "BUY", "STRONG_BUY"]

    try:
        sig_idx = signal_rank.index(signal)
        cap_idx = cap_rank.index(cap)
        return signal if sig_idx <= cap_idx else cap
    except ValueError:
        return signal


def describe_signal(signal: str) -> str:
    """信号中文描述."""
    desc = {
        "STRONG_BUY": "强烈买入 — Market+Theme+ETF+Leader四层共振，主线确立",
        "BUY": "买入 — 趋势向好，适合建仓",
        "ROTATE_IN": "轮动入场 — 排名快速上升，正在走强",
        "PRE_ROTATE": "提前轮动 — 生命周期迁移信号，即将进入上升通道",
        "WATCH": "观察中 — 评分中等，等待确认",
        "HOLD": "持有 — 评分尚可，维持仓位",
        "REDUCE": "减仓 — 评分下降，降低风险敞口",
        "EXIT": "离场 — 评分过低，清仓回避",
    }
    return desc.get(signal, signal)
