"""TERE V1 SQLAlchemy ORM 模型 — 8张评分表."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column,
    String,
    Float,
    Integer,
    DateTime,
    Text,
    JSON,
    create_engine,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ════════════════════════════════════════════════════════════
#  1. theme_etf_score
# ════════════════════════════════════════════════════════════

class ThemeETFScore(Base):
    __tablename__ = "theme_etf_score"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    trade_date: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    main_etf: Mapped[str] = mapped_column(String(20), nullable=False)
    backup_etf: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    trend_score: Mapped[float] = mapped_column(Float, default=0)
    momentum_score: Mapped[float] = mapped_column(Float, default=0)
    alpha_score: Mapped[float] = mapped_column(Float, default=0)
    volume_score: Mapped[float] = mapped_column(Float, default=0)
    money_flow_score: Mapped[float] = mapped_column(Float, default=0)
    volatility_score: Mapped[float] = mapped_column(Float, default=0)
    relative_strength: Mapped[float] = mapped_column(Float, default=0)
    ma_trend: Mapped[float] = mapped_column(Float, default=0)
    slope: Mapped[float] = mapped_column(Float, default=0)
    atr_score: Mapped[float] = mapped_column(Float, default=0)
    breakout_score: Mapped[float] = mapped_column(Float, default=0)
    etf_strength: Mapped[float] = mapped_column(Float, default=0)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("theme_code", "trade_date", name="uq_etf_score"),
        Index("ix_etf_trade_theme", "trade_date", "theme_code"),
    )


# ════════════════════════════════════════════════════════════
#  2. theme_leader_score
# ════════════════════════════════════════════════════════════

class ThemeLeaderScore(Base):
    __tablename__ = "theme_leader_score"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    trade_date: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    leader_count: Mapped[int] = mapped_column(Integer, default=0)
    core_count: Mapped[int] = mapped_column(Integer, default=0)
    follower_count: Mapped[int] = mapped_column(Integer, default=0)
    leader_trend: Mapped[float] = mapped_column(Float, default=0)
    leader_alpha: Mapped[float] = mapped_column(Float, default=0)
    relative_strength: Mapped[float] = mapped_column(Float, default=0)
    volume_score: Mapped[float] = mapped_column(Float, default=0)
    money_flow_score: Mapped[float] = mapped_column(Float, default=0)
    institution_score: Mapped[float] = mapped_column(Float, default=0)
    macd_score: Mapped[float] = mapped_column(Float, default=0)
    ma_trend_score: Mapped[float] = mapped_column(Float, default=0)
    leader_strength: Mapped[float] = mapped_column(Float, default=0)
    leaders: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    cores: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("theme_code", "trade_date", name="uq_leader_score"),
        Index("ix_leader_trade_theme", "trade_date", "theme_code"),
    )


# ════════════════════════════════════════════════════════════
#  3. theme_breadth_score
# ════════════════════════════════════════════════════════════

class ThemeBreadthScore(Base):
    __tablename__ = "theme_breadth_score"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    trade_date: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    total_stocks: Mapped[int] = mapped_column(Integer, default=0)
    up_ratio: Mapped[float] = mapped_column(Float, default=0)
    limit_up_ratio: Mapped[float] = mapped_column(Float, default=0)
    new_high_20d_ratio: Mapped[float] = mapped_column(Float, default=0)
    above_ma20_ratio: Mapped[float] = mapped_column(Float, default=0)
    above_ma60_ratio: Mapped[float] = mapped_column(Float, default=0)
    above_ma120_ratio: Mapped[float] = mapped_column(Float, default=0)
    amount_diffusion: Mapped[float] = mapped_column(Float, default=0)
    return_median: Mapped[float] = mapped_column(Float, default=0)
    avg_alpha: Mapped[float] = mapped_column(Float, default=0)
    avg_relative_alpha: Mapped[float] = mapped_column(Float, default=0)
    breadth_score: Mapped[float] = mapped_column(Float, default=0)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("theme_code", "trade_date", name="uq_breadth_score"),
        Index("ix_breadth_trade_theme", "trade_date", "theme_code"),
    )


# ════════════════════════════════════════════════════════════
#  4. theme_resonance_score
# ════════════════════════════════════════════════════════════

class ThemeResonanceScore(Base):
    __tablename__ = "theme_resonance_score"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    trade_date: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    etf_strength: Mapped[float] = mapped_column(Float, default=0)
    theme_breadth: Mapped[float] = mapped_column(Float, default=0)
    leader_score: Mapped[float] = mapped_column(Float, default=0)
    consistency_score: Mapped[float] = mapped_column(Float, default=0)
    variance_penalty: Mapped[float] = mapped_column(Float, default=0)
    std: Mapped[float] = mapped_column(Float, default=0)
    correlation: Mapped[float] = mapped_column(Float, default=0)
    resonance_score: Mapped[float] = mapped_column(Float, default=0)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("theme_code", "trade_date", name="uq_resonance_score"),
        Index("ix_resonance_trade_theme", "trade_date", "theme_code"),
    )


# ════════════════════════════════════════════════════════════
#  5. theme_stage
# ════════════════════════════════════════════════════════════

class ThemeStage(Base):
    __tablename__ = "theme_stage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    trade_date: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    current_stage: Mapped[str] = mapped_column(String(20), nullable=False)
    stage_confidence: Mapped[float] = mapped_column(Float, default=0)
    days_in_stage: Mapped[int] = mapped_column(Integer, default=0)
    stage_progress: Mapped[float] = mapped_column(Float, default=0)
    next_stage: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    indicators: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("theme_code", "trade_date", name="uq_stage"),
        Index("ix_stage_trade_theme", "trade_date", "theme_code"),
    )


# ════════════════════════════════════════════════════════════
#  6. theme_rotation
# ════════════════════════════════════════════════════════════

class ThemeRotation(Base):
    __tablename__ = "theme_rotation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    trade_date: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    prob_3d: Mapped[float] = mapped_column(Float, default=0)
    prob_5d: Mapped[float] = mapped_column(Float, default=0)
    prob_10d: Mapped[float] = mapped_column(Float, default=0)
    etf_momentum: Mapped[float] = mapped_column(Float, default=0)
    leader_momentum: Mapped[float] = mapped_column(Float, default=0)
    breadth_trend: Mapped[float] = mapped_column(Float, default=0)
    resonance_trend: Mapped[float] = mapped_column(Float, default=0)
    rotation_score: Mapped[float] = mapped_column(Float, default=0)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("theme_code", "trade_date", name="uq_rotation"),
        Index("ix_rotation_trade_theme", "trade_date", "theme_code"),
    )


# ════════════════════════════════════════════════════════════
#  7. theme_signal
# ════════════════════════════════════════════════════════════

class ThemeSignal(Base):
    __tablename__ = "theme_signal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    trade_date: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    signal: Mapped[str] = mapped_column(String(20), nullable=False)
    signal_strength: Mapped[float] = mapped_column(Float, default=0)
    reasons: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("theme_code", "trade_date", name="uq_signal"),
        Index("ix_signal_trade_theme", "trade_date", "theme_code"),
    )


# ════════════════════════════════════════════════════════════
#  8. theme_daily_score（综合排行榜）
# ════════════════════════════════════════════════════════════

class ThemeDailyScore(Base):
    __tablename__ = "theme_daily_score"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    theme_code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    trade_date: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    theme_name: Mapped[str] = mapped_column(String(100), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    total_score: Mapped[float] = mapped_column(Float, default=0)
    etf_strength: Mapped[float] = mapped_column(Float, default=0)
    breadth_score: Mapped[float] = mapped_column(Float, default=0)
    leader_strength: Mapped[float] = mapped_column(Float, default=0)
    purity_score: Mapped[float] = mapped_column(Float, default=0)
    resonance_score: Mapped[float] = mapped_column(Float, default=0)
    flow_score: Mapped[float] = mapped_column(Float, default=0)
    stage: Mapped[str] = mapped_column(String(20), default="birth")
    rotation_prob: Mapped[float] = mapped_column(Float, default=0)
    signal: Mapped[str] = mapped_column(String(20), default="WATCH")
    top_leaders: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    top_stocks: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    main_etf: Mapped[str] = mapped_column(String(20), default="")
    backup_etf: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    explanations: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    __table_args__ = (
        UniqueConstraint("theme_code", "trade_date", name="uq_daily_score"),
        Index("ix_daily_trade_rank", "trade_date", "rank"),
        Index("ix_daily_trade_score", "trade_date", "total_score"),
    )
