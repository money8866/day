# -*- coding: utf-8 -*-
"""§43 HVE-BULL 路径测试（§12 / §13 / §14，对应 G4）"""
from hve_v1 import load_config
from hve_v1 import signals as S
from tests.synth import make_df, analyze, by_date

CFG = load_config()


def _hve_then(extra_close, extra_open, extra_vol):
    """60 天横盘 + 下标 60 的 HVE，再追加后续 K 线"""
    close = [10.0] * 60 + [10.5]
    open_ = [10.0] * 60 + [10.1]
    vol = [1000.0] * 60 + [3000.0]
    close += list(extra_close)
    open_ += list(extra_open)
    vol += list(extra_vol)
    return make_df(close, open_, vol=vol)


def test_bull_buy_on_reexpansion():
    """§14：Close>前3日高 + Volume>=1.2×MA20Vol + 阳线 + CLV>=0.65 → HVE_BULL BUY"""
    d = _hve_then([10.7], [10.2], [1500.0])
    d, _, recs = analyze(d)
    r = by_date(recs, str(d['trade_date'].iloc[61]))
    assert r is not None
    assert r['signal_type'] == S.HVE_BULL
    assert r['state'] == S.S_BULL
    assert r['signal'] == S.SIG_BUY
    assert r['reason'] == S.REASON_BULL
    assert r['executable'] is True
    assert r['tradability_flag'] == 'LIQUID'
    assert r['days_since_hve'] == 1
    assert r['ma20_up'] is True
    assert abs(r['entry_price'] - 10.7) < 1e-9                      # §29 entry = 当日收盘
    assert abs(r['trigger_price'] - float(d['high_prev3'].iloc[61])) < 1e-9


def test_bull_watch_before_reexpansion():
    """§22：有效 HVE 且结构未破，但未突破前3日高 → HVE_BULL_WATCH / WATCH"""
    d = _hve_then([10.4], [10.2], [1500.0])
    d, _, recs = analyze(d)
    r = by_date(recs, str(d['trade_date'].iloc[61]))
    assert float(d['close'].iloc[61]) <= float(d['high_prev3'].iloc[61])
    assert r['state'] == S.S_BULL_WATCH
    assert r['signal_type'] == S.HVE_WATCH
    assert r['signal'] == S.SIG_WATCH
    assert r['reason'] == S.REASON_WATCH


def test_bull_requires_positive_ma20_slope():
    """§12：MA20 必须向上 —— MA20 向下时即便价格站上 MA20 也不给 HVE_BULL"""
    close = [12.0 - 2.0 * k / 59.0 for k in range(60)]
    open_ = [close[0]] + close[:-1]
    vol = [1000.0] * 60
    close += [10.5, 10.55]          # 下标 60 = HVE，下标 61 = 观察日
    open_ += [10.0, 10.5]
    vol += [3000.0, 1400.0]
    d = make_df(close, open_, vol=vol)
    d, _, recs = analyze(d)
    r = by_date(recs, str(d['trade_date'].iloc[61]))
    assert r is not None and r['days_since_hve'] == 1
    assert r['ma20_up'] is False
    assert float(d['close'].iloc[61]) > float(d['ma20'].iloc[61])   # 价格仍在 MA20 上方
    assert r['state'] == S.S_DIGESTION
    assert r['signal'] == S.SIG_WATCH
    assert r['signal_type'] != S.HVE_BULL
