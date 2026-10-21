# -*- coding: utf-8 -*-
"""无未来函数（PIT）测试 —— MSW 测试套件的核心。

任务书铁律：信号与统计只能使用「截至当日已确认」的信息。三类验证：
  A. 截断一致性：全量数据前 T 行 == 截断到 T 的数据（滚动窗口只回看）
  B. 未来变异不变性：篡改 T 之后的数据，T 及以前的输出逐位不变
  C. 恢复统计 PIT：修复事件必须发生在 T 及之前才计入；未修复需过 deadline
"""
import numpy as np
import pandas as pd
import pytest

from msw.feature_engine import compute_features
from msw.swing_analyzer import SwingHistory, build_swings
from msw.tests.conftest import make_bars, make_feats, walker_on

T = 600          # PIT 观测点（> start_idx=250 预热）
LEG_PIT_FIELDS = ('kind', 'start_bar', 'end_bar', 'start_price', 'end_price',
                  'ret', 'dur', 'confirm_bar', 'prev_high', 'lower_high')


# ---------------------------------------------------------------- A. 截断一致性

def test_features_truncation_consistency(cfg):
    """compute_features 为纯回看函数：截断序列的特征与全量序列前段逐位一致。"""
    full = make_feats(n=900, cfg=cfg)
    trunc = compute_features(make_bars(n=900).iloc[:T + 1].copy(), cfg)
    assert len(trunc) == T + 1
    for col in full.columns:
        if col == 'trade_date':
            continue
        a = full[col].iloc[:T + 1].to_numpy(dtype=float)
        b = trunc[col].to_numpy(dtype=float)
        np.testing.assert_array_equal(np.nan_to_num(a, nan=-9e9),
                                      np.nan_to_num(b, nan=-9e9),
                                      err_msg=f'列 {col} 截断后不一致')


def test_swing_legs_truncation_consistency(cfg):
    """截断后 legs_asof(T) 的 PIT 字段不变（修复类字段允许不同，见 C 组测试）。"""
    full_sh = SwingHistory(make_feats(n=900, cfg=cfg), cfg)
    trunc_sh = SwingHistory(make_feats(n=900, cfg=cfg).iloc[:T + 1], cfg)
    a, b = full_sh.legs_asof(T), trunc_sh.legs_asof(T)
    assert len(a) == len(b) and len(a) > 0
    for la, lb in zip(a, b):
        for f in LEG_PIT_FIELDS:
            assert la[f] == lb[f], f'leg 字段 {f} 截断后不一致'
    assert full_sh.pivots_asof(T) == trunc_sh.pivots_asof(T)
    assert full_sh.stage_high(T) == trunc_sh.stage_high(T)
    assert full_sh.prev_breakout_level(T) == trunc_sh.prev_breakout_level(T)


def test_walker_truncation_consistency(cfg, panel):
    """端到端 PIT：截断数据上行走的记录与全量数据同期记录完全一致。"""
    feats_full = make_feats(n=900, cfg=cfg)
    w_full, _ = walker_on(feats_full, cfg, panel)
    recs_full, evs_full = w_full.run()
    assert len(recs_full) > 0, '合成数据应产生日记录'

    feats_tr = feats_full.iloc[:T + 1].copy()
    w_tr, _ = walker_on(feats_tr, cfg, panel, end_idx=T)
    recs_tr, evs_tr = w_tr.run()
    assert len(recs_tr) == T - 250 + 1
    assert recs_tr == recs_full[:len(recs_tr)], '截断后日记录不一致（疑似未来数据泄漏）'

    # 信号事件：signal_date < T 的事件（成交在 <= T）应逐位一致
    key = lambda e: (e['signal_date'], e.get('rule', ''), e['fill_date'], e['fill_price'])
    ev_full = {key(e) for e in evs_full if e['signal_date'] < recs_tr[-1]['trade_date']}
    ev_tr = {key(e) for e in evs_tr if e['signal_date'] < recs_tr[-1]['trade_date']}
    assert ev_full == ev_tr, '截断后事件流不一致（疑似未来数据泄漏）'


# ---------------------------------------------------------------- B. 未来变异不变性

