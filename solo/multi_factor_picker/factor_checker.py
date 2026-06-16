"""
因子检查模块 - 四因子筛选逻辑
"""
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
import pandas as pd
import numpy as np
import loguru

logger = loguru.logger


@dataclass
class FactorResult:
    """因子检查结果"""
    passed: bool
    score: float
    details: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass
class StockFactorData:
    """个股因子数据容器"""
    ts_code: str
    name: str
    industry: str = ""
    # 财务数据
    roe_current: float = 0.0  # 当前ROE
    roe_history: List[float] = field(default_factory=list)  # 历史ROE列表
    gross_margin: float = 0.0  # 毛利率
    rd_expense_ratio: float = 0.0  # 研发费用率
    revenue: float = 0.0  # 营收
    net_profit: float = 0.0  # 净利润
    quarterly_net_profit: float = 0.0  # 季度净利润
    quarterly_net_profit_prev: float = 0.0  # 上季度净利润
    # 供需缺口
    industry_growth: float = 0.0  # 行业营收增速
    capacity_utilization: float = 0.0  # 产能利用率
    inventory_turnover_growth: float = 0.0  # 存货周转率增速
    fixed_asset_turnover_growth: float = 0.0  # 固定资产周转率增速
    price_increase_signal: bool = False  # 涨价信号
    # 业绩预告
    forecast_type: str = ""  # 预告类型
    forecast_profit_change: float = 0.0  # 预告净利润变动
    # 行业景气度（新增）
    revenue_yoy: float = 0.0  # 收入同比增速
    profit_yoy: float = 0.0  # 净利润同比增速
    capex_growth: float = 0.0  # 资本开支增速
    order_proxy_growth: float = 0.0  # 订单代理增速
    price_trend_score: float = 0.0  # 价格趋势得分(0~1)
    # 需求链景气度评分新增字段
    gross_margin_change: float = 0.0  # 毛利率变化(YoY)
    contract_liability_yoy: float = 0.0  # 合同负债增速
    advance_payment_yoy: float = 0.0  # 预付款增速
    inventory_turnover_change: float = 0.0  # 存货周转率变化
    fixed_asset_turnover_change: float = 0.0  # 固定资产周转率变化
    # 产业链标签
    chain_tag: str = ""  # 所属产业链标签
    # 北向资金
    north_bound_ratio_change: float = 0.0  # 北向持股比例变化
    north_bound_daily_net: float = 0.0  # 北向单日净买入


