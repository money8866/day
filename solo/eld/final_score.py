"""
ELD V2 最终评分模块

ELS = Σ(维度评分 × 权重) × 市场乘数

组合所有评分维度，应用市场乘数，生成最终排序。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from .config import Config, get_config
from .constants import (
    DIM_EVENT_QUALITY,
    DIM_EARNINGS,
    DIM_INSTITUTION,
    DIM_CHIP,
    DIM_TREND,
    DIM_INDUSTRY,
    DIM_FRESHNESS,
    DIM_EXPECTATION_GAP,
    DIM_SIMILARITY,
    ALL_DIMENSIONS,
)
from .models import (
    EldReport,
    FinalScoreResult,
    MarketScoreResult,
    EventQualityResult,
    EarningsScoreResult,
    InstitutionScoreResult,
    ChipScoreResult,
    TrendScoreResult,
    IndustryScoreResult,
    FreshnessScoreResult,
    ExpectationGapResult,
    SimilarityResult,
    BuyPointResult,
    StockBasic,
    ForecastData,
    FinancialData,
    DailyPriceData,
)
from .market_score import get_market_score

logger = logging.getLogger("eld.final_score")


class FinalScoreEngine:
    """最终评分引擎——协调所有评分模块"""

    def __init__(
        self,
        config: Optional[Config] = None,
        event_filter=None,
        earnings_scorer=None,
        institution_scorer=None,
        chip_scorer=None,
        trend_scorer=None,
        industry_scorer=None,
        freshness_scorer=None,
        gap_scorer=None,
        similarity_engine=None,
        buy_point_engine=None,
    ):
        self.cfg = config or get_config()
        self.fc = self.cfg.final_score

        # 注入评分模块（不直接 import 以保持低耦合）
        self._event_filter = event_filter
        self._earnings_scorer = earnings_scorer
        self._institution_scorer = institution_scorer
        self._chip_scorer = chip_scorer
        self._trend_scorer = trend_scorer
        self._industry_scorer = industry_scorer
        self._freshness_scorer = freshness_scorer
        self._gap_scorer = gap_scorer
        self._similarity_engine = similarity_engine
        self._buy_point_engine = buy_point_engine

    def compute_els(
        self,
        event_result: EventQualityResult,
        earnings_result: EarningsScoreResult,
        institution_result: InstitutionScoreResult,
        chip_result: ChipScoreResult,
        trend_result: TrendScoreResult,
        industry_result: IndustryScoreResult,
        freshness_result: FreshnessScoreResult,
        gap_result: ExpectationGapResult,
        similarity_result: SimilarityResult,
    ) -> float:
        """
        计算 ELS (Earnings Leader Score)

        ELS = event×25% + earnings×20% + institution×15% + chip×10%
              + trend×10% + industry×5% + freshness×5% + expectation_gap×5% + similarity×5%

        Returns:
            0-100 的 ELS 分数
        """
        scores = {
            DIM_EVENT_QUALITY: event_result.score,
            DIM_EARNINGS: earnings_result.score,
            DIM_INSTITUTION: institution_result.score,
            DIM_CHIP: chip_result.score,
            DIM_TREND: trend_result.score,
            DIM_INDUSTRY: industry_result.score,
            DIM_FRESHNESS: freshness_result.score,
            DIM_EXPECTATION_GAP: gap_result.score,
            DIM_SIMILARITY: similarity_result.score,
        }

        weights = {
            DIM_EVENT_QUALITY: self.fc.event_quality_weight,
            DIM_EARNINGS: self.fc.earnings_weight,
            DIM_INSTITUTION: self.fc.institution_weight,
            DIM_CHIP: self.fc.chip_weight,
            DIM_TREND: self.fc.trend_weight,
            DIM_INDUSTRY: self.fc.industry_weight,
            DIM_FRESHNESS: self.fc.freshness_weight,
            DIM_EXPECTATION_GAP: self.fc.expectation_gap_weight,
            DIM_SIMILARITY: self.fc.similarity_weight,
        }

        total_weight = sum(weights.values())
        if abs(total_weight - 1.0) > 0.001:
            logger.warning("ELS weights sum to %.4f, normalizing", total_weight)
            for k in weights:
                weights[k] /= total_weight

        els = sum(scores[d] * weights[d] for d in ALL_DIMENSIONS)
        els = max(0.0, min(100.0, els))

        logger.debug(
            "ELS=%.2f | event=%.1f earnings=%.1f inst=%.1f chip=%.1f "
            "trend=%.1f ind=%.1f fresh=%.1f gap=%.1f sim=%.1f",
            els, scores[DIM_EVENT_QUALITY], scores[DIM_EARNINGS],
            scores[DIM_INSTITUTION], scores[DIM_CHIP], scores[DIM_TREND],
            scores[DIM_INDUSTRY], scores[DIM_FRESHNESS],
            scores[DIM_EXPECTATION_GAP], scores[DIM_SIMILARITY],
        )

        return els

    def apply_market_multiplier(self, els: float, market: MarketScoreResult) -> float:
        """
        应用市场乘数

        Final = ELS × MarketMultiplier
        """
        final = els * market.multiplier
        final = max(0.0, min(100.0, final))
        logger.debug(
            "ELS=%.2f × multiplier=%.4f(%s) = Final=%.2f",
            els, market.multiplier, market.regime.value if hasattr(market.regime, 'value') else market.regime, final,
        )
        return final

    def generate_recommendation(self, final_score: float, buy_point: Optional[BuyPointResult]) -> str:
        """
        根据最终分数和买点生成建议

        Args:
            final_score: 最终评分 (0-100)
            buy_point: 买点结果

        Returns:
            建议文本
        """
        if final_score >= 85:
            base = "强烈买入"
        elif final_score >= 70:
            base = "买入"
        elif final_score >= 55:
            base = "关注"
        elif final_score >= 40:
            base = "观望"
        else:
            base = "回避"

        if buy_point and buy_point.stars_int >= 4:
            return f"{base} · 当前买点极佳({buy_point.state.value})"
        elif buy_point and buy_point.stars_int >= 3:
            return f"{base} · 买点尚可({buy_point.state.value})"

        return base

    def score_single_stock(
        self,
        stock: StockBasic,
        forecast: ForecastData,
        financial: FinancialData,
        daily_data: list[DailyPriceData],
        market: MarketScoreResult,
        data_source,
    ) -> FinalScoreResult:
        """对单只股票进行完整评分链"""
        result = FinalScoreResult()
        result.ts_code = stock.ts_code
        result.name = stock.name
        result.industry = stock.industry
        result.announce_date = forecast.announce_date
        result.forecast_pct = (forecast.p_change_min + forecast.p_change_max) / 2

        # Stage 2: 事件质量
        if self._event_filter:
            event_r = self._event_filter(forecast, financial, self.cfg.event_filter)
        else:
            from .event_filter import analyze_event_quality
            event_r = analyze_event_quality(forecast, financial, self.cfg.event_filter)
        result.event_quality_score = event_r.score
        result.event_detail = event_r

        # Stage 3: 基本面
        if self._earnings_scorer:
            earn_r = self._earnings_scorer(financial, self.cfg.earnings)
        else:
            from .earnings_score import score_earnings
            earn_r = score_earnings(financial, self.cfg.earnings)
        result.earnings_score = earn_r.score
        result.earnings_detail = earn_r

        # Stage 4: 机构资金
        if self._institution_scorer:
            inst_r = self._institution_scorer(stock.ts_code, data_source, self.cfg.institution)
        else:
            from .institution_score import score_institution
            inst_r = score_institution(stock.ts_code, data_source, self.cfg.institution)
        result.institution_score = inst_r.score
        result.institution_detail = inst_r

        # Stage 5: 筹码
        if self._chip_scorer:
            chip_r = self._chip_scorer(stock.ts_code, data_source, self.cfg.chip)
        else:
            from .chip_score import score_chip
            chip_r = score_chip(stock.ts_code, data_source, self.cfg.chip)
        result.chip_score = chip_r.score
        result.chip_detail = chip_r

        # Stage 6: 趋势
        if self._trend_scorer:
            trend_r = self._trend_scorer(stock.ts_code, daily_data, data_source, self.cfg.trend)
        else:
            from .trend_score import score_trend
            trend_r = score_trend(stock.ts_code, daily_data, data_source, self.cfg.trend)
        result.trend_score = trend_r.score
        result.trend_detail = trend_r

        # Stage 7: 行业
        if self._industry_scorer:
            ind_r = self._industry_scorer(stock.ts_code, data_source)
        else:
            from .industry_score import score_industry
            ind_r = score_industry(stock.ts_code, data_source)
        result.industry_score = ind_r.score
        result.industry_detail = ind_r

        # Stage 8: 公告时效
        if self._freshness_scorer:
            fresh_r = self._freshness_scorer(forecast.announce_date)
        else:
            from .announcement_score import score_freshness
            fresh_r = score_freshness(forecast.announce_date)
        result.freshness_score = fresh_r.score
        result.freshness_detail = fresh_r

        # Stage 9: 预期差
        if self._gap_scorer:
            gap_r = self._gap_scorer(stock.ts_code, data_source)
        else:
            from .expectation_gap import score_expectation_gap
            gap_r = score_expectation_gap(stock.ts_code, data_source)
        result.expectation_gap_score = gap_r.score
        result.expectation_gap_detail = gap_r

        # Stage 10: 历史相似度
        stock_data = {
            "market_cap": financial.roe * 100,  # proxy
            "forecast_pct": result.forecast_pct,
            "roe": financial.roe,
            "ocf_ratio": financial.ocf_ratio,
            "industry": stock.industry,
        }
        if self._similarity_engine:
            sim_r = self._similarity_engine.compute_similarity(stock.ts_code, stock_data)
        else:
            from .similarity_engine import SimilarityEngine
            sim_eng = SimilarityEngine(data_source)
            sim_r = sim_eng.compute_similarity(stock.ts_code, stock_data)
        result.similarity_score = sim_r.score
        result.similarity_detail = sim_r

        # Stage 11: 买点
        if self._buy_point_engine:
            bp_r = self._buy_point_engine(stock.ts_code, daily_data, chip_r, trend_r)
        else:
            from .buy_point import analyze_buy_point
            bp_r = analyze_buy_point(stock.ts_code, daily_data, chip_r, trend_r)
        result.buy_point_detail = bp_r

        # Stage 12: 最终评分
        result.els = self.compute_els(
            event_r, earn_r, inst_r, chip_r, trend_r,
            ind_r, fresh_r, gap_r, sim_r,
        )
        result.final_score = self.apply_market_multiplier(result.els, market)
        result.recommendation = self.generate_recommendation(result.final_score, bp_r)

        return result

    def run_pipeline(
        self,
        forecasts: list[ForecastData],
        stocks: dict[str, StockBasic],
        financials: dict[str, FinancialData],
        daily_data: dict[str, list[DailyPriceData]],
        market: MarketScoreResult,
        data_source,
    ) -> EldReport:
        """
        运行完整评分流水线

        Args:
            forecasts: 业绩预告列表
            stocks: 股票信息 {ts_code: StockBasic}
            financials: 财务数据 {ts_code: FinancialData}
            daily_data: 日线数据 {ts_code: [DailyPriceData]}
            market: 市场评分
            data_source: 数据源

        Returns:
            EldReport 报告
        """
        report = EldReport()
        report.market_regime = market.regime.value if hasattr(market.regime, 'value') else str(market.regime)
        report.total_stocks = len(forecasts)

        results: list[FinalScoreResult] = []
        max_workers = self.cfg.global_.max_workers

        logger.info("Running ELD pipeline for %d stocks with %d workers", len(forecasts), max_workers)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {}
            for fc in forecasts:
                ts_code = fc.ts_code
                if ts_code not in stocks:
                    logger.warning("Stock %s not found in basic info, skipping", ts_code)
                    continue
                if ts_code not in financials:
                    logger.warning("Financial data for %s not found, skipping", ts_code)
                    continue
                if ts_code not in daily_data:
                    logger.warning("Daily data for %s not found, skipping", ts_code)
                    continue

                future = executor.submit(
                    self.score_single_stock,
                    stocks[ts_code],
                    fc,
                    financials[ts_code],
                    daily_data.get(ts_code, []),
                    market,
                    data_source,
                )
                future_map[future] = ts_code

            for future in as_completed(future_map):
                ts_code = future_map[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception:
                    logger.exception("Failed to score %s", ts_code)

        # 按最终评分排序
        results.sort(key=lambda r: r.final_score, reverse=True)
        for i, r in enumerate(results):
            r.rank = i + 1

        report.filtered_stocks = len(results)
        report.results = results

        logger.info(
            "Pipeline complete: %d/%d stocks scored",
            len(results), report.total_stocks,
        )

        return report
