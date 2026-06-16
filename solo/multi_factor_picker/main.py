"""
BullScore 中长线牛股选股主程序

基于产业景气 + 订单验证 + 龙头地位 + 业绩质量 + 预期差 框架，
寻找未来1~3年有机会上涨200%以上的A股中长线牛股。

评分结构：
  BullScore =
    0.25 × IndustryDemandScore + 0.15 × TechBarrierScore
  + 0.15 × OrderExplosionScore + 0.15 × EarningsQualityScore
  + 0.10 × LeaderScore + 0.10 × ExpectationScore
  + 0.05 × InstitutionScore + 0.05 × MarketCapElasticity

  FinalScore = 0.80 × BullScore + 0.20 × ThemeScore
"""
import os
import sys
import time
import json
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import pandas as pd
import numpy as np
from loguru import logger

from data_fetcher import DataFetcher
from bull_scorer import BullScorer, BullStockData, BullScoreResult, fetch_theme_scores_from_db, chain_to_theme
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


def prepare_stock_data(config: Dict, fetcher: DataFetcher) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, Dict]:
    """
    准备全市场股票数据

    Returns:
        (stock_list, daily_data, moneyflow_data, daily_basic_data, concept_map)
    """
    logger.info("获取股票列表...")
    stocks = fetcher.get_stock_list(list_status='L')

    # 排除ST
    if config.get('universe', {}).get('exclude_st', True):
        stocks = stocks[~stocks['name'].str.contains('ST', na=False)]

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

    return stocks, daily, moneyflow, daily_basic, concept_map


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
                       industry_growth_map: Dict,
                       config: Dict = None) -> Optional[BullStockData]:
    """
    从原始数据提取 BullScore 所需数据

    Args:
        row: 股票基本信息行
        financial_data: 财务数据字典(income/balance/forecast/cashflow)
        daily: 日线行情
        daily_basic: 每日基本面(含市值)
        moneyflow: 大单资金流
        industry_growth_map: 行业增速映射
        config: 配置

    Returns:
        BullStockData or None (若无财务数据)
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

    # ── 营收/利润同比(YoY) ──
    annual_income_sorted = annual_income.sort_values('end_date', ascending=False).reset_index(drop=True)
    revenue_yoy = 0.0
    profit_yoy = 0.0
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
            revenue_yoy = (curr_rev - prev_rev) / prev_rev
        if prev_profit > 0:
            profit_yoy = (curr_profit - prev_profit) / prev_profit

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
        market_cap=market_cap,
        price_trend_score=price_trend_score,
        pct_chg=pct_chg,
        industry_growth=industry_growth,
        capacity_utilization=capacity_utilization,
    )
    return data


def bull_scan(config: Dict, fetcher: DataFetcher) -> List[BullScoreResult]:
    """
    BullScore 全市场扫描

    Args:
        config: 配置
        fetcher: 数据获取器

    Returns:
        BullScore 评分结果列表(按 final_score 降序)
    """
    # 准备数据
    stocks, daily, moneyflow, daily_basic, concept_map = prepare_stock_data(config, fetcher)

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

    # ============ 阶段2: 提取因子数据 ============
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

        bull_data = extract_bull_data(row, financial_data, daily, daily_basic, moneyflow, industry_growth_map, config)
        if bull_data is not None:
            all_bull_data.append(bull_data)
        else:
            skip_count += 1

    logger.info(f"因子提取完成, 有效数据 {len(all_bull_data)} 只, 跳过 {skip_count} 只, 用时 {time.time()-check_start:.0f}秒")

    # ============ 阶段3: BullScore 评分 ============
    logger.info("阶段3: BullScore 评分...")
    trade_date = fetcher.get_last_trade_date()

    bull_scorer = BullScorer(config, fetcher)
    results = bull_scorer.compute_all_scores(all_bull_data, trade_date=trade_date)

    logger.info(f"BullScore 评分完成: {len(results)} 只通过评分")
    return results


def secondary_filter(results: List[BullScoreResult]) -> List[BullScoreResult]:
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

    # ── Step 2: 主题去重 ──
    # 为每个 theme 计算综合排序分: 0.5×final_score + 0.3×expectation_score + 0.2×order_explosion_score
    for r in filtered:
        r.sub_details['theme_sort_score'] = (
            0.5 * r.final_score + 0.3 * r.expectation_score + 0.2 * r.order_explosion_score
        )

    # 按 theme 分组，每组取 TOP 1（如有并列最多取 TOP 2）
    theme_groups: Dict[str, List[BullScoreResult]] = {}
    for r in filtered:
        t = r.theme if r.theme else "__其他__"
        theme_groups.setdefault(t, []).append(r)

    theme_deduped = []
    for t, group in theme_groups.items():
        group.sort(key=lambda x: x.sub_details.get('theme_sort_score', 0), reverse=True)
        keep = min(len(group), 1)  # 默认 TOP 1
        # 如果第二名的 score 与第一名相差不到 3 分，则保留 TOP 2
        if len(group) >= 2 and (group[0].sub_details.get('theme_sort_score', 0) -
                                 group[1].sub_details.get('theme_sort_score', 0) < 3):
            keep = min(len(group), 2)
        theme_deduped.extend(group[:keep])

    logger.info(f"Step2-主题去重: {len(theme_deduped)} 只 (原 {len(filtered)} 只)")

    # ── Step 3: 产业链去重 ──
    # 按 chain_tag 分组，每组保留 TOP 1
    chain_groups: Dict[str, List[BullScoreResult]] = {}
    for r in theme_deduped:
        ct = r.chain_tag if r.chain_tag else "__其他__"
        chain_groups.setdefault(ct, []).append(r)

    chain_deduped = []
    for ct, group in chain_groups.items():
        group.sort(key=lambda x: x.final_score, reverse=True)
        chain_deduped.append(group[0])  # 每个 chain 只保留 1 只

    logger.info(f"Step3-产业链去重: {len(chain_deduped)} 只 (原 {len(theme_deduped)} 只)")

    # ── Step 4: 交易价值过滤 ──
    # TradeScore = 0.5×expectation_score + 0.3×order_explosion_score + 0.2×marketcap_score
    trade_filtered = []
    for r in chain_deduped:
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


def save_bull_results(results: List[BullScoreResult], config: Dict) -> str:
    """保存 BullScore 结果到 CSV"""
    if not results:
        return ""

    bull_scorer = BullScorer(config)
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


def print_bull_results(results: List[BullScoreResult]) -> None:
    """打印 BullScore 结果"""
    if not results:
        logger.info("未筛选出符合条件的股票")
        return

    bull_scorer = BullScorer()
    bull_scorer.print_summary(results)


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
        qualified = [r for r in all_results if r.final_score >= 70]
        logger.info(f"一级过滤(>=70分): {len(qualified)} 只")

        # ── 二级精选过滤（核心输出） ──
        elite = secondary_filter(all_results)

        # ── 主线归因 + 产业β过滤（新增） ──
        # 对合格标的执行主线归因，剔除周期股，保留AI/科技产业β驱动龙头
        mainline_results = apply_mainline_filter(qualified)

        # ── 输出 ──
        if qualified:
            print_bull_results(qualified)
        else:
            logger.info("未筛选出符合观察名单(>=70分)的股票")

        # ── 保存 ──
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_dir = Path(config.get('output', {}).get('dir', 'output'))
        if not output_dir.is_absolute():
            output_dir = Path(__file__).parent / output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        # 保存主线精选结果（最核心输出）
        if mainline_results:
            mainline_path = output_dir / f"mainline_stocks_{timestamp}.csv"
            BullScorer(config).to_dataframe(mainline_results).to_csv(mainline_path, index=False, encoding='utf-8-sig')
            logger.info(f"主线精选结果已保存至: {mainline_path}")

            # 打印完整主线归因分析
            print_mainline_analysis(mainline_results)
        else:
            logger.warning("主线归因过滤后无合格标的，使用二级精选作为保底")

        # 保存二级精选结果（保底/对比用）
        if elite:
            elite_path = output_dir / f"elite_stocks_{timestamp}.csv"
            BullScorer(config).to_dataframe(elite).to_csv(elite_path, index=False, encoding='utf-8-sig')
            logger.info(f"二级精选结果已保存至: {elite_path}")

        # 保存全量合格结果(含一级过滤)
        if qualified:
            full_path = output_dir / f"bull_stocks_{timestamp}.csv"
            BullScorer(config).to_dataframe(qualified).to_csv(full_path, index=False, encoding='utf-8-sig')
            logger.info(f"全量结果已保存至: {full_path}")

            # 保存所有数据(含淘汰)用于追溯
            if all_results and len(all_results) > len(qualified):
                all_path = output_dir / f"bull_all_{timestamp}.csv"
                BullScorer(config).to_dataframe(all_results).to_csv(all_path, index=False, encoding='utf-8-sig')
                logger.info(f"原始全量数据已保存至: {all_path}")

        logger.info("=" * 60)
        logger.info("BullScore 选股完成")

    except Exception as e:
        logger.error(f"程序执行出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
