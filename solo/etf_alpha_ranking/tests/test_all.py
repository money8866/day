#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tests for the ETF Alpha Ranking System."""
import os
import sys
import unittest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BASE_DIR))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import numpy as np
import pandas as pd

# config.yaml lives one level up (the etf_alpha_ranking package root)
CONFIG_PATH = os.path.join(os.path.dirname(BASE_DIR), "config.yaml")

from etf_alpha_ranking.indicators import (
    sma, ema, rsi, adx, macd, kdj, atr, natr, volatility, max_drawdown,
    sharpe_ratio, sortino_ratio, ulcer_index, breakout_pct, new_high_count,
    price_position, volume_ratio, percentile_rank, zscore, hurst_exponent,
    beta, relative_strength, slope, returns,
)
from etf_alpha_ranking.tdx_reader import TDXReader, parse_tdx_day_file, ts_code_to_tdx_file
from etf_alpha_ranking.database import Database
from etf_alpha_ranking.features import FeatureEngine
from etf_alpha_ranking.labels import LabelBuilder
from etf_alpha_ranking.theme_persistence import ThemePersistenceEngine
from etf_alpha_ranking.leader_persistence import LeaderPersistenceEngine
from etf_alpha_ranking.breadth import BreadthEngine
from etf_alpha_ranking.market_regime import MarketRegimeEngine
from etf_alpha_ranking.portfolio import PortfolioEngine


def _synth_price(n=120, seed=1):
    rng = np.random.RandomState(seed)
    base = 1.0 + np.cumsum(rng.randn(n) * 0.01 + 0.0005)
    close = base
    high = close * (1 + rng.rand(n) * 0.01)
    low = close * (1 - rng.rand(n) * 0.01)
    open_ = close * (1 + rng.randn(n) * 0.005)
    vol = rng.rand(n) * 1e6 + 1e5
    amount = vol * close
    pct = np.concatenate([[0], np.diff(close) / close[:-1] * 100.0])
    df = pd.DataFrame({
        "trade_date": [f"2025{int(i)+1:04d}" for i in range(n)],
        "open": open_, "high": high, "low": low, "close": close,
        "vol": vol, "amount": amount, "pct_chg": pct,
    })
    return df


class TestIndicators(unittest.TestCase):
    def test_sma_ema(self):
        x = np.arange(100, dtype=float)
        self.assertFalse(np.isnan(sma(x, 10)[-1]))
        self.assertFalse(np.isnan(ema(x, 10)[-1]))

    def test_rsi_range(self):
        x = np.cumsum(np.random.randn(100)) + 10
        v = rsi(x, 14)
        self.assertGreaterEqual(v, 0)
        self.assertLessEqual(v, 100)

    def test_macd_shapes(self):
        x = np.cumsum(np.random.randn(100)) + 10
        m, s, h = macd(x)
        self.assertEqual(len(m), len(x))
        self.assertEqual(len(h), len(x))

    def test_kdj(self):
        x = np.cumsum(np.random.randn(100)) + 10
        k, d, j = kdj(x, x * 1.01, x * 0.99, 9)
        self.assertTrue(np.isfinite(k))

    def test_percentile_rank(self):
        r = percentile_rank(np.array([1.0, 2.0, 3.0, 4.0, 5.0]))
        self.assertAlmostEqual(r[-1], 100.0)
        self.assertAlmostEqual(r[0], 0.0)


class TestTDXReader(unittest.TestCase):
    def test_ts_code_mapping(self):
        p = ts_code_to_tdx_file("159516.SZ", "C:/new_tdx")
        self.assertIn("sz159516.day", p)
        p = ts_code_to_tdx_file("512480.SH", "C:/new_tdx")
        self.assertIn("sh512480.day", p)

    def test_load_one_etf(self):
        reader = TDXReader("C:/new_tdx", "d:/mystock/cache_daily")
        df = reader.load_daily_price("159516.SZ", "20250101", "20251231")
        if df.empty:
            self.skipTest("no TDX data available")
        self.assertIn("close", df.columns)
        self.assertGreater(len(df), 10)


class TestDatabase(unittest.TestCase):
    def test_roundtrip(self):
        db = Database(":memory:")
        db.upsert_etf_basic([{"ts_code": "159516.SZ", "name": "test",
                              "exchange": "SZ", "theme": "半导体设备",
                              "industry": "半导体", "updated": "2026-07-18"}])
        df = db.get_etf_basic()
        self.assertEqual(len(df), 1)
        # daily price
        dfp = pd.DataFrame({
            "trade_date": ["20260717"], "ts_code": ["159516.SZ"],
            "open": [1.0], "high": [1.1], "low": [0.9], "close": [1.05],
            "vol": [1000.0], "amount": [1050.0],
        })
        db.upsert_daily_price(dfp)
        got = db.get_daily_price("159516.SZ")
        self.assertEqual(len(got), 1)


