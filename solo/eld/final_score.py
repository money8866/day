"""
ELD V2 最终评分模块

ELS (V1) = Σ(维度评分 × 权重) × 市场乘数
ELS V2   = Event×30% + ExpectationGap×20% + TrendAlpha×20% + Institution×15% + Theme×10% + ETF×5%

组合所有评分维度，应用市场乘数，生成最终排序。

V1 评分保持兼容，V2 评分新增。
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
from .etf_score import calc_etf_score, _load_theme_stock_map, _load_theme_config
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
    ExpectationGapV2Result,
    InstitutionAccumulationResult,
    EarningsBuyPointResult,
)
from .market_score import get_market_score

logger = logging.getLogger("eld.final_score")

# ELD V2 评分维度名称
DIM_V2_EVENT_QUALITY = "v2_event_quality"
DIM_V2_EXPECTATION_GAP = "v2_expectation_gap"
DIM_V2_TREND = "v2_trend"
DIM_V2_INSTITUTION = "v2_institution"
DIM_V2_INDUSTRY = "v2_industry"
DIM_V2_ETF = "v2_etf"

ALL_V2_DIMENSIONS = [
    DIM_V2_EVENT_QUALITY,
    DIM_V2_EXPECTATION_GAP,
    DIM_V2_TREND,
    DIM_V2_INSTITUTION,
    DIM_V2_INDUSTRY,
    DIM_V2_ETF,
]


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
        # ELD V2 新增模块
        expectation_gap_v2_engine=None,
        institution_accumulation_engine=None,
        earnings_buy_point_engine=None,
        # ETF 评分模块
        etf_scorer=None,
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
        # ELD V2 新增
        self._expectation_gap_v2_engine = expectation_gap_v2_engine
        self._institution_accumulation_engine = institution_accumulation_engine
        self._earnings_buy_point_engine = earnings_buy_point_engine
        # ETF 评分
        self._etf_scorer = etf_scorer

        # 预加载主题映射数据（对所有股票共享，避免重复读文件）
        _trade_date = self.cfg.global_.target_date or ""
        self._theme_map = _load_theme_stock_map(_trade_date)
        self._theme_config = _load_theme_config()

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
            DIM_EVENT_QUALITY: self.fc.v1_event_quality_weight,
            DIM_EARNINGS: self.fc.v1_earnings_weight,
            DIM_INSTITUTION: self.fc.v1_institution_weight,
            DIM_CHIP: self.fc.v1_chip_weight,
            DIM_TREND: self.fc.v1_trend_weight,
            DIM_INDUSTRY: self.fc.v1_industry_weight,
            DIM_FRESHNESS: self.fc.v1_freshness_weight,
            DIM_EXPECTATION_GAP: self.fc.v1_expectation_gap_weight,
            DIM_SIMILARITY: self.fc.v1_similarity_weight,
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

    def compute_els_v2(
        self,
        event_score: float,
        gap_v2_score: float,
        trend_score: float,
        inst_accum_score: float,
        industry_score: float,
        etf_score: float = 50.0,
        trend_alpha: float = 0.0,
    ) -> float:
        """
        计算 ELD V2 评分

        ELS V2 = Event×30% + ExpectationGap×20% + TrendAlpha×20%
               + Institution×15% + Theme×10% + ETF×5%

        趋势Alpha兜底：
          - trend_alpha < 60: V2 × 0.7（趋势偏弱，事件质量再高也要打折）
          - trend_alpha < 45: V2 × 0.5（趋势很弱，坚决回避）

        Args:
            event_score: 事件质量评分 (0-100)
            gap_v2_score: 预期差V2评分 (0-100)
            trend_score: 趋势Alpha评分 (0-100)
            inst_accum_score: 机构吸筹评分 (0-100)
            industry_score: 行业主题评分 (0-100)
            etf_score: ETF评分 (0-100), 默认中性50分
            trend_alpha: 趋势Alpha值（用于兜底惩罚，非归一化分数）

        Returns:
            0-100 的 ELS V2 分数
        """
        weights = {
            DIM_V2_EVENT_QUALITY: self.fc.event_quality_weight,
            DIM_V2_EXPECTATION_GAP: self.fc.expectation_gap_weight,
            DIM_V2_TREND: self.fc.trend_weight,
            DIM_V2_INSTITUTION: self.fc.institution_weight,
            DIM_V2_INDUSTRY: self.fc.industry_weight,
            DIM_V2_ETF: self.fc.etf_weight,
        }

        scores = {
            DIM_V2_EVENT_QUALITY: event_score,
            DIM_V2_EXPECTATION_GAP: gap_v2_score,
            DIM_V2_TREND: trend_score,
            DIM_V2_INSTITUTION: inst_accum_score,
            DIM_V2_INDUSTRY: industry_score,
            DIM_V2_ETF: etf_score,
        }

        total_weight = sum(weights.values())
        if abs(total_weight - 1.0) > 0.001:
            logger.warning("ELS V2 weights sum to %.4f, normalizing", total_weight)
            for k in weights:
                weights[k] /= total_weight

        els_v2 = sum(scores[d] * weights[d] for d in ALL_V2_DIMENSIONS)
        els_v2 = max(0.0, min(100.0, els_v2))

        # ── 趋势Alpha兜底惩罚 ──
        if trend_alpha > 0:
            if trend_alpha < 45:
                penalty = 0.5
                logger.debug("趋势Alpha=%.1f<45, V2×%.1f", trend_alpha, penalty)
            elif trend_alpha < 60:
                penalty = 0.7
                logger.debug("趋势Alpha=%.1f<60, V2×%.1f", trend_alpha, penalty)
            else:
                penalty = 1.0
            els_v2 *= penalty
            els_v2 = max(0.0, min(100.0, els_v2))

        logger.debug(
            "ELS_V2=%.2f | event=%.1f gap=%.1f trend=%.1f inst=%.1f ind=%.1f etf=%.1f alpha=%.1f penalty=%.1f",
            els_v2, event_score, gap_v2_score, trend_score,
            inst_accum_score, industry_score, etf_score,
            trend_alpha, penalty if trend_alpha > 0 else 1.0,
        )

        return els_v2

    def generate_recommendation_v2(
        self,
        final_score: float,
        earnings_buy_signal: Optional[str] = None,
        institution_state: Optional[str] = None,
        next_day_buyable: bool = False,
    ) -> str:
        """
        根据 V2 分数和信号生成建议。

        Args:
            final_score: 最终评分 (0-100)
            earnings_buy_signal: 业绩回踩买点信号
            institution_state: 机构吸筹状态
            next_day_buyable: 次日可买标记

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

        # 买点增强
        if earnings_buy_signal == "BUY":
            base += " · 业绩回踩买点"
        elif earnings_buy_signal == "WATCH":
            base += " · 关注回踩"

        # 机构状态增强
        if institution_state:
            base += f" · {institution_state}"

        # 次日可买增强
        if next_day_buyable:
            base += " · 明日可低吸"

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
            # 无相似度引擎时使用中性评分
            sim_r = SimilarityResult(
                score=50.0, similar_stocks=[], cosine_sim=0.0,
                euclidean_dist=0.0, xgb_probability=0.5, logic=["简易模式跳过相似度"],
            )
        result.similarity_score = sim_r.score
        result.similarity_detail = sim_r

        # Stage 11: 买点
        if self._buy_point_engine:
            bp_r = self._buy_point_engine(stock.ts_code, daily_data, chip_r, trend_r)
        else:
            from .buy_point import analyze_buy_point
            bp_r = analyze_buy_point(stock.ts_code, daily_data, chip_r, trend_r)
        result.buy_point_detail = bp_r

        # ── ELD V2 新增评分 ──

        # Stage V2-1: 预期差V2
        if self._expectation_gap_v2_engine:
            gap_v2_r = self._expectation_gap_v2_engine(stock.ts_code, data_source)
        else:
            from .expectation_gap import calc_expectation_gap
            gap_v2_r = calc_expectation_gap(stock.ts_code, data_source)
        result.expectation_gap_v2_score = gap_v2_r.score
        result.expectation_gap_v2_detail = gap_v2_r

        # Stage V2-2: 机构吸筹检测
        if self._institution_accumulation_engine:
            inst_acc_r = self._institution_accumulation_engine(stock.ts_code, data_source)
        else:
            from .institution_accumulation import calc_institution_accumulation
            inst_acc_r = calc_institution_accumulation(stock.ts_code, data_source)
        result.institution_accumulation_score = inst_acc_r.score
        result.institution_accumulation_detail = inst_acc_r
        result.institution_state = inst_acc_r.state.value if hasattr(inst_acc_r.state, 'value') else str(inst_acc_r.state)

        # Stage V2-3: 业绩回踩买点
        if self._earnings_buy_point_engine:
            ebp_r = self._earnings_buy_point_engine(
                stock.ts_code, data_source, daily_data,
                announce_date=forecast.announce_date,
                trend_result=trend_r,
                institution_state=result.institution_state,
            )
        else:
            from .earnings_buy_point import detect_earnings_pullback
            ebp_r = detect_earnings_pullback(
                stock.ts_code, data_source, daily_data,
                announce_date=forecast.announce_date,
                trend_result=trend_r,
                institution_state=result.institution_state,
            )
        result.earnings_buy_point_detail = ebp_r
        result.earnings_buy_signal = ebp_r.signal.value if hasattr(ebp_r.signal, 'value') else str(ebp_r.signal)
        result.earnings_buy_score = ebp_r.score

        # Stage 12: 最终评分（V1 保持兼容）
        result.els = self.compute_els(
            event_r, earn_r, inst_r, chip_r, trend_r,
            ind_r, fresh_r, gap_r, sim_r,
        )
        result.final_score = self.apply_market_multiplier(result.els, market)
        result.recommendation = self.generate_recommendation(result.final_score, bp_r)

        # Stage 13: ELD V2 最终评分
        # 计算 ETF 评分
        _trade_date = self.cfg.global_.target_date or ""
        if self._etf_scorer:
            etf_score = self._etf_scorer(
                stock.ts_code, data_source,
                trade_date=_trade_date,
                theme_map=self._theme_map,
                theme_config=self._theme_config,
            )
        else:
            etf_score = calc_etf_score(
                stock.ts_code, data_source,
                trade_date=_trade_date,
                theme_map=self._theme_map,
                theme_config=self._theme_config,
            )
        result.etf_score = etf_score
        result.els_v2 = self.compute_els_v2(
            event_score=event_r.score,
            gap_v2_score=gap_v2_r.score,
            trend_score=trend_r.score,
            inst_accum_score=inst_acc_r.score,
            industry_score=ind_r.score,
            etf_score=etf_score,
            trend_alpha=trend_r.alpha,  # 传入趋势Alpha用于兜底惩罚
        )
        result.final_score_v2 = self.apply_market_multiplier(result.els_v2, market)
        result.recommendation_v2 = self.generate_recommendation_v2(
            result.final_score_v2,
            earnings_buy_signal=result.earnings_buy_signal,
            institution_state=result.institution_state,
            next_day_buyable=ebp_r.next_day_buyable if ebp_r else False,
        )

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

        # 按 V2 最终评分排序（V2 优先，V1 作为备选）
        results.sort(key=lambda r: (r.final_score_v2, r.final_score), reverse=True)
        for i, r in enumerate(results):
            r.rank = i + 1

        report.filtered_stocks = len(results)
        report.results = results

        logger.info(
            "Pipeline complete: %d/%d stocks scored (V2排序)",
            len(results), report.total_stocks,
        )

        return report
