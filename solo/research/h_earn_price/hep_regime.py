# -*- coding: utf-8 -*-
"""H-EARN-PRICE-01 step 5: stability / robustness layer.

  section 35  out-of-sample phases  (VALID / OOS / LIVE-LIKE)
  section 36  walk-forward windows  (WF1..WF6)
  section 37  year-by-year stability
  section 38  regime stability      (BULL / NORMAL / BEAR)
  section 42  pre-registered price-layer parameters (frozen)
  section 43  parameter perturbation surface

Everything is measured on the SAME frozen forecast used by hep_ic / hep_event
/ hep_portfolio.  No parameter is chosen after seeing a result.
"""
import os, sys, json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hep_common import (PREREG, DATA, Log, build_price_grid, load_calendar,
                        build_return_sets, ic_stats)
from hep_portfolio import book, stats

lg = Log('hep_regime')
H_LIST = list(PREREG['horizons'])
Q0 = PREREG['primary_quantile']
C0 = PREREG['primary_cost_bp']
DAY = 'T0'
NLAG = 3
MIN_EV = 200          # a cell thinner than this is reported but not scored
MIN_XS = PREREG['min_xs']


def fm_beta(ev, h, lag=NLAG):
    """Fama-MacBeth mean cross-sectional beta of the forecast on the market
    excess return, controlling momentum / size / volatility."""
    df = pd.DataFrame({'d': ev[DAY].to_numpy(),
                       'y': ev['exmkt%d' % h].to_numpy(),
                       'f': ev['forecast_surprise'].to_numpy(),
                       'm1': ev['mom20'].to_numpy(),
                       'm2': ev['mom60'].to_numpy(),
                       's': ev['log_mv'].to_numpy(),
                       'v': ev['vol20'].to_numpy()})
    for c in ('y', 'f', 'm1', 'm2', 's', 'v'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.replace([np.inf, -np.inf], np.nan).dropna()
    betas = []
    for _, gg in df.groupby('d'):
        if len(gg) < MIN_XS:
            continue
        X = gg[['f', 'm1', 'm2', 's', 'v']].to_numpy()
        sd = X.std(0)
        X = (X - X.mean(0)) / np.where(sd == 0, 1.0, sd)
        A = np.column_stack([np.ones(len(gg)), X])
        coef, *_ = np.linalg.lstsq(A, gg['y'].to_numpy(), rcond=None)
        betas.append(coef[1])
    if len(betas) < 30:
        return np.nan, np.nan, len(betas)
    mu, t, _, n = ic_stats(pd.Series(betas), lag)
    return mu, t, n


def cell(ev, R, N, h, q=Q0, cost_bp=0.0, rtype='market_neutral'):
    """Metrics of one stability cell (already row-masked)."""
    i0 = ev['T0_idx'].to_numpy()
    coh, cpos = pd.factorize(i0)
    span = np.zeros(N, dtype=bool)
    span[int(cpos.min()):min(N, int(cpos.max()) + h + 1)] = True
    s = stats(book(ev, R[rtype], h, q, coh, cpos, N, cost_bp=cost_bp), h, span)
    ic = pd.DataFrame({'d': ev[DAY].to_numpy(),
                       'p': ev['forecast_surprise'].to_numpy(),
                       'r': ev['exmkt%d' % h].to_numpy()}).dropna()
    ic = ic[ic.groupby('d')['p'].transform('count') >= PREREG['ic_min_n']]
    if len(ic):
        d = ic.groupby('d').apply(
            lambda x: x['p'].rank().corr(x['r'].rank()), include_groups=False)
        mu_ic, t_ic, icir, nd = ic_stats(d, NLAG)
    else:
        mu_ic = t_ic = icir = np.nan
        nd = 0
    return {'n_events': len(ev), 'n_days': int(ev[DAY].nunique()),
            'ic': mu_ic, 'ic_t': t_ic, 'icir': icir, 'ic_days': nd,
            'ls_mean_bp': s['mean_bp_active'], 'ls_t': s['t_active'],
            'ls_ann': s['ann_active'], 'sharpe': s['sharpe'],
            'max_dd': s['max_dd'], 'exposure': s['exposure'],
            'n_long': s.get('n_long', np.nan)}


def row(ev, R, N, h, tag, **extra):
    a = cell(ev, R, N, h, cost_bp=0.0)
    b = cell(ev, R, N, h, cost_bp=C0)
    a['ls_mean_bp_c%d' % C0] = b['ls_mean_bp']
    a['ls_t_c%d' % C0] = b['ls_t']
    mu, t, nb = fm_beta(ev, h)
    a['fm_beta'] = mu
    a['fm_t'] = t
    a['fm_days'] = nb
    a.update(extra)
    a['horizon'] = h
    a['tag'] = tag
    return a


def get_mom(close, si, iA, w):
    """Momentum ending the session BEFORE the announcement (PIT)."""
    N = close.shape[1]
    b = np.clip(iA - 1, 0, N - 1)
    a = np.clip(iA - 1 - w, 0, N - 1)
    return close[si, b] / close[si, a] - 1.0


def main():
    cal = load_calendar()
    grid = build_price_grid(cal)
    s2i = grid['s2i']
    close = grid['close']
    N = close.shape[1]

    ev = pd.read_parquet(os.path.join(DATA, 'h_earn_price_events.parquet'))
    ev = ev[ev['traded_t0']].copy()
    si = ev['ts_code'].map(s2i)
    ev = ev[si.notna().to_numpy()].reset_index(drop=True)
    si = si.dropna().to_numpy().astype('int64')
    i0 = ev['T0_idx'].to_numpy()
    iA = ev['A_idx'].to_numpy()
    ev['yr'] = ev['T0'].astype(str).str[:4].astype(int)
    lg('events %d  years %s' % (len(ev), sorted(ev['yr'].unique())))

    # ---- frozen price-layer parameters (section 42) --------------------
    lg('PREREG price layer: momentum %s  holding %s  quantile %s  cost %s'
       % (PREREG['momentum'], PREREG['horizons'],
          PREREG['quantiles'], PREREG['cost_bp']))

    mkt = pd.read_parquet(os.path.join(DATA, 'aux_market_cum.parquet'))
    mkt_lvl = np.asarray(mkt.iloc[:, 0], dtype=float)

    KMAX = max(H_LIST)
    R, ctx = build_return_sets(close, mkt_lvl, ev, si, i0, KMAX)
    lg('return matrices ready')

    # verify the grid momentum reproduces the stored MOM20 / MOM60
    for w in PREREG['momentum']:
        g = get_mom(close, si, iA, w)
        d = np.nanmax(np.abs(g - ev['mom%d' % w].to_numpy()))
        lg('momentum check MOM%d: max |grid - stored| = %.2e' % (w, d))

    out = {}

    # ---------------- section 35: OOS phases ----------------------------
    lg('--- section 35 OOS phases ---')
    rows = []
    for tag, a, b in PREREG['phases']:
        m = (ev['yr'] >= a) & (ev['yr'] <= b)
        if m.sum() < MIN_EV:
            lg('   %-10s skipped (n=%d)' % (tag, int(m.sum())))
            continue
        E = ev[m].reset_index(drop=True)
        Rs = {k: v[m].reset_index(drop=True) for k, v in R.items()}
        for h in H_LIST:
            rows.append(row(E, Rs, N, h, tag, phase=tag,
                            years='%d-%d' % (a, b)))
    pd.DataFrame(rows).to_csv(
        os.path.join(DATA, 'h_earn_price_oos.csv'), index=False)
    for r0 in rows:
        if r0['horizon'] == PREREG['primary_horizon']:
            lg('   %-10s n=%-5d IC=%+.4f t=%+6.2f  L/S=%+7.1fbp t=%+6.2f '
               'c30=%+7.1fbp  FM beta=%+.4f t=%+6.2f'
               % (r0['phase'], r0['n_events'], r0['ic'], r0['ic_t'],
                  r0['ls_mean_bp'], r0['ls_t'],
                  r0['ls_mean_bp_c%d' % C0], r0['fm_beta'], r0['fm_t']))

    # ---------------- section 36: walk forward --------------------------
    lg('--- section 36 walk-forward windows ---')
    rows = []
    for tag, a, b in PREREG['wf_windows']:
        m = (ev['yr'] >= a) & (ev['yr'] <= b)
        if m.sum() < 50:
            lg('   %-4s skipped (n=%d)' % (tag, int(m.sum())))
            continue
        E = ev[m].reset_index(drop=True)
        Rs = {k: v[m].reset_index(drop=True) for k, v in R.items()}
        for h in H_LIST:
            rows.append(row(E, Rs, N, h, tag, year=a,
                            scheme=PREREG['forecast_wf_stitch'].get(a, 'NA')))
    pd.DataFrame(rows).to_csv(
        os.path.join(DATA, 'h_earn_price_walkforward.csv'), index=False)
    for r0 in rows:
        if r0['horizon'] == PREREG['primary_horizon']:
            lg('   %-4s (%d, frozen %-2s) n=%-5d IC=%+.4f t=%+6.2f '
               'L/S=%+7.1fbp t=%+6.2f c30=%+7.1fbp FM=%+.4f t=%+6.2f'
               % (r0['tag'], r0['year'], r0['scheme'], r0['n_events'],
                  r0['ic'], r0['ic_t'], r0['ls_mean_bp'], r0['ls_t'],
                  r0['ls_mean_bp_c%d' % C0], r0['fm_beta'], r0['fm_t']))

    # ---------------- section 37: year by year --------------------------
    lg('--- section 37 year stability ---')
    rows = []
    for y in sorted(ev['yr'].unique()):
        m = ev['yr'].to_numpy() == y
        E = ev[m].reset_index(drop=True)
        Rs = {k: v[m].reset_index(drop=True) for k, v in R.items()}
        for h in H_LIST:
            rows.append(row(E, Rs, N, h, str(y), year=int(y),
                            oos=bool(y in PREREG['oos_years'])))
    pd.DataFrame(rows).to_csv(
        os.path.join(DATA, 'h_earn_price_year.csv'), index=False)
    for r0 in rows:
        if r0['horizon'] == PREREG['primary_horizon']:
            lg('   %d oos=%d n=%-5d IC=%+.4f t=%+6.2f  L/S=%+7.1fbp '
               't=%+6.2f c30=%+7.1fbp ICRR=%s'
               % (r0['year'], int(r0['oos']), r0['n_events'], r0['ic'],
                  r0['ic_t'], r0['ls_mean_bp'], r0['ls_t'],
                  r0['ls_mean_bp_c%d' % C0],
                  ('%.2f' % r0['icir']) if np.isfinite(r0['icir']) else 'NA'))

    # ---------------- section 38: regime --------------------------------
    lg('--- section 38 regime ---')
    lvl = pd.Series(mkt_lvl)
    ma120 = lvl.rolling(120).mean().to_numpy()
    r60 = np.full(len(lvl), np.nan)
    r60[60:] = lvl.to_numpy()[60:] / lvl.to_numpy()[:-60] - 1.0
    above = mkt_lvl > ma120
    bull = above & (r60 > 0.05)
    bear = (~above) & (r60 < -0.05)
    reg = np.where(bull, 'BULL', np.where(bear, 'BEAR', 'NORMAL'))
    reg = np.where(np.isfinite(ma120) & np.isfinite(r60), reg, 'UNKNOWN')
    ev['regime'] = reg[np.clip(i0, 0, N - 1)]
    lg('   regime share: %s' % dict(
        ev['regime'].value_counts().to_dict()))
    rows = []
    for tag in ('BULL', 'NORMAL', 'BEAR'):
        m = (ev['regime'] == tag).to_numpy()
        if m.sum() < MIN_EV:
            lg('   %-7s skipped (n=%d)' % (tag, int(m.sum())))
            continue
        E = ev[m].reset_index(drop=True)
        Rs = {k: v[m].reset_index(drop=True) for k, v in R.items()}
        for h in H_LIST:
            rows.append(row(E, Rs, N, h, tag, regime=tag))
    pd.DataFrame(rows).to_csv(
        os.path.join(DATA, 'h_earn_price_regime.csv'), index=False)
    for r0 in rows:
        if r0['horizon'] == PREREG['primary_horizon']:
            lg('   %-7s n=%-5d IC=%+.4f t=%+6.2f  L/S=%+7.1fbp t=%+6.2f '
               'c30=%+7.1fbp FM=%+.4f t=%+6.2f'
               % (r0['regime'], r0['n_events'], r0['ic'], r0['ic_t'],
                  r0['ls_mean_bp'], r0['ls_t'],
                  r0['ls_mean_bp_c%d' % C0], r0['fm_beta'], r0['fm_t']))

    # ---------------- section 43: parameter perturbation ----------------
    lg('--- section 43 parameter surface (exploratory; required only if '
       'the main hypothesis holds) ---')
    rows = []
    for q in (0.08, Q0, 0.12):
        for h in (3, 5, 10):
            a = cell(ev, R, N, h, q=q, cost_bp=0.0, rtype='market_neutral')
            b = cell(ev, R, N, h, q=q, cost_bp=C0, rtype='market_neutral')
            c = cell(ev, R, N, h, q=q, cost_bp=0.0,
                     rtype='industry_momentum_neutral')
            d = cell(ev, R, N, h, q=q, cost_bp=C0,
                     rtype='industry_momentum_neutral')
            rows.append({'grid': 'hold_x_quantile', 'quantile': q,
                         'horizon': h, 'mom_fast': 20, 'mom_slow': 60,
                         'ls_mean_bp': a['ls_mean_bp'], 'ls_t': a['ls_t'],
                         'ls_mean_bp_c30': b['ls_mean_bp'],
                         'ls_mean_bp_indmom': c['ls_mean_bp'],
                         'ls_t_indmom': c['ls_t'],
                         'ls_mean_bp_indmom_c30': d['ls_mean_bp'],
                         'n_events': a['n_events'], 'n_days': a['n_days']})
    # momentum perturbation -> rebuild the industry+momentum neutral panel
    for wf in (18, 20, 22):
        for ws in (55, 60, 65):
            E = ev.copy()
            E['mom20'] = get_mom(close, si, iA, wf)
            E['mom60'] = get_mom(close, si, iA, ws)
            Rp, _ = build_return_sets(close, mkt_lvl, E, si, i0, KMAX)
            h = PREREG['primary_horizon']
            c = cell(E, Rp, N, h, q=Q0, cost_bp=0.0,
                     rtype='industry_momentum_neutral')
            d = cell(E, Rp, N, h, q=Q0, cost_bp=C0,
                     rtype='industry_momentum_neutral')
            rows.append({'grid': 'momentum', 'quantile': Q0, 'horizon': h,
                         'mom_fast': wf, 'mom_slow': ws,
                         'ls_mean_bp': np.nan, 'ls_t': np.nan,
                         'ls_mean_bp_c30': np.nan,
                         'ls_mean_bp_indmom': c['ls_mean_bp'],
                         'ls_t_indmom': c['ls_t'],
                         'ls_mean_bp_indmom_c30': d['ls_mean_bp'],
                         'n_events': c['n_events'], 'n_days': c['n_days']})
            lg('   mom %d/%d : ind+mom L/S=%+7.1fbp t=%+6.2f  c30=%+7.1fbp'
               % (wf, ws, c['ls_mean_bp'], c['ls_t'], d['ls_mean_bp']))
    pg = pd.DataFrame(rows)
    pg.to_csv(os.path.join(DATA, 'h_earn_price_parameter_grid.csv'), index=False)
    lg('parameter grid rows %d' % len(pg))
    hv = pg[pg['grid'] == 'hold_x_quantile']
    lg('   hold x quantile, industry+momentum neutral L/S (bp):')
    for _, r0 in hv.iterrows():
        lg('      q=%.2f H=%-3d raw=%+7.1fbp t=%+6.2f  c30=%+7.1fbp'
           % (r0['quantile'], r0['horizon'], r0['ls_mean_bp_indmom'],
              r0['ls_t_indmom'], r0['ls_mean_bp_indmom_c30']))

    # ---------------- summary json --------------------------------------
    def pick(df_, col, **sel):
        d = df_
        for k, v in sel.items():
            d = d[d[k] == v]
        return float(d[col].iloc[0]) if len(d) else np.nan

    yrs = pd.read_csv(os.path.join(DATA, 'h_earn_price_year.csv'))
    wfs = pd.read_csv(os.path.join(DATA, 'h_earn_price_walkforward.csv'))
    regs = pd.read_csv(os.path.join(DATA, 'h_earn_price_regime.csv'))
    ooss = pd.read_csv(os.path.join(DATA, 'h_earn_price_oos.csv'))

    def npos(df_, col, h):
        d = df_[df_.horizon == h]
        return int((d[col] > 0).sum()), int(len(d))

    out['section35_oos'] = {
        'primary_horizon': PREREG['primary_horizon'],
        'ic_by_phase': {p: pick(ooss, 'ic', phase=p,
                                horizon=PREREG['primary_horizon'])
                        for p in ooss['phase'].unique()},
        'ls_by_phase': {p: pick(ooss, 'ls_mean_bp', phase=p,
                                horizon=PREREG['primary_horizon'])
                        for p in ooss['phase'].unique()}}
    out['section36_walkforward'] = {
        'n_windows': int(wfs['tag'].nunique()),
        'ls_pos_h5': npos(wfs, 'ls_mean_bp',
                          PREREG['primary_horizon']),
        'ic_pos_h5': npos(wfs, 'ic', PREREG['primary_horizon'])}
    out['section37_year'] = {
        'n_years': int(yrs['year'].nunique()),
        'ls_pos_h5': npos(yrs, 'ls_mean_bp', PREREG['primary_horizon']),
        'c30_pos_h5': npos(yrs, 'ls_mean_bp_c30',
                           PREREG['primary_horizon']),
        'ic_pos_h5': npos(yrs, 'ic', PREREG['primary_horizon'])}
    out['section38_regime'] = {
        'regimes': regs['regime'].unique().tolist(),
        'ls_by_regime_h5': {r0: pick(regs, 'ls_mean_bp', regime=r0,
                                     horizon=PREREG['primary_horizon'])
                            for r0 in regs['regime'].unique()},
        'sign_flip': bool(regs[regs.horizon == PREREG['primary_horizon']]
                          ['ls_mean_bp'].min() < 0 <
                          regs[regs.horizon == PREREG['primary_horizon']]
                          ['ls_mean_bp'].max())}
    out['section42_price_layer_params'] = {
        'momentum': PREREG['momentum'], 'holding': PREREG['horizons'],
        'quantile': PREREG['quantiles'], 'cost_bp': PREREG['cost_bp'],
        'wf_windows': PREREG['wf_windows']}
    out['section43_parameter_grid'] = {
        'rows': int(len(pg)),
        'indmom_pos_share': float((pg['ls_mean_bp_indmom'] > 0).mean()),
        'indmom_pos_share_c30': float((pg['ls_mean_bp_indmom_c30'] > 0).mean()),
        'indmom_min': float(pg['ls_mean_bp_indmom'].min()),
        'indmom_max': float(pg['ls_mean_bp_indmom'].max())}
    with open(os.path.join(DATA, 'hep_regime_summary.json'), 'w',
              encoding='utf-8') as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    lg('summary written')
    lg('DONE regime')


if __name__ == '__main__':
    main()
