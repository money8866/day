# -*- coding: utf-8 -*-
"""TL-01 §33 参数网格所需的替代窗口特征列 —— 单独散射入 data/panel/

背景：tl_panel.py 只持久化了主口径（ma=20 / trend=20 / rs=20）所需的列，
而 §33 要求对 MA 窗口(18/20/22)、趋势窗口(18/20/25)、RS 窗口(18/20/25) 做扰动，
这些替代窗口列已在 P1/P2 阶段计算并落盘于
  tl01_feature_daily.parquet : c_ma18 c_ma22 ma18_ma60 ma22_ma60 ma60_slope
                               ret_18 ret_25 rs_18 rs_25
  tl01_state_daily.parquet   : tv_alt18 tv_alt25
但未写入面板。本脚本只新增这些列文件，**不修改任何既有列**，
因此可以在其它分析读取既有列的memmap 时安全运行。

产出：data/panel/<col>.npy （仅新增列）+ _tl_panel_ext_run.txt
"""
import os
import json
import time
import numpy as np
import pyarrow.parquet as pq

from tl_common import HERE, DATA, Log

PAND = os.path.join(DATA, 'panel')
F1 = os.path.join(HERE, 'tl01_feature_daily.parquet')
F2 = os.path.join(HERE, 'tl01_state_daily.parquet')
EXT1 = ['c_ma18', 'c_ma22', 'ma18_ma60', 'ma22_ma60', 'ma60_slope',
        'ret_18', 'ret_25', 'rs_18', 'rs_25']
EXT2 = ['tv_alt18', 'tv_alt25']


def main():
    t0 = time.time()
    log = Log('_tl_panel_ext_run.txt')
    log('=' * 78)
    log('TL-01 面板扩展列散射（§33 参数扰动所需替代窗口特征）')

    with open(os.path.join(PAND, 'meta.json'), encoding='utf-8') as f:
        meta = json.load(f)
    NCAL, NCODE = meta['ncal'], meta['ncode']
    log('  面板 %d x %d' % (NCAL, NCODE))

    done = []
    for path, cols in ((F1, EXT1), (F2, EXT2)):
        pf = pq.ParquetFile(path)
        names = set(pf.schema_arrow.names)
        use = [c for c in cols if c in names]
        miss = [c for c in cols if c not in names]
        log('  %s：需要 %d 列，可提供 %d 列%s'
            % (os.path.basename(path), len(cols), len(use),
               ('，缺失 ' + ','.join(miss)) if miss else ''))
        if not use:
            continue
        dst = {}
        for c in use:
            a = np.lib.format.open_memmap(os.path.join(PAND, c + '.npy'), mode='w+',
                                          dtype='<f4', shape=(NCAL, NCODE))
            a[:] = np.nan
            a.flush()
            dst[c] = a
        nc = 0
        for g in range(pf.num_row_groups):
            t = pf.read_row_group(g, columns=['ci', 'k'] + use)
            kk = t.column('k').to_numpy()
            ci = t.column('ci').to_numpy()
            for c in use:
                dst[c][kk, ci] = t.column(c).to_numpy().astype('<f4')
            nc += len(kk)
            del t
            if g % 5 == 0 or g == pf.num_row_groups - 1:
                log('    块 %d/%d 累计 %d 行  %.0fs'
                    % (g, pf.num_row_groups - 1, nc, time.time() - t0))
        for c in use:
            v = np.asarray(dst[c])
            log('    %-12s 非空 %d (%.1f%%)  均值 %+.4f'
                % (c, int(np.isfinite(v).sum()),
                   100.0 * np.isfinite(v).sum() / v.size,
                   float(np.nanmean(v)) if np.isfinite(v).any() else np.nan))
            del dst[c]
        del dst
        done += use

    log('  新增列：%s' % ' '.join(done))
    log('完成  %.0fs' % (time.time() - t0))
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
