# -*- coding: utf-8 -*-
"""§43 HVE-2ND 路径测试（§16~§21，对应 G5）

注意 §18 术语：缩量只叫 Volume Contraction，不得解释为"卖压减少"。
"""
import numpy as np

from hve_v1 import load_config
from hve_v1 import signals as S
from hve_v1.event import volume_decay_5d
from hve_v1.indicators import compute_indicators
from tests.synth import make_df, analyze, by_date

CFG = load_config()

# HVE 之后的 5 个整理日（收 / 开）
_CONS = [(10.25, 10.40), (10.30, 10.20), (10.20, 10.30), (10.28, 10.20), (10.25, 10.30)]


def _digest_base(brk_close=10.6, brk_open=10.3):
    """60 天横盘 → 下标60 HVE → 61~65 缩量整理 → 66 突破日"""
    close = [10.0] * 60 + [10.5]
    open_ = [10.0] * 60 + [10.1]
    vol = [1000.0] * 60 + [3000.0]
    for c_, o_ in _CONS:
        close.append(c_)
        open_.append(o_)
        vol.append(800.0)
    close.append(brk_close)
    open_.append(brk_open)
    vol.append(1500.0)
    return make_df(close, open_, vol=vol)


def test_2nd_buy_after_volume_contraction_and_breakout():
    """§21：整理≥3日 + Volume Contraction<=0.60 + Close>consolidation_high + 量能 → HVE_2ND BUY"""
    d = _digest_base()
    _, ev, recs = analyze(d)
    r = by_date(recs, str(d['trade_date'].iloc[66]))
    assert r is not None
    assert abs(ev[0]['volume_decay_5d'] - 800.0 / 3000.0) < 1e-9
    assert r['state'] == S.S_2ND
    assert r['signal_type'] == S.HVE_2ND
    assert r['signal'] == S.SIG_BUY
    assert r['reason'] == S.REASON_2ND
    assert r['days_since_hve'] == 6
    assert r['volume_decay_5d'] <= CFG['volume_decay_max']
    cons_high = float(d['high'].iloc[60:66].max())      # §20 不含当前突破日
    assert abs(r['consolidation_high'] - cons_high) < 1e-9
    assert abs(r['trigger_price'] - cons_high) < 1e-9
    assert r['invalid_rule'] == 'TRIGGER_PRICE'
    assert abs(r['invalid_price'] - r['trigger_price']) < 1e-9


def test_2nd_watch_before_breakout():
    """§22：缩量整理达标但未突破整理区高点 → DIGESTION / WATCH"""
    d = _digest_base(brk_close=10.4, brk_open=10.3)
    _, _, recs = analyze(d)
    r = by_date(recs, str(d['trade_date'].iloc[66]))
    assert float(d['close'].iloc[66]) < float(d['high'].iloc[60:66].max())
    assert r['state'] == S.S_DIGESTION
    assert r['signal_type'] == S.HVE_WATCH
    assert r['signal'] == S.SIG_WATCH


def test_volume_decay_missing_when_window_incomplete():
    """§18：HVE 后不足 5 个交易日 → volume_decay_5d 缺失，且不得产生 HVE_2ND"""
    close = [10.0] * 60 + [10.5, 10.4, 10.3]
    open_ = [10.0] * 60 + [10.1, 10.5, 10.4]
    vol = [1000.0] * 60 + [3000.0, 800.0, 800.0]
    df = make_df(close, open_, vol=vol)
    d = compute_indicators(df, CFG)
    assert np.isnan(volume_decay_5d(d, 60, 5))
    _, _, recs = analyze(df)
    assert recs
    assert all(not np.isfinite(r['volume_decay_5d']) for r in recs)
    assert all(r['signal_type'] != S.HVE_2ND for r in recs)


def test_2nd_not_before_min_digest_days():
    """§19：整理不足 3 个交易日不得产生 HVE_2ND（用 volume_decay_window=2 让该分支可达）"""
    cfg = dict(CFG)
    cfg['volume_decay_window'] = 2
    close = [10.0] * 60 + [10.5, 10.3, 10.6, 10.7]
    open_ = [10.0] * 60 + [10.1, 10.4, 10.3, 10.4]
    vol = [1000.0] * 60 + [3000.0, 1500.0, 1500.0, 1500.0]
    d = make_df(close, open_, vol=vol)
    _, _, recs = analyze(d, cfg=cfg)

    r62 = by_date(recs, str(d['trade_date'].iloc[62]))
    assert r62['days_since_hve'] == 2 < cfg['min_digest_days']
    assert float(d['close'].iloc[62]) > float(d['high'].iloc[60:62].max())  # 突破形态已成立
    assert r62['signal'] == S.SIG_WATCH and r62['state'] == S.S_DIGESTION

    r63 = by_date(recs, str(d['trade_date'].iloc[63]))
    assert r63['days_since_hve'] == 3
    assert r63['signal_type'] == S.HVE_2ND and r63['signal'] == S.SIG_BUY
