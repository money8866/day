# -*- coding: utf-8 -*-
"""TL-01 面板化：把 8.6M 行长表重排为「日期 × 股票」稠密 memmap 面板

交付物 tl01_feature_daily / tl01_state_daily 的行序是 (ci, k)（先股票后日期），
不利于按交易日的横截面运算（IC / AUC / 分位 / 组合 / Regime 分组）。
本脚本把交付物一次性重排为 NCAL × NCODE 的稠密 memmap 面板（每列一个 .npy），
后续分析以 mmap 方式只加载所需列，避免 3.9GB 全量驻留内存。

本脚本不改变任何数值，只做重排（点值一一对应），因此不引入泄漏。
"""
import os
import json
import time
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from tl_common import HERE, DATA, Log

PAND = os.path.join(DATA, 'panel')
F1 = os.path.join(HERE, 'tl01_feature_daily.parquet')
F2 = os.path.join(HERE, 'tl01_state_daily.parquet')

INT_COLS = {
    'state': '<i1', 'state_prev': '<i1', 'state_chg': '<i1', 'regime': '<i1',
    'month': '<i1', 'trend_age': '<i2', 'state_dur': '<i2', 'year': '<i2',
    'ind_l1': '<i2', 'listing_days': '<i4',
}
F32_COLS = [
    'ts_raw', 'ts_18', 'ts_25', 'tv', 'ta', 'reacc_raw', 'atr_pct_pct',
    'ret_20', 'ret_60', 'c_ma20', 'c_ma60', 'ma20_ma60', 'pos_60', 'pos_120',
    'rv_20', 'rs_20', 'rs_60', 'slope_20', 'dd_60', 'dd_min10', 'v_trend',
    'v_ma5_ma20', 'bm_total_mv', 'bm_pb', 'bm_turnover_rate', 'entry_open',
    'buy_ok',
    'fwd_3', 'fwd_5', 'fwd_10', 'fwd_20', 'fwd_60',
    'rel_3', 'rel_5', 'rel_10', 'rel_20', 'rel_60',
    'exe_3', 'exe_5', 'exe_10', 'exe_20', 'exe_60',
    'mfe_5', 'mfe_10', 'mfe_20', 'mfe_60',
    'mae_5', 'mae_10', 'mae_20', 'mae_60',
    'cont_nh20', 'cont_ma20', 'cont_rs20',
    'fail_ma20', 'fail_ma60', 'fail_low', 'fail_rs',
]
INT_DEFAULT = {'state': -1, 'state_prev': -1, 'state_chg': -1, 'regime': -1,
               'year': -1, 'month': -1, 'trend_age': -1, 'state_dur': -1,
               'ind_l1': -1, 'listing_days': -1}


def main():
    t0 = time.time()
    log = Log('_tl_panel.txt')
    log('=' * 78)
    log('TL-01 面板化  ->  %s' % PAND)
    os.makedirs(PAND, exist_ok=True)

    pfs = pq.ParquetFile(F2)
    pff = pq.ParquetFile(F1)

    # ---------- Pass A：ci -> ts_code，k -> trade_date ----------
    cmap = {}
    dmap = {}
    for g in range(pfs.num_row_groups):
        t = pfs.read_row_group(g, columns=['ci', 'ts_code', 'k', 'trade_date'])
        ci = t.column('ci').to_numpy()
        kk = t.column('k').to_numpy()
        sc = t.column('ts_code').to_pylist()
        dt = t.column('trade_date').to_pylist()
        for a, b in zip(ci.tolist(), sc):
            cmap[a] = b
        for a, b in zip(kk.tolist(), dt):
            dmap[a] = b
        del t
    NCODE = max(cmap) + 1
    NCAL = max(dmap) + 1
    codes = [cmap.get(i, '') for i in range(NCODE)]
    dates = [dmap.get(i, '') for i in range(NCAL)]
    log('  NCODE=%d  NCAL=%d  (%.0fs)' % (NCODE, NCAL, time.time() - t0))

    # ---------- 分配 memmap ----------
    shape = (NCAL, NCODE)
    for c, tp in INT_COLS.items():
        p = os.path.join(PAND, c + '.npy')
        m = np.lib.format.open_memmap(p, mode='w+', dtype=tp, shape=shape)
        m[:] = np.array(INT_DEFAULT[c], dtype=tp)
        del m
    for c in F32_COLS:
        p = os.path.join(PAND, c + '.npy')
        m = np.lib.format.open_memmap(p, mode='w+', dtype='<f4', shape=shape)
        m[:] = np.float32(np.nan)
        del m

    def mm(c):
        return np.lib.format.open_memmap(os.path.join(PAND, c + '.npy'), mode='r+')

    ind_map = {}

    def ind_code(sv):
        out = np.empty(len(sv), dtype=np.int16)
        for i, s in enumerate(sv):
            if s is None or s == '':
                out[i] = -1
                continue
            j = ind_map.get(s)
            if j is None:
                j = len(ind_map)
                ind_map[s] = j
            out[i] = j
        return out

    # ---------- Pass B：逐 row group 散射 ----------
    st_f32 = [c for c in ('tv', 'ta', 'reacc_raw') if c in F32_COLS]
    st_i = ['state', 'state_prev', 'state_chg', 'trend_age', 'state_dur']
    nc = 0
    for g in range(pfs.num_row_groups):
        ts = pfs.read_row_group(g, columns=['ci', 'k'] + st_i + st_f32)
        kk = ts.column('k').to_numpy()
        ci = ts.column('ci').to_numpy()
        for c in st_i:
            mm(c)[kk, ci] = ts.column(c).to_numpy()
        for c in st_f32:
            mm(c)[kk, ci] = ts.column(c).to_numpy().astype('<f4')
        del ts
        cols = ['ci', 'k'] + [c for c in F32_COLS
                              if c not in ('tv', 'ta', 'reacc_raw')] + \
               ['year', 'month', 'regime', 'listing_days', 'ind_l1']
        tf = pff.read_row_group(g, columns=cols)
        kk = tf.column('k').to_numpy()
        ci = tf.column('ci').to_numpy()
        for c in cols:
            if c in ('ci', 'k'):
                continue
            v = tf.column(c)
            if c == 'ind_l1':
                mm(c)[kk, ci] = ind_code(v.to_pylist())
            elif c in INT_COLS:
                mm(c)[kk, ci] = v.to_numpy()
            else:
                mm(c)[kk, ci] = v.to_numpy().astype('<f4')
        del tf
        nc += len(kk)
        if g % 5 == 0 or g == pfs.num_row_groups - 1:
            log('  块 %d/%d 累计 %d 行  %.0fs' % (g, pfs.num_row_groups - 1,
                                                 nc, time.time() - t0))

    meta = dict(ncal=NCAL, ncode=NCODE, dates=dates, codes=codes,
                int_cols=INT_COLS, f32_cols=F32_COLS,
                int_default=INT_DEFAULT, ind_map=ind_map)
    with open(os.path.join(PAND, 'meta.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False)
    log('  面板列数 = %d  (int %d / f32 %d)'
        % (len(INT_COLS) + len(F32_COLS), len(INT_COLS), len(F32_COLS)))
    log('  行业类别 = %d' % len(ind_map))
    log('=' * 78)
    log('面板化完成  %.0fs' % (time.time() - t0))
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
