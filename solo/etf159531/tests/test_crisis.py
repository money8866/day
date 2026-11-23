import numpy as np
import pandas as pd
import pytest

import strategy
from conftest import make_features


def _df(dev, vol, os_weight):
    n = len(dev)
    df = make_features(n, close=np.full(n, 1.0), dev20=np.array(dev, dtype=float),
                       vol20=0.2)
    df["vol20"] = np.array(vol, dtype=float)
    return df, pd.Series(os_weight, index=df.index, dtype=float)


def test_crisis_trigger_and_release(cfg):
    dev = [0.0, 0.0, -0.10, -0.10, -0.10, -0.03, -0.03]
    vol = [0.2, 0.2, 0.40, 0.40, 0.40, 0.38, 0.20]
    df, os_w = _df(dev, vol, [0] * 7)
    out, events = strategy.crisis_series(df, os_w, cfg)

    assert not out["crisis_active"].iloc[1]
    assert out["crisis_active"].iloc[2]          # -10% 且 vol40% -> 触发
    assert out["crisis_cap"].iloc[2] == pytest.approx(0.0)
    assert out["crisis_active"].iloc[4]          # 条件仍在，保持开启（latch）
    assert not out["crisis_active"].iloc[5]      # 偏离改善至 -3% -> 解除
    assert out["crisis_cap"].iloc[5] == pytest.approx(1.0)

    assert len(events) == 1
    assert events[0]["trigger_date"] == df["trade_date"].iloc[2]
    assert events[0]["release_date"] == df["trade_date"].iloc[5]
    assert events[0]["status"] == "RELEASED"


def test_crisis_releases_when_vol_drops(cfg):
    dev = [0.0, -0.10, -0.10, -0.10]
    vol = [0.2, 0.40, 0.34, 0.30]
    df, os_w = _df(dev, vol, [0] * 4)
    out, _ = strategy.crisis_series(df, os_w, cfg)
    assert out["crisis_active"].iloc[1]
    # vol 34% 已 <= 35%，因此第 2 日即解除
    assert not out["crisis_active"].iloc[2]


def test_crisis_probe_allows_ten_percent(cfg):
    dev = [0.0, -0.10, -0.10, -0.10]
    vol = [0.2, 0.40, 0.40, 0.40]
    df, os_w = _df(dev, vol, [0.0, 0.05, 0.10, 0.10])
    out, events = strategy.crisis_series(df, os_w, cfg)
    assert out["crisis_cap"].iloc[1] == pytest.approx(0.05)
    assert out["crisis_cap"].iloc[2] == pytest.approx(0.10)
    assert out["crisis_cap"].iloc[3] == pytest.approx(0.10)
    assert events[0]["probe_date"] == df["trade_date"].iloc[1]


def test_crisis_no_probe_without_recovery(cfg):
    dev = [0.0, -0.10, -0.10]
    vol = [0.2, 0.40, 0.40]
    df, os_w = _df(dev, vol, [0.0, 0.0, 0.0])
    out, _ = strategy.crisis_series(df, os_w, cfg)
    assert out["crisis_cap"].iloc[1] == pytest.approx(0.0)
    assert out["crisis_active"].iloc[1]


def test_crisis_requires_both_conditions(cfg):
    dev = [0.0, -0.10, 0.0]
    vol = [0.2, 0.30, 0.40]
    df, os_w = _df(dev, vol, [0] * 3)
    out, _ = strategy.crisis_series(df, os_w, cfg)
    assert not out["crisis_active"].any()