def _mutate_future(df_feats: pd.DataFrame, t: int) -> pd.DataFrame:
    """把 T 之后的所有价格/量能篡改为极端值（1.0），模拟最恶劣的未来信息泄漏。"""
    d = df_feats.copy()
    for c in ('hfq_open', 'hfq_high', 'hfq_low', 'hfq_close', 'open', 'high', 'low',
              'close', 'pre_close', 'pct_chg', 'amount', 'vol'):
        if c in d.columns:
            d.loc[d.index[t + 1:], c] = 1.0
    return d


def test_swing_stats_future_mutation(cfg):
    """篡改 T 之后的数据：stats(T) / legs_asof(T) / stage_high 不变。"""
    feats = make_feats(n=900, cfg=cfg)
    sh0 = SwingHistory(feats, cfg)
    sh1 = SwingHistory(_mutate_future(feats, T), cfg)
    s0, s1 = sh0.stats(T), sh1.stats(T)
    assert s0.keys() == s1.keys()
    for k in s0:
        v0, v1 = s0[k], s1[k]
        if v0 is None or (isinstance(v0, float) and not np.isfinite(v0)):
            assert v1 is None or (isinstance(v1, float) and not np.isfinite(v1)), k
        else:
            assert v0 == pytest.approx(v1, abs=1e-12), f'stats[{k}] 受未来数据影响'
    for f in LEG_PIT_FIELDS:
        assert [l[f] for l in sh0.legs_asof(T)] == [l[f] for l in sh1.legs_asof(T)]
    assert sh0.stage_high(T) == sh1.stage_high(T)
    assert sh0.prev_breakout_level(T) == sh1.prev_breakout_level(T)


def test_walker_future_mutation(cfg, panel):
    """端到端 PIT：篡改 T 之后的数据，T 及以前的信号/状态逐位不变。"""
    feats = make_feats(n=900, cfg=cfg)
    w0, _ = walker_on(feats, cfg, panel)
    recs0, evs0 = w0.run()

    w1, _ = walker_on(_mutate_future(feats, T), cfg, panel)
    recs1, evs1 = w1.run()

    n = T - 250 + 1
    assert recs1[:n] == recs0[:n], '未来数据变异改变了历史信号（未来函数泄漏）'
    cut = recs0[n - 1]['trade_date']          # T 日
    ev0 = {(e['signal_date'], e.get('rule', ''), e['fill_date'], e['fill_price'])
           for e in evs0 if e['signal_date'] < cut}
    ev1 = {(e['signal_date'], e.get('rule', ''), e['fill_date'], e['fill_price'])
           for e in evs1 if e['signal_date'] < cut}
    assert ev0 == ev1, '未来数据变异改变了历史事件（未来函数泄漏）'
    # T 日当天的信号本身（规则/日期）也不得改变（只允许成交价是 T+1 的）
    sig0 = {(e['signal_date'], e['rule']) for e in evs0 if e['signal_date'] == cut}
    sig1 = {(e['signal_date'], e['rule']) for e in evs1 if e['signal_date'] == cut}
    assert sig0 == sig1


def test_zigzag_confirm_bar_lag(cfg):
    """转折点确认滞后期 >= 1：confirm_bar 必须严格大于极值 bar。"""
    sh = SwingHistory(make_feats(n=900, cfg=cfg), cfg)
    piv = sh.pivots_asof(T)
    legs = sh.legs_asof(T)
    bars = {l['start_bar'] for l in legs} | {l['end_bar'] for l in legs}
    conf = {l['confirm_bar'] for l in legs}
    assert bars, '应存在已确认波段'
    assert min(conf) > min(bars), '确认 K 线必须晚于极值 K 线'
    for l in legs:
        assert l['confirm_bar'] > l['end_bar']


# ---------------------------------------------------------------- C. 恢复统计 PIT

def _controlled_bars(points, n, noise=0.004, seed=3):
    """由对数价格控制点构造日线（points: [(bar, price), ...] 对数线性插值）。"""
    rng = np.random.default_rng(seed)
    xs = np.array([p[0] for p in points], dtype=float)
    ys = np.log([p[1] for p in points])
    t = np.arange(n)
    close = np.exp(np.interp(t, xs, ys) + rng.normal(0, noise, n))
    prev = np.concatenate([[close[0]], close[:-1]])
    df = pd.DataFrame({
        'trade_date': pd.bdate_range('2023-01-03', periods=n).strftime('%Y%m%d'),
        'open': prev * (1 + rng.normal(0, 0.002, n)),
        'high': np.maximum(close, prev) * 1.004,
        'low': np.minimum(close, prev) * 0.996,
        'close': close, 'pre_close': prev,
        'pct_chg': close / prev - 1.0,
        'vol': 1e6, 'amount': 50_000_000.0,
        'total_mv': 1e10, 'circ_mv': 8e9, 'adj_factor': np.ones(n),
    })
    for c in ('open', 'high', 'low', 'close'):
        df[f'hfq_{c}'] = df[c]
    return df


