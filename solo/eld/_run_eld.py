"""
ELD V2 完整运行脚本

全流程：数据获取 → 评分 → 报告生成 → 微信推送
支持每日 17:00 定时自动运行（非交易日自动跳过）。
"""
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 加载 token
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
logger = logging.getLogger("eld.run")

# ── 交易日守卫 ──
# 定时任务每日 17:00 触发，非交易日直接跳过
_now = datetime.now()
_today_str = _now.strftime("%Y%m%d")
try:
    import tushare as ts
    _pro = ts.pro_api(os.getenv("TUSHARE_TOKEN", ""))
    _cal_df = _pro.trade_cal(exchange="SSE", start_date=_today_str, end_date=_today_str)
    if _cal_df is not None and len(_cal_df) > 0:
        _today_open = _cal_df.iloc[0]["is_open"] == 1
        if not _today_open and _now.hour >= 16:
            logger.info("今日非交易日（%s），自动跳过运行。", _today_str)
            sys.exit(0)
except Exception:
    # 兜底：周末直接跳过
    if _now.weekday() >= 5:
        logger.info("今日为周末（%s），自动跳过运行。", _today_str)
        sys.exit(0)

from eld.config import get_config
from eld.cache import EldCache
from eld.datasource import EldDataSource
from eld.utils import get_last_trade_date
from eld.event_filter import analyze_event_quality
from eld.earnings_score import score_earnings
from eld.institution_score import score_institution
from eld.chip_score import score_chip
from eld.trend_score import score_trend
from eld.industry_score import score_industry
from eld.announcement_score import score_freshness
from eld.expectation_gap import score_expectation_gap, calc_expectation_gap
from eld.buy_point import analyze_buy_point
from eld.institution_accumulation import calc_institution_accumulation
from eld.earnings_buy_point import detect_earnings_pullback
from eld.final_score import FinalScoreEngine
from eld.report import ReportGenerator

logger.info("=" * 60)
logger.info("ELD V2 完整运行开始")
logger.info("=" * 60)

# ── 初始化 ──
cfg = get_config()
cache = EldCache(cfg.cache)
ds = EldDataSource(cfg.tushare.token, cache)
trade_date = get_last_trade_date()
logger.info("交易日: %s", trade_date)

# ── 1. 获取业绩预告 ──
logger.info("Step 1: 获取业绩预告...")
forecasts = ds.get_forecast_all()
logger.info("  获取到 %d 条业绩预告", len(forecasts))

# ── 1b. 过滤预告利润增速 ──
min_growth = cfg.event_filter.min_forecast_growth
before = len(forecasts)
forecasts = [fc for fc in forecasts
             if fc.p_change_min is not None and fc.p_change_max is not None
             and fc.p_change_max >= min_growth
             and (fc.p_change_min + fc.p_change_max) / 2 >= min_growth]
logger.info("  Step 1b: 预告利润增速过滤 %d → %d (阈值≥%.0f%%)", before, len(forecasts), min_growth)

# ── 2. 获取股票基本信息 ──
logger.info("Step 2: 获取股票基本信息...")
stocks: dict[str, StockBasic] = {}
from eld.models import StockBasic

stock_basic_df = ds._load_stock_basic_csv()
if stock_basic_df is not None:
    for fc in forecasts:
        mask = stock_basic_df["ts_code"] == fc.ts_code
        row = stock_basic_df[mask]
        if len(row) > 0:
            r = row.iloc[0]
            stocks[fc.ts_code] = StockBasic(
                ts_code=str(r.get("ts_code", fc.ts_code)),
                name=str(r.get("name", "")),
                industry=str(r.get("industry", "")),
                area="",
                market="",
            )
logger.info("  获取到 %d 只股票基本信息", len(stocks))

# ── 3. 获取财务数据 ──
logger.info("Step 3: 获取财务数据...")
financials: dict[str, FinancialData] = {}
from eld.models import FinancialData

