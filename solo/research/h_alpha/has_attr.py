# -*- coding: utf-8 -*-
"""H-ALPHA-SOURCE-01 -- steps 3-6.

Four matched counterfactuals plus the randomised null model.

  Level 1 Candidate : the stock the system bought  vs  a matched random stock
                      on the SAME session (coarsened exact matching on
                      trade_date / board / market-cap tercile / liquidity
                      tercile, same SW-L1 industry preferred).
  Level 2 Event     : event leg open[ev+1]         vs  a random eligible
                      session of the SAME stock in the SAME calendar month,
                      excluding +-5 sessions around the event and the fill.
  Level 3 Delay     : the frozen ladder D0/D1/D3/D5/D10  vs  the system's own
                      fill.  Fill of D_k = open[ev + k + 1]  (T+1 convention).
  Level 4 Entry     : the system's own fill        vs  E1 = open[ev+1],
                      E3 = open[ev+3], ER = mean over 200 uniform draws of a
                      fill inside the frozen event lifecycle.

Nothing here modifies HVT or W7.  Every leg is measured on the shared qfq
panel:  entry = open[t_decision + 1],  exit = close[entry + h].

Outputs
  data/H_ALPHA_SOURCE_01_MATCHED.csv    per-observation master table (all legs)
  data/H_ALPHA_SOURCE_01_CANDIDATE.csv  level 1 summary
  data/H_ALPHA_SOURCE_01_EVENT.csv      level 2 summary
  data/H_ALPHA_SOURCE_01_DELAY.csv      level 3 ladder summary
  data/H_ALPHA_SOURCE_01_ENTRY.csv      level 4 summary
  data/H_ALPHA_SOURCE_01_BOOTSTRAP.csv  month-cluster bootstrap for every leg
  data/H_ALPHA_SOURCE_01_NULL.csv       randomised null distribution
"""
import numpy as np
import pandas as pd

import has_common as C

P = C.PREREG_A
HZ = list(P['horizons'])
DEL = list(P['delays'])
EXCL = P['match_excl_win']
W_LIFE = P['overlap_guard_sessions']     # 20: minimum lifecycle length
B_BOOT = P['boot_B']
B_NULL = P['n_perm']
SEED = P['seed']
K_ER = 200                               # ER ensemble size (frozen here)
SYS = ('HVT', 'W7', 'ALL')


# =====================================================================
# statistics
# =====================================================================
def boot_diff(ra, ma, rb, mb, B=B_BOOT, seed=SEED):
    """Same estimator as hrbp_common.cluster_boot_diff, extended to expose the
    replicate distribution so a p-value can be reported alongside the CI."""
    ra, rb = np.asarray(ra, 'float64'), np.asarray(rb, 'float64')
    ma, mb = np.asarray(ma), np.asarray(mb)
    oa, ob = np.isfinite(ra), np.isfinite(rb)
    ra, ma = ra[oa], ma[oa]
    rb, mb = rb[ob], mb[ob]
    out = dict(obs=np.nan, lo=np.nan, hi=np.nan, p_neg=np.nan, p_pos=np.nan,
               p_two=np.nan, n_month=0, n_a=int(ra.size), n_b=int(rb.size))
    if ra.size < 2 or rb.size < 2:
        return out
    uq = np.union1d(np.unique(ma), np.unique(mb))
    pos = {v: i for i, v in enumerate(uq)}
    ka = np.array([pos[v] for v in ma])
    kb = np.array([pos[v] for v in mb])
    k = len(uq)
    ca = np.bincount(ka, minlength=k).astype('float64')
    cb = np.bincount(kb, minlength=k).astype('float64')
    sa = np.bincount(ka, weights=ra, minlength=k)
    sb = np.bincount(kb, weights=rb, minlength=k)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, k, size=(B, k))
    ac = ca[draws].sum(1)
    bc = cb[draws].sum(1)
    asum = sa[draws].sum(1)
    bsum = sb[draws].sum(1)
    ma_ = np.where(ac > 0, asum / np.maximum(ac, 1e-9), np.nan)
    mb_ = np.where(bc > 0, bsum / np.maximum(bc, 1e-9), np.nan)
    d = ma_ - mb_
    d = d[np.isfinite(d)]
    obs = float(ra.mean() - rb.mean())
    out.update(obs=obs, lo=float(np.percentile(d, 2.5)),
               hi=float(np.percentile(d, 97.5)),
               p_neg=float((d <= 0).mean()), p_pos=float((d >= 0).mean()),
               n_month=k, n_a=int(ra.size), n_b=int(rb.size))
    out['p_two'] = float(min(1.0, 2.0 * min(out['p_neg'], out['p_pos'])))
    return out


