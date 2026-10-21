# -*- coding: utf-8 -*-
"""MSW-C 信号逻辑测试：C1/C2/C3 规则、退出规则、优先级、风险收益比。

全部使用受控行字典 + 桩 SwingHistory，精确命中/偏离各配置阈值。
"""
import numpy as np
import pandas as pd
import pytest

from msw.signal_engine import (PosState, eval_c1, eval_c2, eval_c3, eval_exit,
                               risk_reward)
from msw.tests.conftest import make_feats, make_bars, walker_on


# ---------------------------------------------------------------- 桩

class StubSH:
    """固定 stage_high / prev_breakout_level 的 SwingHistory 替身"""

    def __init__(self, stage_high=(12.0, 50), prev_bo=None):
        self._sh = stage_high
        self._pb = prev_bo

    def stage_high(self, t, lookback=250):
        return self._sh

    def prev_breakout_level(self, t, lookback=250):
        return self._pb


def _c1_row(**over):
    """理想 C1 行：回撤 10%（6%~18%）、2.4×ATR、贴近 MA20、缩量、收复 MA5"""
    row = {'_t': 59, 'hfq_close': 10.8, 'atr20': 0.5,
           'ma5': 10.7, 'ma20': 10.9, 'ma60': 10.5,
           'amount_ma5': 30_000.0, 'amount_ma20': 50_000.0,
           'high_10_prev': 10.85}
    row.update(over)
    return row


def _st():
    return {'swing_count': 5}


# ---------------------------------------------------------------- C1

def test_c1_buy_ideal(cfg):
    status, detail = eval_c1(_c1_row(), StubSH(), _st(), True, cfg)
    assert status == 'buy'
    assert detail['depth'] == pytest.approx(0.1)
    assert detail['atr_mult'] == pytest.approx(2.4)
    assert detail['support'] == pytest.approx(abs(10.8 / 10.9 - 1.0))
    assert detail['vol_contract'] and detail['reclaim']


def test_c1_setup_when_volume_not_contracted(cfg):
    status, detail = eval_c1(_c1_row(amount_ma5=48_000.0), StubSH(), _st(), True, cfg)
    assert status == 'setup'
    assert not detail['vol_contract']


def test_c1_setup_when_no_reclaim(cfg):
    status, detail = eval_c1(_c1_row(ma5=11.0, high_10_prev=11.2),
                             StubSH(), _st(), True, cfg)
    assert status == 'setup'
    assert not detail['reclaim']


def test_c1_blocked_without_trend_filter(cfg):
    status, _ = eval_c1(_c1_row(), StubSH(), _st(), False, cfg)
    assert status is None


def test_c1_none_when_shallow_and_far_and_no_vol_contract(cfg):
    # 回撤过浅（不在 6%~18% 也不在 1.5~2.5×ATR）+ 远离支撑 + 不放缩量
    row = _c1_row(hfq_close=11.7, ma5=11.6, ma20=12.5, ma60=12.5,
                  amount_ma5=49_000.0, high_10_prev=11.75)
    status, detail = eval_c1(row, StubSH(prev_bo=None), _st(), True, cfg)
    assert status is None
    assert detail['depth'] == pytest.approx(0.025)     # 2.5% < 6% 下限
    assert detail['atr_mult'] == pytest.approx(0.6)    # < 1.5×ATR 下限


def test_c1_prev_breakout_level_support(cfg):
    """前突破位也可作为支撑（靠近即算）"""
    row = _c1_row(ma20=12.5, ma60=12.5)          # 远离 MA20/MA60
    status, detail = eval_c1(row, StubSH(prev_bo=10.85), _st(), True, cfg)
    assert detail['support'] == pytest.approx(abs(10.8 / 10.85 - 1.0))
    assert status == 'buy'


# ---------------------------------------------------------------- C2

def test_c2_buy_oversold_no_panic(cfg):
    row = {'rsi6': 25.0, 'dev_ma20': -0.05, 'boll_pos': 0.15, 'vr20': 1.2}
    status, detail = eval_c2(row, _st(), True, cfg)
    assert status == 'buy'


def test_c2_buy_on_dev_ma20(cfg):
    row = {'rsi6': 50.0, 'dev_ma20': -0.10, 'boll_pos': 0.5, 'vr20': 1.0}
    status, _ = eval_c2(row, _st(), True, cfg)
    assert status == 'buy'


