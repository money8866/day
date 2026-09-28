# -*- coding: utf-8 -*-
"""H-EARN-PRICE-01 step 2: cross-sectional predictive tests.

  section 16  Rank IC  (raw / market-excess / industry-excess / event window)
  section 17  Incremental IC  (forecast residualised on momentum)
  section 18  Residual alpha  (Fama-MacBeth cross-sectional regression)
  section 39  Null models N1-N6
  section 40  Permutation test on the forecast rank (1000 draws)
  section 41  Multiple testing (Benjamini-Hochberg)

One row per frozen forecast event (P30, TRAINFIX/GBM).  The signal is always
the FROZEN forecast - never the realised earnings surprise.
"""
import os, sys, json
from math import erfc, erf, sqrt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hep_common import (PREREG, DATA, Log, ic_stats, bh_fdr, dummies, ols_resid)

lg = Log('hep_ic')
H_LIST = list(PREREG['horizons'])
LAG = {1: 1, 3: 1, 5: 1, 10: 2, 20: 3}
RNG = np.random.default_rng(PREREG['seed'])
DAY = 'T0'
MIN_N = int(PREREG['ic_min_n'])


def p_two(t):
    return float(erfc(abs(t) / sqrt(2))) if np.isfinite(t) else np.nan


def daily_rank_ic(df, pred, ret, min_n=MIN_N):
    sub = df[[DAY, pred, ret]].dropna()
    if len(sub) == 0:
        return pd.Series(dtype=float)
    sub = sub.assign(_p=sub.groupby(DAY)[pred].rank(),
                     _r=sub.groupby(DAY)[ret].rank())
    cnt = sub.groupby(DAY)['_p'].count()
    keep = cnt[cnt >= min_n].index
    sub = sub[sub[DAY].isin(keep)]
    out = sub.groupby(DAY).apply(
        lambda x: x['_p'].corr(x['_r']), include_groups=False)
    return out.astype(float).dropna()


def ic_row(df, pred, ret, signal, rettype, horizon, lag, scheme='TRAINFIX_GBM'):
    ics = daily_rank_ic(df, pred, ret)
    mu, t, icir, n = ic_stats(ics, lag)
    return {'signal': signal, 'return_type': rettype, 'horizon': horizon,
            'scheme': scheme,
            'mean_ic': mu, 'median_ic': float(ics.median()) if len(ics) else np.nan,
            'ic_std': float(ics.std()) if len(ics) else np.nan,
            'icir': icir,
            'pos_ic_share': float((ics > 0).mean()) if len(ics) else np.nan,
            'ic_t': t, 'n_days': n, 'ic_p': p_two(t)}


def group_resid(df, ycol, xcols, min_n=20):
    """Cross-sectional (per-day) OLS residual of ycol on xcols -> Series."""
    out = pd.Series(np.nan, index=df.index, dtype=float)
    for k, g in df.groupby(DAY):
        sub = g[[ycol] + xcols].replace([np.inf, -np.inf], np.nan).dropna()
        if len(sub) < min_n:
            continue
        A = np.column_stack([np.ones(len(sub))] +
                            [sub[c].to_numpy() for c in xcols])
        y = sub[ycol].to_numpy()
        try:
            b, *_ = np.linalg.lstsq(A, y, rcond=None)
        except Exception:
            continue
        out.loc[sub.index] = y - A.dot(b)
    return out


def fama_macbeth(df, ret, xcols, model, min_n=30):
    sub = df[[DAY, ret] + xcols].replace([np.inf, -np.inf], np.nan).dropna()
    if len(sub) == 0:
        return None
    coefs, r2s, ns = [], [], []
    for k, x in sub.groupby(DAY):
        if len(x) < min_n:
            continue
        A = np.column_stack([np.ones(len(x))] + [x[c].to_numpy() for c in xcols])
        y = x[ret].to_numpy()
        try:
            beta, *_ = np.linalg.lstsq(A, y, rcond=None)
        except Exception:
            continue
        resid = y - A.dot(beta)
        ss = float(((y - y.mean()) ** 2).sum())
        r2s.append(1 - float((resid ** 2).sum()) / ss if ss > 0 else np.nan)
        coefs.append(beta)
        ns.append(len(x))
    if len(coefs) < 5:
        return None
    C = np.array(coefs)
    mean = C.mean(axis=0)
    se = C.std(axis=0, ddof=1) / np.sqrt(len(C))
    t = np.where(se > 0, mean / se, np.nan)
    return {'model': model, 'names': ['const'] + xcols, 'mean': mean, 't': t,
            'mean_r2': float(np.nanmean(r2s)), 'n_days': len(C),
            'n_obs': int(np.sum(ns)), 'ret': ret}


