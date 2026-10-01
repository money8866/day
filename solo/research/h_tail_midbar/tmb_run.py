# -*- coding: utf-8 -*-
"""H-TAIL-MIDBAR-01 -- main run.

Every rule is transcribed from the frozen SPEC; see tmb_common.PREREG_T.
Outputs (all in this directory):
    H_TAIL_MIDBAR_01_EVENTS.csv        core matrix + incremental ladder
    H_TAIL_MIDBAR_01_MA.csv            MA-structure strata
    H_TAIL_MIDBAR_01_VOLUME.csv        volume-gate strata
    H_TAIL_MIDBAR_01_THRESHOLD.csv     2..7% threshold axis
    H_TAIL_MIDBAR_01_NULL.csv          four null families
    H_TAIL_MIDBAR_01_BOOTSTRAP.csv     monthly cluster / block bootstrap
    H_TAIL_MIDBAR_01_OOS.csv           IS / Validation / OOS + yearly
    H_TAIL_MIDBAR_01_WALK_FORWARD.csv  expanding-window folds
    H_TAIL_MIDBAR_01_TAIL.csv          leave-tail test
    H_TAIL_MIDBAR_01_PARAM_GRID.csv    450-cell pre-registered sweep
    H_TAIL_MIDBAR_01_REGIME.csv        external market description
    H_TAIL_MIDBAR_01_OVERLAP.csv       overlap / independence audit
    H_TAIL_MIDBAR_01_BIAS_AUDIT.csv    eight selection-bias checks
    H_TAIL_MIDBAR_01_RESULTS.json      raw blocks consumed by has_report.py
"""
import os
import sys
import json
import time

import numpy as np
import pandas as pd

import tmb_common as T

P = T.PREREG_T
SEED = P['seed']
COST = P['primary_cost_bp']
THRS = P['thr_grid']
WINS = P['first_windows']
GRID_MA = P['grid_ma']
GRID_VR = P['grid_vr']


def lite(r):
    x = np.asarray(r, dtype='float64')
    x = x[np.isfinite(x)]
    if x.size == 0:
        return dict(n=0, mean=np.nan, median=np.nan, net30=np.nan, win=np.nan,
                    pf30=np.nan)
    xn = x - COST / 10000.0
    gs = float(xn[xn > 0].sum())
    ls = float(-xn[xn < 0].sum())
    return dict(n=int(x.size), mean=float(x.mean()),
                median=float(np.median(x)), net30=float(xn.mean()),
                win=float((xn > 0).mean()),
                pf30=(gs / ls) if ls > 0 else np.nan)


def phase_of(t_abs, dates):
    y = int(dates[int(t_abs)][:4])
    a, b = P['phase_IS']
    if a <= y <= b:
        return 'IS'
    a, b = P['phase_VALID']
    if a <= y <= b:
        return 'VALID'
    a, b = P['phase_OOS']
    if a <= y <= b:
        return 'OOS'
    return 'PRE' if y < P['phase_IS'][0] else 'OUT'


