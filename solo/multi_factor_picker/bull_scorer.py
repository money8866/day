# -*- coding: utf-8 -*-
"""
BullScore 中长线牛股评分系统（BullScore v2）

基于产业景气 + 订单验证 + 龙头地位 + 业绩质量 + 预期差 + 估值安全 + 筹码面 框架，
寻找未来1~3年有机会上涨200%以上的A股中长线牛股。

评分结构（BullScore v2）：
  BullScore =
    0.20 × IndustryDemandScore    (产业景气 — 降权25%→20%，避免TOP100都拿满分)
  + 0.12 × TechBarrierScore       (技术壁垒 — 降权15%→12%，极端值需非线性处理)
  + 0.15 × OrderExplosionScore    (订单爆发 — 百分比+绝对增量双维度)
  + 0.15 × EarningsQualityScore  (业绩质量)
  + 0.08 × LeaderScore            (龙头地位 — 基于市占率×技术护城河，降权10%→8%)
  + 0.13 × ExpectationScore        (预期差 — 提权10%→13%，利润YoY非线性放大)
  + 0.05 × InstitutionScore       (机构认可 — 分析师覆盖，避免与龙头信号重叠)
  + 0.05 × MarketCapElasticity    (市值弹性 — 300~1500亿最优)
  + 0.07 × ChipScore           (筹码面 — v2新增，主力资金+股东人数+增减持+回购+公募持仓)
  + 0.05 × ValuationScore      (估值安全 — PEG+质押风险+解禁压力+现金流+审计意见)

  FinalScore = 0.88 × BullScore + 0.12 × ThemeScore (主题加成权重已降低)

牛股等级（基于排名）：
  TOP10    → A级产业龙头
  TOP11-20 → B级成长股
  其余     → 观察名单
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

# 行业景气度静态评分（按申万行业/行业大类匹配，覆盖动态计算可能不准确的场景）
# 优先级高于动态计算，匹配规则：股票 industry 字段包含下方任意关键词即命中
INDUSTRY_DEMAND_STATIC = {
    # ========= AI =========
    "人工智能": 95,

    # ========= 半导体 =========
    "半导体": 92,

    # ========= 医药 =========
    "创新药": 88,

    # ========= 高端制造 =========
    "机器人": 88,
    "商业航天": 86,
    "低空经济": 84,
    "智能驾驶": 84,

    # ========= 新能源 =========
    "新能源": 82,
    "储能": 80,

    # ========= 数字经济 =========
    "数据要素": 80,
    "信创": 78,
    "网络安全": 76,

    # ========= 新材料 =========
    "新材料": 80,

    # ========= 消费 =========
    "消费电子": 75,

    # ========= 军工 =========
    "军工": 78,

    # ========= 周期 =========
    "化工": 72,
    "有色": 72,
    "钢铁": 60,
    "煤炭": 58,

    # ========= 公用 =========
    "电力": 68,
}


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
    
    # ── 增长趋势字段（v3.1新增） ──
    growth_trend: str = 'stable'  # stable/rising/falling
    data_source: str = 'annual'  # annual/Q1/semi/quarterly
    q1_revenue_yoy: float = None  # Q1营收同比（可能为None）
    q1_profit_yoy: float = None  # Q1利润同比（可能为None）
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

    # ── 卖方盈利预测一致性指标 (来自 report_rc) ──
    analyst_count: int = 0
    avg_eps_current_year: float = 0.0
    avg_eps_next_year: float = 0.0
    avg_np_current_year: float = 0.0
    avg_np_next_year: float = 0.0
    np_growth_current: float = 0.0     # 明年 vs 当年 净利润增速预测
    eps_growth_next: float = 0.0       # 明年 vs 当年 EPS 增速预测
    buy_ratio: float = 0.0              # 买入+增持+推荐 评级占比 (0~1)
    rating_sentiment: float = 0.0       # 评级情绪分 (0~3)
    analyst_revision_30d: float = 0.0   # 近30天上调幅度
    analyst_expectation_score: float = 0.0  # 一致预期综合分（由 _score_expectation 计算后写回，供 _score_institution 使用）
    latest_report_date: str = ""        # 最新研报日期

    # ── 机构资金 ──
    north_bound_daily_net: float = 0.0   # 单日净买入
    north_bound_ratio_change: float = 0.0  # 持股比例变化
    north_bound_holding_ratio: float = 0.0  # 北向资金持股比例(%)
    social_security_holding_ratio: float = 0.0  # 社保持仓比例(%)
    foreign_holding_ratio: float = 0.0  # 外资持股比例(%)

    # ── 市值 ──
    market_cap: float = 0.0  # 总市值(元)

    # ── 价格趋势 ──
    price_trend_score: float = 0.0  # MA20以上得分
    pct_chg: float = 0.0           # 当日涨跌幅

    # ── 行业/产业链 ──
    industry_growth: float = 0.0  # 行业增速(日线代理)
    capacity_utilization: float = 0.0  # 产能利用率代理

    # ── BullScore v2 新增字段 ──
    # 筹码面数据
    net_inflow_ratio: float = 0.0       # 近20日主力净流入/流通市值(%)
    holder_num_change_ratio: float = 0.0  # 近3期股东人数缩减比例(缩减=正)
    holder_trade_ratio: float = 0.0     # 近90日股东增减持/流通股本(%)
    holder_trade_netbuy: int = 0        # 净增持(1)/净减持(-1)
    repurchase_amount: float = 0.0      # 近1年回购金额(万元)
    repurchase_ratio: float = 0.0       # 回购/总市值(%)
    has_repurchase: int = 0            # 是否有回购
    fund_holding_ratio: float = 0.0    # 公募持仓占比
    fund_ratio_change: float = 0.0     # 公募持仓变化
    fund_count: int = 0                # 持有该股票的基金数量（覆盖广度）

    # 估值安全数据
    pledge_ratio: float = 0.0           # 质押比例(%)
    pledge_risk_score: float = 100.0   # 质押风险分
    unlock_ratio: float = 0.0           # 未来60天解禁/总股本(%)
    unlock_risk_score: float = 100.0    # 解禁风险分
    audit_risk_score: float = 100.0     # 审计风险分
    cashflow_ratio: float = 0.0        # 经营现金流/营收

    # 主营业务构成（用于主题匹配）
    main_business_items: list = field(default_factory=list)  # [{bz_item, bz_ratio}, ...]

    # ── 数据质量标记（v2优化：区分"数据缺失"与"真实为零"） ──
    data_completeness: float = 100.0      # 数据完整度 0~100，<60 时视为低质量信号
    data_missing_flags: Dict[str, bool] = field(default_factory=dict)  # {类别: True=缺失}
    # 标记类别：'financial', 'growth', 'profitability', 'rd', 'cashflow',
    #           'analyst', 'institutional', 'chip', 'safety'
    # True = 该类别数据缺失（不是零，而是没有数据）
    
    # ── 增长趋势字段（v3.1新增） ──
    growth_trend: str = 'stable'  # stable/rising/falling
    data_source: str = 'annual'  # annual/Q1/semi/quarterly
    q1_revenue_yoy: float = None  # Q1营收同比（可能为None）
    q1_profit_yoy: float = None  # Q1利润同比（可能为None）


@dataclass
class BullScoreResult:
    """BullScore 评分结果"""
    ts_code: str
    name: str
    industry: str
    chain_tag: str = ""

    # 9 个子维度得分(0~100) — BullScore v2 共10个
    industry_demand_score: float = 0.0
    tech_barrier_score: float = 0.0
    order_explosion_score: float = 0.0
    earnings_quality_score: float = 0.0
    leader_score: float = 0.0
    expectation_score: float = 0.0
    institution_score: float = 0.0
    marketcap_score: float = 0.0
    chip_score: float = 0.0           # BullScore v2 新增：筹码面(7%)
    valuation_score: float = 0.0

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

    # 卖方盈利预测一致性输出
    analyst_count: int = 0
    np_growth_current: float = 0.0
    eps_growth_next: float = 0.0
    buy_ratio: float = 0.0
    rating_sentiment: float = 0.0
    analyst_revision_30d: float = 0.0
    analyst_expectation_score: float = 0.0  # 一致性预期综合得分 (0~100)

    # v3.1 新增：增长趋势字段
    growth_trend: str = 'stable'  # stable/rising/falling
    data_source: str = 'annual'  # annual/Q1/semi/quarterly
    q1_revenue_yoy: float = None  # Q1营收同比（可能为None）
    q1_profit_yoy: float = None  # Q1利润同比（可能为None）

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


def get_bull_level(score: float, rank: int = None) -> str:
    """
    根据排名和分数确定牛股等级
    
    新规则（基于排名）：
    - TOP 10: A级产业龙头
    - TOP 11-20: B级成长股
    - 其余: 观察名单
    """
    if rank is not None:
        if rank <= 10:
            return "A级产业龙头"
        elif rank <= 20:
            return "B级成长股"
        else:
            return "观察名单"
    
    # 兜底：按分数（用于单只股票评分时）
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
                                group_series: Dict[str, pd.Series],
                                all_rev_series: pd.Series = None) -> Tuple[float, Dict]:
        """
        产业景气度评分 (0~100) - 优化版：加入景气加速度和全市场排序

        优化点：
        1. 避免TOP100产业景气都拿满分 → 引入"景气加速度"细分
        2. 全市场营收增速分位数 → 在全市场范围内比较，提高区分度
        3. 绝对营收增量分位数 → 大公司高增长更有分量
        """
        industry = data.industry
        details = {}

        # 0. 静态评分优先：若行业匹配 INDUSTRY_DEMAND_STATIC 则直接返回
        if industry:
            for keyword, score in INDUSTRY_DEMAND_STATIC.items():
                if keyword in industry:
                    details['from_static'] = True
                    details['matched_keyword'] = keyword
                    return float(score), details

        # 1. 终端需求：营收增速(行业内分位) + 毛利率变化
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

        # 2. 景气加速度：利润加速度(行业内分位) - 区分高增长vs更高增长
        profit_acc = 0.0
        if data.quarterly_net_profit_prev > 0:
            profit_acc = _safe_div(
                data.quarterly_net_profit - data.quarterly_net_profit_prev,
                data.quarterly_net_profit_prev
            )
        profit_acc_pct = _percentile_rank(
            group_series.get(f'profit_acceleration_{industry}', pd.Series()),
            profit_acc
        )
        # 全市场营收加速：提供更多区分度
        if all_rev_series is not None and len(all_rev_series) > 0:
            rev_cross_pct = _percentile_rank(all_rev_series, data.revenue_yoy)
        else:
            rev_cross_pct = rev_yoy_pct
        acc_combined = (rev_cross_pct * profit_acc_pct) ** 0.5 if rev_cross_pct > 0 and profit_acc_pct > 0 else profit_acc_pct
        details['acceleration_rank'] = acc_combined

        # 3. 订单强度：合同负债增速 + 预付款增速
        cl_pct = _percentile_rank(
            group_series.get(f'contract_liability_yoy_{industry}', pd.Series()),
            data.contract_liability_yoy
        )
        ap_pct = _percentile_rank(
            group_series.get(f'advance_payment_yoy_{industry}', pd.Series()),
            data.advance_payment_yoy
        )
        order_str = 0.6 * cl_pct + 0.4 * ap_pct
        details['order_strength_rank'] = order_str

        # 4. 价格强度：毛利率变化
        details['price_strength_rank'] = gm_chg_pct

        # 5. 产能利用率：固定资产周转率变化
        cap_pct = _percentile_rank(
            group_series.get(f'fixed_asset_turnover_change_{industry}', pd.Series()),
            data.fixed_asset_turnover_change
        )
        details['capacity_utilization_rank'] = cap_pct

        # 6. 资本开支：capex增速
        capex_pct = _percentile_rank(
            group_series.get(f'capex_growth_{industry}', pd.Series()),
            data.capex_growth
        )
        details['industry_capex_rank'] = capex_pct

        # 综合评分：引入景气加速度(20%) + 绝对增量维度(10%) 提高区分度
        score = (
            0.25 * (0.30 * rev_yoy_pct + 0.20 * gm_chg_pct) +  # 终端需求
            0.20 * acc_combined +                                 # 景气加速度
            0.20 * order_str +                                    # 订单强度
            0.15 * gm_chg_pct +                                   # 价格强度
            0.10 * cap_pct +                                      # 产能利用
            0.10 * capex_pct                                      # 资本开支
        ) * 100
        details['raw_score'] = score
        return min(score, 100), details

    # 高科技行业列表（研发属性加成系数）
    HIGH_TECH_INDUSTRIES = {
        '半导体', '软件开发', 'IT设备', '通信设备', '电子元件', '互联网',
        '医药', '医疗', '生物', '新材料', '光伏', '新能源', '储能', '机器人',
        '自动化', '军工', '航天', '航空', '化工', '软件', '计算机'
    }
    
    def _score_tech_barrier(self, data: BullStockData,
                             group_series: Dict[str, pd.Series],
                             all_rd_series: pd.Series = None) -> Tuple[float, Dict]:
        """
        技术壁垒评分 (0~100) — v2优化版

        优化点：
        1. 增加绝对值下限约束：避免行业排名高但绝对指标一般的公司拿满分
        2. 增加毛利率趋势惩罚：毛利率持续下降说明壁垒松动
        3. 军工/航空行业加成下调：区分行政垄断 vs 技术垄断
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

        # 研发费用率 - 行业内分位
        rd_pct = _percentile_rank(
            group_series.get(f'rd_ratio_{industry}', pd.Series()), data.rd_expense_ratio
        )
        details['rd_intensity_rank'] = rd_pct

        # 跨行业研发标准化（全市场分位）- 体现科技研发 alpha 差异
        if all_rd_series is not None and len(all_rd_series) > 0:
            rd_cross_pct = _percentile_rank(all_rd_series, data.rd_expense_ratio)
        else:
            rd_cross_pct = rd_pct
        details['rd_cross_industry_rank'] = rd_cross_pct

        # 专利代理：研发投入强度 × ROE（技术变现能力）
        patent_proxy = _safe_div(data.rd_expense_ratio * data.roe_current, 0.01)
        patent_pct = _percentile_rank(
            group_series.get(f'patent_proxy_{industry}', pd.Series()), patent_proxy
        )
        details['patent_rank'] = patent_pct

        # 行业研发加成系数 — v2：区分真科技 vs 行政垄断
        industry_bonus = 1.0
        high_tech_core = {'半导体', '软件开发', 'IT设备', '通信设备', '电子元件',
                          '互联网', '医药', '生物', '新材料', '光伏', '新能源',
                          '储能', '机器人', '自动化', '软件', '计算机', '化工'}
        high_tech_moderate = {'军工', '航天', '航空', '医疗'}
        if any(ht in industry for ht in high_tech_core):
            industry_bonus = 1.15
        elif any(ht in industry for ht in high_tech_moderate):
            industry_bonus = 1.08
        elif any(keyword in industry for keyword in ['消费', '食品', '零售', '物流', '金融']):
            industry_bonus = 0.85
        details['industry_bonus'] = industry_bonus

        # 综合得分：行业内分位 * 跨行业研发分位（几何平均，体现科技差异）
        rd_combined = (rd_pct * rd_cross_pct) ** 0.5 if rd_pct > 0 and rd_cross_pct > 0 else rd_pct

        raw_score = (0.30 * roic_pct + 0.20 * roe_pct + 0.15 * gm_pct +
                 0.25 * rd_combined + 0.10 * patent_pct) * 100 * industry_bonus
        details['raw_score'] = round(raw_score, 1)

        # v2：绝对值下限约束 — 防止"矮子里拔将军"
        # 分级约束：不是简单的一刀切，而是多档递减
        # 毛利率: <15%→上限60, <20%→上限75, <25%→上限85, <30%→上限92
        # ROE: <5%→上限60, <8%→上限75, <12%→上限88
        # 研发率: <1%→上限50, <2%→上限70, <4%→上限85
        cap_score = 100.0

        # 毛利率约束
        if data.gross_margin < 0.15:
            cap_score = min(cap_score, 60.0)
            details['cap_gm_low'] = '毛利率<15%，上限60分'
        elif data.gross_margin < 0.20:
            cap_score = min(cap_score, 75.0)
            details['cap_gm_low'] = '毛利率<20%，上限75分'
        elif data.gross_margin < 0.25:
            cap_score = min(cap_score, 85.0)
            details['cap_gm_low'] = '毛利率<25%，上限85分'
        elif data.gross_margin < 0.30:
            cap_score = min(cap_score, 92.0)
            details['cap_gm_low'] = '毛利率<30%，上限92分'

        # ROE约束
        if data.roe_current < 0.05:
            cap_score = min(cap_score, 60.0)
            details['cap_roe_low'] = 'ROE<5%，上限60分'
        elif data.roe_current < 0.08:
            cap_score = min(cap_score, 75.0)
            details['cap_roe_low'] = 'ROE<8%，上限75分'
        elif data.roe_current < 0.12:
            cap_score = min(cap_score, 88.0)
            details['cap_roe_low'] = 'ROE<12%，上限88分'

        # 研发费用率约束
        if data.rd_expense_ratio < 0.01:
            cap_score = min(cap_score, 50.0)
            details['cap_rd_low'] = '研发率<1%，上限50分'
        elif data.rd_expense_ratio < 0.02:
            cap_score = min(cap_score, 70.0)
            details['cap_rd_low'] = '研发率<2%，上限70分'
        elif data.rd_expense_ratio < 0.04:
            cap_score = min(cap_score, 85.0)
            details['cap_rd_low'] = '研发率<4%，上限85分'

        details['absolute_cap'] = cap_score
        score = min(raw_score, cap_score)

        # v2：毛利率趋势惩罚 — 毛利率连续下降说明壁垒松动/竞争加剧
        # gross_margin_change < -2%：扣 5 分；< -5%：扣 10 分
        gm_change = data.gross_margin_change or 0.0
        gm_trend_penalty = 0.0
        if gm_change < -0.05:
            gm_trend_penalty = 10.0
            details['gm_trend_penalty'] = '毛利率下降>5%，扣10分'
        elif gm_change < -0.02:
            gm_trend_penalty = 5.0
            details['gm_trend_penalty'] = '毛利率下降2~5%，扣5分'
        score = max(score - gm_trend_penalty, 0.0)

        return min(score, 100), details

    def _score_order_explosion(self, data: BullStockData,
                                group_series: Dict[str, pd.Series]) -> Tuple[float, Dict]:
        """
        订单爆发评分 (0~100) — 修复版：百分比增速 + 绝对增量双维

        百分比增速维度 (55%):
        - ContractLiabilityGrowth: 合同负债增速分位
        - RevenueAcceleration: 营收同比增速分位
        - AdvanceReceiptGrowth: 预付款增速分位
        - InventoryStructureOptimization: 存货周转变化

        绝对增量维度 (45%):
        - RevenueAbsGrowth: 营收绝对增量的行业分位
          (避免小基数高增速钻空子，巨化+6.2亿 vs 三美+6.3亿本应相当)
        """
        industry = data.industry
        details = {}

        # 合同负债增速 — 数据缺失时使用营收增速和预付款增速的平均值替代
        if data.contract_liability_yoy != 0:
            cl_pct = _percentile_rank(
                group_series.get(f'contract_liability_yoy_{industry}', pd.Series()),
                data.contract_liability_yoy
            )
        else:
            rev_temp_pct = _percentile_rank(
                group_series.get(f'revenue_yoy_{industry}', pd.Series()), data.revenue_yoy
            )
            ap_temp_pct = _percentile_rank(
                group_series.get(f'advance_payment_yoy_{industry}', pd.Series()),
                data.advance_payment_yoy
            )
            cl_pct = (rev_temp_pct + ap_temp_pct) / 2.0
            details['contract_liability_missing'] = True
        details['contract_liability_rank'] = cl_pct

        # 营收增速 — 允许小幅下滑（-10%以内不扣分）
        adjusted_revenue_yoy = max(data.revenue_yoy, -0.10)
        rev_pct = _percentile_rank(
            group_series.get(f'revenue_yoy_{industry}', pd.Series()), adjusted_revenue_yoy
        )
        details['rev_acceleration_pct'] = rev_pct

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

        # 绝对营收增量（亿元）的行业分位 — 防止小基数增速钻空子
        abs_rev_growth = data.revenue * data.revenue_yoy if data.revenue > 0 else 0.0
        abs_rev_pct = _percentile_rank(
            group_series.get(f'abs_rev_growth_{industry}', pd.Series()), abs_rev_growth
        )
        details['rev_abs_growth_pct'] = abs_rev_pct

        # 综合：55% 百分比增速 + 45% 绝对增量
        pct_score = (0.40 * cl_pct + 0.25 * rev_pct + 0.20 * ap_pct + 0.15 * inv_pct)
        score = (0.55 * pct_score + 0.45 * abs_rev_pct) * 100
        details['raw_score'] = score
        return min(score, 100), details

    def _score_earnings_quality(self, data: BullStockData,
                                 group_series: Dict[str, pd.Series]) -> Tuple[float, Dict]:
        """
        业绩质量评分 (0~100) — 修复版：百分比增速 + 绝对增量双维

        百分比增速维度 (55%):
        - ProfitGrowthRank: 净利润增速分位
        - ProfitAccelerationRank: 利润增速变化
        - RevenueGrowthRank: 营收增长率分位
        - CashflowGrowthRank: 现金流增速分位

        绝对增量维度 (45%):
        - ProfitAbsGrowth: 净利润绝对增量的行业分位
          (避免小基数净利润钻空子，大公司稳定高盈利更有说服力)
        """
        industry = data.industry
        details = {}

        # 净利润增速分位 — 允许小幅下滑（-10%以内不扣分）
        adjusted_profit_yoy = max(data.profit_yoy, -0.10)
        profit_pct = _percentile_rank(
            group_series.get(f'profit_yoy_{industry}', pd.Series()), adjusted_profit_yoy
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

        # 营收增速分位 — 允许小幅下滑（-10%以内不扣分）
        adjusted_revenue_yoy = max(data.revenue_yoy, -0.10)
        rev_pct = _percentile_rank(
            group_series.get(f'revenue_yoy_{industry}', pd.Series()), adjusted_revenue_yoy
        )
        details['revenue_growth_rank'] = rev_pct

        # 现金流增速分位 — 允许小幅下滑
        adjusted_cashflow_growth = max(data.cashflow_growth, -0.10)
        cf_pct = _percentile_rank(
            group_series.get(f'cashflow_growth_{industry}', pd.Series()), adjusted_cashflow_growth
        )
        details['cashflow_growth_rank'] = cf_pct

        # 绝对净利润增量（亿元）的行业分位 — 防止小基数净利润增速钻空子
        abs_profit_growth = data.net_profit * data.profit_yoy if data.net_profit > 0 else 0.0
        abs_profit_pct = _percentile_rank(
            group_series.get(f'abs_profit_growth_{industry}', pd.Series()), abs_profit_growth
        )
        details['profit_abs_growth_pct'] = abs_profit_pct

        # 综合：55% 百分比增速 + 45% 绝对增量
        pct_score = (0.35 * profit_pct + 0.25 * profit_acc_pct +
                     0.20 * rev_pct + 0.20 * cf_pct)
        score = (0.55 * pct_score + 0.45 * abs_profit_pct) * 100
        details['raw_score'] = score
        return min(score, 100), details

    def _score_leader(self, data: BullStockData,
                       group_data: Dict[str, List['BullStockData']]) -> Tuple[float, Dict]:
        """
        龙头地位评分 (0~100) — 市占率 × 技术护城河 双重维度

        市占率维度 (65%):
        - 营收市占率排名 (RevenueMarketShare): 在行业内按营收排名
        - 定价权 (PricingPower): 毛利率相对行业水平，反映定价权与市场份额

        技术护城河维度 (35%):
        - 研发壁垒 (RDTechBarrier): 研发费用率相对行业，衡量技术唯一性/不可替代性
        - 盈利质量 (CashFlowQuality): 经营性现金流/营收，衡量护城河变现能力

        权重: 营收市占(35%) + 定价权(30%) + 研发壁垒(20%) + 现金流质量(15%)
        """
        industry = data.industry
        details = {}

        peers = group_data.get(industry, [])
        n_peers = len(peers)
        if n_peers == 0:
            return 30.0, {'error': '无同业对比'}

        # ── 1. 营收市占率排名 (RevenueMarketShare, 35%) ──
        # 方法：取该股营收在行业内的百分位排名（前10% → 100分）
        rev_series = pd.Series([p.revenue for p in peers if p.revenue > 0])
        rev_share_score = _percentile_rank(rev_series, data.revenue) * 100
        details['revenue_share_pct'] = round(rev_share_score, 1)

        # ── 2. 定价权 (PricingPower, 30%) ──
        # 方法：毛利率在行业内的百分位，反映定价权与不可替代性
        gm_series = pd.Series([p.gross_margin for p in peers if p.gross_margin > 0])
        pricing_power_score = _percentile_rank(gm_series, data.gross_margin) * 100
        details['pricing_power_pct'] = round(pricing_power_score, 1)

        # ── 3. 研发壁垒 (RDTechBarrier, 20%) ──
        # 方法：研发费用率在行业内的百分位，反映技术唯一性
        rd_series = pd.Series([p.rd_expense_ratio for p in peers if p.rd_expense_ratio > 0])
        rd_barrier_score = _percentile_rank(rd_series, data.rd_expense_ratio) * 100
        details['rd_barrier_pct'] = round(rd_barrier_score, 1)

        # ── 4. 现金流质量 (CashFlowQuality, 15%) ──
        # 方法：经营性现金流净额/营收 的百分位，衡量护城河真实变现能力
        # 对亏损公司或现金流为负给0分
        cf_ratios = []
        for p in peers:
            if p.revenue > 0 and p.net_operate_cash_flow > 0:
                cf_ratios.append(p.net_operate_cash_flow / p.revenue)
            else:
                cf_ratios.append(0.0)
        cf_series = pd.Series(cf_ratios)
        data_cf_ratio = (data.net_operate_cash_flow / data.revenue
                          if data.revenue > 0 and data.net_operate_cash_flow > 0 else 0.0)
        cash_quality_score = _percentile_rank(cf_series, data_cf_ratio) * 100
        details['cash_quality_pct'] = round(cash_quality_score, 1)

        # 综合得分
        score = (0.35 * rev_share_score
                + 0.30 * pricing_power_score
                + 0.20 * rd_barrier_score
                + 0.15 * cash_quality_score)
        details['raw_score'] = round(score, 1)
        return min(score, 100), details

    def _score_expectation(self, data: BullStockData,
                            group_series: Dict[str, pd.Series]) -> Tuple[float, Dict]:
        """
        预期差评分 (0~100) — v2优化版：低基数校验

        优化点：
        1. 低基数检测：利润增速远高于营收增速时，降低非线性放大
        2. 营收验证：高利润增长必须有营收增长支撑
        3. 扭亏/低基数高增打折：上一年利润基数异常低时，高增长可信度低
        """
        industry = data.industry
        details = {}

        # 1. 未来利润增速：行业内分位
        fut_cagr_pct = _percentile_rank(
            group_series.get(f'profit_yoy_{industry}', pd.Series()), data.profit_yoy
        )
        details['future_cagr_rank'] = fut_cagr_pct

        # v2：低基数可信度校验
        # 核心逻辑：利润高增长必须有营收增长支撑，否则大概率是基数效应/非经常性损益
        profit_yoy = data.profit_yoy or 0.0
        revenue_yoy = data.revenue_yoy or 0.0

        # 可信度系数：营收增速 vs 利润增速的匹配度
        # 利润增速 > 100% 但营收增速 < 30%：高增长可信度低
        # 利润增速 > 200% 但营收增速 < 50%：可信度极低
        credibility = 1.0
        if profit_yoy > 2.0 and revenue_yoy < 0.5:
            credibility = 0.5
            details['low_base_warning'] = '利润增速>200%但营收<50%，低基数效应，可信度0.5'
        elif profit_yoy > 1.0 and revenue_yoy < 0.3:
            credibility = 0.7
            details['low_base_warning'] = '利润增速>100%但营收<30%，可能低基数，可信度0.7'
        elif profit_yoy > 0.5 and revenue_yoy < 0.15:
            credibility = 0.85
            details['low_base_warning'] = '利润增速>50%但营收<15%，需验证，可信度0.85'
        details['growth_credibility'] = credibility

        # 非线性放大：应用可信度系数后再判断增速档位
        adjusted_profit_yoy = profit_yoy * credibility
        growth_surprise = 0.0
        if adjusted_profit_yoy > 3.0:
            growth_surprise = 0.30
        elif adjusted_profit_yoy > 1.5:
            growth_surprise = 0.15
        elif adjusted_profit_yoy > 0.5:
            growth_surprise = 0.05
        details['growth_surprise'] = round(growth_surprise * 100, 1)
        details['adjusted_profit_yoy'] = round(adjusted_profit_yoy * 100, 1)

        # 2. 盈利超预期：利润增速 > 营收增速 的差额(盈利质量超预期)
        # v2：如果利润增速远高于营收增速（差额>100%），反而可能是基数效应，降低权重
        earnings_surprise = max(profit_yoy - revenue_yoy, 0.0)
        earnings_surprise_pct = _percentile_rank(
            group_series.get(f'profit_yoy_{industry}', pd.Series()),
            earnings_surprise
        ) if earnings_surprise > 0 else 0.0
        # v2：差额过大时打折（利润增速-营收增速 > 100%，大概率不是经营改善）
        if (profit_yoy - revenue_yoy) > 1.0:
            earnings_surprise_pct *= 0.5
            details['earnings_surprise_discounted'] = '利润营收差>100%，盈利超预期打折50%'
        details['earnings_surprise'] = round(earnings_surprise_pct * 100, 1)

        # 3. 业绩上修代理：业绩预告类型
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
        if data.forecast_profit_change > 50:
            upgrade_score = min(1.0, upgrade_score + 0.1)
        details['earnings_upgrade'] = upgrade_score

        # 4. PEG倒数代理 = 利润增速 / (1/ROE 代理估值)
        # v2：应用可信度系数，低基数高增长的PEG意义不大
        pe_inv = max(data.roe_current, 0.01)
        adjusted_peg_yoy = profit_yoy * credibility
        peg = _safe_div(adjusted_peg_yoy + 0.01, pe_inv + 0.01)
        peg_pct = _percentile_rank(
            group_series.get(f'peg_inverse_{industry}', pd.Series()), peg
        )
        details['peg_inverse_rank'] = peg_pct

        # 5. 新业务贡献代理：研发费用率
        rd_pct = _percentile_rank(
            group_series.get(f'rd_ratio_{industry}', pd.Series()), data.rd_expense_ratio
        )
        details['new_business_rank'] = rd_pct

        # ---------- 卖方盈利预测一致性 ----------
        analyst_expectation_score = 0.0
        if data.analyst_count >= 3:
            # 一致预期净利润增速 (满分线 50%，>100%非线性加成)
            # v2：同样应用可信度校验，防止一致预期也被低基数忽悠
            np_growth = max(data.np_growth_current, 0.0)
            if np_growth > 1.0:
                g_score = min(1.0 + (np_growth - 1.0) * 0.2 * credibility, 1.3)
            else:
                g_score = min(np_growth / 0.5, 1.0)
            b_score = min(data.buy_ratio / 0.8, 1.0)
            r_score = min(max(data.analyst_revision_30d, 0.0) / 0.1, 1.0)
            s_score = min(data.rating_sentiment / 2.5, 1.0)

            analyst_expectation_score = (
                0.40 * g_score + 0.25 * b_score + 0.20 * r_score + 0.15 * s_score
            )
        else:
            analyst_expectation_score = 0.2

        data.analyst_expectation_score = analyst_expectation_score
        details['analyst_count'] = data.analyst_count
        details['np_growth_current'] = round(data.np_growth_current * 100, 1)
        details['analyst_expectation_score'] = round(analyst_expectation_score * 100, 1)

        # 预期差综合分（非线性放大：growth_surprise 直接加成）
        has_analyst = data.analyst_count >= 3
        if has_analyst:
            coverage_bonus = min(data.analyst_count / 20.0, 1.0) * 0.15
            analyst_weight = 0.30 + coverage_bonus
            residual = 1.0 - analyst_weight
            base_score = (
                residual * 0.40 * fut_cagr_pct +
                residual * 0.25 * upgrade_score +
                residual * 0.15 * peg_pct +
                residual * 0.10 * rd_pct +
                residual * 0.10 * earnings_surprise_pct +
                analyst_weight * analyst_expectation_score
            ) * 100
        else:
            base_score = (0.35 * fut_cagr_pct + 0.30 * upgrade_score +
                         0.20 * peg_pct + 0.10 * rd_pct + 0.05 * earnings_surprise_pct) * 100

        # 非线性放大加成
        score = base_score * (1.0 + growth_surprise)
        details['raw_score'] = round(score, 1)
        details['has_analyst_coverage'] = has_analyst
        return min(score, 100), details

    def _score_valuation(self, data: BullStockData,
                         group_series: Dict[str, pd.Series]) -> Tuple[float, Dict]:
        """
        估值安全边际评分 (0~100) — BullScore v2 增强版

        子因子权重：
          PEG代理 (30%): 利润增速/ROE，PEG<1加分，PEG>2扣分
          质押风险 (25%): pledge_stat质押比例，>50%危险
          解禁压力 (20%): share_float未来60天解禁比例
          营收质量 (15%): 经营现金流/营收比率
          审计意见 (10%): 标准无保留=加分，否则预警
        """
        industry = data.industry
        details = {}

        # 1. PEG代理评分
        pe_proxy = 1.0 / max(data.roe_current, 0.01) if data.roe_current > 0 else 50.0
        peg_proxy = pe_proxy / max(data.profit_yoy * 100, 1.0) if data.profit_yoy > 0 else 5.0
        if peg_proxy < 0.5:
            peg_score = 1.0
        elif peg_proxy < 1.0:
            peg_score = 0.8
        elif peg_proxy < 1.5:
            peg_score = 0.6
        elif peg_proxy < 2.0:
            peg_score = 0.4
        else:
            peg_score = 0.1
        details['peg_score'] = round(peg_score * 100, 1)

        # 2. ROE/PE性价比
        roe_pe_ratio = data.roe_current * max(data.profit_yoy, 0.01)
        roe_pe_pct = _percentile_rank(
            group_series.get(f'peg_inverse_{industry}', pd.Series()), roe_pe_ratio
        )
        details['roe_pe_rank'] = roe_pe_pct

        # 3. 利润增长质量
        quality_score = 0.0
        if data.profit_yoy > 0.3 and data.roe_current > 0.10:
            quality_score = 1.0
        elif data.profit_yoy > 0.1 and data.roe_current > 0.08:
            quality_score = 0.7
        elif data.profit_yoy > 0 and data.roe_current > 0:
            quality_score = 0.4
        details['quality_score'] = quality_score * 100

        # ── BullScore v2 新增子因子 ──
        # 4. 质押风险：API 无数据时用 50（中性），不默认 100
        if data.pledge_ratio == 0.0 and data.pledge_risk_score == 100.0 \
           and data.data_missing_flags.get('chip', False):
            pledge_score = 50.0
            details['pledge_data_missing'] = True
        else:
            pledge_score = data.pledge_risk_score
        details['pledge_score'] = pledge_score

        # 5. 解禁压力：API 无数据时用 50（中性）
        if data.unlock_ratio == 0.0 and data.unlock_risk_score == 100.0 \
           and data.data_missing_flags.get('chip', False):
            unlock_score = 50.0
            details['unlock_data_missing'] = True
        else:
            unlock_score = data.unlock_risk_score
        details['unlock_score'] = unlock_score

        # 6. 审计意见：API 无数据时用 50（中性）
        if data.audit_risk_score == 100.0 \
           and data.data_missing_flags.get('chip', False):
            audit_score = 50.0
            details['audit_data_missing'] = True
        else:
            audit_score = data.audit_risk_score
        details['audit_score'] = audit_score

        # 7. 营收质量（经营现金流/营收）
        cf_ratio_score = 0.0
        if data.cashflow_ratio > 0.15:
            cf_ratio_score = 1.0
        elif data.cashflow_ratio > 0.08:
            cf_ratio_score = 0.8
        elif data.cashflow_ratio > 0.03:
            cf_ratio_score = 0.5
        elif data.cashflow_ratio > 0:
            cf_ratio_score = 0.2
        details['cashflow_ratio_score'] = cf_ratio_score * 100

        # 综合评分：PEG(30%) + ROE/PE(25%) + 增长质量(15%) + 质押(15%) + 解禁(8%) + 审计(7%)
        score = (
            0.30 * peg_score +
            0.25 * roe_pe_pct +
            0.15 * quality_score +
            0.15 * (pledge_score / 100) +
            0.08 * (unlock_score / 100) +
            0.07 * (audit_score / 100)
        ) * 100
        details['raw_score'] = round(score, 1)
        return min(score, 100), details

    def _score_chip(self, data: BullStockData,
                     group_series: Dict[str, pd.Series]) -> Tuple[float, Dict]:
        """
        筹码面评分 (0~100) — BullScore v2 新增因子(7%)

        子因子权重：
          股东人数变化 (36%): 近3期股东人数缩减=筹码集中
          股东增减持 (29%): 近90日净增持/流通股本
          回购信号 (21%): 近1年有回购
          公募持仓变化 (14%): 基金持仓占流通股比例变化
        """
        industry = data.industry
        details = {}

        # 1. 股东人数变化（缩减=筹码集中=加分）
        hn_pct = _percentile_rank(
            group_series.get(f'holder_change_ratio_{industry}', pd.Series()),
            data.holder_num_change_ratio
        )
        details['holder_num_rank'] = hn_pct

        # 2. 股东增减持（净增持=加分）
        trade_score = 0.5  # 默认中性
        if data.holder_trade_netbuy > 0:
            trade_pct = _percentile_rank(
                group_series.get(f'holder_trade_ratio_{industry}', pd.Series()),
                data.holder_trade_ratio
            )
            trade_score = 0.5 + 0.5 * trade_pct
        elif data.holder_trade_netbuy < 0:
            trade_pct = _percentile_rank(
                group_series.get(f'holder_trade_ratio_{industry}', pd.Series()),
                abs(data.holder_trade_ratio)
            )
            trade_score = 0.5 - 0.5 * trade_pct
        details['holder_trade_rank'] = trade_score

        # 3. 回购信号
        repurchase_score = 1.0 if data.has_repurchase else 0.3
        details['repurchase_score'] = repurchase_score * 100

        # 4. 公募持仓变化（行业内分位）
        fund_pct = _percentile_rank(
            group_series.get(f'fund_ratio_change_{industry}', pd.Series()),
            data.fund_ratio_change
        )
        details['fund_holding_rank'] = fund_pct

        # 综合评分（删除资金流向，权重重新分配）
        score = (
            0.36 * hn_pct +
            0.29 * trade_score +
            0.21 * repurchase_score +
            0.14 * fund_pct
        ) * 100
        details['raw_score'] = round(score, 1)
        return min(score, 100), details

    def _score_institution(self, data: BullStockData,
                            group_series: Dict[str, pd.Series]) -> Tuple[float, Dict]:
        """
        机构认可评分 v4 (0~100) — 外资持仓权重最大化

        权重分配：外资机构持仓(30%) > 公募持仓(18%) > 分析师覆盖(15%) > 基金覆盖广度(12%) > 买入评级(10%) > 公募变化(8%) > 北向净流入(7%)
        - ForeignHoldingRank (30%): ★★★★★ 外资持股比例（行业分位）— 最大权重
        - FundHoldingRank (18%): 公募基金持仓占比，行业分位
        - AnalystCountRank (15%): 分析师覆盖数量，行业分位
        - FundCountRank (12%): 基金覆盖广度（持有基金数），行业分位
        - BuyRatioRaw (10%): 买入+增持+推荐 评级占比（绝对数值）
        - FundChangeRank (8%): 公募持仓变化趋势，行业分位
        - NorthNetRank (7%): 北向资金净流入，行业分位
        - DiversityBonus (最多10分): 机构多样性交叉验证（外资+公募+分析师+北向 三类以上同时认可）
        """
        industry = data.industry
        details = {}

        # 1. ★★★★★ 外资持股比例（行业分位）— 最大权重
        #    优先使用foreign_holding_ratio，其次使用north_bound_holding_ratio
        foreign_ratio = max(float(data.foreign_holding_ratio), float(data.north_bound_holding_ratio))
        fh_pct = _percentile_rank(
            group_series.get(f'foreign_holding_{industry}', pd.Series()),
            foreign_ratio
        )
        details['foreign_holding_rank'] = round(fh_pct * 100, 1)
        details['foreign_holding_ratio'] = round(foreign_ratio, 2)
        details['north_bound_holding_ratio'] = round(float(data.north_bound_holding_ratio), 2)

        # 2. 公募基金持仓占比（行业分位）
        fh_pct_fund = _percentile_rank(
            group_series.get(f'fund_holding_ratio_{industry}', pd.Series()),
            float(data.fund_holding_ratio)
        )
        details['fund_holding_rank'] = round(fh_pct_fund * 100, 1)

        # 3. 分析师覆盖数量（行业分位）
        ac_pct = _percentile_rank(
            group_series.get(f'analyst_count_{industry}', pd.Series()),
            float(data.analyst_count)
        )
        details['analyst_count_rank'] = round(ac_pct * 100, 1)

        # 4. 基金覆盖广度（行业分位）— 持有基金数量越多，认可越广泛
        fc_pct = _percentile_rank(
            group_series.get(f'fund_count_{industry}', pd.Series()),
            float(data.fund_count)
        )
        details['fund_count_rank'] = round(fc_pct * 100, 1)

        # 5. 买入评级占比（绝对数值，非行业内排名）
        buy_score = float(data.buy_ratio) * 100.0
        details['buy_ratio_raw'] = round(buy_score, 1)

        # 6. 公募持仓变化趋势（行业分位）
        fch_pct = _percentile_rank(
            group_series.get(f'fund_ratio_change_{industry}', pd.Series()),
            float(data.fund_ratio_change)
        )
        details['fund_change_rank'] = round(fch_pct * 100, 1)

        # 7. 北向资金净流入（行业分位）
        nn_pct = _percentile_rank(
            group_series.get(f'north_net_{industry}', pd.Series()),
            float(data.north_bound_daily_net)
        )
        details['north_net_rank'] = round(nn_pct * 100, 1)

        # 8. ★★★ 机构多样性交叉验证：同时有3类以上机构认可
        diversity_count = 0
        if foreign_ratio > 1.0:                        # 外资持股 > 1%
            diversity_count += 1
        if float(data.fund_holding_ratio) > 5:         # 公募持仓 > 5%
            diversity_count += 1
        if float(data.analyst_count) >= 10:            # 分析师覆盖 ≥ 10家
            diversity_count += 1
        if float(data.north_bound_daily_net) > 0:      # 北向资金净流入
            diversity_count += 1
        if float(data.fund_count) >= 20:               # 基金覆盖 ≥ 20只
            diversity_count += 1
        if buy_score > 60:                             # 买入评级 > 60%
            diversity_count += 1
        diversity_bonus = min(diversity_count * 2, 10)  # 每类+2分，上限10分
        details['diversity_count'] = diversity_count
        details['diversity_bonus'] = diversity_bonus

        # 综合评分：外资持仓最大权重(30%)
        score = (
            0.30 * (fh_pct * 100) +
            0.18 * (fh_pct_fund * 100) +
            0.15 * (ac_pct * 100) +
            0.12 * (fc_pct * 100) +
            0.10 * buy_score +
            0.08 * (fch_pct * 100) +
            0.07 * (nn_pct * 100)
        ) + diversity_bonus
        details['raw_score'] = round(score, 1)
        return min(score, 100), details

    def _score_marketcap_elasticity(self, data: BullStockData) -> Tuple[float, Dict]:
        """
        市值弹性评分 (0~100) — 中长线修正版

        中大盘（300亿~1500亿）是最优区间：兼具规模护城河 + 增长弹性
        < 50亿：过小，流动性差/抗风险能力弱
        50~300亿：小盘，高弹性但风险大
        300~1500亿：中大盘，最优区间（规模壁垒 + 流动性 + 增长空间兼备）
        1500~3000亿：大盘，增长弹性开始下降
        > 3000亿：超大规模，增长潜力有限
        """
        mc = data.market_cap
        details = {'market_cap': mc}

        if mc <= 0:
            return 0.0, details

        if mc <= 5e9:          # < 50亿
            score = 30.0
            details['cap_range'] = '<50亿'
        elif mc <= 3e10:        # 50~300亿
            score = 70.0
            details['cap_range'] = '50~300亿'
        elif mc <= 1.5e11:      # 300~1500亿（最优区间）
            score = 100.0
            details['cap_range'] = '300~1500亿（最优）'
        elif mc <= 3e11:        # 1500~3000亿
            score = 80.0
            details['cap_range'] = '1500~3000亿'
        else:                    # > 3000亿
            score = 60.0
            details['cap_range'] = '>3000亿'

        return score, details

    # 主营业务关键词 → 主题 映射（用于 fina_mainbz 匹配）
    THEME_KEYWORDS = {
        "AI算力": ["算力", "服务器", "AI芯片", "GPU", "数据中心", "云端"],
        "PCB": ["PCB", "印制电路板", "覆铜板", "CCL"],
        "光模块": ["光模块", "光通信", "光器件", "光纤"],
        "液冷服务器": ["液冷", "温控", "散热"],
        "机器人": ["机器人", "人形机器人", "工业机器人", "伺服电机", "减速器"],
        "商业航天": ["商业航天", "卫星", "火箭", "航天"],
        "低空经济": ["低空经济", "eVTOL", "无人机", "通用航空"],
        "半导体设备": ["半导体设备", "晶圆设备", "光刻", "刻蚀", "沉积"],
        "半导体材料": ["半导体材料", "硅片", "光刻胶", "电子特气"],
        "创新药": ["创新药", "生物药", "新药研发", "CXO", "创新生物"],
        "数据要素": ["数据要素", "数据服务", "数据确权", "数据交易"],
        "消费电子": ["消费电子", "智能手机", "智能穿戴", "TWS"],
        "新能源车": ["新能源汽车", "电动车", "锂电池", "动力电池"],
        "存储芯片": ["存储芯片", "NAND", "DRAM", "HBM", "存储器"],
        "IC设计": ["芯片设计", "IC设计", "SoC", "FPGA"],
    }

    def _compute_theme_score(self, theme_name: str,
                               main_business_items: List = None) -> float:
        """
        根据主题名 + 主营业务构成获取主题分 - BullScore v2 增强版

        1. 先用 chain_tag（theme.json 主题名）查数据库 + 白名单
        2. 再用 fina_mainbz 主营业务关键词做兜底匹配
        """
        if not theme_name or theme_name == 'nan':
            theme_name = ""

        base_score = 0.0

        # 1. 主题名匹配（数据库 + 白名单）
        if theme_name:
            db_score = self._theme_scores_cache.get(theme_name, 0.0)
            if db_score > 0.01:
                base_score = max(base_score, db_score)

            hot_theme_bonus = {
                "AI算力": 85.0, "半导体设备": 80.0, "半导体材料": 78.0,
                "机器人": 75.0, "低空经济": 72.0, "商业航天": 72.0,
                "PCB": 70.0, "新能源车": 70.0, "创新药": 65.0,
                "数据要素": 70.0, "军工": 65.0, "消费电子": 60.0,
                "光模块": 78.0, "液冷服务器": 75.0, "存储芯片": 73.0,
                "IC设计": 72.0,
            }
            for keyword, score in hot_theme_bonus.items():
                if keyword in theme_name or theme_name in keyword:
                    base_score = max(base_score, score)

        # 2. 主营业务关键词匹配（fina_mainbz）
        if main_business_items:
            theme_scores_v2 = {}
            for item in main_business_items:
                bz_item = str(item.get('bz_item', ''))
                bz_ratio = float(item.get('bz_ratio', 0.0)) / 100.0  # 转为0~1

                for theme, keywords in self.THEME_KEYWORDS.items():
                    for kw in keywords:
                        if kw in bz_item:
                            theme_scores_v2[theme] = theme_scores_v2.get(theme, 0.0) + bz_ratio
                            break

            if theme_scores_v2:
                best_theme = max(theme_scores_v2, key=theme_scores_v2.get)
                best_score = min(theme_scores_v2[best_theme], 1.0)  # 最多1.0
                # 主营业务匹配得分（映射到 40~80 分区间）
                theme_matched_score = 40.0 + 40.0 * best_score
                base_score = max(base_score, theme_matched_score)

        return base_score

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
        
        def _tos(s: List[float]) -> pd.Series:
            return pd.Series([v for v in s if not np.isnan(v)])

        for ind, members in industry_groups.items():
            if len(members) < 3:
                continue

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

            # 机构相关（改为分析师覆盖数据）
            group_series[f'north_net_{ind}'] = _tos([m.north_bound_daily_net for m in members])
            group_series[f'north_flow_chg_{ind}'] = _tos([m.north_bound_ratio_change for m in members])
            # ★★★ 外资持股比例（用于机构认可评分v4的最大权重因子）
            group_series[f'foreign_holding_{ind}'] = _tos([
                max(float(m.foreign_holding_ratio), float(m.north_bound_holding_ratio))
                for m in members])

            # 订单爆发的绝对增量维度
            group_series[f'abs_rev_growth_{ind}'] = _tos([
                m.revenue * m.revenue_yoy if m.revenue > 0 else 0.0 for m in members])

            # 盈利质量的绝对增量维度
            group_series[f'abs_profit_growth_{ind}'] = _tos([
                m.net_profit * m.profit_yoy if m.net_profit > 0 else 0.0 for m in members])

            # 机构持仓子因子（分析师覆盖）
            group_series[f'analyst_count_{ind}'] = _tos([float(m.analyst_count) for m in members])
            # analyst_expectation_score 需先通过 _score_expectation 计算后才有值
            # 此处用 analyst_expectation_score 原始代理值（增速×评级占比的归一化）
            group_series[f'analyst_expectation_{ind}'] = _tos([
                m.np_growth_current * m.buy_ratio if m.analyst_count >= 3 else 0.0
                for m in members])

            # ── BullScore v2 筹码面分位数 ──
            # 主力资金流向
            group_series[f'net_inflow_ratio_{ind}'] = _tos([m.net_inflow_ratio for m in members])
            # 股东人数变化
            group_series[f'holder_change_ratio_{ind}'] = _tos([m.holder_num_change_ratio for m in members])
            # 股东增减持
            group_series[f'holder_trade_ratio_{ind}'] = _tos([m.holder_trade_ratio for m in members])
            # 公募持仓占比
            group_series[f'fund_holding_ratio_{ind}'] = _tos([m.fund_holding_ratio for m in members])
            # 公募持仓变化
            group_series[f'fund_ratio_change_{ind}'] = _tos([m.fund_ratio_change for m in members])
            # ★★★ 基金覆盖广度（持有基金数量）
            group_series[f'fund_count_{ind}'] = _tos([float(m.fund_count) for m in members])

        # 3. 计算全市场研发费用率分位数（用于跨行业研发标准化）
        all_rd_series = _tos([m.rd_expense_ratio for m in all_data])

        # 4. 逐只计算评分
        results = []
        for data in all_data:
            try:
                result = self._compute_single(data, group_series, industry_groups, all_rd_series)
                results.append(result)
            except Exception as e:
                logger.debug(f"评分异常 {data.ts_code}: {e}")

        # 5. 按 final_score 排序
        results.sort(key=lambda r: r.final_score, reverse=True)

        return results

    def _compute_single(self, data: BullStockData,
                        group_series: Dict[str, pd.Series],
                        group_data: Dict[str, List[BullStockData]],
                        all_rd_series: pd.Series = None,
                        all_rev_series: pd.Series = None) -> BullScoreResult:
        """计算单只股票评分"""

        # 各子维度评分
        ind_demand, ind_detail = self._score_industry_demand(data, group_series, all_rev_series)
        tech_bar, tech_detail = self._score_tech_barrier(data, group_series, all_rd_series)
        order_exp, order_detail = self._score_order_explosion(data, group_series)
        earn_qual, earn_detail = self._score_earnings_quality(data, group_series)
        leader, leader_detail = self._score_leader(data, group_data)
        expect, expect_detail = self._score_expectation(data, group_series)
        inst, inst_detail = self._score_institution(data, group_series)
        mc_ela, mc_detail = self._score_marketcap_elasticity(data)
        chip, chip_detail = self._score_chip(data, group_series)
        val, val_detail = self._score_valuation(data, group_series)

        # BullScore v2 — 权重调整：
        # 产业景气 20%, 技术壁垒 12%, 订单爆发 15%, 业绩质量 15%
        # 龙头地位 8%, 预期差 13%, 机构认可 5%, 市值弹性 5%, 筹码面 7%, 估值安全 5%
        bull_score = (
            0.20 * ind_demand +
            0.12 * tech_bar +
            0.15 * order_exp +
            0.15 * earn_qual +
            0.08 * leader +
            0.13 * expect +
            0.05 * inst +
            0.05 * mc_ela +
            0.07 * chip +
            0.05 * val
        )

        # ThemeScore — v2: 同时传入 fina_mainbz 主营业务数据
        theme_score = self._compute_theme_score(data.chain_tag, data.main_business_items)

        # FinalScore
        final_score = 0.88 * bull_score + 0.12 * theme_score

        # ── 数据完整度惩罚（v2：缺失重大财务数据时打折） ──
        dc = data.data_completeness
        dq_penalty = 1.0
        if dc < 62.5:
            # 缺失 >= 3 个维度，中等惩罚
            dq_penalty = 1.0 - (87.5 - dc) / 100 * 0.08
            logger.debug(f"{data.ts_code} 数据完整度过低({dc}%), 最终分折扣 {round(dq_penalty, 3)}")
        elif dc < 87.5:
            # 缺失 1~2 个维度，轻微惩罚
            dq_penalty = 1.0 - (87.5 - dc) / 100 * 0.05
        if dq_penalty < 1.0:
            final_score = round(final_score * dq_penalty, 2)

        # 卖方一致预期综合得分（已由 _score_expectation 写入 data.analyst_expectation_score）
        _ae = round(data.analyst_expectation_score * 100, 2)

        return BullScoreResult(
            ts_code=data.ts_code,
            name=data.name,
            industry=data.industry,
            chain_tag=data.chain_tag,
            theme=data.chain_tag,
            industry_demand_score=round(ind_demand, 2),
            tech_barrier_score=round(tech_bar, 2),
            order_explosion_score=round(order_exp, 2),
            earnings_quality_score=round(earn_qual, 2),
            leader_score=round(leader, 2),
            expectation_score=round(expect, 2),
            institution_score=round(inst, 2),
            marketcap_score=round(mc_ela, 2),
            chip_score=round(chip, 2),
            valuation_score=round(val, 2),  # 新增：估值安全边际
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
            analyst_count=data.analyst_count,
            np_growth_current=round(data.np_growth_current * 100, 2),
            eps_growth_next=round(data.eps_growth_next * 100, 2),
            buy_ratio=round(data.buy_ratio * 100, 2),
            rating_sentiment=round(data.rating_sentiment, 2),
            analyst_revision_30d=round(data.analyst_revision_30d * 100, 2),
            analyst_expectation_score=_ae,
            # v3.1 新增字段
            growth_trend=data.growth_trend,
            data_source=data.data_source,
            q1_revenue_yoy=data.q1_revenue_yoy,
            q1_profit_yoy=data.q1_profit_yoy,
            sub_details={
                'ind_demand': ind_detail,
                'tech_barrier': tech_detail,
                'order_explosion': order_detail,
                'earnings_quality': earn_detail,
                'leader': leader_detail,
                'expectation': expect_detail,
                'institution': inst_detail,
                'marketcap': mc_detail,
                'chip': chip_detail,
                'valuation': val_detail,
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
                'chip_score': r.chip_score,  # BullScore v2 新增
                'valuation_score': r.valuation_score,
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
                # 龙头地位四维度（来自市占率×技术护城河）
                'leader_rev_share_pct': r.sub_details.get('leader', {}).get('revenue_share_pct', 0),
                'leader_pricing_power_pct': r.sub_details.get('leader', {}).get('pricing_power_pct', 0),
                'leader_rd_barrier_pct': r.sub_details.get('leader', {}).get('rd_barrier_pct', 0),
                'leader_cash_quality_pct': r.sub_details.get('leader', {}).get('cash_quality_pct', 0),
                # 卖方盈利预测一致性输出
                'analyst_count': r.analyst_count,
                'analyst_np_growth_%': f"{r.np_growth_current:.1f}%",
                'analyst_eps_growth_%': f"{r.eps_growth_next:.1f}%",
                'analyst_buy_ratio_%': f"{r.buy_ratio:.1f}%",
                'analyst_rating_score': f"{r.rating_sentiment:.2f}",
                'analyst_revision_30d_%': f"{r.analyst_revision_30d:.1f}%",
                'analyst_expectation_score': f"{r.analyst_expectation_score:.1f}",
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

        print(f"\nTop 10 龙头地位最强（市占率×技术护城河）:")
        top_leader = sorted(results, key=lambda x: x.leader_score, reverse=True)[:10]
        print(f"  {'名称':<10} {'龙头分':>6} {'营收市占':>7} {'定价权':>6} {'研发壁垒':>7} {'现金流质量':>9}")
        print(f"  {'-'*10} {'-'*6} {'-'*7} {'-'*6} {'-'*7} {'-'*9}")
        for r in top_leader:
            ld = r.sub_details.get('leader', {})
            print(f"  {r.name:<10} {r.leader_score:>6.1f} "
                  f"{ld.get('revenue_share_pct', 0):>6.1f}% "
                  f"{ld.get('pricing_power_pct', 0):>5.1f}% "
                  f"{ld.get('rd_barrier_pct', 0):>6.1f}% "
                  f"{ld.get('cash_quality_pct', 0):>8.1f}%")

        print(f"\nTop 10 卖方一致预期最高:")
        top_analyst = sorted(results, key=lambda x: x.analyst_expectation_score, reverse=True)[:10]
        print(f"  {'名称':<10} {'机构数':>5} {'净利增':>7} {'买入占比':>8} {'30天上修':>8} {'一致分':>6}")
        print(f"  {'-'*10} {'-'*5} {'-'*7} {'-'*8} {'-'*8} {'-'*6}")
        for r in top_analyst:
            print(f"  {r.name:<10} {r.analyst_count:>5} {r.np_growth_current:>6.1f}% {r.buy_ratio:>7.1f}% {r.analyst_revision_30d:>7.1f}% {r.analyst_expectation_score:>5.1f}")
