# -*- coding: utf-8 -*-
"""溢价档×状态 期望收益查表（V3 期望分层模块）

突破日决策时点只用两个当时已知的特征：
  prem:   突破日收盘相对天量日最高价的溢价(%)   （回测事件列 breakout_pct_above_t0_high）
  state:  决策时点 V3 状态（classify_v3 输出）   （回测事件列 v3_state）

在滚动训练窗口内统计各分组的 t+120 前瞻收益均值，三级回退查表：
  L1 cross(prem档, state)  样本 >= min_n
  L2 state 边际            样本 >= min_n
  L3 prem 档边际           样本 >= min_n（不满足则视为无信息）
未命中 -> score=None，调用方按"规则不表态"处理。

零裁量：分档边界 / min_n / 层级顺序全部参数化；训练与打分均为确定性纯函数，
lookup 为可 JSON 序列化的嵌套 dict，便于落盘审计与运行时加载。
"""

import numpy as np
import pandas as pd

TIER_EDGES = (1.0, 5.0, 10.0)  # 溢价档边界: <1% / 1-5% / 5-10% / >10%
MIN_N = 30                     # 分组最小样本（纪律下限 30，偏好 100+）
TIER_LABELS = ('<1%', '1-5%', '5-10%', '>10%')


def tier_of(prem, tier_edges=TIER_EDGES):
    """溢价(%) -> 分档标签；无效输入返回 None。"""
    try:
        p = float(prem)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(p):
        return None
    e1, e2, e3 = tier_edges
    if p < e1:
        return '<1%'
    if p < e2:
        return '1-5%'
    if p < e3:
        return '5-10%'
    return '>10%'


def _agg(s: pd.Series):
    s = pd.to_numeric(s, errors='coerce').dropna()
    if s.empty:
        return None
    return (int(s.size), round(float(s.mean()), 2), round(float((s > 0).mean() * 100), 1))


def train_lookup(df: pd.DataFrame, tier_edges=TIER_EDGES, min_n=MIN_N,
                 y_col='r120_brk', prem_col='prem', state_col='state') -> dict:
    """从训练窗口事件构建三级查表。

    Args:
        df: 事件 DataFrame，需含 prem_col / state_col / y_col 三列。
        tier_edges: 溢价档边界（升序三元组）。
        min_n: 分组最小样本数。
        y_col: 前瞻收益列名（%）。brk 锚用 r120_brk，t0 锚用 r120_fwd。

    Returns:
        {'tier_edges', 'min_n', 'y_col', 'n',
         'cross': {(tier,state): (n,mean,win)}, 'state': {...}, 'pct': {...}}
    """
    x = df[[prem_col, state_col, y_col]].copy()
    x.columns = ['prem', 'state', 'y']
    x['tier'] = x['prem'].apply(lambda p: tier_of(p, tier_edges))
    x = x.dropna(subset=['y'])
    out = {'tier_edges': list(tier_edges), 'min_n': int(min_n), 'y_col': y_col,
           'n': int(len(x)), 'cross': {}, 'state': {}, 'pct': {}}
    if x.empty:
        return out
    for (tier, state), g in x.dropna(subset=['tier']).groupby(['tier', 'state'], observed=True):
        a = _agg(g['y'])
        if a:
            out['cross'][(str(tier), str(state))] = a
    for state, g in x.dropna(subset=['state']).groupby('state', observed=True):
        a = _agg(g['y'])
        if a:
            out['state'][str(state)] = a
    for tier, g in x.dropna(subset=['tier']).groupby('tier', observed=True):
        a = _agg(g['y'])
        if a:
            out['pct'][str(tier)] = a
    return out


def score(prem, state, lookup: dict):
    """零裁量打分：三级回退查表。

    Returns:
        (mean, hit)：mean=float 命中组均值(%)，hit 如 'cross >10%|PRIMARY_BUY' /
        'state PRIMARY_BUY' / 'pct 5-10%'；无命中返回 (None, None)。
    """
    tier = tier_of(prem, lookup.get('tier_edges', TIER_EDGES))
    min_n = lookup.get('min_n', MIN_N)
    state_k = str(state) if state is not None and str(state) not in ('', 'nan', 'None') else None
    if tier and state_k:
        rec = lookup.get('cross', {}).get((tier, state_k))
        if rec and rec[0] >= min_n:
            return rec[1], f'cross {tier}|{state_k}'
    if state_k:
        rec = lookup.get('state', {}).get(state_k)
        if rec and rec[0] >= min_n:
            return rec[1], f'state {state_k}'
    if tier:
        rec = lookup.get('pct', {}).get(tier)
        if rec and rec[0] >= min_n:
            return rec[1], f'pct {tier}'
    return None, None
