#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Module E - Leader Persistence Engine
====================================
For each theme, identify the top leaders and compute leader persistence.

Per theme:
  - Identify top N leaders (by RS vs benchmark + recent return)
  - leader_RS            : relative strength vs benchmark (0-100)
  - leader_return20/60   : recent returns (0-100 normalized)
  - leader_breakout      : breakout strength (0-100)
  - leader_health        : above-MA + trend quality (0-100)
  - leader_persistence   : sustained leadership (0-100)

Output: leader_score (0-100) per theme.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List

import numpy as np
import pandas as pd

from . import indicators as ind

LOG = logging.getLogger("etf_alpha_ranking.leader")


@dataclass
class LeaderResult:
    theme: str = ""
    core_leader: str = ""
    leader_score: float = 0.0
    leader_RS: float = 0.0
    leader_return20: float = 0.0
    leader_return60: float = 0.0
    leader_breakout: float = 0.0
    leader_health: float = 0.0
    leader_persistence: float = 0.0
    top_leaders: list = None

    def __post_init__(self):
        if self.top_leaders is None:
            self.top_leaders = []


class LeaderPersistenceEngine:
    def __init__(self, config: dict):
        cfg = config.get("leader_persistence", {})
        self.top_n = cfg.get("top_leader_count", 5)
        self.rs_period = cfg.get("rs_period", 60)
        self.return_periods = cfg.get("return_periods", [20, 60])
        self.breakout_period = cfg.get("breakout_period", 60)
        self.health_period = cfg.get("health_period", 20)
        self.persistence_period = cfg.get("persistence_period", 20)
        self.min_score = cfg.get("min_leader_score", 70)
        self._benchmark: np.ndarray = np.array([])

    def set_benchmark(self, bench_close: np.ndarray):
        self._benchmark = np.asarray(bench_close, dtype=float)

    def _stock_strength(self, df: pd.DataFrame) -> Dict[str, float]:
        """Compute a single strength composite for ranking constituents."""
        if df is None or df.empty or len(df) < self.rs_period + 1:
            return {}
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float) if "high" in df.columns else close
        low = df["low"].values.astype(float) if "low" in df.columns else close
        vol = df["vol"].values.astype(float) if "vol" in df.columns else np.ones_like(close)

        ret20 = float(close[-1] / close[-21] - 1.0) if len(close) > 20 else 0.0
        ret60 = float(close[-1] / close[-61] - 1.0) if len(close) > 60 else 0.0
        # RS vs benchmark
        rs_vs_bench = 0.0
        if len(self._benchmark) > self.rs_period:
            b = self._benchmark[-self.rs_period - 1:]
            e = close[-self.rs_period - 1:]
            if len(b) > 1 and len(e) > 1 and abs(b[0]) > 1e-9:
                rs_vs_bench = (e[-1] / e[0] - 1.0) - (b[-1] / b[0] - 1.0)
        # volume ratio (liquidity confirmation)
        vr = ind.volume_ratio(vol, 20)
        # composite strength
        strength = ret60 * 100.0 + ret20 * 50.0 + rs_vs_bench * 100.0 + np.log1p(max(vr, 0.1)) * 5.0
        return {"strength": float(strength), "ret20": ret20, "ret60": ret60,
                "rs": rs_vs_bench, "close": close, "high": high, "low": low, "vol": vol}

    def score(self, theme: str, stock_data: Dict[str, pd.DataFrame]) -> LeaderResult:
        r = LeaderResult(theme=theme)
        if not stock_data:
            return r
        metrics = {}
        for code, df in stock_data.items():
            m = self._stock_strength(df)
            if m:
                metrics[code] = m
        if not metrics:
            return r
        # Top leaders by strength
        ranked = sorted(metrics.items(), key=lambda kv: kv[1]["strength"], reverse=True)
        top = ranked[: self.top_n]
        r.top_leaders = [c for c, _ in top]
        r.core_leader = top[0][0] if top else ""

        if not top:
            return r

        # ---- Sub-scores (0-100) across top leaders ----
        rs_scores, r20_scores, r60_scores, brk_scores, hlth_scores, pers_scores = ([] for _ in range(6))
        for code, m in top:
            close, high, low, vol = m["close"], m["high"], m["low"], m["vol"]
            # RS percentile within theme
            all_rs = [metrics[c]["rs"] for c in metrics]
            rs_pct = float(np.mean(m["rs"] >= np.array(all_rs))) * 100.0
            rs_scores.append(rs_pct)
            # returns normalized: clip to [-20%,+40%] -> [0,100]
            r20_scores.append(float(np.clip((m["ret20"] + 0.2) / 0.6, 0, 1) * 100.0))
            r60_scores.append(float(np.clip((m["ret60"] + 0.2) / 0.8, 0, 1) * 100.0))
            # breakout strength
            brk = ind.breakout_pct(close, high, self.breakout_period)
            brk_scores.append(float(np.clip((brk + 5.0) / 25.0, 0, 1) * 100.0))
            # health: above-MA20 days ratio + positive drift
            n = len(close)
            if n >= self.health_period + 1:
                ma = ind.sma(close, 20)
                seg_c = close[-self.health_period:]
                seg_m = ma[-self.health_period:]
                above = np.nanmean(seg_c > seg_m) * 100.0 if len(seg_m) else 0.0
                drift = float(np.mean(np.diff(close[-self.health_period:])) > 0) * 30.0
                hlth_scores.append(float(np.clip(above * 0.7 + drift, 0, 100)))
            else:
                hlth_scores.append(50.0)
            # persistence: std of rolling strength low => stable leadership
            if n >= self.persistence_period + 20:
                rets = np.diff(close[-self.persistence_period - 1:]) / close[-self.persistence_period - 1:-1]
                win = (rets > 0).sum() / len(rets) * 100.0 if len(rets) else 50.0
                pers_scores.append(win)
            else:
                pers_scores.append(50.0)

        r.leader_RS = float(np.mean(rs_scores))
        r.leader_return20 = float(np.mean(r20_scores))
        r.leader_return60 = float(np.mean(r60_scores))
        r.leader_breakout = float(np.mean(brk_scores))
        r.leader_health = float(np.mean(hlth_scores))
        r.leader_persistence = float(np.mean(pers_scores))

        # weighted leader_score
        r.leader_score = float(np.clip(
            0.25 * r.leader_RS
            + 0.20 * r.leader_return60
            + 0.15 * r.leader_return20
            + 0.15 * r.leader_breakout
            + 0.15 * r.leader_health
            + 0.10 * r.leader_persistence, 0, 100))
        return r

    def score_all(self, theme_stocks: Dict[str, List[str]],
                  all_stock_data: Dict[str, pd.DataFrame]) -> Dict[str, LeaderResult]:
        out: Dict[str, LeaderResult] = {}
        for theme, stocks in theme_stocks.items():
            sd = {c: all_stock_data[c] for c in stocks if c in all_stock_data}
            out[theme] = self.score(theme, sd)
        return out

    @staticmethod
    def to_dict(r: LeaderResult) -> dict:
        return {
            "theme": r.theme,
            "core_leader": r.core_leader,
            "leader_score": round(r.leader_score, 2),
            "leader_RS": round(r.leader_RS, 2),
            "leader_return20": round(r.leader_return20, 2),
            "leader_return60": round(r.leader_return60, 2),
            "leader_breakout": round(r.leader_breakout, 2),
            "leader_health": round(r.leader_health, 2),
            "leader_persistence": round(r.leader_persistence, 2),
            "top_leaders": r.top_leaders,
        }
