# -*- coding: utf-8 -*-
"""
H-IND-DIFFUSION-01  P3：个股扩散暴露 + 控制变量（STEP 9 / §21 / §28）

隔离声明：本模块自包含，不 import 任何既有研究模块。

产出（§57）
    stock_diffusion_exposure.parquet   个股 × 日：暴露主分数 / 分量 / 映射的行业状态 / 前向收益
    data/panel/expo_*.npy              code-major (NCODES, NCAL) 面板（P4-P6 直接读取）

暴露定义（冻结于 hid_common.DERIVED，derived_hash）
    rs_20    = ret_20 - mom_ew_20(所属行业)                       （§18 个股超额）
    exposure = 0.5 * pct_ind(diffusion_score)                     （行业层：跨行业分位）
             + 0.5 * pct_within_ind(rs_20)                        （个股层：行业内分位）
    等权固定，不做任何权重搜索（§59）。

口径说明
    - 暴露仅在 valid = 1 且所属行业 eligible（N >= 20）且两分量均可得时定义。
    - pct_ind 直接取 P2 的 diffusion_score（其本身即跨行业分位 0~1，见 DERIVED 分量定义）。
    - 主口径行业层级 = SW L1（用户 20260927 确认）；L2 抽检在 P6 内即时重算。
    - 本模块不产生任何信号、阈值或交易规则（§61）。
"""
import os
import sys
import time

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from hid_common import (DATA, PREREG, DERIVED, mklog, pload, pload_day, pload_codes,
                        pload_meta, psave_meta_side, code_grid, map_grid,
                        within_group_pct, rows_by_code, EXP_FP,
                        prereg_hash, derived_hash)

LOG = mklog('exposure')

PAN = os.path.join(DATA, 'panel')
MIN_N = int(PREREG['ind_n_min'])
RS_MIN_N = int(DERIVED['stock_rs_min_n'])

# 行业层需要映射到个股的列（来自 P2 的两张表）
FROM_STATE = [('mom_ew_20', 'ind_mom_20'), ('up_20', 'ind_up_20'),
              ('dchg_5', 'ind_dchg_5'), ('accel20', 'ind_accel20'),
              ('lb_020', 'ind_lb_020'), ('rsb_chg_5', 'ind_rsb_chg_5'),
              ('diff_gap', 'ind_diff_gap'), ('n_used', 'ind_n_used')]
FROM_FEAT = [('diffusion_score', 'ind_diffusion_score'),
             ('pct_dchg_5', 'ind_pct_dchg_5'), ('pct_accel20', 'ind_pct_accel20'),
             ('pct_lb_020', 'ind_pct_lb_020'), ('pct_rsb_chg_5', 'ind_pct_rsb_chg_5')]

PARQ_COLS = (['ts_code', 'trade_date', 'k', 'code_i', 'ind_l1', 'ind_l1_name', 'ind_l2',
              'ind_n_used', 'ind_eligible']
             + ['ret_20', 'rs_20', 'expo_rs_only', 'expo_ind_only', 'exposure']
             + ['ind_up_20', 'ind_dchg_5', 'ind_accel20', 'ind_lb_020',
                'ind_rsb_chg_5', 'ind_diff_gap', 'ind_diffusion_score']
             + ['exe_3', 'exe_5', 'exe_10'])


