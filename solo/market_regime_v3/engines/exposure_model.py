"""
仓位暴露模型 - Portfolio Exposure Model

根据市场评分、风险偏好评分和市场状态，计算建议的总体仓位暴露水平、
主题数量以及 ETF/龙头/跟风/现金的配置比例。
"""

import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

import yaml

# 添加项目路径
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

# 配置文件路径
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yaml')


@dataclass
class ExposureResult:
    """仓位暴露建议结果"""
    portfolio_exposure: float  # 0.0-1.0（小数仓位）
    portfolio_exposure_pct: float  # 0-100%（百分比仓位）
    raw_exposure: float  # 风险偏好调整前的原始暴露
    theme_count_min: int  # 建议最小主题数
    theme_count_max: int  # 建议最大主题数
    etf_allocation: float  # ETF 配置比例
    leader_allocation: float  # 龙头配置比例
    follower_allocation: float  # 跟风配置比例
    cash_allocation: float  # 现金配置比例
    risk_appetite_adjustment: float  # 风险偏好调整乘数（实际调整量）
    explain: Dict[str, str]  # 各决策步骤的文字说明


class ExposureModel:
    """仓位暴露模型

    根据市场综合评分和市场状态，通过分段线性插值计算建议仓位，
    并结合风险偏好评分进行修正，最终给出 ETF/龙头/跟风/现金的配置建议。
    """

    def __init__(self, config: dict):
        """
        Args:
            config: 完整配置字典（yaml.safe_load 后的结果）
        """
        cfg = config.get('exposure', {})

        # ---- 模型层级 ----
        model_cfg = cfg.get('model', {})
        self.levels: List[dict] = model_cfg.get('levels', [])

        # ---- 风险偏好修正参数 ----
        ra_cfg = model_cfg.get('risk_appetite_adjustment', {})
        self.high_appetite_threshold: float = ra_cfg.get('high_appetite_threshold', 70.0)
        self.high_appetite_increase: float = ra_cfg.get('high_appetite_increase', 0.10)
        self.low_appetite_threshold: float = ra_cfg.get('low_appetite_threshold', 30.0)
        self.low_appetite_decrease: float = ra_cfg.get('low_appetite_decrease', 0.15)

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
        """在给定的层级中进行分段线性插值

        在匹配的层级内，按 score 在 [score_min, score_max) 中的位置，
        线性插值得到对应的 exposure_min ~ exposure_max 之间的值。
        """
        if not levels:
            return 0.0

        # 在层级中查找匹配区间
        for level in levels:
            score_min = level['score_min']
            score_max = level['score_max']
            if score_min <= score < score_max:
                # 计算 score 在区间内的比例
                ratio = (score - score_min) / (score_max - score_min)
                exp_min = level.get('exposure_min', 0.0)
                exp_max = level.get('exposure_max', 0.0)
                return exp_min + ratio * (exp_max - exp_min)

        # 边界情况：score 恰好等于最后一个层级的 score_max（即 100）
        last = levels[-1]
        if score >= last['score_max']:
            return last.get('exposure_max', last.get('exposure_min', 0.0))
        # 低于最低分，返回第一个层级的最小值
        return levels[0].get('exposure_min', 0.0)

    # ──────────────────────────────────────────────
    # 主题数量查找
    # ──────────────────────────────────────────────

    def _get_theme_count(self, score: float) -> tuple:
        """根据市场评分查找建议的主题数量区间"""
        if not self.theme_levels:
            return (1, 2)

        for level in self.theme_levels:
            score_min = level['score_min']
            score_max = level['score_max']
            if score_min <= score < score_max:
                return (level['min_themes'], level['max_themes'])

        # 边界情况
        last = self.theme_levels[-1]
        if score >= last['score_max']:
            return (last['max_themes'], last['max_themes'])
        first = self.theme_levels[0]
        return (first['min_themes'], first['max_themes'])

    # ──────────────────────────────────────────────
    # 主计算接口
    # ──────────────────────────────────────────────

    def calculate(self, market_score: float, risk_appetite_score: float,
                  regime_name: str) -> ExposureResult:
        """计算仓位暴露建议

        Args:
            market_score: 市场综合评分（0-100）
            risk_appetite_score: 风险偏好评分（0-100）
            regime_name: 市场状态名称（如 "Bear", "Recovery", "Neutral", "Bull", "Euphoria"）

        Returns:
            ExposureResult
        """
        explain: Dict[str, str] = {}

        # 1. 基础暴露：分段线性插值
        raw_exposure = self._interpolate_exposure(market_score, self.levels)
        explain['base_exposure'] = (
            f"市场评分 {market_score:.1f} 分 → 基础暴露 {raw_exposure:.2%}"
        )

        # 2. 风险偏好修正
        adjustment = 0.0
        if risk_appetite_score >= self.high_appetite_threshold:
            adjustment = self.high_appetite_increase
            explain['risk_appetite'] = (
                f"风险偏好偏高（{risk_appetite_score:.1f} ≥ {self.high_appetite_threshold}），"
                f"上调 {adjustment:.0%}"
            )
        elif risk_appetite_score <= self.low_appetite_threshold:
            adjustment = -self.low_appetite_decrease
            explain['risk_appetite'] = (
                f"风险偏好偏低（{risk_appetite_score:.1f} ≤ {self.low_appetite_threshold}），"
                f"下调 {-adjustment:.0%}"
            )
        else:
            explain['risk_appetite'] = (
                f"风险偏好中性（{risk_appetite_score:.1f}），不调整"
            )

        # 应用调整并限幅到 [0, 1]
        portfolio_exposure = raw_exposure + adjustment
        portfolio_exposure = max(0.0, min(1.0, portfolio_exposure))
        explain['after_clamp'] = (
            f"调整后暴露 {raw_exposure:.2%} {'+' if adjustment >= 0 else ''}{adjustment:.0%} = "
            f"{portfolio_exposure:.2%}（限幅 0~100%）"
        )

        # 3. 主题数量
        theme_min, theme_max = self._get_theme_count(market_score)
        explain['theme_count'] = (
            f"市场评分 {market_score:.1f} 分 → 建议主题 {theme_min}~{theme_max} 个"
        )

        # 4. ETF/龙头/跟风/现金 配置
        default_alloc = {'etf': 0.30, 'leader': 0.35, 'follower': 0.10, 'cash': 0.25}
        alloc = self.regime_allocations.get(regime_name, default_alloc)

        etf_alloc = alloc.get('etf', default_alloc['etf'])
        leader_alloc = alloc.get('leader', default_alloc['leader'])
        follower_alloc = alloc.get('follower', default_alloc['follower'])
        cash_alloc = alloc.get('cash', default_alloc['cash'])

        explain['allocation'] = (
            f"当前市场状态【{regime_name}】→ "
            f"ETF {etf_alloc:.0%} / 龙头 {leader_alloc:.0%} / "
            f"跟风 {follower_alloc:.0%} / 现金 {cash_alloc:.0%}"
        )

        # 5. 按总仓位缩放实际配置
        #    ETF/Leader/Follower 的配置比例是"在总仓位内的分配"，
        #    实际占用资金 = 总仓位 × 各配置比例
        #    剩余资金全部归入现金
        effective_etf = portfolio_exposure * etf_alloc
        effective_leader = portfolio_exposure * leader_alloc
        effective_follower = portfolio_exposure * follower_alloc
        effective_cash = 1.0 - effective_etf - effective_leader - effective_follower
        effective_cash = max(0.0, effective_cash)

        explain['effective_allocation'] = (
            f"总仓位 {portfolio_exposure:.0%} × 配置 → "
            f"实际ETF {effective_etf:.1%} / 实际龙头 {effective_leader:.1%} / "
            f"实际跟风 {effective_follower:.1%} / 实际现金 {effective_cash:.1%}"
        )

        return ExposureResult(
            portfolio_exposure=round(portfolio_exposure, 4),
            portfolio_exposure_pct=round(portfolio_exposure * 100.0, 2),
            raw_exposure=round(raw_exposure, 4),
            theme_count_min=theme_min,
            theme_count_max=theme_max,
            etf_allocation=round(effective_etf, 4),
            leader_allocation=round(effective_leader, 4),
            follower_allocation=round(effective_follower, 4),
            cash_allocation=round(effective_cash, 4),
            risk_appetite_adjustment=round(adjustment, 4),
            explain=explain,
        )


# ──────────────────────────────────────────────
# 工厂函数
# ──────────────────────────────────────────────

def load_config() -> dict:
    """加载配置文件"""
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def create_exposure_model() -> ExposureModel:
    """从 config.yaml 创建仓位暴露模型实例"""
    config = load_config()
    return ExposureModel(config)
