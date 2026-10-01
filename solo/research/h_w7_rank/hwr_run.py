# -*- coding: utf-8 -*-
"""H-W7-RANK-01 -- arms, lattice, randomised null.

Reads the two frozen ledgers only.  No threshold of W7 or HVT is touched.
Everything comes out of PREREG_R, which was frozen in H_W7_RANK_01_SPEC.md
before this file was run.

Outputs (data/)
  H_W7_RANK_01_ARMS.csv         arm descriptives (incl. the HVT positive control)
  H_W7_RANK_01_LATTICE.csv      N x key grid -- all pre-registered, none selected
  H_W7_RANK_01_NULL.csv         RANDOM-N null distribution vs the ranked arm
  H_W7_RANK_01_OOS.csv          year / phase for the primary config
  H_W7_RANK_01_REGIME.csv       regime split for the primary config
  H_W7_RANK_01_TAIL.csv         leave-top-k% for the primary config
"""
import numpy as np
import pandas as pd

import hwr_common as R

P = R.PREREG_R
HZ = list(P['horizons'])
H0 = P['primary_horizon']
N_PRIM = P['primary_n']
KEY_PRIM = P['primary_key']


def desc_row(g, s, tag):
    row = {'arm': tag, 'n': len(s), 'n_day': int(s['dec_date'].nunique())}
    for h in HZ:
        r = R.rets_of(g, s, h)
        d = R.A.desc(r)
        row['mean_%d' % h] = d['mean']
        row['median_%d' % h] = d['median']
        row['win_%d' % h] = d['win']
        row['pf_%d' % h] = d['pf']
        row['net30_%d' % h] = float(np.nanmean(R.cost_net(r)))
    return row


def cmp_row(g, sa, sb, key, tag_a, tag_b, extra=None):
    row = {'config': key, 'arm_a': tag_a, 'arm_b': tag_b,
           'n_a': len(sa), 'n_b': len(sb)}
    if extra:
        row.update(extra)
    ra_all, rb_all = {}, {}
    for h in HZ:
        ra = R.rets_of(g, sa, h)
        rb = R.rets_of(g, sb, h)
        ra_all[h], rb_all[h] = ra, rb
        if np.isfinite(ra).sum() < P['min_n'] or np.isfinite(rb).sum() < P['min_n']:
            row['delta_%d' % h] = np.nan
            continue
        o = R.compare(ra, sa['month'].to_numpy(), rb, sb['month'].to_numpy())
        row['mean_a_%d' % h] = float(np.nanmean(ra))
        row['mean_b_%d' % h] = float(np.nanmean(rb))
        row['delta_%d' % h] = o['obs']
        row['ci_lo_%d' % h] = o['lo']
        row['ci_hi_%d' % h] = o['hi']
        row['p_%d' % h] = o['p_two']
        row['net30_%d' % h] = o['obs'] - P['primary_cost_bp'] / 10000.0
    return row, ra_all, rb_all


def null_for(g, d, n, lg, tag, B=None):
    """B uniform draws of n rows per decision day; identical pipeline."""
    B = P['B_null'] if B is None else B
    rng = np.random.default_rng(P['seed'] + 1000 + n)
    acc = {h: np.full(B, np.nan) for h in HZ}
    ns = np.zeros(B, dtype='int64')
    for b in range(B):
        m = R.random_n_mask(d, n, rng)
        s = R.arm_sample(d, m)
        if len(s) == 0:
            continue
        ns[b] = len(s)
        for h in HZ:
            acc[h][b] = float(np.nanmean(R.rets_of(g, s, h)))
        if lg and (b + 1) % 250 == 0:
            lg('    %s draw %d/%d' % (tag, b + 1, B))
    return acc, ns