def _down_leg(sh):
    downs = [l for l in sh.legs_asof(10**9) if l['kind'] == 'down']
    return downs[0]


def _cfg_deadline(cfg, days):
    c = dict(cfg)
    c['swing'] = dict(cfg['swing'], recovery_deadline_days=int(days))
    return c


# 回撤必须足够深：ZigZag 候选机制要求「确认高点后再跌 10% 才更新候选低点、
# 自候选低点反弹 10% 才确认低点」，故 12.5 → 9.0（-28%）才能形成 down 段。
RISE_FALL = [(0, 10.0), (60, 12.5), (90, 9.0), (130, 11.6)]


def test_recovery_stats_pit_success(cfg):
    """修复成功路径：recovery_bar <= T 才计入成功；之前一律 unresolved。"""
    c = _cfg_deadline(cfg, 120)                      # 放宽 deadline，保证修复在窗口内
    pts = RISE_FALL + [(200, 13.0), (400, 13.0)]
    feats = compute_features(_controlled_bars(pts, 500), c)
    sh = SwingHistory(feats, c)
    dl = _down_leg(sh)
    eb, rb = dl['end_bar'], dl['recovery_bar']
    assert rb is not None and rb > eb, '受控序列应在低点后修复到段前高点'

    before = sh.stats(rb - 1)
    assert before['recovery_unresolved'] >= 1
    assert before['recovery_rate'] is None or before['recovery_rate'] < 1.0
    after = sh.stats(rb)
    assert after['recovery_rate'] == 1.0
    assert after['recovery_days_median'] == pytest.approx(rb - eb)
    assert after['recovery_unresolved'] == 0


def test_recovery_stats_pit_failure(cfg):
    """修复失败路径：只有过了 deadline 才可计失败；此前必须 unresolved。"""
    pts = RISE_FALL + [(500, 11.6)]                  # 永不回到段前高点
    feats = compute_features(_controlled_bars(pts, 600), cfg)
    sh = SwingHistory(feats, cfg)
    dl = _down_leg(sh)
    eb = dl['end_bar']
    deadline = eb + int(cfg['swing']['recovery_deadline_days'])
    assert dl['recovery_bar'] is None, '受控序列不应修复到段前高点'

    mid = sh.stats(deadline - 1)
    assert mid['recovery_unresolved'] >= 1, 'deadline 前不得计失败'
    assert mid['recovery_rate'] is None
    late = sh.stats(deadline + 1)
    assert late['recovery_rate'] == 0.0
    assert late['recovery_unresolved'] == 0


def test_recovery_future_mutation(cfg):
    """T 之后才发生的修复不得被 T 日的统计知晓（恢复事件的 PIT）。"""
    c = _cfg_deadline(cfg, 120)
    pts = RISE_FALL + [(200, 13.0), (400, 13.0)]
    feats = compute_features(_controlled_bars(pts, 500), c)
    sh = SwingHistory(feats, c)
    dl = _down_leg(sh)
    rb = dl['recovery_bar']
    assert rb is not None
    # 在 rb 日截断：修复尚未发生（截断序列最大 bar = rb-1）
    sh_tr = SwingHistory(feats.iloc[:rb].copy(), c)
    dl_tr = _down_leg(sh_tr)
    assert dl_tr['recovery_bar'] is None, '截断数据不得提前知晓未来修复'
    s_full_at = sh.stats(rb - 1)
    s_tr_at = sh_tr.stats(rb - 1)
    for k in ('recovery_rate', 'recovery_unresolved', 'recovery_days_median'):
        v0, v1 = s_full_at[k], s_tr_at[k]
        assert (v0 is None) == (v1 is None) and (
            v0 is None or v0 == pytest.approx(v1, abs=1e-12)), k
