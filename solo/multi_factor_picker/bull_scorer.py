# -*- coding: utf-8 -*-
"""
BullScore 中长线牛股评分系统

基于产业景气 + 订单验证 + 龙头地位 + 业绩质量 + 预期差 框架，
寻找未来1~3年有机会上涨200%以上的A股中长线牛股。

评分结构：
  BullScore =
    0.25 × IndustryDemandScore    (产业景气)
    0.15 × TechBarrierScore       (技术壁垒)
    0.15 × OrderExplosionScore    (订单爆发)
    0.15 × EarningsQualityScore   (业绩质量)
    0.10 × LeaderScore            (龙头地位)
    0.10 × ExpectationScore       (预期差)
    0.05 × InstitutionScore       (机构认可)
    0.05 × MarketCapElasticity    (市值弹性)

  FinalScore = 0.80 × BullScore + 0.20 × ThemeScore

牛股等级：
  >=95   S+级核心牛股   90~95  S级牛股
  85~90  A级产业龙头    80~85  B级成长股
  70~80  观察名单       <70   淘汰
"""
import os
import math
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from loguru import logger

from chain_mapping import identify_chain_with_cache

# ──────────────────────────────────────────
# 主题配置：确定哪些主题参与 ThemeScore
# ──────────────────────────────────────────
BULL_THEMES = [
    "AI算力", "PCB", "光模块", "液冷服务器", "机器人",
    "商业航天", "低空经济", "半导体设备", "半导体材料",
    "创新药", "数据要素", "消费电子", "新能源车"
]


@dataclass
class BullStockData:
    """BullScore 输入数据容器"""
    ts_code: str
    name: str
    industry: str
    chain_tag: str = ""  # 产业链标签

    # ── 原始财务数据 ──
    revenue: float = 0.0          # 营收
    net_profit: float = 0.0       # 净利润
    total_assets: float = 0.0     # 总资产
    equity: float = 0.0           # 股东权益
    n_income: float = 0.0         # 净利润(最新报告期)
    revenue_yoy: float = 0.0      # 营收同比
    profit_yoy: float = 0.0       # 净利润同比
    gross_margin: float = 0.0     # 毛利率
    gross_margin_change: float = 0.0  # 毛利率变化
    rd_expense_ratio: float = 0.0 # 研发费用率
    roe_current: float = 0.0      # 当前ROE
    roe_history: list = field(default_factory=list)  # 历史ROE列表
    total_cogs: float = 0.0       # 营业成本

    # ── 订单/合同数据 ──
    contract_liability_yoy: float = 0.0  # 合同负债增速
    advance_payment_yoy: float = 0.0     # 预付款增速
    inventory_turnover_change: float = 0.0  # 存货周转率变化
    order_proxy_growth: float = 0.0      # 订单代理增速

    # ── 资本与现金流 ──
    capex_growth: float = 0.0         # 资本开支增速
    fixed_asset_turnover_change: float = 0.0  # 固定资产周转率变化
    net_operate_cash_flow: float = 0.0    # 经营性现金流净额
    cashflow_growth: float = 0.0          # 经营性现金流增速

    # ── 季度业绩 ──
    quarterly_net_profit: float = 0.0       # 季度净利润
    quarterly_net_profit_prev: float = 0.0  # 上年同期季度净利润

    # ── 业绩预告 ──
    forecast_type: str = ""
    forecast_profit_change: float = 0.0

    # ── 机构资金 ──
    north_bound_daily_net: float = 0.0   # 单日净买入
    north_bound_ratio_change: float = 0.0  # 持股比例变化

    # ── 市值 ──
    market_cap: float = 0.0  # 总市值(元)

    # ── 价格趋势 ──
    price_trend_score: float = 0.0  # MA20以上得分
    pct_chg: float = 0.0           # 当日涨跌幅

    # ── 行业/产业链 ──
    industry_growth: float = 0.0  # 行业增速(日线代理)
    capacity_utilization: float = 0.0  # 产能利用率代理


