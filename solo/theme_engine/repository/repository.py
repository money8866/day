"""数据持久化层.

封装 SQLAlchemy session，提供8张评分表的 CRUD 操作。
支持 SQLite 和 PostgreSQL。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Type

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from theme_engine.config.settings import DATABASE_URL, ECHO_SQL
from theme_engine.models.dataclasses import (
    BreadthResult,
    ETFStrengthResult,
    FlowResult,
    LeaderResult,
    PurityResult,
    ResonanceResult,
    RotationResult,
    SignalResult,
    StageResult,
    ThemeDailyScore,
)
from theme_engine.models.orm import (
    Base,
    ThemeBreadthScore,
    ThemeDailyScore as ORMThemeDailyScore,
    ThemeETFScore,
    ThemeLeaderScore,
    ThemeResonanceScore,
    ThemeRotation,
    ThemeSignal,
    ThemeStage,
)

logger = logging.getLogger(__name__)


class Repository:
    """数据持久化层.

    封装 SQLAlchemy session，提供8张评分表的 CRUD 操作。
    支持 SQLite 和 PostgreSQL。
    """

    def __init__(self, database_url: Optional[str] = None) -> None:
        """初始化数据库连接.

        Args:
            database_url: 数据库 URL，默认使用配置中的 DATABASE_URL
        """
        self.database_url = database_url or DATABASE_URL
        self._engine = None
        self._async_engine = None
        self._session_factory = None
        self._is_async = False
        self._initialized = False

    async def initialize(self) -> None:
        """初始化数据库引擎和表结构."""
        if self._initialized:
            return

        try:
            url = self.database_url

            # 判断是否异步
            if url.startswith("sqlite"):
                # SQLite 使用 aiosqlite
                if "+aiosqlite" not in url and "+" not in url:
                    url = url.replace("sqlite://", "sqlite+aiosqlite://")
                self._is_async = True
                self._async_engine = create_async_engine(url, echo=ECHO_SQL)
                self._session_factory = sessionmaker(
                    self._async_engine, class_=AsyncSession, expire_on_commit=False
                )

                async with self._async_engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)
            else:
                # PostgreSQL 或其他
                self._is_async = True
                self._async_engine = create_async_engine(url, echo=ECHO_SQL)
                self._session_factory = sessionmaker(
                    self._async_engine, class_=AsyncSession, expire_on_commit=False
                )

                async with self._async_engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)

            self._initialized = True
            logger.info("数据库初始化完成: %s", self.database_url)
        except Exception as e:
            logger.error("数据库初始化失败: %s", e)
            raise

    async def _get_session(self) -> AsyncSession:
        """获取数据库会话."""
        if not self._initialized:
            await self.initialize()
        session = self._session_factory()
        return session

    @staticmethod
    def _now() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Upsert 辅助方法 ──────────────────────────────────────

    async def _upsert(
        self,
        orm_class: Type,
        unique_keys: Dict[str, Any],
        update_data: Dict[str, Any],
    ) -> None:
        """通用 upsert 操作.

        Args:
            orm_class: ORM 模型类
            unique_keys: 用于查找的唯一键字典 (theme_code + trade_date)
            update_data: 待更新/插入的数据
        """
        session = await self._get_session()
        async with session.begin():
            try:
                # 查询已有记录（异步 SQLAlchemy 必须用 select）
                from sqlalchemy import select
                stmt = select(orm_class)
                for key, value in unique_keys.items():
                    stmt = stmt.where(
                        getattr(orm_class, key) == value
                    )
                existing = await session.execute(stmt)
                instance = existing.scalar_one_or_none()

                if instance:
                    # 更新
                    for key, value in update_data.items():
                        setattr(instance, key, value)
                    logger.debug(
                        "更新 %s: %s", orm_class.__tablename__, unique_keys
                    )
                else:
                    # 插入
                    instance = orm_class(**unique_keys, **update_data)
                    session.add(instance)
                    logger.debug(
                        "插入 %s: %s", orm_class.__tablename__, unique_keys
                    )
            except Exception as e:
                logger.error(
                    "Upsert %s 失败: %s, keys=%s",
                    orm_class.__tablename__,
                    e,
                    unique_keys,
                )
                raise

    # ── ETF Score ──────────────────────────────────────────

    async def save_etf_score(self, data: ETFStrengthResult) -> None:
        """保存或更新 ETF 评分."""
        unique_keys = {
            "theme_code": data.theme_code,
            "trade_date": data.trade_date,
        }
        update_data = {
            "main_etf": data.main_etf,
            "backup_etf": data.backup_etf,
            "etf_strength": data.etf_strength,
            "trend_score": data.trend_score,
            "momentum_score": data.momentum_score,
            "alpha_score": data.alpha_score,
            "volume_score": data.volume_score,
            "money_flow_score": data.money_flow_score,
            "volatility_score": data.volatility_score,
            "relative_strength": data.relative_strength,
            "ma_trend": data.ma_trend,
            "slope": data.slope,
            "atr_score": data.atr_score,
            "breakout_score": data.breakout_score,
            "details": data.details,
            "created_at": datetime.now(),
        }
        await self._upsert(ThemeETFScore, unique_keys, update_data)

    # ── Leader Score ───────────────────────────────────────

    async def save_leader_score(self, data: LeaderResult) -> None:
        """保存或更新龙头评分."""
        unique_keys = {
            "theme_code": data.theme_code,
            "trade_date": data.trade_date,
        }
        update_data = {
            "leader_count": data.leader_count,
            "core_count": data.core_count,
            "follower_count": data.follower_count,
            "leader_strength": data.leader_strength,
            "leader_trend": data.leader_trend,
            "leader_alpha": data.leader_alpha,
            "relative_strength": data.relative_strength,
            "volume_score": data.volume_score,
            "money_flow_score": data.money_flow_score,
            "institution_score": data.institution_score,
            "macd_score": data.macd_score,
            "ma_trend_score": data.ma_trend_score,
            "leaders": data.leaders,
            "cores": data.cores,
            "details": data.details,
            "created_at": datetime.now(),
        }
        await self._upsert(ThemeLeaderScore, unique_keys, update_data)

    # ── Breadth Score ─────────────────────────────────────

    async def save_breadth_score(self, data: BreadthResult) -> None:
        """保存或更新扩散度评分."""
        unique_keys = {
            "theme_code": data.theme_code,
            "trade_date": data.trade_date,
        }
        update_data = {
            "total_stocks": data.total_stocks,
            "breadth_score": data.breadth_score,
            "up_ratio": data.up_ratio,
            "limit_up_ratio": data.limit_up_ratio,
            "new_high_20d_ratio": data.new_high_20d_ratio,
            "above_ma20_ratio": data.above_ma20_ratio,
            "above_ma60_ratio": data.above_ma60_ratio,
            "above_ma120_ratio": data.above_ma120_ratio,
            "amount_diffusion": data.amount_diffusion,
            "return_median": data.return_median,
            "avg_alpha": data.avg_alpha,
            "avg_relative_alpha": data.avg_relative_alpha,
            "details": data.details,
            "created_at": datetime.now(),
        }
        await self._upsert(ThemeBreadthScore, unique_keys, update_data)

    # ── Resonance Score ───────────────────────────────────

    async def save_resonance_score(self, data: ResonanceResult) -> None:
        """保存或更新共振评分."""
        unique_keys = {
            "theme_code": data.theme_code,
            "trade_date": data.trade_date,
        }
        update_data = {
            "resonance_score": data.resonance_score,
            "etf_strength": data.etf_strength,
            "theme_breadth": data.theme_breadth,
            "leader_score": data.leader_score,
            "consistency_score": data.consistency_score,
            "variance_penalty": data.variance_penalty,
            "std": data.std,
            "correlation": data.correlation,
            "details": data.details,
            "created_at": datetime.now(),
        }
        await self._upsert(ThemeResonanceScore, unique_keys, update_data)

    # ── Stage ────────────────────────────────────────────

    async def save_stage(self, data: StageResult) -> None:
        """保存或更新生命周期阶段."""
        unique_keys = {
            "theme_code": data.theme_code,
            "trade_date": data.trade_date,
        }
        update_data = {
            "current_stage": data.current_stage,
            "stage_confidence": data.stage_confidence,
            "days_in_stage": data.days_in_stage,
            "stage_progress": data.stage_progress,
            "next_stage": data.next_stage,
            "indicators": data.indicators,
            "details": data.details,
            "created_at": datetime.now(),
        }
        await self._upsert(ThemeStage, unique_keys, update_data)

    # ── Rotation ─────────────────────────────────────────

    async def save_rotation(self, data: RotationResult) -> None:
        """保存或更新轮动概率."""
        unique_keys = {
            "theme_code": data.theme_code,
            "trade_date": data.trade_date,
        }
        update_data = {
            "rotation_score": data.rotation_score,
            "prob_3d": data.prob_3d,
            "prob_5d": data.prob_5d,
            "prob_10d": data.prob_10d,
            "etf_momentum": data.etf_momentum,
            "leader_momentum": data.leader_momentum,
            "breadth_trend": data.breadth_trend,
            "resonance_trend": data.resonance_trend,
            "details": data.details,
            "created_at": datetime.now(),
        }
        await self._upsert(ThemeRotation, unique_keys, update_data)

    # ── Signal ───────────────────────────────────────────

    async def save_signal(self, data: SignalResult) -> None:
        """保存或更新信号."""
        unique_keys = {
            "theme_code": data.theme_code,
            "trade_date": data.trade_date,
        }
        update_data = {
            "signal": data.signal,
            "signal_strength": data.signal_strength,
            "reasons": data.reasons,
            "details": data.details,
            "created_at": datetime.now(),
        }
        await self._upsert(ThemeSignal, unique_keys, update_data)

    # ── Daily Score ──────────────────────────────────────

    async def save_daily_score(self, data: ThemeDailyScore) -> None:
        """保存或更新每日综合评分."""
        unique_keys = {
            "theme_code": data.theme_code,
            "trade_date": data.trade_date,
        }
        update_data: Dict[str, Any] = {
            "theme_name": data.theme_name,
            "rank": data.rank,
            "total_score": data.total_score,
            "etf_strength": data.etf_strength,
            "breadth_score": data.breadth_score,
            "leader_strength": data.leader_strength,
            "purity_score": data.purity_score,
            "resonance_score": data.resonance_score,
            "flow_score": data.flow_score,
            "stage": data.stage,
            "rotation_prob": data.rotation_prob,
            "signal": data.signal,
            "top_leaders": data.top_leaders,
            "top_stocks": data.top_stocks,
            "main_etf": data.main_etf,
            "backup_etf": data.backup_etf,
            "explanations": data.explanations,
            "summary": data.summary,
            "created_at": datetime.now(),
        }
        # 如果 explanations 包含 ExplainItem 对象，转为 dict
        if update_data["explanations"] and isinstance(
            update_data["explanations"], list
        ):
            update_data["explanations"] = [
                e if isinstance(e, dict) else {"reason": e.reason, "score": e.score, "weight": e.weight}
                for e in update_data["explanations"]
            ]

        await self._upsert(ORMThemeDailyScore, unique_keys, update_data)

    # ── History Query ──────────────────────────────────────

    async def get_history(
        self, orm_class: Type, theme_code: str, days: int = 20
    ) -> List[Any]:
        """获取主题的历史记录.

        Args:
            orm_class: ORM 模型类
            theme_code: 主题代码
            days: 回溯天数

        Returns:
            ORM 实例列表，按 trade_date 降序
        """
        try:
            from sqlalchemy import desc, select
            session = await self._get_session()
            async with session.begin():
                    stmt = (
                        select(orm_class)
                        .where(orm_class.theme_code == theme_code)
                        .order_by(desc(orm_class.trade_date))
                        .limit(days)
                    )
                    result = await session.execute(stmt)
                    return list(result.scalars().all())
        except Exception as e:
            logger.error("查询历史 %s %s 失败: %s", orm_class.__tablename__, theme_code, e)
            return []

    async def get_latest_stage(self, theme_code: str) -> Optional[StageResult]:
        """获取主题的最新生命周期阶段.

        Returns:
            StageResult 或 None
        """
        try:
            session = await self._get_session()
            async with session.begin():
                from sqlalchemy import desc, select

                stmt = (
                    select(ThemeStage)
                    .where(ThemeStage.theme_code == theme_code)
                    .order_by(desc(ThemeStage.trade_date))
                    .limit(1)
                )
                result = await session.execute(stmt)
                instance = result.scalar_one_or_none()

                if instance:
                    return StageResult(
                        theme_code=instance.theme_code,
                        trade_date=instance.trade_date,
                        current_stage=instance.current_stage,
                        stage_confidence=instance.stage_confidence,
                        days_in_stage=instance.days_in_stage,
                        stage_progress=instance.stage_progress,
                        next_stage=instance.next_stage,
                        indicators=instance.indicators or {},
                        details=instance.details or {},
                    )
                return None
        except Exception as e:
            logger.error("查询最新阶段 %s 失败: %s", theme_code, e)
            return None

    async def get_daily_ranking(
        self, trade_date: str, top_n: int = 20
    ) -> List[ORMThemeDailyScore]:
        """获取指定日期的排行榜.

        Args:
            trade_date: 交易日
            top_n: 前N名

        Returns:
            ORMThemeDailyScore 实例列表，按 rank 升序
        """
        try:
            from sqlalchemy import select
            session = await self._get_session()
            async with session.begin():
                    stmt = (
                        select(ORMThemeDailyScore)
                        .where(ORMThemeDailyScore.trade_date == trade_date)
                        .order_by(ORMThemeDailyScore.rank)
                        .limit(top_n)
                    )
                    result = await session.execute(stmt)
                    return list(result.scalars().all())
        except Exception as e:
            logger.error("查询排行榜 %s 失败: %s", trade_date, e)
            return []

    # ── Cleanup ──────────────────────────────────────────

    async def close(self) -> None:
        """关闭数据库连接."""
        try:
            if self._async_engine:
                await self._async_engine.dispose()
                logger.info("数据库连接已关闭")
        except Exception as e:
            logger.error("关闭数据库连接失败: %s", e)
