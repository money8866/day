"""
机构吸筹检测引擎 — Institution Accumulation Engine

识别公告后机构资金持续建仓行为。

核心逻辑：
1. 资金趋势（40%）：5日/10日/20日净大单流入趋势
2. 量价结构（30%）：成交额趋势、上涨放量、下跌缩量、换手率变化
3. 筹码变化（30%）：平均成本变化、获利盘变化、成本集中度变化

机构状态：
- 吸筹(ACCUMULATION)：资金持续流入，量能温和，筹码集中
- 洗盘(WASHING)：资金波动，量能萎缩，成本震荡
- 启动(LAUNCH)：资金加速流入，放量突破，获利盘增加
- 加速(ACCELERATE)：资金大幅流入，量能放大，成本上升
- 派发(DISTRIBUTE)：资金流出，放量滞涨，集中度下降
"""

from __future__ import annotations

import logging
import statistics
from typing import Any, Optional

from .config import get_config, InstitutionAccumulationConfig
from .constants import InstitutionState
from .models import InstitutionAccumulationResult, MoneyFlowData, CyqData

logger = logging.getLogger(__name__)


def _score_fund_flow(
    moneyflow_list: list[MoneyFlowData],
    cfg: InstitutionAccumulationConfig,
) -> tuple[float, float, float, float]:
    """评估资金趋势。

    计算各期限净大单流入比例并评分。

    Args:
        moneyflow_list: 按日期降序排列的资金流向数据。
        cfg: 机构吸筹配置。

    Returns:
        (综合分, 短期分, 中期分, 长期分)
    """
    logic: list[str] = []

    if not moneyflow_list:
        return 0.0, 0.0, 0.0, 0.0

    def _net_ratio(days: int) -> float:
        subset = moneyflow_list[:days]
        if not subset:
            return 0.0
        total_buy = sum(m.buy_lg_amount for m in subset)
        total_sell = sum(m.sell_lg_amount for m in subset)
        total = total_buy + total_sell
        return (total_buy - total_sell) / total if total > 0 else 0.0

    def _ratio_to_score(ratio: float) -> float:
        if ratio > 0.15:
            return 100.0
        elif ratio > 0.10:
            return 80.0
        elif ratio > 0.05:
            return 60.0
        elif ratio > 0.0:
            return 40.0
        elif ratio > -0.05:
            return 20.0
        else:
            return 0.0

    short_ratio = _net_ratio(cfg.short_term_days)
    mid_ratio = _net_ratio(cfg.mid_term_days)
    long_ratio = _net_ratio(cfg.long_term_days)

    short_score = _ratio_to_score(short_ratio)
    mid_score = _ratio_to_score(mid_ratio)
    long_score = _ratio_to_score(long_ratio)

    logic.append(f"近{cfg.short_term_days}日净流入比: {short_ratio:+.2%} → {short_score}分")
    logic.append(f"近{cfg.mid_term_days}日净流入比: {mid_ratio:+.2%} → {mid_score}分")
    logic.append(f"近{cfg.long_term_days}日净流入比: {long_ratio:+.2%} → {long_score}分")

    # 加权综合：近5/10/20日权重递减
    fund_score = short_score * 0.40 + mid_score * 0.35 + long_score * 0.25
    logic.append(f"资金趋势综合分: {fund_score:.1f}分")

    return fund_score, short_score, mid_score, long_score


