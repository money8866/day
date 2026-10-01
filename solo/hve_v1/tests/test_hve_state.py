# -*- coding: utf-8 -*-
"""§43 HVE 状态机 / 失效 / 冷却 / 市场环境测试（§23~§25、§31/§32，对应 G3/G6）"""
import numpy as np
import pandas as pd

from hve_v1 import load_config
from hve_v1 import signals as S
from hve_v1.indicators import compute_indicators
from hve_v1.market import (MarketEnv, apply_regime,
                           MARKET_OK, MARKET_NEUTRAL, MARKET_RISK)
from hve_v1.state import _StockView, _fail_gate
from tests.synth import make_df, analyze, by_date

CFG = load_config()

_ALLOWED_STATES = {S.S_HVE_EVENT, S.S_BULL_WATCH, S.S_DIGESTION,
                   S.S_BULL, S.S_2ND, S.S_FAIL}


def _flat_hve(extra_close=(), extra_open=(), extra_vol=()):
    close = [10.0] * 60 + [10.5] + list(extra_close)
    open_ = [10.0] * 60 + [10.1] + list(extra_open)
    vol = [1000.0] * 60 + [3000.0] + list(extra_vol)
    return make_df(close, open_, vol=vol)


# ---------------------------------------------------------------- 状态机

def test_hve_day_is_event_not_buy():
    """§15 / §25 / G3：HVE 当日只进 HVE_EVENT，绝不 BUY"""
    d = _flat_hve()
    _, _, recs = analyze(d)
    r = by_date(recs, str(d['trade_date'].iloc[60]))
    assert r['state'] == S.S_HVE_EVENT
    assert r['signal_type'] == S.HVE_WATCH
    assert r['signal'] == S.SIG_WATCH
    assert r['days_since_hve'] == 0


def test_state_is_trackable_every_day():
    """§25：状态必须逐日可追踪，且能真正走到 HVE_BULL"""
    d = _flat_hve([10.7, 10.8], [10.2, 10.7], [1500.0, 1400.0])
    _, _, recs = analyze(d)
    assert recs[0]['state'] == S.S_HVE_EVENT
    assert all(r['state'] in _ALLOWED_STATES for r in recs)
    assert any(r['state'] == S.S_BULL for r in recs)


def test_daily_output_fields_complete():
    """§27：每条记录必须含全部约定输出字段"""
    d = _flat_hve()
    _, _, recs = analyze(d)
    assert recs
    for r in recs:
        missing = [f for f in S.DAILY_FIELDS if f not in r]
        assert not missing, f'缺少 §27 输出字段: {missing}'


# ---------------------------------------------------------------- 失效 §23

def test_fail_on_drawdown_over_10pct():
    """§23：HVE 后最大回撤 > 10% → HVE_FAIL"""
    d = _flat_hve([9.3], [10.2], [1200.0])
    _, _, recs = analyze(d)
    r = by_date(recs, str(d['trade_date'].iloc[61]))
    assert r['drawdown_from_hve'] > CFG['second_max_drawdown']
    assert r['state'] == S.S_FAIL
    assert r['signal_type'] == S.HVE_FAIL
    assert r['signal'] == S.SIG_NONE
    assert r['reason'] == S.REASON_FAIL
    assert r['invalid_rule'] in ('MA20', 'STRUCTURE_LOW', 'TRIGGER_PRICE')


def test_fail_gate_three_rules():
    """§23 三条失效路径分别可触发（白盒：直接驱动 _fail_gate）"""
    d = _flat_hve([10.6] * 9, [10.5] * 9, [1000.0] * 9)
    d = compute_indicators(d, CFG)
    sv = _StockView(d)
    e, t = 60, 61

    # 规则 1：回撤超限
    assert _fail_gate(sv, e, t, CFG, float(CFG['second_max_drawdown']) + 0.01) is True
    # 规则 2：Close < MA20 − k×ATR20（_StockView 数组只读，故整体替换属性）
    sv.ma20 = np.full(sv.n, 20.0)
    sv.atr = np.full(sv.n, 1.0)
    sv.c = np.full(sv.n, 10.0)
    assert _fail_gate(sv, e, t, CFG, 0.0) is True
    # 规则 3：跌破 HVE 后整理区最低价（此时规则 2 已被人为关闭）
    sv.ma20 = np.full(sv.n, 0.0)
    sv.atr = np.full(sv.n, 0.0)
    sv.c = np.full(sv.n, 9.0)
    assert _fail_gate(sv, e, t, CFG, 0.0) is True
    # 结构完好
    sv.c = np.full(sv.n, 11.0)
    assert _fail_gate(sv, e, t, CFG, 0.0) is False


# ---------------------------------------------------------------- 冷却期 §24

