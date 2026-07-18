#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
仓位管理模块
============
基于三层择时综合评分，计算每只股票的建议仓位，
并满足总量限制、单只上限、主题集中度限制。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

import pandas as pd

from .signal import TimingSignal

LOG = logging.getLogger("timing_trading.position")


def score_to_position_ratio(composite_score: float, config: dict) -> float:
    """评分 → 仓位比例映射

    从 config.position.score_to_position 读取阶梯映射
    默认:
        score >= 90 → 1.0
        score >= 70 → 0.7
        score >= 50 → 0.4
        score >= 30 → 0.2
        score <  30 → 0.05
    """
    mapping = config.get("score_to_position", {})
    # 按score降序找到第一个满足的
    sorted_thresholds = sorted(
        [(int(k), float(v)) for k, v in mapping.items()],
        key=lambda x: x[0], reverse=True
    )
    for threshold, ratio in sorted_thresholds:
        if composite_score >= threshold:
            return ratio
    return 0.05


def calculate_positions(
    signals: List[TimingSignal],
    config: dict,
    market_position_suggest: float = 1.0,
) -> pd.DataFrame:
    """根据信号列表计算最终仓位分配

    流程:
        1. 只取 signal_type == "buy" 的信号
        2. 按 composite_score 降序排列
        3. 计算每只初始仓位 (score -> ratio * market_position_suggest)
        4. 应用约束: 单只上限、主题上限、总量上限

    返回:
        DataFrame: ts_code, stock_name, score, signal_type, position, theme, ...
    """
    pos_cfg = config.get("position", {})
    max_per_stock = pos_cfg.get("max_per_stock", 0.15)
    max_per_theme = pos_cfg.get("max_per_theme", 0.35)
    max_total = pos_cfg.get("max_total", 1.0)
    base_position = pos_cfg.get("base_position", 0.20)

    if not signals:
        return pd.DataFrame()

    # 1. 筛选买入信号
    buy_signals = [s for s in signals if s.signal_type == "buy"]
    if not buy_signals:
        LOG.info("无买入信号，建议仓位: %.0f%%", base_position * 100 * market_position_suggest)
        return pd.DataFrame()

    # 2. 排序
    buy_signals.sort(key=lambda s: s.composite_score, reverse=True)

    # 3. 构建DataFrame
    records = []
    for s in buy_signals:
        raw_position = score_to_position_ratio(s.composite_score, pos_cfg)
        theme_name = s.details.get("best_theme", "")
        records.append({
            "ts_code": s.ts_code,
            "stock_name": s.stock_name,
            "composite_score": s.composite_score,
            "entry_score": s.entry_score,
            "theme_score": s.theme_score,
            "signal_type": s.signal_type,
            "raw_position": raw_position,
            "theme": theme_name,
            "primary_entry": s.primary_entry,
        })

    df = pd.DataFrame(records)

    # 4. 应用市场调整
    df["position"] = df["raw_position"] * market_position_suggest

    # 5. 单只上限
    df["position"] = df["position"].clip(upper=max_per_stock)

    # 6. 主题集中度限制
    if "theme" in df.columns and max_per_theme < 1.0:
        for theme_name in df["theme"].unique():
            if not theme_name:
                continue
            mask = df["theme"] == theme_name
            theme_total = df.loc[mask, "position"].sum()
            if theme_total > max_per_theme:
                df.loc[mask, "position"] *= (max_per_theme / theme_total)

    # 7. 总仓位上限
    total_pos = df["position"].sum()
    if total_pos > max_total:
        df["position"] *= (max_total / total_pos)

    # 8. 归一化到总仓位
    df["position_pct"] = (df["position"] * 100).round(1)
    df = df.sort_values("position", ascending=False).reset_index(drop=True)

    LOG.info("仓位分配: %d 只标的, 总仓位 %.1f%%",
             len(df), df["position"].sum() * 100)
    return df


def get_position_summary(position_df: pd.DataFrame) -> dict:
    """生成仓位摘要"""
    if position_df.empty:
        return {"total_positions": 0, "total_capital_ratio": 0}

    return {
        "total_positions": len(position_df),
        "total_capital_ratio": round(position_df["position"].sum() * 100, 1),
        "avg_score": round(position_df["composite_score"].mean(), 1),
        "top_position": round(position_df["position"].max() * 100, 1),
        "primary_theme": position_df.groupby("theme")["position"].sum().nlargest(3).to_dict()
            if "theme" in position_df.columns else {},
        "top_stocks": position_df.head(5)[["stock_name", "position_pct"]].to_dict("records"),
    }
