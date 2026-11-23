import numpy as np
import pandas as pd
import pytest

import strategy
from conftest import make_features


def _build_trigger_sequence(n=40):
    """构造：第 10 日触发超卖 -> 第 11 日初步确认 -> 第 12 日进一步确认 -> 第 13 日 RSI 退出。"""
    close = np.full(n, 1.00)
    ma5 = np.full(n, 1.02)
    ma10 = np.full(n, 1.02)
    ma20 = np.full(n, 1.00)
    rsi = np.full(n, 50.0)

    # 第 10 日：触发（RSI<=20 且收盘低于 MA20 8%）
    close[10] = 0.90
    ma5[10] = 0.88          # 收盘在 MA5 上方
    ma20[10] = 1.00
    rsi[10] = 15.0
    # 第 11 日：RSI 上穿 30 且连续两日站上 MA5 -> RECOVERY_1
    close[11] = 0.91
    ma5[11] = 0.885
    rsi[11] = 35.0
    # 第 12 日：连续两日站上 MA10 -> RECOVERY_2
    close[12] = 0.93
    ma10[12] = 0.90
    ma10[11] = 0.90
    close[11] = 0.93
    ma5[11] = 0.90
    # 第 13 日：RSI >= 65 退出
    close[13] = 0.94
    rsi[13] = 70.0
    ma20[13] = 0.95

    df = make_features(n, close=close, ma5=ma5, ma10=ma10, ma20=ma20, rsi6=rsi)
    return df


def test_oversold_full_lifecycle(cfg):
    df = _build_trigger_sequence()
    out, events, objs = strategy.oversold_series(df, cfg)

    assert out["os_state"].iloc[9] == "NORMAL"
    assert out["os_state"].iloc[10] == "OVERSOLD_WATCH"
    assert out["os_state"].iloc[11] == "RECOVERY_1"
    assert out["os_weight"].iloc[11] == pytest.approx(0.05)
    assert out["os_state"].iloc[12] == "RECOVERY_2"
    assert out["os_weight"].iloc[12] == pytest.approx(0.10)
    assert out["os_state"].iloc[13] == "EXPIRED"
    assert out["os_weight"].iloc[13] == pytest.approx(0.0)

    ev = events[0]
    assert ev["trigger_date"] == df["trade_date"].iloc[10]
    assert ev["activate_date"] == df["trade_date"].iloc[11]
    assert ev["tactical_weight"] == pytest.approx(0.0)   # 已过期清零
    assert ev["exit_date"] == df["trade_date"].iloc[13]
    assert "RSI6" in ev["exit_reason"]
    assert objs[0].lowest_close == pytest.approx(0.90)


def test_no_reactivation_during_cooldown(cfg):
    df = _build_trigger_sequence(n=60)
    # 冷却期内再次满足触发条件，不应产生新事件
    for i in range(14, 30):
        df.loc[i, "rsi6"] = 10.0
        df.loc[i, "close"] = 0.80
        df.loc[i, "close_adj"] = 0.80
        df.loc[i, "ma20"] = 1.00
    out, events, _ = strategy.oversold_series(df, cfg)
    assert len(events) == 1
    assert out["os_state"].iloc[13] == "EXPIRED"
    assert (out["os_state"].iloc[14:34] == "NORMAL").all()


def test_event_expires_without_confirmation(cfg):
    n = 40
    close = np.full(n, 1.0)
    ma20 = np.full(n, 1.0)
    rsi = np.full(n, 50.0)
    close[5] = 0.90
    rsi[5] = 15.0
    df = make_features(n, close=close, ma20=ma20, rsi6=rsi)
    # 触发后长期 RSI 低位、不站上 MA5
    for i in range(6, 30):
        df.loc[i, "rsi6"] = 18.0
        df.loc[i, "close"] = 0.88
        df.loc[i, "close_adj"] = 0.88
        df.loc[i, "ma5"] = 1.00
    out, events, _ = strategy.oversold_series(df, cfg)
    assert len(events) == 1
    assert events[0]["state"] == "EXPIRED"
    assert "未完成初步反弹确认" in events[0]["exit_reason"]


def test_exit_on_break_low(cfg):
    n = 40
    close = np.full(n, 1.0)
    ma5 = np.full(n, 1.02)
    ma10 = np.full(n, 1.02)
    ma20 = np.full(n, 1.0)
    rsi = np.full(n, 50.0)
    close[5] = 0.90
    ma5[5] = 0.88
    rsi[5] = 15.0
    close[6] = 0.92
    ma5[6] = 0.88
    rsi[6] = 35.0
    close[7] = 0.85          # 跌破事件最低收盘价 0.90
    df = make_features(n, close=close, ma5=ma5, ma10=ma10, ma20=ma20, rsi6=rsi)
    out, events, _ = strategy.oversold_series(df, cfg)
    assert out["os_state"].iloc[7] == "EXPIRED"
    assert "跌破" in events[0]["exit_reason"]


def test_oversold_never_exceeds_probe_limits(cfg):
    df = _build_trigger_sequence()
    out, _, _ = strategy.oversold_series(df, cfg)
    assert out["os_weight"].max() <= float(cfg["oversold"]["weight_step2"])