@dataclass
class BullScoreResult:
    """BullScore 评分结果"""
    ts_code: str
    name: str
    industry: str
    chain_tag: str = ""

    # 8 个子维度得分(0~100)
    industry_demand_score: float = 0.0
    tech_barrier_score: float = 0.0
    order_explosion_score: float = 0.0
    earnings_quality_score: float = 0.0
    leader_score: float = 0.0
    expectation_score: float = 0.0
    institution_score: float = 0.0
    marketcap_score: float = 0.0

    # 汇总
    bull_score: float = 0.0

    # 主题
    theme: str = ""
    theme_score: float = 0.0
    final_score: float = 0.0

    # 等级
    bull_level: str = ""

    # 原始数据（用于输出）
    revenue: float = 0.0
    net_profit: float = 0.0
    roe: float = 0.0
    gross_margin: float = 0.0
    rd_expense_ratio: float = 0.0
    revenue_yoy: float = 0.0
    profit_yoy: float = 0.0
    contract_liability_yoy: float = 0.0
    market_cap: float = 0.0
    forecast_type: str = ""

    # 子维度详情
    sub_details: Dict = field(default_factory=dict)


# ============================================================
# 辅助函数
# ============================================================

def _percentile_rank(series: pd.Series, value: float) -> float:
    """计算 value 在 series 中的分位数排名 (0~1)"""
    if len(series) == 0:
        return 0.0
    if np.isnan(value):
        return 0.0
    # 排除NaN
    clean = series.dropna()
    if len(clean) == 0:
        return 0.0
    # 计算秩
    count_less = (clean <= value).sum()
    return count_less / len(clean)


def _winsorize(series: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    """缩尾处理：将极端值压缩到分位数边界"""
    if len(series) == 0:
        return series
    lq = series.quantile(lower)
    uq = series.quantile(upper)
    return series.clip(lq, uq)


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    """安全除法"""
    return a / b if b != 0 else default


def _norm_minmax(series: pd.Series) -> pd.Series:
    """Min-Max 归一化到 0~1"""
    if len(series) == 0:
        return series
    mn, mx = series.min(), series.max()
    if mx - mn == 0:
        return pd.Series([0.5] * len(series), index=series.index)
    return (series - mn) / (mx - mn)


def get_bull_level(score: float) -> str:
    if score >= 95:
        return "S+级核心牛股"
    elif score >= 90:
        return "S级牛股"
    elif score >= 85:
        return "A级产业龙头"
    elif score >= 80:
        return "B级成长股"
    elif score >= 70:
        return "观察名单"
    else:
        return "淘汰"


# 产业链标签→主题名称映射（与 _compute_theme_score 保持一致）
_CHAIN_TO_THEME = {
    "AI算力链": "AI算力", "PCB链": "PCB", "机器人链": "机器人",
    "低空经济链": "低空经济", "半导体设备链": "半导体设备",
    "半导体材料链": "半导体材料", "医药链": "创新药",
    "消费电子链": "消费电子", "新能源链": "新能源车",
    "军工链": "商业航天", "化工链": "新能源车",
}


def chain_to_theme(chain_tag: str) -> str:
    """根据产业链标签获取所属主题"""
    return _CHAIN_TO_THEME.get(chain_tag, "")


# ============================================================
# 主题分获取
# ============================================================

def fetch_theme_scores_from_db(trade_date: str = None) -> Dict[str, float]:
    """
    从 theme_trend_sentiment.db 获取主题综合分

    返回: {theme_name: composite_score}
    """
    db_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "cache_backbone_tushare", "theme_trend_sentiment.db"
    )
    if not os.path.exists(db_path):
        logger.warning(f"主题数据库不存在: {db_path}")
        return {}

    try:
        conn = sqlite3.connect(db_path)

        if trade_date is None:
            # 取最新交易日
            df_dates = pd.read_sql(
                "SELECT DISTINCT trade_date FROM theme_scores ORDER BY trade_date DESC LIMIT 1", conn
            )
            if df_dates.empty:
                conn.close()
                return {}
            trade_date = df_dates.iloc[0]['trade_date']

        df = pd.read_sql(
            f"SELECT theme, composite_score, hot_score, trend_score, sentiment_score "
            f"FROM theme_scores WHERE trade_date = '{trade_date}'", conn
        )
        conn.close()

        result = {}
        for _, row in df.iterrows():
            theme = row['theme']
            composite = float(row['composite_score']) if pd.notna(row['composite_score']) else 0.0
            hot = float(row['hot_score']) if pd.notna(row['hot_score']) else 0.0
            trend = float(row['trend_score']) if pd.notna(row['trend_score']) else 0.0
            sentiment = float(row['sentiment_score']) if pd.notna(row['sentiment_score']) else 0.0

            # 综合分 = 0.35*趋势 + 0.30*情绪 + 0.20*热榜 + 0.15*composite(原有)
            theme_score = trend * 0.35 + sentiment * 0.30 + hot * 0.20 + composite * 0.15
            result[theme] = theme_score

        return result

    except Exception as e:
        logger.warning(f"读取主题数据库失败: {e}")
        return {}


