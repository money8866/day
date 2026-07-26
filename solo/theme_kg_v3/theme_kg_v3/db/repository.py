"""数据访问层 - 主题知识图谱仓储操作."""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, desc, func, select, update, delete, or_
from sqlalchemy.orm import Session, joinedload

from theme_kg_v3.schema.models import (
    Theme, IndustryChain, ConceptTag, ThemeETF, ThemeKeyword,
    StockTheme, ThemeRelation, ThemeHistory, ThemeStage,
    LeaderStock, StockRelation, ThemeScoreDaily,
)
from theme_kg_v3.schema.dataclasses import (
    ThemeCreate, ThemeResponse,
    IndustryChainCreate, IndustryChainResponse,
    StockThemeCreate, StockThemeResponse,
    ClassificationResult,
    ConfidenceBreakdown,
    LifecycleResult,
    LeaderAnalysisResult,
)

logger = logging.getLogger(__name__)


# ── 辅助函数 ────────────────────────────────────────────────

def _orm_to_response(orm_obj: Any, response_cls: type, *,
                     exclude: Optional[set[str]] = None) -> Any:
    """将 SQLAlchemy ORM 对象转换为 Pydantic Response 模型.

    UUID 类型字段会被转换为字符串表示，以兼容 Pydantic 模型定义。
    """
    exclude = exclude or set()
    data: dict[str, Any] = {}
    for column in orm_obj.__table__.columns:
        if column.name in exclude:
            continue
        value = getattr(orm_obj, column.name)
        if isinstance(value, uuid.UUID):
            value = str(value)
        data[column.name] = value
    # 处理 relationship 属性不在 __table__.columns 中的字段
    for key in orm_obj.__dict__:
        if key.startswith("_") or key in data or key in exclude:
            continue
        value = getattr(orm_obj, key)
        if isinstance(value, uuid.UUID):
            value = str(value)
        data[key] = value
    return response_cls(**data)


# ── ThemeRepository ─────────────────────────────────────────

