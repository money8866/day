"""
仓位暴露模型 V4 - Portfolio Exposure Model V4

V4 升级要点：
1. Market Score → 基础仓位（Base Exposure），分段线性插值
2. Regime → 仓位下限（Floor）和上限（Cap），避免 Recovery 空仓
3. Risk Appetite → 乘数（0.6～1.3），替代加减法
4. Market Heat → 乘数调整（±10%～20%），参考热度等级
5. 最终结果限制在 Regime 仓位区间内
"""

import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import yaml

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yaml')


@dataclass
class ExposureResult:
    """仓位暴露建议结果"""
    portfolio_exposure: float        # 0.0-1.0
    portfolio_exposure_pct: float    # 0-100%
    raw_exposure: float              # 未限幅前的原始暴露
    base_exposure: float             # Market Score 决定的基础仓位
    theme_count_min: int
    theme_count_max: int
    etf_allocation: float
    leader_allocation: float
    follower_allocation: float
    cash_allocation: float
    risk_appetite_multiplier: float  # 风险偏好乘数
    heat_multiplier: float           # 热度乘数
    regime_floor: float              # Regime 下限
    regime_cap: float                # Regime 上限
    explain: Dict[str, str]


# ── 预设热度乘数（与 heat_engine config.yaml 同步） ──
_HEAT_MULT_MAP = {
    "Extreme Hot": 0.85,
    "Very Hot":    0.95,
    "Hot":         1.00,
    "Warm":        1.00,
    "Normal":      1.05,
    "Cool":        0.90,
    "Cold":        0.90,
    "Ice":         0.90,
}