# ============================================================
# BullScore 计算器
# ============================================================

class BullScorer:
    """
    BullScore 评分计算器

    使用流程：
    1. 提供全市场 stocks_data (List[BullStockData])
    2. 调用 compute_all_scores() 得到所有股票评分
    3. 输出结果 DataFrame
    """

    def __init__(self, config: Dict = None, fetcher=None):
        self.config = config or {}
        self.fetcher = fetcher
        self._theme_scores_cache: Dict[str, float] = {}

    # ─────────────── 子维度评分 ───────────────

    def _score_industry_demand(self, data: BullStockData,
                                group_series: Dict[str, pd.Series]) -> Tuple[float, Dict]:
        """
        产业景气度评分 (0~100)

        使用需求链框架，行业内分位数：
        - TerminalDemand: revenue_yoy + gross_margin_change
        - OrderStrength: contract_liability_yoy + advance_payment_yoy
        - PriceStrength: gross_margin_change
        - CapacityUtilization: fixed_asset_turnover_change
        - IndustryCapex: capex_growth
        """
        industry = data.industry
        details = {}

        # 终端需求：营收增速 + 毛利率变化（供需紧张代理）
        rev_yoy_pct = _percentile_rank(
            group_series.get(f'revenue_yoy_{industry}', pd.Series()),
            data.revenue_yoy
        )
        gm_chg_pct = _percentile_rank(
            group_series.get(f'gross_margin_change_{industry}', pd.Series()),
            data.gross_margin_change
        )
        terminal = 0.30 * rev_yoy_pct + 0.20 * gm_chg_pct
        details['terminal_demand_rank'] = rev_yoy_pct
        details['terminal_demand'] = terminal * 100

        # 订单强度：合同负债增速 + 预付款增速
        cl_pct = _percentile_rank(
            group_series.get(f'contract_liability_yoy_{industry}', pd.Series()),
            data.contract_liability_yoy
        )
        ap_pct = _percentile_rank(
            group_series.get(f'advance_payment_yoy_{industry}', pd.Series()),
            data.advance_payment_yoy
        )
        order_str = 0.25 * (0.6 * cl_pct + 0.4 * ap_pct)
        details['order_strength_rank'] = (cl_pct + ap_pct) / 2

        # 价格强度：毛利率变化
        price_pct = 0.20 * gm_chg_pct
        details['price_strength_rank'] = gm_chg_pct

        # 产能利用率：固定资产周转率变化
        cap_pct = _percentile_rank(
            group_series.get(f'fixed_asset_turnover_change_{industry}', pd.Series()),
            data.fixed_asset_turnover_change
        )
        capacity = 0.15 * cap_pct
        details['capacity_utilization_rank'] = cap_pct

        # 资本开支：capex增速
        capex_pct = _percentile_rank(
            group_series.get(f'capex_growth_{industry}', pd.Series()),
            data.capex_growth
        )
        ind_capex = 0.10 * capex_pct
        details['industry_capex_rank'] = capex_pct

        score = (terminal + order_str + price_pct + capacity + ind_capex) * 100
        details['raw_score'] = score
        return min(score, 100), details

    def _score_tech_barrier(self, data: BullStockData,
                             group_series: Dict[str, pd.Series]) -> Tuple[float, Dict]:
        """
        技术壁垒评分 (0~100)

        行业内分位数：
        - ROIC: n_income / total_assets (投入资本回报率)
        - ROE: 行业内分位
        - GrossMargin: 行业内分位
        - RDIntensity: 行业内分位
        - PatentScore: 研发费用率 * ROE 的复合代理
        """
        industry = data.industry
        details = {}

        # ROIC = n_income / total_assets
        roic = _safe_div(data.n_income, data.total_assets)
        roic_pct = _percentile_rank(
            group_series.get(f'roic_{industry}', pd.Series()), roic
        )
        details['roic_rank'] = roic_pct

        # ROE
        roe_pct = _percentile_rank(
            group_series.get(f'roe_{industry}', pd.Series()), data.roe_current
        )
        details['roe_rank'] = roe_pct

        # 毛利率
        gm_pct = _percentile_rank(
            group_series.get(f'gross_margin_{industry}', pd.Series()), data.gross_margin
        )
        details['gross_margin_rank'] = gm_pct

        # 研发费用率
        rd_pct = _percentile_rank(
            group_series.get(f'rd_ratio_{industry}', pd.Series()), data.rd_expense_ratio
        )
        details['rd_intensity_rank'] = rd_pct

        # 专利代理：研发投入强度 × ROE（技术变现能力）
        patent_proxy = _safe_div(data.rd_expense_ratio * data.roe_current, 0.01)
        patent_pct = _percentile_rank(
            group_series.get(f'patent_proxy_{industry}', pd.Series()), patent_proxy
        )
        details['patent_rank'] = patent_pct

        score = (0.30 * roic_pct + 0.20 * roe_pct + 0.20 * gm_pct +
                 0.20 * rd_pct + 0.10 * patent_pct) * 100
        details['raw_score'] = score
        return min(score, 100), details

    def _score_order_explosion(self, data: BullStockData,
                                group_series: Dict[str, pd.Series]) -> Tuple[float, Dict]:
        """
        订单爆发评分 (0~100)

        连续评分，无二元判断：
        - ContractLiabilityGrowth: 合同负债增速分位
        - RevenueAcceleration: 营收同比增速分位
        - AdvanceReceiptGrowth: 预付款增速分位
        - InventoryStructureOptimization: 存货周转变化
        """
        industry = data.industry
        details = {}

        # 合同负债增速
        cl_pct = _percentile_rank(
            group_series.get(f'contract_liability_yoy_{industry}', pd.Series()),
            data.contract_liability_yoy
        )
        details['contract_liability_rank'] = cl_pct

        # 营收增速
        rev_pct = _percentile_rank(
            group_series.get(f'revenue_yoy_{industry}', pd.Series()), data.revenue_yoy
        )
        details['revenue_acceleration_rank'] = rev_pct

        # 预付款增速
        ap_pct = _percentile_rank(
            group_series.get(f'advance_payment_yoy_{industry}', pd.Series()),
            data.advance_payment_yoy
        )
        details['advance_receipt_rank'] = ap_pct

        # 存货周转优化
        inv_pct = _percentile_rank(
            group_series.get(f'inventory_turnover_change_{industry}', pd.Series()),
            data.inventory_turnover_change
        )
        details['inventory_optimization_rank'] = inv_pct

        score = (0.40 * cl_pct + 0.25 * rev_pct + 0.20 * ap_pct + 0.15 * inv_pct) * 100
        details['raw_score'] = score
        return min(score, 100), details

    def _score_earnings_quality(self, data: BullStockData,
                                 group_series: Dict[str, pd.Series]) -> Tuple[float, Dict]:
        """
        业绩质量评分 (0~100)

        全部连续化评分：
        - ProfitGrowthRank: 净利润增速分位
        - ProfitAccelerationRank: 利润增速变化
        - RevenueGrowthRank: 营收增长率分位
        - CashflowGrowthRank: 现金流增速分位
        """
        industry = data.industry
        details = {}

        # 净利润增速分位
        profit_pct = _percentile_rank(
            group_series.get(f'profit_yoy_{industry}', pd.Series()), data.profit_yoy
        )
        details['profit_growth_rank'] = profit_pct

        # 利润增速变化（加速度）：季度环比
        profit_acc = 0.0
        if data.quarterly_net_profit_prev > 0:
            profit_acc = _safe_div(
                data.quarterly_net_profit - data.quarterly_net_profit_prev,
                data.quarterly_net_profit_prev
            )
        profit_acc_pct = _percentile_rank(
            group_series.get(f'profit_acceleration_{industry}', pd.Series()), profit_acc
        )
        details['profit_acceleration_rank'] = profit_acc_pct

        # 营收增速分位
        rev_pct = _percentile_rank(
            group_series.get(f'revenue_yoy_{industry}', pd.Series()), data.revenue_yoy
        )
        details['revenue_growth_rank'] = rev_pct

        # 现金流增速分位
        cf_pct = _percentile_rank(
            group_series.get(f'cashflow_growth_{industry}', pd.Series()), data.cashflow_growth
        )
        details['cashflow_growth_rank'] = cf_pct

        score = (0.35 * profit_pct + 0.25 * profit_acc_pct +
                 0.20 * rev_pct + 0.20 * cf_pct) * 100
        details['raw_score'] = score
        return min(score, 100), details

    def _score_leader(self, data: BullStockData,
                       group_data: Dict[str, List['BullStockData']]) -> Tuple[float, Dict]:
        """
        龙头地位评分 (0~100)

        - MarketShare: 营收在行业内排名
        - IndustryRank: 综合排名
        - InstitutionCoverage: 北向资金覆盖
        - CustomerQuality: 客户质量(毛利率代理)
        """
        industry = data.industry
        details = {}

        peers = group_data.get(industry, [])
        n_peers = len(peers)
        if n_peers == 0:
            return 30.0, {'error': '无同业对比'}

        # 市场份额：按营收排名
        rev_list = sorted([p.revenue for p in peers], reverse=True)
        rank = sum(1 for r in rev_list if r > data.revenue) + 1
        market_share_score = max(0, 100 - (rank - 1) / max(n_peers, 1) * 100)
        details['revenue_rank'] = rank
        details['n_peers'] = n_peers

        # 综合排名（ROE + 毛利率 + 营收增速 综合）
        peer_scores = []
        for p in peers:
            s = (p.roe_current * 0.4 + p.gross_margin * 0.3 +
                 max(0, min(p.revenue_yoy, 0.5)) * 0.3)
            peer_scores.append(s)
        my_score = (data.roe_current * 0.4 + data.gross_margin * 0.3 +
                    max(0, min(data.revenue_yoy, 0.5)) * 0.3)
        comp_rank = sum(1 for s in peer_scores if s > my_score) + 1
        comp_score = max(0, 100 - (comp_rank - 1) / max(n_peers, 1) * 100)
        details['composite_rank'] = comp_rank

        # 机构覆盖（北向资金流入）
        inst_pct = _percentile_rank(
            pd.Series([p.north_bound_daily_net for p in peers]),
            data.north_bound_daily_net
        )
        details['institution_coverage_rank'] = inst_pct

        # 客户质量（毛利率水平）
        gm_pct = _percentile_rank(
            pd.Series([p.gross_margin for p in peers]), data.gross_margin
        )
        details['customer_quality_rank'] = gm_pct

        score = (0.40 * market_share_score + 0.20 * comp_score +
                 0.20 * inst_pct * 100 + 0.20 * gm_pct * 100)
        details['raw_score'] = score
        return min(score, 100), details

    def _score_expectation(self, data: BullStockData,
                            group_series: Dict[str, pd.Series]) -> Tuple[float, Dict]:
        """
        预期差评分 (0~100)

        - FutureProfitCAGR: 历史利润增速(过去3年YoCAGR代理)
        - EarningsUpgradeCount: 业绩预告类型
        - PEGInverse: 利润增速/估值(用ROE代理)
        - NewBusinessContribution: 研发占比(未来增长投入)
        """
        industry = data.industry
        details = {}

        # 未来利润增速代理：近2年利润增速
        fut_cagr_pct = _percentile_rank(
            group_series.get(f'profit_yoy_{industry}', pd.Series()), data.profit_yoy
        )
        details['future_cagr_rank'] = fut_cagr_pct

        # 业绩上修代理：业绩预告类型
        upgrade_score = 0.0
        ft = data.forecast_type or ''
        if '预增' in ft:
            upgrade_score = 1.0
        elif '扭亏' in ft:
            upgrade_score = 0.9
        elif '略增' in ft or '预盈' in ft:
            upgrade_score = 0.7
        elif '续盈' in ft:
            upgrade_score = 0.5
        # 用 forecast_profit_change 进一步修正
        if data.forecast_profit_change > 50:
            upgrade_score = min(1.0, upgrade_score + 0.1)
        details['earnings_upgrade'] = upgrade_score

        # PEG倒数代理 = 利润增速 / (1/ROE 代理估值)
        # 高增速 + 合理估值 = 高分数
        pe_inv = max(data.roe_current, 0.01)  # ROE作为估值效率代理
        peg = _safe_div(data.profit_yoy + 0.01, pe_inv + 0.01)
        peg_pct = _percentile_rank(
            group_series.get(f'peg_inverse_{industry}', pd.Series()), peg
        )
        details['peg_inverse_rank'] = peg_pct

        # 新业务贡献代理：研发费用率
        rd_pct = _percentile_rank(
            group_series.get(f'rd_ratio_{industry}', pd.Series()), data.rd_expense_ratio
        )
        details['new_business_rank'] = rd_pct

        score = (0.40 * fut_cagr_pct + 0.30 * upgrade_score +
                 0.20 * peg_pct + 0.10 * rd_pct) * 100
        details['raw_score'] = score
        return min(score, 100), details

    def _score_institution(self, data: BullStockData,
                            group_series: Dict[str, pd.Series]) -> Tuple[float, Dict]:
        """
        机构认可评分 (0~100)

        - FundHoldingChange: 北向资金持股变化分位(占流通股比)
        - ResearchCount: 机构覆盖(资金流入天数代理)
        - CoverageCount: 分析师覆盖(资金正流入比例)
        @note: 由于缺乏真正的基金持仓/分析师数据,使用资金流代理
        """
        industry = data.industry
        details = {}

        # 资金变化：净买入额分位
        fund_pct = _percentile_rank(
            group_series.get(f'north_net_{industry}', pd.Series()),
            data.north_bound_daily_net
        )
        details['fund_holding_change_rank'] = fund_pct

        # 北向流入天数代理(正流入/总天数)：这里用流入量的正负比例
        flow_chg = data.north_bound_ratio_change
        flow_pct = _percentile_rank(
            group_series.get(f'north_flow_chg_{industry}', pd.Series()), flow_chg
        )
        details['research_count_rank'] = flow_pct

        score = (0.40 * fund_pct + 0.30 * flow_pct + 0.20 * fund_pct + 0.10 * flow_pct) * 100
        details['raw_score'] = score
        return min(score, 100), details

    def _score_marketcap_elasticity(self, data: BullStockData) -> Tuple[float, Dict]:
        """
        市值弹性评分 (0~100)

        50~300亿  → 100分  → 5分
        300~800亿 → 80分   → 4分
        800~2000亿 → 60分  → 3分
        2000亿+   → 30分   → 1.5分
        """
        mc = data.market_cap
        details = {'market_cap': mc}

        if mc <= 0:
            return 0.0, details

        if mc <= 3e10:  # 300亿
            score = 100.0
            details['cap_range'] = '<300亿'
        elif mc <= 8e10:  # 800亿
            score = 80.0
            details['cap_range'] = '300~800亿'
        elif mc <= 2e11:  # 2000亿
            score = 60.0
            details['cap_range'] = '800~2000亿'
        else:
            score = 30.0
            details['cap_range'] = '>2000亿'

        return score, details

    def _compute_theme_score(self, chain_tag: str) -> float:
        """根据产业链标签获取主题分"""
        if not chain_tag:
            return 0.0

        # 主题名到产业链的映射
        theme_to_chain = {
            "AI算力": "AI算力链", "PCB": "PCB链", "光模块": "AI算力链",
            "液冷服务器": "AI算力链", "机器人": "机器人链", "商业航天": "低空经济链",
            "低空经济": "低空经济链", "半导体设备": "半导体设备链",
            "半导体材料": "半导体材料链", "创新药": "医药链",
            "数据要素": "AI算力链", "消费电子": "消费电子链",
            "新能源车": "新能源链"
        }

        # 反向找匹配的主题
        matched_themes = []
        for theme_name, chain in theme_to_chain.items():
            if chain == chain_tag:
                matched_themes.append(theme_name)

        if not matched_themes:
            return 0.0

        # 取匹配主题的最高分
        max_score = 0.0
        for theme in matched_themes:
            ts = self._theme_scores_cache.get(theme, 0.0)
            max_score = max(max_score, ts)

        return max_score

    # ─────────────── 主入口 ───────────────

    def compute_all_scores(self, all_data: List[BullStockData],
                            trade_date: str = None) -> List[BullScoreResult]:
        """
        计算所有股票的 BullScore

        Args:
            all_data: 全市场股票数据列表
            trade_date: 交易日（用于主题分）

        Returns:
            评分结果列表(按final_score降序)
        """
        if not all_data:
            return []

        # 1. 加载主题分
        if not self._theme_scores_cache:
            self._theme_scores_cache = fetch_theme_scores_from_db(trade_date)
            logger.info(f"已加载 {len(self._theme_scores_cache)} 个主题评分")

        # 2. 构建行业内分位数统计
        # 按 industry 分组
        industry_groups: Dict[str, List[BullStockData]] = {}
        for d in all_data:
            industry_groups.setdefault(d.industry, []).append(d)

        # 构建分位数序列
        group_series: Dict[str, pd.Series] = {}
        for ind, members in industry_groups.items():
            if len(members) < 3:
                continue

            def _tos(s: List[float]) -> pd.Series:
                return pd.Series([v for v in s if not np.isnan(v)])

            # 技术壁垒相关
            group_series[f'roic_{ind}'] = _tos(
                [_safe_div(m.n_income, m.total_assets) for m in members])
            group_series[f'roe_{ind}'] = _tos([m.roe_current for m in members])
            group_series[f'gross_margin_{ind}'] = _tos([m.gross_margin for m in members])
            group_series[f'rd_ratio_{ind}'] = _tos([m.rd_expense_ratio for m in members])
            group_series[f'patent_proxy_{ind}'] = _tos([
                _safe_div(m.rd_expense_ratio * m.roe_current, 0.01) for m in members])

            # 产业景气相关
            group_series[f'revenue_yoy_{ind}'] = _tos([m.revenue_yoy for m in members])
            group_series[f'profit_yoy_{ind}'] = _tos([m.profit_yoy for m in members])
            group_series[f'gross_margin_change_{ind}'] = _tos([m.gross_margin_change for m in members])
            group_series[f'fixed_asset_turnover_change_{ind}'] = _tos([m.fixed_asset_turnover_change for m in members])
            group_series[f'capex_growth_{ind}'] = _tos([m.capex_growth for m in members])

            # 订单相关
            group_series[f'contract_liability_yoy_{ind}'] = _tos([m.contract_liability_yoy for m in members])
            group_series[f'advance_payment_yoy_{ind}'] = _tos([m.advance_payment_yoy for m in members])
            group_series[f'inventory_turnover_change_{ind}'] = _tos([m.inventory_turnover_change for m in members])

            # 业绩相关
            group_series[f'cashflow_growth_{ind}'] = _tos([m.cashflow_growth for m in members])

            # 预期差相关
            profit_acc_list = []
            for m in members:
                if m.quarterly_net_profit_prev > 0:
                    pa = _safe_div(m.quarterly_net_profit - m.quarterly_net_profit_prev,
                                   m.quarterly_net_profit_prev)
                else:
                    pa = 0.0
                profit_acc_list.append(pa)
            group_series[f'profit_acceleration_{ind}'] = _tos(profit_acc_list)

            peg_list = []
            for m in members:
                pe_inv = max(m.roe_current, 0.01)
                peg_list.append(_safe_div(m.profit_yoy + 0.01, pe_inv + 0.01))
            group_series[f'peg_inverse_{ind}'] = _tos(peg_list)

            # 机构相关
            group_series[f'north_net_{ind}'] = _tos([m.north_bound_daily_net for m in members])
            group_series[f'north_flow_chg_{ind}'] = _tos([m.north_bound_ratio_change for m in members])

        # 3. 逐只计算评分
        results = []
        for data in all_data:
            try:
                result = self._compute_single(data, group_series, industry_groups)
                results.append(result)
            except Exception as e:
                logger.debug(f"评分异常 {data.ts_code}: {e}")

        # 4. 按 final_score 排序
        results.sort(key=lambda r: r.final_score, reverse=True)

        return results

    def _compute_single(self, data: BullStockData,
                        group_series: Dict[str, pd.Series],
                        group_data: Dict[str, List[BullStockData]]) -> BullScoreResult:
        """计算单只股票评分"""

        # 各子维度评分
        ind_demand, ind_detail = self._score_industry_demand(data, group_series)
        tech_bar, tech_detail = self._score_tech_barrier(data, group_series)
        order_exp, order_detail = self._score_order_explosion(data, group_series)
        earn_qual, earn_detail = self._score_earnings_quality(data, group_series)
        leader, leader_detail = self._score_leader(data, group_data)
        expect, expect_detail = self._score_expectation(data, group_series)
        inst, inst_detail = self._score_institution(data, group_series)
        mc_ela, mc_detail = self._score_marketcap_elasticity(data)

        # BullScore
        bull_score = (
            0.25 * ind_demand +
            0.15 * tech_bar +
            0.15 * order_exp +
            0.15 * earn_qual +
            0.10 * leader +
            0.10 * expect +
            0.05 * inst +
            0.05 * mc_ela
        )

        # ThemeScore
        theme_score = self._compute_theme_score(data.chain_tag)

        # FinalScore
        final_score = 0.80 * bull_score + 0.20 * theme_score

        return BullScoreResult(
            ts_code=data.ts_code,
            name=data.name,
            industry=data.industry,
            chain_tag=data.chain_tag,
            theme=chain_to_theme(data.chain_tag),
            industry_demand_score=round(ind_demand, 2),
            tech_barrier_score=round(tech_bar, 2),
            order_explosion_score=round(order_exp, 2),
            earnings_quality_score=round(earn_qual, 2),
            leader_score=round(leader, 2),
            expectation_score=round(expect, 2),
            institution_score=round(inst, 2),
            marketcap_score=round(mc_ela, 2),
            bull_score=round(bull_score, 2),
            theme_score=round(theme_score, 2),
            final_score=round(final_score, 2),
            bull_level=get_bull_level(final_score),
            revenue=data.revenue,
            net_profit=data.net_profit,
            roe=round(data.roe_current * 100, 2),
            gross_margin=round(data.gross_margin * 100, 2),
            rd_expense_ratio=round(data.rd_expense_ratio * 100, 2),
            revenue_yoy=round(data.revenue_yoy * 100, 2),
            profit_yoy=round(data.profit_yoy * 100, 2),
            contract_liability_yoy=round(data.contract_liability_yoy * 100, 2),
            market_cap=data.market_cap,
            forecast_type=data.forecast_type,
            sub_details={
                'ind_demand': ind_detail,
                'tech_barrier': tech_detail,
                'order_explosion': order_detail,
                'earnings_quality': earn_detail,
                'leader': leader_detail,
                'expectation': expect_detail,
                'institution': inst_detail,
                'marketcap': mc_detail,
            }
        )

    # ─────────────── 输出 ───────────────

    def to_dataframe(self, results: List[BullScoreResult]) -> pd.DataFrame:
        """评分结果转 DataFrame"""
        rows = []
        for r in results:
            rows.append({
                'ts_code': r.ts_code.replace('.SH', '').replace('.SZ', '').replace('.BJ', ''),
                'name': r.name,
                'theme': r.chain_tag,
                'industry': r.industry,
                'industry_demand_score': r.industry_demand_score,
                'tech_barrier_score': r.tech_barrier_score,
                'order_explosion_score': r.order_explosion_score,
                'earnings_quality_score': r.earnings_quality_score,
                'leader_score': r.leader_score,
                'expectation_score': r.expectation_score,
                'institution_score': r.institution_score,
                'marketcap_score': r.marketcap_score,
                'bull_score': r.bull_score,
                'theme_score': r.theme_score,
                'final_score': r.final_score,
                'bull_level': r.bull_level,
                'revenue': f"{r.revenue:.0f}" if r.revenue else "0",
                'net_profit': f"{r.net_profit:.0f}" if r.net_profit else "0",
                'roe': f"{r.roe:.1f}%" if r.roe else "0%",
                'gross_margin': f"{r.gross_margin:.1f}%" if r.gross_margin else "0%",
                'rd_ratio': f"{r.rd_expense_ratio:.1f}%" if r.rd_expense_ratio else "0%",
                'revenue_yoy': f"{r.revenue_yoy:.1f}%" if r.revenue_yoy else "0%",
                'profit_yoy': f"{r.profit_yoy:.1f}%" if r.profit_yoy else "0%",
                'contract_liability_yoy': f"{r.contract_liability_yoy:.1f}%",
                'forecast_type': r.forecast_type,
            })
        return pd.DataFrame(rows)

    def print_summary(self, results: List[BullScoreResult], top_n: int = 50):
        """打印摘要"""
        if not results:
            print("未筛选出符合条件的股票")
            return

        # 等级分布
        levels = {}
        for r in results:
            lv = r.bull_level
            levels[lv] = levels.get(lv, 0) + 1

        print(f"\n{'='*80}")
        print(f"BullScore 中长线牛股选股结果")
        print(f"{'='*80}")
        print(f"扫描范围: {len(results)} 只")
        print(f"\n牛股等级分布:")
        for lv in ["S+级核心牛股", "S级牛股", "A级产业龙头", "B级成长股", "观察名单", "淘汰"]:
            cnt = levels.get(lv, 0)
            print(f"  {lv}: {cnt}只")

        # 各板块 TOP
        top = results[:top_n]
        print(f"\nTop {top_n} 龙头股:")
        print(f"{'排名':>4} {'代码':>8} {'名称':<8} {'产业链':<12} {'Bull分':>6} {'主题分':>6} {'最终分':>6} {'等级':<14}")
        print("-" * 70)
        for i, r in enumerate(top, 1):
            code = r.ts_code.split('.')[0]
            print(f"{i:>4} {code:>8} {r.name:<8} {r.chain_tag:<12} {r.bull_score:>6.1f} {r.theme_score:>6.1f} {r.final_score:>6.1f} {r.bull_level:<14}")

        # 专项列表
        print(f"\nTop 10 产业景气最高:")
        for r in sorted(results, key=lambda x: x.industry_demand_score, reverse=True)[:10]:
            print(f"  {r.name:<8} {r.industry_demand_score:>6.1f}")

        print(f"\nTop 10 订单爆发最强:")
        for r in sorted(results, key=lambda x: x.order_explosion_score, reverse=True)[:10]:
            print(f"  {r.name:<8} {r.order_explosion_score:>6.1f}")

        print(f"\nTop 10 预期差最大:")
        for r in sorted(results, key=lambda x: x.expectation_score, reverse=True)[:10]:
            print(f"  {r.name:<8} {r.expectation_score:>6.1f}")
