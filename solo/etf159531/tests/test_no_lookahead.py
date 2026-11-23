import numpy as np
import pandas as pd
import pytest

import backtest
import indicators
import strategy
from conftest import make_ohlcv


def test_features_are_causal(cfg):
    df = make_ohlcv(n=320, seed=11)
    full = indicators.compute_features(df, cfg)
    cut = 250
    part = indicators.compute_features(df.iloc[:cut], cfg)
    cols = ["ma5", "ma20", "ma60", "ma120", "ma250", "rsi6", "rsi14",
            "vol20", "vol60", "effective_vol", "ret20", "dev20"]
    for c in cols:
        a = full[c].iloc[cut - 1]
        b = part[c].iloc[cut - 1]
        if pd.isna(a) and pd.isna(b):
            continue
        assert a == pytest.approx(b), c


def test_market_signals_are_causal(cfg):
    df = make_ohlcv(n=340, seed=12)
    full, _ = strategy.compute_market(indicators.compute_features(df, cfg), cfg)
    cut = 280
    part, _ = strategy.compute_market(indicators.compute_features(df.iloc[:cut], cfg), cfg)
    cols = ["trend_level", "trend_base", "os_state", "os_weight", "crisis_active",
            "crisis_cap", "vol_cap", "market_final"]
    for c in cols:
        a, b = full[c].iloc[cut - 1], part[c].iloc[cut - 1]
        if isinstance(a, (int, float, np.floating)) and isinstance(b, (int, float, np.floating)):
            if pd.isna(a) and pd.isna(b):
                continue
            assert a == pytest.approx(b), c
        else:
            assert a == b, c


def test_backtest_trades_do_not_depend_on_future_prices(cfg):
    cfg["backtest"] = dict(cfg["backtest"], initial_cash=500000)
    df = make_ohlcv(n=340, seed=21)
    cut = 300
    df2 = df.copy()
    for c in ("open", "high", "low", "close"):
        df2.loc[cut:, c] = df2.loc[cut:, c] * 1.6
    df2.loc[cut:, "close_adj"] = df2.loc[cut:, "close"] * df2.loc[cut:, "adj_factor"]

    v = backtest.BASE_VARIANTS[4]
    start = 150
    r1 = backtest.simulate(backtest.prepare(cfg, df, None, v), cfg, v, start_idx=start)
    r2 = backtest.simulate(backtest.prepare(cfg, df2, None, v), cfg, v, start_idx=start)

    t1 = r1["trades"]
    t2 = r2["trades"]
    if t1.empty:
        pytest.skip("样本内没有触发交易，无法验证")
    cutoff_date = str(df["trade_date"].iloc[cut])
    a = t1[t1["trade_date"] < cutoff_date].reset_index(drop=True)
    b = t2[t2["trade_date"] < cutoff_date].reset_index(drop=True)
    assert len(a) == len(b)
    if len(a):
        assert a["trade_date"].tolist() == b["trade_date"].tolist()
        assert a["shares"].tolist() == pytest.approx(b["shares"].tolist())


def test_signal_is_executed_next_day(cfg):
    """t 日收盘生成的指令最早在 t+1 开盘成交，不会在信号当日成交。"""
    df = make_ohlcv(n=300, seed=5)
    v = backtest.BASE_VARIANTS[4]
    prepared = backtest.prepare(cfg, df, None, v)
    # start_idx=0 时首日没有任何历史信号，因此不应在首日成交
    res = backtest.simulate(prepared, cfg, v, start_idx=0)
    curve = res["curve"]
    trades = res["trades"]
    if trades.empty:
        pytest.skip("无交易")
    dates = curve["trade_date"].tolist()
    first_idx = dates.index(trades["trade_date"].iloc[0])
    assert first_idx >= 1
    # 每一笔成交都不早于其信号日（信号在上一交易日收盘产生）
    assert set(trades["trade_date"]).issubset(set(dates[1:]))


def test_metrics_on_flat_equity(cfg):
    curve = pd.DataFrame({
        "trade_date": pd.bdate_range("2024-01-02", periods=30).strftime("%Y%m%d"),
        "equity": [100.0] * 30,
        "position": [0.0] * 30,
    })
    m = backtest.compute_metrics(curve, pd.DataFrame(), 100.0)
    assert m["total_return"] == pytest.approx(0.0)
    assert m["mdd"] == pytest.approx(0.0)
    assert m["avg_position"] == pytest.approx(0.0)