def _cooldown_series(day61_close):
    close = [10.0] * 60 + [10.5, day61_close, 10.6]
    open_ = [10.0] * 60 + [10.1, 10.4, 10.3]
    vol = [1000.0] * 60 + [3000.0, 1000.0, 1400.0]
    return make_df(close, open_, vol=vol)


def test_cooldown_blocks_buy_after_fail():
    """§24：HVE_FAIL 后 10 个交易日内不得产生新的 BUY"""
    d = _cooldown_series(10.0)          # 第61日跌破 HVE 后整理区低点 → FAIL
    _, _, recs = analyze(d)
    r61 = by_date(recs, str(d['trade_date'].iloc[61]))
    r62 = by_date(recs, str(d['trade_date'].iloc[62]))
    assert r61['state'] == S.S_FAIL
    assert r62['days_since_hve'] == 2
    assert r62['signal'] == S.SIG_WATCH
    assert r62['signal_type'] == S.HVE_WATCH
    assert r62['reason'].endswith(S.SUF_COOLDOWN)
    assert r62['executable'] is False


def test_no_cooldown_when_no_fail():
    """对照组：同一条 K 线序列，只要第61日不触发 FAIL，第62日照常 BUY"""
    d = _cooldown_series(10.4)
    _, _, recs = analyze(d)
    r61 = by_date(recs, str(d['trade_date'].iloc[61]))
    r62 = by_date(recs, str(d['trade_date'].iloc[62]))
    assert r61['state'] != S.S_FAIL
    assert r62['signal'] == S.SIG_BUY
    assert r62['signal_type'] == S.HVE_BULL
    assert not r62['reason'].endswith(S.SUF_COOLDOWN)


# ---------------------------------------------------------------- 市场环境 §31/§32

def test_apply_regime_risk_downgrades_buy_only():
    """§32：MARKET_RISK 不删信号，只把 BUY 改成 CONDITIONAL"""
    assert apply_regime(S.SIG_BUY, MARKET_RISK) == S.SIG_CONDITIONAL
    assert apply_regime(S.SIG_BUY, MARKET_OK) == S.SIG_BUY
    assert apply_regime(S.SIG_BUY, MARKET_NEUTRAL) == S.SIG_BUY
    assert apply_regime(S.SIG_WATCH, MARKET_RISK) == S.SIG_WATCH
    assert apply_regime(S.SIG_NONE, MARKET_RISK) == S.SIG_NONE


def test_market_risk_turns_buy_into_conditional():
    """§32 端到端：同一根 BUY K 线，RISK 环境下 signal=CONDITIONAL 但 signal_type 保留"""
    from hve_v1 import signals as SG

    d = _flat_hve([10.7], [10.2], [1500.0])
    _, _, neutral = analyze(d)
    _, _, risky = analyze(d, regime_fn=lambda dt: MARKET_RISK)
    rn = by_date(neutral, str(d['trade_date'].iloc[61]))
    rr = by_date(risky, str(d['trade_date'].iloc[61]))
    assert rn['signal'] == SG.SIG_BUY
    assert rr['signal'] == SG.SIG_CONDITIONAL
    assert rr['signal_type'] == SG.HVE_BULL
    assert rr['market_regime'] == MARKET_RISK


class _StubData:
    """MarketEnv 的最小数据桩（只需 index_daily + breadth）"""

    def __init__(self, closes, up_ratio=0.5):
        self._closes = list(closes)
        self._ur = up_ratio

    def index_daily(self, code, end_date='', start_date=''):
        dates = pd.bdate_range('20240101', periods=len(self._closes)).strftime('%Y%m%d')
        return pd.DataFrame({'trade_date': dates, 'close': self._closes})

    def breadth(self, trade_date):
        return {'up_ratio': self._ur, 'n': 100, 'amount_sum': 1e9}


def test_market_env_three_states():
    """§31：指数上行+广度好 → OK；指数跌破 MA60 → RISK；其余 → NEUTRAL"""
    td = '20250106'
    rising = [10.0 + 0.05 * k for k in range(80)]
    falling = [12.0 - 0.05 * k for k in range(80)]
    dip_rebound = [10.0 - 2.0 * k / 59.0 for k in range(60)] \
        + [8.0 + 1.0 * (j + 1) / 20.0 for j in range(20)]

    assert MarketEnv(_StubData(rising, 0.60), CFG).regime(td) == MARKET_OK
    assert MarketEnv(_StubData(falling, 0.60), CFG).regime(td) == MARKET_RISK
    assert MarketEnv(_StubData(dip_rebound, 0.50), CFG).regime(td) == MARKET_NEUTRAL
    # 广度崩塌时不能是 OK
    assert MarketEnv(_StubData(rising, 0.10), CFG).regime(td) == MARKET_RISK
