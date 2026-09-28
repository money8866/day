# -*- coding: utf-8 -*-
"""hep_analyze: H-EARN-POST Level 1/2 分析

覆盖预注册条款
  §19 预测周期 T+5/10/20/60（Primary = T+20）
  §20 横截面 Rank IC：Mean / Median / Std / ICIR / Positive Ratio / Newey-West t
  §21 Top-Bottom：T10/T20/Middle/B20/B10 与 Gross / Net
  §22 Winner-Loser：AUC / Effect Size / Std Gap / Rank Separation / Precision@10%
  §25 事件后路径（自 D0 起的累计超额收益）
  §35 时间稳定性：TRAIN / VALID / OOS / LIVE-LIKE 分期

隔离声明（§1）：只读 hep_events.parquet，不触碰任何生产模块。
输出：out/h_earn_post_ic.csv / _spread.csv / _auc.csv / _path.csv
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hep_common import (OUTD, PREREG, ENTRIES, HORIZONS, MIN_XS, Log)
import hep_engine as E

log = Log('_hep_analyze.txt')
PRIMARY_SIG = PREREG['primary_signal']
PHASES = list(PREREG['phases'])
COST_BPS = 30.0


# ------------------------------------------------------------------ 自检
def selftest(d):
    log('=' * 78)
    log('0) 向量化 vs 逐 cohort 一致性自检（E=20 / SIG_RESID / T+20）')
    de = d[d['E'] == 20]
    a = E.ic_series(de, PRIMARY_SIG, 'ex_20')
    b = E.ic_series_loop(de, PRIMARY_SIG, 'ex_20')
    c = a.reindex(b.index).values
    d1 = float(np.nanmax(np.abs(c - b.values))) if len(b) else np.nan
    ta, tb, _ = E.bucket_means(de, PRIMARY_SIG, 'ex_20')
    la, lb, _ = E.bucket_means_loop(de, PRIMARY_SIG, 'ex_20')
    ix = ta.index.intersection(la.index)
    d2 = float(np.nanmax(np.abs(ta.reindex(ix).values - la.reindex(ix).values))) if len(ix) else np.nan
    d3 = float(np.nanmax(np.abs(tb.reindex(ix).values - lb.reindex(ix).values))) if len(ix) else np.nan
    aa = E.auc_block(de, PRIMARY_SIG, 'ex_20')
    ab = E.auc_block_loop(de, PRIMARY_SIG, 'ex_20')
    d4 = abs(aa['auc'] - ab['auc'])
    log('  IC      n=%d  max|diff|=%.3e' % (len(b), d1))
    log('  BUCKET  n=%d  top max|diff|=%.3e  bot max|diff|=%.3e' % (len(ix), d2, d3))
    log('  AUC     fast=%.6f  loop=%.6f  |diff|=%.3e' % (aa['auc'], ab['auc'], d4))
    # §19 口径一致性：ex_T 应等于 Entry 起点累计超额 pex_T
    for T in (5, 20):
        v = de[['ex_%d' % T, 'pex_%d' % T]].dropna()
        log('  ex_%d vs pex_%d  corr=%.6f  n=%d' % (T, T, v.corr().iloc[0, 1], len(v)))
    ok = (np.isfinite(d1) and d1 < 1e-8 and np.isfinite(d2) and d2 < 1e-10
          and np.isfinite(d3) and d3 < 1e-10 and d4 < 1e-6)
    log('  自检结果: %s' % ('PASS' if ok else 'FAIL'))
    return ok


# ------------------------------------------------------------------ §20 Rank IC
def ic_by_phase(de, sig, ret, T):
    ics = E.ic_series(de, sig, ret)
    if ics.empty:
        return {}
    ky = de.groupby('sig_k')['rep_year'].first()
    yy = ky.reindex(ics.index)
    out = {'ALL': E.ic_row(ics, T)}
    for ph, y0, y1 in PHASES:
        out[ph] = E.ic_row(ics[yy.between(y0, y1).values], T)
    return out


def run_ic(d, sigs):
    log('=' * 78)
    log('1) §20 横截面 Rank IC（E20/25/30 × T5/10/20/60 × 分期）')
    rows = []
    for e in ENTRIES:
        de = d[d['E'] == e]
        for sig in sigs:
            for T in HORIZONS:
                blk = ic_by_phase(de, sig, 'ex_%d' % T, T)
                for ph, st in blk.items():
                    r = dict(entry='E%d' % e, signal=sig, horizon='T+%d' % T, phase=ph)
                    r.update(st)
                    rows.append(r)
    df = pd.DataFrame(rows)
    cols = ['entry', 'signal', 'horizon', 'phase', 'n', 'mean_ic', 'med_ic',
            'ic_std', 'icir', 'ic_t', 'pos_ratio']
    df = df[cols]
    df.to_csv(os.path.join(OUTD, 'h_earn_post_ic.csv'), index=False, encoding='utf-8-sig')
    p = df[(df['phase'] == 'ALL') & (df['horizon'] == 'T+20')]
    log(E.fmt_tbl(p.set_index(['entry', 'signal'])[['n', 'mean_ic', 'icir', 'ic_t', 'pos_ratio']]))
    log('  样本外分期（Primary=T+20, 主信号 %s）:' % PRIMARY_SIG)
    q = df[(df['signal'] == PRIMARY_SIG) & (df['horizon'] == 'T+20')]
    log(E.fmt_tbl(q.set_index(['entry', 'phase'])[['n', 'mean_ic', 'icir', 'ic_t', 'pos_ratio']]))
    log('  已存 out/h_earn_post_ic.csv  %d 行' % len(df))
    return df


# ------------------------------------------------------------------ §21 Top-Bottom
def spread_block(de, sig, ret, phase_name):
    q = E.quintile_means(de, sig, ret)
    if q.empty:
        return None
    c = COST_BPS / 1e4
    tb10 = q['t10'] - q['b10']
    tb20 = q['t20'] - q['b20']
    return dict(
        entry=None, signal=sig, horizon=ret.replace('ex_', 'T+'), phase=phase_name,
        n_day=int(len(q)), n=len(q),
        t10=float(q['t10'].mean()), t20=float(q['t20'].mean()), mid=float(q['mid'].mean()),
        b20=float(q['b20'].mean()), b10=float(q['b10'].mean()),
        tb10_gross=float(tb10.mean()), tb10_net=float(tb10.mean() - 4 * c),
        tb20_gross=float(tb20.mean()), tb20_net=float(tb20.mean() - 4 * c),
        t20_net=float(q['t20'].mean() - 2 * c),
        win_ratio=float((tb20 > 0).mean()),
    )


def run_spread(d, sigs):
    log('=' * 78)
    log('2) §21 Top-Bottom 分位组合（q: T10/B10=10%%, T20/B20=20%%；成本 %dbp）' % COST_BPS)
    rows = []
    for e in ENTRIES:
        de = d[d['E'] == e]
        for sig in sigs:
            for T in HORIZONS:
                ret = 'ex_%d' % T
                for ph, y0, y1 in [('ALL', 0, 9999)] + PHASES:
                    sub = de if ph == 'ALL' else de[de['rep_year'].between(y0, y1)]
                    r = spread_block(sub, sig, ret, ph)
                    if r is None:
                        continue
                    r['entry'] = 'E%d' % e
                    rows.append(r)
    df = pd.DataFrame(rows)
    cols = ['entry', 'signal', 'horizon', 'phase', 'n_day', 't10', 't20', 'mid',
            'b20', 'b10', 'tb10_gross', 'tb10_net', 'tb20_gross', 'tb20_net',
            't20_net', 'win_ratio']
    df = df[cols]
    df.to_csv(os.path.join(OUTD, 'h_earn_post_spread.csv'), index=False, encoding='utf-8-sig')
    p = df[(df['phase'] == 'ALL') & (df['horizon'] == 'T+20')]
    log(E.fmt_tbl(p.set_index(['entry', 'signal'])[
        ['tb20_gross', 'tb20_net', 'tb10_gross', 'tb10_net', 'win_ratio']]))
    log('  已存 out/h_earn_post_spread.csv  %d 行' % len(df))
    return df


# ------------------------------------------------------------------ §22 Winner/Loser
def run_auc(d, sigs):
    log('=' * 78)
    log('3) §22 Winner/Loser 判别（Winner=前20%%，Loser=后20%%）')
    rows = []
    for e in ENTRIES:
        de = d[d['E'] == e]
        for sig in sigs:
            for T in HORIZONS:
                ret = 'ex_%d' % T
                for ph, y0, y1 in [('ALL', 0, 9999)] + PHASES:
                    sub = de if ph == 'ALL' else de[de['rep_year'].between(y0, y1)]
                    b = E.auc_block(sub, sig, ret)
                    r = dict(entry='E%d' % e, signal=sig, horizon='T+%d' % T,
                             phase=ph)
                    r.update(b)
                    rows.append(r)
    df = pd.DataFrame(rows)
    cols = ['entry', 'signal', 'horizon', 'phase', 'n', 'auc', 'eff',
            'std_gap', 'rank_sep', 'prec10']
    df = df[cols]
    df.to_csv(os.path.join(OUTD, 'h_earn_post_auc.csv'), index=False, encoding='utf-8-sig')
    p = df[(df['phase'] == 'ALL') & (df['horizon'] == 'T+20')]
    log(E.fmt_tbl(p.set_index(['entry', 'signal'])[['n', 'auc', 'eff', 'rank_sep', 'prec10']]))
    log('  已存 out/h_earn_post_auc.csv  %d 行' % len(df))
    return df


# ------------------------------------------------------------------ §25 事件后路径
def run_path(d, sigs):
    log('=' * 78)
    log('4) §25 事件后路径（Alpha 何时出现）')
    ks = list(PREREG['path_horizons'])
    rows = []
    for e in ENTRIES:
        de = d[d['E'] == e]
        for sig in sigs:
            for k in ks:
                for anchor, pre in (('D0', 'p0ex_'), ('ENTRY', 'pex_')):
                    ret = pre + str(k)
                    if ret not in de.columns:
                        continue
                    ics = E.ic_series(de, sig, ret)
                    q = E.quintile_means(de, sig, ret)
                    rows.append(dict(
                        entry='E%d' % e, signal=sig, anchor=anchor, k=k,
                        n_day=int(len(ics)),
                        mean_ic=float(ics.mean()) if len(ics) else np.nan,
                        t20=float(q['t20'].mean()) if not q.empty else np.nan,
                        tb20=float((q['t20'] - q['b20']).mean()) if not q.empty else np.nan,
                        mid=float(q['mid'].mean()) if not q.empty else np.nan,
                    ))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUTD, 'h_earn_post_path.csv'), index=False, encoding='utf-8-sig')
    for anchor in ('D0', 'ENTRY'):
        p = df[(df['signal'] == PRIMARY_SIG) & (df['anchor'] == anchor)].pivot_table(
            index='k', columns='entry', values=['mean_ic', 'tb20'])
        log('  主信号 %s 的路径（锚点=%s；mean_ic / T20-B20）:' % (PRIMARY_SIG, anchor))
        log(E.fmt_tbl(p))
    log('  已存 out/h_earn_post_path.csv  %d 行' % len(df))
    return df


def main():
    log('=' * 78)
    log('H-EARN-POST Level 1/2 分析（预注册 %s）' % PREREG['hypothesis_id'])
    d = E.load()
    log('载入 hep_events.parquet %s' % (d.shape,))
    d = E.add_features(d)
    # §40 五个基线的方向只在 TRAIN(2018–2022) 上确定后冻结
    dirs = E.freeze_dir(d, E.BASE_COLS, (2018, 2022), horizon=PREREG['primary_horizon'])
    log('基线方向冻结(TRAIN 2018–2022, T+20): %s' % {k: int(v) for k, v in dirs.items()})
    E.apply_dir(d, E.BASE_COLS, dirs)
    sigs = list(E.SIG_COLS) + E.BASE_COLS
    selftest(d)
    run_ic(d, sigs)
    run_spread(d, sigs)
    run_auc(d, sigs)
    run_path(d, sigs)
    log('=' * 78)
    log('DONE')
    log.save()


if __name__ == '__main__':
    main()