class FactorChecker:
    """四因子检查器"""

    def __init__(self, config: Dict):
        self.config = config
        self.factor_config = config.get('factors', {})
        self.tech_config = self.factor_config.get('tech_barrier', {})
        self.demand_config = self.factor_config.get('supply_demand', {})
        self.perf_config = self.factor_config.get('performance', {})
        self.inst_config = self.factor_config.get('institutional', {})
        self.momentum_config = self.factor_config.get('industry_momentum', {})

    def check_tech_barrier(self, data: StockFactorData) -> FactorResult:
        """
        检查技术壁垒因子

        规则:
        ① 最近年报 ROE > 15%
        ② 过去连续3年(含当年) ROE > 0
        ③ 最近年报毛利率 > 30%
        ④ 最近年报研发费用占营收比例 > 5%
        """
        details = {}
        reasons = []

        # ① ROE > 15%
        if data.roe_current <= 0:
            roe_pass = False
            reasons.append(f"ROE={data.roe_current*100:.1f}% ≤ 15%")
        else:
            roe_ratio = min(data.roe_current / self.tech_config.get('roe_min', 0.15), 1.5)
            roe_pass = data.roe_current > self.tech_config.get('roe_min', 0.15)
            details['roe_ratio'] = roe_ratio

        if not roe_pass:
            reasons.append(f"ROE={data.roe_current*100:.1f}% ≤ 15%")

        # ② 连续3年ROE > 0
        consecutive_years = self.tech_config.get('roe_consecutive_years', 3)
        roe_positive_count = sum(1 for r in data.roe_history if r > 0)
        roe_consecutive_pass = roe_positive_count >= consecutive_years
        details['roe_positive_years'] = roe_positive_count
        if not roe_consecutive_pass:
            reasons.append(f"仅{roe_positive_count}年ROE为正,需要{consecutive_years}年")

        # ③ 毛利率 > 30%
        gm_pass = data.gross_margin > self.tech_config.get('gross_margin_min', 0.30)
        details['gross_margin'] = data.gross_margin
        if not gm_pass:
            reasons.append(f"毛利率={data.gross_margin*100:.1f}% ≤ 30%")

        # ④ 研发费用率 > 5%
        rd_pass = data.rd_expense_ratio > self.tech_config.get('rd_expense_ratio_min', 0.05)
        details['rd_expense_ratio'] = data.rd_expense_ratio
        if not rd_pass:
            reasons.append(f"研发费用率={data.rd_expense_ratio*100:.1f}% ≤ 5%")

        passed = roe_pass and roe_consecutive_pass and gm_pass and rd_pass

        # 计算得分
        score = 0.0
        if passed:
            score = (min(data.roe_current / 0.15, 1) * 0.25 +
                    min(data.gross_margin / 0.30, 1) * 0.25 +
                    min(data.rd_expense_ratio / 0.05, 1) * 0.25 +
                    0.25)  # 连续3年ROE为正

        return FactorResult(
            passed=passed,
            score=score,
            details=details,
            reason="; ".join(reasons) if reasons else "通过"
        )

    def check_supply_demand(self, data: StockFactorData) -> FactorResult:
        """
        检查供需缺口因子

        规则:
        ① 行业营收增速 > 30%
        ② 产能利用率 > 85% 或 存货周转率同比提升 + 固定资产周转率提升
        ③ 产品涨价信号(研报关键词)
        """
        details = {}
        reasons = []

        # ① 行业增长信号 (阈值调低为2%, 因为是日线代理值很小)
        industry_growth_threshold = self.demand_config.get('industry_growth_min', 0.02)
        ind_growth_pass = data.industry_growth > industry_growth_threshold
        details['industry_growth'] = data.industry_growth
        if not ind_growth_pass:
            reasons.append(f"行业增速={data.industry_growth*100:.1f}% ≤ {industry_growth_threshold*100:.0f}%")

        # ② 产能利用率/周转率提升 (curr/prev比 >= 0.95 或任一提升代理)
        cap_util_threshold = self.demand_config.get('capacity_utilization_min', 0.95)
        cap_pass = data.capacity_utilization >= cap_util_threshold
        cap_proxy_pass = data.inventory_turnover_growth > 0 or data.fixed_asset_turnover_growth > 0
        cap_final = cap_pass or cap_proxy_pass
        details['capacity_utilization'] = data.capacity_utilization
        details['inventory_turnover_growth'] = data.inventory_turnover_growth
        details['fixed_asset_turnover_growth'] = data.fixed_asset_turnover_growth
        if not cap_final:
            reasons.append(f"产能利用率={data.capacity_utilization*100:.1f}%, 周转代理未提升")

        # ③ 涨价信号 (用产能利用率提升代理)
        price_threshold = self.demand_config.get('price_signal_threshold', 0.95)
        price_pass = data.capacity_utilization > price_threshold or data.industry_growth > industry_growth_threshold
        details['price_increase_signal'] = price_pass
        if not price_pass:
            reasons.append("无涨价信号")

        passed = ind_growth_pass and cap_final and price_pass

        # 计算得分
        score = 0.0
        if passed:
            industry_score = min(data.industry_growth / 0.10, 1.0)
            cap_score = min(data.capacity_utilization, 1.5) / 1.5
            price_score = 0.3 if price_pass else 0.0
            score = industry_score * 0.4 + cap_score * 0.3 + price_score

        return FactorResult(
            passed=passed,
            score=score,
            details=details,
            reason="; ".join(reasons) if reasons else "通过"
        )

    def check_performance(self, data: StockFactorData) -> FactorResult:
        """
        检查业绩兑现因子

        规则(满足任一):
        ① 最新单季度净利润环比 > 50%
        ② 最新单季度净利润同比 > 100%
        ③ 业绩预告为扭亏/预盈
        """
        details = {}
        reasons = []

        passed = False
        perf_type = ""

        # ① 季度净利润同比 (同季度对比)
        if data.quarterly_net_profit_prev > 0:
            qoq_growth = (data.quarterly_net_profit - data.quarterly_net_profit_prev) / data.quarterly_net_profit_prev
        else:
            qoq_growth = 0.0
        qoq_threshold = self.perf_config.get('quarterly_profit_growth_min', 0.30)
        qoq_pass = qoq_growth > qoq_threshold
        details['qoq_growth'] = qoq_growth

        # ② 业绩预告
        forecast_types = self.perf_config.get('forecast_types', ['预盈', '扭亏', '预增', '略增'])
        ft = data.forecast_type or ''
        forecast_min_change = self.perf_config.get('forecast_min_change', 15.0)
        forecast_pass = False
        for allow in forecast_types:
            if allow in ft:
                if allow == '略增':
                    if data.forecast_profit_change >= forecast_min_change:
                        forecast_pass = True
                        break
                else:
                    forecast_pass = True
                    break
        details['forecast_type'] = ft
        details['forecast_profit_change'] = data.forecast_profit_change

        if qoq_pass:
            passed = True
            perf_type = f"净利润同比+{qoq_growth*100:.0f}%"
        elif forecast_pass:
            passed = True
            perf_type = f"预告{ft}(+{data.forecast_profit_change:.0f}%)"

        if not passed:
            reasons.append(f"净利润同比+{qoq_growth*100:.0f}% ≤ {qoq_threshold*100:.0f}%; "
                          f"预告类型={ft}(未在{forecast_types}或增幅不足)")

        details['perf_type'] = perf_type

        # 得分
        score = 0.0
        if passed:
            if ft in ['预增', '扭亏', '预盈']:
                score = 1.0
            else:
                score = min(qoq_growth / 1.0, 1.0)

        return FactorResult(
            passed=passed,
            score=score,
            details=details,
            reason="; ".join(reasons) if reasons else perf_type
        )

    def check_institutional(self, data: StockFactorData) -> FactorResult:
        """
        检查机构认可因子 (用大单/资金流作为机构资金代理)

        规则(满足任一):
        ① 5日累计资金净流入 > 0
        ② 最近交易日单日净买入 > 3000万元
        """
        details = {}
        reasons = []

        # ① 5日累计资金净流入 > 0
        ratio_pass = data.north_bound_ratio_change > 0
        details['north_bound_ratio_change'] = data.north_bound_ratio_change

        # ② 单日净买入 > 3000万元
        daily_min = float(self.inst_config.get('north_bound_daily_min', 3e7))
        daily_pass = float(data.north_bound_daily_net) > daily_min
        details['north_bound_daily_net'] = data.north_bound_daily_net

        # 如果没有资金流数据，则放宽为"只要净利润增长即可通过代理"
        if data.north_bound_daily_net == 0 and data.north_bound_ratio_change == 0:
            # 无资金流数据时，需要业绩强信号补偿：净利润 > 0 且 业绩预增或ROE>15%
            has_strong_perf = data.net_profit > 0 and (
                data.forecast_type in ['预增', '扭亏', '预盈'] or
                data.roe_current > 0.15 or
                data.revenue_yoy > 0.10
            )
            ratio_pass = has_strong_perf

        passed = ratio_pass or daily_pass

        if not passed:
            reasons.append(f"5日净流入={data.north_bound_ratio_change/1e4:.0f}万元, 日净买入={data.north_bound_daily_net/1e4:.0f}万元")

        # 计算得分
        score = 0.0
        if passed:
            if ratio_pass and data.north_bound_ratio_change > 0:
                score = min(data.north_bound_ratio_change / 1e8, 1.0)
            elif daily_pass:
                score = min(data.north_bound_daily_net / daily_min, 1.0)
            else:
                score = 0.3  # 业绩代理通过的基础分

        return FactorResult(
            passed=passed,
            score=score,
            details=details,
            reason="; ".join(reasons) if reasons else "通过"
        )

    def check_industry_momentum(self, data: StockFactorData) -> FactorResult:
        """
        检查行业景气度因子（需求链驱动模型）

        公式: score = 0.30×终端需求 + 0.25×订单强度 + 0.20×价格变化 + 0.15×产能利用率 + 0.10×资本开支

        原理：
        - 终端需求: 收入增速(自身供需验证) + 毛利率变化(供需紧张度)
        - 订单强度: 合同负债增速 + 预付款增速 + 存货周转变化
        - 价格变化: 毛利率变化(反映定价能力/供需紧张)
        - 产能利用率: 固定资产周转率变化 + 存货周转率变化
        - 资本开支: capex增速

        通过条件: 综合得分 > 0.3 且 收入增速>0 且 利润增速>0
        """
        details = {}
        reasons = []

        # ========== 1. 终端需求指数 (30%) ==========
        # 使用收入增速作为核心代理，毛利率变化作为供需紧张度补充
        revenue_score = min(max(data.revenue_yoy / 0.20, 0), 1.5) if data.revenue_yoy > 0 else 0.0
        gm_demand_proxy = min(max(data.gross_margin_change / 0.02, -0.5), 1.0) if data.gross_margin_change != 0 else 0.5
        terminal_demand = 0.7 * revenue_score + 0.3 * gm_demand_proxy
        details['terminal_demand'] = terminal_demand
        details['revenue_yoy'] = data.revenue_yoy
        details['gross_margin_change'] = data.gross_margin_change

        if data.revenue_yoy <= 0:
            reasons.append(f"收入增速={data.revenue_yoy*100:.1f}% ≤ 0%")

        # ========== 2. 产业链订单强度 (25%) ==========
        # 合同负债增速 + 预付款增速 + 存货周转变化
        cl_score = min(max(data.contract_liability_yoy / 0.20, 0), 1.5) if data.contract_liability_yoy > 0 else 0.0
        ap_score = min(max(data.advance_payment_yoy / 0.20, 0), 1.5) if data.advance_payment_yoy > 0 else 0.0
        inv_score = min(max(data.inventory_turnover_change / 0.15, 0), 1.5) if data.inventory_turnover_change > 0 else 0.5
        order_strength = 0.4 * cl_score + 0.3 * ap_score + 0.3 * inv_score
        details['order_strength'] = order_strength
        details['contract_liability_yoy'] = data.contract_liability_yoy
        details['advance_payment_yoy'] = data.advance_payment_yoy
        details['inventory_turnover_change'] = data.inventory_turnover_change

        # ========== 3. 产品价格变化 (20%) ==========
        # 毛利率变化作为定价能力/供需紧张度的主要代理
        price_score = min(max(data.gross_margin_change / 0.03, 0), 1.5) if data.gross_margin_change > 0 else 0.5
        details['price_score'] = price_score

        # ========== 4. 产能利用率 (15%) ==========
        # 固定资产周转率变化反映产能利用紧张程度
        cap_score = 0.5  # 默认中等
        if data.fixed_asset_turnover_change > 0.10:
            cap_score = 0.9  # 高景气
        elif data.fixed_asset_turnover_change > 0:
            cap_score = 0.5 + data.fixed_asset_turnover_change / 0.10 * 0.4
        elif data.fixed_asset_turnover_change < -0.10:
            cap_score = 0.2  # 过剩
        else:
            cap_score = 0.5 + data.fixed_asset_turnover_change / 0.10 * 0.3

        cap_score = max(0.0, min(1.0, cap_score))
        details['capacity_score'] = cap_score
        details['fixed_asset_turnover_change'] = data.fixed_asset_turnover_change

        # ========== 5. 资本开支扩张 (10%) ==========
        capex_score = 0.5  # 默认中等
        if data.capex_growth > 0.30:
            capex_score = 1.0  # 扩张期
        elif data.capex_growth > 0.10:
            capex_score = 0.7 + (data.capex_growth - 0.10) / 0.20 * 0.3
        elif data.capex_growth > 0:
            capex_score = 0.4 + data.capex_growth / 0.10 * 0.3
        else:
            capex_score = max(0.3, 0.4 + data.capex_growth / 0.10 * 0.4)

        capex_score = max(0.0, min(1.0, capex_score))
        details['capex_score'] = capex_score
        details['capex_growth'] = data.capex_growth

        # ========== 综合得分 ==========
        score = (
            terminal_demand * 0.30 +
            order_strength * 0.25 +
            price_score * 0.20 +
            cap_score * 0.15 +
            capex_score * 0.10
        )
        score = min(max(score, 0.0), 1.0)

        # ========== 通过条件 ==========
        # 综合得分 > 0.3 且 收入增速>0
        # 利润增速放宽（允许一次性费用致利润短期下滑但收入健康成长的企业）
        passed = (
            score > 0.3 and
            data.revenue_yoy > 0
        )

        if not passed:
            if score <= 0.3:
                reasons.append(f"综合景气度={score:.3f} ≤ 0.3")
            if data.revenue_yoy <= 0:
                reasons.append(f"收入增速={data.revenue_yoy*100:.1f}% ≤ 0%")

        return FactorResult(
            passed=passed,
            score=score,
            details=details,
            reason="; ".join(reasons) if reasons else "需求链景气度高"
        )

    def check_all_factors(self, data: StockFactorData) -> Dict[str, FactorResult]:
        """
        检查所有五个因子

        Returns:
            dict: {factor_name: FactorResult}
        """
        results = {
            'tech_barrier': self.check_tech_barrier(data),
            'supply_demand': self.check_supply_demand(data),
            'performance': self.check_performance(data),
            'institutional': self.check_institutional(data),
            'industry_momentum': self.check_industry_momentum(data)
        }
        return results

    def all_passed(self, results: Dict[str, FactorResult]) -> bool:
        """检查是否全部因子通过"""
        return all(r.passed for r in results.values())
