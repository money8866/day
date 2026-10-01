# -*- coding: utf-8 -*-
"""H-RBP-01 step 3 -- arm statistics and the incremental-alpha comparisons.

Reads the per-trade table produced by hrbp_signals.py and reports, for every
stored arm:

    per horizon (T+3 / T+5 / T+10 / T+20):
        N, mean, median, win rate, profit factor, average win / loss,
        payoff ratio, max drawdown, cost ladder 0/10/20/30/50bp
    plus the year x arm, phase x arm, regime x arm and leave-top-k% tables.

Nothing here chooses a parameter: every cell reported is pre-registered.
"""
import os

import numpy as np
import pandas as pd

from hrbp_common import (PREREG_H, DATA, Log, cluster_boot_mean,
                         cluster_boot_diff, block_boot_mean, tail_table,
                         portfolio_mdd, save_csv, save_json)

HOR = PREREG_H['horizons']
PH = PREREG_H['primary_horizon']
COSTS = PREREG_H['cost_bp']
C30 = PREREG_H['primary_cost_bp']
B = PREREG_H['boot_B']
SEED = PREREG_H['seed']

ARMS = ['A_RANDOM', 'B_HVE_RANDOM', 'C_BAND_R1', 'C_BAND_R2', 'C_BAND_R3',
        'C_BAND_R4', 'C_ALL_3PCT', 'CF2_RAND_AFTER_RETR', 'D_PRIMARY',
        'D_SIGNAL_STRUCT', 'D_STRUCT_S1', 'D_STRUCT_S2', 'D_STRUCT_S4',
        'D_RS1', 'D_RS3', 'D_VOLCONF']

LBL = {'A_RANDOM': 'A Random-Event', 'B_HVE_RANDOM': 'B HVE-Random',
       'C_BAND_R1': 'C R1 3-5%', 'C_BAND_R2': 'C R2 5-8%',
       'C_BAND_R3': 'C R3 8-12%', 'C_BAND_R4': 'C R4 12-15%',
       'C_ALL_3PCT': 'C all >=3%', 'CF2_RAND_AFTER_RETR': 'CF2 Rnd-after-Retr',
       'D_PRIMARY': 'D Primary', 'D_SIGNAL_STRUCT': 'D struct@signal',
       'D_STRUCT_S1': 'D S1 MA20', 'D_STRUCT_S2': 'D S2 event-low',
       'D_STRUCT_S4': 'D S4 swing-low', 'D_RS1': 'D RS1 3D',
       'D_RS3': 'D RS3 MA20', 'D_VOLCONF': 'D +volconf'}

DIFFS = [('A_RANDOM', 'B_HVE_RANDOM', 'B - A   (event anchor)'),
         ('B_HVE_RANDOM', 'C_BAND_R1', 'C - B   (retracement)'),
         ('C_BAND_R1', 'D_PRIMARY', 'D - C   (structure+re-strength)'),
         ('A_RANDOM', 'C_BAND_R1', 'C - A'),
         ('A_RANDOM', 'D_PRIMARY', 'D - A'),
         ('B_HVE_RANDOM', 'C_ALL_3PCT', 'C(3%) - B'),
         ('C_BAND_R1', 'CF2_RAND_AFTER_RETR', 'CF2 - C (time vs touch)'),
         ('C_BAND_R1', 'C_BAND_R2', 'R2 - R1 (depth)'),
         ('D_PRIMARY', 'D_VOLCONF', 'D volconf - D'),
         ('D_SIGNAL_STRUCT', 'D_PRIMARY', 'struct@signal - struct@retrace')]


