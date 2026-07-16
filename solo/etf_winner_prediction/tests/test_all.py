#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ETF Winner Prediction - 测试模块
"""
import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def test_imports():
    """测试所有模块导入"""
    print("Testing imports...")
    from etf_winner_prediction import __version__
    print(f"  Version: {__version__}")

    from etf_winner_prediction.data_loader import DataLoader, load_config
    from etf_winner_prediction.market_regime import MarketRegimeFilter
    from etf_winner_prediction.theme_forecast import ThemeForecastEngine
    from etf_winner_prediction.lifecycle import LifecyclePredictor
    from etf_winner_prediction.leader_engine import LeaderEngine
    from etf_winner_prediction.etf_trend import ETFTrendEngine
    from etf_winner_prediction.expected_return import ExpectedReturnModel
    from etf_winner_prediction.expected_rank import ExpectedRankModel
    from etf_winner_prediction.risk_engine import RiskEngine
    from etf_winner_prediction.decision import DecisionEngine
    from etf_winner_prediction.reporter import Reporter
    from etf_winner_prediction.main import ETFWinnerPredictionEngine

    print("  All imports OK!")


def test_config():
    """测试配置加载"""
    print("Testing config...")
    from etf_winner_prediction.data_loader import load_config
    config = load_config(os.path.join(BASE_DIR, "config.yaml"))
    assert "etf_universe" in config
    assert "market_regime" in config
    print(f"  Config OK! ETF count: {len(config['etf_universe'])}")


def test_indicators():
    """测试指标模块"""
    print("Testing indicators...")
    import numpy as np
    from etf_winner_prediction.indicators import ema, slope, sharpe_ratio, max_drawdown, hurst_exponent

    close = np.array([100.0 + i * 0.5 + np.random.randn() * 2 for i in range(100)])
    e = ema(close, 20)
    assert len(e) == 100
    s = slope(close, 20)
    assert isinstance(s, float)
    h = hurst_exponent(close)
    assert 0 <= h <= 1
    print("  Indicators OK!")


def test_decision():
    """测试决策引擎硬过滤器"""
    print("Testing decision engine...")
    from etf_winner_prediction.data_loader import load_config
    from etf_winner_prediction.decision import DecisionEngine

    config = load_config(os.path.join(BASE_DIR, "config.yaml"))
    engine = DecisionEngine(config)

    # 全通过
    r = engine.evaluate(
        market_score=70, theme_forecast_rank=1, remaining_trend_days=30,
        leader_score=85, risk_score=30, expected_return=0.15, probability_top3=0.75
    )
    assert r.accepted, f"Expected accepted, got: {r.reject_reasons}"
    print("  All pass: OK")

    # 失败
    r2 = engine.evaluate(
        market_score=40, theme_forecast_rank=5, remaining_trend_days=10,
        leader_score=50, risk_score=60, expected_return=0.05, probability_top3=0.30
    )
    assert not r2.accepted
    assert len(r2.reject_reasons) == 7
    print(f"  All fail: {len(r2.reject_reasons)} rejections OK")


if __name__ == "__main__":
    test_imports()
    test_config()
    test_indicators()
    test_decision()
    print("\nAll tests passed!")