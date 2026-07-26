from __future__ import annotations

import uuid
import datetime
from typing import Optional

from sqlalchemy import (
    String, Integer, Float, Boolean, Date, DateTime, Text,
    ForeignKey, UniqueConstraint, Index, CheckConstraint, JSON,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Theme(Base):
    __tablename__ = "theme"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name_cn: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    level: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    lifecycle_stage: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    main_etf_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    # Relationships
    industry_chains: Mapped[list[IndustryChain]] = relationship(
        "IndustryChain", back_populates="theme", cascade="all, delete-orphan"
    )
    etfs: Mapped[list[ThemeETF]] = relationship(
        "ThemeETF", back_populates="theme", cascade="all, delete-orphan"
    )
    keywords: Mapped[list[ThemeKeyword]] = relationship(
        "ThemeKeyword", back_populates="theme", cascade="all, delete-orphan"
    )
    stock_themes: Mapped[list[StockTheme]] = relationship(
        "StockTheme", back_populates="primary_theme", cascade="all, delete-orphan"
    )
    history_records: Mapped[list[ThemeHistory]] = relationship(
        "ThemeHistory", back_populates="theme", cascade="all, delete-orphan"
    )
    stage_records: Mapped[list[ThemeStage]] = relationship(
        "ThemeStage", back_populates="theme", cascade="all, delete-orphan"
    )
    leader_records: Mapped[list[LeaderStock]] = relationship(
        "LeaderStock", back_populates="theme", cascade="all, delete-orphan"
    )
    daily_scores: Mapped[list[ThemeScoreDaily]] = relationship(
        "ThemeScoreDaily", back_populates="theme", cascade="all, delete-orphan"
    )
    source_relations: Mapped[list[ThemeRelation]] = relationship(
        "ThemeRelation", back_populates="source_theme",
        foreign_keys="ThemeRelation.source_theme_id",
        cascade="all, delete-orphan",
    )
    target_relations: Mapped[list[ThemeRelation]] = relationship(
        "ThemeRelation", back_populates="target_theme",
        foreign_keys="ThemeRelation.target_theme_id",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_theme_status", "status"),
        Index("idx_theme_lifecycle", "lifecycle_stage"),
    )


class IndustryChain(Base):
    __tablename__ = "industry_chain"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    theme_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("theme.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name_cn: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    theme: Mapped[Theme] = relationship("Theme", back_populates="industry_chains")

    __table_args__ = (
        UniqueConstraint("theme_id", "name_cn", name="uq_chain_theme_name"),
        Index("idx_chain_theme", "theme_id"),
    )


class ConceptTag(Base):
    __tablename__ = "concept_tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name_cn: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        Index("idx_concept_category", "category"),
    )


class ThemeETF(Base):
    __tablename__ = "theme_etf"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    theme_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("theme.id", ondelete="CASCADE"), nullable=False
    )
    etf_code: Mapped[str] = mapped_column(String(16), nullable=False)
    etf_name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_main: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    theme: Mapped[Theme] = relationship("Theme", back_populates="etfs")

    __table_args__ = (
        UniqueConstraint("theme_id", "etf_code", name="uq_theme_etf"),
        Index("idx_etf_theme", "theme_id"),
        Index("idx_etf_code", "etf_code"),
    )


class ThemeKeyword(Base):
    __tablename__ = "theme_keywords"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    theme_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("theme.id", ondelete="CASCADE"), nullable=False
    )
    keyword: Mapped[str] = mapped_column(String(128), nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    keyword_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    is_exclude: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    theme: Mapped[Theme] = relationship("Theme", back_populates="keywords")

    __table_args__ = (
        UniqueConstraint("theme_id", "keyword", name="uq_theme_keyword"),
        Index("idx_keyword_theme", "theme_id"),
        Index("idx_keyword_text", "keyword"),
    )


class StockTheme(Base):
    __tablename__ = "stock_theme"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    stock_code: Mapped[str] = mapped_column(String(16), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(64), nullable=False)
    primary_theme_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("theme.id", ondelete="RESTRICT"), nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    secondary_theme_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_leader: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    leader_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    industry_chain_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    concept_tag_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), nullable=True
    )

    primary_theme: Mapped[Theme] = relationship("Theme", back_populates="stock_themes")

    __table_args__ = (
        UniqueConstraint("stock_code", name="uq_stock_theme_code"),
        Index("idx_stock_theme_primary", "primary_theme_id"),
        Index("idx_stock_theme_leader", "is_leader"),
        Index("idx_stock_theme_active", "is_active"),
        Index("idx_stock_secondary", "secondary_theme_ids"),
        Index("idx_stock_industry_chain", "industry_chain_ids"),
        Index("idx_stock_concept_tags", "concept_tag_ids"),
    )