def test_c2_none_on_panic_volume(cfg):
    row = {'rsi6': 25.0, 'dev_ma20': -0.10, 'boll_pos': 0.1, 'vr20': 2.0}
    status, _ = eval_c2(row, _st(), True, cfg)
    assert status is None


def test_c2_none_when_not_oversold(cfg):
    row = {'rsi6': 50.0, 'dev_ma20': -0.02, 'boll_pos': 0.5, 'vr20': 1.0}
    status, _ = eval_c2(row, _st(), True, cfg)
    assert status is None


def test_c2_requires_trend_filter(cfg):
    row = {'rsi6': 25.0, 'dev_ma20': -0.10, 'boll_pos': 0.1, 'vr20': 1.0}
    status, _ = eval_c2(row, _st(), False, cfg)
    assert status is None


# ---------------------------------------------------------------- C3

def _c3_feats(n=60):
    """C3 测试用特征框：0-39 上涨、39-58 窄幅整理、59 放量突破"""
    rng = np.random.default_rng(11)
    t = np.arange(n)
    close = np.concatenate([
        np.exp(np.linspace(np.log(10.0), np.log(13.0), 40)),      # 0-39 上涨
        np.full(19, 13.1) * (1 + rng.normal(0, 0.002, 19)),       # 39-57 整理
        [13.8],                                                   # 58 前收
    ])
    close[58] = 13.3
    high = close * 1.003
    low = close * 0.997
    amount = np.concatenate([
        np.full(40, 60_000.0),        # 0-39 上涨段放量
        np.full(19, 25_000.0),        # 40-58 整理段明显缩量
        [80_000.0],                   # 59 突破日
    ])
    return pd.DataFrame({
        'hfq_close': close, 'hfq_high': high, 'hfq_low': low,
        'amount': amount,
    })


def _c3_row(**over):
    row = {'_t': 59, 'hfq_close': 13.8, 'pct_chg': 0.035, 'amount': 80_000.0,
           'amount_ma20': 45_000.0, 'open': 13.4, 'pre_close': 13.33}
    row.update(over)
    return row


def test_c3_buy_on_valid_breakout(cfg):
    status, detail = eval_c3(_c3_row(), _c3_feats(), 59, StubSH((13.8, 30)), True, cfg)
    assert status == 'buy'
    assert detail['cons_days'] >= 5
    assert detail['pct'] == pytest.approx(0.035)


def test_c3_none_when_pct_too_small(cfg):
    status, _ = eval_c3(_c3_row(pct_chg=0.01), _c3_feats(), 59,
                        StubSH((13.8, 30)), True, cfg)
    assert status is None


def test_c3_none_when_volume_not_expanding(cfg):
    status, _ = eval_c3(_c3_row(amount=40_000.0), _c3_feats(), 59,
                        StubSH((13.8, 30)), True, cfg)
    assert status is None


def test_c3_skips_gap_up(cfg):
    status, detail = eval_c3(_c3_row(open=14.2), _c3_feats(), 59,
                             StubSH((13.8, 30)), True, cfg)
    assert status is None
    assert detail['gap'] > cfg['signal_c3_breakout']['max_gap_up_pct']


def test_c3_none_without_volume_contraction(cfg):
    """整理段不缩量 → 无有效整理窗口"""
    feats = _c3_feats()
    feats.loc[39:58, 'amount'] = 60_000.0        # 整理段与上涨段同量
    status, _ = eval_c3(_c3_row(), feats, 59, StubSH((13.8, 30)), True, cfg)
    assert status is None


def test_c3_blocked_without_trend_filter(cfg):
    status, _ = eval_c3(_c3_row(), _c3_feats(), 59, StubSH((13.8, 30)), False, cfg)
    assert status is None


# ---------------------------------------------------------------- 退出

def _pos(rule='C1', **over):
    kw = dict(entry_bar=100, entry_date='20240101', entry_price=10.0, rule=rule,
              quality=60.0, atr_at_entry=0.5, stage_high_at_entry=12.0,
              pullback_low=9.5, ma120_at_entry=9.8, high_watermark=11.0,
              bars_held=5, tp1_done=False, stop_level=9.5,
              tp1_level=None, tp2_level=None)
    kw.update(over)
    return PosState(**kw)


