# -*- coding: utf-8 -*-
"""H-EARN-PRICE-01 step 3: event study + expectation gap + quadrants.

  section 21  pre-announcement price drift   (ForecastScore vs T-20/T-10/T-5)
  section 22  expectation gap                (Forecast - pre price reaction)
  section 23  four quadrants                 (surprise x pre-price)
  section 24  post-announcement drift        (T+1 / T+3 / T+5 / T+10 / T+20)
  section 25  quintile monotonicity
  section 26  5x5 surprise x momentum matrix
  section 27  actual-surprise validation     (Layer 1 of the causal chain)
  section 29  standard event study AR / CAR
  section 30  announcement timing            (UNKNOWN - never assumed)

The signal column is always the FROZEN forecast never the realised surprise.
"""
import os, sys, json
from math import erf, erfc, sqrt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hep_common import (PREREG, DATA, Log, build_price_grid, load_calendar,
                        event_ar_grid, ic_stats, dummies, bh_fdr, auc_score,
                        pearson_r)

lg = Log('hep_event')
H = list(PREREG['horizons'])
OFF = list(PREREG['event_offsets'])
WINDOWS = [(-20, -1), (-10, -1), (-5, -1), (0, 1), (0, 3), (0, 5),
           (0, 10), (0, 20)]
DAY = 'T0'
NLAG = 3
COST = PREREG['primary_cost_bp'] / 10000.0


def p_two(t):
    return float(erfc(abs(t) / sqrt(2))) if np.isfinite(t) else np.nan


def day_agg(keys, vals, min_n=15):
    """collapse to per-day means then Newey-West t of the mean."""
    d = pd.DataFrame({'d': keys, 'v': vals}).replace([np.inf, -np.inf], np.nan)
    d = d.dropna()
    d = d[d['d'].notna()]
    if len(d) == 0:
        return np.nan, np.nan, 0, 0
    g = d.groupby('d')['v'].agg(['mean', 'count'])
    g = g[g['count'] >= min_n]
    if len(g) < 5:
        return np.nan, np.nan, 0, len(g)
    mu, t, icir, n = ic_stats(g['mean'], NLAG)
    return mu, t, int(d['v'].notna().sum()), n


def xs_z(s, day):
    s = pd.to_numeric(pd.Series(s), errors='coerce')
    mu = s.groupby(day).transform('mean')
    sd = s.groupby(day).transform('std')
    return ((s - mu) / sd).where(sd > 0, 0.0)


def daily_rank_ic(df, pred, ret, min_n=15):
    sub = df[[DAY, pred, ret]].replace([np.inf, -np.inf], np.nan).dropna()
    if len(sub) == 0:
        return pd.Series(dtype=float)
    sub = sub.assign(_p=sub.groupby(DAY)[pred].rank(),
                     _r=sub.groupby(DAY)[ret].rank())
    cnt = sub.groupby(DAY)['_p'].count()
    sub = sub[sub[DAY].isin(cnt[cnt >= min_n].index)]
    return sub.groupby(DAY).apply(
        lambda x: x['_p'].corr(x['_r']), include_groups=False).dropna()


def ic_line(df, pred, ret, min_n=15):
    ics = daily_rank_ic(df, pred, ret, min_n)
    mu, t, icir, n = ic_stats(ics, NLAG)
    return {'mean_ic': mu, 'median_ic': float(ics.median()) if len(ics) else np.nan,
            'ic_t': t, 'icir': icir,
            'pos_share': float((ics > 0).mean()) if len(ics) else np.nan,
            'ic_p': p_two(t), 'n_days': n}


def quintile(s, day, nq=5):
    r = pd.Series(s).groupby(day).rank(pct=True, method='first')
    return np.clip(np.ceil(r * nq), 1, nq).astype('float')


