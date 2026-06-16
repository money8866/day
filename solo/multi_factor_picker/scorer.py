"""
评分模块 - 多因子综合得分计算
"""
from typing import Dict, List
from dataclasses import dataclass, field
import pandas as pd
import loguru
from factor_checker import FactorResult

logger = loguru.logger


@dataclass
class StockScore:
    """股票评分结果"""
    ts_code: str
    name: str
    industry: str = ""

    # 各因子得分
    tech_barrier_score: float = 0.0
    supply_demand_score: float = 0.0
    performance_score: float = 0.0
    institutional_score: float = 0.0
    industry_momentum_score: float = 0.0  # 需求链景气度

    # 综合得分
    total_score: float = 0.0

    # 因子详情
    factor_details: Dict = field(default_factory=dict)
    reason: str = ""

    # 原始数据
    roe: float = 0.0
    gross_margin: float = 0.0
    rd_expense_ratio: float = 0.0
    industry_growth: float = 0.0
    north_bound_change: float = 0.0
    north_bound_daily_net: float = 0.0
    performance_type: str = ""

    # 需求链景气度详情
    chain_tag: str = ""  # 产业链标签
    terminal_demand: float = 0.0  # 终端需求指数
    order_strength: float = 0.0   # 订单强度指数
    price_change_score: float = 0.0  # 价格变化指数
    capacity_score: float = 0.0   # 产能利用率指数
    capex_score: float = 0.0     # 资本开支指数