class ThemeRepository:
    """主题知识图谱仓储，提供对数据库的 CRUD 及业务查询操作."""

    # ── 主题 (Theme) ────────────────────────────────────────

    @staticmethod
    def create_theme(session: Session, data: ThemeCreate) -> ThemeResponse:
        """创建新主题."""
        theme = Theme(
            code=data.code,
            name_cn=data.name_cn,
            description=data.description,
            level=data.level,
            status=data.status,
            lifecycle_stage=data.lifecycle_stage,
            main_etf_code=data.main_etf_code,
        )
        session.add(theme)
        session.flush()
        session.refresh(theme)
        return _orm_to_response(theme, ThemeResponse)

    @staticmethod
    def get_theme(session: Session, theme_id: uuid.UUID) -> Optional[ThemeResponse]:
        """根据 ID 获取主题."""
        theme = session.get(Theme, theme_id)
        if theme is None:
            return None
        return _orm_to_response(theme, ThemeResponse)

    @staticmethod
    def get_theme_by_code(session: Session, code: str) -> Optional[ThemeResponse]:
        """根据代码获取主题."""
        theme = session.execute(
            select(Theme).where(Theme.code == code)
        ).scalar_one_or_none()
        if theme is None:
            return None
        return _orm_to_response(theme, ThemeResponse)

    @staticmethod
    def get_all_themes(session: Session, active_only: bool = True) -> List[ThemeResponse]:
        """获取所有主题，可按 active 状态过滤."""
        stmt = select(Theme)
        if active_only:
            stmt = stmt.where(Theme.status == "active")
        themes = session.execute(stmt.order_by(Theme.created_at.desc())).scalars().all()
        return [_orm_to_response(t, ThemeResponse) for t in themes]

    @staticmethod
    def update_theme(
        session: Session, theme_id: uuid.UUID, data: Dict
    ) -> Optional[ThemeResponse]:
        """更新主题信息.

        Args:
            data: 需更新的字段键值对.
        """
        theme = session.get(Theme, theme_id)
        if theme is None:
            return None
        for key, value in data.items():
            if hasattr(theme, key):
                setattr(theme, key, value)
        session.flush()
        session.refresh(theme)
        return _orm_to_response(theme, ThemeResponse)

    @staticmethod
    def delete_theme(session: Session, theme_id: uuid.UUID) -> bool:
        """删除主题，返回是否成功删除."""
        theme = session.get(Theme, theme_id)
        if theme is None:
            return False
        session.delete(theme)
        session.flush()
        return True

    # ── 产业链 (IndustryChain) ──────────────────────────────

    @staticmethod
    def create_industry_chain(
        session: Session, data: IndustryChainCreate
    ) -> IndustryChainResponse:
        """创建产业链记录."""
        chain = IndustryChain(
            theme_id=uuid.UUID(data.theme_id) if isinstance(data.theme_id, str) else data.theme_id,
            code=data.code,
            name_cn=data.name_cn,
            description=data.description,
            sort_order=data.sort_order,
        )
        session.add(chain)
        session.flush()
        session.refresh(chain)
        return _orm_to_response(chain, IndustryChainResponse)

    @staticmethod
    def get_industry_chains_by_theme(
        session: Session, theme_id: uuid.UUID
    ) -> List[IndustryChainResponse]:
        """获取某主题的所有产业链."""
        chains = session.execute(
            select(IndustryChain)
            .where(IndustryChain.theme_id == theme_id)
            .order_by(IndustryChain.sort_order)
        ).scalars().all()
        return [_orm_to_response(c, IndustryChainResponse) for c in chains]

    # ── 个股-主题归属 (StockTheme) ────────────────────────

    @staticmethod
    def assign_stock_to_theme(
        session: Session, result: ClassificationResult
    ) -> StockThemeResponse:
        """将个股归属到主题（Upsert 语义：stock_code 已存在则更新，否则插入）.

        Args:
            result: 分类器输出的分类结果.
        """
        # 通过 theme_code 查找主题 ID
        theme = session.execute(
            select(Theme).where(Theme.code == result.primary_theme_code)
        ).scalar_one_or_none()
        if theme is None:
            raise ValueError(
                f"主题代码 {result.primary_theme_code!r} 不存在，无法归属个股 {result.stock_code}"
            )

        existing = session.execute(
            select(StockTheme).where(StockTheme.stock_code == result.stock_code)
        ).scalar_one_or_none()

        if existing:
            # 更新已有记录
            existing.stock_name = result.stock_name
            existing.primary_theme_id = theme.id
            existing.confidence = result.confidence
            existing.confidence_reason = result.confidence_breakdown.reason
            existing.is_leader = result.leader_type is not None
            existing.leader_type = result.leader_type
            session.flush()
            session.refresh(existing)
            return _orm_to_response(existing, StockThemeResponse)
        else:
            # 新建记录
            st = StockTheme(
                stock_code=result.stock_code,
                stock_name=result.stock_name,
                primary_theme_id=theme.id,
                confidence=result.confidence,
                confidence_reason=result.confidence_breakdown.reason,
                secondary_theme_ids=None,
                is_leader=result.leader_type is not None,
                leader_type=result.leader_type,
                is_active=True,
            )
            session.add(st)
            session.flush()
            session.refresh(st)
            return _orm_to_response(st, StockThemeResponse)

    @staticmethod
    def get_stock_theme(
        session: Session, stock_code: str
    ) -> Optional[StockThemeResponse]:
        """根据股票代码获取个股-主题归属."""
        st = session.execute(
            select(StockTheme).where(StockTheme.stock_code == stock_code)
        ).scalar_one_or_none()
        if st is None:
            return None
        return _orm_to_response(st, StockThemeResponse)

    @staticmethod
    def get_stocks_by_theme(
        session: Session, theme_id: uuid.UUID, active_only: bool = True
    ) -> List[StockThemeResponse]:
        """获取某主题下的所有个股."""
        stmt = select(StockTheme).where(StockTheme.primary_theme_id == theme_id)
        if active_only:
            stmt = stmt.where(StockTheme.is_active.is_(True))
        stocks = session.execute(stmt).scalars().all()
        return [_orm_to_response(s, StockThemeResponse) for s in stocks]

    @staticmethod
    def get_stocks_by_leader_type(
        session: Session, theme_id: uuid.UUID, leader_type: str
    ) -> List[StockThemeResponse]:
        """获取某主题下指定龙头类型的个股."""
        stocks = session.execute(
            select(StockTheme)
            .where(
                and_(
                    StockTheme.primary_theme_id == theme_id,
                    StockTheme.leader_type == leader_type,
                    StockTheme.is_active.is_(True),
                )
            )
        ).scalars().all()
        return [_orm_to_response(s, StockThemeResponse) for s in stocks]

    # ── 主题历史 (ThemeHistory) ────────────────────────────

    @staticmethod
    def save_theme_history(session: Session, data: Dict) -> ThemeHistory:
        """保存主题历史数据."""
        hist = ThemeHistory(**data)
        session.add(hist)
        session.flush()
        session.refresh(hist)
        return hist

    @staticmethod
    def get_theme_history(
        session: Session, theme_id: uuid.UUID, start_date: date, end_date: date
    ) -> List[ThemeHistory]:
        """获取某主题在指定日期范围内的历史数据."""
        records = session.execute(
            select(ThemeHistory)
            .where(
                and_(
                    ThemeHistory.theme_id == theme_id,
                    ThemeHistory.trade_date >= start_date,
                    ThemeHistory.trade_date <= end_date,
                )
            )
            .order_by(ThemeHistory.trade_date)
        ).scalars().all()
        return list(records)

    @staticmethod
    def get_latest_theme_history(
        session: Session, theme_id: uuid.UUID
    ) -> Optional[ThemeHistory]:
        """获取某主题最新的历史数据."""
        record = session.execute(
            select(ThemeHistory)
            .where(ThemeHistory.theme_id == theme_id)
            .order_by(desc(ThemeHistory.trade_date))
            .limit(1)
        ).scalar_one_or_none()
        return record

    # ── 生命周期阶段 (ThemeStage) ──────────────────────────

    @staticmethod
    def save_lifecycle_stage(
        session: Session, result: LifecycleResult
    ) -> ThemeStage:
        """保存生命周期阶段分析结果."""
        # 通过 theme_code 查找主题
        theme = session.execute(
            select(Theme).where(Theme.code == result.theme_code)
        ).scalar_one_or_none()
        if theme is None:
            raise ValueError(
                f"主题代码 {result.theme_code!r} 不存在，无法保存生命周期阶段"
            )
        stage = ThemeStage(
            theme_id=theme.id,
            trade_date=result.indicators.get("trade_date", date.today())
            if isinstance(result.indicators, dict)
            else date.today(),
            stage_before=None,
            stage_after=result.current_stage,
            reason=json.dumps(
                {
                    "stage_confidence": result.stage_confidence,
                    "indicators": result.indicators,
                    "next_stage_prediction": result.next_stage_prediction,
                    "days_in_stage": result.days_in_stage,
                },
                ensure_ascii=False,
            ),
        )

        # 检查是否有前一个阶段
        prev_stage = session.execute(
            select(ThemeStage)
            .where(ThemeStage.theme_id == theme.id)
            .order_by(desc(ThemeStage.created_at))
            .limit(1)
        ).scalar_one_or_none()
        if prev_stage is not None:
            stage.stage_before = prev_stage.stage_after

        session.add(stage)
        session.flush()
        session.refresh(stage)
        return stage

    @staticmethod
    def get_theme_stages(
        session: Session, theme_id: uuid.UUID, limit: int = 10
    ) -> List[ThemeStage]:
        """获取某主题的生命周期阶段变更记录."""
        stages = session.execute(
            select(ThemeStage)
            .where(ThemeStage.theme_id == theme_id)
            .order_by(desc(ThemeStage.created_at))
            .limit(limit)
        ).scalars().all()
        return list(stages)

    # ── 龙头股分析 (LeaderStock) ───────────────────────────

    @staticmethod
    def save_leader_analysis(
        session: Session, result: LeaderAnalysisResult
    ) -> List[LeaderStock]:
        """保存龙头分析结果，批量写入 LeaderStock 表.

        根据 analysis_date 将之前的龙头记录置为 inactive，
        然后插入当前分析结果中的 leader 和 core 等。
        """
        theme = session.execute(
            select(Theme).where(Theme.code == result.theme_code)
        ).scalar_one_or_none()
        if theme is None:
            raise ValueError(
                f"主题代码 {result.theme_code!r} 不存在，无法保存龙头分析"
            )

        # 将之前的 active 记录置为 inactive
        session.execute(
            update(LeaderStock)
            .where(
                and_(
                    LeaderStock.theme_id == theme.id,
                    LeaderStock.is_active.is_(True),
                )
            )
            .values(is_active=False)
        )

        saved: List[LeaderStock] = []
        # 按 leader_type 处理各类股票
        leader_mapping: list[tuple[str, list[dict]]] = [
            ("leader", result.leaders),
            ("core", result.cores),
            ("follower", result.followers),
            ("catch_up", result.catch_up_candidates),
            ("eliminated", result.eliminated),
        ]

        for leader_type, stock_list in leader_mapping:
            for item in stock_list:
                ls = LeaderStock(
                    theme_id=theme.id,
                    stock_code=item.get("stock_code", ""),
                    stock_name=item.get("stock_name", ""),
                    leader_type=leader_type,
                    assigned_date=result.analysis_date,
                    consecutive_limit_up=item.get("consecutive_limit_up", 0),
                    cumulative_return=item.get("cumulative_return"),
                    market_cap_billion=item.get("market_cap_billion"),
                    is_active=(leader_type != "eliminated"),
                )
                session.add(ls)
                session.flush()
                session.refresh(ls)
                saved.append(ls)

        return saved

    @staticmethod
    def get_active_leaders(
        session: Session, theme_id: uuid.UUID
    ) -> List[LeaderStock]:
        """获取某主题当前活跃的龙头股."""
        leaders = session.execute(
            select(LeaderStock)
            .where(
                and_(
                    LeaderStock.theme_id == theme_id,
                    LeaderStock.is_active.is_(True),
                )
            )
            .order_by(desc(LeaderStock.created_at))
        ).scalars().all()
        return list(leaders)

    # ── 主题日评分 (ThemeScoreDaily) ───────────────────────

    @staticmethod
    def save_daily_score(
        session: Session, data: Dict
    ) -> ThemeScoreDaily:
        """保存主题日评分数据."""
        score = ThemeScoreDaily(**data)
        session.add(score)
        session.flush()
        session.refresh(score)
        return score

    @staticmethod
    def get_daily_scores(
        session: Session,
        theme_id: uuid.UUID,
        start_date: date,
        end_date: date,
    ) -> List[ThemeScoreDaily]:
        """获取某主题在指定日期范围内的日评分."""
        scores = session.execute(
            select(ThemeScoreDaily)
            .where(
                and_(
                    ThemeScoreDaily.theme_id == theme_id,
                    ThemeScoreDaily.trade_date >= start_date,
                    ThemeScoreDaily.trade_date <= end_date,
                )
            )
            .order_by(ThemeScoreDaily.trade_date)
        ).scalars().all()
        return list(scores)

    # ── 批量操作 ────────────────────────────────────────────

    @staticmethod
    def bulk_upsert_stock_themes(
        session: Session, results: List[ClassificationResult]
    ) -> int:
        """批量 Upsert 个股-主题归属.

        对每个 ClassificationResult，若 stock_code 已存在则更新，
        否则插入。返回影响的行数。

        Args:
            results: 分类结果列表.
        """
        affected = 0
        for result in results:
            theme = session.execute(
                select(Theme).where(Theme.code == result.primary_theme_code)
            ).scalar_one_or_none()
            if theme is None:
                logger.warning(
                    "跳过个股 %s: 主题代码 %s 不存在",
                    result.stock_code,
                    result.primary_theme_code,
                )
                continue

            existing = session.execute(
                select(StockTheme).where(StockTheme.stock_code == result.stock_code)
            ).scalar_one_or_none()

            if existing:
                existing.stock_name = result.stock_name
                existing.primary_theme_id = theme.id
                existing.confidence = result.confidence
                existing.confidence_reason = result.confidence_breakdown.reason
                existing.is_leader = result.leader_type is not None
                existing.leader_type = result.leader_type
            else:
                st = StockTheme(
                    stock_code=result.stock_code,
                    stock_name=result.stock_name,
                    primary_theme_id=theme.id,
                    confidence=result.confidence,
                    confidence_reason=result.confidence_breakdown.reason,
                    is_leader=result.leader_type is not None,
                    leader_type=result.leader_type,
                    is_active=True,
                )
                session.add(st)
            affected += 1

        session.flush()
        return affected

    @staticmethod
    def search_stocks_by_keyword(
        session: Session, keyword: str
    ) -> List[StockThemeResponse]:
        """通过关键词模糊搜索个股（匹配股票名称或代码，ILIKE）. """
        pattern = f"%{keyword}%"
        stocks = session.execute(
            select(StockTheme)
            .where(
                or_(
                    StockTheme.stock_name.ilike(pattern),
                    StockTheme.stock_code.ilike(pattern),
                )
            )
            .order_by(desc(StockTheme.confidence))
        ).scalars().all()
        return [_orm_to_response(s, StockThemeResponse) for s in stocks]

    @staticmethod
    def get_theme_statistics(
        session: Session, theme_id: uuid.UUID
    ) -> Dict:
        """获取某主题的统计信息.

        Returns:
            dict，包含:
            - total_stocks: 归属个股总数
            - leader_count:  龙头股数量
            - core_count:    核心股数量
            - avg_confidence: 平均置信度
            - active_ratio:   活跃个股占比
            - last_updated:   最近更新时间
        """
        stats: Dict[str, Any] = {
            "theme_id": str(theme_id),
            "total_stocks": 0,
            "leader_count": 0,
            "core_count": 0,
            "avg_confidence": 0.0,
            "active_ratio": 0.0,
            "last_updated": None,
        }

        # 总数
        total = session.execute(
            select(func.count(StockTheme.id))
            .where(StockTheme.primary_theme_id == theme_id)
        ).scalar()
        stats["total_stocks"] = total or 0

        if total and total > 0:
            # 活跃数
            active = session.execute(
                select(func.count(StockTheme.id))
                .where(
                    and_(
                        StockTheme.primary_theme_id == theme_id,
                        StockTheme.is_active.is_(True),
                    )
                )
            ).scalar()
            stats["active_ratio"] = round((active or 0) / total, 4)

            # 平均置信度
            avg_conf = session.execute(
                select(func.avg(StockTheme.confidence))
                .where(StockTheme.primary_theme_id == theme_id)
            ).scalar()
            stats["avg_confidence"] = round(float(avg_conf or 0.0), 2)

            # 龙头数 (leader_type = 'leader')
            leader_count = session.execute(
                select(func.count(StockTheme.id))
                .where(
                    and_(
                        StockTheme.primary_theme_id == theme_id,
                        StockTheme.leader_type == "leader",
                        StockTheme.is_active.is_(True),
                    )
                )
            ).scalar()
            stats["leader_count"] = leader_count or 0

            # 核心股数 (leader_type = 'core')
            core_count = session.execute(
                select(func.count(StockTheme.id))
                .where(
                    and_(
                        StockTheme.primary_theme_id == theme_id,
                        StockTheme.leader_type == "core",
                        StockTheme.is_active.is_(True),
                    )
                )
            ).scalar()
            stats["core_count"] = core_count or 0

            # 最近更新时间
            last_upd = session.execute(
                select(func.max(StockTheme.updated_at))
                .where(StockTheme.primary_theme_id == theme_id)
            ).scalar()
            if last_upd is not None:
                stats["last_updated"] = last_upd.isoformat() if hasattr(last_upd, "isoformat") else str(last_upd)

        return stats