def main():
    ev = pd.read_parquet(os.path.join(DATA, 'h_earn_price_events.parquet'))
    ev = ev[ev['traded_t0']].copy()
    lg('events after tradability filter: %d  days %d'
       % (len(ev), ev[DAY].nunique()))

    ctrl = ['mom20', 'mom60', 'log_mv', 'vol20', 'turnover', 'pe_ttm', 'pb']
    for c in ctrl:
        s = pd.to_numeric(ev[c], errors='coerce')
        sd = s.groupby(ev[DAY]).transform('std')
        mu = s.groupby(ev[DAY]).transform('mean')
        ev['z_' + c] = ((s - mu) / sd).where(sd > 0, 0.0)
    f = pd.to_numeric(ev['forecast_surprise'], errors='coerce')
    sd = f.groupby(ev[DAY]).transform('std')
    mu = f.groupby(ev[DAY]).transform('mean')
    ev['z_fc'] = ((f - mu) / sd).where(sd > 0, 0.0)

    lg('residualising forecast on momentum (section 17) ...')
    ev['fc_res_mom'] = group_resid(ev, 'z_fc', ['z_mom20', 'z_mom60'])
    ev['mom_combo'] = ev[['z_mom20', 'z_mom60']].mean(axis=1)

    # ---------------- section 16: Rank IC -------------------------------
    rows = []
    for h in H_LIST:
        lag = LAG[h]
        for rettype, col in (('raw', 'fwd%d' % h),
                             ('market_excess', 'exmkt%d' % h),
                             ('industry_excess', 'exind%d' % h),
                             ('event_window', 'fwd_ev%d' % h),
                             ('open_entry', 'fwd_open%d' % h)):
            rows.append(ic_row(ev, 'forecast_surprise', col, 'Forecast',
                               rettype, h, lag))
            if rettype == 'raw':
                rows.append(ic_row(ev, 'mom_combo', col, 'Momentum20_60',
                                   rettype, h, lag))
                rows.append(ic_row(ev, 'fc_res_mom', col, 'Forecast|Momentum',
                                   rettype, h, lag))
                rows.append(ic_row(ev, 'actual_surprise', col, 'ActualSurprise',
                                   rettype, h, lag))
    ic = pd.DataFrame(rows)
    ic['fdr_p'] = bh_fdr(ic['ic_p'])
    ic.to_csv(os.path.join(DATA, 'h_earn_price_ic.csv'), index=False)
    lg('section16 Rank IC (raw):')
    for _, r in ic[ic.return_type == 'raw'].sort_values(['signal', 'horizon']).iterrows():
        lg('   %-18s H%-3d ic=%+.4f t=%+6.2f pos=%s n=%d'
           % (r['signal'], r['horizon'], r['mean_ic'], r['ic_t'],
              ('%.2f' % r['pos_ic_share']) if np.isfinite(r['pos_ic_share']) else 'NA',
              r['n_days']))
    lg('Forecast IC vs market/industry excess:')
    for _, r in ic[(ic.signal == 'Forecast') & (ic.return_type != 'raw')].iterrows():
        lg('   %-16s H%-3d ic=%+.4f t=%+6.2f'
           % (r['return_type'], r['horizon'], r['mean_ic'], r['ic_t']))

    # ---------------- section 18: residual alpha ------------------------
    ind_dum = dummies(ev['sw_l1'], 'ind')
    for c in ind_dum.columns:
        ev[c] = ind_dum[c].to_numpy()
    ind_cols = list(ind_dum.columns)
    specs = {
        'B0_forecast_only': (['z_fc'], 'fwd%d'),
        'B1_market': (['z_fc'], 'exmkt%d'),
        'B2_industry': (['z_fc'], 'exind%d'),
        'B3_momentum': (['z_fc', 'z_mom20', 'z_mom60'], 'fwd%d'),
        'B4_size': (['z_fc', 'z_log_mv'], 'fwd%d'),
        'B5_value': (['z_fc', 'z_pe_ttm', 'z_pb'], 'fwd%d'),
        'B6_market_momentum': (['z_fc', 'z_mom20', 'z_mom60'], 'exmkt%d'),
        'B7_industry_momentum': (['z_fc', 'z_mom20', 'z_mom60'] + ind_cols, 'exind%d'),
        'Full': (['z_fc', 'z_mom20', 'z_mom60', 'z_log_mv', 'z_vol20',
                  'z_turnover', 'z_pe_ttm', 'z_pb'] + ind_cols, 'fwd%d'),
        'Full_excess': (['z_fc', 'z_mom20', 'z_mom60', 'z_log_mv', 'z_vol20',
                         'z_turnover', 'z_pe_ttm', 'z_pb'] + ind_cols, 'exmkt%d'),
    }
    fam_rows = []
    for name, (xs, rtpl) in specs.items():
        for h in H_LIST:
            r = fama_macbeth(ev, rtpl % h, xs, name,
                             min_n=max(PREREG['min_xs'], 30))
            if r is None:
                continue
            j = r['names'].index('z_fc')
            fam_rows.append({'spec': name, 'horizon': h, 'ret': rtpl % h,
                             'beta_forecast': float(r['mean'][j]),
                             't_forecast': float(r['t'][j]),
                             't_p': p_two(float(r['t'][j])),
                             'mean_r2': r['mean_r2'], 'n_days': r['n_days'],
                             'n_obs': r['n_obs'], 'n_ctrl': len(xs) - 1,
                             'has_industry': int(any(c in xs for c in ind_cols))})
            if name in ('Full', 'B3_momentum'):
                lg('FM %-12s H%-3d beta_fc=%+.4f t=%+6.2f r2=%.3f ndays=%d'
                   % (name, h, r['mean'][j], r['t'][j], r['mean_r2'], r['n_days']))
    fam = pd.DataFrame(fam_rows)
    fam['fdr_p'] = bh_fdr(fam['t_p'])
    fam.to_csv(os.path.join(DATA, 'h_earn_price_residual_alpha.csv'), index=False)
    lg('section18 residual-alpha rows %d' % len(fam))

    # ---------------- section 39: null models ---------------------------
    nrows = []
    for h in H_LIST:
        ret = 'fwd%d' % h
        lag = LAG[h]
        true_ic = float(ic[(ic.signal == 'Forecast') & (ic.return_type == 'raw')
                           & (ic.horizon == h)]['mean_ic'].iloc[0])
        n1 = ev.copy()
        n1['sig'] = RNG.permutation(ev['forecast_surprise'].to_numpy())
        n2 = ev.copy()
        n2['sig'] = ev.groupby(DAY)['forecast_surprise'].transform(
            lambda x: RNG.permutation(x.to_numpy()))
        n3 = ev.copy(); n3['sig'] = RNG.random(len(ev))
        n4 = ev.copy(); n4['sig'] = ev['mom_combo'].to_numpy()
        n5 = ev.copy()
        n5['sig'] = ev.groupby([DAY, 'sw_l1'])['mom20'].transform('mean')
        n6 = ev.copy()
        n6['sig'] = ev.groupby(DAY)['forecast_surprise'].transform(
            lambda x: x.sample(frac=1.0, random_state=int(PREREG['seed'])).to_numpy())
        for nm, dd in (('N1_random_stock', n1), ('N2_random_date', n2),
                       ('N3_random_rank', n3), ('N4_momentum', n4),
                       ('N5_industry_momentum', n5), ('N6_shuffled_forecast', n6)):
            r = ic_row(dd, 'sig', ret, nm, 'raw', h, lag)
            r['true_ic'] = true_ic
            nrows.append(r)
    null = pd.DataFrame(nrows)
    null['fdr_p'] = bh_fdr(null['ic_p'])
    null.to_csv(os.path.join(DATA, 'h_earn_price_null.csv'), index=False)
    lg('section39 null models (primary horizon %d):' % PREREG['primary_horizon'])
    for _, r in null[null.horizon == PREREG['primary_horizon']].iterrows():
        lg('   %-22s ic=%+.4f (true %+.4f)' % (r['signal'], r['mean_ic'], r['true_ic']))

    # ---------------- section 40: permutation test ----------------------
    nperm = int(PREREG['n_perm'])
    perm_rows = []
    for h in H_LIST:
        ret = 'fwd%d' % h
        sub = ev[[DAY, 'forecast_surprise', ret]].dropna().reset_index(drop=True)
        true_ic = float(ic[(ic.signal == 'Forecast') & (ic.return_type == 'raw')
                           & (ic.horizon == h)]['mean_ic'].iloc[0])
        codes, uniq = pd.factorize(sub[DAY])
        cnt = pd.Series(codes).value_counts().sort_index()
        used = cnt[cnt >= MIN_N].index.to_numpy()
        null_ic = np.empty((nperm, len(used)))
        for j, gi in enumerate(used):
            pos = np.flatnonzero(codes == gi)
            rr = pd.Series(sub[ret].to_numpy()[pos]).rank().to_numpy()
            n = len(pos)
            m = (n + 1) / 2.0
            s2 = (n * n - 1) / 12.0
            P = RNG.random((nperm, n)).argsort(axis=1)
            num = rr @ rr[P].T - n * m * m
            null_ic[:, j] = num / (n * s2)
        null_mean = null_ic.mean(axis=1)
        mu, sd = float(null_mean.mean()), float(null_mean.std())
        z = (true_ic - mu) / sd if sd > 0 else np.nan
        p = float((null_mean >= true_ic).mean())
        perm_rows.append({'horizon': h, 'true_ic': true_ic, 'perm_mean': mu,
                          'perm_std': sd, 'perm_z': z, 'empirical_p': p,
                          'n_perm': nperm, 'n_days': len(used)})
        lg('section40 permutation H%-3d true=%+.4f null_mean=%+.5f sd=%.5f '
           'z=%+.1f p=%.4f' % (h, true_ic, mu, sd, z, p))
    perm = pd.DataFrame(perm_rows)
    perm.to_csv(os.path.join(DATA, 'h_earn_price_permutation.csv'), index=False)

    # ---------------- section 41: multiple-testing ledger ----------------
    ledger = []
    for _, r in ic.iterrows():
        ledger.append({'family': 'rank_ic', 'test': '%s|%s|H%d'
                       % (r['signal'], r['return_type'], r['horizon']),
                       'stat': r['mean_ic'], 'p': r['ic_p'], 'fdr_p': r['fdr_p']})
    for _, r in fam.iterrows():
        ledger.append({'family': 'residual_alpha', 'test': '%s|H%d'
                       % (r['spec'], r['horizon']),
                       'stat': r['beta_forecast'], 'p': r['t_p'],
                       'fdr_p': r['fdr_p']})
    for _, r in null.iterrows():
        ledger.append({'family': 'null', 'test': '%s|H%d'
                       % (r['signal'], r['horizon']),
                       'stat': r['mean_ic'], 'p': r['ic_p'], 'fdr_p': r['fdr_p']})
    mt = pd.DataFrame(ledger)
    mt.to_csv(os.path.join(DATA, 'h_earn_price_multiple_testing.csv'), index=False)
    lg('section41 multiple-testing ledger rows %d; share FDR<%.2f = %.3f'
       % (len(mt), PREREG['fdr_q'],
          float((mt['fdr_p'] < PREREG['fdr_q']).mean())))

    json.dump({'n_events': int(len(ev)), 'n_days': int(ev[DAY].nunique()),
               'n_perm': nperm}, open(os.path.join(DATA, 'hep_ic_summary.json'),
                                      'w'), indent=1)
    lg('DONE ic')


if __name__ == '__main__':
    main()