class ThemeRelation(Base):
    __tablename__ = "theme_relation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source_theme_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("theme.id", ondelete="CASCADE"), nullable=False
    )
    target_theme_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("theme.id", ondelete="CASCADE"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    strength: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    source_theme: Mapped[Theme] = relationship(
        "Theme", back_populates="source_relations", foreign_keys=[source_theme_id]
    )
    target_theme: Mapped[Theme] = relationship(
        "Theme", back_populates="target_relations", foreign_keys=[target_theme_id]
    )

    __table_args__ = (
        UniqueConstraint(
            "source_theme_id", "target_theme_id", "relation_type",
            name="uq_theme_relation",
        ),
        CheckConstraint("strength >= 0 AND strength <= 1", name="ck_relation_strength"),
    )


class ThemeHistory(Base):
    __tablename__ = "theme_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    theme_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("theme.id", ondelete="CASCADE"), nullable=False
    )
    trade_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    lifecycle_stage: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    momentum_5d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    momentum_20d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    momentum_60d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volume_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    leader_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_market_cap_billion: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_return_5d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_return_20d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    turnover_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sentiment_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    theme: Mapped[Theme] = relationship("Theme", back_populates="history_records")

    __table_args__ = (
        UniqueConstraint("theme_id", "trade_date", name="uq_theme_history_date"),
        Index("idx_theme_hist_date", "trade_date"),
        Index("idx_theme_hist_lifecycle", "lifecycle_stage"),
    )


class ThemeStage(Base):
    __tablename__ = "theme_stage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    theme_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("theme.id", ondelete="CASCADE"), nullable=False
    )
    trade_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    stage_before: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    stage_after: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    theme: Mapped[Theme] = relationship("Theme", back_populates="stage_records")


class LeaderStock(Base):
    __tablename__ = "leader_stock"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    theme_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("theme.id", ondelete="CASCADE"), nullable=False
    )
    stock_code: Mapped[str] = mapped_column(String(16), nullable=False)
    stock_name: Mapped[str] = mapped_column(String(64), nullable=False)
    leader_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    assigned_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    consecutive_limit_up: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cumulative_return: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    market_cap_billion: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    theme: Mapped[Theme] = relationship("Theme", back_populates="leader_records")

    __table_args__ = (
        UniqueConstraint(
            "theme_id", "stock_code", "assigned_date",
            name="uq_leader_stock",
        ),
        Index("idx_leader_theme", "theme_id"),
        Index("idx_leader_type", "leader_type"),
    )


class StockRelation(Base):
    __tablename__ = "stock_relation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source_stock_code: Mapped[str] = mapped_column(String(16), nullable=False)
    target_stock_code: Mapped[str] = mapped_column(String(16), nullable=False)
    relation_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    strength: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    theme_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("theme.id", ondelete="CASCADE"), nullable=True
    )


class ThemeScoreDaily(Base):
    __tablename__ = "theme_score_daily"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    theme_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("theme.id", ondelete="CASCADE"), nullable=False
    )
    trade_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    total_score: Mapped[float] = mapped_column(Float, nullable=False)
    momentum_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volume_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    breadth_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sentiment_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    leader_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    etf_corr_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    capital_flow_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    detail_json: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)

    theme: Mapped[Theme] = relationship("Theme", back_populates="daily_scores")

    __table_args__ = (
        UniqueConstraint("theme_id", "trade_date", name="uq_score_daily"),
        Index("idx_score_theme_date", "theme_id", "trade_date"),
        Index("idx_score_date", "trade_date"),
    )
