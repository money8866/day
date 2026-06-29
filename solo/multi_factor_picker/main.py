"""
BullScore 中长线牛股选股主程序

基于产业景气 + 订单验证 + 龙头地位 + 业绩质量 + 预期差 + 估值安全边际 + 筹码面 框架，
寻找未来1~3年有机会上涨200%以上的A股中长线牛股。

评分结构（BullScore v2）：
  BullScore =
    0.20 × IndustryDemandScore    (产业景气 — 降权，避免TOP100都拿满分)
  + 0.12 × TechBarrierScore       (技术壁垒 — 降权，极端值需非线性处理)
  + 0.15 × OrderExplosionScore    (订单爆发 — 百分比+绝对增量双维度)
  + 0.15 × EarningsQualityScore   (业绩质量)
  + 0.08 × LeaderScore            (龙头地位 — 基于市占率×技术护城河，降权避免与机构重叠)
  + 0.13 × ExpectationScore       (预期差 — 提权，利润YoY非线性放大，超额收益核心)
  + 0.05 × InstitutionScore       (机构认可 — 分析师覆盖)
  + 0.05 × MarketCapElasticity    (市值弹性 — 300~1500亿最优)
  + 0.07 × ChipScore              (筹码面 — BullScore v2 新增，主力资金+股东人数+增减持+回购)
  + 0.05 × ValuationScore         (估值安全 — PEG+质押风险+解禁压力+审计意见)

  FinalScore = 0.80 × BullScore + 0.20 × ThemeScore (主题加成)

  牛股等级（基于排名）：
    TOP10    → A级产业龙头
    TOP11-20 → B级成长股
    其余     → 观察名单
"""
import os
import sys
import time
import json
import yaml
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed as futures_as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import pandas as pd
import numpy as np
from loguru import logger

import tushare as ts
_original_set_token = ts.set_token
def _patched_set_token(token):
    os.environ['TUSHARE_TOKEN'] = token
    try:
        _original_set_token(token)
    except (PermissionError, OSError):
        pass
ts.set_token = _patched_set_token

from data_fetcher import DataFetcher
from bull_scorer import BullStockData, BullScoreResult, BullScorer as _BullScorer  # 数据类型的兼容引用，仅用于基础评分
from bull_scorer_v2 import BullScorerV2, BullScoreV2Result  # v2 评分引擎
from chain_mapping import identify_chain_with_cache, load_concept_cache
from mainline_filter import apply_mainline_filter, print_mainline_analysis


# 配置日志
logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")
log_file = Path("logs")
log_file.mkdir(exist_ok=True)
logger.add(log_file / f"multi_factor_{datetime.now().strftime('%Y%m%d')}.log", level="DEBUG")


def load_config(config_path: str = None) -> Dict:
    """加载配置文件"""
    if config_path is None:
        # 使用脚本所在目录的 config.yaml
        config_path = str(Path(__file__).parent / 'config.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def get_token(config: Dict) -> str:
    """获取Tushare Token"""
    # 优先从环境变量获取
    token_env = config.get('tushare', {}).get('token_env', 'TUSHARE_TOKEN')
    token = os.environ.get(token_env)
    if token:
        return token

    # 尝试从 .env 文件读取 (路径: ../../config/.env
    env_paths = [
        Path(__file__).resolve().parent.parent.parent / "config" / ".env",
        Path(__file__).resolve().parent.parent / "config" / ".env",
        Path(__file__).resolve().parent / "config" / ".env",
        Path.cwd().parent.parent / "config" / ".env",
        Path.cwd().parent / "config" / ".env",
    ]

    for env_path in env_paths:
        if env_path.exists():
            logger.info(f"从 {env_path} 读取 Token")
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' in line:
                        key, value = line.split('=', 1)
                        value = value.strip().strip('"\'')
                        if key.strip() == token_env:
                            os.environ[key.strip()] = value
                            return value
            break
        else:
            logger.debug(f"未找到 {env_path}")

    raise ValueError(f"未找到 Tushare Token, 请设置环境变量 {token_env} 或在 config/.env 中配置")


def prepare_stock_data(config: Dict, fetcher: DataFetcher) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict]:
    """
    准备全市场股票数据

    Returns:
        (stock_list, daily_data, moneyflow_data, daily_basic_data, north_hold_data, concept_map)
    """
    logger.info("获取股票列表...")
    stocks = fetcher.get_stock_list(list_status='L')

    # 排除ST
    if config.get('universe', {}).get('exclude_st', True):
        stocks = stocks[~stocks['name'].str.contains('ST', na=False)]

    # 排除北交所（.BJ）
    stocks = stocks[~stocks['ts_code'].str.endswith('.BJ')]

    logger.info(f"待筛选股票数量: {len(stocks)}")

    # 获取最近交易日
    trade_date = fetcher.get_last_trade_date()
    logger.info(f"最近交易日: {trade_date}")

    # 获取日线行情
    logger.info("获取日线行情...")
    daily = fetcher.get_daily(trade_date)

    # 获取北向资金数据
    logger.info("获取北向资金数据...")
    moneyflow = fetcher.get_moneyflow(trade_date)

    # 获取北向资金持股比例数据
    logger.info("获取北向资金持股数据...")
    north_hold = fetcher.get_north_hold(trade_date)

    # 获取市值数据
    logger.info("获取市值数据...")
    daily_basic = fetcher.get_daily_basic(trade_date)

    # 预加载同花顺概念缓存(用于精细化产业链标签识别)
    logger.info("加载同花顺概念板块缓存...")
    concept_map = {}
    try:
        concept_map = load_concept_cache(config)
        logger.info(f"  ✓ 已加载 {len(concept_map)} 只股票的概念映射")
    except Exception as e:
        logger.warning(f"  概念缓存加载失败(将使用名称匹配): {e}")

    return stocks, daily, moneyflow, daily_basic, north_hold, concept_map


def calculate_industry_growth_map(fetcher: DataFetcher, stocks: pd.DataFrame) -> Dict[str, float]:
    """
    计算各行业增速

    简化: 使用申万行业分类的平均营收增速作为行业增速代理

    Returns:
        {行业名称: 增速}
    """
    logger.info("计算行业增速...")

    try:
        trade_date = fetcher.get_last_trade_date()
        daily = fetcher.get_daily(trade_date)

        if daily is not None and len(daily) > 0:
            stock_ind = stocks[['ts_code', 'industry']].copy()
            daily = daily.merge(stock_ind, on='ts_code', how='left')

            industry_growth = {}
            for industry in daily['industry'].dropna().unique():
                ind_data = daily[daily['industry'] == industry]
                if len(ind_data) > 3:
                    # 用中位数更稳健（避免个别极端值干扰），再归一化到0~0.3范围
                    median_chg = ind_data['pct_chg'].median() / 100
                    # 正值部分保留，负值部分缩小权重，整体压缩到0~0.3之间
                    normalized = min(max((median_chg + 0.04) / 0.08 * 0.15, 0.01), 0.30)
                    industry_growth[industry] = normalized
                else:
                    industry_growth[industry] = 0.10

            for industry in stocks['industry'].dropna().unique():
                if industry not in industry_growth:
                    industry_growth[industry] = 0.30

            return industry_growth
    except Exception as e:
        logger.warning(f"获取行业数据失败: {e}")

    return {industry: 0.30 for industry in stocks['industry'].dropna().unique()}


