# -*- coding: utf-8 -*-
"""MSW-D 验证模块回归测试：参数扰动覆盖落地、日期归一化、基线口径。

对应 2026-10-21 全量运行暴露的三个缺陷：
  1. parameter_stability 用裸字段名构造覆盖 → 写进空节名 → 9 组扰动静默失效
     （全部跑出与子集基线逐位相同的结果，标准⑤形同虚设）
  2. matched_control 把 MSW 事件流限制到不相交的对照代码 → 0 交易 → 总收益 0.0
  3. _momentum_events / 指数过滤用带横杠日期与 YYYYMMDD 文本比较 →
     静默截断整个结束年份（2026 年数据全部丢失）
"""
import numpy as np
import pandas as pd
import pytest

from msw.backtest import apply_cfg_overrides
from msw.signal_engine import eval_c1
from msw.tests.conftest import make_feats
from msw.validation import PARAM_SECTION, _dig, _momentum_events


# ---------------------------------------------------------------- 桩

class StubSH:
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


# ---------------------------------------------------------------- 参数扰动覆盖

def test_param_grid_keys_all_mapped(cfg):
    """param_stability 网格里每个参数都必须有节映射且当前值可定位"""
    grid = cfg['validation']['param_stability']
    for key in grid:
        if key == 'cost_round_trip_bp':
            continue
        assert key in PARAM_SECTION, f'{key} 缺少节映射（覆盖会静默失效）'
        section = PARAM_SECTION[key]
        assert section in cfg, f'节 {section} 不在配置中'
        cur = _dig(cfg, f'{section}.{key}')
        assert cur is not None, f'{section}.{key} 当前值无法定位'
        assert cur in grid[key], '网格应包含基线值邻域'


def test_apply_cfg_overrides_lands_in_section(cfg):
    """覆盖必须落到配置节上（而非空节名），且原配置不被修改"""
    c = apply_cfg_overrides(cfg, {'signal_c1_pullback': {'pullback_min_pct': 0.04}},
                            logger=lambda m: None)
    assert c['signal_c1_pullback']['pullback_min_pct'] == 0.04
    assert cfg['signal_c1_pullback']['pullback_min_pct'] == 0.06
    assert '' not in c


def test_apply_cfg_overrides_warns_unknown_section(cfg):
    warns = []
    c = apply_cfg_overrides(cfg, {'no_such_section': {'x': 1}}, logger=warns.append)
    assert any('no_such_section' in w for w in warns)
    assert c['no_such_section'] == {'x': 1}


def test_override_changes_signal_behavior(cfg):
    """端到端：扰动 pullback_min_pct 后 C1 判定必须改变（覆盖真正生效）

    行数据只靠百分比分支通过（depth 10% ∈ [6%,18%]，atr_mult 0.6 ∉ [1.5,2.5]），
    因此 pullback_min_pct 覆盖到 0.90 后必须回落为 None。
    """
    row = _c1_row(atr20=2.0)                 # atr_mult = 1.2/2.0 = 0.6，出局
    status0, d0 = eval_c1(row, StubSH(stage_high=(12.0, 50)),
                          {'swing_count': 5}, True, cfg)
    assert status0 == 'buy'
    assert d0['depth'] == pytest.approx(0.10)
    assert d0['atr_mult'] == pytest.approx(0.6)
    over = apply_cfg_overrides(
        cfg, {'signal_c1_pullback': {'pullback_min_pct': 0.90}},
        logger=lambda m: None)
    status1, d1 = eval_c1(row, StubSH(stage_high=(12.0, 50)),
                          {'swing_count': 5}, True, over)
    assert status1 is None
    assert d1['depth'] < over['signal_c1_pullback']['pullback_min_pct']


def test_override_changes_zigzag_swings(cfg):
    """扰动 zigzag_pct 改变摆动段数量（阈值覆盖在波段层生效）"""
    from msw.swing_analyzer import SwingHistory
    feats = make_feats(n=600, cfg=cfg)
    sh0 = SwingHistory(feats, cfg)
    over = apply_cfg_overrides(cfg, {'swing': {'zigzag_pct': 0.30}},
                               logger=lambda m: None)
    sh1 = SwingHistory(feats, over)
    assert len(sh0.legs) > 0
    assert len(sh1.legs) < len(sh0.legs)


# ---------------------------------------------------------------- 匹配对照

def test_placebo_events_remap_to_control_codes():
    """安慰剂计时：事件序列移植到对照股代码，字段原样保留，原事件流不动"""
    from msw.validation import build_placebo_events
    pairs = {'A': 'X', 'B': 'Y'}
    events = {'A': [{'type': 'ENTRY', 'signal_date': '20240110', 'rule': 'C1',
                     'quality': 60.0, 'rr': 2.0, 'frac': 1.0},
                    {'type': 'EXIT', 'signal_date': '20240210', 'rule': 'C1',
                     'frac': 1.0, 'reason': 'tp2'}],
              'B': [{'type': 'ENTRY', 'signal_date': '20240310', 'rule': 'C3',
                     'quality': 55.0, 'rr': 1.8, 'frac': 1.0}]}
    p = build_placebo_events(pairs, events)
    assert set(p) == {'X', 'Y'}
    assert [e['signal_date'] for e in p['X']] == ['20240110', '20240210']
    assert [e['type'] for e in p['X']] == ['ENTRY', 'EXIT']
    assert p['X'][0]['rule'] == 'C1' and p['X'][0]['quality'] == 60.0
    assert p['Y'][0]['rule'] == 'C3'
    assert events['A'][0]['type'] == 'ENTRY'          # 原事件流不被修改


def test_placebo_events_skip_pairs_without_events():
    from msw.validation import build_placebo_events
    p = build_placebo_events({'A': 'X', 'C': 'Z'}, {'A': [{'type': 'ENTRY'}]})
    assert set(p) == {'X'}


# ---------------------------------------------------------------- 日期归一化

def test_momentum_events_cover_end_year():
    """动量基线调仓日必须覆盖到结束年份（YYYYMMDD vs 带横杠比较曾静默截断）"""

    class _P:
        pass

    dates = pd.bdate_range('2024-01-02', '2026-10-20').strftime('%Y%m%d').tolist()
    p = _P()
    p.dates = dates
    walk = {'ret120': {'A': (dates, np.linspace(0.0, 0.5, len(dates))),
                       'B': (dates, np.linspace(0.2, -0.1, len(dates)))}}
    evs = _momentum_events(walk, p, '2024-01-02', '2026-10-20', 1)
    assert evs, '事件流不应为空'
    all_dates = {e['signal_date'] for v in evs.values() for e in v}
    assert max(all_dates) == '20261020', '期末退出必须在最后一个交易日'
    assert any(d.startswith('2026') for d in all_dates), '必须覆盖 2026 年调仓'


def test_index_filter_keeps_end_year():
    """指数区间过滤：norm_date 后 2026 年数据不得被丢弃"""
    from msw.data_adapter import norm_date
    idx = pd.Series([1.0, 1.1, 1.2], index=['20240102', '20251231', '20261020'])
    lo, hi = norm_date('2024-01-02'), norm_date('2026-10-20')
    kept = idx[(idx.index >= lo) & (idx.index <= hi)]
    assert len(kept) == 3
    assert kept.index[-1] == '20261020'
    # 反证：不归一化时 2026 被静默丢弃（历史 bug 形态）
    dropped = idx[(idx.index >= '2024-01-02') & (idx.index <= '2026-10-20')]
    assert len(dropped) == 2