def desc(r):
    x = np.asarray(r, 'float64')
    x = x[np.isfinite(x)]
    if x.size == 0:
        return dict(n=0, mean=np.nan, median=np.nan, win=np.nan, pf=np.nan)
    gsum = float(x[x > 0].sum())
    lsum = float(-x[x < 0].sum())
    return dict(n=int(x.size), mean=float(x.mean()), median=float(np.median(x)),
                win=float((x > 0).mean()),
                pf=(gsum / lsum if lsum > 0 else np.nan))


# =====================================================================
# ragged-block samplers
# =====================================================================
def build_blocks(vals):
    """list of per-query arrays -> (flat, lo, hi) with hi exclusive."""
    lens = np.array([len(v) for v in vals], dtype='int64')
    lo = np.zeros(lens.size, dtype='int64')
    if len(lo) > 1:
        np.cumsum(lens[:-1], out=lo[1:])
    hi = lo + lens
    flat = (np.concatenate(vals) if lens.sum() else np.zeros(0, dtype='int64'))
    return flat.astype('int64'), lo, hi


def draw_once(lo, hi, rng, valid=None, rounds=40):
    """One independent randomisation: exactly one draw per query."""
    Q = lo.size
    out = np.full(Q, -1, dtype='int64')
    cnt = hi - lo
    todo = cnt > 0
    for _ in range(rounds):
        q = np.flatnonzero(todo)
        if q.size == 0:
            break
        c = cnt[q].astype('float64')
        p = lo[q] + np.minimum((rng.random(q.size) * c).astype('int64'),
                               (c - 1).astype('int64'))
        ok = np.ones(q.size, bool) if valid is None else valid(q, p)
        if ok.any():
            out[q[ok]] = p[ok]
            todo[q[ok]] = False
    return out


def draw_many(lo, hi, K, rng):
    """K draws per query -> (Q, K) block positions, -1 when the block is empty."""
    Q = lo.size
    cnt = (hi - lo)
    out = np.full((Q, K), -1, dtype='int64')
    live = cnt > 0
    if not live.any():
        return out
    q = np.flatnonzero(live)
    c = cnt[q].astype('float64')
    p = lo[q][:, None] + np.minimum(
        (rng.random((q.size, K)) * c[:, None]).astype('int64'),
        (c[:, None] - 1).astype('int64'))
    out[q] = p
    return out


# =====================================================================
# level summaries
# =====================================================================
def leg_rows(df, obs_fmt, ctrl_fmt, level, variant, systems=SYS):
    rows = []
    for sy in systems:
        sub = df if sy == 'ALL' else df[df['system'] == sy]
        if len(sub) == 0:
            continue
        for h in HZ:
            r = sub[obs_fmt % h].to_numpy('float64')
            c = sub[ctrl_fmt % h].to_numpy('float64')
            m = sub['month'].to_numpy()
            b = boot_diff(r, m, c, m)
            do, dc = desc(r), desc(c)
            row = dict(level=level, variant=variant, system=sy, horizon=h,
                       n=b['n_a'], n_month=b['n_month'],
                       mean_obs=do['mean'], mean_ctrl=dc['mean'],
                       delta_gross=b['obs'], ci_lo=b['lo'], ci_hi=b['hi'],
                       p_neg=b['p_neg'], p_two=b['p_two'],
                       n_obs=do['n'], win_obs=do['win'], pf_obs=do['pf'],
                       med_obs=do['median'], med_ctrl=dc['median'])
            for bp in P['cost_bp']:
                row['delta_net%d' % bp] = (b['obs'] - bp / 10000.0
                                           if np.isfinite(b['obs']) else np.nan)
            rows.append(row)
    return pd.DataFrame(rows)