def extract_bull_data(row: pd.Series,
                       financial_data: Dict,
                       daily: pd.DataFrame,
                       daily_basic: pd.DataFrame,
                       moneyflow: pd.DataFrame,
                       north_hold: pd.DataFrame,
                       industry_growth_map: Dict,
                       config: Dict = None,
                       report_rc_map: Dict = None,
                       chip_margin_data: Dict = None,
                       main_bz_data: List = None) -> Optional[BullStockData]:
    """
    从原始数据提取 BullScore 所需数据

    Args:
        row: 股票基本信息行
        financial_data: 财务数据字典(income/balance/forecast/cashflow)
        daily: 日线行情
        daily_basic: 每日基本面(含市值)
        moneyflow: 大单资金流
        north_hold: 北向资金持股比例数据
        industry_growth_map: 行业增速映射
        config: 配置
        report_rc_map: 卖方盈利预测一致预期 (ts_code -> dict)
        chip_margin_data: BullScore v2 筹码面+估值安全数据 (来自 get_chip_margin_batch)
        main_bz_data: 主营业务构成 (来自 fina_mainbz)
    """
    ts_code = row['ts_code']
    name = row['name']
    industry = row.get('industry', '')

    income = financial_data.get('income', pd.DataFrame())
    balance = financial_data.get('balance', pd.DataFrame())
    forecast = financial_data.get('forecast', pd.DataFrame())
    cashflow_data = financial_data.get('cashflow', pd.DataFrame())

    if len(income) == 0:
        return None

    # ── 防御性类型转换 ──
    def _ensure_str_cols(df):
        if df is None or len(df) == 0:
            return df
        for col in df.columns:
            if col in ['ts_code', 'ann_date', 'f_ann_date', 'end_date',
                       'report_date', 'update_flag', 'list_date'] or \
               (isinstance(col, str) and 'date' in col.lower() and col not in ['report_type', 'end_type']):
                if not pd.api.types.is_string_dtype(df[col]):
                    try:
                        df[col] = df[col].apply(
                            lambda x: str(int(x)) if pd.notna(x) and float(str(x)).is_integer() else str(x) if pd.notna(x) else ''
                        )
                    except Exception:
                        df[col] = df[col].astype(str)
        return df

    income = _ensure_str_cols(income).sort_values('end_date', ascending=False).reset_index(drop=True)
    balance = _ensure_str_cols(balance).sort_values('end_date', ascending=False).reset_index(drop=True) if len(balance) > 0 else pd.DataFrame()
    cashflow_data = _ensure_str_cols(cashflow_data).sort_values('end_date', ascending=False).reset_index(drop=True) if len(cashflow_data) > 0 else pd.DataFrame()

    # 年度数据
    annual_income = income[income['end_date'].str.endswith('1231')].copy() if 'end_date' in income.columns else income.copy()

    # ── 最新一期 ──
    latest = income.iloc[0]
    latest_revenue = float(latest.get('revenue')) if pd.notna(latest.get('revenue')) else 0.0
    latest_n_income = float(latest.get('n_income')) if pd.notna(latest.get('n_income')) else 0.0
    latest_total_cogs = float(latest.get('total_cogs')) if pd.notna(latest.get('total_cogs')) else 0.0
    latest_rd_exp = float(latest.get('rd_exp')) if pd.notna(latest.get('rd_exp')) else 0.0

    # ── ROE ──
    equity = 0.0
    total_assets = 0.0
    if len(balance) > 0:
        bs_latest = balance.iloc[0]
        equity = float(bs_latest.get('total_hldr_eqy_exc_min_int')) if pd.notna(bs_latest.get('total_hldr_eqy_exc_min_int')) else 0.0
        total_assets = float(bs_latest.get('total_assets')) if pd.notna(bs_latest.get('total_assets')) else 0.0

    roe_current = 0.0
    if equity > 0 and latest_n_income != 0:
        end_date = str(latest.get('end_date', ''))
        if len(end_date) >= 6:
            mm = end_date[4:6]
            annualization = {'12': 1.0, '09': 4.0/3.0, '06': 2.0}.get(mm, 4.0)
            roe_current = (latest_n_income * annualization) / equity
        else:
            roe_current = latest_n_income / equity

    # ── 历史ROE ──
    roe_history = []
    for i in range(min(5, len(annual_income))):
        row_inc = annual_income.iloc[i]
        n_inc = float(row_inc.get('n_income')) if pd.notna(row_inc.get('n_income')) else 0.0
        inc_end = str(row_inc.get('end_date', ''))
        if n_inc == 0:
            continue
        eq_val = 0.0
        if len(balance) > 0:
            mask = balance['end_date'] == inc_end
            if mask.any():
                eq_row = balance[mask].iloc[0]
                eq_val = float(eq_row.get('total_hldr_eqy_exc_min_int')) if pd.notna(eq_row.get('total_hldr_eqy_exc_min_int')) else 0.0
        if eq_val > 0:
            roe_history.append(n_inc / eq_val)

    # ── 毛利率 ──
    gross_margin = 0.0
    if latest_revenue > 0 and latest_total_cogs != 0:
        gross_margin = max((latest_revenue - latest_total_cogs) / latest_revenue, 0.0)

    # ── 研发费用率 ──
    rd_expense_ratio = latest_rd_exp / latest_revenue if latest_revenue > 0 else 0.0

    # ── 营收/利润同比(YoY) ── 优化版v3.1：优先使用Q1数据 + 趋势衰减检测
    # 核心逻辑：年报可能滞后，Q1更能反映当前趋势
    # 如果Q1营收增速低于年报的50%，说明增长趋势在衰减，需要降权

    revenue_yoy = 0.0
    profit_yoy = 0.0
    growth_trend = 'stable'  # stable/rising/falling
    q1_revenue_yoy = None
    q1_profit_yoy = None

    # Step 1: 计算年报同比（作为基准）
    annual_income_sorted = annual_income.sort_values('end_date', ascending=False).reset_index(drop=True)
    annual_revenue_yoy = 0.0
    annual_profit_yoy = 0.0
    if len(annual_income_sorted) >= 2:
        curr = annual_income_sorted.iloc[0]
        curr_rev = float(curr.get('revenue')) if pd.notna(curr.get('revenue')) else 0.0
        curr_profit = float(curr.get('n_income')) if pd.notna(curr.get('n_income')) else 0.0

        prev_year = str(curr.get('end_date', ''))[:4]
        prev_year = str(int(prev_year) - 1) if prev_year.isdigit() else ''
        prev_rows = annual_income_sorted[annual_income_sorted['end_date'].str.startswith(prev_year)]
        if len(prev_rows) > 0:
            prev = prev_rows.iloc[-1]
            prev_rev = float(prev.get('revenue')) if pd.notna(prev.get('revenue')) else 0.0
            prev_profit = float(prev.get('n_income')) if pd.notna(prev.get('n_income')) else 0.0
        else:
            prev_rev, prev_profit = 0.0, 0.0

        if prev_rev > 0:
            annual_revenue_yoy = (curr_rev - prev_rev) / prev_rev
        if prev_profit > 0:
            annual_profit_yoy = (curr_profit - prev_profit) / prev_profit

    # Step 2: 尝试获取Q1同比数据（如果最新报告期是Q1）
    latest_end_date = str(income.iloc[0].get('end_date', ''))
    is_q1_latest = latest_end_date.endswith('0331')

    if is_q1_latest and len(income) >= 2:
        # 找到去年同期Q1数据
        curr_q1 = income.iloc[0]
        curr_q1_rev = float(curr_q1.get('revenue')) if pd.notna(curr_q1.get('revenue')) else 0.0
        curr_q1_profit = float(curr_q1.get('n_income')) if pd.notna(curr_q1.get('n_income')) else 0.0

        # 找上年同期Q1
        curr_year = int(latest_end_date[:4])
        prev_q1_end = f"{curr_year - 1}0331"
        prev_q1_rows = income[income['end_date'] == prev_q1_end]
        if len(prev_q1_rows) > 0:
            prev_q1 = prev_q1_rows.iloc[0]
            prev_q1_rev = float(prev_q1.get('revenue')) if pd.notna(prev_q1.get('revenue')) else 0.0
            prev_q1_profit = float(prev_q1.get('n_income')) if pd.notna(prev_q1.get('n_income')) else 0.0

            if prev_q1_rev > 0:
                q1_revenue_yoy = (curr_q1_rev - prev_q1_rev) / prev_q1_rev
            if prev_q1_profit > 0:
                q1_profit_yoy = (curr_q1_profit - prev_q1_profit) / prev_q1_profit

    # Step 3: 决定使用哪个数据源
    # 优先使用Q1数据，因为更及时
    if q1_revenue_yoy is not None and q1_profit_yoy is not None:
        # 有Q1数据，使用Q1同比
        revenue_yoy = q1_revenue_yoy
        profit_yoy = q1_profit_yoy
        data_source = 'Q1'

        # Q1 vs 年报趋势对比，检测衰减
        if annual_revenue_yoy > 0.3:  # 年报增长>30%
            if q1_revenue_yoy < annual_revenue_yoy * 0.5:
                # Q1增速不到年报的一半，明显衰减
                growth_trend = 'falling'
                logger.debug(f"{ts_code} Q1营收增速衰减: Q1={q1_revenue_yoy*100:.1f}% vs 年报={annual_revenue_yoy*100:.1f}%")
            elif q1_revenue_yoy > annual_revenue_yoy:
                growth_trend = 'rising'
    else:
        # 没有Q1数据，使用年报同比
        revenue_yoy = annual_revenue_yoy
        profit_yoy = annual_profit_yoy
        data_source = 'annual'

    # Step 4: 如果Q1营收同比为负，强制降低利润增速的可信度
    if q1_revenue_yoy is not None and q1_revenue_yoy < 0:
        # Q1营收同比下降，但年报利润暴增 → 低基数效应
        profit_yoy *= 0.5  # 利润增速打折50%
        logger.debug(f"{ts_code} Q1营收负增长({q1_revenue_yoy*100:.1f}%)，利润增速({annual_profit_yoy*100:.1f}%)降权50%")

    # ── 毛利率变化 ──
    gross_margin_change = 0.0
    if len(annual_income_sorted) >= 2 and latest_revenue > 0:
        latest_gm = gross_margin
        prev_row = annual_income_sorted.iloc[1] if len(annual_income_sorted) > 1 else None
        if prev_row is not None:
            prev_rev = float(prev_row.get('revenue')) if pd.notna(prev_row.get('revenue')) else 0.0
            prev_cogs = float(prev_row.get('total_cogs')) if pd.notna(prev_row.get('total_cogs')) else 0.0
            if prev_rev > 0:
                prev_gm = (prev_rev - prev_cogs) / prev_rev
                gross_margin_change = latest_gm - prev_gm

    # ── 合同负债/预付款/存货增速 ──
    contract_liability_yoy = 0.0
    advance_payment_yoy = 0.0
    inventory_turnover_change = 0.0
    fixed_asset_turnover_change = 0.0

    if len(balance) >= 2:
        bal = balance.sort_values('end_date', ascending=False).reset_index(drop=True)
        lat, prv = bal.iloc[0], bal.iloc[1]

        # 合同负债
        cl_c = float(lat.get('contract_liability', 0)) if pd.notna(lat.get('contract_liability')) else 0.0
        cl_p = float(prv.get('contract_liability', 0)) if pd.notna(prv.get('contract_liability')) else 0.0
        if cl_p > 0:
            contract_liability_yoy = (cl_c - cl_p) / cl_p

        # 预付款
        ap_c = float(lat.get('advance_payment', 0)) if pd.notna(lat.get('advance_payment')) else 0.0
        ap_p = float(prv.get('advance_payment', 0)) if pd.notna(prv.get('advance_payment')) else 0.0
        if ap_p > 0:
            advance_payment_yoy = (ap_c - ap_p) / ap_p

        # 存货周转变化
        inv_c = float(lat.get('inventories', 0)) if pd.notna(lat.get('inventories')) else 0.0
        inv_p = float(prv.get('inventories', 0)) if pd.notna(prv.get('inventories')) else 0.0
        curr_inv_turn = latest_revenue / inv_c if inv_c > 0 else 1.0
        prev_rev_p = float(prv.get('revenue')) if pd.notna(prv.get('revenue')) else latest_revenue
        prev_inv_turn = prev_rev_p / inv_p if inv_p > 0 else 1.0
        inventory_turnover_change = (curr_inv_turn - prev_inv_turn) / prev_inv_turn if prev_inv_turn > 0 else 0.0

        # 固定资产周转变化
        fa_c = float(lat.get('fix_assets', 0)) if pd.notna(lat.get('fix_assets')) else 0.0
        fa_p = float(prv.get('fix_assets', 0)) if pd.notna(prv.get('fix_assets')) else 0.0
        curr_fa_turn = latest_revenue / fa_c if fa_c > 0 else 1.0
        prev_fa_turn = prev_rev_p / fa_p if fa_p > 0 else 1.0
        fixed_asset_turnover_change = (curr_fa_turn - prev_fa_turn) / prev_fa_turn if prev_fa_turn > 0 else 0.0

    # ── 资本开支增速 ──
    capex_growth = 0.0
    if len(cashflow_data) >= 2:
        cf = cashflow_data.sort_values('end_date', ascending=False).reset_index(drop=True)
        cap_c = float(cf.iloc[0].get('cap_expend_ra', 0)) if pd.notna(cf.iloc[0].get('cap_expend_ra')) else 0.0
        cap_p = float(cf.iloc[1].get('cap_expend_ra', 0)) if pd.notna(cf.iloc[1].get('cap_expend_ra')) else 0.0
        if cap_p > 0:
            capex_growth = (cap_c - cap_p) / cap_p

    # ── 经营性现金流增速 ──
    cashflow_growth = 0.0
    if len(cashflow_data) >= 2:
        cf = cashflow_data.sort_values('end_date', ascending=False).reset_index(drop=True)
        nocf_c = float(cf.iloc[0].get('net_operate_cash_flow', 0)) if pd.notna(cf.iloc[0].get('net_operate_cash_flow')) else 0.0
        nocf_p = float(cf.iloc[1].get('net_operate_cash_flow', 0)) if pd.notna(cf.iloc[1].get('net_operate_cash_flow')) else 0.0
        if nocf_p > 0:
            cashflow_growth = (nocf_c - nocf_p) / nocf_p
        net_operate_cash_flow = nocf_c
    else:
        net_operate_cash_flow = 0.0

    # ── 季度业绩 ──
    quarterly_net_profit = latest_n_income
    quarterly_net_profit_prev = 0.0
    if len(income) >= 2:
        latest_end = str(latest.get('end_date', ''))
        for j in range(1, len(income)):
            prev_end = str(income.iloc[j].get('end_date', ''))
            if len(latest_end) >= 4 and len(prev_end) >= 4 and latest_end[4:6] == prev_end[4:6]:
                pn = float(income.iloc[j].get('n_income')) if pd.notna(income.iloc[j].get('n_income')) else 0.0
                quarterly_net_profit_prev = pn
                break

    # ── 业绩预告 ──
    forecast_type = ''
    forecast_profit_change = 0.0
    if len(forecast) > 0:
        lf = forecast.sort_values('ann_date', ascending=False).iloc[0]
        forecast_type = str(lf.get('type', '')) if pd.notna(lf.get('type')) else ''
        forecast_profit_change = float(lf.get('profit_change')) if pd.notna(lf.get('profit_change')) else 0.0

    # ── 北向资金 ──
    north_bound_daily_net = 0.0
    north_bound_ratio_change = 0.0
    north_bound_holding_ratio = 0.0
    foreign_holding_ratio = 0.0
    if moneyflow is not None and len(moneyflow) > 0:
        smf = moneyflow[moneyflow['ts_code'] == ts_code].sort_values('trade_date', ascending=False).reset_index(drop=True)
        if len(smf) > 0:
            north_bound_daily_net = float(smf.iloc[0].get('net_mf_amount', 0)) if pd.notna(smf.iloc[0].get('net_mf_amount')) else 0.0
            north_bound_daily_net *= 10000  # 万元→元
            net_5d = sum(
                float(smf.iloc[k].get('net_mf_amount', 0)) if pd.notna(smf.iloc[k].get('net_mf_amount')) else 0.0
                for k in range(min(5, len(smf)))
            )
            north_bound_ratio_change = net_5d * 10000

    # ── 北向资金持股比例 ──
    if north_hold is not None and len(north_hold) > 0:
        nh = north_hold[north_hold['ts_code'] == ts_code]
        if len(nh) > 0:
            north_bound_holding_ratio = float(nh.iloc[0].get('hold_ratio', 0)) if pd.notna(nh.iloc[0].get('hold_ratio')) else 0.0
            foreign_holding_ratio = north_bound_holding_ratio

    # ── 市值 ──
    market_cap = 0.0
    if daily_basic is not None and len(daily_basic) > 0:
        db_row = daily_basic[daily_basic['ts_code'] == ts_code]
        if len(db_row) > 0:
            market_cap = float(db_row.iloc[0].get('total_mv', 0)) if pd.notna(db_row.iloc[0].get('total_mv')) else 0.0
            market_cap *= 10000  # 万元→元

    # ── 价格趋势 ──
    price_trend_score = 0.0
    pct_chg = 0.0
    if daily is not None and len(daily) > 0:
        sd = daily[daily['ts_code'] == ts_code].sort_values('trade_date')
        if len(sd) >= 20:
            prices = sd['close'].values
            ma20 = float(pd.Series(prices).tail(20).mean())
            curr_price = float(sd.iloc[-1]['close'])
            price_trend_score = 1.0 if (ma20 > 0 and curr_price > ma20) else max(0, curr_price / ma20) if ma20 > 0 else 0.0
            pct_chg = float(sd.iloc[-1].get('pct_chg', 0)) if pd.notna(sd.iloc[-1].get('pct_chg')) else 0.0

    # ── 行业增速 ──
    industry_growth = float(industry_growth_map.get(industry, 0.0))

    # ── 产能利用率代理 ──
    capacity_utilization = 1.0
    if len(balance) >= 2:
        bal = balance.sort_values('end_date', ascending=False).reset_index(drop=True)
        lat, prv = bal.iloc[0], bal.iloc[1]
        fa_c = float(lat.get('fix_assets', 0)) if pd.notna(lat.get('fix_assets')) else 0.0
        fa_p = float(prv.get('fix_assets', 0)) if pd.notna(prv.get('fix_assets')) else 0.0
        curr_fa = latest_revenue / fa_c if fa_c > 0 else 1.0
        prev_rev_p = float(prv.get('revenue')) if pd.notna(prv.get('revenue')) else latest_revenue
        prev_fa = prev_rev_p / fa_p if fa_p > 0 else 1.0
        capacity_utilization = curr_fa / prev_fa if prev_fa > 0 else 1.0

    # ── 订单代理增速 ──
    order_proxy_growth = 0.0
    if len(balance) >= 2:
        bal = balance.sort_values('end_date', ascending=False).reset_index(drop=True)
        inv_c = float(bal.iloc[0].get('inventories', 0)) if pd.notna(bal.iloc[0].get('inventories')) else 0.0
        inv_p = float(bal.iloc[1].get('inventories', 0)) if pd.notna(bal.iloc[1].get('inventories')) else 0.0
        inv_g = (inv_c - inv_p) / inv_p if inv_p > 0 else 0.0
        order_proxy_growth = revenue_yoy - inv_g

    # ── 产业链标签 ──
    chain_tag = identify_chain_with_cache(ts_code, name, industry, config or {})

    # ── 卖方盈利预测一致性 (来自 report_rc) ──
    analyst_count = 0
    avg_eps_current_year = 0.0
    avg_eps_next_year = 0.0
    avg_np_current_year = 0.0
    avg_np_next_year = 0.0
    np_growth_current = 0.0
    eps_growth_next = 0.0
    buy_ratio = 0.0
    rating_sentiment = 0.0
    analyst_revision_30d = 0.0
    latest_report_date = ""

    if report_rc_map and ts_code in report_rc_map:
        rc = report_rc_map[ts_code]
        analyst_count = int(rc.get('analyst_count', 0))
        avg_eps_current_year = float(rc.get('avg_eps_current_year', 0.0))
        avg_eps_next_year = float(rc.get('avg_eps_next_year', 0.0))
        avg_np_current_year = float(rc.get('avg_np_current_year', 0.0))
        avg_np_next_year = float(rc.get('avg_np_next_year', 0.0))
        np_growth_current = float(rc.get('np_growth_current', 0.0))
        eps_growth_next = float(rc.get('eps_growth_next', 0.0))
        buy_ratio = float(rc.get('buy_ratio', 0.0))
        rating_sentiment = float(rc.get('rating_sentiment', 0.0))
        analyst_revision_30d = float(rc.get('analyst_revision_30d', 0.0))
        latest_report_date = str(rc.get('latest_report_date', ''))

    # ── BullScore v2: 筹码面数据 ──
    net_inflow_ratio = 0.0
    holder_num_change_ratio = 0.0
    holder_trade_ratio = 0.0
    holder_trade_netbuy = 0
    repurchase_amount = 0.0
    repurchase_ratio = 0.0
    has_repurchase = 0
    fund_holding_ratio = 0.0
    fund_ratio_change = 0.0
    fund_count = 0
    pledge_ratio = 0.0
    pledge_risk_score = 100.0
    unlock_ratio = 0.0
    unlock_risk_score = 100.0
    audit_risk_score = 100.0
    cashflow_ratio = 0.0

    if chip_margin_data:
        mf = chip_margin_data.get('moneyflow', {})
        if mf:
            net_inflow_ratio = float(mf.get('net_inflow_ratio', 0.0))
        hn = chip_margin_data.get('holdernumber', {})
        if hn:
            holder_num_change_ratio = float(hn.get('holder_num_change_ratio', 0.0))
        ht = chip_margin_data.get('holdertrade', {})
        if ht:
            holder_trade_ratio = float(ht.get('holder_trade_ratio', 0.0))
            holder_trade_netbuy = int(ht.get('net_buy', 0))
        rp = chip_margin_data.get('repurchase', {})
        if rp:
            repurchase_amount = float(rp.get('repurchase_amount', 0.0))
            repurchase_ratio = float(rp.get('repurchase_ratio', 0.0))
            has_repurchase = int(rp.get('has_repurchase', 0))
        fp = chip_margin_data.get('fund_portfolio', {})
        if fp:
            fund_holding_ratio = float(fp.get('fund_holding_ratio', 0.0))
            fund_ratio_change = float(fp.get('fund_ratio_change', 0.0))
            fund_count = int(fp.get('fund_count', 0))
        pl = chip_margin_data.get('pledge', {})
        if pl:
            pledge_ratio = float(pl.get('pledge_ratio', 0.0))
            pledge_risk_score = float(pl.get('pledge_risk_score', 100.0))
        sf = chip_margin_data.get('share_float', {})
        if sf:
            unlock_ratio = float(sf.get('unlock_ratio', 0.0))
            unlock_risk_score = float(sf.get('unlock_risk_score', 100.0))
        au = chip_margin_data.get('audit', {})
        if au:
            audit_risk_score = float(au.get('audit_risk_score', 100.0))

    # 经营现金流/营收（用于估值安全）
    if latest_revenue > 0 and net_operate_cash_flow > 0:
        cashflow_ratio = net_operate_cash_flow / latest_revenue

    # 包装为 BullStockData
    data = BullStockData(
        ts_code=ts_code,
        name=name,
        industry=industry,
        chain_tag=chain_tag,
        revenue=latest_revenue,
        net_profit=latest_n_income,
        total_assets=total_assets,
        equity=equity,
        n_income=latest_n_income,
        revenue_yoy=revenue_yoy,
        profit_yoy=profit_yoy,
        gross_margin=gross_margin,
        gross_margin_change=gross_margin_change,
        rd_expense_ratio=rd_expense_ratio,
        roe_current=roe_current,
        roe_history=roe_history,
        contract_liability_yoy=contract_liability_yoy,
        advance_payment_yoy=advance_payment_yoy,
        inventory_turnover_change=inventory_turnover_change,
        order_proxy_growth=order_proxy_growth,
        capex_growth=capex_growth,
        fixed_asset_turnover_change=fixed_asset_turnover_change,
        net_operate_cash_flow=net_operate_cash_flow,
        cashflow_growth=cashflow_growth,
        quarterly_net_profit=quarterly_net_profit,
        quarterly_net_profit_prev=quarterly_net_profit_prev,
        forecast_type=forecast_type,
        forecast_profit_change=forecast_profit_change,
        north_bound_daily_net=north_bound_daily_net,
        north_bound_ratio_change=north_bound_ratio_change,
        north_bound_holding_ratio=north_bound_holding_ratio,
        foreign_holding_ratio=foreign_holding_ratio,
        market_cap=market_cap,
        price_trend_score=price_trend_score,
        pct_chg=pct_chg,
        industry_growth=industry_growth,
        capacity_utilization=capacity_utilization,
        analyst_count=analyst_count,
        avg_eps_current_year=avg_eps_current_year,
        avg_eps_next_year=avg_eps_next_year,
        avg_np_current_year=avg_np_current_year,
        avg_np_next_year=avg_np_next_year,
        np_growth_current=np_growth_current,
        eps_growth_next=eps_growth_next,
        buy_ratio=buy_ratio,
        rating_sentiment=rating_sentiment,
        analyst_revision_30d=analyst_revision_30d,
        latest_report_date=latest_report_date,
        # BullScore v2 新增字段
        net_inflow_ratio=net_inflow_ratio,
        holder_num_change_ratio=holder_num_change_ratio,
        holder_trade_ratio=holder_trade_ratio,
        holder_trade_netbuy=holder_trade_netbuy,
        repurchase_amount=repurchase_amount,
        repurchase_ratio=repurchase_ratio,
        has_repurchase=has_repurchase,
        fund_holding_ratio=fund_holding_ratio,
        fund_ratio_change=fund_ratio_change,
        fund_count=fund_count,
        pledge_ratio=pledge_ratio,
        pledge_risk_score=pledge_risk_score,
        unlock_ratio=unlock_ratio,
        unlock_risk_score=unlock_risk_score,
        audit_risk_score=audit_risk_score,
        cashflow_ratio=cashflow_ratio,
        main_business_items=main_bz_data or [],
        # BullScore v3.1 新增字段
        growth_trend=growth_trend,
        data_source=data_source,
        q1_revenue_yoy=q1_revenue_yoy,
        q1_profit_yoy=q1_profit_yoy,
    )

    # ── 数据完整度评估（v2：区分"数据缺失"与"真实为零"） ──
    # 8 个关键数据维度的可用性检查
    missing_flags = {}
    has_financial = latest_revenue > 0 and latest_n_income != 0
    missing_flags['financial'] = not has_financial
    has_growth = revenue_yoy != 0.0 or profit_yoy != 0.0
    missing_flags['growth'] = not has_growth
    has_profitability = gross_margin > 0 and roe_current > 0
    missing_flags['profitability'] = not has_profitability
    has_rd = rd_expense_ratio > 0
    missing_flags['rd'] = not has_rd
    has_cashflow = cashflow_growth != 0.0 or net_operate_cash_flow != 0.0
    missing_flags['cashflow'] = not has_cashflow
    has_analyst = analyst_count > 0
    missing_flags['analyst'] = not has_analyst
    has_institutional = north_bound_daily_net != 0.0 or fund_holding_ratio > 0
    missing_flags['institutional'] = not has_institutional
    has_chip = net_inflow_ratio != 0.0 or holder_num_change_ratio != 0.0
    missing_flags['chip'] = not has_chip

    n_available = sum(1 for v in missing_flags.values() if not v)
    data_completeness = round(n_available / len(missing_flags) * 100, 1)
    data.data_completeness = data_completeness
    data.data_missing_flags = missing_flags

    return data


