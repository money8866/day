# -*- coding: utf-8 -*-
"""§44 / §37.13 未来函数测试（future_data_mutation，对应 G7 / G9）

铁律：修改 T+1 及之后的数据，不得改变 T 及之前已经生成的 HVE / BUY 信号。
"""
import numpy as np

from hve_v1 import load_config
from hve_v1.indicators import compute_indicators
from tests.synth import make_df, analyze, recs_equal

CFG = load_config()

_IND_COLS = ('vol_ma20', 'amount_ma20', 'vr20', 'clv', 'daily_return',
             'ma20', 'ma60', 'ma20_prev5', 'atr20',
             'high_prev3', 'high_prev20', 'high_prev60')


def _series(n_after=12):
    close = [10.0] * 60 + [10.5, 10.7]
    open_ = [10.0] * 60 + [10.1, 10.2]
    vol = [1000.0] * 60 + [3000.0, 1500.0]
    for j in range(n_after):
        close.append(10.7 + 0.05 * (j + 1))
        open_.append(10.7)
        vol.append(1100.0)
    return make_df(close, open_, vol=vol)


def test_future_data_mutation_does_not_change_past_records():
    """§44：把 T 之后的数据改得面目全非，T 及之前的记录必须逐字段不变"""
    df = _series()
    d0, _, recs0 = analyze(df)
    t = 61
    cutoff = str(d0['trade_date'].iloc[t])
    tail = df.index[t + 1:]

    m = df.copy()
    m.loc[tail, 'close'] = 3.0
    m.loc[tail, 'open'] = 5.0
    m.loc[tail, 'high'] = 9.0
    m.loc[tail, 'low'] = 1.0
    m.loc[tail, 'vol'] = 9.0e9
    _, _, recs1 = analyze(m)

    past0 = [r for r in recs0 if r['trade_date'] <= cutoff]
    past1 = [r for r in recs1 if r['trade_date'] <= cutoff]
    assert past0, '截断日之前应存在记录'
    assert recs_equal(past0, past1), '修改未来数据改变了过去的 HVE / BUY 信号'


def test_indicators_are_point_in_time():
    """指标层 PIT：截断 DataFrame 与全量 DataFrame 在重叠区间逐列完全一致"""
    df = _series()
    t = 65
    full = compute_indicators(df, CFG)
    trunc = compute_indicators(df.iloc[:t + 1].copy(), CFG)
    for c in _IND_COLS:
        a = full[c].iloc[:t + 1].to_numpy(dtype=float)
        b = trunc[c].to_numpy(dtype=float)
        assert np.allclose(a, b, equal_nan=True), f'{c} 使用了未来数据'


def test_volume_decay_uses_only_post_hve_volume():
    """§18：volume_decay_5d 只由 HVE 后 T+1..T+5 的量决定，与更晚的数据无关"""
    df = _series()
    d0 = compute_indicators(df, CFG)
    m = df.copy()
    m.loc[m.index[70:], 'vol'] = 1e12
    d1 = compute_indicators(m, CFG)

    from hve_v1.event import volume_decay_5d
    assert volume_decay_5d(d0, 60, 5) == volume_decay_5d(d1, 60, 5)
