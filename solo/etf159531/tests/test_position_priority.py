import pytest

import strategy


def test_final_is_strictest_cap(cfg):
    final, binding = strategy.binding_constraint(
        trend_base=0.40, os_weight=0.10, vol_cap_value=0.55,
        crisis_cap_value=1.0, dd_cap_value=0.25, max_position=0.65)
    assert final == pytest.approx(0.25)
    assert binding == "账户回撤上限"


def test_volatility_caps_trend_plus_oversold(cfg):
    final, binding = strategy.binding_constraint(
        trend_base=0.65, os_weight=0.10, vol_cap_value=0.45,
        crisis_cap_value=1.0, dd_cap_value=1.0, max_position=0.65)
    assert final == pytest.approx(0.45)
    assert binding == "波动率上限"


def test_crisis_zero_overrides_everything(cfg):
    final, binding = strategy.binding_constraint(
        trend_base=0.65, os_weight=0.10, vol_cap_value=0.65,
        crisis_cap_value=0.0, dd_cap_value=0.40, max_position=0.65)
    assert final == pytest.approx(0.0)
    assert binding == "危机开关上限"


def test_breaker_cap_is_applied(cfg):
    final, binding = strategy.binding_constraint(
        trend_base=0.40, os_weight=0.0, vol_cap_value=0.60,
        crisis_cap_value=1.0, dd_cap_value=0.25, max_position=0.65,
        breaker_cap=0.10)
    assert final == pytest.approx(0.10)
    assert binding == "回撤熔断恢复上限"


def test_system_max_position_never_exceeded(cfg):
    final, binding = strategy.binding_constraint(
        trend_base=0.65, os_weight=0.10, vol_cap_value=0.90,
        crisis_cap_value=1.0, dd_cap_value=1.0, max_position=0.65)
    assert final == pytest.approx(0.65)
    assert binding == "系统仓位上限"


def test_raw_weight_short_circuits(cfg):
    final, binding = strategy.binding_constraint(
        trend_base=0.20, os_weight=0.0, vol_cap_value=0.65,
        crisis_cap_value=1.0, dd_cap_value=0.65, max_position=0.65)
    assert final == pytest.approx(0.20)
    assert binding == "策略组合(趋势+超卖)"


def test_vol_cap_formula(cfg):
    # 波动率 24% -> 12% / 24% = 50%
    assert strategy.vol_cap(0.24, 0.20, cfg) == pytest.approx(0.50)
    # 波动率极低时不超过系统上限
    assert strategy.vol_cap(0.10, 0.10, cfg) == pytest.approx(0.65)
    # 有效波动率下限 18% -> 上限 12% / 18% = 66.7%，再被 65% 截断
    assert strategy.vol_cap(0.05, 0.05, cfg) == pytest.approx(0.65)
