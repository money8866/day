from pathlib import Path

import numpy as np
import pytest

import indicators
import portfolio
import report as report_mod
import strategy
import main as app
from conftest import make_ohlcv


def test_load_account_missing_returns_none(cfg, tmp_path):
    cfg["account"] = dict(cfg.get("account", {}), dir=str(tmp_path / "no_account"))
    assert portfolio.load_account(cfg) is None


def test_ensure_templates_created(cfg, tmp_path):
    cfg["account"] = dict(cfg.get("account", {}), dir=str(tmp_path / "acc"))
    created = portfolio.ensure_templates(cfg)
    names = {p.name for p in created}
    assert {"template_snapshot.csv", "template_cashflows.csv", "template_trades.csv"} == names
    assert all(Path(p).exists() for p in created)


def test_snapshot_validation_rejects_negative(cfg, tmp_path):
    acc_dir = tmp_path / "acc"
    acc_dir.mkdir()
    (acc_dir / "snapshot.csv").write_text(
        "trade_date,cash,etf_shares,other_assets\n20260101,-100,0,0\n", encoding="utf-8")
    cfg["account"] = dict(cfg.get("account", {}), dir=str(acc_dir))
    with pytest.raises(ValueError):
        portfolio.load_account(cfg)


def test_market_only_decision_hides_account_fields(cfg):
    df = make_ohlcv(n=320)
    features = indicators.compute_features(df, cfg)
    market, events = strategy.compute_market(features, cfg)
    events["_index_df"] = None

    ctx = app.build_decision(
        features, market, events, cfg,
        trade_date=str(features["trade_date"].iloc[-1]),
        next_trade_date=None, account=None, nav_df=None,
        account_warn=[], quality={},
    )
    assert ctx["account"]["mode"] == "market_only"
    assert ctx["account"]["drawdown"] is None
    assert ctx["caps"]["dd_cap"] == pytest.approx(1.0)
    assert ctx["trade"]["action"] == "N/A"

    text = report_mod.render_markdown(ctx)
    assert "仅市场信号模式" in text
    assert "预计交易金额：需账户数据" in text
    assert "账户当前回撤：未提供账户数据" in text
    assert "预计交易金额：约" not in text


def test_market_only_decision_marks_no_account_conclusion(cfg):
    df = make_ohlcv(n=320)
    features = indicators.compute_features(df, cfg)
    market, events = strategy.compute_market(features, cfg)
    events["_index_df"] = None
    ctx = app.build_decision(
        features, market, events, cfg,
        trade_date=str(features["trade_date"].iloc[-1]),
        next_trade_date=None, account=None, nav_df=None,
        account_warn=[], quality={},
    )
    assert any("未提供账户快照" in r for r in ctx["reasons"])
    assert any("无法计算真实账户回撤" in d for d in ctx["disclaimers"])
