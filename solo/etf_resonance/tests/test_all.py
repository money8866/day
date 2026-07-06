"""Unit tests for ETF Resonance System."""
import os
import sys
import unittest
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etf_resonance.utils.indicators import (
    ema, sma, adx, atr, slope, hurst_exponent,
    max_drawdown, sharpe_ratio, rolling_corr,
    new_high_count, consecutive_up_days, future_return,
)
from etf_resonance.core.trend import TrendScorer
from etf_resonance.core.leader import LeaderScorer, LeaderResult
from etf_resonance.core.resonance import ResonanceScorer
from etf_resonance.core.risk import RiskScorer
from etf_resonance.core.signal import SignalDetector
from etf_resonance.data.loader import DataLoader


class TestIndicators(unittest.TestCase):
    """Test all TA indicators."""

    def setUp(self):
        np.random.seed(42)
        self.close = np.cumprod(1 + np.random.randn(200) * 0.02) + 100
        self.high = self.close * (1 + np.abs(np.random.randn(200)) * 0.01)
        self.low = self.close * (1 - np.abs(np.random.randn(200)) * 0.01)
        self.vol = np.abs(np.random.randn(200)) * 1e6 + 1e6
        self.returns = np.diff(self.close) / self.close[:-1]

    def test_ema_shape(self):
        e = ema(self.close, 20)
        self.assertEqual(len(e), len(self.close))
        self.assertFalse(np.any(np.isnan(e)))

    def test_ema_trend(self):
        rising = np.linspace(100, 200, 100)
        e = ema(rising, 20)
        self.assertTrue(e[-1] > e[0])

    def test_adx(self):
        a = adx(self.high, self.low, self.close, 14)
        self.assertEqual(len(a), len(self.close))
        self.assertTrue(0 <= np.nanmean(a[-20:]) <= 100)

    def test_atr(self):
        a = atr(self.high, self.low, self.close, 14)
        self.assertEqual(len(a), len(self.close))
        self.assertTrue(np.all(a >= 0))

    def test_slope(self):
        s = slope(self.close, 20)
        self.assertEqual(len(s), len(self.close))

    def test_hurst(self):
        h = hurst_exponent(self.close)
        self.assertFalse(np.isnan(h))

    def test_max_drawdown(self):
        dd = max_drawdown(self.close)
        self.assertTrue(0 <= dd <= 100)

    def test_sharpe(self):
        sr = sharpe_ratio(self.returns, 252)
        self.assertTrue(np.isfinite(sr))

    def test_new_high_count(self):
        nh = new_high_count(self.close, 60)
        self.assertEqual(len(nh), len(self.close))

    def test_consecutive_up(self):
        cu = consecutive_up_days(self.close)
        self.assertEqual(len(cu), len(self.close))

    def test_future_return(self):
        fr = future_return(self.close, 5)
        self.assertEqual(len(fr), len(self.close))
        self.assertTrue(np.isnan(fr[-5:]).all())


class TestDataLoader(unittest.TestCase):
    """Test data loading module."""

    def setUp(self):
        self.loader = DataLoader()
        n = 120
        dates = pd.date_range("2025-01-01", periods=n, freq="B")
        self.etf_df = pd.DataFrame({
            "trade_date": [d.strftime("%Y%m%d") for d in dates],
            "open": np.random.randn(n) * 0.5 + 100,
            "high": np.random.randn(n) * 0.5 + 101,
            "low": np.random.randn(n) * 0.5 + 99,
            "close": np.cumprod(1 + np.random.randn(n) * 0.01) + 100,
            "vol": np.abs(np.random.randn(n)) * 1e6 + 1e6,
        })

    def test_load_etf_data(self):
        result = self.loader.load_etf_data({"ETF1": self.etf_df})
        self.assertIn("ETF1", result)

    def test_missing_columns(self):
        bad_df = self.etf_df.drop(columns=["high"])
        result = self.loader.load_etf_data({"ETF1": bad_df})
        self.assertNotIn("ETF1", result)

    def test_short_data(self):
        short_df = self.etf_df.iloc[:30]
        result = self.loader.load_etf_data({"ETF1": short_df})
        self.assertNotIn("ETF1", result)


class TestTrendScorer(unittest.TestCase):
    """Test ETF Trend Scorer."""

    def setUp(self):
        n = 250
        dates = pd.date_range("2025-01-01", periods=n, freq="B")
        np.random.seed(123)
        # Strong upward trend with small noise
        base = 100 * np.exp(np.cumsum(np.random.randn(n) * 0.006 + 0.002))
        self.etf_data = {
            "ETF1": pd.DataFrame({
                "trade_date": [d.strftime("%Y%m%d") for d in dates],
                "open": base * (1 + np.random.randn(n) * 0.002),
                "high": base * (1 + np.abs(np.random.randn(n)) * 0.005),
                "low": base * (1 - np.abs(np.random.randn(n)) * 0.005),
                "close": base,
                "vol": np.abs(np.random.randn(n)) * 1e6 + 1e6,
            })
        }
        self.scorer = TrendScorer()

    def test_score_shape(self):
        results = self.scorer.score(self.etf_data)
        self.assertIn("ETF1", results)
        r = results["ETF1"]
        self.assertTrue(0 <= r.trend_score <= 100)
        self.assertTrue(0 <= r.adx_val <= 100)

    def test_filter(self):
        results = self.scorer.score(self.etf_data)
        filtered = self.scorer.filter_etfs(results)
        self.assertIsInstance(filtered, dict)


class TestLeaderScorer(unittest.TestCase):
    """Test Leader Scorer with mock data."""

    def test_leader_result_dataclass(self):
        r = LeaderResult(
            ts_code="000001.SZ", name="Test", etf_code="ETF1",
            leader_score=85.0, relative_strength=10.0,
            relative_momentum=5.0, relative_volume=1.5,
            relative_high=2.0, breakout_strength=70.0,
            trend_stability=80.0, correlation=0.7, beta=1.2,
            sharpe=0.5, calmar=1.0, drawdown=15.0,
            liquidity_score=80.0, institution_score=70.0,
            rank_in_etf=1,
        )
        self.assertEqual(r.leader_score, 85.0)
        self.assertEqual(r.rank_in_etf, 1)


class TestSignalDetector(unittest.TestCase):
    """Test buy/sell signal detection."""

    def setUp(self):
        n = 200
        base = np.cumprod(1 + np.random.randn(n) * 0.01) + 100
        self.df = pd.DataFrame({
            "trade_date": [f"2025{d:03d}" for d in range(n)],
            "open": base, "high": base * 1.01,
            "low": base * 0.99, "close": base,
            "vol": np.abs(np.random.randn(n)) * 1e6 + 1e6,
        })
        self.detector = SignalDetector()

    def test_buy_signal_none(self):
        signal = self.detector.detect_buy(self.df)
        if signal is not None:
            self.assertTrue(0 <= signal.score <= 100)

    def test_sell_signal(self):
        signal = self.detector.detect_sell(self.df)
        if signal is not None:
            self.assertTrue(0 <= signal.score <= 100)


if __name__ == "__main__":
    unittest.main()