class ExposureModel:
    """仓位暴露模型 V4

    V4 计算管线：
      base = f(market_score)            # 分段线性插值
      raw  = base × risk_mult × heat_mult
      result = clamp(raw, floor, cap)   # Regime 区间限制
    """

    def __init__(self, config: dict):
        cfg = config.get('exposure', {})

        # ---- 基础仓位层级 ----
        model_cfg = cfg.get('model', {})
        self.levels: List[dict] = model_cfg.get('levels', [])

        # ---- V4: 风险偏好乘数 ----
        ra_cfg = model_cfg.get('risk_appetite_multiplier', {})
        self.risk_min_mult = ra_cfg.get('min_multiplier', 0.6)
        self.risk_max_mult = ra_cfg.get('max_multiplier', 1.3)
        self.risk_score_min = ra_cfg.get('score_min', 0)
        self.risk_score_max = ra_cfg.get('score_max', 100)

        # ---- V4: Regime 仓位区间 ----
        self.regime_limits: Dict[str, dict] = model_cfg.get('regime_limits', {})

        # ---- V4: 热度调整开关 ----
        heat_cfg = model_cfg.get('heat_adjustment', {})
        self.heat_enabled = heat_cfg.get('enabled', True)

        # ---- 主题数量层级 ----
        theme_cfg = cfg.get('theme_count', {})
        self.theme_levels: List[dict] = theme_cfg.get('levels', [])

        # ---- ETF/龙头/跟风/现金 配置 ----
        alloc_cfg = cfg.get('etf_allocation', {})
        self.regime_allocations: Dict[str, dict] = alloc_cfg.get('regimes', {})

    # ──────────────────────────────────────────────
    # 分段线性插值
    # ──────────────────────────────────────────────

    def _interpolate_exposure(self, score: float, levels: List[dict]) -> float:
        if not levels:
            return 0.0
        for level in levels:
            smin = level['score_min']
            smax = level['score_max']
            if smin <= score < smax:
                ratio = (score - smin) / (smax - smin)
                emin = level.get('exposure_min', 0.0)
                emax = level.get('exposure_max', 0.0)
                return emin + ratio * (emax - emin)
        last = levels[-1]
        if score >= last['score_max']:
            return last.get('exposure_max', 0.0)
        return levels[0].get('exposure_min', 0.0)

    # ──────────────────────────────────────────────
    # V4: 风险偏好乘数
    # ──────────────────────────────────────────────

    def _calc_risk_multiplier(self, risk_score: float) -> float:
        """
        将 risk_score 线性映射到 [min_mult, max_mult]
        低分(<30) → 0.6~0.8，中等 → 0.8~1.0，高分(>70) → 1.0~1.3
        """
        if self.risk_score_max <= self.risk_score_min:
            return 1.0
        ratio = (risk_score - self.risk_score_min) / (self.risk_score_max - self.risk_score_min)
        ratio = max(0.0, min(1.0, ratio))
        return self.risk_min_mult + ratio * (self.risk_max_mult - self.risk_min_mult)

    # ──────────────────────────────────────────────
    # V4: 热度乘数
    # ──────────────────────────────────────────────

    def _lookup_heat_multiplier(self, heat_level: str) -> float:
        """按热度等级查找乘数"""
        return _HEAT_MULT_MAP.get(heat_level, 1.0)

    # ──────────────────────────────────────────────
    # V4: Regime 仓位区间
    # ──────────────────────────────────────────────

    def _get_regime_limits(self, regime_name: str) -> Tuple[float, float]:
        limits = self.regime_limits.get(regime_name, {})
        return (limits.get('floor', 0.0), limits.get('cap', 1.0))

    # ──────────────────────────────────────────────
    # 主题数量
    # ──────────────────────────────────────────────

    def _get_theme_count(self, score: float) -> tuple:
        if not self.theme_levels:
            return (1, 2)
        for level in self.theme_levels:
            smin = level['score_min']
            smax = level['score_max']
            if smin <= score < smax:
                return (level['min_themes'], level['max_themes'])
        last = self.theme_levels[-1]
        if score >= last['score_max']:
            return (last['max_themes'], last['max_themes'])
        first = self.theme_levels[0]
        return (first['min_themes'], first['max_themes'])

    # ──────────────────────────────────────────────
    # V4 主接口
    # ──────────────────────────────────────────────

    def calculate(self, market_score: float, risk_appetite_score: float,
                  regime_name: str, heat_score: float = None,
                  heat_level: str = None) -> ExposureResult:
        """
        V4 仓位计算管线

        Args:
            market_score: 市场综合评分（0-100）
            risk_appetite_score: 风险偏好评分（0-100）
            regime_name: 市场状态名称
            heat_score: 热度评分（0-100），仅用于说明
            heat_level: 热度等级，用于查找乘数
        """
        explain: Dict[str, str] = {}

        # ── Step 1: 基础仓位 ──
        base_exposure = self._interpolate_exposure(market_score, self.levels)
        explain['base'] = (
            f"Market Score {market_score:.1f} → Base {base_exposure:.2%}"
        )

        # ── Step 2: 风险偏好乘数 ──
        risk_mult = self._calc_risk_multiplier(risk_appetite_score)
        explain['risk'] = (
            f"Risk Appetite {risk_appetite_score:.1f} → ×{risk_mult:.2f}"
        )

        # ── Step 3: 热度乘数 ──
        heat_mult = 1.0
        if self.heat_enabled and heat_level:
            heat_mult = self._lookup_heat_multiplier(heat_level)
            hs = f"{heat_level}"
            if heat_score is not None:
                hs += f"({heat_score:.0f})"
            explain['heat'] = (
                f"Heat {hs} → ×{heat_mult:.2f}"
            )
        else:
            explain['heat'] = "Heat adjustment disabled → ×1.00"

        # ── Step 4: 合成 ──
        raw_exposure = base_exposure * risk_mult * heat_mult
        explain['combined'] = (
            f"{base_exposure:.2%} × {risk_mult:.2f} × {heat_mult:.2f} = {raw_exposure:.2%}"
        )

        # ── Step 5: Regime 限幅 ──
        floor, cap = self._get_regime_limits(regime_name)
        portfolio_exposure = max(floor, min(cap, raw_exposure))
        explain['clamp'] = (
            f"Regime [{regime_name}] floor={floor:.0%} cap={cap:.0%} → {portfolio_exposure:.2%}"
        )

        # ── 主题数量 ──
        theme_min, theme_max = self._get_theme_count(market_score)
        explain['theme_count'] = (
            f"Market Score {market_score:.1f} → {theme_min}~{theme_max}个主题"
        )

        # ── ETF/龙头/跟风/现金 配置 ──
        default_alloc = {'etf': 0.30, 'leader': 0.35, 'follower': 0.10, 'cash': 0.25}
        alloc = self.regime_allocations.get(regime_name, default_alloc)

        etf_alloc = alloc.get('etf', default_alloc['etf'])
        leader_alloc = alloc.get('leader', default_alloc['leader'])
        follower_alloc = alloc.get('follower', default_alloc['follower'])
        cash_alloc = alloc.get('cash', default_alloc['cash'])

        explain['allocation'] = (
            f"Regime [{regime_name}] → "
            f"ETF {etf_alloc:.0%} / Leader {leader_alloc:.0%} / "
            f"Follower {follower_alloc:.0%} / Cash {cash_alloc:.0%}"
        )

        # 按总仓位缩放实际配置
        effective_etf = portfolio_exposure * etf_alloc
        effective_leader = portfolio_exposure * leader_alloc
        effective_follower = portfolio_exposure * follower_alloc
        effective_cash = 1.0 - effective_etf - effective_leader - effective_follower
        effective_cash = max(0.0, effective_cash)

        explain['effective'] = (
            f"Total {portfolio_exposure:.0%} → "
            f"ETF {effective_etf:.1%} / Leader {effective_leader:.1%} / "
            f"Follower {effective_follower:.1%} / Cash {effective_cash:.1%}"
        )

        return ExposureResult(
            portfolio_exposure=round(portfolio_exposure, 4),
            portfolio_exposure_pct=round(portfolio_exposure * 100.0, 2),
            raw_exposure=round(raw_exposure, 4),
            base_exposure=round(base_exposure, 4),
            theme_count_min=theme_min,
            theme_count_max=theme_max,
            etf_allocation=round(effective_etf, 4),
            leader_allocation=round(effective_leader, 4),
            follower_allocation=round(effective_follower, 4),
            cash_allocation=round(effective_cash, 4),
            risk_appetite_multiplier=round(risk_mult, 4),
            heat_multiplier=round(heat_mult, 4),
            regime_floor=floor,
            regime_cap=cap,
            explain=explain,
        )


def load_config() -> dict:
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def create_exposure_model() -> ExposureModel:
    config = load_config()
    return ExposureModel(config)
