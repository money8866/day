#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Output Reporter 输出报告器
输出最终 dataframe:
  ETF | Theme | Market Score | Theme Score | Lifecycle | Trend Duration |
  Rotation Probability | Leader | Leader Score | ETF Alpha | Risk Score |
  Expected Return | Expected Holding Days | Suggested Position |
  Buy | Hold | Sell | Confidence | Reasons

Sort descending by ETF Alpha. Return Top 10 ETFs.
"""
from __future__ import annotations

import os
import json
from typing import List

import pandas as pd

from etf_alpha_engine.composite import FinalETFResult
from etf_alpha_engine.rules import SignalResult


class OutputReporter:
    """输出报告器"""

    def __init__(self, config: dict):
        self.general = config.get("general", {})
        self.output_dir = self.general.get("output_dir", "./output")
        if not os.path.isabs(self.output_dir):
            base = os.path.dirname(os.path.abspath(__file__))
            self.output_dir = os.path.join(base, self.output_dir)
        os.makedirs(self.output_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # 转DataFrame
    # ------------------------------------------------------------------
    def to_dataframe(self, results: List[tuple]) -> pd.DataFrame:
        """results: list of (FinalETFResult, SignalResult)
        输出规范列：
        ETF, Theme, Market Score, Theme Score, Lifecycle, Trend Duration,
        Rotation Probability, Leader, Leader Score, ETF Alpha, Risk Score,
        Expected Return, Expected Holding Days, Suggested Position,
        Buy, Hold, Sell, Confidence, Reasons
        """
        rows = []
        for r, sig in results:
            rows.append({
                "ETF": r.etf_code,
                "Theme": r.theme,
                "Market Score": r.market_score,
                "Theme Score": r.theme_score,
                "Lifecycle": r.lifecycle,
                "Trend Duration": r.trend_duration,
                "Rotation Probability": r.rotation_probability,
                "Leader": r.leader,
                "Leader Score": r.leader_score,
                "ETF Alpha": r.etf_alpha,
                "Risk Score": r.risk_score,
                "Expected Return": f"{r.expected_return*100:.1f}%",
                "Expected Holding Days": r.expected_holding_days,
                "Suggested Position": f"{r.suggested_position*100:.0f}%",
                "Buy": "Yes" if sig.buy else "",
                "Hold": "Yes" if sig.hold else "",
                "Sell": "Yes" if sig.sell else "",
                "Confidence": r.confidence,
                "Reasons": "; ".join(r.reasons[:6]),
            })
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.sort_values("ETF Alpha", ascending=False).reset_index(drop=True)
            df.index = df.index + 1
            df.index.name = "rank"
        return df

    # ------------------------------------------------------------------
    # 导出JSON
    # ------------------------------------------------------------------
    def to_json(self, results: List[tuple], trade_date: str) -> str:
        out = []
        for r, sig in results:
            out.append({
                "etf_code": r.etf_code,
                "etf_name": r.etf_name,
                "theme": r.theme,
                "trade_date": trade_date,
                "market_score": r.market_score,
                "market_state": r.market_state,
                "theme_score": r.theme_score,
                "theme_rank": r.theme_rank,
                "lifecycle": r.lifecycle,
                "trend_duration": r.trend_duration,
                "rotation_probability": r.rotation_probability,
                "leader": r.leader,
                "leader_score": r.leader_score,
                "etf_alpha": r.etf_alpha,
                "risk_score": r.risk_score,
                "expected_return": r.expected_return,
                "expected_holding_days": r.expected_holding_days,
                "suggested_position": r.suggested_position,
                "stop_loss": r.stop_loss,
                "take_profit": r.take_profit,
                "buy": sig.buy,
                "hold": sig.hold,
                "sell": sig.sell,
                "confidence": r.confidence,
                "buy_reasons": sig.buy_reasons,
                "sell_triggers": sig.sell_triggers,
                "reasons": r.reasons,
            })
        path = os.path.join(self.output_dir, f"etf_alpha_{trade_date}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        return path

    # ------------------------------------------------------------------
    # 导出CSV
    # ------------------------------------------------------------------
    def to_csv(self, df: pd.DataFrame, trade_date: str) -> str:
        path = os.path.join(self.output_dir, f"etf_alpha_{trade_date}.csv")
        df.to_csv(path, encoding="utf-8-sig")
        return path

    # ------------------------------------------------------------------
    # 打印Top N
    # ------------------------------------------------------------------
    def print_top(self, df: pd.DataFrame, top_n: int = 10):
        if df.empty:
            print("无结果")
            return
        top = df.head(top_n)
        # 选择显示列
        cols = ["ETF", "Theme", "Market Score", "Theme Score", "Lifecycle",
                "Trend Duration", "Leader", "Leader Score", "ETF Alpha",
                "Risk Score", "Expected Return", "Suggested Position",
                "Buy", "Hold", "Sell", "Confidence"]
        cols = [c for c in cols if c in top.columns]
        print(top[cols].to_string())
