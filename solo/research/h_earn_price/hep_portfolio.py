# -*- coding: utf-8 -*-
"""H-EARN-PRICE-01 step 4: tradable portfolio layer.

  section 19  long-short portfolio (top/bottom 10% and 20%)
  section 20  neutralisation ladder (raw / market / industry / industry+mom)
  section 33  cost ladder (0 / 10 / 20 / 30 / 50 bp round trip)
  section 34  signal density, exposure, turnover, holding period
  section 44  tail dependence (leave-top-1%, leave-top-5%)
  section 45  stock concentration
  section 46  industry concentration
  section 47  size concentration
  section 48  counterfactual A (controls only) vs B (controls + forecast)
  section 49  counterfactual: shuffled forecast rank
  section 50  counterfactual: matched control sample

Book convention: 1x long the top quantile, 1x short the bottom quantile of
the FROZEN forecast, formed on the event day, held H sessions from the T0
close.  A-share shorting is not generally executable - all short-leg results
are research diagnostics (see PREREG note).
"""
import os, sys, json
from math import erfc, sqrt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hep_common import (PREREG, DATA, Log, build_price_grid, load_calendar,
                        build_return_sets, ic_stats)

lg = Log('hep_portfolio')
H_LIST = list(PREREG['horizons'])
Q_LIST = list(PREREG['quantiles'])
COST_BP = list(PREREG['cost_bp'])
DAY = 'T0'
RNG = np.random.default_rng(PREREG['seed'])
MIN_LEG = 10


def p_two(t):
    return float(erfc(abs(t) / sqrt(2))) if np.isfinite(t) else np.nan


def book(ev, R, H, q, coh, cpos, N, cost_bp=0.0, excl=None, leg='ls'):
    """Daily spread series of an H-session holding book.

    ev    : event frame (row aligned to R)
    R     : DataFrame of daily returns, columns = offsets 1..H
    q     : tail quantile (0.10 / 0.20)
    coh   : cohort index per event; cpos : calendar position per cohort
    excl  : boolean mask of events removed from the universe
    leg   : 'ls' (long minus short) / 'long' / 'short'
    Returns dict(daily, contrib, active, n_long_mean, n_short_mean)
    """
    n = len(ev)
    fc = pd.Series(ev['forecast_surprise'].to_numpy()).groupby(coh).rank(
        pct=True, method='first').to_numpy()
    keep = np.ones(n, dtype=bool) if excl is None else ~np.asarray(excl)
    long = keep & (fc > 1.0 - q)
    short = keep & (fc <= q)
    ncoh = int(cpos.shape[0])
    daily = np.zeros(N)
    contrib = np.zeros(n)
    nlg = np.zeros(ncoh)
    nsh = np.zeros(ncoh)
    for h in range(1, H + 1):
        v = np.asarray(R[h], dtype=float)
        ok = np.isfinite(v)
        sl = long & ok
        ss = short & ok
        cL = np.bincount(coh[sl], minlength=ncoh)
        cS = np.bincount(coh[ss], minlength=ncoh)
        sL = np.bincount(coh[sl], weights=v[sl], minlength=ncoh)
        sS = np.bincount(coh[ss], weights=v[ss], minlength=ncoh)
        good = (cL >= MIN_LEG) & (cS >= MIN_LEG)
        mL = np.where(cL > 0, sL / np.maximum(cL, 1), np.nan)
        mS = np.where(cS > 0, sS / np.maximum(cS, 1), np.nan)
        if leg == 'long':
            spread = np.where(good, mL, 0.0)
        elif leg == 'short':
            spread = np.where(good, -mS, 0.0)
        else:
            spread = np.where(good, mL - mS, 0.0)
        tgt = cpos + h
        m2 = tgt < N
        np.add.at(daily, tgt[m2], (spread / H)[m2])
        nlg += np.where(good, cL, 0)
        nsh += np.where(good, cS, 0)
        # per-name contribution (equal weight inside each leg)
        w = np.zeros(n)
        gL = good[coh] & sl
        gS = good[coh] & ss
        w[gL] = 1.0 / np.maximum(cL[coh[gL]], 1)
        w[gS] = -1.0 / np.maximum(cS[coh[gS]], 1)
        contrib += (w * np.nan_to_num(v)) / H
    active = daily != 0.0
    if cost_bp > 0:
        nleg = 1.0 if leg in ('long', 'short') else 2.0
        daily = daily - np.where(active, nleg * (cost_bp / 1e4) / H, 0.0)
    return {'daily': daily, 'contrib': contrib, 'active': active,
            'n_long': float(np.mean(nlg[nlg > 0])) / H if (nlg > 0).any() else np.nan,
            'n_short': float(np.mean(nsh[nsh > 0])) / H if (nsh > 0).any() else np.nan}


