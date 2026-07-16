#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ETF Alpha Engine - 单元测试
==============================
每个模块独立可测试。
运行: python -m etf_alpha_engine.tests.test_all
"""
import os
import sys
import unittest

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from etf_alpha_engine.indicators import (
    ema, sma, rma, roc, returns, atr, volatility, max_drawdown,
    sharpe_ratio, sortino_ratio, normalize, percentile_rank, winsorize,
    consecutive_up_days, above_ema_days, new_high_count, breakout_pct,
)
from etf_alpha_engine.market_regime import MarketRegimeEngine
from etf_alpha_engine.theme_alpha import ThemeAlphaEngine
from etf_alpha_engine.theme_lifecycle import ThemeLifecycleEngine
from etf_alpha_engine.etf_ranking import ETFRankingEngine
from etf_alpha_engine.leader_confirm import LeaderConfirmEngine
from etf_alpha_engine.risk_engine import RiskEngine
from etf_alpha_engine.composite import CompositeEngine
from etf_alpha_engine.rules import RulesEngine


def _gen_config():
    return {
        "market_regime": {"trend_weight": 0.25, "breadth_weight": 0.20,
                          "sentiment_weight": 0.20, "liquidity_weight": 0.15,
                          "risk_appetite_weight": 0.20},
        "theme_alpha": {"theme_alpha_weight": 0.40, "trend_persistence_weight": 0.20,
                        "industry_growth_weight": 0.15, "leader_strength_weight": 0.10,
                        "institution_weight": 0.10, "sentiment_weight": 0.05,
                        "top_themes": 5, "min_theme_stocks": 3},
        "theme_lifecycle": {"ema_fast": 5, "ema_mid": 20, "ema_slow": 60,
                             "stage_bonus": {"Birth": 20, "Acceleration": 25,
                                             "Expansion": 15, "Peak": -15,
                                             "Distribution": -25, "Decline": -40}},
        "etf_ranking": {"relative_strength_weight": 0.20, "trend_quality_weight": 0.20,
                        "momentum_quality_weight": 0.15, "liquidity_weight": 0.10,
                        "tracking_stability_weight": 0.10, "volatility_weight": 0.10,
                        "drawdown_weight": 0.05, "acceleration_weight": 0.10},
        "leader_confirm": {"leader_trend_weight": 0.20, "leader_breakout_weight": 0.15,
                           "leader_breadth_weight": 0.15, "institution_buying_weight": 0.15,
                           "northbound_buying_weight": 0.10, "relative_strength_weight": 0.15,
                           "industry_dominance_weight": 0.05, "leader_persistence_weight": 0.05},
        "risk_engine": {"expected_drawdown_weight": 0.25, "volatility_weight": 0.20,
                        "failure_probability_weight": 0.15, "rotation_risk_weight": 0.15,
                        "concentration_risk_weight": 0.10, "market_correlation_weight": 0.15},
        "composite": {"theme_alpha_weight": 0.25, "lifecycle_weight": 0.20,
                      "etf_trend_weight": 0.20, "leader_weight": 0.20,
                      "market_weight": 0.15, "risk_penalty": -0.10},
        "rules": {"buy": {"market_score_min": 70, "theme_rank_max": 3,
                           "etf_alpha_min": 85, "leader_score_min": 80,
                           "expected_return_min": 0.10, "trend_duration_min": 20,
                           "allowed_lifecycle": ["Birth", "Acceleration", "Expansion"]},
                  "sell": {"triggers": {"lifecycle_distribution": True,
                                         "leader_break_ma20": True,
                                         "etf_below_ma20_3d": True,
                                         "theme_rank_below": 5,
                                         "expected_return_below": 0.05,
                                         "risk_score_above": 70},
                           "min_triggers_to_sell": 2}},
        "etf_universe": {"512480.SH": "半导体", "159995.SZ": "芯片"},
    }


def _gen_price_df(n=200, seed=42, trend=0.001):
    rng = np.random.RandomState(seed)
    dates = [f"2025{m:02d}{d:02d}" for m in range(1, 13) for d in range(1, 29)][:n]
    close = 10.0
    closes = []
    for i in range(n):
        close *= (1 + trend + rng.normal(0, 0.02))
        closes.append(close)
    closes = np.array(closes)
    df = pd.DataFrame({
        "ts_code": "TEST.SH",
        "trade_date": dates[:n],
        "open": closes * 0.99,
        "high": closes * 1.02,
        "low": closes * 0.98,
        "close": closes,
        "vol": rng.uniform(1e6, 5e6, n),
        "amount": closes * rng.uniform(1e6, 5e6, n),
        "pct_chg": np.concatenate([[0], np.diff(closes) / closes[:-1] * 100]),
    })
    return df


class TestIndicators(unittest.TestCase):
    """测试技术指标"""

    def setUp(self):
        self.close = np.cumprod(1 + np.random.RandomState(0).normal(0.001, 0.02, 250)) * 10

    def test_ema(self):
        e = ema(self.close, 20)
        self.assertEqual(len(e), len(self.close))
        self.assertTrue(np.isfinite(e[-1]))

    def test_sma(self):
        s = sma(self.close, 10)
        self.assertEqual(len(s), len(self.close))

    def test_returns(self):
        r = returns(self.close)
        self.assertEqual(len(r), len(self.close))
        self.assertAlmostEqual(r[0], 0.0)

    def test_max_drawdown(self):
        dd = max_drawdown(self.close)
        self.assertGreaterEqual(dd, 0.0)
        self.assertLessEqual(dd, 1.0)

    def test_volatility(self):
        v = volatility(self.close, 20)
        self.assertGreater(v, 0.0)

    def test_sharpe(self):
        s = sharpe_ratio(self.close, 60)
        self.assertTrue(np.isfinite(s))

    def test_normalize(self):
        arr = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        n = normalize(arr)
        self.assertAlmostEqual(n[0], 0.0)
        self.assertAlmostEqual(n[-1], 1.0)

    def test_percentile_rank(self):
        arr = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        p = percentile_rank(arr)
        self.assertGreaterEqual(p[-1], 0.8)

    def test_consecutive_up_days(self):
        pct = np.array([-1, 1, 2, 3, -2, 1, 1])
        self.assertEqual(consecutive_up_days(pct), 2)

    def test_new_high_count(self):
        c = np.array([1, 2, 3, 2, 4, 5, 4, 6])
        self.assertGreater(new_high_count(c, 8), 0)


class TestMarketRegime(unittest.TestCase):
    def test_market_regime(self):
        cfg = _gen_config()
        eng = MarketRegimeEngine(cfg)
        idx = _gen_price_df(200)
        r = eng.score(index_df=idx, market_daily=None, limit_df=None)
        self.assertIsInstance(r.market_score, float)
        self.assertIn(r.market_state, ["Bull", "Recovery", "Neutral", "Weak", "Bear"])


class TestThemeAlpha(unittest.TestCase):
    def test_theme_alpha(self):
        cfg = _gen_config()
        eng = ThemeAlphaEngine(cfg)
        daily = _gen_price_df(200)
        daily["ts_code"] = "TEST.SH"
        universe = {"半导体": ["TEST.SH"]}
        r = eng.score(daily, universe)
        self.assertIsInstance(r, dict)


class TestThemeLifecycle(unittest.TestCase):
    def test_lifecycle(self):
        cfg = _gen_config()
        eng = ThemeLifecycleEngine(cfg)
        daily = _gen_price_df(200)
        daily["ts_code"] = "TEST.SH"
        universe = {"半导体": ["TEST.SH"]}
        r = eng.score(daily, universe)
        self.assertIsInstance(r, dict)
        if r:
            res = list(r.values())[0]
            self.assertIsInstance(res.stage, str)


class TestETFRanking(unittest.TestCase):
    def test_etf_ranking(self):
        cfg = _gen_config()
        eng = ETFRankingEngine(cfg)
        df = _gen_price_df(200)
        etf_data = {"512480.SH": df}
        bm = df["close"].values.astype(float)
        r = eng.score(etf_data, bm)
        self.assertIn("512480.SH", r)
        self.assertGreaterEqual(r["512480.SH"].etf_alpha_score, 0)
        self.assertLessEqual(r["512480.SH"].etf_alpha_score, 100)


class TestLeaderConfirm(unittest.TestCase):
    def test_leader(self):
        cfg = _gen_config()
        eng = LeaderConfirmEngine(cfg)
        df = _gen_price_df(200)
        etf_data = {"512480.SH": df}
        stock_data = {"STOCK1.SH": _gen_price_df(150, seed=1),
                      "STOCK2.SH": _gen_price_df(150, seed=2)}
        constituents = {"512480.SH": ["STOCK1.SH", "STOCK2.SH"]}
        r = eng.score(etf_data, constituents, stock_data)
        self.assertIn("512480.SH", r)
        self.assertGreaterEqual(r["512480.SH"].leader_score, 0)


class TestRiskEngine(unittest.TestCase):
    def test_risk(self):
        cfg = _gen_config()
        eng = RiskEngine(cfg)
        df = _gen_price_df(200)
        etf_data = {"512480.SH": df}
        bm = df["close"].values.astype(float)
        r = eng.score(etf_data, bm)
        self.assertIn("512480.SH", r)
        self.assertGreaterEqual(r["512480.SH"].risk_score, 0)
        self.assertLessEqual(r["512480.SH"].risk_score, 100)


class TestRules(unittest.TestCase):
    def test_buy_rules(self):
        cfg = _gen_config()
        eng = RulesEngine(cfg)
        from etf_alpha_engine.composite import FinalETFResult
        r = FinalETFResult(
            market_score=80, theme_rank=1, lifecycle="Acceleration",
            etf_alpha=90, leader_score=85, expected_return=0.15,
            trend_duration=30, risk_score=30
        )
        sig = eng.evaluate(r)
        self.assertTrue(sig.buy)


if __name__ == "__main__":
    unittest.main(verbosity=2)