class Scorer:
    """多因子评分器（5因子版：各占20%）"""

    def __init__(self, config: Dict):
        self.config = config
        self.weights = config.get('weights', {
            'tech_barrier': 0.20,
            'supply_demand': 0.20,
            'performance': 0.20,
            'institutional': 0.20,
            'industry_momentum': 0.20,
        })

    def calculate_total_score(self, tech_score: float, demand_score: float,
                               perf_score: float, inst_score: float,
                               momentum_score: float = 0.0) -> float:
        """
        计算综合得分（5因子等权模型）

        公式: 总分 = 技术壁垒*0.2 + 供需缺口*0.2 + 业绩兑现*0.2 + 机构认可*0.2 + 行业景气度*0.2
        """
        total = (tech_score * self.weights.get('tech_barrier', 0.20) +
                demand_score * self.weights.get('supply_demand', 0.20) +
                perf_score * self.weights.get('performance', 0.20) +
                inst_score * self.weights.get('institutional', 0.20) +
                momentum_score * self.weights.get('industry_momentum', 0.20))
        return min(total, 1.0)

    def score_stock(self, ts_code: str, name: str, industry: str,
                   factor_results: Dict, factor_details: Dict) -> StockScore:
        """
        对单只股票进行评分

        Args:
            ts_code: 股票代码
            name: 股票名称
            industry: 所属行业
            factor_results: 因子检查结果
            factor_details: 因子详细数据

        Returns:
            StockScore
        """
        tech_score = factor_results.get('tech_barrier', {}).score if hasattr(factor_results.get('tech_barrier', {}), 'score') else factor_results.get('tech_barrier', 0)
        demand_score = factor_results.get('supply_demand', {}).score if hasattr(factor_results.get('supply_demand', {}), 'score') else factor_results.get('supply_demand', 0)
        perf_score = factor_results.get('performance', {}).score if hasattr(factor_results.get('performance', {}), 'score') else factor_results.get('performance', 0)
        inst_score = factor_results.get('institutional', {}).score if hasattr(factor_results.get('institutional', {}), 'score') else factor_results.get('institutional', 0)
        momentum_score = factor_results.get('industry_momentum', {}).score if hasattr(factor_results.get('industry_momentum', {}), 'score') else factor_results.get('industry_momentum', 0)

        total = self.calculate_total_score(tech_score, demand_score, perf_score, inst_score, momentum_score)

        # 提取需求链景气度子因子得分
        momentum_details = factor_results.get('industry_momentum', FactorResult(passed=False, score=0)).details if hasattr(factor_results.get('industry_momentum', FactorResult(passed=False, score=0)), 'details') else {}

        return StockScore(
            ts_code=ts_code,
            name=name,
            industry=industry,
            tech_barrier_score=tech_score,
            supply_demand_score=demand_score,
            performance_score=perf_score,
            institutional_score=inst_score,
            industry_momentum_score=momentum_score,
            total_score=total,
            factor_details=factor_details,
            reason=factor_results.get('reason', ''),
            roe=factor_details.get('roe_current', 0.0),
            gross_margin=factor_details.get('gross_margin', 0.0),
            rd_expense_ratio=factor_details.get('rd_expense_ratio', 0.0),
            industry_growth=factor_details.get('industry_growth', 0.0),
            north_bound_change=factor_details.get('north_bound_ratio_change', 0.0),
            north_bound_daily_net=factor_details.get('north_bound_daily_net', 0.0),
            performance_type=factor_details.get('perf_type', ''),
            chain_tag=factor_details.get('chain_tag', ''),
            terminal_demand=momentum_details.get('terminal_demand', 0.0),
            order_strength=momentum_details.get('order_strength', 0.0),
            price_change_score=momentum_details.get('price_score', 0.0),
            capacity_score=momentum_details.get('capacity_score', 0.0),
            capex_score=momentum_details.get('capex_score', 0.0)
        )

    def rank_stocks(self, stocks: List[StockScore]) -> List[StockScore]:
        """
        对股票列表按总分排序

        Args:
            stocks: 股票评分列表

        Returns:
            排序后的列表
        """
        return sorted(stocks, key=lambda x: x.total_score, reverse=True)

    def to_dataframe(self, stocks: List[StockScore]) -> pd.DataFrame:
        """
        转换为DataFrame便于输出

        Args:
            stocks: 股票评分列表

        Returns:
            DataFrame
        """
        data = []
        for s in stocks:
            data.append({
                '股票代码': s.ts_code,
                '股票名称': s.name,
                '产业链': s.chain_tag,
                '所属行业': s.industry,
                'ROE': f"{s.roe*100:.1f}%",
                '毛利率': f"{s.gross_margin*100:.1f}%",
                '研发占比': f"{s.rd_expense_ratio*100:.1f}%",
                '行业增速': f"{s.industry_growth*100:.1f}%",
                '业绩兑现类型': s.performance_type,
                '北向资金变动': f"{s.north_bound_change*100:.2f}%",
                '北向日净买入(亿)': f"{s.north_bound_daily_net/1e8:.2f}",
                '技术壁垒分': f"{s.tech_barrier_score:.3f}",
                '供需缺口分': f"{s.supply_demand_score:.3f}",
                '业绩兑现分': f"{s.performance_score:.3f}",
                '机构认可分': f"{s.institutional_score:.3f}",
                '行业景气度分': f"{s.industry_momentum_score:.3f}",
                '终端需求指数': f"{s.terminal_demand:.3f}",
                '订单强度指数': f"{s.order_strength:.3f}",
                '价格变化指数': f"{s.price_change_score:.3f}",
                '产能利用率指数': f"{s.capacity_score:.3f}",
                '资本开支指数': f"{s.capex_score:.3f}",
                '综合得分': f"{s.total_score:.4f}"
            })

        return pd.DataFrame(data)

    def generate_summary(self, stocks: List[StockScore]) -> str:
        """
        生成选股摘要

        Args:
            stocks: 股票评分列表

        Returns:
            摘要文本
        """
        if not stocks:
            return "未筛选出符合全部因子的股票"

        top3 = stocks[:3] if len(stocks) >= 3 else stocks

        summary_lines = [
            f"共筛选出 {len(stocks)} 只满足全部五因子的股票",
            "",
            f"综合得分 Top {len(top3)}:",
        ]

        for i, s in enumerate(top3, 1):
            highlights = []
            if s.tech_barrier_score > 0.8:
                highlights.append(f"技术壁垒强(ROE={s.roe*100:.0f}%)")
            if s.supply_demand_score > 0.8:
                highlights.append(f"供需缺口大(行业增速={s.industry_growth*100:.0f}%)")
            if s.performance_score > 0.8:
                highlights.append(f"业绩高增长({s.performance_type})")
            if s.institutional_score > 0.8:
                highlights.append(f"机构大幅增仓")
            if s.industry_momentum_score > 0.8:
                highlights.append(f"行业景气度高")

            highlight_str = ", ".join(highlights) if highlights else "综合优质"
            summary_lines.append(f"  {i}. {s.name}({s.ts_code}) 行业:{s.industry} 总分 {s.total_score:.2f}")
            summary_lines.append(f"     亮点: {highlight_str}")

        return "\n".join(summary_lines)