def _score_volume_price(
    daily_data: list,
    cfg: InstitutionAccumulationConfig,
) -> tuple[float, list[str]]:
    """评估量价结构。

    Args:
        daily_data: 日线数据（从旧到新排序）。
        cfg: 机构吸筹配置。

    Returns:
        (综合分, 逻辑说明列表)
    """
    logic: list[str] = []
    if len(daily_data) < cfg.volume_trend_days + 5:
        return 50.0, ["日线数据不足，量价评分为中性"]

    # 取最近N天
    recent = daily_data[-cfg.volume_trend_days:]

    # 1. 成交额趋势
    amounts = [d.amount for d in recent]
    half = len(amounts) // 2
    recent_avg = sum(amounts[half:]) / len(amounts[half:]) if amounts[half:] else 0
    earlier_avg = sum(amounts[:half]) / len(amounts[:half]) if amounts[:half] else 0
    amount_trend = (recent_avg / earlier_avg - 1.0) if earlier_avg > 0 else 0.0

    # 2. 上涨放量 / 下跌缩量
    up_days = [d for d in recent if d.pct_change > 0]
    down_days = [d for d in recent if d.pct_change < 0]

    up_vol_ratio = 0.0
    if up_days:
        up_vol_avg = sum(d.vol for d in up_days) / len(up_days)
        total_vol_avg = sum(d.vol for d in recent) / len(recent)
        up_vol_ratio = up_vol_avg / total_vol_avg if total_vol_avg > 0 else 1.0

    down_vol_ratio = 1.0
    if down_days:
        down_vol_avg = sum(d.vol for d in down_days) / len(down_days)
        total_vol_avg = sum(d.vol for d in recent) / len(recent)
        down_vol_ratio = down_vol_avg / total_vol_avg if total_vol_avg > 0 else 1.0

    # 3. 评分
    score = 50.0

    # 成交额趋势加分
    if amount_trend > 0.3:
        score += 20.0
        logic.append(f"成交额明显增长({amount_trend:+.1%})")
    elif amount_trend > 0.1:
        score += 10.0
        logic.append(f"成交额温和增长({amount_trend:+.1%})")
    elif amount_trend < -0.1:
        score -= 10.0
        logic.append(f"成交额萎缩({amount_trend:+.1%})")

    # 上涨放量加分
    if up_vol_ratio > cfg.up_volume_threshold:
        score += 15.0
        logic.append(f"上涨日放量(量比{up_vol_ratio:.2f})")
    else:
        logic.append(f"上涨日量比{up_vol_ratio:.2f}")

    # 下跌缩量加分
    if down_vol_ratio < cfg.down_shrink_threshold:
        score += 15.0
        logic.append(f"下跌日缩量(量比{down_vol_ratio:.2f})")
    else:
        logic.append(f"下跌日量比{down_vol_ratio:.2f}")

    score = max(0.0, min(100.0, score))
    logic.append(f"量价结构分: {score:.1f}分")

    return score, logic


def _score_chip_change(
    cyq_data: Optional[CyqData],
    daily_data: list,
    cfg: InstitutionAccumulationConfig,
) -> tuple[float, float, float, float, list[str]]:
    """评估筹码变化。

    比较当前与N天前的筹码分布变化。

    Args:
        cyq_data: 当前筹码数据。
        daily_data: 日线数据。
        cfg: 机构吸筹配置。

    Returns:
        (综合分, 集中度变化, 平均成本变化, 获利盘变化, 逻辑说明列表)
    """
    logic: list[str] = []
    if cyq_data is None:
        return 50.0, 0.0, 0.0, 0.0, ["无筹码数据，筹码评分为中性"]

    # 由于筹码数据只有最新值，我们用价格变化和量能来估算筹码趋势
    # 成本变化 = 当前价 / 平均成本
    if cyq_data.avg_cost > 0 and daily_data:
        current_price = daily_data[-1].close
        cost_diff = (current_price - cyq_data.avg_cost) / cyq_data.avg_cost * 100.0
    else:
        cost_diff = 0.0

    score = 50.0

    # 成本变化评分
    if cost_diff > 5.0:
        score += 15.0  # 价格高于成本，有获利空间
        logic.append(f"价格高于平均成本{cost_diff:.1f}%")
    elif cost_diff > 0:
        score += 5.0
        logic.append(f"价格略高于成本{cost_diff:.1f}%")
    elif cost_diff > -5.0:
        score -= 5.0  # 轻微亏损
        logic.append(f"价格略低于成本{cost_diff:.1f}%")
    else:
        score -= 15.0  # 大幅亏损
        logic.append(f"价格大幅低于成本{cost_diff:.1f}%")

    # 获利盘比例评分
    profit_ratio = cyq_data.profit_ratio
    if 0.4 <= profit_ratio <= 0.7:
        score += 15.0  # 适中获利盘
        logic.append(f"获利盘适中({profit_ratio:.1%})")
    elif profit_ratio > 0.8:
        score -= 10.0  # 获利盘过多，抛压风险
        logic.append(f"获利盘过多({profit_ratio:.1%})")
    elif profit_ratio < 0.2:
        score -= 10.0  # 获利盘过少
        logic.append(f"获利盘过少({profit_ratio:.1%})")
    else:
        logic.append(f"获利盘{profit_ratio:.1%}")

    # 集中度评分（concentration越小越集中）
    concentration = cyq_data.cost_concentration
    if concentration < 0.2:
        score += 15.0
        logic.append(f"筹码高度集中({concentration:.2f})")
    elif concentration < 0.35:
        score += 5.0
        logic.append(f"筹码相对集中({concentration:.2f})")
    elif concentration > 0.5:
        score -= 10.0
        logic.append(f"筹码分散({concentration:.2f})")
    else:
        logic.append(f"筹码集中度{concentration:.2f}")

    score = max(0.0, min(100.0, score))
    logic.append(f"筹码变化分: {score:.1f}分")

    return score, concentration, cost_diff, profit_ratio, logic


