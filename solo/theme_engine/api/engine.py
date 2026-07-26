"""TERE V1 主引擎.

Theme & ETF Resonance Engine 核心流水线。
"""

from __future__ import annotations

import asyncio
import csv
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from theme_engine.config.settings import get_factor_weights, get_layer_weight, get_threshold, load_weights
from theme_engine.factor.breadth import BreadthFactor
from theme_engine.factor.etf_strength import ETFStrengthFactor
from theme_engine.factor.flow import FlowFactor
from theme_engine.factor.leader import LeaderStrengthFactor
from theme_engine.factor.purity import PurityFactor
from theme_engine.factor.registry import get_registry
from theme_engine.factor.resonance import ResonanceFactor
from theme_engine.models.dataclasses import (
    BreadthResult,
    ETFStrengthResult,
    EngineResult,
    ExplainItem,
    FactorResult,
    FlowResult,
    LeaderResult,
    PurityResult,
    ResonanceResult,
    RotationResult,
    SignalResult,
    StageResult,
    ThemeDailyScore,
)
from theme_engine.repository.repository import Repository
from theme_engine.rotation.predictor import RotationPredictor
from theme_engine.score.calculator import ScoreCalculator
from theme_engine.services.etf_service import ETFService
from theme_engine.services.stock_service import StockService
from theme_engine.services.theme_service import ThemeService
from theme_engine.stage.state_machine import StageStateMachine
from theme_engine.validator.validator import Validator

logger = logging.getLogger(__name__)


