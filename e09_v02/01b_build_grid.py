# -*- coding: utf-8 -*-
"""
E09-V02 Step 1b：参数稳定性专用事件集（启动日取最宽口径，使全部网格点为子集）

spec §25 要求测试 启动涨幅 2.5/3.0/3.5/4.0 与 启动量比 1.3/1.5/1.8/2.0。
主事件集 S 定义为 3.0%/1.5x，无法测试"放宽"方向（2.5%/1.3x），
故另建超集：pct_chg>=2.5 & vol/MA20>=1.3 & close>ma20，L∈{2,3,4,5}。
全部网格点通过对该超集加过滤得到，不做任何重新定义。
"""
import os
import sqlite3
import numpy as np
import pandas as pd
import e09_lib as E

W = E.W
LGRID = [2, 3, 4, 5]
RET_LO, VR_LO = 2.5, 1.3


def main():
    print('载入面板 ...', flush=True)
    p = pd.read_parquet(E.PANEL, columns=['ts_code', 'trade_date', 'td_idx', 'open', 'high',
                                          'low', 'close', 'pct_chg', 'vol', 'amount',
                                          'ma20', 'vma20', 'is_st', 'obs_days'])
    p['_c'] = pd.factorize(p['ts_code'], sort=False)[0]
    E.add_roll(p, 'amount', 20, 'mean', 0, 20, 'amt20')
    p['vol_ma20r'] = p['vol'] / p['vma20']
    bad = ((p['vol'].values <= 0) | (p['close'].values <= 0) | ~np.isfinite(p['pct_chg'].values))
    uni = ((np.nan_to_num(p['is_st'].values) == 0) &
           (np.nan_to_num(p['obs_days'].values) >= E.MIN_OBS) &
           np.isfinite(p['ma20'].values) & np.isfinite(p['vma20'].values) & ~bad)
    S = uni & (p['pct_chg'].values >= RET_LO) & (p['vol_ma20r'].values >= VR_LO) & \
        (p['close'].values > p['ma20'].values)
    rows = np.flatnonzero(S)
    n = len(rows)
    print(f'  超集启动日 {n:,}', flush=True)

    N = len(p)
    cats = pd.Categorical(p['ts_code'].values)
    code_i = cats.codes
    ev_code = code_i[rows]
    off = np.arange(1, W + 1)
    idx = np.clip(rows[:, None] + off[None, :], 0, N - 1)
    same = (code_i[idx] == ev_code[:, None])
    M = {}
    for cname in ('close', 'high', 'low', 'open', 'vol', 'vma20'):
        v = p[cname].values.astype(np.float32)
        M[cname] = np.where(same, v[idx], np.nan).astype(np.float32)
    td_m = np.where(same, p['td_idx'].values.astype(np.float32)[idx], np.nan).astype(np.float32)
    del idx, same

    close_m = M['close'].astype(np.float64)
    high_m = M['high'].astype(np.float64)
    low_m = M['low'].astype(np.float64)
    open_m = M['open'].astype(np.float64)
    vol_m = M['vol'].astype(np.float64)
    vma_m = M['vma20'].astype(np.float64)

    ev = p.iloc[rows][['ts_code', 'trade_date', 'td_idx', 'close', 'high', 'low',
                       'pct_chg', 'vol', 'vol_ma20r', 'amt20']].reset_index(drop=True)
    ev['row'] = rows
    c0 = ev['close'].values.astype(np.float64)
    h0 = ev['high'].values.astype(np.float64)
    v0 = ev['vol'].values.astype(np.float64)

    # S-entry（少量持有期）
    for h in (3, 5, 10, 20):
        if h <= W:
            ev[f'S_ret_{h}'] = close_m[:, h - 1] / c0 - 1.0

    ar = np.arange(n)
    colidx = np.arange(W)[None, :]

    for Lx in LGRID:
        sl = slice(0, Lx)
        ev[f'meanvr_{Lx}'] = np.nanmean(vol_m[:, sl], axis=1) / v0
        ev[f'minlow_{Lx}'] = np.nanmin(low_m[:, sl], axis=1) / c0
        cols = np.arange(Lx, 10)              # 偏移 L+1 .. 10
        cw = close_m[:, cols]
        b1 = cw > h0[:, None]
        vrb = vol_m[:, cols] / vma_m[:, cols]
        for ver, thr in E.BRK_VOL.items():
            cond = b1 if thr <= 0 else (b1 & (vrb >= thr))
            anyb = cond.any(axis=1)
            db = np.where(anyb, cols[0] + np.argmax(cond, axis=1) + 1, np.nan)
            ev[f'dB_{ver}_{Lx}'] = db
            ok = np.isfinite(db)
            di = np.where(ok, db.astype(np.int64) - 1, 0)
            cb = np.where(ok, close_m[ar, np.clip(di, 0, W - 1)], np.nan)
            lo = di
            for h in (3, 5, 10):
                hi_ = np.clip(di + h - 1, 0, W - 1)
                msk = (colidx >= lo[:, None]) & (colidx <= hi_[:, None]) & ok[:, None]
                ev[f'B_ret_{h}_{ver}_{Lx}'] = np.where(ok, close_m[ar, np.clip(di + h, 0, W - 1)] / cb - 1.0, np.nan)
                ev[f'B_mfe_{h}_{ver}_{Lx}'] = np.where(
                    ok, np.nanmax(np.where(msk, high_m, np.nan), axis=1) / cb - 1.0, np.nan)
                ev[f'B_mae_{h}_{ver}_{Lx}'] = np.where(
                    ok, np.nanmin(np.where(msk, low_m, np.nan), axis=1) / cb - 1.0, np.nan)

    ix = pd.read_sql("select trade_date, close from index_daily_cache where ts_code='000001.SH' order by trade_date",
                     sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db'))
    ix['ma20'] = ix['close'].rolling(20, min_periods=20).mean()
    ix['ma60'] = ix['close'].rolling(60, min_periods=60).mean()
    ix['regime'] = np.where((ix['close'] > ix['ma20']) & (ix['ma20'] > ix['ma60']), 'Bull',
                    np.where((ix['close'] < ix['ma20']) & (ix['ma20'] < ix['ma60']), 'Bear', 'Neutral'))
    ix2 = ix[['trade_date', 'regime']].copy()
    ix2['trade_date'] = ix2['trade_date'].astype(np.int64)
    ev = ev.merge(ix2, on='trade_date', how='left')
    ev['year'] = (ev['trade_date'] // 10000).astype(int)
    ev['segment'] = np.where(ev['trade_date'] > E.IS_END, 'OOS', 'IS')
    sz = np.full(n, np.nan)
    for td, g in ev.groupby('td_idx', sort=False):
        v = g['amt20'].values.astype(np.float64)
        ok = np.isfinite(v)
        if ok.sum() < 50:
            continue
        q = np.nanpercentile(v, [33.3, 66.7])
        sz[g.index.values] = np.where(~ok, np.nan, np.digitize(v, q))
    ev['size_q'] = sz
    ev = ev.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
    ev['gap_prev'] = (ev['td_idx'] - ev.groupby('ts_code')['td_idx'].shift(1)).fillna(999).values
    ev.to_parquet(os.path.join(E.DATA, 'events_e09_grid.parquet'), index=False)
    print(f'  网格事件集 {len(ev):,} 行 × {ev.shape[1]} 列', flush=True)
    print('DONE', flush=True)


if __name__ == '__main__':
    main()