def _classify_state(
    fund_score: float,
    volume_price_score: float,
    chip_score: float,
    cfg: InstitutionAccumulationConfig,
) -> tuple[InstitutionState, float, list[str]]:
    """分类机构状态。

    Args:
        fund_score: 资金趋势分。
        volume_price_score: 量价结构分。
        chip_score: 筹码变化分。
        cfg: 机构吸筹配置。

    Returns:
        (状态枚举, 综合分, 逻辑说明列表)
    """
    logic: list[str] = []
    total = fund_score * 0.40 + volume_price_score * 0.30 + chip_score * 0.30

    if total >= cfg.accelerate_score_threshold:
        state = InstitutionState.ACCELERATE
        desc = "加速上涨"
    elif total >= cfg.launch_score_threshold:
        state = InstitutionState.LAUNCH
        desc = "启动拉升"
    elif total >= cfg.accumulation_score_threshold:
        state = InstitutionState.ACCUMULATION
        desc = "底部吸筹"
    elif total >= cfg.wash_score_threshold:
        state = InstitutionState.WASHING
        desc = "洗盘整理"
    elif total >= cfg.distribute_score_threshold:
        state = InstitutionState.DISTRIBUTE
        desc = "高位派发"
    else:
        state = InstitutionState.UNKNOWN
        desc = "信号不明"

    logic.append(
        f"综合评分{total:.1f}分 → {desc}({state.value})"
    )

    return state, total, logic


def calc_institution_accumulation(
    ts_code: str,
    data_source: Any,
) -> InstitutionAccumulationResult:
    """计算机构吸筹状态和评分。

    Args:
        ts_code: 股票代码。
        data_source: 数据源（需提供 get_moneyflow, get_daily_data, get_cyq 接口）。

    Returns:
        InstitutionAccumulationResult: 机构吸筹检测结果。
    """
    cfg = get_config().institution_accumulation
    all_logic: list[str] = []

    # 1. 获取数据
    moneyflow = data_source.get_moneyflow(ts_code) if hasattr(data_source, "get_moneyflow") else []
    daily_data = data_source.get_daily_data(ts_code, "20200101", "20261231") if hasattr(data_source, "get_daily_data") else []
    cyq = data_source.get_cyq(ts_code) if hasattr(data_source, "get_cyq") else None

    # 按日期升序排列
    if daily_data:
        daily_data.sort(key=lambda x: x.trade_date)

    # 按日期降序排列（moneyflow）
    if moneyflow:
        moneyflow.sort(key=lambda x: x.trade_date, reverse=True)

    # 2. 资金趋势评分
    fund_score, short_s, mid_s, long_s = _score_fund_flow(moneyflow, cfg)
    all_logic.append(f"资金趋势评分: {fund_score:.1f}/100")

    # 3. 量价结构评分
    vol_price_score, vp_logic = _score_volume_price(daily_data, cfg)
    all_logic.extend(vp_logic)

    # 4. 筹码变化评分
    chip_score, concentration, cost_diff, profit_ratio, chip_logic = _score_chip_change(cyq, daily_data, cfg)
    all_logic.extend(chip_logic)

    # 5. 状态分类
    state, total_score, state_logic = _classify_state(
        fund_score, vol_price_score, chip_score, cfg,
    )
    all_logic.extend(state_logic)

    return InstitutionAccumulationResult(
        score=round(total_score, 2),
        state=state,
        fund_flow_score=round(fund_score, 2),
        volume_price_score=round(vol_price_score, 2),
        chip_change_score=round(chip_score, 2),
        short_term_flow_ratio=round(short_s, 2),
        mid_term_flow_ratio=round(mid_s, 2),
        long_term_flow_ratio=round(long_s, 2),
        volume_trend_score=round(vol_price_score, 2),
        concentration_change=round(concentration, 4),
        avg_cost_change=round(cost_diff, 2),
        profit_ratio_change=round(profit_ratio, 4),
        logic=all_logic,
    )
