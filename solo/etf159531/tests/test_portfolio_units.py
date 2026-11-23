import pytest

import portfolio


def _state(nav=100000.0, shares=0.0, cash=100000.0, price=1.0):
    return {
        "nav": nav, "shares": shares, "cash": cash,
        "current_weight": shares * price / nav if nav else 0.0,
    }


def test_buy_rounds_to_lot(cfg):
    st = _state()
    plan = portfolio.plan_trade(0.50, st, 1.00, cfg)
    assert plan.action == "BUY"
    assert plan.exec_shares == pytest.approx(50000)
    assert plan.exec_shares % 100 == 0


def test_sell_rounds_to_lot(cfg):
    st = _state(nav=100000, shares=50000, cash=0, price=1.0)
    plan = portfolio.plan_trade(0.30, st, 1.00, cfg)
    assert plan.action == "SELL"
    assert plan.exec_shares == pytest.approx(-20000)


def test_buy_respects_cash(cfg):
    st = _state(nav=100000, shares=0, cash=1000)
    plan = portfolio.plan_trade(0.65, st, 2.00, cfg)
    assert plan.action == "BUY"
    assert plan.exec_shares == pytest.approx(400)
    assert plan.est_amount <= 1000 + 1e-6


def test_hold_when_below_one_lot(cfg):
    st = _state(nav=1000, shares=0, cash=1000)
    plan = portfolio.plan_trade(0.50, st, 10.0, cfg)
    assert plan.action == "HOLD"
    assert plan.exec_shares == 0


def test_target_zero_liquidates(cfg):
    st = _state(nav=100000, shares=100000, cash=0, price=1.0)
    plan = portfolio.plan_trade(0.0, st, 1.0, cfg)
    assert plan.action == "SELL"
    assert plan.exec_shares == pytest.approx(-100000)


def test_should_rebalance_threshold(cfg):
    ok, why = portfolio.should_rebalance(0.33, 0.30, cfg)
    assert not ok and "小于" in why
    ok, why = portfolio.should_rebalance(0.36, 0.30, cfg)
    assert ok
    ok, why = portfolio.should_rebalance(0.20, 0.30, cfg)
    assert ok and "降风险" in why


def test_week_last_detection():
    assert portfolio.is_week_last("20261120", "20261123")   # 周五 -> 下周一
    assert not portfolio.is_week_last("20261119", "20261120")
    assert portfolio.is_week_last("20261120", None)


def test_account_state_marked_to_market():
    import pandas as pd

    acc = portfolio.AccountData(
        snapshot=pd.DataFrame({"trade_date": ["20261120"], "cash": [10000.0],
                               "etf_shares": [9000.0], "other_assets": [0.0]}),
        flows=pd.DataFrame(), trades=pd.DataFrame(), source_dir=None,
    )
    st = portfolio.latest_account_state(acc, 1.5)
    assert st["etf_value"] == pytest.approx(13500.0)
    assert st["nav"] == pytest.approx(23500.0)
    assert st["current_weight"] == pytest.approx(13500 / 23500)