class TERE:
    """Theme & ETF Resonance Engine 主引擎."""

    def __init__(self) -> None:
        self.registry = get_registry()

        # 注册全部真实因子
        self.registry.register(ETFStrengthFactor(), layer="etf_strength")
        self.registry.register(BreadthFactor(), layer="breadth")
        self.registry.register(LeaderStrengthFactor(), layer="leader")
        self.registry.register(PurityFactor(), layer="purity")
        self.registry.register(ResonanceFactor(), layer="resonance")
        self.registry.register(FlowFactor(), layer="flow")

        self.stage_machine = StageStateMachine()
        self.rotation_predictor = RotationPredictor()
        self.score_calculator = ScoreCalculator()
        self.etf_service = ETFService()
        self.stock_service = StockService()
        self.theme_service = ThemeService()
        self.repository = Repository()
        self.validator = Validator(self.theme_service)

        # 运行时状态
        self._weights: Dict[str, Any] = {}
        self._dry_run: bool = False

    async def run(
        self,
        trade_date: Optional[str] = None,
        **kwargs: Any,
    ) -> EngineResult:
        """执行完整流水线.

        Args:
            trade_date: 交易日 YYYYMMDD，默认使用当前日期
            **kwargs:
                dry_run: 仅计算不保存
                single: 仅计算单个主题代码
                skip_etf: 跳过ETF强度计算
                skip_breadth: 跳过扩散度计算
                skip_leader: 跳过龙头计算
                skip_purity: 跳过纯度计算
                skip_resonance: 跳过共振计算
                skip_flow: 跳过资金流计算
                skip_stage: 跳过生命周期判定
                skip_signal: 跳过信号生成
                skip_rotation: 跳过轮动预测

        Returns:
            EngineResult 包含完整结果
        """
        start_time = datetime.now()
        trade_date = trade_date or start_time.strftime("%Y%m%d")
        self._dry_run = kwargs.get("dry_run", False)
        single_theme = kwargs.get("single", None)

        logger.info(
            "═══ TERE V1 引擎启动 ═══  date=%s  dry_run=%s",
            trade_date,
            self._dry_run,
        )

        result = EngineResult(
            trade_date=trade_date,
            generated_at=start_time.strftime("%Y-%m-%d %H:%M:%S"),
        )

        try:
            # 1. 加载权重配置
            self._weights = load_weights()
            logger.info("权重配置已加载")

            # 2. 加载主题配置和股票映射
            config = await self.theme_service.load_config()
            await self.theme_service.load_stock_map(trade_date)

            theme_codes = [single_theme] if single_theme else [k for k in config.keys() if not k.startswith("_")]
            logger.info("待处理主题数量: %d", len(theme_codes))

            # 3. 对每个主题执行流水线
            daily_scores: List[ThemeDailyScore] = []

            for theme_code in theme_codes:
                try:
                    score = await self.run_single(theme_code, trade_date, **kwargs)
                    if score is not None:
                        daily_scores.append(score)
                except Exception as e:
                    logger.error("主题 %s 评分失败: %s", theme_code, e)
                    continue

            # 4. 排序和排名
            daily_scores.sort(key=lambda x: x.total_score, reverse=True)
            for rank, score in enumerate(daily_scores, start=1):
                score.rank = rank

            result.themes = daily_scores
            result.ranking = daily_scores
            result.top_themes = daily_scores[:10]

            # 5. 校验
            if not kwargs.get("skip_validation", False):
                try:
                    warnings = await self.validator.validate_all(trade_date)
                    if warnings:
                        logger.info("校验完成: %d 条警告", len(warnings))
                except Exception as e:
                    logger.warning("校验过程异常: %s", e)

            # 6. 保存到数据库
            if not self._dry_run:
                try:
                    await self._save_results(daily_scores)
                except Exception as e:
                    logger.error("保存结果失败: %s", e)
                    result.error = f"保存失败: {e}"

            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(
                "═══ TERE V1 引擎完成 ═══  耗时: %.2fs  主题数: %d",
                elapsed,
                len(daily_scores),
            )

        except Exception as e:
            logger.error("引擎运行异常: %s", e)
            result.error = str(e)

        return result

    async def run_single(
        self, theme_code: str, trade_date: str, **kwargs: Any
    ) -> Optional[ThemeDailyScore]:
        """计算单个主题的完整评分.

        Args:
            theme_code: 主题代码
            trade_date: 交易日

        Returns:
            ThemeDailyScore 或 None（失败时）
        """
        try:
            theme_name = await self.theme_service.get_theme_name(theme_code)
            main_etf, backup_etf = await self.theme_service.get_theme_etfs(theme_code)
            stocks = await self.theme_service.get_theme_stocks(theme_code, trade_date)

            # 富化成分股数据: 添加 pct_chg, amount, MA 状态等行情指标
            enriched_stocks = await self.stock_service.enrich_stocks(stocks, trade_date)

            logger.debug("处理主题: %s (%s), 成分股: %d", theme_code, theme_name, len(enriched_stocks))

            # a. ETF 强度
            etf_result: Optional[ETFStrengthResult] = None
            if not kwargs.get("skip_etf", False):
                factor_results = await self.registry.calculate_layer(
                    "etf_strength", theme_code, trade_date,
                    etf_service=self.etf_service,
                    main_etf=main_etf,
                    backup_etf=backup_etf,
                )
                etf_result = await self._build_etf_result(
                    theme_code, trade_date, main_etf, backup_etf, factor_results,
                )

            # b. 扩散度
            breadth_result: Optional[BreadthResult] = None
            if not kwargs.get("skip_breadth", False):
                factor_results = await self.registry.calculate_layer(
                    "breadth", theme_code, trade_date,
                    stock_service=self.stock_service,
                    theme_stocks=enriched_stocks,
                )
                breadth_result = await self._build_breadth_result(
                    theme_code, trade_date, enriched_stocks, factor_results,
                )

            # c. 龙头强度
            leader_result: Optional[LeaderResult] = None
            if not kwargs.get("skip_leader", False):
                factor_results = await self.registry.calculate_layer(
                    "leader", theme_code, trade_date,
                    stock_service=self.stock_service,
                    theme_stocks=enriched_stocks,
                )
                leader_result = await self._build_leader_result(
                    theme_code, trade_date, factor_results,
                )

            # d. 纯度
            purity_score_val = 0.0
            if not kwargs.get("skip_purity", False):
                purity_result = self._calc_purity(theme_code, trade_date, enriched_stocks)
                purity_score_val = purity_result.purity_score

            # e. 共振
            resonance_score_val = 0.0
            if not kwargs.get("skip_resonance", False) and etf_result and breadth_result and leader_result:
                factor_results = await self.registry.calculate_layer(
                    "resonance", theme_code, trade_date,
                    etf_strength=etf_result.etf_strength,
                    breadth_score=breadth_result.breadth_score,
                    leader_score=leader_result.leader_strength,
                )
                resonance_result = self._build_resonance_result(
                    theme_code, trade_date, factor_results,
                    etf_result.etf_strength, breadth_result.breadth_score,
                    leader_result.leader_strength,
                )
                resonance_score_val = resonance_result.resonance_score

            # f. 资金流
            flow_score_val = 0.0
            if not kwargs.get("skip_flow", False):
                factor_results = await self.registry.calculate_layer(
                    "flow", theme_code, trade_date,
                    etf_service=self.etf_service,
                    stock_service=self.stock_service,
                    main_etf=main_etf,
                    theme_stocks=enriched_stocks,
                )
                flow_result = self._build_flow_result(
                    theme_code, trade_date, factor_results,
                )
                flow_score_val = flow_result.flow_score

            # g. 生命周期
            stage_str = "birth"
            if not kwargs.get("skip_stage", False):
                stage_result = await self._calc_stage(
                    theme_code, trade_date, etf_result, breadth_result
                )
                stage_str = stage_result.current_stage if stage_result else "birth"

            # 3. 计算总分 (提前赋值, 后续各环节依赖)
            etf_strength_score = etf_result.etf_strength if etf_result else 0.0
            breadth_val = breadth_result.breadth_score if breadth_result else 0.0
            leader_val = leader_result.leader_strength if leader_result else 0.0
            purity_score_val = purity_result.purity_score if purity_result else 0.0
            resonance_score_val = resonance_result.resonance_score if resonance_result else 0.0
            flow_score_val = flow_result.flow_score if flow_result else 0.0

            # h. 轮动概率
            rotation_prob = 0.0
            if not kwargs.get("skip_rotation", False):
                indicators = {
                    "etf_strength": etf_strength_score,
                    "breadth": breadth_val,
                    "leader": leader_val,
                    "resonance": resonance_score_val,
                    "flow": flow_score_val,
                    "purity": purity_score_val,
                }
                rotation_result = await self.rotation_predictor.predict(
                    theme_code=theme_code,
                    trade_date=trade_date,
                    indicators=indicators,
                    history_days=20,
                )
                rotation_prob = rotation_result.rotation_score if rotation_result else 0.0

            # i. 信号
            signal_str = "WATCH"
            if not kwargs.get("skip_signal", False):
                signal_result = await self._calc_signal(
                    theme_code, trade_date,
                    etf_result, breadth_result, leader_result,
                )
                signal_str = signal_result.signal if signal_result else "WATCH"

            # 构建 factor_results 字典供 ScoreCalculator 使用
            calc_factor_results: Dict[str, FactorResult] = {}
            if etf_result:
                calc_factor_results["etf_strength"] = FactorResult(
                    factor_name="etf_strength", version="1.0",
                    score=etf_strength_score, weight=get_layer_weight("etf_strength"),
                    contribution=0.0,
                )
            if breadth_result:
                calc_factor_results["breadth"] = FactorResult(
                    factor_name="breadth", version="1.0",
                    score=breadth_val, weight=get_layer_weight("breadth"),
                    contribution=0.0,
                )
            if leader_result:
                calc_factor_results["leader"] = FactorResult(
                    factor_name="leader", version="1.0",
                    score=leader_val, weight=get_layer_weight("leader"),
                    contribution=0.0,
                )

            # 龙头股票列表
            top_leaders = []
            top_stocks = []
            if leader_result:
                top_leaders = [l.get("code", "") for l in leader_result.leaders[:5]]
                top_stocks = [l.get("code", "") for l in leader_result.leaders[:3]]

            daily_score_obj = self.score_calculator.calculate(
                theme_code=theme_code,
                theme_name=theme_name,
                factor_results=calc_factor_results,
                stage=stage_str,
                signal=signal_str,
                rotation=rotation_prob,
                top_leaders=top_leaders,
                top_stocks=top_stocks,
                main_etf=main_etf,
                backup_etf=backup_etf,
                trade_date=trade_date,
            )
            total_score = daily_score_obj.total_score

            # 构建解释
            explanations = self._build_explain_items(
                etf_strength=etf_strength_score,
                breadth=breadth_val,
                leader=leader_val,
                purity=purity_score_val,
                resonance=resonance_score_val,
                flow=flow_score_val,
                rotation=rotation_prob,
                signal=signal_str,
            )
            summary = self._build_explain_text(
                [ExplainItem(**e) if isinstance(e, dict) else e for e in explanations],
                total_score,
            )

            daily_score = ThemeDailyScore(
                rank=0,
                theme_code=theme_code,
                theme_name=theme_name,
                total_score=round(total_score, 2),
                etf_strength=round(etf_strength_score, 2),
                breadth_score=round(breadth_val, 2),
                leader_strength=round(leader_val, 2),
                purity_score=round(purity_score_val, 2),
                resonance_score=round(resonance_score_val, 2),
                flow_score=round(flow_score_val, 2),
                stage=stage_str,
                rotation_prob=round(rotation_prob, 2),
                signal=signal_str,
                top_leaders=top_leaders,
                top_stocks=top_stocks,
                main_etf=main_etf,
                backup_etf=backup_etf,
                explanations=explanations,
                summary=summary,
                trade_date=trade_date,
                created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )

            # 保存各层级结果
            if not self._dry_run:
                await self._save_layer_results(
                    theme_code, trade_date,
                    etf_result=etf_result,
                    breadth_result=breadth_result,
                    leader_result=leader_result,
                    resonance_result=None,
                    stage_result=None if kwargs.get("skip_stage") else None,
                    rotation_result=None if kwargs.get("skip_rotation") else None,
                    signal_result=None if kwargs.get("skip_signal") else None,
                )

            return daily_score

        except Exception as e:
            logger.error("计算主题 %s 失败: %s", theme_code, e)
            return None

    # ── 内部辅助方法 ──────────────────────────────────────

    async def _build_etf_result(
        self,
        theme_code: str,
        trade_date: str,
        main_etf: str,
        backup_etf: Optional[str],
        factor_results: Dict[str, FactorResult],
    ) -> ETFStrengthResult:
        """从因子结果构建 ETFStrengthResult.

        支持两种模式:
        1. 单因子模式: factor_results["etf_strength"] 包含整体 score
        2. 多子因子模式: 遍历 trend/momentum/alpha 等子因子
        """
        result = ETFStrengthResult(
            theme_code=theme_code,
            trade_date=trade_date,
            main_etf=main_etf,
            backup_etf=backup_etf,
        )

        # 单因子模式: 主因子名存在（即使有 error 仍使用其默认分）
        main = factor_results.get("etf_strength")
        if main is not None:
            result.etf_strength = main.score
            # 从 details 中提取子因子分
            details = main.details or {}
            if "trend_score" in details:
                result.trend_score = details.get("trend_score", 0.0)
                result.momentum_score = details.get("momentum_score", 0.0)
                result.alpha_score = details.get("alpha_score", 0.0)
                result.volume_score = details.get("volume_score", 0.0)
                result.money_flow_score = details.get("money_flow_score", 0.0)
                result.volatility_score = details.get("volatility_score", 0.0)
                result.relative_strength = details.get("relative_strength", 0.0)
                result.ma_trend = details.get("ma_trend", 0.0)
                result.slope = details.get("slope", 0.0)
                result.atr_score = details.get("atr_score", 0.0)
                result.breakout_score = details.get("breakout_score", 0.0)
            result.details = main.details or {}
            return result

        # 多子因子模式（兼容）
        total = 0.0
        weight_sum = 0.0
        weights = get_factor_weights("etf_strength")

        field_map = {
            "trend": "trend_score",
            "momentum": "momentum_score",
            "alpha": "alpha_score",
            "volume": "volume_score",
            "money_flow": "money_flow_score",
            "volatility": "volatility_score",
            "relative_strength": "relative_strength",
            "ma_trend": "ma_trend",
            "slope": "slope",
            "atr": "atr_score",
            "breakout": "breakout_score",
        }

        for factor_name, field in field_map.items():
            fr = factor_results.get(factor_name)
            if fr and fr.error is None:
                setattr(result, field, fr.score)
                w = weights.get(factor_name, 1.0)
                total += fr.score * w
                weight_sum += w

        result.etf_strength = total / weight_sum if weight_sum > 0 else 0.0
        result.details = {"factor_results": {k: v.score for k, v in factor_results.items()}}
        return result

    async def _build_breadth_result(
        self,
        theme_code: str,
        trade_date: str,
        stocks: List[Dict],
        factor_results: Dict[str, FactorResult],
    ) -> BreadthResult:
        """从因子结果构建 BreadthResult.

        支持两种模式:
        1. 单因子模式: factor_results["breadth"] 包含整体 score
        2. 多子因子模式
        """
        result = BreadthResult(
            theme_code=theme_code,
            trade_date=trade_date,
            total_stocks=len(stocks),
        )

        # 单因子模式
        main = factor_results.get("breadth")
        if main and main.error is None:
            result.breadth_score = main.score
            details = main.details or {}
            result.up_ratio = details.get("up_ratio", 0.0)
            result.limit_up_ratio = details.get("limit_up_ratio", 0.0)
            result.new_high_20d_ratio = details.get("new_high_20d_ratio", 0.0)
            result.above_ma20_ratio = details.get("above_ma20_ratio", 0.0)
            result.above_ma60_ratio = details.get("above_ma60_ratio", 0.0)
            result.above_ma120_ratio = details.get("above_ma120_ratio", 0.0)
            result.amount_diffusion = details.get("amount_diffusion", 0.0)
            result.return_median = details.get("return_median", 0.0)
            result.avg_alpha = details.get("avg_alpha", 0.0)
            result.avg_relative_alpha = details.get("avg_relative_alpha", 0.0)
            result.details = main.details or {}
            return result

        # 多子因子模式（兼容）
        total = 0.0
        weight_sum = 0.0
        weights = get_factor_weights("breadth")

        field_map = {
            "up_ratio": "up_ratio",
            "limit_up_ratio": "limit_up_ratio",
            "new_high_20d_ratio": "new_high_20d_ratio",
            "above_ma20_ratio": "above_ma20_ratio",
            "above_ma60_ratio": "above_ma60_ratio",
            "above_ma120_ratio": "above_ma120_ratio",
            "amount_diffusion": "amount_diffusion",
            "return_median": "return_median",
            "avg_alpha": "avg_alpha",
            "avg_relative_alpha": "avg_relative_alpha",
        }

        for factor_name, field in field_map.items():
            fr = factor_results.get(factor_name)
            if fr and fr.error is None:
                setattr(result, field, fr.score)
                w = weights.get(factor_name, 1.0)
                total += fr.score * w
                weight_sum += w

        result.breadth_score = total / weight_sum if weight_sum > 0 else 0.0
        result.details = {"factor_results": {k: v.score for k, v in factor_results.items()}}
        return result

    async def _build_leader_result(
        self,
        theme_code: str,
        trade_date: str,
        factor_results: Dict[str, FactorResult],
    ) -> LeaderResult:
        """从因子结果构建 LeaderResult.

        支持两种模式:
        1. 单因子模式: factor_results["leader"] 包含整体 score
        2. 多子因子模式
        """
        result = LeaderResult(
            theme_code=theme_code,
            trade_date=trade_date,
        )

        # 单因子模式
        main = factor_results.get("leader")
        if main and main.error is None:
            result.leader_strength = main.score
            details = main.details or {}
            result.leader_trend = details.get("leader_trend", 0.0)
            result.leader_alpha = details.get("leader_alpha", 0.0)
            result.relative_strength = details.get("relative_strength", 0.0)
            result.volume_score = details.get("volume_score", 0.0)
            result.money_flow_score = details.get("money_flow_score", 0.0)
            result.institution_score = details.get("institution_score", 0.0)
            result.macd_score = details.get("macd_score", 0.0)
            result.ma_trend_score = details.get("ma_trend_score", 0.0)
            result.details = main.details or {}
            return result

        # 多子因子模式（兼容）
        total = 0.0
        weight_sum = 0.0
        weights = get_factor_weights("leader")

        field_map = {
            "leader_trend": "leader_trend",
            "leader_alpha": "leader_alpha",
            "relative_strength": "relative_strength",
            "volume": "volume_score",
            "money_flow": "money_flow_score",
            "institution_score": "institution_score",
            "macd": "macd_score",
            "ma_trend": "ma_trend_score",
        }

        for factor_name, field in field_map.items():
            fr = factor_results.get(factor_name)
            if fr and fr.error is None:
                setattr(result, field, fr.score)
                w = weights.get(factor_name, 1.0)
                total += fr.score * w
                weight_sum += w

        result.leader_strength = total / weight_sum if weight_sum > 0 else 0.0
        result.details = {"factor_results": {k: v.score for k, v in factor_results.items()}}
        return result

    def _calc_purity(
        self, theme_code: str, trade_date: str, stocks: List[Dict]
    ) -> PurityResult:
        """计算纯度评分."""
        purities = [s.get("purity", 0) for s in stocks if s.get("purity", 0) > 0]
        avg_purity = sum(purities) / len(purities) if purities else 0.0

        result = PurityResult(
            theme_code=theme_code,
            trade_date=trade_date,
            theme_purity=avg_purity,
            stock_purities=stocks,
        )

        weights = get_factor_weights("purity")
        result.purity_score = avg_purity  # 简化: 直接将平均纯度作为分数
        result.details = {"stock_count": len(stocks), "purity_count": len(purities)}
        return result

    def _build_resonance_result(
        self,
        theme_code: str,
        trade_date: str,
        factor_results: Dict[str, FactorResult],
        etf_strength: float,
        breadth: float,
        leader_score: float,
    ) -> ResonanceResult:
        """构建共振评分."""
        result = ResonanceResult(
            theme_code=theme_code,
            trade_date=trade_date,
            etf_strength=etf_strength,
            theme_breadth=breadth,
            leader_score=leader_score,
        )

        # 单因子模式
        main = factor_results.get("resonance")
        if main and main.error is None:
            result.resonance_score = main.score
            details = main.details or {}
            result.consistency_score = details.get("consistency_score", 0.0)
            result.variance_penalty = details.get("variance_penalty", 0.0)
            result.std = details.get("std", 0.0)
            result.correlation = details.get("correlation", 0.0)
            result.details = main.details or {}
            return result

        total = 0.0
        weight_sum = 0.0
        weights = get_factor_weights("resonance")

        field_map = {
            "consistency_score": "consistency_score",
            "variance_penalty": "variance_penalty",
            "std": "std",
            "correlation": "correlation",
        }

        for factor_name, field in field_map.items():
            fr = factor_results.get(factor_name)
            if fr and fr.error is None:
                setattr(result, field, fr.score)
                w = weights.get(factor_name, 1.0)
                total += fr.score * w
                weight_sum += w

        result.resonance_score = total / weight_sum if weight_sum > 0 else 0.0
        result.details = {"factor_results": {k: v.score for k, v in factor_results.items()}}
        return result

    def _build_flow_result(
        self,
        theme_code: str,
        trade_date: str,
        factor_results: Dict[str, FactorResult],
    ) -> FlowResult:
        """构建资金流评分."""
        result = FlowResult(
            theme_code=theme_code,
            trade_date=trade_date,
        )

        # 单因子模式
        main = factor_results.get("flow")
        if main and main.error is None:
            result.flow_score = main.score
            details = main.details or {}
            result.etf_net_flow = details.get("etf_net_flow", 0.0)
            result.theme_total_amount = details.get("theme_total_amount", 0.0)
            result.leader_amount = details.get("leader_amount", 0.0)
            result.amount_change_pct = details.get("amount_change_pct", 0.0)
            result.volume_change_pct = details.get("volume_change_pct", 0.0)
            result.flow_diffusion = details.get("flow_diffusion", 0.0)
            result.details = main.details or {}
            return result

        total = 0.0
        weight_sum = 0.0
        weights = get_factor_weights("flow")

        field_map = {
            "etf_flow": "etf_net_flow",
            "theme_amount": "theme_total_amount",
            "leader_amount": "leader_amount",
            "amount_change": "amount_change_pct",
            "volume_change": "volume_change_pct",
            "flow_diffusion": "flow_diffusion",
        }

        for factor_name, field in field_map.items():
            fr = factor_results.get(factor_name)
            if fr and fr.error is None:
                setattr(result, field, fr.score)
                w = weights.get(factor_name, 1.0)
                total += fr.score * w
                weight_sum += w

        result.flow_score = total / weight_sum if weight_sum > 0 else 0.0
        result.details = {"factor_results": {k: v.score for k, v in factor_results.items()}}
        return result

    async def _calc_stage(
        self,
        theme_code: str,
        trade_date: str,
        etf_result: Optional[ETFStrengthResult],
        breadth_result: Optional[BreadthResult],
    ) -> Optional[StageResult]:
        """计算生命周期阶段."""
        try:
            # 获取历史阶段
            latest_stage = await self.repository.get_latest_stage(theme_code)

            indicators = {
                "etf_strength": etf_result.etf_strength if etf_result else 0.0,
                "breadth": breadth_result.breadth_score if breadth_result else 0.0,
            }

            stage = await self.stage_machine.analyze(
                theme_code=theme_code,
                trade_date=trade_date,
                indicators=indicators,
            )

            if not self._dry_run:
                await self.repository.save_stage(stage)

            return stage
        except Exception as e:
            logger.error("阶段判定失败 %s: %s", theme_code, e)
            return None

    async def _calc_signal(
        self,
        theme_code: str,
        trade_date: str,
        etf_result: Optional[ETFStrengthResult],
        breadth_result: Optional[BreadthResult],
        leader_result: Optional[LeaderResult],
    ) -> Optional[SignalResult]:
        """计算交易信号."""
        try:
            signal = SignalResult(theme_code=theme_code, trade_date=trade_date)

            etf_str = etf_result.etf_strength if etf_result else 0.0
            breadth = breadth_result.breadth_score if breadth_result else 0.0
            leader_str = leader_result.leader_strength if leader_result else 0.0

            strong_buy_th = get_threshold("strong_buy")
            buy_th = get_threshold("buy")
            watch_th = get_threshold("watch")
            reduce_th = get_threshold("reduce")
            exit_th = get_threshold("exit")

            # 综合打分
            composite = (etf_str * 0.4 + breadth * 0.3 + leader_str * 0.3)

            if composite >= strong_buy_th:
                signal.signal = "STRONG_BUY"
                signal.reasons = ["ETF强度高", "扩散度强", "龙头强势"]
            elif composite >= buy_th:
                signal.signal = "BUY"
                signal.reasons = ["综合评分较好"]
            elif composite >= watch_th:
                signal.signal = "WATCH"
                signal.reasons = ["综合评分中等"]
            elif composite >= reduce_th:
                signal.signal = "REDUCE"
                signal.reasons = ["综合评分下降"]
            else:
                signal.signal = "EXIT"
                signal.reasons = ["综合评分过低"]

            signal.signal_strength = composite
            signal.details = {
                "etf_strength": etf_str,
                "breadth": breadth,
                "leader_strength": leader_str,
                "composite": composite,
            }

            if not self._dry_run:
                await self.repository.save_signal(signal)

            return signal
        except Exception as e:
            logger.error("信号计算失败 %s: %s", theme_code, e)
            return None

    def _build_explain_items(
        self,
        etf_strength: float,
        breadth: float,
        leader: float,
        purity: float,
        resonance: float,
        flow: float,
        rotation: float,
        signal: str,
    ) -> List[Dict[str, Any]]:
        """构建可解释条目."""
        items: List[Dict[str, Any]] = []

        weights = self._weights.get("layer_weights", {})
        layer_items = [
            ("ETF强度", etf_strength, weights.get("etf_strength", 30)),
            ("扩散度", breadth, weights.get("breadth", 20)),
            ("龙头强度", leader, weights.get("leader", 20)),
            ("纯度", purity, weights.get("purity", 10)),
            ("共振", resonance, weights.get("resonance", 10)),
            ("资金流", flow, weights.get("flow", 5)),
            ("轮动概率", rotation, weights.get("rotation", 5)),
        ]

        for name, score, weight in layer_items:
            items.append({
                "reason": f"{name}: {score:.1f}/100",
                "score": round(score, 2),
                "weight": weight,
            })

        items.append({
            "reason": f"信号: {signal}",
            "score": 0.0,
            "weight": 0.0,
        })

        return items

    def _build_explain_text(
        self,
        reasons: List[ExplainItem],
        total: float,
    ) -> str:
        """生成可读解释文本."""
        lines = [f"综合评分: {total:.1f}/100"]
        for item in reasons:
            if item.weight > 0:
                lines.append(
                    f"  - {item.reason} (权重: {item.weight:.0f}%)"
                )
            else:
                lines.append(f"  - {item.reason}")
        return "\n".join(lines)

    async def export_ranking(
        self,
        trade_date: str,
        output_path: Optional[str] = None,
    ) -> str:
        """导出排行榜到CSV.

        Args:
            trade_date: 交易日
            output_path: 输出路径，默认输出到 data/ 目录

        Returns:
            输出文件路径
        """
        if output_path is None:
            from theme_engine.config.settings import PROJECT_ROOT

            output_dir = PROJECT_ROOT / "data"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(output_dir / f"ranking_{trade_date}.csv")

        rankings = await self.repository.get_daily_ranking(trade_date, top_n=100)

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "rank", "theme_code", "theme_name", "total_score",
                "etf_strength", "breadth", "leader", "purity",
                "resonance", "flow", "stage", "signal", "main_etf",
            ])
            for r in rankings:
                writer.writerow([
                    r.rank, r.theme_code, r.theme_name, r.total_score,
                    r.etf_strength, r.breadth_score, r.leader_strength,
                    r.purity_score, r.resonance_score, r.flow_score,
                    r.stage, r.signal, r.main_etf,
                ])

        logger.info("排行榜已导出: %s", output_path)
        return output_path

    async def _save_results(self, scores: List[ThemeDailyScore]) -> None:
        """批量保存每日评分到数据库."""
        for score in scores:
            try:
                await self.repository.save_daily_score(score)
            except Exception as e:
                logger.error("保存主题 %s 评分失败: %s", score.theme_code, e)

    async def _save_layer_results(
        self,
        theme_code: str,
        trade_date: str,
        etf_result: Optional[ETFStrengthResult] = None,
        breadth_result: Optional[BreadthResult] = None,
        leader_result: Optional[LeaderResult] = None,
        resonance_result: Optional[ResonanceResult] = None,
        stage_result: Optional[StageResult] = None,
        rotation_result: Optional[RotationResult] = None,
        signal_result: Optional[SignalResult] = None,
    ) -> None:
        """保存各层级评分结果."""
        tasks = []

        if etf_result:
            tasks.append(self.repository.save_etf_score(etf_result))
        if breadth_result:
            tasks.append(self.repository.save_breadth_score(breadth_result))
        if leader_result:
            tasks.append(self.repository.save_leader_score(leader_result))
        if resonance_result:
            tasks.append(self.repository.save_resonance_score(resonance_result))
        if stage_result:
            tasks.append(self.repository.save_stage(stage_result))
        if rotation_result:
            tasks.append(self.repository.save_rotation(rotation_result))
        if signal_result:
            tasks.append(self.repository.save_signal(signal_result))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def cleanup(self) -> None:
        """清理资源."""
        await self.repository.close()
        await self.etf_service.clear_cache()
        await self.stock_service.clear_cache()
        await self.theme_service.clear_cache()