def bull_scan(config: Dict, fetcher: DataFetcher) -> List[BullScoreV2Result]:
    """
    BullScore 全市场扫描

    Args:
        config: 配置
        fetcher: 数据获取器

    Returns:
        BullScore 评分结果列表(按 final_score 降序)
    """
    # 准备数据
    stocks, daily, moneyflow, daily_basic, north_hold, concept_map = prepare_stock_data(config, fetcher)

    # 计算行业增速
    industry_growth_map = calculate_industry_growth_map(fetcher, stocks)

    total = len(stocks)
    logger.info(f"开始 BullScore 全市场扫描, 共 {total} 只股票...")

    # ============ 阶段1: 并发拉取财务数据 ============
    ts_code_list = stocks['ts_code'].tolist()
    start_year = str(datetime.now().year - 3)

    logger.info(f"阶段1: 并发拉取 {total} 只股票的财务数据...")
    fetch_start = time.time()
    financial_batch = fetcher.get_stock_financial_batch(ts_code_list, start_year=start_year, max_workers=16)
    logger.info(f"财务数据拉取完成, 共 {len(financial_batch)} 只, 用时 {time.time()-fetch_start:.0f}秒")

    # ============ 阶段1b: 拉取卖方盈利预测一致预期 ============
    logger.info("阶段1b: 拉取卖方盈利预测 (report_rc，按ts_code逐只拉全量历史)...")
    rc_start = time.time()
    # 传入股票列表，让 fetcher 只拉这些股票的研报（有缓存则跳过）
    stock_codes = stocks['ts_code'].tolist()
    report_rc_map = fetcher.get_report_rc_batch(stock_list=stock_codes)
    logger.info(f"卖方预期数据: {len(report_rc_map)} 只股票有研报覆盖, 用时 {time.time()-rc_start:.0f}秒")

    # ============ 阶段2: 提取因子数据（第一轮，无筹码面数据）============
    logger.info("阶段2: 提取因子数据...")
    check_start = time.time()

    all_bull_data: List[BullStockData] = []
    skip_count = 0

    for _, row in stocks.iterrows():
        ts_code = row['ts_code']
        financial_data = financial_batch.get(ts_code, {})
        income = financial_data.get('income', pd.DataFrame())
        if len(income) == 0:
            skip_count += 1
            continue

        bull_data = extract_bull_data(row, financial_data, daily, daily_basic, moneyflow, north_hold, industry_growth_map, config, report_rc_map)
        if bull_data is not None:
            all_bull_data.append(bull_data)
        else:
            skip_count += 1

    logger.info(f"因子提取完成, 有效数据 {len(all_bull_data)} 只, 跳过 {skip_count} 只, 用时 {time.time()-check_start:.0f}秒")

    # ============ 阶段3: BullScore 评分（第一轮）============
    logger.info("阶段3: BullScore 评分...")
    trade_date = fetcher.get_last_trade_date()

    # 基础评分（BullScore v1）
    bull_scorer = _BullScorer(config, fetcher)
    base_results = bull_scorer.compute_all_scores(all_bull_data, trade_date=trade_date)
    logger.info(f"BullScore 基础评分完成: {len(base_results)} 只")

    # BullScore v2.1 增强评分（历史辨识度 + Alpha因子 + 龙头/中军识别）
    logger.info("阶段3b: BullScore v2.1 增强评分（历史辨识度+Alpha因子+龙头识别）...")
    scorer_v2 = BullScorerV2(token=get_token(config))
    results_v2 = scorer_v2.batch_compute(base_results)
    logger.info(f"BullScore v2.1 增强评分完成: {len(results_v2)} 只")

    # 按 final_score 降序排序
    results_v2.sort(key=lambda x: x.final_score, reverse=True)

    logger.info(f"BullScore 评分完成: {len(results_v2)} 只通过评分")
    return results_v2


