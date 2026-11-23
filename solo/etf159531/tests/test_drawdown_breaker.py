import numpy as np
import pandas as pd
import pytest

import risk_manager


def test_drawdown_cap_tiers(cfg):
    assert risk_manager.drawdown_cap(-0.01, cfg) == pytest.approx(0.65)
    assert risk_manager.drawdown_cap(-0.08, cfg) == pytest.approx(0.40)
    assert risk_manager.drawdown_cap(-0.119, cfg) == pytest.approx(0.40)
    assert risk_manager.drawdown_cap(-0.12, cfg) == pytest.approx(0.25)
    assert risk_manager.drawdown_cap(-0.16, cfg) == pytest.approx(0.10)
    assert risk_manager.drawdown_cap(-0.18, cfg) == pytest.approx(0.0)
    assert risk_manager.drawdown_cap(-0.25, cfg) == pytest.approx(0.0)
    assert risk_manager.drawdown_cap(None, cfg) == pytest.approx(1.0)


def test_unit_nav_excludes_external_flows(cfg):
    price = pd.DataFrame({"trade_date": ["20240102", "20240103", "20240104"],
                          "close": [1.0, 1.0, 1.0]})
    snap = pd.DataFrame({
        "trade_date": ["20240102", "20240104"],
        "cash": [100000.0, 200000.0],
        "etf_shares": [0.0, 0.0],
        "other_assets": [0.0, 0.0],
    })
    flows = pd.DataFrame({"trade_date": ["20240104"], "amount": [100000.0]})
    nav = risk_manager.build_account_nav(snap, price, flows)
    assert nav["unit_nav"].tolist() == pytest.approx([1.0, 1.0, 1.0])
    assert nav["drawdown"].abs().max() == pytest.approx(0.0)


def test_nav_drawdown_without_flows(cfg):
    price = pd.DataFrame({"trade_date": ["20240102", "20240103", "20240104"],
                          "close": [2.0, 1.8, 1.9]})
    snap = pd.DataFrame({
        "trade_date": ["20240102", "20240103", "20240104"],
        "cash": [0.0, 0.0, 0.0],
        "etf_shares": [1000.0, 1000.0, 1000.0],
        "other_assets": [0.0, 0.0, 0.0],
    })
    nav = risk_manager.build_account_nav(snap, price, None)
    assert nav["drawdown"].iloc[1] == pytest.approx(1.8 / 2.0 - 1)
    assert nav["drawdown"].iloc[2] == pytest.approx(1.9 / 2.0 - 1)


def test_breaker_requires_cooldown_and_gates(cfg):
    m = risk_manager.BreakerMachine(cfg)
    stage, cap = m.step(-0.19)
    assert stage == "COOLDOWN" and cap == pytest.approx(0.0)

    # 冷静期 10 日：前 9 日即使指数站上 MA20 也不能重启
    for _ in range(9):
        stage, cap = m.step(-0.19, above_ma20_3=True, idx_vol20=0.2)
    assert stage == "COOLDOWN" and cap == pytest.approx(0.0)

    stage, cap = m.step(-0.19, above_ma20_3=True, idx_vol20=0.2)
    assert stage == "RESTART" and cap == pytest.approx(0.10)

    # 重启后 10 日 + 指数 3 日站上 MA60 -> 25%
    for _ in range(9):
        stage, cap = m.step(-0.19, above_ma20_3=True, above_ma60_3=True, idx_vol20=0.2)
    assert stage == "RESTART" and cap == pytest.approx(0.10)
    stage, cap = m.step(-0.19, above_ma20_3=True, above_ma60_3=True, idx_vol20=0.2)
    assert stage == "STEP2" and cap == pytest.approx(0.25)

    # 回撤改善至 12% 以内 -> 解除恢复状态
    stage, cap = m.step(-0.05)
    assert stage == "NORMAL" and cap == pytest.approx(1.0)


def test_breaker_does_not_restart_without_index_gate(cfg):
    m = risk_manager.BreakerMachine(cfg)
    m.step(-0.20)
    for _ in range(15):
        stage, cap = m.step(-0.20, above_ma20_3=False, idx_vol20=0.20)
    assert stage == "COOLDOWN" and cap == pytest.approx(0.0)


def test_breaker_restart_blocked_by_high_vol(cfg):
    m = risk_manager.BreakerMachine(cfg)
    m.step(-0.20)
    for _ in range(15):
        stage, cap = m.step(-0.20, above_ma20_3=True, idx_vol20=0.40)
    assert stage == "COOLDOWN"


def test_breaker_new_alert_blocks_step2(cfg):
    m = risk_manager.BreakerMachine(cfg)
    m.step(-0.20)
    for _ in range(10):
        stage, cap = m.step(-0.20, above_ma20_3=True, idx_vol20=0.20)
    assert stage == "RESTART"
    for _ in range(10):
        stage, cap = m.step(-0.20, above_ma20_3=True, above_ma60_3=True,
                            idx_vol20=0.20, alert=True)
    assert stage == "RESTART" and cap == pytest.approx(0.10)


def test_account_risk_combines_tiers_and_breaker(cfg):
    nav = pd.DataFrame({
        "trade_date": ["20240102", "20240103", "20240104"],
        "nav": [1.0, 0.95, 0.83],
        "flow": [0.0, 0.0, 0.0],
        "unit_nav": [1.0, 0.95, 0.83],
        "peak_unit_nav": [1.0, 1.0, 1.0],
        "drawdown": [0.0, -0.05, -0.17],
    })
    out = risk_manager.account_risk(nav, cfg, None)
    assert out["dd_cap"].iloc[2] == pytest.approx(0.10)
    assert out["account_cap"].iloc[2] <= 0.10
