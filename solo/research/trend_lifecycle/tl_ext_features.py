# -*- coding: utf-8 -*-
"""TL-01 §33 参数扰动所需列的补齐（从行情面板 + 面板本身重算，全程 point-in-time）

背景
  tl01_feature_daily.parquet 只落了主口径窗口（ret_5/10/20/40/60/120、rs_18/20/25/60），
  P1/P2/P3 中间件（tl_ts_daily / tl_xs_daily / tl_state_daily）已清理，且
  tv_alt18 / tv_alt25 在 tl_build 中只是段内临时变量、从未落盘。
  但 §33 要求对趋势窗口 18/20/25 也做扰动，因此需要：
      ret_18, ret_25              —— 由 qfq_close 按 tl_build._ret 同口径重算
      tv_alt18, tv_alt25          —— 由面板 ts_18 / ts_25 按 tl_build 同口径段内 velocity 重算
  另外 c_ma18 / c_ma22 / ma18_ma60 / ma22_ma60 / ma60_slope / rs_18 / rs_25
  已由 tl_panel_ext.py 从 tl01_feature_daily.parquet 散射完成。

口径一致性（关键）
  tl_build 的所有 shift/ret 都是「同一股票在行情文件中的相邻行」（code-major 压缩行序），
  不是「自然交易日的相邻日」，停牌会跨过。本脚本严格复现该口径：
    1) 读 calendar + price_panel，按 (ci, k) 排序建 code-major 行序；
    2) 段内计算；
    3) 散射回 (NCAL, NCODE) 面板。
  自校验：用同样流程重算 tv = ts_raw - shift5(ts_raw)，与面板既有 tv 逐格比对，
  最大绝对偏差应为 0（除 NaN 位置）—— 这同时验证了段内行序与 tl_build 完全一致。

产出：data/panel/{ret_18,ret_25,tv_alt18,tv_alt25}.npy + _tl_ext_features.txt
"""
import os
import time
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from tl_common import HERE, DATA, FS_DATA, Log, panel_meta

PAND = os.path.join(DATA, 'panel')
PCOLS = ['qfq_close']
NEW = ['ret_18', 'ret_25', 'tv_alt18', 'tv_alt25']


def _shift(v, n):
    y = np.asarray(v, dtype=np.float64)
    out = np.full(len(y), np.nan, dtype=np.float32)
    if n < len(y):
        out[n:] = y[:-n]
    return out


def _ret(px, w):
    px = np.asarray(px, dtype=np.float64)
    n = len(px)
    out = np.full(n, np.nan, dtype=np.float32)
    if n > w:
        with np.errstate(invalid='ignore', divide='ignore'):
            out[w:] = px[w:] / px[:-w] - 1.0
    return out


