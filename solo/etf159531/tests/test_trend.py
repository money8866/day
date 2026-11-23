import numpy as np
import pytest

import strategy
from conftest import make_features


def _trend_df(n=8):
    close = np.full(n, 1.0)
    df = make_features(n, close=close, ma20=close * 0.9)
    df["ma60"] = close * 0.95
    df["ma120"] = close * 1.05
    return df


def test_two_day_confirmation(cfg):
    df = _trend_df()
    ts = strategy.trend_series(df, cfg)
    # 第一天即使收盘在均线上方也不算确认
    assert ts["trend_level"].iloc[0] == 0
    # 连续两天在上方后确认 MA20 / MA60，MA120 未确认
    assert ts["trend_level"].iloc[1] == 2
    assert ts["dir_MA20"].iloc[1] == "上"
    assert ts["dir_MA120"].iloc[1] == "下"


def test_trend_levels_and_weights(cfg):
    df = _trend_df()
    ts = strategy.trend_series(df, cfg)
    assert ts["trend_base"].iloc[1] == pytest.approx(0.40)
    res = strategy.trend_result(df, len(df) - 1, cfg)
    assert res.level == 2
    assert res.base_weight == pytest.approx(0.40)


def test_all_three_mas_above_gives_65(cfg):
    n = 8
    close = np.full(n, 1.0)
    df = make_features(n, close=close, ma20=close * 0.9)
    df["ma60"] = close * 0.95
    df["ma120"] = close * 0.98
    res = strategy.trend_result(df, None, cfg)
    assert res.level == 3
    assert res.base_weight == pytest.approx(0.65)


def test_equal_price_is_not_above(cfg):
    n = 5
    close = np.full(n, 1.0)
    df = make_features(n, close=close)  # 所有 MA 等于收盘价
    ts = strategy.trend_series(df, cfg)
    assert ts["trend_level"].iloc[-1] == 0


def test_trend_change_reason(cfg):
    n = 6
    close = np.full(n, 1.0)
    # 前 2 天在 MA20 下方，之后切换到上方
    ma20 = np.array([0.9, 0.9, 1.1, 0.9, 0.9, 0.9])
    df = make_features(n, close=close, ma20=ma20)
    df["ma60"] = close * 2.0
    df["ma120"] = close * 2.0
    res = strategy.trend_result(df, len(df) - 1, cfg)
    assert res.level == 1
    assert "增至 1" in res.change_reason or "维持 1" in res.change_reason
