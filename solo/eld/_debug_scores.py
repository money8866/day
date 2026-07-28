"""
调试脚本：检查各维度评分详情
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_env_path = r"D:\mystock\config\.env"
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("TUSHARE_TOKEN="):
                os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("eld.debug")

from eld.config import get_config
from eld.cache import EldCache
from eld.datasource import EldDataSource
from eld.utils import get_last_trade_date

cfg = get_config()
cache = EldCache(cfg.cache)
ds = EldDataSource(cfg.tushare.token, cache)
trade_date = get_last_trade_date()
logger.info("交易日: %s", trade_date)

# ── 1. 市场状态 ──
logger.info("=" * 60)
logger.info("【1】市场状态")
market = ds.get_market_data()
logger.info("  状态: %s", market.regime)
logger.info("  乘数: %.4f", market.multiplier)
logger.info("  风险偏好: %.1f", market.risk_appetite)
for l in market.logic:
    logger.info("    %s", l)

# ── 2. 业绩预告 ──
logger.info("=" * 60)
logger.info("【2】业绩预告")
forecasts = ds.get_forecast_all()
logger.info("  总数: %d", len(forecasts))
min_growth = cfg.event_filter.min_forecast_growth
forecasts = [fc for fc in forecasts
             if fc.p_change_min is not None and fc.p_change_max is not None
             and fc.p_change_max >= min_growth
             and (fc.p_change_min + fc.p_change_max) / 2 >= min_growth]
logger.info("  过滤后: %d (增速≥%.0f%%)", len(forecasts), min_growth)

# 取前5只试评分
sample = forecasts[:5]
logger.info("  样本股票: %s", [f.ts_code for f in sample])

# ── 3. 逐维度评分 ──
logger.info("=" * 60)
logger.info("【3】各维度评分 (前5只)")

from eld.models import FinancialData, DailyPriceData

daily_data_cache = {}
end_date = trade_date
start_date = (__import__('datetime').datetime.strptime(end_date, "%Y%m%d") - __import__('datetime').timedelta(days=280)).strftime("%Y%m%d")

# 加载日线缓存
ds._load_daily_csv_range(start_date, end_date)

for fc in sample:
    ts_code = fc.ts_code
    logger.info("─" * 50)
    logger.info("股票: %s", ts_code)

    # 3a. 日线数据
    dd = ds.get_daily_data(ts_code, start_date, end_date)
    logger.info("  [日线] 获取到 %d 条数据", len(dd))
    if dd:
        dates = [d.trade_date for d in dd]
        logger.info("   日期区间: %s ~ %s (%d天)", dates[0], dates[-1], len(dd))

    # 3b. 财务数据
    fin = ds.get_financial(ts_code)
    if fin:
        logger.info("  [财务] 营收yoy=%.1f%%, 扣非yoy=%.1f%%, ROE=%.1f%%",
                     fin.revenue_yoy, fin.deducted_yoy, fin.roe)
    else:
        logger.info("  [财务] 无数据")

    # 3c. 资金流向
    mf = ds.get_moneyflow(ts_code)
    logger.info("  [资金] 获取到 %d 条资金流向数据", len(mf))
    if mf:
        dates = sorted(set(m.trade_date for m in mf))
        logger.info("   日期区间: %s ~ %s", dates[0], dates[-1])

    # 3d. 筹码
    cyq = ds.get_cyq(ts_code)
    if cyq:
        logger.info("  [筹码] profit_ratio=%.2f%%, avg_cost=%.2f, concentration=%.4f, lockup=%.2f%%",
                     cyq.profit_ratio*100 if cyq.profit_ratio < 1 else cyq.profit_ratio,
                     cyq.avg_cost, cyq.cost_concentration, cyq.lockup_ratio*100 if cyq.lockup_ratio < 1 else cyq.lockup_ratio)
        logger.info("    peak_price=%.2f, peak_strength=%.4f", cyq.peak_price, cyq.peak_strength)
    else:
        logger.info("  [筹码] 无数据")

    # 3e. 行业评分
    from eld.industry_score import score_industry
    ind_r = score_industry(ts_code, ds)
    logger.info("  [行业] score=%.1f, rank=%s", ind_r.score, ind_r.industry_rank)

    # 3f. 基准数据
    bench = ds.get_benchmark_daily(ts_code)
    logger.info("  [基准] 获取到 %d 条", len(bench))

    # 3g. 行业指数
    ind_daily = ds.get_industry_daily(ts_code)
    logger.info("  [行业指数] 获取到 %d 条", len(ind_daily))

    # 3h. 北向资金
    hk = ds.get_hk_hold(ts_code)
    logger.info("  [北向] 获取到 %d 条", len(hk))

    # 3i. 基金持仓
    fund = ds.get_fund_hold(ts_code)
    logger.info("  [基金] 获取到 %d 条", len(fund))

logger.info("=" * 60)
logger.info("调试完成")