def _row(close=10.6, atr20=0.4):
    return {'hfq_close': close, 'atr20': atr20, 'ma120': 9.8, '_t': 105}


def test_exit_c1_close_below_ma120(cfg):
    action, reason = eval_exit(_row(close=9.7), _pos(rule='C1'), 0, cfg)
    assert (action, reason) == ('exit', 'close_below_ma120')


def test_exit_c2_survives_below_ma120(cfg):
    """C2 入场价本就在 MA120 下方，不得以 MA120 为失效条件（规则错配回归）"""
    action, reason = eval_exit(_row(close=9.7), _pos(rule='C2'), 0, cfg)
    assert action == 'hold'


def test_exit_c2_still_stops_below_pullback_low(cfg):
    action, reason = eval_exit(_row(close=9.0), _pos(rule='C2'), 0, cfg)
    assert (action, reason) == ('exit', 'below_pullback_low')


def test_exit_tp1_reduce_by_atr(cfg):
    action, reason = eval_exit(_row(close=10.6), _pos(), 0, cfg)
    assert (action, reason) == ('reduce', 'tp1')


def test_exit_tp1_reduce_near_prior_high(cfg):
    action, reason = eval_exit(_row(close=11.8), _pos(), 0, cfg)
    assert (action, reason) == ('reduce', 'tp1')


def test_exit_tp2_after_reduce(cfg):
    action, reason = eval_exit(_row(close=11.2), _pos(tp1_done=True), 0, cfg)
    assert (action, reason) == ('exit', 'tp2')


def test_exit_trailing_stop(cfg):
    pos = _pos(tp1_done=True, high_watermark=11.5)
    action, reason = eval_exit(_row(close=10.5, atr20=0.4), pos, 0, cfg)
    assert (action, reason) == ('exit', 'trailing_stop')


def test_exit_max_hold_days(cfg):
    action, reason = eval_exit(_row(), _pos(bars_held=120), 0, cfg)
    assert (action, reason) == ('exit', 'max_hold_days')


def test_exit_rs_breakdown(cfg):
    action, reason = eval_exit(_row(), _pos(), 10, cfg)
    assert (action, reason) == ('exit', 'rs_breakdown')


def test_exit_hold_inside_band(cfg):
    action, reason = eval_exit(_row(close=10.4), _pos(), 0, cfg)
    assert (action, reason) == ('hold', '')


# ---------------------------------------------------------------- 风险收益比

def test_risk_reward_basic(cfg):
    row = {'_t': 59, 'hfq_close': 10.0, 'low_20': 9.0, 'ma120': 9.5}
    rr, stop = risk_reward(row, StubSH((12.0, 50)), cfg)
    assert stop == pytest.approx(9.0)              # min(低点, MA120)
    assert rr == pytest.approx(0.2 / 0.1)          # 0.2 / 0.1


def test_risk_reward_none_without_stage_high(cfg):
    row = {'_t': 59, 'hfq_close': 10.0, 'low_20': 9.0, 'ma120': 9.5}
    rr, stop = risk_reward(row, StubSH(stage_high=None), cfg)
    assert rr is None and stop is None


def test_risk_reward_none_when_stop_above_close(cfg):
    row = {'_t': 59, 'hfq_close': 10.0, 'low_20': 10.5, 'ma120': 10.2}
    rr, stop = risk_reward(row, StubSH((12.0, 50)), cfg)
    assert rr is None


# ---------------------------------------------------------------- 优先级（集成）

def _fresh_walker(cfg, panel, feats):
    w, _ = walker_on(feats, cfg, panel)
    return w


def test_rule_priority_c1_over_c3_over_c2(monkeypatch, cfg, panel):
    """同日多信号就绪时：C1 > C3 > C2（§6.4）"""
    import msw.signal_engine as SE
    feats = make_feats(n=400, cfg=cfg)
    c = dict(cfg)
    c['quality_score'] = dict(cfg['quality_score'], gate_entries=False)
    monkeypatch.setattr(SE, 'risk_reward',
                        lambda row, sh, cfg: (2.0, 9.0))

    def run_with(c1, c3, c2):
        monkeypatch.setattr(SE, 'eval_c1', lambda row, sh, st, tp, cfg: (c1, {'r': 1}))
        monkeypatch.setattr(SE, 'eval_c3',
                            lambda row, feats, t, sh, tp, cfg: (c3, {'r': 3}))
        monkeypatch.setattr(SE, 'eval_c2', lambda row, st, tp, cfg: (c2, {'r': 2}))
        w = _fresh_walker(c, panel, feats)
        w.step(300)
        return [e for e in w.events if e['type'] == 'ENTRY']

    ev = run_with('buy', 'buy', 'buy')
    assert len(ev) == 1 and ev[0]['rule'] == 'C1'
    ev = run_with('setup', 'buy', 'buy')
    assert len(ev) == 1 and ev[0]['rule'] == 'C3'
    ev = run_with(None, None, 'buy')
    assert len(ev) == 1 and ev[0]['rule'] == 'C2'