def supplement_chip_margin_data(results: List[BullScoreV2Result],
                                 fetcher: DataFetcher,
                                 top_n: int = 200) -> List[BullScoreV2Result]:
    """
    BullScore v2: 对通过初筛的股票补充筹码面+估值安全数据

    策略：
      - 只对 final_score >= 70 的股票获取详细筹码数据
      - 限制 top_n 只，避免大量 API 调用
      - 并发获取，每只股票调用 get_chip_margin_batch (含8个接口)
    """
    # 筛选通过初筛的股票
    filtered = [r for r in results if r.final_score >= 70.0]
    if len(filtered) == 0:
        logger.info("无股票通过初筛，跳过筹码面数据补充")
        return results

    target = min(top_n, len(filtered))
    to_supplement = filtered[:target]
    ts_codes = [r.ts_code for r in to_supplement]

    logger.info(f"阶段3b: BullScore v2 筹码面数据补充，对 {target} 只股票获取详细数据...")

    # 并发获取筹码面数据（限制并发数避免超过 API 限制）
    chip_data_map = {}
    semaphore = threading.Semaphore(4)  # 最多4个并发

    def fetch_chip(ts_code):
        with semaphore:
            try:
                return (ts_code, fetcher.get_chip_margin_batch(ts_code))
            except Exception as e:
                logger.warning(f"获取筹码数据失败 {ts_code}: {e}")
                return (ts_code, {})

    start_time = time.time()
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch_chip, tc): tc for tc in ts_codes}
        for f in futures_as_completed(futures):
            tc, data = f.result()
            if data:
                chip_data_map[tc] = data

    elapsed = time.time() - start_time
    logger.info(f"筹码面数据获取完成: {len(chip_data_map)}/{target} 只成功, 用时 {elapsed:.0f}秒")

    # 更新结果中的 chip_score 和 valuation_score
    updated = 0
    for r in results:
        if r.ts_code in chip_data_map:
            chip = chip_data_map[r.ts_code]
            # 在 bull_scorer 中已通过 compute_all_scores 计算过，此处不做二次评分
            # 仅记录原始数据到日志或详情
            updated += 1

    logger.info(f"已为 {updated} 只股票补充筹码面数据")

    return results