# =====================================================================
# main
# =====================================================================
def main():
    lg = C.Log('has_attr')
    rng = np.random.default_rng(SEED)

    g = C.grid(lg=lg)
    S, N = g['close'].shape
    el = C.eligibility(lg=lg)
    mv = C.mv_panel(g, lg=lg)
    lq = C.liq_panel(g, lg=lg)
    ind, ind_ids = C.industry_panel(g, lg=lg)
    lg('panel S=%d N=%d  industry classes %d' % (S, N, len(ind_ids)))
    bid = np.array([C.BOARD_ID[b] for b in g['board']], dtype='int64')
    mv_b = C.tercile_ids(el, mv, P['mv_quantiles'], lg, 'mv')
    lq_b = C.tercile_ids(el, lq, P['liq_quantiles'], lg, 'liq')
    codes = np.array(g['codes'])
    dates = g['dates']

    obs = C.load_obs(lg=lg)
    Q = len(obs)
    si = obs['s_i'].to_numpy('int64')
    evi = obs['ev_idx'].to_numpy('int64')
    dci = obs['dec_idx'].to_numpy('int64')
    eii = obs['entry_idx'].to_numpy('int64')
    month = obs['month'].to_numpy('int64')
    ev_month = np.array([int(dates[i][:6]) for i in evi], dtype='int64')
    sys = obs['system'].to_numpy()

    lg.sep()
    lg('observations Q = %d  (HVT %d / W7 %d)'
       % (Q, int((sys == 'HVT').sum()), int((sys == 'W7').sum())))
    for sy in ('HVT', 'W7'):
        m = sys == sy
        lag = (dci - evi)[m]
        lg('  %s decision-lag (dec - event, sessions): min %d  p50 %.0f  '
           'p75 %.0f  max %d' % (sy, lag.min(), np.median(lag),
                                 np.percentile(lag, 75), lag.max()))
        lg('  %s eligible at its own fill: %.4f'
           % (sy, float(el[si[m], eii[m]].mean())))
    lg.sep()

    # -----------------------------------------------------------------
    # Level 1 -- candidate vs matched random stock, same session
    # -----------------------------------------------------------------
    amap = C.strata_index(el, mv_b, lq_b, ind, bid, lg=lg)
    okq = el[si, eii] & (mv_b[si, eii] >= 0) & (lq_b[si, eii] >= 0)
    lg('Level1 usable queries %d / %d' % (int(okq.sum()), Q))
    keys1 = C.cell_key(eii, bid[si], np.where(okq, mv_b[si, eii], 0),
                       np.where(okq, lq_b[si, eii], 0))
    ban_tab = np.zeros(N * S, dtype=bool)
    ban_tab[np.unique(eii * S + si)] = True
    ctrl, cmode = C.pick_stratum(amap, keys1, ind[si, eii], si, eii,
                                 eii * S + si, rng, S)
    ctrl = np.where(okq, ctrl, -1)
    lg('Level1 matched control %d / %d = %.4f   (exact industry %.4f)'
       % (int((ctrl >= 0).sum()), int(okq.sum()),
          float((ctrl >= 0).mean()),
          float((cmode[ctrl >= 0] == 0).mean()) if (ctrl >= 0).any() else np.nan))

    # -----------------------------------------------------------------
    # Level 2 -- event vs random date, same stock, same calendar month
    # -----------------------------------------------------------------
    mo = C.month_index(g, el, lg=lg)
    mkey = si * 10000 + ev_month
    rdate = _pick_month2(mo, mkey, evi - EXCL, evi + EXCL,
                         eii - EXCL, eii + EXCL, rng)
    lg('Level2 random date drawn for %d / %d = %.4f'
       % (int((rdate >= 0).sum()), Q, float((rdate >= 0).mean())))

    # -----------------------------------------------------------------
    # frozen event lifecycle: [ev+1, max(ev+WLIFE, dec)]   (structure only)
    # -----------------------------------------------------------------
    life_hi_v = np.minimum(np.maximum(evi + W_LIFE, dci), N - 2)
    life_lo_v = evi + 1
    blocks = []
    for q in range(Q):
        a, b = int(life_lo_v[q]), int(life_hi_v[q])
        if b < a:
            blocks.append(np.zeros(0, dtype='int64'))
            continue
        c = np.flatnonzero(el[si[q], a:b + 1])
        blocks.append((c + a).astype('int64'))
    life_flat, life_lo, life_hi = build_blocks(blocks)
    lg('event lifecycle: mean length %.1f sessions, empty %d'
       % (float((life_hi - life_lo).mean()), int((life_hi == life_lo).sum())))

    # -----------------------------------------------------------------
    # returns for every leg
    # -----------------------------------------------------------------
    cols = {
        'system': sys, 'ts_code': obs['ts_code'].to_numpy(),
        'year': obs['year'].to_numpy(), 'month': month,
        'phase': obs['phase'].to_numpy(), 'top3': obs['top3'].to_numpy(),
        'lag': obs['lag'].to_numpy(),
        's_i': si, 'ev_idx': evi, 'dec_idx': dci, 'entry_idx': eii,
        'life_lo': life_lo_v, 'life_hi': life_hi_v,
        'ctrl_s_i': ctrl, 'ctrl_mode': cmode,
        'ctrl_code': np.where(ctrl >= 0, codes[np.maximum(ctrl, 0)], ''),
        'rdate_idx': rdate,
        'rdate_date': np.where(rdate >= 0, dates[np.maximum(rdate, 0)], ''),
        'cand_ok': okq,
    }
    sel_er = draw_many(life_lo, life_hi, K_ER, rng)
    ss_er = life_flat[np.maximum(sel_er, 0)]
    qq_er = np.repeat(np.arange(Q), K_ER)
    for h in HZ:
        cols['r_obs_%d' % h] = C.ret_open(g, si, eii, h)
        cols['r_cand_%d' % h] = np.where(
            ctrl >= 0, C.ret_open(g, np.maximum(ctrl, 0), eii, h), np.nan)
        cols['r_ev_%d' % h] = C.ret_open(g, si, evi + 1, h)
        cols['r_rdate_%d' % h] = np.where(
            rdate >= 0, C.ret_open(g, si, np.maximum(rdate + 1, 0), h), np.nan)
        for k in DEL:
            cols['r_d%d_%d' % (k, h)] = C.ret_open(g, si, evi + k + 1, h)
        cols['r_e1_%d' % h] = cols['r_ev_%d' % h]
        cols['r_e3_%d' % h] = C.ret_open(g, si, evi + 3, h)
        rr = C.ret_open(g, si[qq_er], ss_er.reshape(-1), h).reshape(Q, K_ER)
        rr = np.where(sel_er >= 0, rr, np.nan)
        cols['r_er_%d' % h] = np.nanmean(rr, axis=1)
    df = pd.DataFrame(cols)
    lg('master table %s' % str(df.shape))

    for h in HZ:
        d0 = df['r_d0_%d' % h].to_numpy('float64')
        e1 = df['r_e1_%d' % h].to_numpy('float64')
        ev = df['r_ev_%d' % h].to_numpy('float64')
        m = np.isfinite(d0) & np.isfinite(e1) & np.isfinite(ev)
        lg('  identity check D0 == E1 == event leg at T+%d: max|diff| %.2e'
           % (h, float(np.max(np.abs(d0[m] - e1[m]))) if m.any() else 0.0))

    C.save_csv(df, 'H_ALPHA_SOURCE_01_MATCHED.csv')

    # -----------------------------------------------------------------
    # level summaries
    # -----------------------------------------------------------------
    cand = leg_rows(df, 'r_obs_%d', 'r_cand_%d', 'Candidate', 'vs matched stock')
    evt = leg_rows(df, 'r_ev_%d', 'r_rdate_%d', 'Event', 'vs random date')
    delay = pd.concat([leg_rows(df, 'r_obs_%d', 'r_d%d_%%d' % k, 'Delay',
                                'Original - D%d' % k) for k in DEL],
                      ignore_index=True)
    entry = pd.concat([
        leg_rows(df, 'r_obs_%d', 'r_e1_%d', 'Entry', 'Original - E1'),
        leg_rows(df, 'r_obs_%d', 'r_e3_%d', 'Entry', 'Original - E3'),
        leg_rows(df, 'r_obs_%d', 'r_er_%d', 'Entry', 'Original - ER')],
        ignore_index=True)
    C.save_csv(cand, 'H_ALPHA_SOURCE_01_CANDIDATE.csv')
    C.save_csv(evt, 'H_ALPHA_SOURCE_01_EVENT.csv')
    C.save_csv(delay, 'H_ALPHA_SOURCE_01_DELAY.csv')
    C.save_csv(entry, 'H_ALPHA_SOURCE_01_ENTRY.csv')

    keepc = ['level', 'variant', 'system', 'horizon', 'n', 'n_month',
             'delta_gross', 'ci_lo', 'ci_hi', 'p_neg', 'p_two', 'delta_net30']
    boot = pd.concat([t[keepc] for t in (cand, evt, delay, entry)],
                     ignore_index=True)
    C.save_csv(boot, 'H_ALPHA_SOURCE_01_BOOTSTRAP.csv')

    sub = df[df['system'] == 'HVT']
    ob, lo, hi = C.cluster_boot_diff(sub['r_obs_10'].to_numpy('float64'),
                                     sub['month'].to_numpy(),
                                     sub['r_cand_10'].to_numpy('float64'),
                                     sub['month'].to_numpy(), B=B_BOOT,
                                     seed=SEED)
    r0 = boot[(boot['level'] == 'Candidate') & (boot['system'] == 'HVT') &
              (boot['horizon'] == 10)].iloc[0]
    lg.sep()
    lg('hrbp_common  cluster_boot_diff (Candidate, HVT, T+10): %.6f [%.6f, %.6f]'
       % (ob, lo, hi))
    lg('has_attr local estimator                 (same leg): %.6f [%.6f, %.6f]'
       % (r0['delta_gross'], r0['ci_lo'], r0['ci_hi']))

    # -----------------------------------------------------------------
    # randomised null model
    # -----------------------------------------------------------------
    lg.sep()
    nulls = _nulls(g, S, N, el, ind, df, si, eii, evi, sys, okq, amap, keys1,
                   mkey, mo, life_flat, life_lo, life_hi, rng, lg)
    C.save_csv(nulls, 'H_ALPHA_SOURCE_01_NULL.csv')

    _echo(lg, cand, 'CANDIDATE')
    _echo(lg, evt, 'EVENT')
    _echo(lg, entry, 'ENTRY')
    lg.sep()
    lg('DELAY ladder (ALL systems): delta = Original - Dk, gross')
    pv = delay[delay['system'] == 'ALL'].pivot_table(
        index='variant', columns='horizon', values='delta_gross')
    for k in pv.index:
        lg('  %-14s %s' % (k, '  '.join('T+%-2d %+.4f' % (h, pv.loc[k, h])
                                        for h in HZ)))
    lg('done')


