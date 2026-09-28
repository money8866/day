# -*- coding: utf-8 -*-
"""_fs_probe16: 量化 §5 前视污染（滞后报告 ann_date 可能晚于当期 ann_date）"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fs_common import DATA, Log

log = Log('_fs_probe16.txt')


def main():
    fp = os.path.join(DATA, 'events_base.parquet')
    b = pd.read_parquet(fp)
    log('events_base shape=%s  columns=%d' % (b.shape, len(b.columns)))
    b = b.sort_values(['ts_code', 'end_date']).reset_index(drop=True)
    g = b.groupby('ts_code', sort=False)

    ad = b['ann_date'].astype(str)
    ed = b['end_date'].astype(str)

    # ---- 1) shift(1)（上一期报告按 end_date 排序）----
    for k in (1, 4, 12):
        la = ad.groupby(b['ts_code']).shift(k)
        ld = ed.groupby(b['ts_code']).shift(k)
        ok = la.notna()
        leak = ok & (la >= ad)          # 滞后报告公告日 >= 当期公告日 → 尚未公开
        log('shift(%d): 可比 %d  滞后公告日>=当期 %d (%.4f)' % (
            k, int(ok.sum()), int(leak.sum()), leak.sum() / max(ok.sum(), 1)))
        if k == 1:
            sub = b[leak]
            if len(sub):
                log('   shift(1) 污染行的当期报告季度分布: %s' % (
                    sub['end_date'].str[4:6].value_counts().to_dict()))
                log('   滞后季与当期季示例:')
                ex = pd.DataFrame({'cur_end': ed[leak].values,
                                   'cur_ann': ad[leak].values,
                                   'lag_end': ld[leak].values,
                                   'lag_ann': la[leak].values}).head(8)
                log(ex.to_string(index=False))

    # ---- 2) TTM = 最近 4 个单季之和：是否包含未公开的单季 ----
    # 逐股滚动：对每个事件 i，检查 i-3..i 中是否存在 ann_date >= ann_i 的报告
    log('-' * 70)
    log('TTM(rolling 4) 内是否含未公开报告')
    bad = []
    for _, sub in b.groupby('ts_code', sort=False):
        a = sub['ann_date'].astype(str).values
        n = len(a)
        for i in range(3, n):
            if (a[i - 3:i] >= a[i]).any():
                bad.append(i)
        # 标记：需要全局索引，改用下面 groupby 索引方式
    log('  （逐股扫描未公开 TTM 的样本数 = %d）' % len(bad))

    # ---- 3) 用 groupby.apply 精确统计 TTM 污染率 ----
    def ttm_flag(sub):
        a = sub['ann_date'].astype(str).values
        n = len(a)
        f = np.zeros(n, dtype=bool)
        for i in range(3, n):
            f[i] = bool((a[i - 3:i] >= a[i]).any())
        return pd.Series(f, index=sub.index)

    fl = b.groupby('ts_code', sort=False, group_keys=False).apply(ttm_flag)
    log('  TTM(rolling4) 含未公开报告的占比 = %.4f (%d/%d)' % (
        fl.mean(), int(fl.sum()), len(fl)))
    log('  其中当期报告季度分布: %s' % (
        b.loc[fl.values, 'end_date'].str[4:6].value_counts().to_dict()))

    # ---- 4) 当期报告为 Q1 时，Q4 年报是否已公开 ----
    log('-' * 70)
    q1 = b['end_date'].str[4:6] == '03'
    m = b[q1].copy()
    m['lag_ann'] = ad[q1].groupby(m['ts_code']).shift(1)
    m['lag_end'] = ed[q1].groupby(m['ts_code']).shift(1)
    has = m['lag_ann'].notna()
    late = has & (m['lag_ann'].values >= m['ann_date'].astype(str).values)
    log('Q1 事件 %d：有上一期报告 %d；其中上一期(Q4年报)公布晚于 Q1 的占比 %.4f' % (
        len(m), int(has.sum()), late.sum() / max(has.sum(), 1)))

    log('-' * 70)
    log('结论用：若污染率显著(>1%)，则 qoq/acc/S1/S2/TTM 类特征需加 as-of 掩码')
    log.save()


if __name__ == '__main__':
    main()
