"""
ELD V2 — Earnings Leader Discovery Engine

主入口：调度数据加载 → 事件过滤 → 多维度评分 → 报告生成

用法:
    python main.py                         # 运行今日
    python main.py --date 20260722          # 运行指定日期
    python main.py --simple                 # 简易模式（跳过相似度和买点）
    python main.py --top 30                 # 输出TOP 30
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

# 确保 eld 包可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ─── 从 .env 加载 token ────────────────
def _load_token() -> str:
    """从环境变量或 config/.env 加载 TUSHARE_TOKEN"""
    token = os.getenv("TUSHARE_TOKEN", "")
    if token:
        return token
    # 尝试从 config/.env 读取
    env_candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", ".env"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config", ".env"),
    ]
    for env_path in env_candidates:
        env_path = os.path.normpath(env_path)
        if os.path.exists(env_path):
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("TUSHARE_TOKEN="):
                        token = line.split("=", 1)[1].strip()
                        if token:
                            os.environ["TUSHARE_TOKEN"] = token
                            return token
    return ""

from eld.config import get_config, Config, reload_config
from eld.cache import EldCache
from eld.datasource import EldDataSource
from eld.models import (
    EldReport, StockBasic, ForecastData, FinancialData, DailyPriceData,
    EventQualityResult, EarningsScoreResult,
)
from eld.final_score import FinalScoreEngine
from eld.report import ReportGenerator
from eld.event_filter import analyze_event_quality
from eld.earnings_score import score_earnings
from eld.institution_score import score_institution
from eld.chip_score import score_chip
from eld.trend_score import score_trend
from eld.industry_score import score_industry
from eld.announcement_score import score_freshness
from eld.expectation_gap import score_expectation_gap
from eld.similarity_engine import SimilarityEngine
from eld.buy_point import analyze_buy_point
from eld.market_score import get_market_score
from eld.utils import setup_logging, get_last_trade_date

logger = logging.getLogger("eld.main")


def parse_args() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="ELD V2 — Earnings Leader Discovery Engine"
    )
    parser.add_argument(
        "--date", "-d", type=str, default=None,
        help="目标日期 YYYYMMDD（默认：最近交易日）",
    )
    parser.add_argument(
        "--simple", action="store_true",
        help="简易模式（跳过相似度和买点分析）",
    )
    parser.add_argument(
        "--top", type=int, default=None,
        help="报告输出前 N 只（默认：配置值）",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="调试模式",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="跳过缓存，强制重新获取数据",
    )
    return parser.parse_args()


def print_banner() -> None:
    """打印启动横幅"""
    banner = r"""
    ╔══════════════════════════════════════════╗
    ║   ELD V2                                ║
    ║   Earnings Leader Discovery Engine       ║
    ║   机构级事件驱动选股系统                   ║
    ╚══════════════════════════════════════════╝
    """
    print(banner)


def run_eld(target_date: Optional[str] = None, simple_mode: bool = False,
            top_n: Optional[int] = None, no_cache: bool = False) -> EldReport:
    """
    运行 ELD 完整流水线

    Args:
        target_date: 目标日期 YYYYMMDD，None 表示最近交易日
        simple_mode: 简易模式（跳过耗时模块）
        top_n: 报告输出数量
        no_cache: 跳过缓存

    Returns:
        EldReport 对象
    """
    cfg = get_config()

    # 覆盖配置
    if target_date:
        cfg.global_.target_date = target_date
    if top_n:
        cfg.report.top_n = top_n

    date_str = target_date or datetime.now().strftime("%Y%m%d")
    logger.info("=" * 60)
    logger.info("ELD V2 启动 | 目标日期: %s | 简易模式: %s", date_str, simple_mode)
    logger.info("=" * 60)

    # ── 1. 初始化基础设施 ──
    logger.info("[1/7] 初始化缓存...")
    cache = EldCache(cfg.cache)
    if no_cache:
        cache.clear_all()

    logger.info("[2/7] 初始化数据源...")
    data_source = EldDataSource(cfg.tushare.token, cache)

    # ── 2. 获取市场状态 ──
    logger.info("[3/7] 获取市场状态...")
    market = get_market_score(data_source)
    logger.info("  市场状态: %s (乘数: %.4f)", market.regime, market.multiplier)

    # ── 3. 获取数据 ──
    logger.info("[4/7] 获取业绩预告数据...")
    forecasts = data_source.get_forecast_all()

    if not forecasts:
        logger.warning("未获取到业绩预告数据，请检查 Tushare 接口和日期")
        empty_report = EldReport()
        empty_report.run_date = date_str
        empty_report.market_regime = str(market.regime)
        return empty_report

    logger.info("  共获取 %d 条业绩预告", len(forecasts))

    # 过滤：只保留中报（end_date 包含 0630）和预增/略增/扭亏
    mid_year_forecasts = []
    for fc in forecasts:
        if "0630" in fc.end_date or "06-30" in fc.end_date:
            if fc.type in ("预增", "略增", "扭亏", "续盈"):
                mid_year_forecasts.append(fc)

    logger.info("  中报预增/略增/扭亏: %d 条", len(mid_year_forecasts))

    if not mid_year_forecasts:
        logger.warning("无符合条件的中报预增数据")
        empty_report = EldReport()
        empty_report.run_date = date_str
        empty_report.market_regime = str(market.regime)
        return empty_report

    # ── 4. 获取各股票详细数据 ──
    logger.info("[5/7] 获取股票详细数据...")
    stock_basics: dict[str, StockBasic] = {}
    financials: dict[str, FinancialData] = {}
    daily_datas: dict[str, list[DailyPriceData]] = {}

    # 全量分析所有中报预增股票
    max_analyze = len(mid_year_forecasts)
    mid_year_forecasts.sort(
        key=lambda x: (x.p_change_min + x.p_change_max) / 2, reverse=True
    )

    for fc in mid_year_forecasts[:max_analyze]:
        ts_code = fc.ts_code
        try:
            sb = data_source.get_stock_basic(ts_code)
            if sb:
                stock_basics[ts_code] = sb

            fin = data_source.get_financial(ts_code)
            if fin:
                financials[ts_code] = fin

            daily = data_source.get_daily_data(
                ts_code,
                start_date=(datetime.now() - timedelta(days=260)).strftime("%Y%m%d"),
                end_date=date_str,
            )
            if daily and len(daily) >= 20:
                daily_datas[ts_code] = daily
        except Exception:
            logger.debug("获取 %s 数据失败", ts_code, exc_info=True)

    logger.info(
        "  股票信息: %d | 财务数据: %d | 日线数据: %d",
        len(stock_basics), len(financials), len(daily_datas),
    )

    # ── 5. 初始化评分引擎 ──
    logger.info("[6/7] 运行评分流水线...")

    similarity_eng = None if simple_mode else SimilarityEngine(data_source)

    engine = FinalScoreEngine(
        config=cfg,
        event_filter=analyze_event_quality,
        earnings_scorer=score_earnings,
        institution_scorer=score_institution,
        chip_scorer=score_chip,
        trend_scorer=score_trend,
        industry_scorer=score_industry,
        freshness_scorer=score_freshness,
        gap_scorer=score_expectation_gap,
        similarity_engine=similarity_eng,
        buy_point_engine=None if simple_mode else analyze_buy_point,
    )

    report = engine.run_pipeline(
        forecasts=mid_year_forecasts[:max_analyze],
        stocks=stock_basics,
        financials=financials,
        daily_data=daily_datas,
        market=market,
        data_source=data_source,
    )
    report.run_date = date_str

    # ── 6. 生成报告 ──
    logger.info("[7/7] 生成报告...")
    rgen = ReportGenerator(cfg)
    outputs = rgen.generate_all(report)

    for fmt, path in outputs.items():
        logger.info("  %s → %s", fmt.upper(), path)

    # 控制台简要输出
    print(f"\n{'='*60}")
    print(f"ELD V2 运行完成 | {date_str}")
    print(f"  扫描: {report.total_stocks} → 通过过滤: {report.filtered_stocks}")
    print(f"  市场状态: {report.market_regime}")
    print(f"  TOP 5:")
    for i, r in enumerate(report.results[:5]):
        print(f"    {i+1}. {r.name} ({r.ts_code}) | ELS={r.els:.1f} | 最终={r.final_score:.1f} | {r.recommendation}")
    print(f"{'='*60}\n")

    return report


def main() -> None:
    """主入口"""
    args = parse_args()

    # 全局配置
    cfg = get_config()

    # 加载 Token
    token = _load_token()
    if not token:
        logger.warning("TUSHARE_TOKEN 未配置，请设置环境变量或 config/.env 文件")
    cfg.tushare.token = token

    if args.debug:
        cfg.global_.debug = True

    setup_logging(
        level="DEBUG" if cfg.global_.debug else cfg.global_.log_level,
        log_file=cfg.global_.log_file,
    )

    # 横幅
    if not cfg.global_.debug:
        print_banner()

    # 确定目标日期
    target_date = args.date or cfg.global_.target_date
    if not target_date:
        target_date = get_last_trade_date()
        logger.info("自动检测最近交易日: %s", target_date)

    # 运行
    start = time.time()
    try:
        report = run_eld(
            target_date=target_date,
            simple_mode=args.simple,
            top_n=args.top,
            no_cache=args.no_cache,
        )
    except KeyboardInterrupt:
        logger.warning("用户中断运行")
        sys.exit(1)
    except Exception:
        logger.exception("ELD 运行异常")
        sys.exit(1)

    elapsed = time.time() - start
    logger.info("总耗时: %.1f 秒 | 评分股票: %d 只", elapsed, report.filtered_stocks)


if __name__ == "__main__":
    main()
