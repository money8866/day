#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Reporter
========
Generates the daily CSV report and a console summary.

CSV columns:
  Rank, ETF, Theme, PredictionScore, ThemePersistence, LeaderScore,
  ETFTrendScore, RelativeStrength, RiskScore, ExpectedHoldingDays,
  Confidence, Signal
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional

import numpy as np
import pandas as pd

LOG = logging.getLogger("etf_alpha_ranking.reporter")


class Reporter:
    def __init__(self, config: dict):
        self.config = config
        rcfg = config.get("report", {})
        self.csv_dir = rcfg.get("csv_dir", "./output/csv")
        self.top_n = rcfg.get("top_n", 20)
        os.makedirs(self.csv_dir, exist_ok=True)
        self.etf_theme = config.get("etf_universe", {})

    # ------------------------------------------------------------------
    # Build the report DataFrame from a predictions frame
    # ------------------------------------------------------------------
    def build_report_df(self, predictions: pd.DataFrame) -> pd.DataFrame:
        if predictions.empty:
            return pd.DataFrame()
        df = predictions.copy()
        df = df.sort_values("rank")
        # theme
        df["theme"] = df["etf"].map(self.etf_theme).fillna("")
        # ETF trend score proxy: use ret_20d*100 + market alignment
        if "ret_20d" in df.columns:
            df["ETFTrendScore"] = (df.get("ret_20d", 0) * 100 + 50).clip(0, 100)
        else:
            df["ETFTrendScore"] = df.get("etf_trend_score", 50.0)
        # relative strength
        if "alpha60" in df.columns:
            df["RelativeStrength"] = (df["alpha60"] * 100 + 50).clip(0, 100)
        else:
            df["RelativeStrength"] = 50.0
        # risk score (lower vol -> lower risk)
        if "vol_60d" in df.columns:
            df["RiskScore"] = (100 - df["vol_60d"] * 3000).clip(0, 100)
        else:
            df["RiskScore"] = 50.0
        # expected holding days
        df["ExpectedHoldingDays"] = df.get("theme_expected_duration", 40)
        # confidence
        df["Confidence"] = df.get("prediction_score", 0.0)
        # signal placeholder; portfolio engine fills it
        df["Signal"] = "HOLD"

        cols = ["rank", "etf", "theme", "prediction_score",
                "theme_persistence", "leader_score", "ETFTrendScore",
                "RelativeStrength", "RiskScore", "ExpectedHoldingDays",
                "Confidence", "Signal"]
        out = pd.DataFrame()
        for c in cols:
            if c in df.columns:
                out[c] = df[c]
        out = out.rename(columns={
            "rank": "Rank", "etf": "ETF", "prediction_score": "PredictionScore",
            "theme_persistence": "ThemePersistence", "leader_score": "LeaderScore",
        })
        return out.head(self.top_n).reset_index(drop=True)

    # ------------------------------------------------------------------
    # CSV
    # ------------------------------------------------------------------
    def to_csv(self, report_df: pd.DataFrame, trade_date: str) -> str:
        path = os.path.join(self.csv_dir, f"etf_ranking_{trade_date}.csv")
        report_df.to_csv(path, index=False, encoding="utf-8-sig")
        LOG.info("CSV report -> %s", path)
        return path

    # ------------------------------------------------------------------
    # Console summary
    # ------------------------------------------------------------------
    def print_summary(self, report_df: pd.DataFrame, trade_date: str = ""):
        if report_df.empty:
            print("[reporter] empty report")
            return
        print("\n" + "=" * 70)
        print(f"  ETF Alpha Ranking Report  {trade_date}")
        print("=" * 70)
        print(f"{'Rank':<5}{'ETF':<12}{'Theme':<10}{'Score':<7}{'Theme':<7}"
              f"{'Leader':<7}{'Trend':<7}{'RS':<7}{'Risk':<7}{'Signal':<8}")
        print("-" * 70)
        for _, r in report_df.iterrows():
            print(f"{int(r['Rank']):<5}{str(r['ETF']):<12}{str(r['theme']):<10}"
                  f"{r['PredictionScore']:<7.1f}{r['ThemePersistence']:<7.1f}"
                  f"{r['LeaderScore']:<7.1f}{r['ETFTrendScore']:<7.1f}"
                  f"{r['RelativeStrength']:<7.1f}{r['RiskScore']:<7.1f}"
                  f"{r['Signal']:<8}")
        print("=" * 70)

    # ------------------------------------------------------------------
    # Markdown report (for WeChat / daily push)
    # ------------------------------------------------------------------
    def to_markdown(self, report_df: pd.DataFrame, trade_date: str,
                    market_state: str = "", market_score: float = 0.0,
                    signal_etf: str = "", signal_type: str = "") -> str:
        if report_df.empty:
            return f"# ETF Alpha Ranking {trade_date}\n\n无数据\n"
        lines = [f"# ETF Alpha Ranking {trade_date}", ""]
        if market_state:
            lines.append(f"**市场状态**: {market_state} (score={market_score:.1f})")
            lines.append("")
        if signal_etf and signal_type:
            lines.append(f"**交易信号**: {signal_type} {signal_etf}")
            lines.append("")
        lines.append("| Rank | ETF | Theme | Score | Theme | Leader | Signal |")
        lines.append("|------|-----|-------|-------|-------|--------|--------|")
        for _, r in report_df.iterrows():
            lines.append(
                f"| {int(r['Rank'])} | {r['ETF']} | {r['theme']} | "
                f"{r['PredictionScore']:.1f} | {r['ThemePersistence']:.1f} | "
                f"{r['LeaderScore']:.1f} | {r['Signal']} |")
        return "\n".join(lines) + "\n"