for i, fc in enumerate(forecasts):
    if fc.ts_code not in stocks:
        continue
    fin = ds.get_financial(fc.ts_code)
    if fin is not None:
        financials[fc.ts_code] = fin
    if (i + 1) % 200 == 0:
        logger.info("  进度: %d/%d", i + 1, len(forecasts))

logger.info("  获取到 %d 只股票财务数据", len(financials))

# ── 4. 获取日线数据 ──
logger.info("Step 4: 获取日线数据...")
end = trade_date
start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=280)).strftime("%Y%m%d")
daily_data: dict[str, list[DailyPriceData]] = {}
from eld.models import DailyPriceData

# 先批量加载 cache_daily CSV 到内存
ds._load_daily_csv_range(start, end)

for i, fc in enumerate(forecasts):
    if fc.ts_code not in stocks:
        continue
    dd = ds.get_daily_data(fc.ts_code, start, end)
    if dd and len(dd) > 0:
        daily_data[fc.ts_code] = dd
    if (i + 1) % 200 == 0:
        logger.info("  进度: %d/%d", i + 1, len(forecasts))

logger.info("  获取到 %d 只股票日线数据", len(daily_data))

# ── 5. 市场评分 ──
logger.info("Step 5: 获取市场评分...")
market = ds.get_market_data()
logger.info("  市场状态: %s (乘数: %.2f)", market.regime, market.multiplier)

# ── 6. 创建评分引擎并运行流水线 ──
logger.info("Step 6: 运行评分流水线...")
engine = FinalScoreEngine(
    cfg,
    # V2 模块默认使用内置实现
    expectation_gap_v2_engine=calc_expectation_gap,
    institution_accumulation_engine=calc_institution_accumulation,
    earnings_buy_point_engine=detect_earnings_pullback,
)
report = engine.run_pipeline(forecasts, stocks, financials, daily_data, market, ds)

# ── 7. 生成报告 ──
logger.info("Step 7: 生成报告...")
rg = ReportGenerator(cfg)

# 设置 target_date
if cfg.global_.target_date is None:
    cfg.global_.target_date = trade_date

outputs = rg.generate_all(report)

# ── 输出概要 ──
logger.info("=" * 60)
logger.info("ELD V2 运行完成")
logger.info("  扫描股票: %d", report.total_stocks)
logger.info("  通过过滤: %d", report.filtered_stocks)
logger.info("  市场状态: %s", report.market_regime)
for fmt, path in outputs.items():
    logger.info("  %s: %s", fmt, path)

# 打印 TOP 10（V2 评分）
logger.info("")
logger.info("TOP 10 结果 (ELD V2):")
logger.info("%-4s %-12s %-8s %-6s %-6s %-6s %-8s %-8s %s",
            "排名", "代码", "名称", "行业", "V2分", "V1分", "预期差V2", "机构吸筹", "买点信号")
for i, r in enumerate(report.results[:10]):
    logger.info("%-4d %-12s %-8s %-6s %-6.1f %-6.1f %-8.0f %-8.0f %s",
                r.rank, r.ts_code, r.name, r.industry,
                r.final_score_v2, r.final_score,
                r.expectation_gap_v2_score, r.institution_accumulation_score,
                r.earnings_buy_signal)

# ── 8. 微信推送 ──
# 支持 --no-push 跳过推送（调试用），其余 argv 保留给推送脚本传日期
_no_push = "--no-push" in sys.argv
if _no_push:
    sys.argv = [a for a in sys.argv if a != "--no-push"]
logger.info("")
if _no_push:
    logger.info("Step 8: 微信推送 (已跳过 --no-push)")
else:
    logger.info("Step 8: 微信推送...")
    try:
        from eld._push_eld_theme import main as push_main
        push_main()
        logger.info("  微信推送完成")
    except Exception as e:
        logger.warning("  微信推送失败: %s", e)
