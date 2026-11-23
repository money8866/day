import numpy as np
import pandas as pd
import pytest

import indicators


def test_sma_requires_full_window():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    ma3 = indicators.sma(s, 3)
    assert np.isnan(ma3.iloc[0]) and np.isnan(ma3.iloc[1])
    assert ma3.iloc[2] == pytest.approx(2.0)
    assert ma3.iloc[4] == pytest.approx(4.0)


def test_rsi_all_up_is_100():
    s = pd.Series(np.arange(1, 40, dtype=float))
    rsi = indicators.rsi_wilder(s, 6)
    assert np.isnan(rsi.iloc[5])
    assert rsi.iloc[6] == pytest.approx(100.0)
    assert rsi.iloc[-1] == pytest.approx(100.0)


def test_rsi_all_down_is_0():
    s = pd.Series(np.arange(40, 1, -1, dtype=float))
    rsi = indicators.rsi_wilder(s, 6)
    assert rsi.iloc[6] == pytest.approx(0.0)


def test_rsi_flat_is_neutral_50():
    s = pd.Series(np.full(30, 1.0))
    rsi = indicators.rsi_wilder(s, 6)
    assert rsi.iloc[6] == pytest.approx(50.0)
    assert rsi.iloc[-1] == pytest.approx(50.0)


def test_rsi_known_wilder_value():
    # 手工可复核：6 日窗口，先涨 6 天后持平一天，使用 Wilder 递推
    s = pd.Series([1, 2, 3, 4, 5, 6, 7, 7], dtype=float)
    rsi = indicators.rsi_wilder(s, 6)
    # 第一天收益均值 = 1，平均跌幅 = 0 -> 100；之后 gain=0, loss=0 -> avg_gain 递减但仍 >0, avg_loss=0 -> 仍 100
    assert rsi.iloc[6] == pytest.approx(100.0)
    assert rsi.iloc[7] == pytest.approx(100.0)


def test_annualized_vol_constant_returns():
    # 每日固定 +1% -> 日收益标准差为 0
    close = pd.Series([100 * (1.01 ** i) for i in range(40)])
    v20 = indicators.annualized_vol(close, 20)
    assert v20.iloc[25] == pytest.approx(0.0, abs=1e-12)


def test_annualized_vol_matches_manual():
    rng = np.random.default_rng(0)
    close = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.01, 100)))
    v20 = indicators.annualized_vol(close, 20)
    manual = close.pct_change().iloc[-20:].std(ddof=1) * np.sqrt(252)
    assert v20.iloc[-1] == pytest.approx(manual)


def test_effective_vol_floor():
    assert indicators.effective_volatility(0.10, 0.05, 0.18) == pytest.approx(0.18)
    assert indicators.effective_volatility(0.30, 0.05, 0.18) == pytest.approx(0.30)
    assert indicators.effective_volatility(float("nan"), 0.22, 0.18) == pytest.approx(0.22)


def test_drawdown_series():
    nav = pd.Series([1.0, 1.1, 1.05, 0.99, 1.2])
    dd = indicators.drawdown_series(nav)
    assert dd.iloc[2] == pytest.approx(1.05 / 1.1 - 1)
    assert dd.iloc[-1] == pytest.approx(0.0)
    assert indicators.max_drawdown(nav) == pytest.approx(0.99 / 1.1 - 1)


def test_compute_features_no_partial_window(cfg):
    from conftest import make_ohlcv

    df = make_ohlcv(n=300)
    feat = indicators.compute_features(df, cfg)
    assert np.isnan(feat["ma120"].iloc[0])
    assert np.isnan(feat["ma120"].iloc[118])
    assert not np.isnan(feat["ma120"].iloc[119])
    # 复权收盘一致（adj_factor=1）
    assert feat["close_adj"].iloc[-1] == pytest.approx(feat["close"].iloc[-1])
