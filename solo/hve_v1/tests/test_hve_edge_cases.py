# -*- coding: utf-8 -*-
"""§45 边界条件测试（对应 G10）

必须覆盖：high == low / volume == 0 / MA20 缺失 / MA60 缺失 / ATR20 缺失 /
上市不足 20 日 / 停牌 / 复牌 / 涨停 / 连续涨停 / 一字板 / 极端异常成交量。

总要求：**不得产生 NaN BUY** —— 任何 signal=BUY/CONDITIONAL 的记录，
其 entry_price 必须有限且 > 0。
"""
import numpy as np
import pytest

from hve_v1 import load_config
from hve_v1 import signals as S
from hve_v1.event import annotate_events
from hve_v1.indicators import compute_indicators
from hve_v1.state import _StockView, evaluate_day
from hve_v1.tradability import (tradability_flag,
                                LIQUID, LIMIT_UP_RISK, ONE_PRICE_BOARD)
from tests.synth import make_df, analyze, by_date

CFG = load_config()


def _base(n=60):
    """n 天横盘基线（显式给 high/low，避免 make_df 的自算口径干扰断言）"""
    return ([10.0] * n, [10.0] * n, [10.02] * n, [9.98] * n, [1000.0] * n)


def _cat(base, close, open_, high, low, vol):
    c, o, h, l, v = base
    return make_df(c + list(close), o + list(open_),
                   high=h + list(high), low=l + list(low), vol=v + list(vol))


def _assert_no_nan_buy(recs, ctx=''):
    """§45 总要求：BUY / CONDITIONAL 记录的 entry_price 必须有限且 > 0"""
    for r in recs:
        if r['signal'] in (S.SIG_BUY, S.SIG_CONDITIONAL):
            assert np.isfinite(r['entry_price']) and r['entry_price'] > 0, \
                f'{ctx} 出现 NaN BUY: {r}'
            assert r['signal_type'] in (S.HVE_BULL, S.HVE_2ND), \
                f'{ctx} BUY 的 signal_type 非法: {r["signal_type"]}'


# ---------------------------------------------------------------- 形态异常

def test_high_eq_low_no_event():
    """§6：high == low → CLV 缺失 → 不产生 HVE 事件"""
    d = _cat(_base(), [10.5], [10.1], [10.5], [10.5], [3000.0])
    d, ev, recs = analyze(d)
    assert np.isnan(float(d['clv'].iloc[60]))
    assert ev == []
    assert recs == []


def test_volume_zero_no_event():
    """§6：volume == 0 → VR20 = 0 → 不产生 HVE 事件"""
    d = _cat(_base(), [10.5], [10.1], [10.521], [10.0798], [0.0])
    _, ev, recs = analyze(d)
    assert ev == []
    assert recs == []


def test_extreme_volume_still_produces_event():
    """§45 极端异常成交量：量比 100× 仍按规则成事件，指标不溢出/不 NaN"""
    d = _cat(_base(), [10.5], [10.1], [10.521], [10.0798], [100000.0])
    d, ev, recs = analyze(d)
    assert len(ev) == 1
    r = by_date(recs, str(d['trade_date'].iloc[60]))
    assert r['vr20'] == pytest.approx(100.0)
    assert r['state'] == S.S_HVE_EVENT
    assert r['signal'] == S.SIG_WATCH          # HVE 当日永不 BUY（§15）


# ---------------------------------------------------------------- 指标缺失

def test_short_history_no_event():
    """§45 上市不足 20 日：vr20 / MA20 / MA60 / ATR20 全缺失 → 无事件、无信号"""
    close = [10.0] * 5 + [10.5]
    open_ = [10.0] * 5 + [10.1]
    d = make_df(close, open_, vol=[1000.0] * 5 + [3000.0])
    d, ev, recs = analyze(d)
    assert ev == []
    assert recs == []
    assert d['vr20'].isna().all()
    assert d['ma20'].isna().all()
    assert d['ma60'].isna().all()
    assert d['atr20'].isna().all()


def test_ma60_missing_does_not_block_or_crash():
    """§45 MA60 缺失（上市 42 日）：§12 只要求 MA20，故仍可给出 HVE_BULL"""
    d = _cat(_base(40), [10.5, 10.7], [10.1, 10.2],
             [10.521, 10.7214], [10.0798, 10.1796], [3000.0, 1500.0])
    d, ev, recs = analyze(d)
    r = by_date(recs, str(d['trade_date'].iloc[41]))
    assert r is not None
    assert not np.isfinite(r['ma60'])
    assert r['signal_type'] == S.HVE_BULL
    assert r['signal'] == S.SIG_BUY
    assert np.isfinite(r['entry_price'])
    _assert_no_nan_buy(recs, 'MA60 缺失')


def test_missing_ma20_or_atr_never_yields_buy():
    """§45 MA20 / ATR20 缺失：结构保护无法成立 → 不得给 BUY（白盒注入 NaN）"""
    d = _cat(_base(), [10.5, 10.7], [10.1, 10.2],
             [10.521, 10.7214], [10.0798, 10.1796], [3000.0, 1500.0])
    d = compute_indicators(d, CFG)
    d, ev = annotate_events(d, '600000.SH', CFG)
    sv = _StockView(d)
    sv.ma20 = np.full(sv.n, np.nan)
    sv.atr = np.full(sv.n, np.nan)
    rec = evaluate_day(sv, '600000.SH', '', ev[0], 61, CFG)
    assert rec['signal'] != S.SIG_BUY
    assert rec['signal_type'] != S.HVE_BULL
    assert rec['state'] in (S.S_HVE_EVENT, S.S_DIGESTION)
    assert not np.isfinite(rec['ma20'])