def main():
    lg = T.Log('tmb_run')
    lg('=' * 72)
    lg('H-TAIL-MIDBAR-01  first >=3%% bullish mid-bar -> next-close exit')
    lg('=' * 72)
    if not T.verify_spec(lg):
        return 2

    g = T.load_grid(lg)
    st = T.State(g, lg)
    dates = st.dates
    S, N, c0, s0 = st.S, st.N, st.c0, st.s0
    res = {}

    def ev(msk):
        return st.events(msk)

    def describe(si, ti, j, r):
        met = T.metrics(r, do_boot=True, months=st.months(ti))
        return met

    # =================================================================
    # A. arms (SPEC section 7) + incremental ladder (SPEC section 15)
    # =================================================================
    lg.sep()
    lg('A. arms + ladder')
    E0 = st.e0(P['thr_primary'])
    F2 = st.first(P['thr_primary'], P['first_primary'])
    A = {}
    A['RAW'] = E0
    A['+F'] = F2
    A['+F+MA'] = F2 & st.MAANY
    A['+F+V'] = F2 & st.V['V5']
    A['+F+MA+V'] = F2 & st.MAANY & st.V['V5']
    ARMNAME = {'RAW': 'RAW (E0 3%)', '+F': 'B = First(F2)', '+F+MA': 'B + MA',
               '+F+V': 'B + VOL', '+F+MA+V': 'B + MA + VOL  [PRIMARY]'}
    PARENT = {'RAW': None, '+F': 'RAW', '+F+MA': '+F', '+F+V': '+F',
              '+F+MA+V': '+F+MA'}
    PARENT2 = {'+F+MA+V': '+F+V'}

    evd = {}
    rows = []
    for k in ('RAW', '+F', '+F+MA', '+F+V', '+F+MA+V'):
        s_, t_, j_, r_ = ev(A[k])
        evd[k] = (s_, t_, j_, r_)
        met = describe(s_, t_, j_, r_)
        d = {'arm': k, 'label': ARMNAME[k],
             'thr': P['thr_primary'], 'first_w': P['first_primary'],
             'ma': 'MApresent' if 'MA' in k else '',
             'vr': 'V5' if 'V' in k.split('+')[-1] else '',
             'entry': P['entry_primary'], 'exit': P['exit_primary']}
        d.update(met)
        # exit diagnostics -- the T+1 open / high / low are NEVER the strategy
        d['mean_open_t1'] = float(np.nanmean(T.leg(g, s_, t_, 1, 'open', 'close')))
        d['mean_high_t1'] = float(np.nanmean(T.leg(g, s_, t_, 1, 'high', 'close')))
        d['mean_low_t1'] = float(np.nanmean(T.leg(g, s_, t_, 1, 'low', 'close')))
        d['share_high_t1_gt_close'] = float(np.nanmean(
            T.leg(g, s_, t_, 1, 'high', 'close') > 0))
        p = PARENT[k]
        if p is not None:
            rp = evd[p][3] - COST / 10000.0
            rc = r_ - COST / 10000.0
            dd = T.diff_boot(rc, st.months(t_), rp, st.months(evd[p][1]),
                             B=P['boot_B'], seed=SEED)
            d.update({'incr_vs': p, 'incr_obs': dd['obs'], 'incr_lo': dd['lo'],
                      'incr_hi': dd['hi'], 'incr_p_ge0': dd['p_ge0'],
                      'incr_n_month': dd['n_month'],
                      'incr_win': met['win_net30'] - lite(rp)['win'],
                      'incr_pf30': met['pf30'] - lite(rp)['pf30']})
        p2 = PARENT2.get(k)
        if p2 is not None:
            rp2 = evd[p2][3] - COST / 10000.0
            rc = r_ - COST / 10000.0
            dd = T.diff_boot(rc, st.months(t_), rp2, st.months(evd[p2][1]),
                             B=P['boot_B'], seed=SEED)
            d.update({'incr2_vs': p2, 'incr2_obs': dd['obs'],
                      'incr2_lo': dd['lo'], 'incr2_hi': dd['hi'],
                      'incr2_p_ge0': dd['p_ge0']})
        rows.append(d)
        lg('  %-14s n=%7d  mean=%+.4f  net30=%+.4f  win30=%.3f  pf30=%.3f'
           % (k, met['n'], met['mean'], met['mean_net30'],
              met['win_net30'], met['pf30']))

    # second execution model: enter T+1 open, exit T+1 close (SPEC section 8)
    ps, pt, _, _ = evd['+F+MA+V']
    j1 = pt + 1
    o1 = g['open'][ps, j1].astype('float64')
    c1 = g['close'][ps, j1].astype('float64')
    with np.errstate(invalid='ignore'):
        r2nd = np.where((o1 > 0) & np.isfinite(o1) & np.isfinite(c1),
                        c1 / o1 - 1.0, np.nan)
    m2 = T.metrics(r2nd, do_boot=True, months=st.months(pt))
    d2 = {'arm': '+F+MA+V', 'label': ARMNAME['+F+MA+V'] + '  [entry OPEN_T1]',
          'thr': P['thr_primary'], 'first_w': P['first_primary'],
          'ma': 'MApresent', 'vr': 'V5', 'entry': P['entry_second'],
          'exit': P['exit_primary']}
    d2.update(m2)
    rows.append(d2)
    lg('  second execution model (T+1 open entry): n=%d net30=%+.4f'
       % (m2['n'], m2['mean_net30']))

    r_inc = dict((k, evd[k][3]) for k in evd)
    res['events'] = rows

    # =================================================================
    # B. MA structure strata (SPEC section 12)
    # =================================================================
    lg.sep()
    lg('B. MA structure strata (base = First(F2))')
    ma_rows = []
    base_s, base_t, base_j, base_r = evd['+F']
    nbase = base_s.size
    rbase = base_r - COST / 10000.0
    ma_keys = [('ALL', None), ('M1', st.M['M1']), ('M2', st.M['M2']),
               ('M3', st.M['M3']), ('M4', st.M['M4']),
               ('MApresent', st.MAANY)]
    for k, mm in ma_keys:
        msk = F2 if mm is None else (F2 & mm)
        s_, t_, j_, r_ = ev(msk)
        met = T.metrics(r_, do_boot=True, months=st.months(t_))
        d = {'stratum': k, 'base': 'First(F2)', 'n': met['n'],
             'share_of_base': float(met['n']) / nbase if nbase else np.nan}
        d.update(met)
        if k != 'ALL' and r_.size >= 2:
            dd = T.diff_boot(r_ - COST / 10000.0, st.months(t_), rbase,
                             st.months(base_t), B=P['boot_B'], seed=SEED)
            d.update({'incr_obs': dd['obs'], 'incr_lo': dd['lo'],
                      'incr_hi': dd['hi'], 'incr_p_ge0': dd['p_ge0']})
        ma_rows.append(d)
        lg('  %-10s n=%7d net30=%+.4f win30=%.3f pf30=%.3f'
           % (k, met['n'], met['mean_net30'], met['win_net30'], met['pf30']))
    res['ma'] = ma_rows

    # =================================================================
    # C. volume gate strata (SPEC section 12)
    # =================================================================
    lg.sep()
    lg('C. volume gate strata (base = First(F2))')
    vol_rows = []
    for k in ('V0', 'V1', 'V2', 'V3', 'V4', 'V5'):
        msk = F2 if k == 'V0' else (F2 & st.V[k])
        s_, t_, j_, r_ = ev(msk)
        met = T.metrics(r_, do_boot=True, months=st.months(t_))
        d = {'stratum': k, 'base': 'First(F2)',
             'rule': ('none' if k == 'V0' else
                      ('1.20<=VR20<=3.00' if k == 'V5' else
                       'VR20>=%.2f' % P['vr_gates'][k])),
             'n': met['n'],
             'share_of_base': float(met['n']) / nbase if nbase else np.nan}
        d.update(met)
        if k != 'V0' and r_.size >= 2:
            dd = T.diff_boot(r_ - COST / 10000.0, st.months(t_), rbase,
                             st.months(base_t), B=P['boot_B'], seed=SEED)
            d.update({'incr_obs': dd['obs'], 'incr_lo': dd['lo'],
                      'incr_hi': dd['hi'], 'incr_p_ge0': dd['p_ge0']})
        vol_rows.append(d)
        lg('  %-3s n=%7d net30=%+.4f win30=%.3f pf30=%.3f'
           % (k, met['n'], met['mean_net30'], met['win_net30'], met['pf30']))
    res['volume'] = vol_rows

    # =================================================================
    # D. threshold axis (SPEC section 13)
    # =================================================================
    lg.sep()
    lg('D. threshold axis 2..7%')
    thr_rows = []
    for thr in THRS:
        for base_kind in ('RAW', 'FULL'):
            if base_kind == 'RAW':
                msk = st.e0(thr)
            else:
                msk = (st.e0(thr) & st.first(thr, P['first_primary'])
                       & st.MAANY & st.V['V5'])
            s_, t_, j_, r_ = ev(msk)
            met = T.metrics(r_, do_boot=False)
            d = {'thr': thr, 'base': base_kind, 'n': met['n']}
            d.update(met)
            thr_rows.append(d)
    res['threshold'] = thr_rows
    mono = {}
    for base_kind in ('RAW', 'FULL'):
        sub = [r for r in thr_rows if r['base'] == base_kind
               and r['n'] >= P['min_n']]
        if len(sub) >= 3:
            x = np.array([r['thr'] for r in sub], dtype='float64')
            y = np.array([r['mean_net30'] for r in sub], dtype='float64')
            mono[base_kind] = float(pd.Series(x).corr(pd.Series(y),
                                                      method='spearman'))
        else:
            mono[base_kind] = np.nan
    res['threshold_monotonicity'] = mono
    for r in thr_rows:
        lg('  thr=%d%% %-4s n=%7d net30=%+.4f' % (int(round(r['thr'] * 100)),
                                                  r['base'], r['n'],
                                                  r['mean_net30']))
    lg('  spearman(thr, net30): RAW=%s  FULL=%s'
       % (T.fmt(mono['RAW']), T.fmt(mono['FULL'])))

    # =================================================================
    # E. null models (SPEC section 14)
    # =================================================================
    lg.sep()
    lg('E. null models (observed set = PRIMARY ARM)')
    nsel = np.zeros((S, N), dtype=bool)
    nsel[:, s0:N - 1] = True
    pool_mask = st.el & nsel & np.isfinite(st.Rg)
    psi, psj = np.nonzero(pool_mask)
    self_all = (psi.astype('int64') * N + psj.astype('int64'))
    rflat = st.Rg.reshape(-1)
    del pool_mask
    lg('  pool cells (eligible, study window, tradable next close) = %d'
       % psi.size)

    esi, eti, esj, er = evd['+F+MA+V']
    esi = esi.astype('int64')
    ev_self = esi * N + esj.astype('int64')
    obs_gross = float(np.nanmean(er))
    rb = T.ret_bucket(st.pc)
    vb = T.vr_bucket(st.vr20)
    up = st.cl > st.o

    fam = {}
    # N1 random stock-day, key = trading day
    k1 = psj.astype('int64')
    flat, lo, hi, uq = T.build_pools(self_all, k1)
    fam['N1_STOCK_DAY'] = T.null_family(rflat, flat, lo, hi, uq,
                                        esj.astype('int64'), ev_self,
                                        obs_gross, B=P['null_B'], seed=SEED)
    del flat, lo, hi, uq, k1
    # N2 same-stock random day, key = stock
    k2 = psi.astype('int64')
    flat, lo, hi, uq = T.build_pools(self_all, k2)
    fam['N2_STOCK_RANDOM_DAY'] = T.null_family(rflat, flat, lo, hi, uq, esi,
                                               ev_self, obs_gross,
                                               B=P['null_B'], seed=SEED)
    del flat, lo, hi, uq, k2
    # N3 same stock + matched state (MA code x return bucket x VR bucket)
    state = (st.MAcode[psi, psj].astype('int64') * 100
             + (rb[psi, psj].astype('int64') + 1) * 10
             + vb[psi, psj].astype('int64'))
    k3 = psi.astype('int64') * 1000 + state
    flat, lo, hi, uq = T.build_pools(self_all, k3)
    estate = (st.MAcode[esi, esj].astype('int64') * 100
              + (rb[esi, esj].astype('int64') + 1) * 10
              + vb[esi, esj].astype('int64'))
    ek3 = esi * 1000 + estate
    fam['N3_STOCK_STATE_DAY'] = T.null_family(rflat, flat, lo, hi, uq, ek3,
                                              ev_self, obs_gross,
                                              B=P['null_B'], seed=SEED)
    del flat, lo, hi, uq, k3, state, estate, ek3
    # N4 random >=3% upward bar, same day + same return bucket, close > open
    keep4 = up[psi, psj] & (rb[psi, psj] >= 0)
    c4, p4, j4 = self_all[keep4], psi[keep4], psj[keep4]
    k4 = (j4.astype('int64') * 10 + rb[p4, j4].astype('int64'))
    flat, lo, hi, uq = T.build_pools(c4, k4)
    ek4 = (esj.astype('int64') * 10 + rb[esi, esj].astype('int64'))
    fam['N4_RANDOM_UP_BAR'] = T.null_family(rflat, flat, lo, hi, uq, ek4,
                                            ev_self, obs_gross,
                                            B=P['null_B'], seed=SEED)
    del flat, lo, hi, uq, k4, c4, p4, j4, ek4
    null_rows = []
    for k, v in fam.items():
        d = dict(v)
        d['family'] = k
        d['adopted'] = bool(v['resolution'] >= P['null_min_resolution'])
        d['adopted_rule'] = ('resolution >= %.2f' % P['null_min_resolution'])
        null_rows.append(d)
        lg('  %-22s obs=%+.4f  null=%+.4f [%+.4f, %+.4f]  p=%.4f  res=%.4f  %s'
           % (k, v['obs'], v['null_mean'], v['null_lo'], v['null_hi'],
              v['p_one_sided'], v['resolution'],
              'ADOPTED' if d['adopted'] else 'NOT-ADOPTED'))
    res['null'] = null_rows

    # =================================================================
    # F. bootstrap (SPEC section 19)
    # =================================================================
    lg.sep()
    lg('F. bootstrap')
    boot_rows = []
    for k in ('RAW', '+F', '+F+MA', '+F+V', '+F+MA+V'):
        s_, t_, j_, r_ = evd[k]
        xn = r_ - COST / 10000.0
        b = T.boot_mean(xn, st.months(t_), B=P['boot_B'], seed=SEED)
        bm, bl, bh = T.R.block_boot_mean(xn, block=20, B=P['boot_B'], seed=SEED)
        boot_rows.append({'arm': k, 'method': 'monthly_cluster',
                          'n': b['n'], 'n_cluster': b['n_month'],
                          'mean': b['mean'], 'lo': b['lo'], 'hi': b['hi'],
                          'p_one_sided_le0': b['p_le0']})
        boot_rows.append({'arm': k, 'method': 'block_20_sessions',
                          'n': int(xn.size), 'n_cluster': int(np.ceil(
                              max(xn.size, 1) / 20.0)),
                          'mean': bm, 'lo': bl, 'hi': bh,
                          'p_one_sided_le0': np.nan})
    res['bootstrap'] = boot_rows
    for b in boot_rows:
        lg('  %-10s %-18s mean=%+.4f [%+.4f, %+.4f] p=%.4f'
           % (b['arm'], b['method'], b['mean'], b['lo'], b['hi'],
              b['p_one_sided_le0']))

    # =================================================================
    # G. OOS / phases / yearly (SPEC sections 17, 18)
    # =================================================================
    lg.sep()
    lg('G. phases + yearly stability')
    oos_rows = []
    oos_by_arm = {}
    for k in ('RAW', '+F', '+F+MA', '+F+V', '+F+MA+V'):
        s_, t_, j_, r_ = evd[k]
        ph = np.array([phase_of(x, dates) for x in t_])
        yr = st.years(t_)
        oos_by_arm[k] = {}
        for scope in ('FULL', 'IS', 'VALID', 'OOS'):
            sel = np.ones(t_.size, bool) if scope == 'FULL' else (ph == scope)
            r_s = r_[sel]
            m = T.metrics(r_s, do_boot=(scope in ('FULL', 'OOS')),
                          months=st.months(t_[sel]))
            d = {'arm': k, 'scope': scope, 'years': '', 'n': m['n']}
            d.update(m)
            if scope == 'OOS':
                oos_by_arm[k]['OOS'] = m
            oos_rows.append(d)
        for y in (2022, 2023, 2024, 2025, 2026):
            sel = (yr == y)
            m = T.metrics(r_[sel], do_boot=False, months=st.months(t_[sel]))
            d = {'arm': k, 'scope': 'YEAR_%d' % y, 'years': y, 'n': m['n']}
            d.update(m)
            oos_rows.append(d)
            if k == '+F+MA+V':
                oos_by_arm[k][str(y)] = m
    res['oos'] = oos_rows
    for r in oos_rows:
        if r['arm'] in ('+F+MA+V', 'RAW') and r['scope'] in (
                'FULL', 'IS', 'VALID', 'OOS'):
            lg('  %-10s %-6s n=%7d net30=%+.4f win30=%.3f pf30=%.3f'
               % (r['arm'], r['scope'], r['n'], r['mean_net30'],
                  r['win_net30'], r['pf30']))
    for y in (2022, 2023, 2024, 2025, 2026):
        r = [x for x in oos_rows if x['arm'] == '+F+MA+V'
             and x['scope'] == 'YEAR_%d' % y][0]
        lg('  yearly %d  n=%7d net30=%+.4f' % (y, r['n'], r['mean_net30']))
    res['oos_by_arm'] = oos_by_arm

    # =================================================================
    # H. walk-forward folds (SPEC section 17)
    # =================================================================
    lg.sep()
    lg('H. walk-forward')
    wf_rows = []
    FOLDS = ((2022, 2023, 2024), (2022, 2024, 2025), (2022, 2025, 2026))
    ss, tt, jj, rr = evd['+F+MA+V']
    yrs = st.years(tt)
    for a, b, c in FOLDS:
        tr = (yrs >= a) & (yrs <= b)
        te = (yrs == c)
        m_tr = T.metrics(rr[tr] - COST / 10000.0, do_boot=False)
        m_te = T.metrics(rr[te], do_boot=True, months=st.months(tt[te]))
        wf_rows.append({'fold': 'train_%d_%d__test_%d' % (a, b, c),
                        'train_years': '%d-%d' % (a, b), 'test_year': c,
                        'n_train': int(tr.sum()), 'net30_train': m_tr['mean'],
                        'n_test': m_te['n'], 'mean_test': m_te['mean'],
                        'net30_test': m_te['mean_net30'],
                        'win30_test': m_te['win_net30'],
                        'pf30_test': m_te['pf30'],
                        'boot_lo': m_te.get('boot_lo'),
                        'boot_hi': m_te.get('boot_hi'),
                        'boot_p_le0': m_te.get('boot_p_le0')})
        lg('  fold train %d-%d -> test %d : n_test=%d net30=%+.4f'
           % (a, b, c, m_te['n'], m_te['mean_net30']))
    res['walk_forward'] = wf_rows

    # =================================================================
    # I. regime description (SPEC section 16) -- descriptive only
    # =================================================================
    lg.sep()
    lg('I. regime description (no parameter uses it)')
    reg = np.asarray(T.R.regime_by_day(g, lg)).astype(str)[st.sl]
    idxret = index_daily_ret(g)
    adv = np.array([float(np.nanmean(st.pc[st.el[:, k], k] > 0))
                    if st.el[:, k].any() else np.nan for k in range(N)])
    amt = np.array([float(np.nansum(g['amount'][st.el[:, k], c0 + k]))
                    if st.el[:, k].any() else np.nan for k in range(N)])
    study = np.arange(N) >= s0
    idx_w = idxret[c0:][:N]
    reg_rows = []
    for lab in ('ALL', 'BULL', 'RANGE', 'BEAR'):
        sel_day = study if lab == 'ALL' else (study & (reg == lab))
        s_, t_, j_, r_ = ev(A['+F+MA+V'] & sel_day[None, :])
        m = T.metrics(r_, do_boot=False)
        n_days = int(sel_day.sum())
        d = {'regime': lab, 'days': n_days,
             'index_up_ratio': (float(np.nanmean(idx_w[sel_day] > 0))
                                if n_days and np.isfinite(idx_w[sel_day]).any()
                                else np.nan),
             'mean_index_ret': (float(np.nanmean(idx_w[sel_day]))
                                if n_days else np.nan),
             'advance_ratio_mean': (float(np.nanmean(adv[sel_day]))
                                    if n_days else np.nan),
             'market_amount_mean': (float(np.nanmean(amt[sel_day]))
                                    if n_days else np.nan)}
        d.update(m)
        reg_rows.append(d)
        lg('  %-5s days=%4d  events=%6d  net30=%+.4f  idx_up=%s adv=%s'
           % (lab, n_days, m['n'], m['mean_net30'],
              T.fmt(d['index_up_ratio'], 3), T.fmt(d['advance_ratio_mean'], 3)))
    res['regime'] = reg_rows
    del adv, amt

    # =================================================================
    # J. tail test (SPEC section 11)
    # =================================================================
    lg.sep()
    lg('J. leave-tail test')
    tail_rows = []
    for k in ('RAW', '+F', '+F+MA', '+F+V', '+F+MA+V'):
        s_, t_, j_, r_ = evd[k]
        rf = np.asarray(r_, dtype='float64')
        rf = rf[np.isfinite(rf)]
        xn = rf - COST / 10000.0
        t1 = T.R.tail_table(xn, P['tail_levels'])
        t0 = T.R.tail_table(rf, P['tail_levels'])
        d = {'arm': k, 'n': t1['n'],
             'full_gross': t0['mean_full'], 'full_net30': t1['mean_full'],
             'leave_top_1pct_gross': t0['leave_top_1pct'],
             'leave_top_5pct_gross': t0['leave_top_5pct'],
             'leave_top_10pct_gross': t0['leave_top_10pct'],
             'leave_top_1pct_net30': t1['leave_top_1pct'],
             'leave_top_5pct_net30': t1['leave_top_5pct'],
             'leave_top_10pct_net30': t1['leave_top_10pct'],
             'tail_share_5pct_gross': t0['tail_share_5pct'],
             'tail_share_5pct_net30': t1['tail_share_5pct'],
             'median_gross': float(np.median(rf)),
             'median_net30': float(np.median(xn)),
             'top1_share': T._share(rf, 0.01),
             'top5_share': T._share(rf, 0.05)}
        tail_rows.append(d)
        lg('  %-10s full_net30=%+.4f  leave5_net30=%+.4f  top5_share=%.3f'
           % (k, t1['mean_full'], t1['leave_top_5pct'], d['top5_share']))
    res['tail'] = tail_rows

    # =================================================================
    # K. overlap / independence audit (SPEC section 20)
    # =================================================================
    lg.sep()
    lg('K. overlap audit')
    ov = {}
    for k in ('RAW', '+F', '+F+MA+V'):
        s_, t_, j_, r_ = evd[k]
        o = T.overlap_stats(s_, t_, P['overlap_windows'])
        o['arm'] = k
        ov[k] = o
        lg('  %-10s n=%d  adj_rate=%.4f  rate<=5=%.4f  rate<=20=%.4f  n_eff=%.1f'
           % (k, o['n_events'], o['adjacent_rate'], o['rate_le5'],
              o['rate_le20'], o['n_eff']))
    res['overlap'] = ov

    # =================================================================
    # L. parameter grid (SPEC section 22) -- pre-registered perturbation only
    # =================================================================
    lg.sep()
    lg('L. parameter grid (6 x 3 x 5 x 5 = 450 cells)')
    MGRID = [('ALL', None), ('M1', st.M['M1']), ('M2', st.M['M2']),
             ('M3', st.M['M3']), ('M4', st.M['M4'])]
    VGRID = [(None, None), (1.2, st.V['V1']), (1.5, st.V['V2']),
             (2.0, st.V['V3']), (3.0, st.V['V4'])]
    grid = []
    for thr in THRS:
        E = st.e0(thr)
        for w in WINS:
            base = T.first_mask(E, w)
            for vlab, vm in VGRID:
                for mlab, mm in MGRID:
                    m = base
                    if vm is not None:
                        m = m & vm
                    if mm is not None:
                        m = m & mm
                    s_, t_, j_, r_ = ev(m)
                    lm = lite(r_)
                    grid.append({'thr': int(thr * 100), 'first_w': w,
                                 'vr': ('none' if vlab is None else vlab),
                                 'ma': mlab, 'n': lm['n'], 'mean': lm['mean'],
                                 'median': lm['median'], 'net30': lm['net30'],
                                 'win30': lm['win'], 'pf30': lm['pf30']})
    gd = pd.DataFrame(grid)
    valid = gd['n'] >= P['min_n']
    pos_ratio = float((gd.loc[valid, 'net30'] > 0).mean()) if valid.any() else np.nan
    ax_mono = {}
    for col in ('thr', 'first_w', 'vr', 'ma'):
        grp = gd[valid].groupby(col)['net30'].mean()
        if len(grp) >= 3:
            x = np.arange(len(grp), dtype='float64')
            ax_mono[col] = float(pd.Series(x).corr(
                pd.Series(grp.to_numpy(dtype='float64')), method='spearman'))
        else:
            ax_mono[col] = np.nan
    near = gd[valid & (gd['thr'] == 3) & (gd['first_w'] == 10)]
    near_ratio = (float((near['net30'] > 0).mean()) if near.shape[0]
                  else np.nan)
    arg = gd.loc[gd[valid].index[gd.loc[valid, 'net30'].to_numpy().argmax()]]
    prim_net30 = [r for r in rows if r['arm'] == '+F+MA+V'][0]['mean_net30']
    pct = float((gd.loc[valid, 'net30'] <= prim_net30).mean())
    grid_sum = {'n_cells': int(gd.shape[0]), 'n_cells_valid': int(valid.sum()),
                'positive_ratio': pos_ratio,
                'axis_monotonicity': ax_mono,
                'primary_neighbourhood_positive_ratio': near_ratio,
                'primary_neighbourhood_cells': int(near.shape[0]),
                'argmax_cell': {'thr': int(arg['thr']), 'first_w': int(arg['first_w']),
                                'vr': arg['vr'], 'ma': arg['ma'],
                                'net30': float(arg['net30']), 'n': int(arg['n'])},
                'primary_net30': float(prim_net30),
                'primary_percentile_in_grid': pct,
                'primary_in_grid': False,
                'parameter_stable': bool(np.isfinite(pos_ratio) and pos_ratio >= 0.5),
                'parameter_fragile': bool(np.isfinite(pos_ratio) and pos_ratio < 0.5)}
    T.save_csv(gd, 'H_TAIL_MIDBAR_01_PARAM_GRID.csv')
    res['grid'] = {'rows': grid, 'summary': grid_sum}
    lg('  valid cells %d/%d  positive_ratio=%.3f  near(3%%,10)=%.3f'
       % (grid_sum['n_cells_valid'], grid_sum['n_cells'],
          pos_ratio if np.isfinite(pos_ratio) else np.nan,
          near_ratio if np.isfinite(near_ratio) else np.nan))
    lg('  argmax cell thr=%d%% w=%d vr=%s ma=%s net30=%+.4f (n=%d); '
       'PRIMARY net30=%+.4f at grid percentile %.3f'
       % (grid_sum['argmax_cell']['thr'], grid_sum['argmax_cell']['first_w'],
          grid_sum['argmax_cell']['vr'], grid_sum['argmax_cell']['ma'],
          grid_sum['argmax_cell']['net30'], grid_sum['argmax_cell']['n'],
          prim_net30, pct))

    # =================================================================
    # M. bias audit (SPEC section 21)
    # =================================================================
    lg.sep()
    lg('M. selection-bias audit')
    bias = []
    # 1 look_ahead ----------------------------------------------------
    gT = T.truncate(g, 120)
    stT = T.State(gT)
    checks = [('E0_3pct', E0, stT.e0(P['thr_primary'])),
              ('F1', st.first(P['thr_primary'], 5),
               stT.first(P['thr_primary'], 5)),
              ('F2', F2, stT.first(P['thr_primary'], P['first_primary'])),
              ('F3', st.first(P['thr_primary'], 20),
               stT.first(P['thr_primary'], 20)),
              ('MApresent', st.MAANY, stT.MAANY)]
    for nm in sorted(st.M):
        checks.append((nm, st.M[nm], stT.M[nm]))
    for nm in ('V1', 'V2', 'V3', 'V4', 'V5'):
        checks.append((nm, st.V[nm], stT.V[nm]))
    bad = []
    ncmp = min(st.N, stT.N)
    for nm, a_, b_ in checks:
        if not np.array_equal(a_[:, :ncmp], b_[:, :ncmp]):
            bad.append(nm)
    bias.append({'check': '1_look_ahead', 'verdict': 'PASS' if not bad else 'FAIL',
                 'detail': 'masks recomputed on a panel truncated by 120 sessions '
                           'are bit-identical over %d shared sessions (%d masks)'
                           % (ncmp, len(checks)),
                 'value': ('mismatch: %s' % ','.join(bad)) if bad else 'none'})
    del stT
    # 2 selection_bias ------------------------------------------------
    sb = 'PASS' if (grid_sum['primary_in_grid'] is False
                    and grid_sum['primary_percentile_in_grid'] < 0.90) else 'FLAG'
    bias.append({'check': '2_selection_bias', 'verdict': sb,
                 'detail': 'the PRIMARY configuration (thr=3%%, First(10), '
                           'V5 band, MApresent union) is not a point of the '
                           'pre-registered 450-cell grid and was fixed in the '
                           'SPEC before any result; its net30 sits at grid '
                           'percentile %.3f' % grid_sum['primary_percentile_in_grid'],
                 'value': 'grid argmax = thr=%d%%/w=%d/vr=%s/ma=%s'
                          % (grid_sum['argmax_cell']['thr'],
                             grid_sum['argmax_cell']['first_w'],
                             grid_sum['argmax_cell']['vr'],
                             grid_sum['argmax_cell']['ma'])})
    # 3 survivorship --------------------------------------------------
    gone = np.asarray(g['gone'])
    gone_rows = set(np.unique(psi).tolist()) & set(np.flatnonzero(gone).tolist())
    bias.append({'check': '3_survivorship_bias', 'verdict': 'FLAG',
                 'detail': 'the Tushare cache carries no point-in-time delisting '
                           'flag (delist_status=DELIST_PIT_UNAVAILABLE); the '
                           'universe is ST- and name-based only.  Stocks whose '
                           'last print falls >60 sessions before the panel end '
                           'are reported, not silently dropped',
                 'value': 'n_codes_gone=%d, of which %d carry a PRIMARY-arm event'
                          % (int(gone.sum()), len(gone_rows))})
    # 4 execution_bias ------------------------------------------------
    r_rec = T.leg(g, esi, eti, 1, 'close', 'close')
    dmax = float(np.nanmax(np.abs(r_rec.astype('float32').astype('float64') - er)))
    bias.append({'check': '4_execution_bias',
                 'verdict': 'PASS' if dmax < 1e-6 else 'FAIL',
                 'detail': 'entry = close[t], exit = close[t+1]; no T-day '
                           'intraday price is ever used as a fill (open[t], '
                           'low[t], high[t], vwap[t] are unused).  The stored '
                           'leg is float32, so the comparison is made on the '
                           'float32 grid and the tolerance is one float32 ulp',
                 'value': 'max |stored r - close[t+1]/close[t]-1| = %.2e' % dmax})
    # 5 overlapping_sample -------------------------------------------
    o5 = ov['+F+MA+V']
    bias.append({'check': '5_overlapping_sample',
                 'verdict': 'PASS' if (o5['rate_le20'] <= 0.5) else 'FLAG',
                 'detail': 'same-stock signal clustering inside 20 sessions; '
                           'N is NOT treated as the independent sample size',
                 'value': 'n=%d, adjacent_rate=%.4f, rate<=5d=%.4f, '
                          'rate<=20d=%.4f, n_eff=%.1f'
                          % (o5['n_events'], o5['adjacent_rate'],
                             o5['rate_le5'], o5['rate_le20'], o5['n_eff'])})
    # 6 future_information -------------------------------------------
    n_back = int((E0 & F2).sum())
    n_fwd = int((E0 & T.first_mask(E0, P['first_primary'], forward=True)).sum())
    bias.append({'check': '6_future_information',
                 'verdict': 'PASS' if n_back != n_fwd else 'FAIL',
                 'detail': 'the "first" window is BACKWARD-looking only '
                           '([t-10, t-1]); the deliberately forward-looking '
                           'variant produces a different event count, which '
                           'proves the backward rule is the one in use',
                 'value': 'backward N=%d vs forward N=%d' % (n_back, n_fwd)})
    # 7 parameter_leakage --------------------------------------------
    ok7 = ((T.sha256(T.SPEC) == T.SPEC_SHA256)
           and (os.path.getmtime(T.SPEC) <= os.path.getmtime(lg.path)))
    bias.append({'check': '7_parameter_leakage',
                 'verdict': 'PASS' if (ok7 and sb == 'PASS') else 'FLAG',
                 'detail': 'the SPEC was written and hashed before this run '
                           'started (its mtime precedes the run log), the run '
                           're-verifies the SPEC hash at start-up, and the '
                           'primary configuration is not the grid argmax',
                 'value': 'spec_sha256=%s, spec_mtime=%d, log_mtime=%d, '
                          'primary_percentile=%.3f'
                          % (T.SPEC_SHA256[:16], int(os.path.getmtime(T.SPEC)),
                             int(os.path.getmtime(lg.path)),
                             grid_sum['primary_percentile_in_grid'])})
    # 8 randomization_leakage ----------------------------------------
    outp = 1.0 - min(fam[k]['resolution'] for k in fam)
    stds = [fam[k]['null_std'] for k in fam]
    ok8 = (outp <= 1.0 - P['null_min_resolution']) and all(
        np.isfinite(s) and s > 0 for s in stds)
    bias.append({'check': '8_randomization_leakage',
                 'verdict': 'PASS' if ok8 else 'FLAG',
                 'detail': 'matched resampling: the only forbidden cell is the '
                           'event itself; each replicate draws once per event; '
                           'the null spread is finite and non-degenerate',
                 'value': 'pool-out rate=%.5f, null_std min=%.6f'
                          % (outp, float(np.nanmin(stds)))})
    res['bias'] = bias
    for b in bias:
        lg('  %-24s %s  %s' % (b['check'], b['verdict'], b['value']))
    del psi, psj, self_all, rflat

    # =================================================================
    # N. write tables
    # =================================================================
    lg.sep()
    lg('N. writing tables')
    T.save_csv(pd.DataFrame(rows), 'H_TAIL_MIDBAR_01_EVENTS.csv')
    T.save_csv(pd.DataFrame(ma_rows), 'H_TAIL_MIDBAR_01_MA.csv')
    T.save_csv(pd.DataFrame(vol_rows), 'H_TAIL_MIDBAR_01_VOLUME.csv')
    T.save_csv(pd.DataFrame(thr_rows), 'H_TAIL_MIDBAR_01_THRESHOLD.csv')
    T.save_csv(pd.DataFrame(null_rows), 'H_TAIL_MIDBAR_01_NULL.csv')
    T.save_csv(pd.DataFrame(boot_rows), 'H_TAIL_MIDBAR_01_BOOTSTRAP.csv')
    T.save_csv(pd.DataFrame(oos_rows), 'H_TAIL_MIDBAR_01_OOS.csv')
    T.save_csv(pd.DataFrame(wf_rows), 'H_TAIL_MIDBAR_01_WALK_FORWARD.csv')
    T.save_csv(pd.DataFrame(reg_rows), 'H_TAIL_MIDBAR_01_REGIME.csv')
    T.save_csv(pd.DataFrame(tail_rows), 'H_TAIL_MIDBAR_01_TAIL.csv')
    T.save_csv(pd.DataFrame(bias), 'H_TAIL_MIDBAR_01_BIAS_AUDIT.csv')
    T.save_csv(pd.DataFrame([dict(v, arm=k) for k, v in ov.items()]),
               'H_TAIL_MIDBAR_01_OVERLAP.csv')

    res['prereg'] = P
    res['meta'] = {
        'S': int(S), 'N': int(N), 'c0': int(c0), 's0': int(s0),
        'window': [str(st.dates_w[0]), str(st.dates_w[-1])],
        'panel_range': [str(dates[0]), str(dates[-1])],
        'minutes_data_available': False,
        'minute_scan': 'cache_daily/*min*, *5m*, *15m*, *60m*, *1min* -> 0 hits',
        'spec_sha256': T.SPEC_SHA256,
        'n_eligible_cells_window': int(st.el[:, s0:].sum()),
        'n_raw_events_e0': int(E0[:, s0:].sum()),
        'n_primary_events': int(esi.size),
        'universe': "hve_common.eligibility(g) & board != 'BSE'",
        'run_finished_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    T.save_json(res, 'H_TAIL_MIDBAR_01_RESULTS.json')
    lg('results json written')
    lg('=' * 72)
    lg('done')
    return 0


def index_daily_ret(g):
    """CSI300 daily close-to-close return, aligned to the panel calendar."""
    p = os.path.join(T.H.FS_DATA, 'index_panel.parquet')
    idx = pd.read_parquet(p)
    idx['trade_date'] = idx['trade_date'].astype(str)
    idx = idx[idx['ts_code'].astype(str) == P['regime_index']]
    idx = idx.sort_values('trade_date')
    kmap = {d: i for i, d in enumerate(g['dates'])}
    k = idx['trade_date'].map(kmap).to_numpy()
    v = pd.to_numeric(idx['close'], errors='coerce').to_numpy()
    m = np.isfinite(k) & np.isfinite(v)
    z = np.full(len(g['dates']), np.nan)
    z[k[m].astype(int)] = v[m]
    z = pd.Series(z).ffill().to_numpy()
    r = np.full(len(z), np.nan)
    r[1:] = z[1:] / z[:-1] - 1.0
    return r


if __name__ == '__main__':
    sys.exit(main())