def test_no_entry_when_rr_below_threshold(monkeypatch, cfg, panel):
    """风险收益比不足 → 仅 setup，不开仓"""
    import msw.signal_engine as SE
    feats = make_feats(n=400, cfg=cfg)
    c = dict(cfg)
    c['quality_score'] = dict(cfg['quality_score'], gate_entries=False)
    monkeypatch.setattr(SE, 'eval_c1', lambda row, sh, st, tp, cfg: ('buy', {'r': 1}))
    monkeypatch.setattr(SE, 'eval_c3', lambda row, feats, t, sh, tp, cfg: (None, {}))
    monkeypatch.setattr(SE, 'eval_c2', lambda row, st, tp, cfg: (None, {}))
    monkeypatch.setattr(SE, 'risk_reward', lambda row, sh, cfg: (1.0, 9.0))
    w = _fresh_walker(c, panel, feats)
    w.step(300)
    assert not w.events
    assert w.records[-1]['current_signal'] == 'BUY_SETUP'


def test_no_entry_when_quality_gate_fails(monkeypatch, cfg, panel):
    """质量分门槛：总分 < min_score 时禁止入场（gate_entries=true）"""
    import msw.signal_engine as SE
    from msw import scanner as SC
    feats = make_feats(n=400, cfg=cfg)
    monkeypatch.setattr(SE, 'eval_c1', lambda row, sh, st, tp, cfg: ('buy', {'r': 1}))
    monkeypatch.setattr(SE, 'eval_c3', lambda row, feats, t, sh, tp, cfg: (None, {}))
    monkeypatch.setattr(SE, 'eval_c2', lambda row, st, tp, cfg: (None, {}))
    monkeypatch.setattr(SE, 'risk_reward', lambda row, sh, cfg: (2.0, 9.0))
    monkeypatch.setattr(SC, 'quality_score',
                        lambda row, st, rs, uni_row, cfg: {
                            'total': 40.0,
                            'sub': {k: 40.0 for k in (
                                'trend_quality', 'swing_space', 'recovery',
                                'frequency', 'volume_structure', 'liquidity_risk')},
                            'tags': ['测试低分']})
    w = _fresh_walker(cfg, panel, feats)          # gate_entries=True
    w.step(300)
    assert not w.events, '质量分不足时不得开仓'


def test_entry_allowed_when_quality_gate_disabled(monkeypatch, cfg, panel):
    """消融项 quality_score：gate_entries=false 时同样信号可以入场"""
    import msw.signal_engine as SE
    from msw import scanner as SC
    feats = make_feats(n=400, cfg=cfg)
    c = dict(cfg)
    c['quality_score'] = dict(cfg['quality_score'], gate_entries=False)
    monkeypatch.setattr(SE, 'eval_c1', lambda row, sh, st, tp, cfg: ('buy', {'r': 1}))
    monkeypatch.setattr(SE, 'eval_c3', lambda row, feats, t, sh, tp, cfg: (None, {}))
    monkeypatch.setattr(SE, 'eval_c2', lambda row, st, tp, cfg: (None, {}))
    monkeypatch.setattr(SE, 'risk_reward', lambda row, sh, cfg: (2.0, 9.0))
    monkeypatch.setattr(SC, 'quality_score',
                        lambda row, st, rs, uni_row, cfg: {
                            'total': 40.0,
                            'sub': {k: 40.0 for k in (
                                'trend_quality', 'swing_space', 'recovery',
                                'frequency', 'volume_structure', 'liquidity_risk')},
                            'tags': ['测试低分']})
    w = _fresh_walker(c, panel, feats)
    w.step(300)
    assert len(w.events) == 1 and w.events[0]['rule'] == 'C1'
