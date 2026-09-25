# -*- coding: utf-8 -*-
"""Post-FirstBoard 可交易量价 Alpha V2.0 · 特征矩阵构建

纪律
  * 只读 panel.parquet / basic.parquet / first_board_events.parquet，不修改任何现有模块
  * 所有特征只用「Entry Time 之前已完整可见」的信息
  * 不预设方向（量能大/小、涨/跌、突破/回踩 一律作为候选变量交给实验层）

输出
  out/pfb_mats.npz   面板增强矩阵（均线/滚动高低点/ATR/波动/换手均线/市场宽度/行业收益）
  out/pfb_base.parquet  每事件一行的 T0 特征 + 维度标签 + 分期
"""
import os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
KMAX = 25
BENCH = '000852.SH'

PCOLS = ['ts_code', 'trade_date', 'seq', 'board', 'open', 'high', 'low', 'close',
         'pre_close', 'pct_chg', 'vol', 'amount', 'adjf',
         'a_open', 'a_high', 'a_low', 'a_close',
         'limit_price', 'limit_pct', 'is_limit_up', 'one_word', 'opened_board']


def main():
    print('读取 panel ...')
    panel = pd.read_parquet(os.path.join(OUT, 'panel.parquet'), columns=PCOLS)
    panel = panel.sort_values(['ts_code', 'trade_date'], kind='mergesort').reset_index(drop=True)
    print('  行数', len(panel), '| 股票', panel['ts_code'].nunique())

    basic = pd.read_parquet(os.path.join(OUT, 'basic.parquet'))
    ind_map = basic.set_index('ts_code')['industry'].to_dict()
    panel['industry'] = panel['ts_code'].astype(str).map(ind_map).fillna('NA')

    codes, cid = np.unique(panel['ts_code'].astype(str).values, return_inverse=True)
    cid = cid.astype(np.int64)
    dt = panel['trade_date'].values.astype(np.int64)
    nrows = len(panel)

    # ── 面板级滚动特征（全部只用截至当日信息） ──
    print('面板滚动特征 ...')
    g = panel.groupby('ts_code', sort=False, observed=True)
    ac = panel['a_close']
    ah = panel['a_high']
    al = panel['a_low']
    pc = g['a_close'].shift(1)

    tr = np.maximum.reduce([
        (ah - al).values,
        (ah - pc).abs().values,
        (al - pc).abs().values])
    panel['_tr'] = tr
    panel['_dret'] = ac.values / pc.values - 1.0

    roll = {}
    for w in (5, 10, 20, 60):
        roll['ma%d' % w] = g['a_close'].transform(lambda s, w=w: s.rolling(w, min_periods=w).mean())
    roll['hi20'] = g['a_high'].transform(lambda s: s.rolling(20, min_periods=20).max())
    roll['lo20'] = g['a_low'].transform(lambda s: s.rolling(20, min_periods=20).min())
    roll['atr14'] = panel.groupby('ts_code', sort=False, observed=True)['_tr'].transform(
        lambda s: s.rolling(14, min_periods=14).mean())
    roll['rvol20'] = panel.groupby('ts_code', sort=False, observed=True)['_dret'].transform(
        lambda s: s.rolling(20, min_periods=20).std())
    roll['vma5'] = g['vol'].transform(lambda s: s.rolling(5, min_periods=5).mean())
    roll['vma10'] = g['vol'].transform(lambda s: s.rolling(10, min_periods=10).mean())
    roll['vma20'] = g['vol'].transform(lambda s: s.rolling(20, min_periods=20).mean())
    roll['ama5p'] = g['a_close'].transform(lambda s: s.shift(5))
    for nm in list(roll):
        roll[nm] = roll[nm].values.astype(np.float32)
    print('  ok')

    # ── 市场宽度（全市场，Entry 前可见） ──
    print('市场宽度 ...')
    p = panel[['trade_date', 'pct_chg', 'limit_pct', 'is_limit_up', 'opened_board']].copy()
    p['up'] = (p['pct_chg'] > 0).astype(np.int32)
    p['dn'] = (p['pct_chg'] < 0).astype(np.int32)
    p['ldn'] = (p['pct_chg'] <= -(p['limit_pct'].fillna(10.0) - 0.3)).astype(np.int32)
    b = p.groupby('trade_date').agg(
        br_adv=('up', 'sum'), br_dec=('dn', 'sum'),
        br_lu=('is_limit_up', 'sum'), br_ld=('ldn', 'sum'),
        br_mkt=('pct_chg', 'mean')).reset_index()
    br = p.groupby('trade_date').apply(
        lambda d: pd.Series({
            'br_broken': d.loc[d['is_limit_up'] > 0, 'opened_board'].mean()}),
        include_groups=False).reset_index()
    b = b.merge(br, on='trade_date', how='left')
    b['br_brokenrate'] = b['br_broken'].fillna(0.0)
    # 昨日涨停股今日表现
    p2 = panel[['ts_code', 'trade_date', 'pct_chg', 'is_limit_up']].copy()
    p2['prev_lu'] = p2.groupby('ts_code', sort=False)['is_limit_up'].shift(1)
    y = p2[p2['prev_lu'] == 1].groupby('trade_date')['pct_chg'].mean().reset_index()
    y.columns = ['trade_date', 'br_prevlu']
    b = b.merge(y, on='trade_date', how='left')
    b['br_net'] = (b['br_adv'] - b['br_dec']) / np.maximum(1, b['br_adv'] + b['br_dec'])
    bcols = ['br_adv', 'br_dec', 'br_lu', 'br_ld', 'br_mkt', 'br_brokenrate', 'br_prevlu', 'br_net']
    print(b[bcols].describe().to_string())

    # ── 行业当日平均收益（剔除自身） ──
    print('行业收益 ...')
    panel['_dret'] = panel['_dret'].astype(np.float32)
    ig = panel.groupby(['trade_date', 'industry'])['_dret'].agg(['sum', 'count']).reset_index()
    ig.columns = ['trade_date', 'industry', 'isum', 'icnt']
    panel = panel.merge(ig, on=['trade_date', 'industry'], how='left')
    panel['ind_ret'] = np.where(
        panel['icnt'] > 1,
        (panel['isum'].values - panel['_dret'].values) / np.maximum(1, panel['icnt'].values - 1),
        np.nan).astype(np.float32)

    # ── 事件窗口映射 ──
    print('事件窗口映射 ...')
    key = cid * 100000000 + dt
    fb = pd.read_parquet(os.path.join(OUT, 'first_board_events.parquet'))
    e_cid = pd.Index(codes).get_indexer(fb['ts_code'].astype(str).values)
    ok = e_cid >= 0
    fb = fb[ok].reset_index(drop=True)
    e_cid = e_cid[ok]
    ekey = e_cid * 100000000 + fb['trade_date'].values.astype(np.int64)
    pos = np.searchsorted(key, ekey)
    hit = (pos < nrows) & (key[np.minimum(pos, nrows - 1)] == ekey)
    print('  锚点', int(hit.sum()), '/', len(fb))
    fb = fb[hit].reset_index(drop=True)
    e_cid = e_cid[hit]
    pos = pos[hit]
    n = len(fb)

    off = np.arange(KMAX + 1)
    rows = pos[:, None] + off[None, :]
    inb = rows < nrows
    rr = np.clip(rows, 0, nrows - 1)
    same = inb & (cid[rr] == e_cid[:, None])
    rows = np.where(same, rows, 0)

    dtt = np.where(same, dt[rows], -1).astype(np.int64)
    pday = pd.to_datetime(panel['trade_date'].astype(str), format='%Y%m%d').values \
        .astype('datetime64[D]').astype(np.int64)
    dnum = np.where(same, pday[rows], -1).astype(np.int64)
    gap = np.full((n, KMAX + 1), np.nan, dtype=np.float32)
    with np.errstate(all='ignore'):
        gap[:, 1:] = np.where((dnum[:, 1:] >= 0) & (dnum[:, :-1] >= 0),
                              dnum[:, 1:] - dnum[:, :-1], np.nan)
    clean = ~(np.nan_to_num(gap, nan=0.0) > 10).any(axis=1)

    def take(vals, dtype=np.float32):
        return np.where(same, vals[rr].astype(dtype), np.nan).astype(dtype)

    def map_date(dmap, col, dtype=np.float32):
        v = np.where(same, pd.Series(dtt.ravel()).map(dmap).values.reshape(n, KMAX + 1).astype(np.float64), np.nan)
        return v.astype(dtype)

    M = {}
    for nm in ('ma5', 'ma10', 'ma20', 'ma60', 'hi20', 'lo20', 'atr14', 'rvol20',
               'vma5', 'vma10', 'vma20', 'ama5p'):
        M[nm] = take(roll[nm])
    M['ind_ret'] = take(panel['ind_ret'].values)
    for c in ('br_adv', 'br_dec', 'br_lu', 'br_ld', 'br_mkt', 'br_brokenrate', 'br_prevlu', 'br_net'):
        M[c] = map_date(b.set_index('trade_date')[c].to_dict(), c)

    # 首板日前收盘价（用于振幅/缺口的分母）
    M['pc0a'] = np.where(same, pc.values[rr].astype(np.float32), np.nan).astype(np.float32)

    np.savez_compressed(os.path.join(OUT, 'pfb_extra.npz'), **M)
    print('已写 out/pfb_extra.npz', {k: M[k].shape for k in list(M)[:3]})

    base = pd.DataFrame({
        'event_id': ['PFB%06d' % i for i in range(n)],
        'ts_code': fb['ts_code'].astype(str).values,
        'name': fb['name'].values,
        'industry': fb['industry'].values,
        'board': fb['board'].astype(str).values,
        'trade_date': fb['trade_date'].values.astype(np.int64),
        't0_date': dtt[:, 0],
        'seq': fb['seq'] if 'seq' in fb else np.nan,
        'clean': clean,
        'gap3': gap[:, 3],
    })
    tds = np.sort(panel['trade_date'].unique())
    ld = pd.to_numeric(fb['list_date'].astype(str).str[:8], errors='coerce').fillna(20210101).values.astype(np.int64)
    base['listed_days'] = np.searchsorted(tds, fb['trade_date'].values) - np.searchsorted(tds, ld)
    base['yr'] = (base['trade_date'] // 10000).astype(int)
    base['period'] = np.where(base['yr'] <= 2023, 'TRAIN',
                              np.where(base['yr'] <= 2025, 'VALID', 'OOS'))
    base.to_parquet(os.path.join(OUT, 'pfb_base.parquet'), index=False)
    print('已写 out/pfb_base.parquet', len(base))
    print(base.groupby(['period', 'yr']).size().to_string())


if __name__ == '__main__':
    main()
