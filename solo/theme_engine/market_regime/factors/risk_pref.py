"""MarketRiskPrefFactor — 风险偏好评分."""

from __future__ import annotations

import logging
from typing import Dict, List

from ..config import get_factor_weights, get_norm_range, load_config
from ..data import MarketDataFetcher
from ..models import MarketRiskPrefResult

logger = logging.getLogger(__name__)


def normalize(value: float, norm_range) -> float:
    """将值归一化到 0-100 区间."""
    lo, hi = norm_range
    if hi == lo:
        return 50.0
    clipped = max(lo, min(hi, value))
    return (clipped - lo) / (hi - lo) * 100.0


def _load_risk_pref_etfs() -> Dict[str, List[str]]:
    """从配置加载风险偏好ETF分组."""
    cfg = load_config()
    return cfg.get("risk_pref_etfs", {})


async def calc_market_risk_pref(
    fetcher: MarketDataFetcher, trade_date: str
) -> MarketRiskPrefResult:
    """计算风险偏好评分."""
    etf_groups = _load_risk_pref_etfs()
    if not etf_groups:
        logger.warning("无 risk_pref_etfs 配置，返回默认评分 0")
        return MarketRiskPrefResult(score=0.0, details={"error": "no config"})

    # 获取各分组 ETF 表现
    growth_codes = etf_groups.get("growth", [])
    defense_codes = etf_groups.get("defense", [])
    bank_codes = etf_groups.get("bank", [])
    tech_codes = etf_groups.get("tech", [])

    all_codes = list(
        set(growth_codes + defense_codes + bank_codes + tech_codes)
    )
    perf_map = await fetcher.get_etf_performance(all_codes, trade_date)

    def avg_perf(codes: List[str]) -> float:
        vals = [perf_map.get(c, 0.0) for c in codes if c in perf_map]
        if not vals:
            return 0.0
        return sum(vals) / len(vals)

    growth_perf = avg_perf(growth_codes)
    defense_perf = avg_perf(defense_codes)
    bank_perf = avg_perf(bank_codes)
    tech_perf = avg_perf(tech_codes)

    # ── 子因子计算 ──

    # 1) 成长/红利收益比（成长强 → 风险偏好高）
    growth_defense_ratio = (
        (1 + growth_perf / 100.0) / (1 + defense_perf / 100.0)
        if defense_perf > -100.0
        else 1.0
    )
    s_gd = normalize(
        growth_defense_ratio,
        get_norm_range("risk_pref", "growth_defense_ratio"),
    )

    # 2) 科技ETF相对强度（科技 / 银行）
    tech_rel_strength = (
        (1 + tech_perf / 100.0) / (1 + bank_perf / 100.0)
        if bank_perf > -100.0
        else 1.0
    )
    s_tech = normalize(
        tech_rel_strength, get_norm_range("risk_pref", "tech_rs")
    )

    # 3) 小盘成交占比（用 ETF perf 差近似）
    small_cap_ratio = (growth_perf - defense_perf) / 10.0  # 归一化到约 ±5
    s_small = normalize(
        small_cap_ratio, [-5.0, 5.0]
    )

    # 4) 高Beta表现（用 tech / defense 近似）
    high_beta_ratio = tech_rel_strength
    s_beta = normalize(
        high_beta_ratio, get_norm_range("risk_pref", "tech_rs")
    )

    weights = get_factor_weights("risk_pref")
    w_gd = weights.get("growth_defense_ratio_weight", 0.30)
    w_tech = weights.get("tech_rel_strength_weight", 0.25)
    w_small = weights.get("small_cap_ratio_weight", 0.20)
    w_beta = weights.get("high_beta_ratio_weight", 0.25)

    score = (
        s_gd * w_gd
        + s_tech * w_tech
        + s_small * w_small
        + s_beta * w_beta
    )

    return MarketRiskPrefResult(
        score=round(score, 2),
        growth_defense_ratio=round(growth_defense_ratio, 4),
        growth_etf_perf=round(growth_perf, 2),
        defense_etf_perf=round(defense_perf, 2),
        bank_etf_perf=round(bank_perf, 2),
        tech_etf_perf=round(tech_perf, 2),
        details={
            "sub_scores": {
                "growth_defense_ratio": round(s_gd, 2),
                "tech_rel_strength": round(s_tech, 2),
                "small_cap_ratio": round(s_small, 2),
                "high_beta_ratio": round(s_beta, 2),
            },
            "etf_performance": {
                "growth": round(growth_perf, 2),
                "defense": round(defense_perf, 2),
                "bank": round(bank_perf, 2),
                "tech": round(tech_perf, 2),
            },
        },
    )