# ---------------------------------------------------------------- 停牌 / 复牌

def test_suspension_then_resume():
    """§45 停牌日（volume=0、high==low、close==pre_close）不成事件，复牌后正常推进"""
    d = _cat(_base(),
             [10.5, 10.5, 10.7],                     # 60=HVE, 61=停牌, 62=复牌
             [10.1, 10.5, 10.2],
             [10.521, 10.5, 10.7214],
             [10.0798, 10.5, 10.1796],
             [3000.0, 0.0, 1500.0])
    d, ev, recs = analyze(d)
    assert not bool(d['hve_flag'].iloc[61])          # 停牌日不产生事件
    r61 = by_date(recs, str(d['trade_date'].iloc[61]))
    assert r61 is not None and r61['signal'] == S.SIG_WATCH
    r62 = by_date(recs, str(d['trade_date'].iloc[62]))
    assert r62['signal_type'] == S.HVE_BULL          # 复牌后量价确认 → BUY
    assert r62['signal'] == S.SIG_BUY
    _assert_no_nan_buy(recs, '停牌/复牌')


# ---------------------------------------------------------------- 涨停 / 一字板

def _trad_df(tail_close, tail_open, tail_high, tail_low, tail_vol, n=5):
    d = make_df([10.0] * n + list(tail_close), [10.0] * n + list(tail_open),
                high=[10.02] * n + list(tail_high),
                low=[9.98] * n + list(tail_low),
                vol=[1000.0] * n + list(tail_vol))
    return d


def test_tradability_flags():
    """§46 tradability_flag：LIQUID / LIMIT_UP_RISK / ONE_PRICE_BOARD 三态可区分"""
    # 一字板：open == close == high == low == 涨停价
    one = _trad_df([11.0], [11.0], [11.0], [11.0], [1500.0])
    assert tradability_flag(one, 5, '600000.SH') == ONE_PRICE_BOARD
    # 涨停但盘中有成交：high != low
    lu = _trad_df([11.0], [10.5], [11.0], [10.4], [1500.0])
    assert tradability_flag(lu, 5, '600000.SH') == LIMIT_UP_RISK
    # 普通交易日
    ok = _trad_df([10.2], [10.0], [10.25], [9.95], [1200.0])
    assert tradability_flag(ok, 5, '600000.SH') == LIQUID
    # 创业板 20cm：PreClose 10 → 涨停 12.0
    cy = _trad_df([12.0], [11.0], [12.0], [10.9], [1500.0])
    assert tradability_flag(cy, 5, '300001.SZ') == LIMIT_UP_RISK


def test_consecutive_limit_up_flagged():
    """§45 连续涨停：两日均标记 LIMIT_UP_RISK（研究信号保留、执行层控制）"""
    d = _trad_df([11.0, 12.1], [10.5, 11.2], [11.0, 12.1], [10.4, 11.2],
                 [1500.0, 1500.0])
    assert tradability_flag(d, 5, '600000.SH') == LIMIT_UP_RISK
    assert tradability_flag(d, 6, '600000.SH') == LIMIT_UP_RISK


def test_limit_up_buy_is_not_executable():
    """§46：涨停日可以是 HVE_BULL 研究信号，但 executable=False 且 reason 带后缀"""
    d = _cat(_base(), [10.5, 11.55], [10.1, 11.0],
             [10.521, 11.55], [10.0798, 10.9], [3000.0, 1500.0])
    _, _, recs = analyze(d)
    r = by_date(recs, str(d['trade_date'].iloc[61]))
    assert r['signal_type'] == S.HVE_BULL
    assert r['signal'] == S.SIG_BUY
    assert r['tradability_flag'] == LIMIT_UP_RISK
    assert r['executable'] is False
    assert r['reason'].endswith(S.SUF_LIMIT_UP)
    assert np.isfinite(r['entry_price'])
    _assert_no_nan_buy(recs, '涨停日')


# ---------------------------------------------------------------- 全局断言

def test_no_nan_buy_across_edge_scenarios():
    """§45 总要求：以上全部边界场景都不得产生 NaN BUY"""
    scenarios = {
        'high_eq_low': _cat(_base(), [10.5], [10.1], [10.5], [10.5], [3000.0]),
        'volume_zero': _cat(_base(), [10.5], [10.1], [10.521], [10.0798], [0.0]),
        'extreme_vol': _cat(_base(), [10.5], [10.1], [10.521], [10.0798], [1e5]),
        'ma60_missing': _cat(_base(40), [10.5, 10.7], [10.1, 10.2],
                             [10.521, 10.7214], [10.0798, 10.1796], [3000.0, 1500.0]),
        'suspension': _cat(_base(), [10.5, 10.5, 10.7], [10.1, 10.5, 10.2],
                           [10.521, 10.5, 10.7214], [10.0798, 10.5, 10.1796],
                           [3000.0, 0.0, 1500.0]),
        'limit_up': _cat(_base(), [10.5, 11.55], [10.1, 11.0],
                         [10.521, 11.55], [10.0798, 10.9], [3000.0, 1500.0]),
    }
    for name, df in scenarios.items():
        _, _, recs = analyze(df)
        for r in recs:
            missing = [f for f in S.DAILY_FIELDS if f not in r]
            assert not missing, f'{name} 缺少输出字段: {missing}'
        _assert_no_nan_buy(recs, name)