def main():
    cal = load_calendar()
    grid = build_price_grid(cal)
    s2i = grid['s2i']
    close = grid['close']
    N = close.shape[1]

    ev = pd.read_parquet(os.path.join(DATA, 'h_earn_price_events.parquet'))
    ev = ev[ev['traded_t0']].copy()
    si = ev['ts_code'].map(s2i)
    ev = ev[si.notna().to_numpy()].copy()
    si = si.dropna().to_numpy().astype('int64')
    i0 = ev['T0_idx'].to_numpy()
    lg('events for event study: %d  T0 days %d' % (len(ev), ev[DAY].nunique()))

    mkt = pd.read_parquet(os.path.join(DATA, 'aux_market_cum.parquet'))
    mkt_lvl = np.asarray(mkt.iloc[:, 0], dtype=float)

    # ---- daily abnormal returns on the full offset grid ----------------
    AR = event_ar_grid(close, mkt_lvl, si, i0, -21, 20)
    lg('abnormal-return grid built %s' % (AR.shape,))

    # ---- controls / transforms -----------------------------------------
    ev['z_fc'] = xs_z(ev['forecast_surprise'], ev[DAY])
    ev['z_mom20'] = xs_z(ev['mom20'], ev[DAY])
    ev['z_mom60'] = xs_z(ev['mom60'], ev[DAY])
    ev['z_log_mv'] = xs_z(ev['log_mv'], ev[DAY])
    ev['z_pre5'] = xs_z(ev['pre5'], ev[DAY])
    ev['z_pre20'] = xs_z(ev['pre20'], ev[DAY])
    ev['fc_q'] = quintile(ev['forecast_surprise'], ev[DAY])
    ev['mom_q'] = quintile(ev['mom20'], ev[DAY])
    ev['gap5'] = ev['z_fc'] - ev['z_pre5']
    ev['gap20'] = ev['z_fc'] - ev['z_pre20']

    # ---- section 29 + 9: event study AR / CAR --------------------------
    def mom_resid(car):
        """per-day cross-sectional OLS residual of the window CAR on the two
        preregistered momentum controls (MOM20 / MOM60); aligned to ev rows."""
        tmp = pd.DataFrame({'i': np.arange(len(ev)), 'd': ev[DAY].to_numpy(),
                            'v': car, 'm1': ev['z_mom20'].to_numpy(),
                            'm2': ev['z_mom60'].to_numpy()})
        tmp = tmp.replace([np.inf, -np.inf], np.nan).dropna()
        out = np.full(len(ev), np.nan)
        for _, gg in tmp.groupby('d'):
            if len(gg) < 20:
                continue
            A = np.column_stack([np.ones(len(gg)), gg['m1'].to_numpy(),
                                 gg['m2'].to_numpy()])
            coef, *_ = np.linalg.lstsq(A, gg['v'].to_numpy(), rcond=None)
            out[gg['i'].to_numpy()] = gg['v'].to_numpy() - A.dot(coef)
        return out

    def day_spread(vals, q=None, lo=5, hi=1):
        """per-day mean(Q5) - mean(Q1) with a Newey-West t on the daily series."""
        qq = ev['fc_q'].to_numpy() if q is None else q
        d = pd.DataFrame({'d': ev[DAY].to_numpy(), 'v': vals, 'q': qq})
        d = d.replace([np.inf, -np.inf], np.nan).dropna()
        if len(d) == 0:
            return np.nan, np.nan, 0
        g = d.groupby(['d', 'q'])['v'].agg(['mean', 'count'])
        piv = g['mean'].unstack()
        cnt = g['count'].unstack()
        if lo not in piv.columns or hi not in piv.columns:
            return np.nan, np.nan, 0
        ok = (cnt[lo].fillna(0) >= 5) & (cnt[hi].fillna(0) >= 5)
        s = (piv[lo] - piv[hi])[ok].dropna()
        if len(s) < 5:
            return np.nan, np.nan, len(s)
        mu, t, icir, n = ic_stats(s, NLAG)
        return mu, t, n

    rows = []
    for k in OFF:
        mu, t, n, nd = day_agg(ev[DAY].to_numpy(), AR[k].to_numpy())
        oos = ev['ann_year'].isin(PREREG['oos_years']).to_numpy()
        mu_o, _, _, _ = day_agg(ev[DAY].to_numpy()[oos], AR[k].to_numpy()[oos])
        r = mom_resid(AR[k].to_numpy())
        sp, spt, _ = day_spread(AR[k].to_numpy())
        spm, sptm, _ = day_spread(r)
        rows.append({'window': 'T%+d' % k, 'kind': 'offset', 'k_from': k,
                     'k_to': k, 'n_days_in_win': 1,
                     'ar_mean': mu, 'ar_t': t, 'car_mean': mu, 'car_t': t,
                     'car_median': float(np.nanmedian(AR[k].to_numpy())),
                     'oos_car': mu_o, 'q5_q1_spread': sp, 'spread_t': spt,
                     'spread_momadj': spm, 'spread_momadj_t': sptm,
                     'cost_adj_car': np.nan, 'cost_adj_spread': np.nan,
                     'n_events': n, 'n_days': nd})
    for a, b in WINDOWS:
        cs = list(range(a, b + 1))
        car = AR[cs].sum(axis=1, min_count=len(cs)).to_numpy()
        mu, t, n, nd = day_agg(ev[DAY].to_numpy(), car)
        oos = ev['ann_year'].isin(PREREG['oos_years']).to_numpy()
        mu_o, _, _, _ = day_agg(ev[DAY].to_numpy()[oos], car[oos])
        r = mom_resid(car)
        sp, spt, _ = day_spread(car)
        spm, sptm, _ = day_spread(r)
        rows.append({'window': '%d/%+d' % (a, b), 'kind': 'car',
                     'k_from': a, 'k_to': b, 'n_days_in_win': len(cs),
                     'ar_mean': mu / len(cs), 'ar_t': t, 'car_mean': mu,
                     'car_t': t, 'car_median': float(np.nanmedian(car)),
                     'oos_car': mu_o, 'q5_q1_spread': sp, 'spread_t': spt,
                     'spread_momadj': spm, 'spread_momadj_t': sptm,
                     'cost_adj_car': (mu - COST) if a >= 0 else np.nan,
                     'cost_adj_spread': (sp - COST) if a >= 0 else np.nan,
                     'n_events': n, 'n_days': nd})
    es = pd.DataFrame(rows)
    es.to_csv(os.path.join(DATA, 'h_earn_price_eventstudy.csv'), index=False)
    lg('section29 event study (AR / CAR, market-adjusted):')
    for _, r in es.iterrows():
        lg('   %-8s AR=%+.4f CAR=%+.4f t=%+6.2f oos=%+.4f '
           'Q5-Q1=%+.4f(t%+.1f) momadj=%+.4f costadj=%s'
           % (r['window'], r['ar_mean'], r['car_mean'], r['car_t'],
              r['oos_car'], r['q5_q1_spread'], r['spread_t'],
              r['spread_momadj'],
              ('%+.4f' % r['cost_adj_car'])
              if np.isfinite(r['cost_adj_car']) else '   NA'))

    # ---- section 21/22: pre-drift + expectation gap --------------------
    gap_rows = []
    for nm, pred in (('Forecast', 'forecast_surprise'),
                     ('Gap_fc_minus_pre5', 'gap5'),
                     ('Gap_fc_minus_pre20', 'gap20'),
                     ('Pre5_only', 'pre5'), ('Pre20_only', 'pre20')):
        for h in H:
            for rt, col in (('raw', 'fwd%d' % h), ('market_excess', 'exmkt%d' % h)):
                r = ic_line(ev, pred, col, min_n=PREREG['ic_min_n'])
                r.update({'signal': nm, 'return_type': rt, 'horizon': h})
                gap_rows.append(r)
    # section 21 explicitly: forecast vs the pre-announcement window return
    for pw in PREREG['pre_windows']:
        r = ic_line(ev, 'forecast_surprise', 'pre%d' % pw)
        r.update({'signal': 'Forecast', 'return_type': 'pre%d' % pw,
                  'horizon': 0})
        gap_rows.append(r)
    # residual forecast: control momentum / size / industry / market
    ind = dummies(ev['sw_l1'], 'ind')
    for c in ind.columns:
        ev[c] = ind[c].to_numpy()
    X = ev[['z_mom20', 'z_mom60', 'z_log_mv'] + list(ind.columns)].astype(float)
    D = pd.concat([ev[['z_fc']].rename(columns={'z_fc': 'y'}), X], axis=1)
    D = D.replace([np.inf, -np.inf], np.nan).dropna()
    A = np.column_stack([np.ones(len(D))] + [D[c].to_numpy() for c in X.columns])
    beta, *_ = np.linalg.lstsq(A, D['y'].to_numpy(), rcond=None)
    ev['resid_fc'] = np.nan
    ev.loc[D.index, 'resid_fc'] = D['y'].to_numpy() - A.dot(beta)
    lg('residual forecast: r2=%.3f  corr(z_fc,resid)=%.3f  coverage %.3f'
       % (1 - float(((D['y'].to_numpy() - A.dot(beta)) ** 2).sum())
          / float(((D['y'].to_numpy() - D['y'].mean()) ** 2).sum()),
          float(ev[['z_fc', 'resid_fc']].dropna().corr().iloc[0, 1]),
          float(ev['resid_fc'].notna().mean())))
    for h in H:
        for rt, col in (('raw', 'fwd%d' % h), ('market_excess', 'exmkt%d' % h)):
            r = ic_line(ev, 'resid_fc', col, min_n=PREREG['ic_min_n'])
            r.update({'signal': 'ResidForecast_mom_size_ind',
                      'return_type': rt, 'horizon': h})
            gap_rows.append(r)
    gp = pd.DataFrame(gap_rows)
    gp['fdr_p'] = bh_fdr(gp['ic_p'])
    gp.to_csv(os.path.join(DATA, 'h_earn_price_expectation_gap.csv'), index=False)
    lg('section21/22 expectation-gap rows %d' % len(gp))
    for _, r in gp[gp.return_type.isin(['pre5', 'pre10', 'pre20'])].iterrows():
        lg('   IC(forecast, %s) = %+.4f t=%+6.2f' % (r['return_type'],
                                                     r['mean_ic'], r['ic_t']))

    # ---- section 24: post-announcement drift ---------------------------
    lg('section24 post-announcement drift (forecast quintile 5 - 1, '
       'market excess):')
    for h in H:
        col = 'exmkt%d' % h
        q5 = float(ev.loc[ev['fc_q'] == 5, col].mean())
        q1 = float(ev.loc[ev['fc_q'] == 1, col].mean())
        lg('   H%-3d Q5=%+.4f Q1=%+.4f spread=%+.4f' % (h, q5, q1, q5 - q1))

    # ---- section 25: quintile curve ------------------------------------
    qrows = []
    for h in H:
        col = 'exmkt%d' % h
        means = []
        for q in (1, 2, 3, 4, 5):
            m = ev.loc[ev['fc_q'] == q, col]
            mu, t, n, nd = day_agg(ev.loc[ev['fc_q'] == q, DAY].to_numpy(),
                                   m.to_numpy())
            means.append(mu)
            qrows.append({'horizon': h, 'quintile': q, 'n': int(m.notna().sum()),
                          'mean_excess': mu, 't': t, 'n_days': nd})
        v = np.array(means, dtype=float)
        qidx = np.arange(5)
        m = np.isfinite(v)
        sp = float(pd.Series(qidx[m]).corr(pd.Series(v[m]), method='spearman')) \
            if m.sum() >= 3 else np.nan
        am = int(np.nanargmax(v)) if m.sum() else -1
        if np.isfinite(sp) and abs(sp) >= 0.9:
            shape = 'MONOTONIC_UP' if sp > 0 else 'MONOTONIC_DOWN'
        elif am in (0, 4):
            shape = 'ISOLATED_PEAK'
        elif am in (1, 2, 3) and np.nanmin(v) in (v[0], v[4]):
            shape = 'U_SHAPED'
        elif am in (1, 2, 3):
            shape = 'INVERTED_U'
        else:
            shape = 'MIXED'
        qrows.append({'horizon': h, 'quintile': 'shape', 'n': int(m.sum()),
                      'mean_excess': np.nan, 't': sp, 'n_days': 0,
                      'shape': shape})
        lg('section25 H%-3d quintiles %s  spearman(q,ret)=%s  shape=%s'
           % (h, ' '.join('%+.4f' % x for x in v),
              ('%+.2f' % sp) if np.isfinite(sp) else 'NA', shape))
    pd.DataFrame(qrows).to_csv(
        os.path.join(DATA, 'h_earn_price_quintile.csv'), index=False)

    # ---- section 26: 5x5 surprise x momentum ---------------------------
    mrows = []
    for qf in (1, 2, 3, 4, 5):
        for qm in (1, 2, 3, 4, 5):
            sel = ev[(ev['fc_q'] == qf) & (ev['mom_q'] == qm)]
            for h in (5, 10, 20):
                col = 'exmkt%d' % h
                m = sel[col]
                mu, t, n, nd = day_agg(sel[DAY].to_numpy(), m.to_numpy())
                mrows.append({'fc_q': qf, 'mom_q': qm, 'horizon': h,
                              'n': int(m.notna().sum()), 'mean_excess': mu,
                              't': t, 'n_days': nd})
    mt = pd.DataFrame(mrows)
    mt.to_csv(os.path.join(DATA, 'h_earn_price_2d_matrix.csv'), index=False)
    hl = mt[(mt.horizon == 5) & (mt.fc_q == 5) & (mt.mom_q == 1)]
    lh = mt[(mt.horizon == 5) & (mt.fc_q == 1) & (mt.mom_q == 5)]
    lg('section26 5x5 (H5): high-fc/low-mom=%+.4f  low-fc/high-mom=%+.4f'
       % (float(hl['mean_excess'].iloc[0]) if len(hl) else np.nan,
          float(lh['mean_excess'].iloc[0]) if len(lh) else np.nan))

    # ---- section 23 / 60: four quadrants --------------------------------
    hi = ev['z_fc'] > ev.groupby(DAY)['z_fc'].transform('median')
    strong = ev['z_pre20'] > ev.groupby(DAY)['z_pre20'].transform('median')
    ev['quad'] = np.where(hi, np.where(strong, 'B_HighFc_StrongPre',
                                       'A_HighFc_WeakPre'),
                          np.where(strong, 'D_LowFc_StrongPre',
                                   'C_LowFc_WeakPre'))
    qrows2 = []
    for nm in ('A_HighFc_WeakPre', 'B_HighFc_StrongPre',
               'C_LowFc_WeakPre', 'D_LowFc_StrongPre'):
        sel = ev[ev['quad'] == nm]
        row = {'quadrant': nm, 'n': int(len(sel))}
        for h in (5, 10, 20):
            col = 'exmkt%d' % h
            mu, t, n, nd = day_agg(sel[DAY].to_numpy(),
                                   pd.to_numeric(sel[col]).to_numpy())
            row['excess%d' % h] = mu
            row['t%d' % h] = t
        oo = sel[sel['ann_year'].isin(PREREG['oos_years'])]
        mu_o, _, _, _ = day_agg(oo[DAY].to_numpy(),
                                pd.to_numeric(oo['exmkt5']).to_numpy())
        row['oos_excess5'] = mu_o
        row['mean_pre20'] = float(sel['pre20'].mean())
        row['mean_fc'] = float(sel['forecast_surprise'].mean())
        qrows2.append(row)
    quad = pd.DataFrame(qrows2)
    quad.to_csv(os.path.join(DATA, 'h_earn_price_quadrant.csv'), index=False)
    lg('section23/60 four quadrants (market excess, H5):')
    for _, r in quad.iterrows():
        lg('   %-18s n=%-6d pre20=%+.3f  H5=%+.4f H10=%+.4f H20=%+.4f OOS5=%+.4f'
           % (r['quadrant'], r['n'], r['mean_pre20'], r['excess5'],
              r['excess10'], r['excess20'], r['oos_excess5']))

    # ---- section 27: actual-surprise validation (Layer 1) ---------------
    l1 = ev[['forecast_surprise', 'actual_surprise', 'positive_surprise',
             'strong_surprise']].dropna(subset=['forecast_surprise',
                                                'actual_surprise'])
    pr, npr = pearson_r(l1['forecast_surprise'], l1['actual_surprise'],
                        min_n=30)
    sr = float(l1['forecast_surprise'].corr(l1['actual_surprise'],
                                            method='spearman'))
    auc_pos = auc_score(l1['positive_surprise'].astype(float),
                        l1['forecast_surprise'])
    auc_str = auc_score(l1['strong_surprise'].astype(float),
                        l1['forecast_surprise'])
    r1 = ic_line(ev, 'forecast_surprise', 'actual_surprise', min_n=15)
    # calibration: decile of the forecast -> realised surprise
    dec = ev.copy()
    dec['d'] = quintile(dec['forecast_surprise'], dec[DAY], nq=10)
    cal_rows = []
    for q in range(1, 11):
        s = dec[(dec['d'] == q) & dec['actual_surprise'].notna()]
        cal_rows.append({'decile': q, 'n': int(len(s)),
                         'mean_actual': float(s['actual_surprise'].mean()),
                         'pos_rate': float(s['positive_surprise'].mean())})
    pd.DataFrame(cal_rows).to_csv(
        os.path.join(DATA, 'h_earn_price_layer1.csv'), index=False)
    layer1 = {'pearson': pr, 'spearman': sr, 'auc_positive': auc_pos,
              'auc_strong': auc_str, 'n': int(len(l1)),
              'daily_rank_ic_mean': r1['mean_ic'],
              'daily_rank_ic_median': r1['median_ic'],
              'daily_rank_ic_t': r1['ic_t'],
              'daily_rank_ic_pos_share': r1['pos_share'],
              'daily_rank_ic_days': r1['n_days']}
    lg('section27 Layer1 forecast -> actual surprise: rho=%.3f rank=%.3f '
       'dailyIC=%+.3f t=%+.1f AUC=%s' %
       (pr, sr, r1['mean_ic'], r1['ic_t'], ('%.3f' % auc_pos)
        if np.isfinite(auc_pos) else 'NA'))
    for c in cal_rows:
        lg('   decile %2d n=%-6d mean_actual=%+.3f pos_rate=%.3f'
           % (c['decile'], c['n'], c['mean_actual'], c['pos_rate']))

    summary = {'n_events': int(len(ev)), 'n_days': int(ev[DAY].nunique()),
               'announcement_timing': 'UNKNOWN',
               'car_h5': float(es.loc[es['window'] == '0/+5', 'car_mean'].iloc[0]),
               'layer1': layer1}
    json.dump(summary, open(os.path.join(DATA, 'hep_event_summary.json'), 'w'),
              indent=1, default=float)
    lg('DONE event')


if __name__ == '__main__':
    main()
