# -*- coding: utf-8 -*-
"""§43 HVE 事件定义与聚簇测试（对应 G3 / G8）"""
import numpy as np

from hve_v1 import load_config
from hve_v1.event import hve_mask, cluster_indices
from tests.synth import make_df, analyze, by_date

CFG = load_config()


def _base_with_hve(second_gap=None, second_vol=4000.0):
    """60 天横盘 + 第 60 天（下标 60）一根标志性高量阳线；可选在第 2 根 HVE。

    第 2 根 HVE 的量默认取 4000（> 第 1 根的 3000），使「簇内最大量日」唯一。
    """
    close = [10.0] * 60 + [10.5]
    open_ = [10.0] * 60 + [10.1]
    vol = [1000.0] * 60 + [3000.0]
    if second_gap is not None:
        # 第 2 根 HVE 出现在下标 60+second_gap
        for _ in range(second_gap - 1):
            close.append(10.5); open_.append(10.5); vol.append(1000.0)
        close.append(11.0); open_.append(10.6); vol.append(second_vol)
    return make_df(close, open_, vol=vol)


def test_hve_basic_conditions():
    """§6 四条基础条件同时满足才产生事件"""
    d, ev, _ = analyze(_base_with_hve())
    assert bool(d['hve_flag'].iloc[60]) is True
    assert abs(float(d['vr20'].iloc[60]) - 3.0) < 1e-6      # 3000 / 1000
    assert float(d['daily_return'].iloc[60]) > 0.02
    assert float(d['close'].iloc[60]) > float(d['open'].iloc[60])
    assert float(d['clv'].iloc[60]) >= CFG['clv_min']
    assert len(ev) == 1 and ev[0]['primary_idx'] == 60


def test_vr20_excludes_today():
    """§6 前 20 日均量不含当日（否则 3000 会被自身抬高，比值必然 < 2）"""
    d, _, _ = analyze(_base_with_hve())
    vma = float(d['vol_ma20'].iloc[60])
    assert abs(vma - 1000.0) < 1e-9, 'vol_ma20 必须是「前 20 日」均量，不含当日'


def test_hve_day_is_never_buy():
    """§15 / G3：HVE 当日只观察，绝不 BUY"""
    _, _, recs = analyze(_base_with_hve())
    r = by_date(recs, recs[0]['trade_date'])
    assert r['state'] == 'HVE_EVENT'
    assert r['signal_type'] == 'HVE_WATCH'
    assert r['signal'] == 'WATCH'
    assert all(not (x['days_since_hve'] == 0 and x['signal'] == 'BUY') for x in recs)


def test_cluster_dedup_gap_le_5():
    """§9 / G8：间隔 <=5 个交易日 → 同一簇，且 primary = 簇内第一次"""
    d, ev, _ = analyze(_base_with_hve(second_gap=3))
    hve_n = int(d['hve_flag'].sum())
    assert hve_n == 2
    assert len(ev) == 1, 'gap<=5 应合并为 1 个 Event Cluster'
    assert ev[0]['primary_idx'] == 60
    assert len(ev[0]['hve_idx']) == 2
    assert bool(d['is_primary'].iloc[60]) is True
    assert str(d['event_date'].iloc[63]) == str(d['trade_date'].iloc[60])


def test_cluster_split_gap_gt_5():
    """§9：间隔 >5 个交易日 → 两个独立簇"""
    d, ev, _ = analyze(_base_with_hve(second_gap=6))
    assert len(ev) == 2
    assert [e['primary_idx'] for e in ev] == [60, 66]


def test_cluster_keeps_max_volume_meta():
    """§9：保留 cluster_max_volume_date / cluster_max_volume_ratio 供研究"""
    d, ev, _ = analyze(_base_with_hve(second_gap=3))
    e = ev[0]
    assert e['cluster_max_volume_date'] == str(d['trade_date'].iloc[63])
    assert np.isfinite(e['cluster_max_volume_ratio'])
    assert e['cluster_max_volume_ratio'] > 2.0


def test_high_eq_low_no_event():
    """§6：high == low → CLV 缺失 → 不产生 HVE（§45 边界）"""
    close = [10.0] * 60
    open_ = [10.0] * 60
    vol = [1000.0] * 60
    close.append(10.5); open_.append(10.1); vol.append(3000.0)
    df = make_df(close, open_, high=[10.5] * 61, low=[10.5] * 61, vol=vol)
    _, ev, recs = analyze(df)
    assert len(ev) == 0
    assert recs == []


def test_volume_zero_no_event():
    """§45：volume == 0 → vr20 = 0 → 不产生 HVE"""
    close = [10.0] * 60 + [10.5]
    open_ = [10.0] * 60 + [10.1]
    vol = [1000.0] * 60 + [0.0]
    d, ev, _ = analyze(make_df(close, open_, vol=vol))
    assert len(ev) == 0


def test_cluster_indices_pure():
    """cluster_indices 纯函数行为"""
    assert cluster_indices([1, 3, 6, 12], 5) == [[1, 3, 6], [12]]
    assert cluster_indices([1, 7], 5) == [[1], [7]]
    assert cluster_indices([], 5) == []


def test_distance_to_high_uses_only_past():
    """§10：距离前高只能使用事件日之前的数据"""
    d, _, _ = analyze(_base_with_hve())
    hp20 = float(d['high'].iloc[40:60].max())
    assert abs(float(d['distance_to_20d_high'].iloc[60]) - (10.5 / hp20 - 1.0)) < 1e-9