def stats(res, H, span):
    """Performance summary of a book over the in-sample span."""
    d = res['daily'][span]
    act = res['active'][span]
    a = d[act]
    if act.sum() < 5:
        return {'exposure': np.nan, 'mean_bp_all': np.nan,
                'mean_bp_active': np.nan, 't_active': np.nan,
                'ann_all': np.nan, 'ann_active': np.nan,
                'sharpe': np.nan, 'max_dd': np.nan, 'hit': np.nan}
    mu, t, icir, n = ic_stats(pd.Series(a), H)
    sd = float(np.std(a, ddof=1))
    eq = np.cumprod(1.0 + d)
    dd = float(np.min(eq / np.maximum.accumulate(eq) - 1.0))
    return {'exposure': float(act.mean()),
            'mean_bp_all': float(np.mean(d)) * 1e4,
            'mean_bp_active': float(np.mean(a)) * 1e4,
            't_active': t,
            'ann_all': float(np.mean(d)) * 252,
            'ann_active': float(np.mean(a)) * 252,
            'sharpe': float(np.mean(a) / sd * np.sqrt(252)) if sd > 0 else np.nan,
            'max_dd': dd, 'hit': float((a > 0).mean()), 'n_days': int(act.sum())}


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
    lg('events: %d  days %d' % (len(ev), ev[DAY].nunique()))

    mkt = pd.read_parquet(os.path.join(DATA, 'aux_market_cum.parquet'))
    mkt_lvl = np.asarray(mkt.iloc[:, 0], dtype=float)
    mkt_ret = np.asarray(pd.read_parquet(
        os.path.join(DATA, 'aux_market.parquet'))['mkt_ret'], dtype=float)
    mkt_ret = np.nan_to_num(mkt_ret)

    KMAX = max(H_LIST)
    RETSETS, ctx = build_return_sets(close, mkt_lvl, ev, si, i0, KMAX)
    l1 = ctx['l1']
    Rmkt = RETSETS['market_neutral']
    lg('return matrices ready (raw / market / industry / industry+momentum)')

    t0i = ev['T0_idx'].to_numpy()
    coh, cpos = pd.factorize(t0i)
    span = np.zeros(N, dtype=bool)
    span[int(cpos.min()):min(N, int(cpos.max()) + KMAX + 1)] = True
    lg('in-sample span: %d calendar days' % int(span.sum()))

    # ---------------- section 19/20/33: L/S grid + cost ladder -----------
    rows = []
    for leg in ('ls', 'long', 'short'):
        for rn, R in RETSETS.items():
            for h in H_LIST:
                for q in Q_LIST:
                    res = book(ev, R, h, q, coh, cpos, N, leg=leg)
                    s = stats(res, h, span)
                    s.update({'leg': leg, 'return_type': rn, 'horizon': h,
                              'quantile': q, 'cost_bp': 0})
                    rows.append(s)
                    for cb in COST_BP:
                        if cb == 0:
                            continue
                        r2 = book(ev, R, h, q, coh, cpos, N, cost_bp=cb,
                                  leg=leg)
                        s2 = stats(r2, h, span)
                        s2.update({'leg': leg, 'return_type': rn, 'horizon': h,
                                   'quantile': q, 'cost_bp': cb})
                        rows.append(s2)
    cost = pd.DataFrame(rows)
    cost.to_csv(os.path.join(DATA, 'h_earn_price_cost.csv'), index=False)
    lg('section19/20/33 cost ladder rows %d' % len(cost))
    pri = cost[(cost.cost_bp.isin([0, PREREG['primary_cost_bp']]))
               & (cost['quantile'] == PREREG['primary_quantile'])
               & (cost.horizon.isin([1, 5, 20]))]
    for _, r in pri.sort_values(['leg', 'return_type', 'horizon',
                                 'cost_bp']).iterrows():
        lg('   %-4s %-26s H%-3d cost=%2dbp  mean_active=%+7.1fbp t=%+6.2f '
           'ann_act=%+7.3f exposure=%.3f sharpe=%s maxDD=%.3f'
           % (r['leg'], r['return_type'], r['horizon'], r['cost_bp'],
              r['mean_bp_active'], r['t_active'], r['ann_active'],
              r['exposure'],
              ('%+.2f' % r['sharpe']) if np.isfinite(r['sharpe']) else 'NA',
              r['max_dd']))

    # ---------------- section 34: exposure / turnover --------------------
    ex_rows = []
    for rn, R in RETSETS.items():
        for h in H_LIST:
            for q in Q_LIST:
                res = book(ev, R, h, q, coh, cpos, N)
                ex_rows.append({'return_type': rn, 'horizon': h, 'quantile': q,
                                'signal_days': int(np.sum(res['active'] & span)),
                                'span_days': int(span.sum()),
                                'exposure': float(np.mean(res['active'][span])),
                                'avg_holding_days': h,
                                'turnover_per_day': float(2.0 / h),
                                'avg_names_long': res['n_long'],
                                'avg_names_short': res['n_short'],
                                'cost_bp_primary': PREREG['primary_cost_bp']})
    pd.DataFrame(ex_rows).to_csv(
        os.path.join(DATA, 'h_earn_price_exposure.csv'), index=False)
    lg('section34 exposure rows %d' % len(ex_rows))

    # ---------------- section 44/45/46: concentration --------------------
    q0 = PREREG['primary_quantile']
    h0 = PREREG['primary_horizon']
    base = book(ev, Rmkt, h0, q0, coh, cpos, N)
    contrib = base['contrib']
    tot = float(np.nansum(contrib))
    abs_c = np.abs(contrib)
    order = np.argsort(-abs_c)
    conc = []
    for pct in (0.01, 0.05, 0.10):
        k = max(1, int(round(pct * len(ev))))
        top = order[:k]
        share = float(abs_c[top].sum() / abs_c.sum()) if abs_c.sum() > 0 else np.nan
        exc = np.zeros(len(ev), dtype=bool)
        exc[top] = True
        r2 = book(ev, Rmkt, h0, q0, coh, cpos, N, excl=exc)
        s2 = stats(r2, h0, span)
        conc.append({'dim': 'stock', 'bucket': 'top_%d%%' % int(pct * 100),
                     'n': k, 'abs_share': share,
                     'mean_bp_after_exclusion': s2['mean_bp_active'],
                     't_after_exclusion': s2['t_active']})
    lg('section44/45 tail dependence (primary book, total contrib %+.4f):' % tot)
    for c in conc:
        lg('   leave out %-8s n=%-5d abs_share=%.4f -> mean %+7.1fbp t=%+6.2f'
           % (c['bucket'], c['n'], c['abs_share'],
              c['mean_bp_after_exclusion'], c['t_after_exclusion']))
    ind_c = pd.DataFrame({'sw_l1': l1, 'c': contrib}).groupby('sw_l1')['c'].sum()
    ind_share = ind_c / tot if tot != 0 else ind_c * np.nan
    for nm, v in ind_share.sort_values(key=np.abs, ascending=False).head(8).items():
        conc.append({'dim': 'industry', 'bucket': nm,
                     'n': int((l1 == nm).sum()),
                     'abs_share': float(v),
                     'mean_bp_after_exclusion': np.nan,
                     't_after_exclusion': np.nan})
        lg('   industry %-16s contrib_share=%+.4f  n=%d' % (nm, v, (l1 == nm).sum()))
    flag_ind = float(ind_share.abs().max())
    lg('   INDUSTRY_CONCENTRATED flag (max |share| > %.2f): %s'
       % (PREREG['industry_concentration_threshold'],
          flag_ind > PREREG['industry_concentration_threshold']))

    # section 47: size buckets
    mv = ev['log_mv'].to_numpy()
    mvr = pd.Series(mv).groupby(ev[DAY]).rank(pct=True).to_numpy()
    for nm, lo, hi in (('Small', 0.0, 1 / 3), ('Mid', 1 / 3, 2 / 3),
                       ('Large', 2 / 3, 1.0)):
        sel = (mvr > lo) & (mvr <= hi)
        sub = ev[sel].reset_index(drop=True)
        subR = Rmkt.loc[sel].reset_index(drop=True)
        cs, cp = pd.factorize(sub['T0_idx'].to_numpy())
        r2 = book(sub, subR, h0, q0, cs, cp, N)
        s2 = stats(r2, h0, span)
        ic = pd.DataFrame({DAY: sub[DAY].to_numpy(),
                           'p': sub['forecast_surprise'].to_numpy(),
                           'r': sub['exmkt%d' % h0].to_numpy()}).dropna()
        ic = ic[ic.groupby(DAY)['p'].transform('count') >= 15]
        d_ic = ic.groupby(DAY).apply(
            lambda x: x['p'].rank().corr(x['r'].rank()), include_groups=False)
        mu_ic, t_ic, _, nd = ic_stats(d_ic, 3)
        conc.append({'dim': 'size', 'bucket': nm, 'n': int(sel.sum()),
                     'abs_share': np.nan,
                     'mean_bp_after_exclusion': s2['mean_bp_active'],
                     't_after_exclusion': s2['t_active'],
                     'ic': mu_ic, 'ic_t': t_ic})
        lg('   size %-6s n=%-6d L/S mean=%+7.1fbp t=%+6.2f  IC=%+.4f t=%+6.2f'
           % (nm, int(sel.sum()), s2['mean_bp_active'], s2['t_active'],
              mu_ic, t_ic))
    pd.DataFrame(conc).to_csv(
        os.path.join(DATA, 'h_earn_price_concentration.csv'), index=False)

    # ---------------- section 48/49/50: counterfactuals ------------------
    cf = []
    zf = pd.Series(ev['forecast_surprise'].to_numpy()).groupby(ev[DAY]).transform(
        lambda x: (x - x.mean()) / x.std() if x.std() else 0.0).to_numpy()
    ctls = {'z_mom20': ev['mom20'], 'z_mom60': ev['mom60'],
            'z_log_mv': ev['log_mv'], 'z_vol20': ev['vol20'],
            'z_turnover': ev['turnover']}
    Z = {}
    for c, s in ctls.items():
        Z[c] = pd.Series(pd.to_numeric(s, errors='coerce').to_numpy()).groupby(
            ev[DAY]).transform(
            lambda x: (x - x.mean()) / x.std() if x.std() else 0.0).to_numpy()
    scoreA = pd.DataFrame({c: Z[c] for c in ctls}).mean(axis=1).to_numpy()
    for nm, sig in (('A_controls_only', scoreA),
                    ('B_controls_plus_forecast', scoreA + zf),
                    ('Forecast_only', zf)):
        sub = ev.copy()
        sub['forecast_surprise'] = sig
        cs, cp = pd.factorize(sub['T0_idx'].to_numpy())
        r2 = book(sub, Rmkt, h0, q0, cs, cp, N)
        s2 = stats(r2, h0, span)
        cf.append({'counterfactual': nm, 'horizon': h0, 'quantile': q0,
                   'mean_bp_active': s2['mean_bp_active'],
                   't_active': s2['t_active'], 'ann_active': s2['ann_active']})
        lg('section48 %-26s L/S mean=%+7.1fbp t=%+6.2f ann=%+.3f'
           % (nm, s2['mean_bp_active'], s2['t_active'], s2['ann_active']))
    a = cf[0]['mean_bp_active']
    b = cf[1]['mean_bp_active']
    lg('section48 incremental B - A = %+.1fbp per %d sessions' % (b - a, h0))

    # section 49: shuffled forecast rank, 200 draws
    nperm = 200
    nulls = np.empty(nperm)
    for it in range(nperm):
        sub = ev.copy()
        sub['forecast_surprise'] = ev.groupby(DAY)['forecast_surprise'].transform(
            lambda x: RNG.permutation(x.to_numpy()))
        cs, cp = pd.factorize(sub['T0_idx'].to_numpy())
        s2 = stats(book(sub, Rmkt, h0, q0, cs, cp, N), h0, span)
        nulls[it] = s2['mean_bp_active']
    ps49 = float((nulls >= cf[2]['mean_bp_active']).mean())
    lg('section49 shuffled-forecast null: mean=%+.1fbp sd=%.1f p=%.3f '
       '(true %+.1fbp)' % (np.nanmean(nulls), np.nanstd(nulls), ps49,
                           cf[2]['mean_bp_active']))
    cf.append({'counterfactual': 'N49_shuffled_forecast', 'horizon': h0,
               'quantile': q0, 'mean_bp_active': float(np.nanmean(nulls)),
               't_active': np.nan, 'ann_active': np.nan,
               'empirical_p': ps49, 'n_draws': nperm})

    # section 50: matched control on (industry, size, momentum)
    fcr = pd.Series(ev['forecast_surprise'].to_numpy()).groupby(ev[DAY]).rank(
        pct=True, method='first').to_numpy()
    treat = fcr > 1.0 - q0
    pool = (fcr > q0) & (fcr <= 1.0 - q0)
    mm = np.nan_to_num(Z['z_log_mv']) + np.nan_to_num(Z['z_mom20'])
    t50 = []
    for d in pd.unique(ev[DAY].to_numpy()):
        s = np.flatnonzero((ev[DAY].to_numpy() == d) & treat)
        p = np.flatnonzero((ev[DAY].to_numpy() == d) & pool)
        if len(s) == 0 or len(p) < 5:
            continue
        for i in s:
            cand = p[l1[p] == l1[i]]
            if len(cand) == 0:
                cand = p
            j = cand[np.argmin(np.abs(mm[cand] - mm[i]))]
            t50.append((ev['exmkt%d' % h0].iloc[i] - ev['exmkt%d' % h0].iloc[j], d))
    if t50:
        s50 = pd.DataFrame(t50, columns=['v', 'd']).dropna()
        g = s50.groupby('d')['v'].agg(['mean', 'count'])
        g = g[g['count'] >= 5]
        mu50, t50s, _, n50 = ic_stats(g['mean'], 3)
        lg('section50 matched control (industry+size+momentum): '
           'treat-minus-control = %+.1fbp t=%+6.2f days=%d'
           % (mu50 * 1e4, t50s, n50))
        cf.append({'counterfactual': 'N50_matched_control', 'horizon': h0,
                   'quantile': q0, 'mean_bp_active': mu50 * 1e4,
                   't_active': t50s, 'ann_active': np.nan, 'n_pairs': len(s50)})
    pd.DataFrame(cf).to_csv(
        os.path.join(DATA, 'h_earn_price_counterfactual.csv'), index=False)
    lg('DONE portfolio')


if __name__ == '__main__':
    main()