def boot_p(ra, ma, rb, mb, rng):
    """Cluster-bootstrap two-sided p for mean(a) - mean(b) == 0."""
    ra, ma = np.asarray(ra, 'float64'), np.asarray(ma)
    rb, mb = np.asarray(rb, 'float64'), np.asarray(mb)
    oa, ob = np.isfinite(ra), np.isfinite(rb)
    ra, ma, rb, mb = ra[oa], ma[oa], rb[ob], mb[ob]
    if ra.size < 30 or rb.size < 30:
        return np.nan
    uq = np.union1d(np.unique(ma), np.unique(mb))
    pos = {v: i for i, v in enumerate(uq)}
    k = len(uq)
    ka = np.array([pos[v] for v in ma])
    kb = np.array([pos[v] for v in mb])
    ca = np.bincount(ka, minlength=k).astype('float64')
    cb = np.bincount(kb, minlength=k).astype('float64')
    sa = np.bincount(ka, weights=ra, minlength=k)
    sb = np.bincount(kb, weights=rb, minlength=k)
    draws = rng.integers(0, k, size=(B, k))
    ac = ca[draws].sum(axis=1)
    bc = cb[draws].sum(axis=1)
    asum = sa[draws].sum(axis=1)
    bsum = sb[draws].sum(axis=1)
    d = (np.where(ac > 0, asum / np.maximum(ac, 1e-9), np.nan)
         - np.where(bc > 0, bsum / np.maximum(bc, 1e-9), np.nan))
    d = d[np.isfinite(d)]
    if d.size == 0:
        return np.nan
    return float(2.0 * min((d <= 0).mean(), (d >= 0).mean()))


def stats_block(r, months, entry_idx, dates, h):
    """All pre-registered statistics for one arm at one horizon."""
    x = np.asarray(r, 'float64')
    ok = np.isfinite(x)
    n = int(ok.sum())
    out = {'n': n}
    if n == 0:
        return out
    xv = x[ok]
    mean, lo, hi = cluster_boot_mean(xv, np.asarray(months)[ok], B=B, seed=SEED)
    bmean, blo, bhi = block_boot_mean(xv, block=20, B=B, seed=SEED)
    out.update({'mean': mean, 'ci_lo': lo, 'ci_hi': hi,
                'block_lo': blo, 'block_hi': bhi,
                'median': float(np.median(xv)),
                'win_rate': float((xv > 0).mean())})
    w = xv[xv > 0]
    l = xv[xv <= 0]
    out['avg_win'] = float(w.mean()) if w.size else np.nan
    out['avg_loss'] = float(l.mean()) if l.size else np.nan
    out['payoff'] = (abs(out['avg_win'] / out['avg_loss'])
                     if l.size and out['avg_loss'] not in (0.0, np.nan) else np.nan)
    for c in COSTS:
        v = xv - c / 10000.0
        out['net%02d_mean' % c] = float(v.mean())
        out['net%02d_win' % c] = float((v > 0).mean())
    c30 = xv - C30 / 10000.0
    gp = float(c30[c30 > 0].sum())
    gl = float(c30[c30 <= 0].sum())
    out['pf30'] = (gp / abs(gl)) if gl < 0 else np.inf
    mdd, curve = portfolio_mdd(x - C30 / 10000.0, entry_idx, h, dates)
    out['mdd30'] = mdd
    out['curve_end'] = curve
    out['std'] = float(xv.std(ddof=1)) if n > 1 else np.nan
    out['t_stat'] = (mean / (out['std'] / np.sqrt(n))
                     if out['std'] and n > 1 else np.nan)
    return out