class TestFeatureEngine(unittest.TestCase):
    def test_build_features(self):
        import yaml
        cfg_path = CONFIG_PATH
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        eng = FeatureEngine(cfg)
        df = _synth_price(130)
        bench = df["close"].values.astype(float)
        feats = eng.build("159516.SZ", df, bench)
        self.assertGreater(len(feats), 50, f"only {len(feats)} features built")
        # check key modules present
        for key in ["ret_20d", "alpha60", "vol_20d", "sharpe_60", "rsi_14"]:
            self.assertIn(key, feats)


class TestEngines(unittest.TestCase):
    def test_market_regime(self):
        import yaml
        cfg_path = CONFIG_PATH
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        eng = MarketRegimeEngine(cfg)
        bench = _synth_price(130)
        etf_data = {"159516.SZ": _synth_price(130, seed=2)}
        r = eng.score(bench, etf_data)
        self.assertGreaterEqual(r.market_score, 0)
        self.assertLessEqual(r.market_score, 100)

    def test_theme_leader_breadth(self):
        import yaml
        cfg_path = CONFIG_PATH
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        stocks = {f"60000{i}.SH": _synth_price(130, seed=i) for i in range(5)}
        be = BreadthEngine(cfg)
        be.set_benchmark(stocks["600000.SH"]["close"].values)
        br = be.score("半导体", stocks)
        self.assertGreaterEqual(br.breadth_score, 0)
        le = LeaderPersistenceEngine(cfg)
        le.set_benchmark(stocks["600000.SH"]["close"].values)
        lr = le.score("半导体", stocks)
        self.assertGreaterEqual(lr.leader_score, 0)
        te = ThemePersistenceEngine(cfg)
        tr = te.score("半导体", stocks, br.breadth_score, lr.leader_score)
        self.assertGreaterEqual(tr.theme_persistence, 0)
        self.assertLessEqual(tr.theme_persistence, 100)


class TestPortfolio(unittest.TestCase):
    def test_buy_signal(self):
        import yaml
        cfg_path = CONFIG_PATH
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        pf = PortfolioEngine(cfg)
        sig = pf.evaluate(rank=1, prediction_score=90, theme_persistence=80,
                          leader_score=75, below_ma60=False)
        self.assertEqual(sig.signal, "BUY")
        sig = pf.evaluate(rank=1, prediction_score=50, theme_persistence=50,
                          leader_score=30, below_ma60=False)
        self.assertEqual(sig.signal, "BUY")  # top-1 strategy: rank=1 always BUY
        sig = pf.evaluate(rank=2, prediction_score=90, theme_persistence=80,
                          leader_score=75, below_ma60=False)
        self.assertEqual(sig.signal, "HOLD")  # not rank-1, hold
        sig = pf.evaluate(rank=25, prediction_score=90, theme_persistence=80,
                          leader_score=75, below_ma60=False)
        self.assertEqual(sig.signal, "SELL")


class TestLabels(unittest.TestCase):
    def test_rank_label(self):
        import yaml
        cfg_path = CONFIG_PATH
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        lb = LabelBuilder(cfg)
        # 10 ETFs on one date, forward returns 1..10
        df = pd.DataFrame({
            "date": ["20250101"] * 10,
            "etf": [f"E{i}" for i in range(10)],
            "fwd_40d": np.linspace(-0.1, 0.2, 10),
        })
        df = lb.add_rank_label(df, 40)
        self.assertIn("rank_label", df.columns)
        # top 10% (1 ETF) -> label 3
        self.assertEqual(int(df["rank_label"].max()), 3)
        self.assertEqual(int(df["rank_label"].min()), 0)


class TestRanker(unittest.TestCase):
    def test_train_predict(self):
        try:
            import lightgbm as lgb
        except Exception:
            self.skipTest("lightgbm not installed")
        import yaml
        cfg_path = CONFIG_PATH
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        from etf_alpha_ranking.ranker import LGBRankerModel
        # build synthetic panel: 50 dates x 10 ETFs
        rng = np.random.RandomState(0)
        rows = []
        for d in range(50):
            date = f"2024{d+101:04d}"
            for e in range(10):
                rows.append({
                    "date": date, "etf": f"E{e}",
                    "f1": rng.randn(), "f2": rng.randn(), "f3": rng.randn(),
                    "rank_label": int(rng.randint(0, 4)),
                })
        panel = pd.DataFrame(rows)
        m = LGBRankerModel(cfg)
        m.train(panel, target_col="rank_label")
        self.assertIsNotNone(m.model)
        pred = m.predict(panel)
        self.assertIn("prediction_score", pred.columns)
        self.assertIn("rank", pred.columns)


if __name__ == "__main__":
    unittest.main(verbosity=2)

