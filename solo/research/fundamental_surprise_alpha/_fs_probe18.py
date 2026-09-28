# -*- coding: utf-8 -*-
"""_fs_probe18: neutralize / apply_asof_mask 单元自检（不依赖大面板）"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fs_engine as E
from fs_common import Log

log = Log('_fs_probe18.txt')


def main():
    rng = np.random.RandomState(7)
    # 构造：2 个 cohort，每群 40 行，但按 ts_code 乱序排列（模拟真实面板）
    rows = []
    for k in (100, 200):
        for i in range(40):
            rows.append(dict(ts_code='S%03d' % i, k=k,
                             x=rng.randn(), c=rng.randn(),
                             y=rng.randn()))
    df = pd.DataFrame(rows)
    df = df.sample(frac=1.0, random_state=3).reset_index(drop=True)
    df['ind'] = np.where(np.arange(len(df)) % 3 == 0, 'A', 'B')
    log('排列后 k 是否乱序: %s' % (not df['k'].is_monotonic_increasing))

    # 1) 无控制变量：残差应 = cohort 内去均值后的 rank
    R = E.neutralize(df, ['x'], [], min_n=10)
    log('无控制 min_n=10: NaN 占比 = %.4f (期望 0)' % R['x'].isna().mean())
    xr = E.g_rank(df, ['x'])['x']
    grp = df['k']
    z = (xr - xr.groupby(grp).transform('mean'))
    log('与手工 cohort 去均值 rank 的最大差 = %.6f' % float((R['x'] - z).abs().max()))

    # 2) 控制变量
    R2 = E.neutralize(df, ['x'], ['c'], min_n=10)
    log('有控制 min_n=10: NaN 占比 = %.4f (期望 0)' % R2['x'].isna().mean())
    log('  与 x 的 cohort 内相关 = %.4f (期望≈0)'
        % float(R2['x'].groupby(df['k']).corr(E.g_rank(df, ['x'])['x']).mean()))

    # 3) 行业 FE
    R3 = E.neutralize(df, ['x'], ['c'], min_n=10, ind_col='ind')
    log('行业 FE: NaN 占比 = %.4f' % R3['x'].isna().mean())

    # 4) 极小 cohort 应被剔除
    df2 = pd.concat([df, pd.DataFrame([dict(ts_code='Z', k=999, x=1.0, c=0.0,
                                            y=0.0, ind='A')])], ignore_index=True)
    R4 = E.neutralize(df2, ['x'], [], min_n=10)
    log('新增单行 cohort 是否被剔除: %s' % bool(np.isnan(R4['x'].iloc[-1])))

    # 5) apply_asof_mask
    d = pd.DataFrame({
        'ts_code': ['A', 'A', 'A', 'A', 'B', 'B'],
        'end_date': ['20230331', '20230630', '20230930', '20231231',
                     '20230331', '20230630'],
        'ann_date': ['20230430', '20230830', '20231030', '20240410',
                     '20230420', '20230820'],
        'np_qoq': [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        'np_yoy': [1., 2., 3., 4., 5., 6.],
    })
    d2, st = E.apply_asof_mask(d)
    log('asof 掩码: %s' % st)
    log('  期望仅 A/20231231(np_qoq) 被掩（上一期 ann=20231030? 不，lag1=20230930 ann=20231030 < 20240410 → 不掩）')
    log('  np_qoq 掩码情况: %s' % d2['np_qoq'].tolist())
    log('  np_yoy 未被掩: %s' % d2['np_yoy'].tolist())

    # 6) 构造真前视：B/20230630 ann=20230820，其 lag1=B/20230331 ann=20230420 < 20230820 → 不掩
    d3 = pd.DataFrame({
        'ts_code': ['C', 'C'], 'end_date': ['20230331', '20230630'],
        'ann_date': ['20230810', '20230801'],           # lag1 晚于当期 → 掩
        'np_qoq': [0.1, 0.2], 'np_yoy': [1., 2.],
    })
    d4, st4 = E.apply_asof_mask(d3)
    log('真前视用例: %s -> np_qoq=%s (期望 [0.1, nan])' % (st4, d4['np_qoq'].tolist()))
    log.save()


if __name__ == '__main__':
    main()