def main():
    lg = Log('hrbp_analyze')
    lg('H-RBP-01 arm statistics   horizons=%s  primary=%d'
       % (list(HOR), PH))
    lg.sep('=')

    tr = pd.read_parquet(os.path.join(DATA, 'hrbp_trades.parquet'))
    dates = None
    import hve_common as H
    g = H.build_grid(lg=None, use_cache=True)
    dates = g['dates']
    tr = tr[tr['entry_idx'] >= 0].reset_index(drop=True)
    lg('trades with a fill: %d / %d' % (len(tr), tr['arm'].value_counts().sum()))
    dv = {a: tr[tr['arm'] == a] for a in ARMS}

    # ---------------- 1. arm x horizon statistics -----------------------
    rows = []
    for a in ARMS:
        d = dv[a]
        if len(d) == 0:
            continue
        for h in HOR:
            s = stats_block(d['r%d' % h], d['month'], d['entry_idx'],
                            dates, h)
            s.update({'arm': a, 'label': LBL[a], 'horizon': 'T+%d' % h})
            rows.append(s)
    st = pd.DataFrame(rows)
    cols = ['arm', 'label', 'horizon', 'n', 'mean', 'ci_lo', 'ci_hi',
            'block_lo', 'block_hi', 'median', 'win_rate', 'avg_win',
            'avg_loss', 'payoff', 'std', 't_stat'] + \
           ['net%02d_mean' % c for c in COSTS] + \
           ['net%02d_win' % c for c in COSTS] + ['pf30', 'mdd30', 'curve_end']
    st = st[cols]
    save_csv(st, '20_hrbp_arm_stats.csv')
    lg('20_hrbp_arm_stats.csv  %d rows' % len(st))
    lg.sep('-')
    lg('%-24s %8s %8s %8s %8s %8s %8s' %
       ('arm', 'N', 'T+3', 'T+5', 'T+10', 'T+20', 'Net30@10'))
    for a in ARMS:
        s = st[st['arm'] == a].set_index('horizon')
        if len(s) == 0:
            continue
        lg('%-24s %8d %8s %8s %8s %8s %8s' %
           (LBL[a], int(s.loc['T+3', 'n']),
            '%.4f' % s.loc['T+3', 'mean'], '%.4f' % s.loc['T+5', 'mean'],
            '%.4f' % s.loc['T+10', 'mean'], '%.4f' % s.loc['T+20', 'mean'],
            '%.4f' % s.loc['T+10', 'net30_mean']))

    # ---------------- 2. incremental alpha ------------------------------
    lg.sep('=')
    lg('incremental alpha (cluster bootstrap by calendar month, B=%d)' % B)
    rng = np.random.default_rng(SEED + 101)
    inc = []
    for a, b, note in DIFFS:
        da, db = dv[a], dv[b]
        for h in HOR:
            ra = da['r%d' % h].to_numpy()
            rb = db['r%d' % h].to_numpy()
            # labels read "second - first", so feed (b, a) to the differ.
            obs, lo, hi = cluster_boot_diff(rb, db['month'].to_numpy(),
                                            ra, da['month'].to_numpy(),
                                            B=B, seed=SEED)
            p = boot_p(rb, db['month'].to_numpy(), ra, da['month'].to_numpy(),
                       rng)
            inc.append({'comparison': note, 'a': a, 'b': b,
                        'horizon': 'T+%d' % h, 'n_a': len(da), 'n_b': len(db),
                        'mean_a': float(np.nanmean(ra)) if len(ra) else np.nan,
                        'mean_b': float(np.nanmean(rb)) if len(rb) else np.nan,
                        'diff': obs, 'ci_lo': lo, 'ci_hi': hi, 'p_boot': p,
                        'sig': bool(np.isfinite(lo) and np.isfinite(hi)
                                    and (lo > 0 or hi < 0))})
    incdf = pd.DataFrame(inc)
    save_csv(incdf, '21_hrbp_incremental.csv')
    lg('%-34s %5s %9s %9s %9s %8s' %
       ('comparison', 'T+h', 'diff', 'ci_lo', 'ci_hi', 'p'))
    for r in incdf.to_dict('records'):
        lg('%-34s %5s %9.4f %9.4f %9.4f %8.4f%s' %
           (r['comparison'], r['horizon'], r['diff'], r['ci_lo'], r['ci_hi'],
            r['p_boot'], '  *' if r['sig'] else ''))

    # ---------------- 3. cost ladder (primary horizon) ------------------
    lad = []
    for a in ARMS:
        d = dv[a]
        if len(d) == 0:
            continue
        r = d['r%d' % PH].to_numpy()
        for c in COSTS:
            v = r[np.isfinite(r)] - c / 10000.0
            gp = float(v[v > 0].sum())
            gl = float(v[v <= 0].sum())
            lad.append({'arm': a, 'label': LBL[a], 'cost_bp': c, 'n': v.size,
                        'mean': float(v.mean()),
                        'win_rate': float((v > 0).mean()),
                        'pf': (gp / abs(gl)) if gl < 0 else np.inf})
    save_csv(pd.DataFrame(lad), '22_hrbp_cost_ladder.csv')

    # ---------------- 4. tail dependence ---------------------------------
    tl = []
    for a in ARMS:
        d = dv[a]
        if len(d) == 0:
            continue
        t10 = tail_table(d['r%d' % PH].to_numpy() - C30 / 10000.0)
        t10.update({'arm': a, 'label': LBL[a]})
        tl.append(t10)
    tt = pd.DataFrame(tl)
    save_csv(tt, '23_hrbp_tail.csv')
    lg.sep('-')
    lg('%-24s %10s %10s %10s %10s' %
       ('arm (net30 @T+10)', 'full', 'drop1%', 'drop5%', 'tail5%share'))
    for r in tl:
        lg('%-24s %10.4f %10.4f %10.4f %10.3f' %
           (LBL[r['arm']], r['mean_full'], r['leave_top_1pct'],
            r['leave_top_5pct'], r['tail_share_5pct']))

    # ---------------- 5. year / phase / regime ---------------------------
    oos = []
    for a in ARMS:
        d = dv[a]
        if len(d) == 0:
            continue
        for h in HOR:
            for key, lab in (('year', 'year'), ('phase', 'phase'),
                             ('regime', 'regime')):
                for v, sub in d.groupby(key):
                    r = sub['r%d' % h].to_numpy()
                    net = r[np.isfinite(r)] - C30 / 10000.0
                    if net.size == 0:
                        continue
                    gp = float(net[net > 0].sum())
                    gl = float(net[net <= 0].sum())
                    oos.append({'arm': a, 'label': LBL[a], 'horizon': 'T+%d' % h,
                                'group_by': lab, 'group': v, 'n': net.size,
                                'mean': float(np.nanmean(r)),
                                'mean_net30': float(net.mean()),
                                'win_rate': float((net > 0).mean()),
                                'pf30': (gp / abs(gl)) if gl < 0 else np.inf})
    oosdf = pd.DataFrame(oos)
    save_csv(oosdf, 'H_RBP_01_OOS.csv')
    lg('H_RBP_01_OOS.csv  %d rows' % len(oosdf))

    lg.sep('-')
    lg('yearly mean net30 @T+10')
    yr = oosdf[(oosdf['group_by'] == 'year') & (oosdf['horizon'] == 'T+10')]
    piv = yr.pivot_table(index='group', columns='arm',
                         values='mean_net30')
    show = [a for a in ('A_RANDOM', 'B_HVE_RANDOM', 'C_BAND_R1', 'D_PRIMARY')
            if a in piv.columns]
    lg('%-8s %10s %10s %10s %10s' % tuple(['year'] + show))
    for y, row in piv.iterrows():
        lg('%-8s %10.4f %10.4f %10.4f %10.4f' %
           tuple([y] + [row.get(a, np.nan) for a in show]))

    lg.sep('-')
    lg('regime mean net30 @T+10')
    rg = oosdf[(oosdf['group_by'] == 'regime') & (oosdf['horizon'] == 'T+10')]
    for a in show:
        s = rg[rg['arm'] == a]
        lg('%-16s ' % LBL[a] + '  '.join(
            '%s n=%d %+.4f win=%.3f' % (r['group'], r['n'], r['mean_net30'],
                                        r['win_rate'])
            for r in s.to_dict('records')))

    # ---------------- 6. persistence into the OOS phase ------------------
    lg.sep('=')
    lg('phase split @T+10 (mean, net30)')
    ph = oosdf[(oosdf['group_by'] == 'phase') & (oosdf['horizon'] == 'T+10')]
    for a in show:
        s = ph[ph['arm'] == a]
        lg('%-16s ' % LBL[a] + '  '.join(
            '%s n=%d %+.4f (net %+.4f)' % (r['group'], r['n'], r['mean'],
                                           r['mean_net30'])
            for r in s.to_dict('records')))

    n_tests = int(len(incdf)) + int(len(st)) + int(len(lad)) + int(len(oosdf))
    save_json({'n_tests': n_tests, 'boot_B': B, 'seed': SEED,
               'arms': ARMS, 'comparisons': [d[2] for d in DIFFS],
               'note': 'every cell is pre-registered; no parameter chosen here'},
              '20_hrbp_analyze_meta.json')
    lg('n_tests recorded %d' % n_tests)
    lg('done')


if __name__ == '__main__':
    main()