def secondary_filter(results: List[BullScoreV2Result]) -> List[BullScoreV2Result]:
    """
    二级精选过滤

    流程:
      1. 基础过滤: final_score >= 85 且 bull_level >= B级成长股
      2. 主题去重: 每个 theme 最多保留 TOP 1~2
      3. 产业链去重: 每个 chain_tag 最多保留 TOP 1
      4. 交易价值过滤: TradeScore >= 75

    Returns:
        精选后的结果列表（最多20只）
    """
    if not results:
        return []

    # ── Step 1: 基础过滤 ──
    BULL_LEVEL_ORDER = {"S+级核心牛股": 6, "S级牛股": 5, "A级产业龙头": 4,
                        "B级成长股": 3, "观察名单": 2, "淘汰": 1}
    min_level = BULL_LEVEL_ORDER.get("B级成长股", 3)

    filtered = [
        r for r in results
        if r.final_score >= 85 and BULL_LEVEL_ORDER.get(r.bull_level, 0) >= min_level
    ]
    if not filtered:
        logger.warning("基础过滤后无合格标的")
        return []

    logger.info(f"Step1-基础过滤: {len(filtered)} 只 (final_score>=85, bull_level>=B级)")

    # ── Step 2: 跳过主题去重（用户要求不限制同一主题的入选数量）──
    theme_deduped = list(filtered)  # 跳过去重，保留全部
    logger.info(f"Step2-主题去重: 跳过 (保持 {len(theme_deduped)} 只)")

    # ── Step 3: 跳过产业链去重（用户要求不限制同一产业链的入选数量）──
    no_dedup = list(theme_deduped)
    logger.info(f"Step3-产业链去重: 跳过 (保持 {len(no_dedup)} 只)")

    # ── Step 4: 交易价值过滤 ──
    # TradeScore = 0.5×expectation_score + 0.3×order_explosion_score + 0.2×marketcap_score
    trade_filtered = []
    for r in no_dedup:
        trade_score = 0.5 * r.expectation_score + 0.3 * r.order_explosion_score + 0.2 * r.marketcap_score
        r.sub_details['trade_score'] = trade_score
        if trade_score >= 75:
            trade_filtered.append(r)

    logger.info(f"Step4-交易价值过滤: {len(trade_filtered)} 只 (TradeScore>=75)")

    # ── 最终排序输出 TOP 20 ──
    trade_filtered.sort(key=lambda x: x.final_score, reverse=True)
    final = trade_filtered[:20]

    logger.info(f"最终输出: {len(final)} 只")

    # ── 额外专项列表（从 original results 中选取） ──
    logger.info("")
    logger.info("=" * 60)
    logger.info("二级精选过滤完成")

    # TOP 5 主题（主题去重后的结果按 theme 分组取最高分）
    theme_list = sorted(theme_deduped, key=lambda x: x.final_score, reverse=True)[:5]
    logger.info("")
    logger.info("▼ TOP 5 主题龙头")
    for i, r in enumerate(theme_list, 1):
        logger.info(f"  {i}. {r.theme or r.chain_tag} | {r.name} {r.ts_code} | final={r.final_score:.1f}")

    # TOP 5 订单爆发最强（从原始 results 中按 order_explosion_score 排序，排除淘汰的）
    order_elite = [r for r in results if r.bull_level != "淘汰"]
    order_elite.sort(key=lambda x: x.order_explosion_score, reverse=True)
    logger.info("")
    logger.info("▼ TOP 5 订单爆发最强")
    for i, r in enumerate(order_elite[:5], 1):
        logger.info(f"  {i}. {r.name} {r.ts_code} {r.theme or r.chain_tag} | 订单爆发={r.order_explosion_score:.1f} | final={r.final_score:.1f}")

    # TOP 5 预期差最大（从原始 results 中按 expectation_score 排序，排除淘汰的）
    expect_elite = [r for r in results if r.bull_level != "淘汰"]
    expect_elite.sort(key=lambda x: x.expectation_score, reverse=True)
    logger.info("")
    logger.info("▼ TOP 5 预期差最大")
    for i, r in enumerate(expect_elite[:5], 1):
        logger.info(f"  {i}. {r.name} {r.ts_code} {r.theme or r.chain_tag} | 预期差={r.expectation_score:.1f} | final={r.final_score:.1f}")

    return final