def main():
    t0 = time.time()
    log = Log('_tl_ext_features.txt')
    log('=' * 78)
    log('TL-01 §33 参数扰动所需列补齐（ret_18/ret_25/tv_alt18/tv_alt25）')

    meta = panel_meta()
    NCAL, NCODE = meta['ncal'], meta['ncode']
    codes = meta['codes']

    cal = pd.read_parquet(os.path.join(FS_DATA, 'calendar.parquet'))
    td = cal['trade_date'].astype(str).values
    kmap = pd.Series(np.arange(len(td)), index=td)
    log('  日历 %d 日  面板 %d x %d' % (len(td), NCAL, NCODE))

    px = pd.read_parquet(os.path.join(FS_DATA, 'price_panel.parquet'),
                         columns=['ts_code', 'trade_date'] + PCOLS)
    px['trade_date'] = px['trade_date'].astype(str)
    # 面板 codes 中缺失槽位为空串且可能重复，去重后再做 ts_code -> ci 映射
    ix = pd.Index(codes)
    keep = ~ix.duplicated(keep='first')
    mapc = pd.Series(np.arange(NCODE)[keep], index=ix[keep])
    ci = px['ts_code'].map(mapc)
    ci = ci.fillna(-1.0).values.astype(np.int64)
    kk = px['trade_date'].map(kmap).values
    Cq = pd.to_numeric(px['qfq_close'], errors='coerce').values.astype(np.float32)
    del px, ix
    ok = (ci >= 0) & np.isfinite(kk)
    ci = ci[ok].astype(np.int32)
    kk = kk[ok].astype(np.int32)
    Cq = Cq[ok]
    order = np.lexsort((kk, ci))          # 主键 ci、次键 k
    ci, kk, Cq = ci[order], kk[order], Cq[order]
    starts = np.append(np.searchsorted(ci, np.arange(NCODE), side='left'), len(ci))
    log('  行情 %d 行；%d 只股票有行情  %.0fs'
        % (len(ci), int((starts[1:] > starts[:-1]).sum()), time.time() - t0))

    dst = {}
    for c in NEW:
        a = np.lib.format.open_memmap(os.path.join(PAND, c + '.npy'), mode='w+',
                                      dtype='<f4', shape=(NCAL, NCODE))
        a[:] = np.nan
        a.flush()
        dst[c] = a

    T18 = np.asarray(np.lib.format.open_memmap(os.path.join(PAND, 'ts_18.npy')))
    T25 = np.asarray(np.lib.format.open_memmap(os.path.join(PAND, 'ts_25.npy')))
    TRAW = np.asarray(np.lib.format.open_memmap(os.path.join(PAND, 'ts_raw.npy')))
    chk = np.full(len(ci), np.nan, dtype=np.float32)   # 自校验：重算 tv

    for c in range(NCODE):
        a, b = int(starts[c]), int(starts[c + 1])
        if b <= a:
            continue
        kk_s, ci_s = kk[a:b], ci[a:b]
        r18 = _ret(Cq[a:b], 18)
        r25 = _ret(Cq[a:b], 25)
        m18 = T18[kk_s, ci_s]
        m25 = T25[kk_s, ci_s]
        mraw = TRAW[kk_s, ci_s]
        v18 = m18.astype(np.float64) - _shift(m18, 5).astype(np.float64)
        v25 = m25.astype(np.float64) - _shift(m25, 5).astype(np.float64)
        chk[a:b] = (mraw.astype(np.float64)
                    - _shift(mraw, 5).astype(np.float64)).astype(np.float32)
        dst['ret_18'][kk_s, ci_s] = r18
        dst['ret_25'][kk_s, ci_s] = r25
        dst['tv_alt18'][kk_s, ci_s] = v18.astype(np.float32)
        dst['tv_alt25'][kk_s, ci_s] = v25.astype(np.float32)
    for c in NEW:
        dst[c].flush()
    log('  四列计算并落盘完成  %.0fs' % (time.time() - t0))

    # ---------------- 自校验：重算 tv vs 面板 tv ----------------
    TV = np.asarray(np.lib.format.open_memmap(os.path.join(PAND, 'tv.npy')))
    d = np.abs(TV[kk, ci] - chk)
    fin = np.isfinite(d)
    log('  自校验 tv = ts_raw - shift5(ts_raw)：可比 %d / %d 行，最大偏差 %.3e，'
        '不一致(>1e-6) %d 行 -> %s'
        % (int(fin.sum()), len(d), float(np.nanmax(d)) if fin.any() else 0.0,
           int((d[fin] > 1e-6).sum()),
           'PASS（段内行序与 tl_build 一致）'
           if fin.any() and not (d[fin] > 1e-6).any() else 'FAIL'))
    for c in NEW:
        v = np.asarray(dst[c])
        log('    %-9s 非空 %d (%.1f%%)  均值 %+.4f'
            % (c, int(np.isfinite(v).sum()),
               100.0 * np.isfinite(v).sum() / v.size,
               float(np.nanmean(v)) if np.isfinite(v).any() else np.nan))
    log('完成  %.0fs' % (time.time() - t0))
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