def main():
    LOG.sep('=')
    LOG('H-IND-DIFFUSION-01  P3 个股扩散暴露  hash=%s derived=%s  %s'
        % (prereg_hash(), derived_hash(), time.strftime('%Y-%m-%d %H:%M:%S')))
    LOG.sep()

    meta = pload_meta()
    NCODES, NCAL = int(meta['NCODES']), int(meta['NCAL'])
    n_l1, n_l2 = int(meta['n_l1']), int(meta['n_l2'])
    l1_names = meta['l1_names']
    day = pload_day()
    td = np.asarray(day['dates']).astype(str)

    st = pd.read_parquet(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      'industry_daily_state.parquet'))
    ft = pd.read_parquet(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      'industry_diffusion_features.parquet'))
    st = st[st['ind_level'] == 'L1']
    ft = ft[ft['ind_level'] == 'L1']
    LOG('[STEP9] 行业层输入：state %d 行 / feat %d 行（L1）' % (len(st), len(ft)))

    ind_l1 = np.asarray(pload('_code_ind_l1'))
    valid = (np.asarray(pload('valid')) == 1)

    # ---- 行业层网格 -> 个股层映射 ----
    grid_el = code_grid(st, 'eligible', n_l1, NCAL)
    grids = {}
    for src, dst in FROM_STATE:
        grids[dst] = code_grid(st, src, n_l1, NCAL)
    for src, dst in FROM_FEAT:
        grids[dst] = code_grid(ft, src, n_l1, NCAL)

    M = {}
    for dst, g in grids.items():
        v = map_grid(g, ind_l1).astype(np.float32)
        if dst == 'ind_eligible':
            v = np.where(np.isfinite(v) & (v > 0.5), 1.0, 0.0).astype(np.float32)
        M[dst] = v
    M['ind_eligible'] = np.where(np.isfinite(map_grid(grid_el, ind_l1)), 1.0, 0.0).astype(np.float32)

    scope = valid & (ind_l1 > 0) & (M['ind_eligible'] > 0.5)
    LOG('  暴露范围（valid & 行业已知 & 行业 eligible）：%d 股票日（占 valid 的 %.4f）'
        % (int(scope.sum()), scope.sum() / max(1, int(valid.sum()))))

    # ---- §18 个股相对强度 + 行业内分位 ----
    r20 = np.asarray(pload('ret_20'), dtype=np.float64)
    rs20 = r20 - M['ind_mom_20']
    with np.errstate(invalid='ignore'):
        rs20 = np.where(scope & np.isfinite(r20) & np.isfinite(M['ind_mom_20']), rs20, np.nan)
    LOG('  rs_20 可得 %d；均值 %.5f / std %.4f'
        % (int(np.isfinite(rs20).sum()),
           float(np.nanmean(rs20)), float(np.nanstd(rs20))))

    t0 = time.time()
    expo_rs = within_group_pct(rs20, ind_l1, scope, min_n=RS_MIN_N)
    LOG('  行业内分位完成（%.1fs）；可得 %d（min_n=%d）'
        % (time.time() - t0, int(np.isfinite(expo_rs).sum()), RS_MIN_N))

    expo_ind = np.where(scope & np.isfinite(M['ind_diffusion_score']),
                        M['ind_diffusion_score'], np.nan)
    exposure = np.where(np.isfinite(expo_ind) & np.isfinite(expo_rs),
                        0.5 * expo_ind + 0.5 * expo_rs, np.nan)

    LOG('  §28 暴露主分数可得 %d 股票日（占 valid 的 %.4f）'
        % (int(np.isfinite(exposure).sum()),
           np.isfinite(exposure).sum() / max(1, int(valid.sum()))))
    LOG('  暴露分布：min %.3f / p25 %.3f / median %.3f / p75 %.3f / max %.3f'
        % tuple(np.nanpercentile(exposure, [0, 25, 50, 75, 100])))

    # ---- 落盘 npy ----
    out = {'expo_ind': exposure.astype(np.float32),
           'expo_rs_only': expo_rs.astype(np.float32),
           'expo_ind_only': expo_ind.astype(np.float32),
           'expo_scope': scope.astype(np.int8),
           'rs_20': np.where(np.isfinite(rs20), rs20, np.nan).astype(np.float32)}
    for dst in [d for _, d in FROM_STATE] + [d for _, d in FROM_FEAT] + ['ind_eligible']:
        out[dst] = M[dst]
    for k, v in out.items():
        np.save(os.path.join(PAN, 'expo_%s.npy' % k), v)
    LOG('  [SAVE] data/panel/expo_*.npy 共 %d 个（形状 (%d, %d)）'
        % (len(out), NCODES, NCAL))

    # ---- 落盘 stock_diffusion_exposure.parquet（按时间分块写入，控内存）----
    # P1 落盘的代码列表（与 memmap 行序严格一致，见 hid_panel.py）
    codes = pload_codes()
    if len(codes) != NCODES:
        raise RuntimeError('_codes.npy 长度 %d != NCODES %d' % (len(codes), NCODES))

    exe = {h: np.asarray(pload('exe_%d' % h)) for h in (3, 5, 10)}
    br = {}
    writer = None
    CH = 240
    n_rows = 0
    for k0 in range(0, NCAL, CH):
        k1 = min(NCAL, k0 + CH)
        blk = valid[:, k0:k1]
        ii, kk = np.nonzero(blk)
        kk = kk + k0
        if len(ii) == 0:
            continue
        d = {
            'ts_code': codes[ii].astype(str),
            'trade_date': td[kk],
            'k': kk.astype(np.int32),
            'code_i': ii.astype(np.int32),
            'ind_l1': ind_l1[ii, kk].astype(np.int16),
        }
        d['ind_l1_name'] = np.asarray(
            [l1_names[c - 1] if c > 0 else 'UNKNOWN' for c in d['ind_l1']], dtype=object)
        l2 = np.asarray(pload('_code_ind_l2'))[ii, kk].astype(np.int16)
        d['ind_l2'] = l2
        d['ind_n_used'] = M['ind_n_used'][ii, kk].astype(np.float32)
        d['ind_eligible'] = (M['ind_eligible'][ii, kk] > 0.5).astype(np.int8)
        d['ret_20'] = np.asarray(pload('ret_20'))[ii, kk].astype(np.float32)
        for nm, arr in (('rs_20', rs20), ('expo_rs_only', expo_rs),
                        ('expo_ind_only', expo_ind), ('exposure', exposure)):
            d[nm] = arr[ii, kk].astype(np.float32)
        for _, dst in FROM_STATE:
            d[dst] = M[dst][ii, kk]
        d['ind_diffusion_score'] = M['ind_diffusion_score'][ii, kk]
        for h in (3, 5, 10):
            d['exe_%d' % h] = exe[h][ii, kk]
        df = pd.DataFrame({c: d[c] for c in PARQ_COLS})
        tbl = pa.Table.from_pandas(df, preserve_index=False)
        if writer is None:
            writer = pq.ParquetWriter(EXP_FP, tbl.schema, compression='zstd')
        writer.write_table(tbl)
        n_rows += len(df)
        del df, tbl, d
    if writer is not None:
        writer.close()
    LOG('  [SAVE] %s  rows=%d  cols=%d  (%.1f MB)'
        % (os.path.basename(EXP_FP), n_rows, len(PARQ_COLS),
           os.path.getsize(EXP_FP) / 1e6))

    psave_meta_side('_meta_p3.json', {
        'p3_built_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'p3_prereg_hash': prereg_hash(), 'p3_derived_hash': derived_hash(),
        'p3_rows_parquet': int(n_rows),
        'p3_exposure_coverage': float(np.isfinite(exposure).sum() / max(1, int(valid.sum()))),
        'p3_scope_coverage': float(scope.sum() / max(1, int(valid.sum()))),
        'p3_exposure_quantiles': {
            'p0': float(np.nanmin(exposure)), 'p25': float(np.nanpercentile(exposure, 25)),
            'p50': float(np.nanmedian(exposure)), 'p75': float(np.nanpercentile(exposure, 75)),
            'p100': float(np.nanmax(exposure))},
        'p3_cols': PARQ_COLS,
    })
    LOG.sep('=')
    LOG('DONE  P3')


if __name__ == '__main__':
    main()