def main():
    lg = R.Log('hwr_run')
    lg('H-W7-RANK-01  (parent: H-ALPHA-SOURCE-01)  -- arms + lattice + null')
    g = R.C.grid(lg=lg)
    R.C.eligibility(lg=lg)
    reg = R.regime_of(g)
    lg('regime sessions %s' % {k: int((reg == k).sum()) for k in ('BULL', 'RANGE', 'BEAR')})

    lg.sep()
    w7 = R.load_w7(lg)
    hvt = R.load_hvt(lg)
    lg('W7  actionable/day  mean %.2f  median %.0f  max %d'
       % (w7.groupby('dec_date').size().mean(),
          w7.groupby('dec_date').size().median(), w7.groupby('dec_date').size().max()))
    lg('HVT actionable/day  mean %.2f  median %.0f  max %d'
       % (hvt.groupby('dec_date').size().mean(),
          hvt.groupby('dec_date').size().median(), hvt.groupby('dec_date').size().max()))

    # ---------------- arms: W7 ---------------------------------------
    lg.sep()
    lg('W7 arms')
    allm = np.ones(len(w7), dtype=bool)
    base = R.arm_sample(w7, allm, lg, 'W7 BASE')
    m_rank = R.top_n_mask(w7, KEY_PRIM, N_PRIM)
    ranked = R.arm_sample(w7, m_rank, lg, 'W7 RANKED  n=%d %s' % (N_PRIM, KEY_PRIM))
    comp = R.arm_sample(w7, ~m_rank, lg, 'W7 COMPLEMENT')

    # ---------------- arms: HVT positive control ---------------------
    lg.sep()
    lg('HVT positive control (its own Top-3 already exists)')
    hall = np.ones(len(hvt), dtype=bool)
    hbase = R.arm_sample(hvt, hall, lg, 'HVT BASE')
    hm = hvt['top3'].to_numpy()
    hrank = R.arm_sample(hvt, hm, lg, 'HVT RANKED (own Top-3)')
    hcomp = R.arm_sample(hvt, ~hm, lg, 'HVT COMPLEMENT')

    # ---------------- ARMS.csv ---------------------------------------
    rows = []
    for tag, s in (('W7 BASE', base), ('W7 RANKED', ranked), ('W7 COMPLEMENT', comp),
                   ('HVT BASE', hbase), ('HVT RANKED', hrank),
                   ('HVT COMPLEMENT', hcomp)):
        rows.append(desc_row(g, s, tag))
    arms = pd.DataFrame(rows)
    R.save_csv(arms, 'H_W7_RANK_01_ARMS.csv')
    lg.sep()
    lg('ARMS')
    for _, r in arms.iterrows():
        lg('  %-16s n=%5d  T+10 mean %+0.4f  net30 %+0.4f  win %.3f  pf %.2f'
           % (r['arm'], r['n'], r['mean_10'], r['net30_10'], r['win_10'], r['pf_10']))

    # ---------------- headline comparisons ---------------------------
    lg.sep()
    lg('Headline (W7):  RANKED vs BASE / vs COMPLEMENT')
    head = []
    for tag_b, sb in (('BASE', base), ('COMPLEMENT', comp)):
        row, _, _ = cmp_row(g, ranked, sb, 'W7 PRIMARY n=3 (exec,eq)', 'RANKED', tag_b)
        head.append(row)
        lg('  RANKED - %-11s T+10 %+0.4f [%+0.4f,%+0.4f] p %.3f  net30 %+0.4f'
           % (tag_b, row['delta_10'], row['ci_lo_10'], row['ci_hi_10'],
              row['p_10'], row['net30_10']))
    lg.sep()
    lg('Headline (HVT control):  RANKED vs BASE / vs COMPLEMENT')
    hctl = []
    for tag_b, sb in (('BASE', hbase), ('COMPLEMENT', hcomp)):
        row, _, _ = cmp_row(g, hrank, sb, 'HVT own Top-3', 'RANKED', tag_b)
        hctl.append(row)
        lg('  RANKED - %-11s T+10 %+0.4f [%+0.4f,%+0.4f] p %.3f  net30 %+0.4f'
           % (tag_b, row['delta_10'], row['ci_lo_10'], row['ci_hi_10'],
              row['p_10'], row['net30_10']))

    # ---------------- lattice (all pre-registered, none selected) ----
    lg.sep()
    lg('LATTICE: N x key, W7, ranked vs BASE (all reported, none selected)')
    lat = []
    for n in P['lattice_n']:
        for key in P['lattice_keys']:
            kname = '+'.join(key)
            m = R.top_n_mask(w7, key, n)
            s = R.arm_sample(w7, m)
            if len(s) < P['min_n']:
                continue
            row, _, _ = cmp_row(g, s, base, kname, 'RANKED n=%d' % n, 'BASE',
                                extra={'n_setting': n, 'key': kname})
            lat.append(row)
    latdf = pd.DataFrame(lat)
    R.save_csv(latdf, 'H_W7_RANK_01_LATTICE.csv')
    top = latdf.sort_values('delta_10', ascending=False)
    lg('  best 3 by T+10 delta (reported for completeness ONLY -- not selected):')
    for _, r in top.head(3).iterrows():
        lg('    n=%-2d %-14s  T+10 %+0.4f  net30 %+0.4f  n_arm %d'
           % (r['n_setting'], r['key'], r['delta_10'], r['net30_10'], r['n_a']))
    lg('  primary config n=%d %s  T+10 %+0.4f  net30 %+0.4f  n_arm %d'
       % (N_PRIM, '+'.join(KEY_PRIM),
          head[0]['delta_10'], head[0]['net30_10'], head[0]['n_a']))

    # ---------------- RANDOM-N null ----------------------------------
    lg.sep()
    lg('RANDOM-N null (same days, same N, order destroyed)')
    nul = []
    for n in P['lattice_n']:
        acc, ns = null_for(g, w7, n, lg, 'N=%d' % n)
        m = R.top_n_mask(w7, KEY_PRIM, n)
        s = R.arm_sample(w7, m)
        row = {'n_setting': n, 'n_arm': len(s), 'n_random_mean': float(ns.mean())}
        for h in HZ:
            obs = float(np.nanmean(R.rets_of(g, s, h)))
            v = acc[h][np.isfinite(acc[h])]
            row['obs_%d' % h] = obs
            row['null_mean_%d' % h] = float(v.mean()) if v.size else np.nan
            row['null_med_%d' % h] = float(np.median(v)) if v.size else np.nan
            row['null_lo_%d' % h] = float(np.percentile(v, 2.5)) if v.size else np.nan
            row['null_hi_%d' % h] = float(np.percentile(v, 97.5)) if v.size else np.nan
            row['excess_%d' % h] = obs - row['null_mean_%d' % h]
            row['p_excess_%d' % h] = float((v >= obs).mean()) if v.size else np.nan
            row['net30_excess_%d' % h] = row['excess_%d' % h] - P['primary_cost_bp'] / 10000.0
        nul.append(row)
        lg('  N=%-2d  obs T+10 %+0.4f  null mean %+0.4f [%+0.4f,%+0.4f]  '
           'excess %+0.4f  p %.3f'
           % (n, row['obs_10'], row['null_mean_10'], row['null_lo_10'],
              row['null_hi_10'], row['excess_10'], row['p_excess_10']))
    nuldf = pd.DataFrame(nul)
    R.save_csv(nuldf, 'H_W7_RANK_01_NULL.csv')

    # ---------------- HVT control null -------------------------------
    lg.sep()
    lg('HVT control null (same decision days, 3 random names of 16)')
    hacc, hns = null_for(g, hvt, 3, lg, 'HVT N=3')
    hrow = {'arm': 'HVT RANKED (own Top-3)', 'n': len(hrank),
            'n_random_mean': float(hns.mean())}
    for h in HZ:
        obs = float(np.nanmean(R.rets_of(g, hrank, h)))
        v = hacc[h][np.isfinite(hacc[h])]
        hrow['obs_%d' % h] = obs
        hrow['null_mean_%d' % h] = float(v.mean()) if v.size else np.nan
        hrow['null_lo_%d' % h] = float(np.percentile(v, 2.5)) if v.size else np.nan
        hrow['null_hi_%d' % h] = float(np.percentile(v, 97.5)) if v.size else np.nan
        hrow['excess_%d' % h] = obs - hrow['null_mean_%d' % h]
        hrow['p_excess_%d' % h] = float((v >= obs).mean()) if v.size else np.nan
    lg('  HVT obs T+10 %+0.4f  null mean %+0.4f [%+0.4f,%+0.4f]  excess %+0.4f  p %.3f'
       % (hrow['obs_10'], hrow['null_mean_10'], hrow['null_lo_10'],
          hrow['null_hi_10'], hrow['excess_10'], hrow['p_excess_10']))
    R.save_csv(pd.DataFrame([hrow]), 'H_W7_RANK_01_NULL_HVT.csv')

    # ---------------- OOS / REGIME / TAIL ----------------------------
    lg.sep()
    lg('OOS by year (primary config) and per-system split')
    oos = []
    for h in HZ:
        for (ta, sa, tb, sb) in (('RANKED', ranked, 'BASE', base),
                                 ('RANKED', ranked, 'COMPLEMENT', comp)):
            for yy in sorted(set(w7['dec_date'].astype(str).str[:4])):
                ya = sa[sa['year'] == int(yy)]
                yb = sb[sb['year'] == int(yy)]
                if len(ya) < P['min_n'] or len(yb) < P['min_n']:
                    continue
                o = R.compare(R.rets_of(g, ya, h), ya['month'].to_numpy(),
                              R.rets_of(g, yb, h), yb['month'].to_numpy())
                oos.append({'pair': '%s-%s' % (ta, tb), 'horizon': h, 'year': int(yy),
                            'n_a': len(ya), 'n_b': len(yb), 'delta': o['obs'],
                            'ci_lo': o['lo'], 'ci_hi': o['hi'], 'p': o['p_two'],
                            'net30': o['obs'] - P['primary_cost_bp'] / 10000.0})
    oosdf = pd.DataFrame(oos)
    R.save_csv(oosdf, 'H_W7_RANK_01_OOS.csv')
    for _, r in oosdf[oosdf['horizon'] == H0].iterrows():
        lg('  %-18s %d  n=%4d/%4d  delta %+0.4f  net30 %+0.4f  p %.3f'
           % (r['pair'], r['year'], r['n_a'], r['n_b'], r['delta'], r['net30'], r['p']))

    lg.sep()
    lg('Regime split (entry session, T+10)')
    regrows = []
    for (ta, sa, tb, sb) in (('RANKED', ranked, 'BASE', base),
                             ('RANKED', ranked, 'COMPLEMENT', comp)):
        ra_ = reg[sa['entry_idx'].to_numpy()]
        rb_ = reg[sb['entry_idx'].to_numpy()]
        for k in ('BULL', 'RANGE', 'BEAR'):
            ya, yb = sa[ra_ == k], sb[rb_ == k]
            if len(ya) < P['min_n'] or len(yb) < P['min_n']:
                continue
            o = R.compare(R.rets_of(g, ya, H0), ya['month'].to_numpy(),
                          R.rets_of(g, yb, H0), yb['month'].to_numpy())
            regrows.append({'pair': '%s-%s' % (ta, tb), 'regime': k,
                            'n_a': len(ya), 'n_b': len(yb), 'delta': o['obs'],
                            'ci_lo': o['lo'], 'ci_hi': o['hi'], 'p': o['p_two']})
            lg('  %-18s %-6s n=%4d/%4d  delta %+0.4f  p %.3f'
               % (r['pair'] if False else '%s-%s' % (ta, tb), k,
                  len(ya), len(yb), o['obs'], o['p_two']))
    R.save_csv(pd.DataFrame(regrows), 'H_W7_RANK_01_REGIME.csv')

    lg.sep()
    lg('Tail (leave-top-k%) on the paired subset, T+10')
    tailrows = []
    for (ta, sa, tb, sb) in (('RANKED', ranked, 'BASE', base),
                             ('RANKED', ranked, 'COMPLEMENT', comp)):
        keym = pd.MultiIndex.from_arrays([sa['ts_code'], sa['ev_date']])
        kb = pd.MultiIndex.from_arrays([sb['ts_code'], sb['ev_date']])
        common = keym.intersection(kb)
        ya = sa.set_index(['ts_code', 'ev_date']).loc[common]
        yb = sb.set_index(['ts_code', 'ev_date']).loc[common]
        ra, rb = R.rets_of(g, ya.reset_index(), H0), R.rets_of(g, yb.reset_index(), H0)
        tt = R.tail_table(ra)
        tt.update({'pair': '%s-%s' % (ta, tb), 'n_paired': int(len(common))})
        tailrows.append(tt)
        lg('  %-18s n=%4d full %+0.4f  leave5%% %+0.4f  leave10%% %+0.4f'
           % ('%s-%s' % (ta, tb), len(common), tt['mean_full'],
              tt['leave_top_5pct'], tt['leave_top_10pct']))
    R.save_csv(pd.DataFrame(tailrows), 'H_W7_RANK_01_TAIL.csv')

    lg.sep()
    lg('done')


if __name__ == '__main__':
    main()
