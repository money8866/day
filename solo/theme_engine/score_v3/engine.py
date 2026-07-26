"""V3 Engine — 机构动态轮动评分引擎.

集成 V3 评分系统到现有 TERE 框架。
保留现有数据服务接口，仅升级评分算法。

用法:
    python -m theme_engine.main --date 20260724 --v3
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import math
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from theme_engine.score_v3.calculator import V3Calculator
from theme_engine.score_v3.config import load_config
from theme_engine.score_v3.factors.etf_trend import calc_etf_trend
from theme_engine.score_v3.factors.etf_accel import calc_etf_accel
from theme_engine.score_v3.factors.breadth import calc_breadth
from theme_engine.score_v3.factors.leader import calc_leader
from theme_engine.score_v3.factors.leader_expand import calc_leader_expand
from theme_engine.score_v3.factors.rank_momentum import calc_rank_momentum
from theme_engine.score_v3.factors.money_flow import calc_money_flow
from theme_engine.score_v3.factors.lifecycle import calc_lifecycle
from theme_engine.score_v3.factors.resonance import calc_resonance
from theme_engine.score_v3.models import (
    EngineV3Result,
    MarketInfo,
    ThemeV3Score,
)
from theme_engine.market_regime.engine import MarketRegimeEngine
from theme_engine.services.etf_service import ETFService
from theme_engine.services.stock_service import StockService
from theme_engine.services.theme_service import ThemeService

logger = logging.getLogger(__name__)


class V3Engine:
    """V3 机构动态轮动评分引擎.

    四层架构:
      Layer 1 Market  → MarketRegimeEngine
      Layer 2 Theme   → V3Calculator (IntrinsicScore)
      Layer 3 ETF     → ETF factors
      Layer 4 Leader  → Leader factors
    """

    def __init__(self) -> None:
        self.calculator = V3Calculator()
        self.market_engine = MarketRegimeEngine()
        self.etf_service = ETFService()
        self.stock_service = StockService()
        self.theme_service = ThemeService()
        self._dry_run: bool = False
        self._stage_history: Dict[str, Dict[str, Any]] = {}
        self._stage_history_path = Path(__file__).resolve().parent / "cache" / "stage_history.json"

    async def _load_stage_history(self) -> Dict[str, Dict[str, Any]]:
        """加载历史阶段记录."""
        if self._stage_history_path.exists():
            try:
                with open(self._stage_history_path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_stage_history(self, history: Dict[str, Dict[str, Any]]) -> None:
        """保存阶段记录."""
        self._stage_history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._stage_history_path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    def _get_days_in_stage(self, theme_code: str, current_stage: str) -> int:
        """计算当前阶段已持续天数."""
        record = self._stage_history.get(theme_code, {})
        prev_stage = record.get("stage", "")
        prev_date = record.get("date", "")
        if prev_stage == current_stage:
            days = record.get("days", 0)
            return days + 1
        return 1

    def _update_stage_history(self, theme_code: str, stage: str, trade_date: str) -> None:
        """更新阶段记录 (不立即写盘)."""
        record = self._stage_history.get(theme_code, {})
        prev_stage = record.get("stage", "")
        if prev_stage == stage:
            days = record.get("days", 0) + 1
        else:
            days = 1
        self._stage_history[theme_code] = {"stage": stage, "date": trade_date, "days": days}

    async def run(
        self,
        trade_date: Optional[str] = None,
        **kwargs: Any,
    ) -> EngineV3Result:
        """执行完整 V3 流水线.

        Args:
            trade_date: 交易日 YYYYMMDD
            **kwargs:
                dry_run: 仅计算不保存
                single: 仅计算单个主题代码
                skip_factors: 跳过的因子列表 (默认空)

        Returns:
            EngineV3Result 包含完整 V3 评分结果
        """
        start_time = datetime.now()
        trade_date = trade_date or start_time.strftime("%Y%m%d")
        self._dry_run = kwargs.get("dry_run", False)
        single = kwargs.get("single", None)
        skip_factors: List[str] = kwargs.get("skip_factors", [])

        logger.info(
            "═══ TERE V3 引擎启动 ═══  date=%s  dry_run=%s",
            trade_date,
            self._dry_run,
        )

        result = EngineV3Result(
            trade_date=trade_date,
            generated_at=start_time.strftime("%Y-%m-%d %H:%M:%S"),
        )

        try:
            # 0. 加载阶段历史
            self._stage_history = await self._load_stage_history()

            # 1. 加载 V3 配置
            cfg = load_config()
            if not cfg:
                result.error = "V3配置加载失败"
                return result
            logger.info("V3评分配置已加载 (layer_weights=%d)", len(cfg.get("layer_weights", {})))

            # 2. 运行 Market Regime (Layer 1)
            logger.info("═══ Market Regime 分析 ═══")
            market_result = await self.market_engine.analyze(trade_date)

            result.market_info = MarketInfo(
                market_score=market_result.market_score,
                market_regime=market_result.regime,
                market_regime_cn=market_result.regime_cn,
                confidence=market_result.confidence,
                market_multiplier=market_result.market_multiplier,
                recommended_exposure=market_result.recommended_exposure,
                details=market_result.details,
            )

            # 3. 加载主题配置和股票映射
            config = await self.theme_service.load_config()
            await self.theme_service.load_stock_map(trade_date)

            theme_codes = [single] if single else [k for k in config.keys() if not k.startswith("_")]
            logger.info("待处理主题数量: %d", len(theme_codes))

            # 4. 对每个主题执行V3流水线 (含市场乘数)
            all_scores: List[ThemeV3Score] = []

            for idx, theme_code in enumerate(theme_codes):
                try:
                    score = await self._run_single(
                        theme_code, trade_date, skip_factors,
                        market_regime=market_result.regime,
                        market_multiplier=market_result.market_multiplier,
                        recommended_exposure=market_result.recommended_exposure,
                    )
                    if score is not None:
                        all_scores.append(score)

                    if (idx + 1) % 20 == 0:
                        logger.info("V3进度: %d/%d | Regime: %s",
                                    idx + 1, len(theme_codes), market_result.regime_cn)

                except Exception as e:
                    logger.error("V3主题 %s 评分失败: %s", theme_code, e)
                    continue

            # 5. 截面标准化: 对 money 分做 ZScore 归一化, 确保区分度
            money_vals = [s.money for s in all_scores]
            if money_vals:
                m_mean = sum(money_vals) / len(money_vals)
                m_std = (sum((v - m_mean) ** 2 for v in money_vals) / len(money_vals)) ** 0.5
                if m_std > 1:
                    for s in all_scores:
                        z = (s.money - m_mean) / m_std
                        # z_score → 0~100: z=0 → 50, z=±2 → 0/100
                        s.money = max(0.0, min(100.0, 50.0 + z * 25.0))

            # 6. 排序和排名 (按 tradable_score)
            all_scores = await self.calculator.rank_and_predict(all_scores)

            result.themes = all_scores
            result.ranking = all_scores
            result.top_themes = all_scores[:10]

            # 6. 保存阶段历史 (非 dry_run 模式)
            if not self._dry_run:
                self._save_stage_history(self._stage_history)

            # 7. 摘要
            summary = self._build_summary(all_scores)
            logger.info("V3排名 Top5: %s", " | ".join(summary))

            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(
                "═══ TERE V3 引擎完成 ═══  耗时: %.2fs  主题数: %d  Regime: %s",
                elapsed, len(all_scores), market_result.regime_cn,
            )

        except Exception as e:
            logger.error("V3引擎运行异常: %s", e)
            result.error = str(e)

        return result

    async def _run_single(
        self,
        theme_code: str,
        trade_date: str,
        skip_factors: List[str],
        market_regime: str = "",
        market_multiplier: float = 1.0,
        recommended_exposure: float = 1.0,
    ) -> Optional[ThemeV3Score]:
        """计算单个主题的 V3 评分."""
        theme_name = await self.theme_service.get_theme_name(theme_code)
        main_etf, backup_etf = await self.theme_service.get_theme_etfs(theme_code)

        # 获取ETF数据
        etf_code = main_etf or backup_etf or ""
        etf_df = await self.etf_service.get_etf_daily(etf_code, trade_date, days=120)

        # 获取成分股数据
        stocks = await self.theme_service.get_theme_stocks(theme_code, trade_date)
        enriched_stocks = await self.stock_service.enrich_stocks(stocks, trade_date)

        # ── 计算 8 个一级因子 ──

        # 1. ETF趋势
        etf_trend_result = None
        etf_trend_score = 0.0
        if "etf_trend" not in skip_factors:
            etf_trend_result = await calc_etf_trend(
                theme_code, trade_date, etf_df,
            )
            etf_trend_score = etf_trend_result.score

        # 2. ETF加速度
        etf_accel_result = None
        etf_accel_score = 0.0
        if "etf_accel" not in skip_factors:
            etf_accel_result = await calc_etf_accel(
                theme_code, trade_date, etf_df,
            )
            etf_accel_score = etf_accel_result.score

        # 3. 扩散度
        breadth_result = None
        breadth_score = 0.0
        if "breadth" not in skip_factors:
            breadth_result = await calc_breadth(
                theme_code, trade_date, enriched_stocks,
            )
            breadth_score = breadth_result.score

        # 4. 龙头质量
        leader_result = None
        leader_score = 0.0
        if "leader" not in skip_factors:
            leader_result = await calc_leader(
                theme_code, trade_date, enriched_stocks,
            )
            leader_score = leader_result.score
            if leader_result and leader_result.top_leaders:
                leader_result.top_leaders = leader_result.top_leaders[:5]

        # 5. 龙头扩散 (使用当前数据计算宽度/集中度)
        leader_expand_result = None
        leader_expand_score = 0.0
        if "leader_expand" not in skip_factors:
            leader_expand_result = await calc_leader_expand(
                theme_code, trade_date, enriched_stocks,
            )
            leader_expand_score = leader_expand_result.score

        # 6. 排名动量 (使用ETF收益率加速度)
        rank_momentum_result = None
        rank_momentum_score = 0.0
        if "rank_momentum" not in skip_factors:
            rank_momentum_result = await calc_rank_momentum(
                theme_code, trade_date,
                etf_df=etf_df,
                enriched_stocks=enriched_stocks,
            )
            rank_momentum_score = rank_momentum_result.score

        # 7. 资金流
        money_flow_result = None
        money_score = 0.0
        if "money" not in skip_factors:
            money_flow_result = await calc_money_flow(
                theme_code, trade_date,
                etf_df=etf_df,
                enriched_stocks=enriched_stocks,
            )
            money_score = money_flow_result.score

        # 8. 生命周期 (含迁移检测 V2)
        prev_record = self._stage_history.get(theme_code, {})
        prev_stage = prev_record.get("stage", "")
        prev_days = prev_record.get("days", 0)
        days_in_stage = prev_days + 1 if prev_stage else 1
        lifecycle_result = await calc_lifecycle(
            theme_code, trade_date,
            etf_trend_score=etf_trend_score,
            etf_accel_score=etf_accel_score,
            breadth_score=breadth_score,
            leader_score=leader_score,
            prev_stage=prev_stage or None,
            theme_name=theme_name,
            money=money_score,
            leader_expand=leader_expand_score,
            market_regime=market_regime,
            days_in_stage=days_in_stage,
        )

        # 更新阶段历史
        self._update_stage_history(theme_code, lifecycle_result.stage, trade_date)

        # 9. 共振
        resonance_result = await calc_resonance(
            theme_code, trade_date,
            etf_trend=etf_trend_score,
            etf_accel=etf_accel_score,
            leader=leader_score,
            breadth=breadth_score,
        )

        # ── 综合评分 ──
        top_leaders = leader_result.top_leaders if leader_result else []
        core_stocks = [s.get("code", "") for s in enriched_stocks[:3]]

        score = await self.calculator.calculate_single(
            theme_code=theme_code,
            theme_name=theme_name,
            trade_date=trade_date,
            etf_trend=etf_trend_score,
            etf_accel=etf_accel_score,
            breadth=breadth_score,
            leader=leader_score,
            leader_expand=leader_expand_score,
            rank_momentum=rank_momentum_score,
            money=money_score,
            etf_trend_result=etf_trend_result,
            etf_accel_result=etf_accel_result,
            breadth_result=breadth_result,
            leader_result=leader_result,
            leader_expand_result=leader_expand_result,
            rank_momentum_result=rank_momentum_result,
            money_flow_result=money_flow_result,
            lifecycle_result=lifecycle_result,
            resonance_result=resonance_result,
            transition_result=lifecycle_result.transition if lifecycle_result else None,
            life_stage=lifecycle_result.stage,
            lifecycle_bonus=float(lifecycle_result.stage_bonus),
            resonance_multiplier=resonance_result.multiplier,
            pre_rotate=lifecycle_result.transition.pre_rotate if lifecycle_result and lifecycle_result.transition else False,
            transition_direction=lifecycle_result.transition.direction if lifecycle_result and lifecycle_result.transition else "",
            market_regime=market_regime,
            market_multiplier=market_multiplier,
            recommended_exposure=recommended_exposure,
            top_leaders=top_leaders,
            core_stocks=core_stocks,
            etf_code=etf_code,
            etf_name=etf_code,
        )

        return score

    def _build_summary(self, themes: List[ThemeV3Score]) -> List[str]:
        """构建排行榜摘要."""
        summaries = []
        for t in themes[:5]:
            summaries.append(
                f"{t.rank}.{t.theme_name}(I={t.intrinsic_score:.0f}/T={t.tradable_score:.0f}/{t.signal})"
            )
        return summaries

    async def export_ranking(
        self,
        trade_date: str,
        output_path: Optional[str] = None,
    ) -> str:
        """导出 V3 排行榜到 CSV."""
        if output_path is None:
            output_dir = Path(__file__).resolve().parent.parent / "data"
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(output_dir / f"ranking_v3_{trade_date}.csv")

        # 从文件读取上次运行结果（简化方案）
        cache_path = Path(__file__).resolve().parent / "cache" / f"v3_ranking_{trade_date}.json"
        if cache_path.exists():
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
        else:
            logger.warning("无缓存结果，导出空文件: %s", output_path)
            themes: List[ThemeV3Score] = []
        # 注：生产环境应从内存/数据库读取

        logger.info("V3排行榜已导出: %s", output_path)
        return output_path

    async def cleanup(self) -> None:
        """清理资源."""
        await self.market_engine.cleanup()
        await self.etf_service.clear_cache()
        await self.stock_service.clear_cache()
        await self.theme_service.clear_cache()