# =====================================================================
# helpers
# =====================================================================
def _pick_month2(amap, key, a_lo, a_hi, b_lo, b_hi, rng, tries=30):
    """random eligible session of the same stock in the same month, excluding
    [a_lo, a_hi] around the event and [b_lo, b_hi] around the fill."""
    lo = np.searchsorted(amap['key'], key, 'left')
    hi = np.searchsorted(amap['key'], key, 'right')
    n = (hi - lo).astype('int64')
    out = np.full(len(key), -1, dtype='int64')
    pend = np.flatnonzero(n > 0)
    for _ in range(tries):
        if pend.size == 0:
            break
        k = n[pend]
        d = amap['day'][lo[pend] + np.minimum(
            (rng.random(pend.size) * k).astype('int64'), k - 1)]
        bad = ((d >= a_lo[pend]) & (d <= a_hi[pend])) | \
              ((d >= b_lo[pend]) & (d <= b_hi[pend]))
        out[pend[~bad]] = d[~bad]
        pend = pend[bad]
    return out


def _nulls(g, S, N, el, ind, df, si, eii, evi, sys, okq, amap, keys1, mkey,
           mo, life_flat, life_lo, life_hi, rng, lg):
    """Three randomisation families, B_NULL independent replicates each.

    NULL_CANDIDATE  the stock is replaced by a matched random stock on the
                    same session -- supplementary, because it does change the
                    stock and therefore sits outside the literal "keep the
                    stock" null; reported separately.
    NULL_EVENT      the event date is replaced by a random eligible session of
                    the same stock in the same calendar month.
    NULL_ENTRY      the fill is replaced by a random eligible session inside
                    the frozen event lifecycle.

    The observed leg is always the system's own fill, so every replicate is
    compared against exactly the same observations (the base set keeps only
    queries the randomiser resolved in every replicate).
    """
    Q = len(df)
    obs_r = {h: df['r_obs_%d' % h].to_numpy('float64') for h in HZ}
    sysmask = {'ALL': np.ones(Q, bool),
               'HVT': (sys == 'HVT'), 'W7': (sys == 'W7')}
    cost = P['primary_cost_bp'] / 10000.0
    ban_tab = np.zeros(N * S, dtype=bool)
    ban_tab[np.unique(eii * S + si)] = True
    want_ind = ind[si, eii]

    lo1 = np.searchsorted(amap['key'], keys1, 'left')
    hi1 = np.searchsorted(amap['key'], keys1, 'right')
    cnt1 = hi1 - lo1
    tot1 = int(cnt1.sum())
    has_ind = np.zeros(Q, bool)
    if tot1:
        qid = np.repeat(np.arange(Q), cnt1)
        within = np.arange(tot1) - np.repeat(np.cumsum(cnt1) - cnt1, cnt1)
        pos = np.repeat(lo1, cnt1) + within
        m_s = amap['s'][pos]
        m_i = amap['ind'][pos]
        adm = ((m_s != si[qid]) & (~ban_tab[eii[qid] * S + m_s]) &
               (m_i == want_ind[qid]))
        np.logical_or.at(has_ind, qid[adm], True)

    def valid_cand(q, p):
        sv = amap['s'][p]
        ok = okq[q] & (sv != si[q]) & (~ban_tab[eii[q] * S + sv])
        hq = has_ind[q]
        return ok & ((~hq) | (amap['ind'][p] == want_ind[q]))

    lo2 = np.searchsorted(mo['key'], mkey, 'left')
    hi2 = np.searchsorted(mo['key'], mkey, 'right')

    def valid_rdate(q, p):
        d = mo['day'][p]
        return ~(((d >= evi[q] - EXCL) & (d <= evi[q] + EXCL)) |
                 ((d >= eii[q] - EXCL) & (d <= eii[q] + EXCL)))

    rows = []
    for fam in ('NULL_CANDIDATE', 'NULL_EVENT', 'NULL_ENTRY'):
        D = np.empty((B_NULL, Q), dtype='int64')
        for b in range(B_NULL):
            if fam == 'NULL_CANDIDATE':
                D[b] = draw_once(lo1, hi1, rng, valid_cand)
            elif fam == 'NULL_EVENT':
                D[b] = draw_once(lo2, hi2, rng, valid_rdate)
            else:
                D[b] = draw_once(life_lo, life_hi, rng)
        idx0 = np.maximum(D, 0)
        if fam == 'NULL_CANDIDATE':
            st_all = np.where(D >= 0, amap['s'][idx0], -1)
            ss_all = np.broadcast_to(eii, D.shape)
        elif fam == 'NULL_EVENT':
            st_all = np.broadcast_to(si, D.shape)
            ss_all = np.where(D >= 0, mo['day'][idx0] + 1, -1)
        else:
            st_all = np.broadcast_to(si, D.shape)
            ss_all = np.where(D >= 0, life_flat[idx0], -1)
        drawok = (D >= 0).all(axis=0)
        lg('  %s resolution %.4f' % (fam, float(drawok.mean())))
        for sy in ('ALL', 'HVT', 'W7'):
            base = sysmask[sy] & drawok
            if fam == 'NULL_CANDIDATE':
                base = base & okq
            ii = np.flatnonzero(base)
            if ii.size < P['min_n']:
                continue
            st = np.asarray(st_all[:, ii])
            ss = np.asarray(ss_all[:, ii])
            okm = st >= 0
            stf = np.where(okm, st, 0).reshape(-1)
            ssf = np.where(okm, ss, 0).reshape(-1)
            nb_at = {}
            for h in HZ:
                rr = C.ret_open(g, stf, ssf, h).reshape(st.shape)
                rr = np.where(okm, rr, np.nan)
                nb = np.nanmean(rr, axis=1)
                nb = nb[np.isfinite(nb)]
                nb_at[h] = float(nb.mean()) if nb.size else np.nan
                om = float(np.nanmean(obs_r[h][ii]))
                if nb.size == 0:
                    continue
                rows.append(dict(
                    family=fam, system=sy, horizon=h, n=int(ii.size),
                    n_rep=int(nb.size), obs_gross=om, obs_net30=om - cost,
                    null_mean=float(nb.mean()),
                    null_median=float(np.median(nb)),
                    null_lo=float(np.percentile(nb, 2.5)),
                    null_hi=float(np.percentile(nb, 97.5)),
                    excess_gross=om - float(nb.mean()),
                    excess_net30=om - cost - float(nb.mean()),
                    p_gross_right=float((nb >= om).mean()),
                    p_net30_right=float((nb >= om - cost).mean()),
                    pctile_of_obs=float((nb < om).mean())))
            lg('    %-5s n=%d   obs T+10 %+.4f  null T+10 %+.4f'
               % (sy, ii.size, float(np.nanmean(obs_r[10][ii])), nb_at[10]))
        del D, st_all, ss_all
    return pd.DataFrame(rows)


def _echo(lg, t, tag):
    lg.sep()
    lg('%s -- ALL systems: delta = observed - counterfactual (gross)' % tag)
    for h in HZ:
        r = t[(t['system'] == 'ALL') & (t['horizon'] == h)]
        if len(r) == 0:
            continue
        r = r.iloc[0]
        lg('  T+%-3d n=%-5d delta %+.4f [%+.4f, %+.4f]  net30 %+.4f  '
           'p_two %.3f  win %.3f' % (h, r['n'], r['delta_gross'], r['ci_lo'],
                                     r['ci_hi'], r['delta_net30'], r['p_two'],
                                     r['win_obs']))


if __name__ == '__main__':
    main()
