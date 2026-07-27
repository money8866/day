"""
ELD V2 基本面评分 (Earnings Quality Score)

对财务数据的多个维度进行评分，加权计算综合得分。
"""

from __future__ import annotations

from typing import Optional

from .config import EarningsScoreConfig
from .models import EarningsScoreResult, FinancialData
from .utils import threshold_score


# ──────────────────────────────────────────────
# 主评分函数
# ──────────────────────────────────────────────
def score_earnings(
    financial: FinancialData,
    config: Optional[EarningsScoreConfig] = None,
) -> EarningsScoreResult:
    """对单个股票的财务数据进行基本面评分。

    评分维度及权重由 config 控制，每个子维度通过 threshold_score 打分，
    累计连续改善/加速加分，最终加权求和后截断至 [0, 100]。

    Args:
        financial: 当前季度财务数据
        config: 基本面积分配置，默认使用全局配置

    Returns:
        基本面评分结果
    """
    # ── 初始化 ──────────────────────────────
    if config is None:
        from .config import get_config

        config = get_config().earnings

    logic: list[str] = []

    # ── 1. 各子维度评分 ──────────────────────

    # 1a. 营收增长评分
    revenue_growth_score = threshold_score(
        financial.revenue_yoy, config.revenue_growth_thresholds
    )
    logic.append(
        f"营收增长 {financial.revenue_yoy:.1f}% → {revenue_growth_score}分"
    )

    # 1b. 扣非净利润增长评分
    deducted_growth_score = threshold_score(
        financial.deducted_yoy, config.deducted_growth_thresholds
    )
    logic.append(
        f"扣非增长 {financial.deducted_yoy:.1f}% → {deducted_growth_score}分"
    )

    # 1c. 净利润增长评分（复用扣非阈值）
    net_profit_growth_score = threshold_score(
        financial.net_profit_yoy, config.deducted_growth_thresholds
    )
    logic.append(
        f"净利增长 {financial.net_profit_yoy:.1f}% → {net_profit_growth_score}分"
    )

    # 1d. 毛利率评分
    gross_margin_score = threshold_score(
        financial.gross_margin, config.gross_margin_thresholds
    )
    logic.append(
        f"毛利率 {financial.gross_margin:.1f}% → {gross_margin_score}分"
    )

    # 1e. ROE 评分
    roe_score = threshold_score(financial.roe, config.roe_thresholds)
    logic.append(f"ROE {financial.roe:.1f}% → {roe_score}分")

    # 1f. ROIC 评分
    roic_score = threshold_score(financial.roic, config.roic_thresholds)
    logic.append(f"ROIC {financial.roic:.1f}% → {roic_score}分")

    # 1g. 经营现金流评分（ocf_ratio）
    ocf_score = threshold_score(
        financial.ocf_ratio, config.ocf_ratio_thresholds
    )
    logic.append(
        f"经营现金流/净利润 {financial.ocf_ratio:.2f} → {ocf_score}分"
    )

    # 1h. 负债率评分（越低越好，所以用负债率本身去查表）
    debt_score = threshold_score(
        financial.debt_ratio, config.debt_ratio_thresholds
    )
    logic.append(
        f"资产负债率 {financial.debt_ratio:.1f}% → {debt_score}分"
    )

    # 1i. 主营业务占比评分
    main_biz_score = threshold_score(
        financial.main_biz_ratio, config.main_biz_ratio_thresholds
    )
    logic.append(
        f"主营业务占比 {financial.main_biz_ratio:.1f}% → {main_biz_score}分"
    )

    # ── 2. 连续改善/加速检测 ─────────────────
    consecutive_bonus: float = 0.0

    # 连续改善判断：营收、扣非、净利三者均正增长
    improve_count = 0
    if financial.revenue_yoy > 0:
        improve_count += 1
    if financial.deducted_yoy > 0:
        improve_count += 1
    if financial.net_profit_yoy > 0:
        improve_count += 1

    if improve_count >= 3:
        consecutive_bonus += config.consecutive_improve_bonus
        logic.append(
            f"营收/扣非/净利均正增长 → 连续改善奖励 +{config.consecutive_improve_bonus}分"
        )

    # 加速判断：扣非增长 > 营收增长（利润增速超过收入增速，说明利润率提升）
    if (
        financial.deducted_yoy > financial.revenue_yoy
        and financial.revenue_yoy > 0
    ):
        consecutive_bonus += config.consecutive_accelerate_bonus
        logic.append(
            f"扣非增长({financial.deducted_yoy:.1f}%) > 营收增长({financial.revenue_yoy:.1f}%) "
            f"→ 加速奖励 +{config.consecutive_accelerate_bonus}分"
        )

    # ── 3. 加权求和 ─────────────────────────
    raw_score = (
        revenue_growth_score * config.revenue_growth_weight
        + deducted_growth_score * config.deducted_profit_growth_weight
        + net_profit_growth_score * config.net_profit_growth_weight
        + gross_margin_score * config.gross_margin_weight
        + roe_score * config.roe_weight
        + roic_score * config.roic_weight
        + ocf_score * config.ocf_ratio_weight
        + debt_score * config.debt_ratio_weight
        + main_biz_score * config.main_biz_ratio_weight
        + consecutive_bonus
    )

    # ── 4. 截断至 [0, 100] ─────────────────
    final_score = max(0.0, min(100.0, raw_score))

    logic.append(
        f"加权得分={raw_score:.2f}, 加分={consecutive_bonus:.1f}, "
        f"最终={final_score:.2f}"
    )

    return EarningsScoreResult(
        score=round(final_score, 2),
        revenue_growth_score=revenue_growth_score,
        deducted_growth_score=deducted_growth_score,
        net_profit_growth_score=net_profit_growth_score,
        gross_margin_score=gross_margin_score,
        roe_score=roe_score,
        roic_score=roic_score,
        ocf_score=ocf_score,
        debt_score=debt_score,
        main_biz_score=main_biz_score,
        consecutive_bonus=round(consecutive_bonus, 2),
        logic=logic,
    )
