# -*- coding: utf-8 -*-
"""hep_incremental: H-EARN-POST §16 / §23 / §24 增量 Alpha

§16 反伪装：Momentum 控制后 Residual Alpha ≈ 0  =>  FAIL — MOMENTUM PROXY
§23 五个 Baseline B1–B5：H-EARN-POST 必须证明自己不是 B1–B5 的简单变形
§24 三层残差：Raw -> Momentum Neutral -> Momentum+Value+Size+Liquidity+Industry

输出：out/h_earn_post_incremental.csv
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hep_common import (OUTD, PREREG, ENTRIES, HORIZONS, MIN_XS, Log, spearman_ic)
import hep_engine as E

log = Log('_hep_incremental.txt')
PRIMARY_SIG = PREREG['primary_signal']
COST_BPS = 30.0

CTRL_SETS = [
    ('RAW', [], None),
    ('MOM', ['CTRL_MOM'], None),
    ('MOM_VAL', ['CTRL_MOM', 'CTRL_VAL'], None),
    ('MOM_VAL_SIZE_LIQ', ['CTRL_MOM', 'CTRL_VAL', 'CTRL_SIZE', 'CTRL_LIQ'], None),
    ('FULL_TX', ['CTRL_MOM', 'CTRL_VAL', 'CTRL_SIZE', 'CTRL_LIQ'], 'ind_tx'),
    ('FULL_SW', ['CTRL_MOM', 'CTRL_VAL', 'CTRL_SIZE', 'CTRL_LIQ'], 'ind_l1'),
    ('BASE5', ['B1_MOM', 'B2_VAL', 'B3_GROW', 'B4_REACT', 'B5_FM'], None),
]
CTRLS_FOR_CORR = ['CTRL_MOM', 'CTRL_VAL', 'CTRL_SIZE', 'CTRL_LIQ']


def mean_cohort_corr(d, a, b):
    out = []
    for idx in d.groupby('sig_k', sort=False).groups.values():
        s = d.loc[idx]
        c = spearman_ic(pd.to_numeric(s[a], errors='coerce').values,
                        pd.to_numeric(s[b], errors='coerce').values)
        if np.isfinite(c):
            out.append(c)
    return float(np.mean(out)) if out else np.nan


def mean_ind_r2(d, sig, ind_col='ind_tx', ind_min_n=5):
    """信号有多少比例可以被行业解释（逐 cohort 对行业哑变量回归的平均 R²）。"""
    out = []
    for idx in d.groupby('sig_k', sort=False).groups.values():
        s = d.loc[idx]
        X = E._ctrl_matrix(s, [], ind_col, ind_min_n)
        if X.shape[1] == 0:
            continue
        y = pd.to_numeric(s[sig], errors='coerce').values
        m = np.isfinite(y)
        if m.sum() < max(MIN_XS, X.shape[1] + 6):
            continue
        A = np.column_stack([np.ones(int(m.sum())), X[m]])
        b, *_ = np.linalg.lstsq(A, y[m], rcond=None)
        res = y[m] - A.dot(b)
        ss = float(((y[m] - y[m].mean()) ** 2).sum())
        if ss > 1e-18:
            out.append(1.0 - float((res ** 2).sum()) / ss)
    return float(np.mean(out)) if out else np.nan


def tb20(d, sig, ret):
    q = E.quintile_means(d, sig, ret)
    if q.empty:
        return np.nan, np.nan
    c = COST_BPS / 1e4
    g = float((q['t20'] - q['b20']).mean())
    return g, g - 4 * c


def main():
    log('=' * 78)
    log('H-EARN-POST §16/§23/§24 增量 Alpha（预注册 %s）' % PREREG['hypothesis_id'])
    d = E.load()
    d = E.add_features(d)
    dirs = E.freeze_dir(d, E.BASE_COLS, (2018, 2022), horizon=PREREG['primary_horizon'])
    E.apply_dir(d, E.BASE_COLS, dirs)
    log('载入 %s；基线方向(TRAIN 冻结) %s' % ((d.shape,), {k: int(v) for k, v in dirs.items()}))
    sigs = list(E.SIG_COLS) + E.BASE_COLS

    rows = []
    for e in ENTRIES:
        de = d[d['E'] == e]
        grp = E.cohorts(de)
        log('  E%d：%d 行 / %d 个 cohort（中性化已按 cohort 预计算）' % (e, len(de), len(grp)))
        for sig in sigs:
            cors = {c: mean_cohort_corr(de, sig, c) for c in CTRLS_FOR_CORR} if sig in E.SIG_COLS else {}
            cors['IND_R2'] = mean_ind_r2(de, sig, 'ind_tx') if sig in E.SIG_COLS else np.nan
            # 残差信号与 Horizon 无关：每个 (signal, layer) 只算一次
            layers = {}
            for layer, cols, ind in CTRL_SETS:
                if layer == 'RAW':
                    layers[layer] = pd.to_numeric(de[sig], errors='coerce')
                else:
                    layers[layer] = E.neutralize(de, sig, cols, ind_col=ind, groups=grp)
            for layer, cols, ind in CTRL_SETS:
                dd = de.assign(_sig=layers[layer])
                for T in HORIZONS:
                    ret = 'ex_%d' % T
                    ics = E.ic_series(dd, '_sig', ret)
                    g, n = tb20(dd, '_sig', ret)
                    st = E.ic_row(ics, T)
                    r = dict(entry='E%d' % e, signal=sig, horizon='T+%d' % T, layer=layer,
                             ctrl=','.join(cols) if cols else '-', ind=ind or '-',
                             tb20_gross=g, tb20_net=n,
                             corr_mom=cors.get('CTRL_MOM', np.nan),
                             corr_val=cors.get('CTRL_VAL', np.nan),
                             corr_size=cors.get('CTRL_SIZE', np.nan),
                             corr_liq=cors.get('CTRL_LIQ', np.nan),
                             corr_ind=cors.get('IND_R2', np.nan))
                    r.update(st)
                    rows.append(r)
        log('    E%d 完成，累计 %d 行' % (e, len(rows)))
    df = pd.DataFrame(rows)
    cols = ['entry', 'signal', 'horizon', 'layer', 'ctrl', 'ind', 'n', 'mean_ic',
            'icir', 'ic_t', 'pos_ratio', 'tb20_gross', 'tb20_net',
            'corr_mom', 'corr_val', 'corr_size', 'corr_liq', 'corr_ind']
    df = df[cols]
    df.to_csv(os.path.join(OUTD, 'h_earn_post_incremental.csv'), index=False, encoding='utf-8-sig')

    log('=' * 78)
    log('1) §16 与各控制变量的横截面相关（E20 / T+20）—— 伪装风险诊断')
    c = df[(df['entry'] == 'E20') & (df['horizon'] == 'T+20') & (df['layer'] == 'RAW')]
    log(E.fmt_tbl(c.set_index('signal')[['corr_mom', 'corr_val', 'corr_size', 'corr_liq', 'corr_ind']]))

    log('=' * 78)
    log('2) §24 三层残差 Alpha（mean_ic；Primary=T+20）')
    p = df[(df['horizon'] == 'T+20') & (df['signal'].isin(E.SIG_COLS))]
    log(E.fmt_tbl(p.pivot_table(index=['entry', 'signal'], columns='layer',
                                values='mean_ic')[['RAW', 'MOM', 'FULL_TX', 'BASE5']], 4))

    log('=' * 78)
    log('3) §24 三层残差 Top-Bottom（tb20_gross / tb20_net30bp；Primary=T+20）')
    piv_g = p.pivot_table(index=['entry', 'signal'], columns='layer', values='tb20_gross')
    piv_n = p.pivot_table(index=['entry', 'signal'], columns='layer', values='tb20_net')
    log('  gross:')
    log(E.fmt_tbl(piv_g[['RAW', 'MOM', 'FULL_TX', 'BASE5']], 4))
    log('  net(30bp):')
    log(E.fmt_tbl(piv_n[['RAW', 'MOM', 'FULL_TX', 'BASE5']], 4))

    log('=' * 78)
    log('4) §23 Baseline 对照（RAW mean_ic，全部 Entry × Horizon）')
    b = df[df['signal'].isin(E.BASE_COLS) & (df['layer'] == 'RAW')]
    log(E.fmt_tbl(b.pivot_table(index=['entry', 'horizon'], columns='signal',
                                values='mean_ic'), 4))
    log('  H-EARN-POST 候选信号 RAW mean_ic')
    h = df[df['signal'].isin(E.SIG_COLS) & (df['layer'] == 'RAW')]
    log(E.fmt_tbl(h.pivot_table(index=['entry', 'horizon'], columns='signal',
                                values='mean_ic'), 4))
    log('  已存 out/h_earn_post_incremental.csv  %d 行' % len(df))
    log('=' * 78)
    log('DONE')
    log.save()


if __name__ == '__main__':
    main()