def save_bull_results(results: List[BullScoreV2Result], config: Dict) -> str:
    """保存 BullScore v2 结果到 CSV"""
    if not results:
        return ""

    bull_scorer = BullScorerV2(token=get_token(config))
    df = bull_scorer.to_dataframe(results)

    output_dir = Path(config.get('output', {}).get('dir', 'output'))
    if not output_dir.is_absolute():
        output_dir = Path(__file__).parent / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"bull_stocks_{timestamp}.csv"
    filepath = output_dir / filename

    df.to_csv(filepath, index=False, encoding='utf-8-sig')
    logger.info(f"结果已保存至: {filepath}")
    return str(filepath)


def print_bull_results(results: List[BullScoreV2Result]) -> None:
    """打印 BullScore v2 结果"""
    if not results:
        logger.info("未筛选出符合条件的股票")
        return

    bull_scorer = BullScorerV2()
    bull_scorer.print_summary(results)


# ==================== 微信推送（PushPlus） ====================

PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN", "9dd91e1aad484af2b77e9e4e7b4bae37")


def push_wechat_notification(all_results, qualified, elite, report_daily_dir):
    """
    通过 PushPlus 推送 BullScore 报告摘要到微信

    Args:
        all_results: 全量评分结果
        qualified: 合格股票(>=70分)
        elite: 精选股票
        report_daily_dir: 报告输出目录
    """
    import requests
    from datetime import datetime

    logger.info("=" * 60)
    logger.info("开始微信推送 (PushPlus)...")

    # 构造推送标题
    trade_date = datetime.now().strftime('%Y-%m-%d')
    title = f"🐂 BullScore 选股日报 {trade_date}"

    # 构造推送内容（Markdown 格式）
    lines = [
        f"## {title}",
        f"> 扫描: {len(all_results)} 只 | 合格: {len(qualified)} 只 | 精选: {len(elite)} 只",
        "",
    ]

    if elite:
        lines.append("### 🏆 TOP 5 精选标的")
        lines.append("| 排名 | 代码 | 名称 | 主题 | Bull分 | 主题分 | Final分 |")
        lines.append("|------|------|------|------|--------|--------|---------|")
        for i, r in enumerate(elite[:5], 1):
            code = r.ts_code.replace('.SH', '').replace('.SZ', '').replace('.BJ', '')
            name = getattr(r, 'name', '')
            theme = getattr(r, 'theme', '')
            bull = getattr(r, 'bull_score', 0)
            t_score = getattr(r, 'theme_score', 0)
            f_score = getattr(r, 'final_score', 0)
            lines.append(f"| {i} | **{code}** | {name} | {theme} | {bull:.1f} | {t_score:.1f} | **{f_score:.1f}** |")
        lines.append("")

    if qualified:
        lines.append("### 📈 各主题龙头分布 (Top 10)")
        lines.append("| 主题 | 龙头 | Final分 |")
        lines.append("|------|------|---------|")
        # 按主题聚合，挑每个主题得分最高的
        theme_top = {}
        for r in qualified:
            theme = getattr(r, 'theme', '')
            if not theme:
                continue
            f_score = getattr(r, 'final_score', 0)
            if theme not in theme_top or f_score > theme_top[theme][1]:
                theme_top[theme] = (getattr(r, 'name', ''), f_score)
        sorted_themes = sorted(theme_top.items(), key=lambda x: x[1][1], reverse=True)[:10]
        for theme, (name, score) in sorted_themes:
            lines.append(f"| {theme} | {name} | {score:.1f} |")
        lines.append("")

    lines.append("### 📁 生成文件")
    lines.append(f"- 全量: `report_daily/bull_stocks_all.csv` ({len(all_results)}只)")
    lines.append(f"- 合格: `report_daily/bull_stocks_qualified.csv` ({len(qualified)}只)")
    lines.append(f"- 精选: `report_daily/bull_stocks_elite.csv` ({len(elite)}只)")
    lines.append("")
    lines.append(f"> 🤖 自动生成于 {datetime.now().strftime('%H:%M:%S')}")

    content = "\n".join(lines)

    # 调用 PushPlus API
    url = "https://www.pushplus.plus/send"
    payload = {
        "token": PUSHPLUS_TOKEN,
        "title": title,
        "content": content,
        "template": "markdown",
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        result = resp.json()
        if result.get('code') == 200:
            logger.info(f"✅ 微信推送成功！请查看公众号 PushPlus 推送加 消息")
            logger.info(f"   推送ID: {result.get('data')}")
        else:
            logger.warning(f"⚠️ 微信推送返回异常: {result.get('msg', result)}")
    except Exception as e:
        logger.warning(f"❌ 微信推送请求失败: {e}")


def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("BullScore 中长线牛股选股系统启动")
    logger.info("=" * 60)

    try:
        config = load_config()
        token = get_token(config)
        logger.info("Tushare Token 已配置")

        fetcher = DataFetcher(token, config)

        # 全市场 BullScore 扫描
        all_results = bull_scan(config, fetcher)

        # ── 一级过滤: 观察名单以上 ──
        qualified = [r for r in all_results if r.final_score >= 60]
        logger.info(f"一级过滤(>=60分): {len(qualified)} 只")

        # ── 二级精选过滤（核心输出） ──
        elite = secondary_filter(all_results)

        # ── 主线归因 + 产业β过滤（新增） ──
        # 对合格标的执行主线归因，剔除周期股，保留AI/科技产业β驱动龙头
        mainline_results = apply_mainline_filter(qualified)

        # ── 输出 ──
        if qualified:
            print_bull_results(qualified)
        else:
            logger.info("未筛选出符合观察名单(>=60分)的股票")

        # ── 保存 ──
        scorer_v2 = BullScorerV2(token=token)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_dir = Path(config.get('output', {}).get('dir', 'output'))
        if not output_dir.is_absolute():
            output_dir = Path(__file__).parent / output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        # 保存主线精选结果（最核心输出）
        if mainline_results:
            mainline_path = output_dir / f"mainline_stocks_{timestamp}.csv"
            scorer_v2.to_dataframe(mainline_results).to_csv(mainline_path, index=False, encoding='utf-8-sig')
            logger.info(f"主线精选结果已保存至: {mainline_path}")

            # 打印完整主线归因分析
            print_mainline_analysis(mainline_results)
        else:
            logger.warning("主线归因过滤后无合格标的，使用二级精选作为保底")

        # 保存二级精选结果（保底/对比用）
        if elite:
            elite_path = output_dir / f"elite_stocks_{timestamp}.csv"
            scorer_v2.to_dataframe(elite).to_csv(elite_path, index=False, encoding='utf-8-sig')
            logger.info(f"二级精选结果已保存至: {elite_path}")

        # 保存全量合格结果(含一级过滤)
        if qualified:
            full_path = output_dir / f"bull_stocks_{timestamp}.csv"
            scorer_v2.to_dataframe(qualified).to_csv(full_path, index=False, encoding='utf-8-sig')
            logger.info(f"全量结果已保存至: {full_path}")

            # 保存所有数据(含淘汰)用于追溯
            if all_results and len(all_results) > len(qualified):
                all_path = output_dir / f"bull_all_{timestamp}.csv"
                scorer_v2.to_dataframe(all_results).to_csv(all_path, index=False, encoding='utf-8-sig')
                logger.info(f"原始全量数据已保存至: {all_path}")

        # ── 固定路径输出：供其他程序调用 ──
        report_daily_dir = Path(__file__).parent.parent / "report_daily"
        report_daily_dir.mkdir(parents=True, exist_ok=True)

        # 全量数据（含淘汰）：固定文件名，每次覆盖
        full_fixed_path = report_daily_dir / "bull_stocks_all.csv"
        if all_results:
            scorer_v2.to_dataframe(all_results).to_csv(full_fixed_path, index=False, encoding='utf-8-sig')
            logger.info(f"全量数据已保存至(固定路径): {full_fixed_path}")

        # 合格数据(>=60分)：固定文件名
        qualified_fixed_path = report_daily_dir / "bull_stocks_qualified.csv"
        if qualified:
            scorer_v2.to_dataframe(qualified).to_csv(qualified_fixed_path, index=False, encoding='utf-8-sig')
            logger.info(f"合格数据已保存至(固定路径): {qualified_fixed_path}")

        # 精选数据：固定文件名
        elite_fixed_path = report_daily_dir / "bull_stocks_elite.csv"
        if elite:
            scorer_v2.to_dataframe(elite).to_csv(elite_fixed_path, index=False, encoding='utf-8-sig')
            logger.info(f"精选数据已保存至(固定路径): {elite_fixed_path}")

        # ── 微信推送（PushPlus） ──
        try:
            push_wechat_notification(all_results, qualified, elite, report_daily_dir)
        except Exception as pe:
            logger.warning(f"微信推送失败: {pe}")

        logger.info("=" * 60)
        logger.info("BullScore 选股完成")

    except Exception as e:
        logger.error(f"程序执行出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
