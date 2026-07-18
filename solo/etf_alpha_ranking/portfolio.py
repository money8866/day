#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Portfolio Rules
===============
Single-ETF strategy decision engine.

BUY:
    Prediction Rank == 1
    AND Prediction Score > 85
    AND Theme Persistence > 75
    AND Leader Score > 70

HOLD (keep current position):
    Current ETF Rank <= 10

SELL:
    Rank > 20
    OR Theme Persistence < 60
    OR ETF closes below MA60

Minimum holding period: 10 trading days.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

LOG = logging.getLogger("etf_alpha_ranking.portfolio")


@dataclass
class Position:
    etf: str = ""
    entry_date: str = ""
    entry_price: float = 0.0
    holding_days: int = 0
    rank: int = 0


@dataclass
class TradeSignal:
    signal: str = "HOLD"          # BUY / HOLD / SELL
    etf: str = ""
    reason: str = ""
    confidence: float = 0.0
    expected_holding_days: int = 40


class PortfolioEngine:
    def __init__(self, config: dict):
        pcfg = config.get("portfolio", {})
        buy = pcfg.get("buy", {})
        hold = pcfg.get("hold", {})
        sell = pcfg.get("sell", {})
        self.buy_rank = buy.get("rank", 1)
        self.hold_max_rank = hold.get("max_rank", 10)
        self.sell_rank = sell.get("rank", 20)
        self.sell_theme = sell.get("theme_persistence", 60)
        self.sell_break_ma60 = sell.get("break_ma60", True)
        self.min_holding = pcfg.get("min_holding_days", 10)

    def evaluate(self, rank: int, prediction_score: float,
                 theme_persistence: float, leader_score: float,
                 below_ma60: bool = False, etf: str = "") -> TradeSignal:
        """Top-1 strategy: rank==1 -> BUY; else SELL if weak, else HOLD."""
        if rank == self.buy_rank:
            return TradeSignal(
                signal="BUY", etf=etf,
                reason=f"Rank={rank},Score={prediction_score:.1f},"
                       f"Theme={theme_persistence:.1f},Leader={leader_score:.1f}",
                confidence=min(100.0, prediction_score),
                expected_holding_days=40)
        if rank > self.sell_rank:
            return TradeSignal(signal="SELL", reason=f"rank>{self.sell_rank} ({rank})")
        if theme_persistence < self.sell_theme:
            return TradeSignal(signal="SELL",
                               reason=f"theme<{self.sell_theme} ({theme_persistence:.1f})")
        if self.sell_break_ma60 and below_ma60:
            return TradeSignal(signal="SELL", reason="close<MA60")
        if rank <= self.hold_max_rank:
            return TradeSignal(signal="HOLD", reason=f"rank<= {self.hold_max_rank} ({rank})")
        return TradeSignal(signal="HOLD", reason="no signal")

    def manage(self, position: Optional[Position], today_row: pd.Series,
               below_ma60: bool = False) -> TradeSignal:
        """Given the current open position and today's ranking row, decide."""
        today_rank = int(today_row.get("rank", 999))
        today_theme = float(today_row.get("theme_persistence", 0.0))
        today_score = float(today_row.get("prediction_score", 0.0))
        today_etf = str(today_row.get("etf", ""))

        if position is None or not position.etf:
            return self.evaluate(today_rank, today_score, today_theme,
                                 float(today_row.get("leader_score", 0.0)),
                                 below_ma60, etf=today_etf)

        hard_reset = today_rank > self.sell_rank or (
            self.sell_break_ma60 and below_ma60)

        if position.holding_days < self.min_holding:
            if hard_reset:
                return TradeSignal(signal="SELL", etf=position.etf,
                                   reason=f"hard exit (rank={today_rank}, ma60={below_ma60})")
            return TradeSignal(signal="HOLD", etf=position.etf,
                               reason=f"min_holding ({position.holding_days}d)")

        if hard_reset and today_etf == position.etf:
            return TradeSignal(signal="SELL", etf=position.etf,
                               reason=f"hard exit (rank={today_rank}, ma60={below_ma60})")
        if today_theme < self.sell_theme and today_etf == position.etf:
            return TradeSignal(signal="SELL", etf=position.etf,
                               reason=f"theme<{self.sell_theme}")

        if today_rank == self.buy_rank and today_etf != position.etf:
            return TradeSignal(signal="BUY", etf=today_etf,
                               reason=f"rotate to rank-1 {today_etf}",
                               confidence=min(100.0, today_score))

        if today_etf == position.etf and today_rank <= self.hold_max_rank:
            return TradeSignal(signal="HOLD", etf=position.etf,
                               reason=f"still top ({today_rank})")
        return TradeSignal(signal="HOLD", etf=position.etf, reason="no exit")
