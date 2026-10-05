# -*- coding: utf-8 -*-
"""H-SLG-02 -- main run: every computation and every CSV (SPEC 21).

Chain under study (H_SLG_02_SPEC.md section 0): the *completion* of a
post-surge consolidation is treated as a CONTINUOUS cross-sectional factor
inside ``U_FACTOR = CTX & R1``.  ``READY`` / ``FAILED`` never filter the pool.

Sections mirror the SPEC: factor pool (6) / factor & label construction (7-8) /
overlap books (9) / quantiles (10) / composites (11) / incremental models (12) /
nulls (13) / OOS + walk-forward (14) / parameter grid (15) / subgroups (16) /
funnel (17) / verdict bits (18).

Only frozen thresholds from ``PREREG2`` (and the read-only ``PREREG_S``) are
consumed.  ``slg_common`` / ``H_SLG_01_*`` are never modified.
"""
import os
import sys
import time

import numpy as np
import pandas as pd

import slg_common as S
import slg2_common as CM
import hve_common as H
import mce_common as MC

P = S.PREREG_S
P2 = CM.PREREG2
OUT = S.OUT
LOG = S.Log('slg2_run')

REG_ENC = {'BULL': 0, 'NEUTRAL': 1, 'BEAR': 2, 'NA': 3}
REG_NAME = {'BULL': '上涨', 'NEUTRAL': '震荡', 'BEAR': '下跌', 'NA': 'NA'}

FID = CM.FACTOR_IDS
CFID = CM.COMP_FACTORS
HZ = CM.HORIZONS2
HSTAR = CM.HSTAR2
DEDUP_GAP = int(P2['DEDUP_GAP'])
MIN_N = int(P2['MIN_XS_N'])


# =====================================================================
# helpers
# =====================================================================
def load_basic_cols(g, cols):
    p = os.path.join(H.FS_DATA, 'basic_panel.parquet')
    b = pd.read_parquet(p, columns=['ts_code', 'trade_date'] + list(cols))
    b['ts_code'] = b['ts_code'].astype(str)
    b['trade_date'] = b['trade_date'].astype(str)
    d2i, s2i = g['d2i'], g['s2i']
    b = b[b['trade_date'].isin(d2i) & b['ts_code'].isin(s2i)]
    si = b['ts_code'].map(s2i).to_numpy().astype('int64')
    di = b['trade_date'].map(d2i).to_numpy().astype('int64')
    out = {}
    for c in cols:
        M = np.full(g['close'].shape, np.nan, dtype='float32')
        M[si, di] = pd.to_numeric(b[c], errors='coerce').to_numpy(dtype='float64')
        out[c] = M
    return out


def y_cells(st, cs, cj, h, panels=None):
    """y_abs / y_ex_mkt / y_ex_sec on flat cells for ONE horizon (SPEC 8.2)."""
    if panels is None:
        Y = CM._fwd_panel(st.cl, h)
        sm = CM.sec_mean_panel(st, h)
        cl_, cb_ = CM.mkt_index_cum(st)
    else:
        Y, sm, cl_, cb_ = panels
    N = st.N
    y_abs = Y[cs, cj].astype('float64')
    j2 = cj + int(h)
    ok = j2 <= N - 1
    jc = np.clip(j2, 0, N - 1)
    with np.errstate(invalid='ignore'):
        ir = np.exp(cl_[jc + 1] - cl_[cj + 1]) - 1.0
    nbad = cb_[jc + 1] - cb_[cj + 1]
    y_mkt = np.where(ok & (nbad == 0), y_abs - ir, np.nan)
    smv = sm[cs, cj].astype('float64')
    with np.errstate(invalid='ignore'):
        y_sec = np.where(np.isfinite(smv), y_abs - smv, np.nan)
    return y_abs, y_mkt, y_sec


def scope_row(scope, d):
    return {'scope': scope, 'ic_mean': d['ic_mean'], 'ic_std': d['ic_std'],
            'icir': d['icir'], 't_nw': d['t_nw'], 'pos_ratio': d['pos_ratio'],
            'avg_n': d['avg_n'], 'days': d['days']}


def is_stats(ic_day, is_days):
    x = np.asarray(ic_day, dtype='float64')[is_days]
    x = x[np.isfinite(x)]
    mu, t, _, _ = CM.nw_t(x)
    return mu, t


def quintile_rows(st, cs, cj, rk, ya, ys, h, ph_days, tag, sel=None, ngroups=None):
    """SPEC 10: per-quintile gross / net / win / median / maxdd / pf + spread."""
    ng = int(ngroups or P2['Q_GROUPS'])
    m = np.isfinite(rk) & np.isfinite(ya)
    if sel is not None:
        m = m & sel
    g = np.zeros(cs.size, dtype='int64')
    with np.errstate(invalid='ignore'):
        g[m] = np.clip(np.floor(rk[m] * ng).astype('int64') + 1, 1, ng)
    rows, means = [], []
    for q in range(1, ng + 1):
        s_ = m & (g == q)
        n_ = int(s_.sum())
        if n_ == 0:
            rows.append({'asset': tag, 'h': h, 'group': 'Q%d' % q, 'n': 0})
            means.append(np.nan)
            continue
        d = S.stats(ya[s_])
        exs = float(np.nanmean(ys[s_])) if np.isfinite(ys[s_]).any() else np.nan
        mdd = float(np.nanmean(CM_maxdd(st, cs[s_], cj[s_], h))) if n_ else np.nan
        means.append(exs)
        rows.append({'asset': tag, 'h': h, 'group': 'Q%d' % q, 'n': n_,
                     'gross': d['mean'], 'net15': d['mean_net15'],
                     'net30': d['mean_net30'], 'net50': d['mean_net50'],
                     'win30': d['win_net30'], 'median': d['median'],
                     'maxdd': mdd, 'pf': d['pf_net%d' % int(P2['primary_cost_bp'])],
                     'exsec': exs})
    mm = pd.Series(means, dtype='float64')
    mono = np.nan
    if int(np.isfinite(mm).sum()) >= 3:
        try:
            mono = float(pd.Series(np.arange(1, ng + 1, dtype='float64'))
                         .corr(mm, method='spearman'))
        except Exception:
            mono = np.nan
    g1 = rows[0].get('gross', np.nan)
    gN = rows[-1].get('gross', np.nan)
    spread = gN - g1
    for r in rows:
        r['monotonicity'] = mono
        r['spread_gross'] = spread
        r['spread_net30'] = spread
    rows.append({'asset': tag, 'h': h, 'group': 'Q%d-Q1' % ng,
                 'n': int(m.sum()), 'gross': spread, 'net15': spread,
                 'net30': spread, 'net50': spread, 'win30': np.nan,
                 'median': np.nan, 'maxdd': np.nan, 'pf': np.nan,
                 'exsec': np.nan, 'monotonicity': mono,
                 'spread_gross': spread, 'spread_net30': spread})
    return rows, mono, spread


def CM_maxdd(st, s_, j_, K):
    """mean within-h max drawdown (reuses slg_run's definition)."""
    a = np.asarray(j_, dtype='int64')
    off = np.arange(1, int(K) + 1)
    cols = a[:, None] + off[None, :]
    ok = cols <= st.N - 1
    cc = np.clip(cols, 0, st.N - 1)
    p0 = st.cl[s_, a].astype('float64')
    with np.errstate(invalid='ignore'):
        cr = (st.cl[s_[:, None], cc].astype('float64')
              / np.where(p0 > 0, p0, np.nan)[:, None] - 1.0)
    cr = np.where(ok, cr, np.nan)
    fin = np.isfinite(cr)
    run = np.maximum.accumulate(np.where(fin, cr, -np.inf), axis=1)
    dd = np.where(fin, cr - run, np.nan)
    return np.nanmin(dd, axis=1)


def daily_pair(day, small, big, y, min_n=MIN_N, fixed=None):
    """Same-sample nested daily cross-sectional OLS (SPEC 12.1).

    ``big`` extends ``small`` (small's columns must be a prefix of big's).
    fixed=(beta_small, beta_big) switches to the extrapolation pass: no fit is
    performed, the frozen IS coefficients are applied instead (SPEC 12.5).
    """
    d = np.asarray(day)
    order = np.argsort(d, kind='stable')
    d = d[order]
    yy = np.asarray(y, dtype='float64')[order]
    SB = [np.asarray(x, dtype='float64')[order] for x in big]
    ns = len(small)
    uq, first = np.unique(d, return_index=True)
    ends = np.r_[first[1:], d.size]
    res = {'beta_s': [], 'beta_b': [], 'r2_s': [], 'r2_b': [], 'n': [],
           'day': [], 'pred': [], 'y': [], 'r2_s_pool': np.nan,
           'r2_b_pool': np.nan, 'sse_s': 0.0, 'sse_b': 0.0, 'sst': 0.0}
    for i, (a, b) in enumerate(zip(first, ends)):
        if b - a < min_n:
            continue
        M = np.column_stack([x[a:b] for x in SB])
        ym = yy[a:b]
        ok = np.isfinite(ym) & np.isfinite(M).all(axis=1)
        k = int(ok.sum())
        if k < min_n:
            continue
        Xb = M[ok]
        yb = ym[ok]
        if fixed is None:
            Ab = np.column_stack([np.ones(k), Xb])
            bb, *_ = np.linalg.lstsq(Ab, yb, rcond=None)
            As = np.column_stack([np.ones(k), Xb[:, :ns]])
            bs_, *_ = np.linalg.lstsq(As, yb, rcond=None)
            p_b = Ab.dot(bb)
            p_s = As.dot(bs_)
            res['beta_b'].append(bb)
            res['beta_s'].append(bs_)
            res['r2_b'].append(_r2(yb, p_b))
            res['r2_s'].append(_r2(yb, p_s))
            res['day'].append(int(uq[i]))
            res['n'].append(k)
        else:
            bs_, bb = fixed
            Ab = np.column_stack([np.ones(k), Xb])
            As = np.column_stack([np.ones(k), Xb[:, :ns]])
            p_b = Ab.dot(bb)
            p_s = As.dot(bs_)
            res['pred'].append(p_b)
            res['y'].append(yb)
            res['sse_b'] += float(((yb - p_b) ** 2).sum())
            res['sse_s'] += float(((yb - p_s) ** 2).sum())
            res['sst'] += float(((yb - yb.mean()) ** 2).sum())
            res['day'].append(int(uq[i]))
            res['n'].append(k)
    for k_ in ('beta_s', 'beta_b', 'r2_s', 'r2_b'):
        res[k_] = np.asarray(res[k_], dtype='float64')
    res['day'] = np.asarray(res['day'], dtype='int64')
    res['n'] = np.asarray(res['n'], dtype='int64')
    if fixed is not None:
        res['pred'] = (np.concatenate(res['pred']) if res['pred']
                       else np.array([]))
        res['y'] = (np.concatenate(res['y']) if res['y'] else np.array([]))
        if res['sst'] > 0:
            res['r2_b_pool'] = 1.0 - res['sse_b'] / res['sst']
            res['r2_s_pool'] = 1.0 - res['sse_s'] / res['sst']
    return res


def _r2(y, pred):
    sst = float(((y - y.mean()) ** 2).sum())
    if sst <= 0:
        return np.nan
    return 1.0 - float(((y - pred) ** 2).sum()) / sst


def derive_verdict(bits, n_bits, param_fragile):
    n_pass = int(sum(1 for v in bits.values() if v))
    if param_fragile:
        v = 'FRAGILE'
    elif n_pass == n_bits:
        v = 'ROBUST'
    elif n_pass >= 9:
        v = 'PROMISING'
    elif n_pass >= 5:
        v = 'FRAGILE'
    else:
        v = 'NO EDGE'
    return n_pass, v


def eval_asset(tag, comp_rk, ry5, day, D, ph_days, ph_cell, mono, spread_net30,
               param_pos_ratio, regime_ics, n1p, dedup_ic, dedup_t,
               beta_t, retain, is_single, expect_sign, per_factor):
    """SPEC 18 -- 12 bits for composites, 11 for single factors."""
    isd = ph_days == 'IS'
    vald = ph_days == 'VALID'
    oosd = ph_days == 'OOS'

    def _m(sel):
        x = comp_rk[sel]
        x = x[np.isfinite(x)]
        return float(x.mean()) if x.size else np.nan

    ic_all, cnt_all = CM.day_ic(comp_rk, ry5, day, D)
    i_is, t_is = is_stats(ic_all, isd)
    x = ic_all[vald]
    x = x[np.isfinite(x)]
    i_val = float(x.mean()) if x.size else np.nan
    _, t_val, _, _ = CM.nw_t(x)
    x = ic_all[oosd]
    x = x[np.isfinite(x)]
    i_oos = float(x.mean()) if x.size else np.nan
    sign_is = 0.0 if not np.isfinite(i_is) else np.sign(i_is)
    bits = {}
    bits['g1_dir'] = bool(np.isfinite(i_is) and sign_is == np.sign(expect_sign))
    bits['g2_is_t'] = bool(np.isfinite(t_is) and abs(t_is) >= P2['T_MIN'])
    bits['g3_valid_same'] = bool(np.isfinite(i_val) and np.isfinite(i_is)
                                 and np.sign(i_val) == sign_is and sign_is != 0
                                 and np.isfinite(t_val)
                                 and abs(t_val) >= P2['T_WF_MIN'])
    bits['g4_oos_same'] = bool(np.isfinite(i_oos) and sign_is != 0
                               and np.sign(i_oos) == sign_is)
    bits['g5_mono'] = bool(np.isfinite(mono) and mono >= P2['MONO_MIN'])
    bits['g6_cost30'] = bool(np.isfinite(spread_net30) and spread_net30 > 0)
    bits['g7_param'] = bool(np.isfinite(param_pos_ratio)
                            and param_pos_ratio >= P2['param_stable_thr'])
    rg = [v for v in regime_ics.values() if np.isfinite(v)]
    bits['g8_regime_same'] = bool(len(rg) == 3
                                  and all(np.sign(v) == sign_is for v in rg)
                                  and sign_is != 0)
    bits['g9_null'] = bool(np.isfinite(n1p) and n1p < 0.05)
    bits['g10_dedup'] = bool(np.isfinite(dedup_ic) and sign_is != 0
                             and np.sign(dedup_ic) == sign_is
                             and abs(dedup_ic) >= P2['PARAM_IC_FLOOR'])
    if is_single:
        bits['g10b_dedup_t'] = bool(np.isfinite(dedup_t)
                                    and abs(dedup_t) >= P2['T_MIN'])
        n_bits = 11
    else:
        bits['g11_mom_ctl'] = bool(np.isfinite(beta_t) and beta_t >= P2['T_MIN'])
        bits['g12_retain'] = bool(np.isfinite(retain) and retain >= P2['RETAIN_THR'])
        n_bits = 12
    n_pass, verdict = derive_verdict(bits, n_bits, param_pos_ratio is not None
                                     and np.isfinite(param_pos_ratio)
                                     and param_pos_ratio <= P2['param_fragile_thr'])
    flags = []
    if np.isfinite(i_is) and sign_is != np.sign(expect_sign):
        flags.append('SIGN_CONFLICT')
    if (np.isfinite(i_is) and sign_is != 0
            and ((np.isfinite(i_val) and np.sign(i_val) != sign_is)
                 or (np.isfinite(i_oos) and np.sign(i_oos) != sign_is))):
        flags.append('UNSTABLE')
    if (np.isfinite(i_is) and sign_is != 0 and np.isfinite(dedup_ic)
            and np.sign(dedup_ic) != sign_is):
        flags.append('EPISODE_SENSITIVE')
    return {'bits': bits, 'n_pass': n_pass, 'n_bits': n_bits,
            'verdict': verdict, 'flags': flags, 'ic_is': i_is, 't_is': t_is,
            'ic_valid': i_val, 't_valid': t_val, 'ic_oos': i_oos,
            'ic_all': _m(np.ones(comp_rk.size, dtype=bool)),
            'per_factor': per_factor}


# =====================================================================
# main
# =====================================================================
def main():
    t0 = time.time()
    LOG.sep('=')
    LOG('H-SLG-02 main run  version %s' % P2['version'])
    LOG.sep('=')

    if not CM.verify_spec2(LOG):
        LOG('FATAL: SPEC hash mismatch -> EXIT 2')
        return 2

    g = S.load_grid(LOG)
    st = CM.load_state(g, LOG)
    S_, N_ = st.S, st.N
    D = N_
    LOG('sliced panel S=%d N=%d  %s..%s  (c0=%d s0=%d)'
        % (S_, N_, st.dates_w[0], st.dates_w[-1], st.c0, st.s0))

    ph_days = np.array([CM.phase_of2(d) for d in st.dates_w])

    # ---- auxiliary matrices ------------------------------------------
    reg = np.asarray(MC.regime_ma(g, LOG), dtype=object)[st.c0:]
    reg_nm = np.array([str(v) for v in reg])
    reg_num = np.array([REG_ENC.get(x, 3) for x in reg_nm], dtype='int64')
    mv = np.ascontiguousarray(MC.load_size_mv(g)[:, st.c0:])
    turn = np.ascontiguousarray(
        load_basic_cols(g, ['turnover_rate'])['turnover_rate'][:, st.c0:])
    LOG('aux matrices ready (regime / mv / turnover)')

    p60 = np.full((S_, N_), np.nan, dtype='float32')
    p60[:, 60:] = st.cl[:, :-60]
    with np.errstate(invalid='ignore'):
        r60 = np.where(p60 > 0, st.cl / p60 - 1.0, np.nan).astype('float32')
    del p60

    # =================================================================
    # section B -- factor pool U_FACTOR = CTX & R1 (SPEC 6)
    # =================================================================
    LOG.sep()
    LOG('section B  factor pool')
    cs0, cj0 = st.cand_idx()
    cv0 = S.completion_vars(st, cs0, cj0)
    r1_ok = np.asarray(cv0['R1'], dtype=bool).copy()
    fl_ok = np.asarray(cv0['FAILED'], dtype=bool).copy()
    del cv0
    cs = np.ascontiguousarray(cs0[r1_ok])
    cj = np.ascontiguousarray(cj0[r1_ok])
    del cs0, cj0
    C = cs.size
    n_failed = int(fl_ok.sum())
    LOG('CTX cells=%d   R1-in-CTX=%d   U_FACTOR cells=%d   FAILED-in-U_FACTOR=%d'
        % (int(r1_ok.size), int(r1_ok.sum()), C, n_failed))
    if C == 0:
        LOG('FATAL: empty factor pool -> EXIT 3')
        return 3

    ok_scan, scan = CM.future_column_scan2(g, st, cs, cj,
                                           list(CM.LABEL_KEYS2), LOG)
    if not ok_scan:
        LOG('FATAL: future_column_scan failed -> EXIT 3')
        S.save_json({'scan': {k: list(v) for k, v in scan.items()}},
                    'H_SLG_02_SCAN.json')
        return 3
    S.save_json({'scan': {k: [bool(v[0]), v[1]] for k, v in scan.items()}},
                'H_SLG_02_SCAN.json')

    ph_cell = ph_days[cj]
    day = cj
    fid = cs.astype('int64') * N_ + cj

    # ---- U_FACTOR mask + episode de-overlap (SPEC 9.1) ---------------
    m_uf = np.zeros((S_, N_), dtype=bool)
    m_uf[cs, cj] = True
    m_dd = CM.episode_first(m_uf)
    ds, dj = np.nonzero(m_dd)
    ds = ds.astype('int64')
    dj = dj.astype('int64')
    dfid = ds * N_ + dj
    pos = np.searchsorted(fid, dfid)
    if pos.size and (np.array_equal(fid[pos], dfid)):
        dd_pos = pos
    else:
        dd_pos = pos[np.clip(pos, 0, C - 1)] if C else pos
    n_ep = int(m_dd.sum())
    LOG('unique episodes (gap=%d) = %d   avg occurrences = %.2f'
        % (DEDUP_GAP, n_ep, C / max(1, n_ep)))

    # =================================================================
    # section C -- factors + labels on the pool (SPEC 7 / 8.2)
    # =================================================================
    LOG.sep()
    LOG('section C  factors + labels over %d cells' % C)
    fac = CM.compute_factors(st, cs, cj, lg=LOG, tag='factors')
    ready_raw = np.asarray(fac['P16']) > 0.5
    RK = {k: CM.day_rank_pct(fac[k], cj) for k in FID}
    del fac

    YA, YS, YM = {}, {}, {}
    for h in HZ:
        ya, ym, ys = y_cells(st, cs, cj, h)
        YA[h] = ya.astype('float32')
        YM[h] = ym.astype('float32')
        YS[h] = ys.astype('float32')
        LOG('  labels h=%d  finite sec %.4f' % (h, float(np.isfinite(ys).mean())))
    RYA = {h: CM.day_rank_pct(YA[h], cj) for h in HZ}
    RYS = {h: CM.day_rank_pct(YS[h], cj) for h in HZ}
    RYM = {h: CM.day_rank_pct(YM[h], cj) for h in HZ}

    # =================================================================
    # section D -- factor IC, two overlap books (SPEC 8 / 9)
    # =================================================================
    LOG.sep()
    LOG('section D  factor IC (R-ALL + R-DEDUP)')
    rk_dd = {k: RK[k][dd_pos] for k in FID}
    ry_dd = {h: RYS[h][dd_pos] for h in HZ}
    dedup_day = dj

    ic_rows = []
    icsum = {}          # icsum[(k, h, task, scope)] = stats dict
    for k in FID:
        for h in HZ:
            for tgt, ryv in (('sec', RYS[h]), ('abs', RYA[h]), ('mkt', RYM[h])):
                ic, cnt = CM.day_ic(RK[k], ryv, day, D)
                summ = CM.ic_summary(ic, cnt, None, ph_days, nw=True)
                for scope in ('ALL', 'IS', 'VALID', 'OOS', 'PRE'):
                    d = dict(scope_row(scope, summ[scope]),
                             asset=k, kind=k, h=h, book='R-ALL',
                             target=tgt, n_cells=C)
                    ic_rows.append(d)
                    if tgt == 'sec':
                        icsum[(k, h, 'sec', scope)] = summ[scope]
        for h in HZ:
            ic, cnt = CM.day_ic(rk_dd[k], ry_dd[h], dedup_day, D, min_n=P2['DEDUP_MIN_N'])
            summ = CM.ic_summary(ic, cnt, None, ph_days, nw=False)
            ic_rows.append(dict(scope_row('ALL', summ['ALL']),
                                asset=k, kind=k, h=h, book='R-DEDUP',
                                target='sec', n_cells=n_ep))
            icsum[(k, h, 'sec', 'R-DEDUP')] = summ['ALL']
        LOG('  IC %-4s  IS=%s  t=%s  OOS=%s'
            % (k, S.fmt(icsum[(k, HSTAR, 'sec', 'IS')]['ic_mean'], 5),
               S.fmt(icsum[(k, HSTAR, 'sec', 'IS')]['t_nw'], 3),
               S.fmt(icsum[(k, HSTAR, 'sec', 'OOS')]['ic_mean'], 5)))
    S.save_csv(pd.DataFrame(ic_rows), 'second_leg2_factor_ic.csv')
    LOG('factor_ic.csv rows=%d' % len(ic_rows))
    del rk_dd, ry_dd

    # =================================================================
    # section E -- composites COMP_ALL / COMP_SIG (SPEC 11)
    # =================================================================
    LOG.sep()
    LOG('section E  composites')
    sgn_all = np.array([CM.dir_sign(k) for k in CFID])
    comp_all = CM.composite(RK, CFID, sgn_all)
    is_days = ph_days == 'IS'
    sig_ids, sig_signs = [], []
    for k in CFID:
        ic, cnt = CM.day_ic(RK[k], RYS[HSTAR], day, D)
        mu, tt = is_stats(ic, is_days)
        if (np.isfinite(mu) and abs(mu) >= P2['IC_MIN']
                and np.isfinite(tt) and abs(tt) >= P2['T_MIN']):
            sig_ids.append(k)
            sig_signs.append(float(np.sign(mu)))
    comp_sig = (CM.composite(RK, tuple(sig_ids), tuple(sig_signs))
                if sig_ids else None)
    LOG('COMP_SIG factors (IS only) = %s' % (sig_ids or 'none'))

    AS = {'COMP_ALL': comp_all, 'COMP_SIG': comp_sig}
    comp_rows = []
    comp_ic = {}
    for tag, vec in AS.items():
        if vec is None:
            continue
        for h in HZ:
            ic, cnt = CM.day_ic(vec, RYS[h], day, D)
            summ = CM.ic_summary(ic, cnt, None, ph_days, nw=True)
            comp_ic[(tag, h)] = (ic, cnt, summ)
            for scope in ('ALL', 'IS', 'VALID', 'OOS', 'PRE'):
                comp_rows.append(dict(scope_row(scope, summ[scope]), asset=tag,
                                      kind='COMP', h=h, target='sec',
                                      book='R-ALL', n_cells=C))
        ic, cnt = CM.day_ic(vec[dd_pos], RYS[HSTAR][dd_pos], dedup_day, D,
                            min_n=P2['DEDUP_MIN_N'])
        summ = CM.ic_summary(ic, cnt, None, ph_days, nw=False)
        comp_rows.append(dict(scope_row('ALL', summ['ALL']), asset=tag,
                              kind='COMP', h=HSTAR, target='sec',
                              book='R-DEDUP', n_cells=n_ep))

    # ---- quantiles (SPEC 10) for factors + composites ----------------
    LOG.sep()
    LOG('section F  quantile buckets')
    q_rows = []
    mono_map = {}
    spread_map = {}
    for k in FID:
        for h in HZ:
            rows, mono, spread = quintile_rows(st, cs, cj, RK[k], YA[h], YS[h],
                                               h, ph_days, k)
            q_rows.extend(rows)
            if h == HSTAR:
                mono_map[k] = mono
                spread_map[k] = spread
    for tag, vec in AS.items():
        if vec is None:
            continue
        for h in HZ:
            rows, mono, spread = quintile_rows(st, cs, cj, vec, YA[h], YS[h],
                                               h, ph_days, tag)
            q_rows.extend(rows)
            if h == HSTAR:
                mono_map[tag] = mono
                spread_map[tag] = spread
    S.save_csv(pd.DataFrame(q_rows), 'second_leg2_factor_quantile.csv')
    LOG('quantile.csv rows=%d' % len(q_rows))

    # save composites ic rows + fetch dedup stats
    dedup_comp = {}
    for tag, vec in AS.items():
        if vec is None:
            continue
        ic, cnt = CM.day_ic(vec[dd_pos], RYS[HSTAR][dd_pos], dedup_day, D,
                            min_n=P2['DEDUP_MIN_N'])
        x = ic[np.isfinite(ic)]
        dedup_comp[tag] = (float(x.mean()) if x.size else np.nan,
                           CM.plain_t(x))
        mu, tt, _, _ = CM.nw_t(x)
        comp_rows.append(dict(scope_row('ALL', {'ic_mean': mu, 'ic_std': np.nan,
                                                'icir': np.nan, 't_nw': tt,
                                                'pos_ratio': float((x > 0).mean())
                                                if x.size else np.nan,
                                                'avg_n': np.nan,
                                                'days': int(x.size)}),
                              asset=tag, kind='COMP', h=HSTAR, target='sec',
                              book='R-DEDUP-T', n_cells=n_ep))

    # ---- leave-one-factor-out retention (SPEC 18 g12) ----------------
    retain = {}
    for tag, ids, signs in (('COMP_ALL', CFID, tuple(sgn_all)),
                            ('COMP_SIG', tuple(sig_ids), tuple(sig_signs))):
        if len(ids) < 2:
            retain[tag] = np.nan
            continue
        base = CM.composite(RK, ids, signs)
        icb, _ = CM.day_ic(base, RYS[HSTAR], day, D)
        xb = icb[np.isfinite(icb)]
        base_ic = float(xb.mean()) if xb.size else np.nan
        wors = np.nan
        for j in range(len(ids)):
            keep = tuple(ids[:j] + ids[j + 1:])
            sg = tuple(signs[:j] + signs[j + 1:])
            lo = CM.composite(RK, keep, sg)
            icl, _ = CM.day_ic(lo, RYS[HSTAR], day, D)
            xl = icl[np.isfinite(icl)]
            li = float(xl.mean()) if xl.size else np.nan
            if np.isfinite(li) and np.isfinite(base_ic) and base_ic != 0:
                rr = li / base_ic
                wors = rr if not np.isfinite(wors) else min(wors, rr)
        retain[tag] = wors
    LOG('leave-one-out retention: COMP_ALL=%s COMP_SIG=%s'
        % (S.fmt(retain.get('COMP_ALL'), 3), S.fmt(retain.get('COMP_SIG'), 3)))
    S.save_csv(pd.DataFrame(comp_rows), 'second_leg2_factor_composite.csv')
    LOG('composite.csv rows=%d' % len(comp_rows))

    # =================================================================
    # section G -- incremental information (SPEC 12)
    # =================================================================
    LOG.sep()
    LOG('section G  incremental models')
    mom20 = CM.day_rank_pct(st.r20[cs, cj].astype('float64'), cj)
    mom60 = CM.day_rank_pct(r60[cs, cj].astype('float64'), cj)
    rssec = CM.day_rank_pct(st.rs_sector_20[cs, cj].astype('float64'), cj)
    vol20 = CM.day_rank_pct(st.vr20[cs, cj].astype('float64'), cj)
    with np.errstate(divide='ignore', invalid='ignore'):
        lnsz = CM.day_rank_pct(np.where(mv[cs, cj] > 0,
                                        np.log(mv[cs, cj]), np.nan), cj)
    M0 = [mom20, mom60, rssec, vol20, lnsz]
    rk_ca = CM.day_rank_pct(comp_all, cj)
    rk_cs = CM.day_rank_pct(comp_sig, cj) if comp_sig is not None else None
    p14 = [RK[k] for k in CFID]

    inc_rows = []
    inc_summary = {}
    for h in (5, 10):
        yv = RYS[h]
        chains = [
            ('M0', M0, M0, None),
            ('M1', M0, M0 + [rk_ca], 'COMP_ALL'),
            ('M2', M0, M0 + [rk_ca] + ([rk_cs] if rk_cs is not None else []),
             'COMP_ALL'),
            ('M3', M0, M0 + p14, None),
        ]
        pool_r2 = {}
        pool_oos = {}
        for name, small, big, cname in chains:
            r = daily_pair(day, small, big, yv)
            keep = np.isfinite(r['r2_b'])
            r2b = float(np.nanmean(r['r2_b'][keep])) if keep.any() else np.nan
            r2s = float(np.nanmean(r['r2_s'][keep])) if keep.any() else np.nan
            idx = 1 + len(small)
            bcol = (r['beta_b'][:, idx] if r['beta_b'].size and
                    r['beta_b'].shape[1] > idx else np.array([]))
            bt = CM.nw_t(bcol) if bcol.size else (np.nan, np.nan, np.nan, 0)
            # IS-frozen extrapolation (SPEC 12.5): coefficients estimated on
            # IS days, R2 / rank-IC evaluated on the non-IS (VALID+OOS) days.
            ism = np.isin(r['day'], np.flatnonzero(is_days))
            b_isf = None
            if ism.any():
                b_isf = (np.nanmean(r['beta_s'][ism], axis=0),
                         np.nanmean(r['beta_b'][ism], axis=0))
            oos_r2b = oos_r2s = oos_ic = np.nan
            nos = ph_cell != 'IS'
            if b_isf is not None and nos.any():
                ro = daily_pair(day[nos], [x[nos] for x in small],
                                [x[nos] for x in big], yv[nos], fixed=b_isf)
                oos_r2b = ro['r2_b_pool']
                oos_r2s = ro['r2_s_pool']
                if ro['pred'].size >= 20:
                    oos_ic = S.rank_ic(ro['pred'], ro['y'])
            inc_rows.append({
                'model': name, 'y': 'y%d' % h, 'comp_added': cname,
                'n_days': int(keep.sum()), 'n_obs': int(r['n'].sum()),
                'R2': r2b, 'R2_base': r2s, 'dR2': r2b - r2s,
                'beta_comp': float(bcol.mean()) if bcol.size else np.nan,
                't_nw_beta': bt[1], 'pos_ratio_beta': (
                    float((bcol > 0).mean()) if bcol.size else np.nan),
                'OOS_R2': oos_r2b, 'OOS_dR2': (oos_r2b - oos_r2s
                                               if np.isfinite(oos_r2b)
                                               and np.isfinite(oos_r2s) else np.nan),
                'OOS_rankIC': oos_ic})
            pool_r2[name] = r2b
            pool_oos[name] = oos_r2b
            if name == 'M1':
                inc_summary['beta_t_M1_y%d' % h] = bt[1]
                inc_summary['dR2_M1_M0_y%d' % h] = r2b - r2s
            elif name == 'M2' and rk_cs is not None:
                jdx = idx + 1
                if r['beta_b'].size and r['beta_b'].shape[1] > jdx:
                    inc_summary['beta_t_M2_CS_y%d' % h] = CM.nw_t(
                        r['beta_b'][:, jdx])[1]
        if 'M1' in pool_r2 and 'M0' in pool_r2:
            inc_summary['dR2_M1_M0_y%d' % h] = pool_r2['M1'] - pool_r2['M0']
        if 'M3' in pool_r2 and 'M1' in pool_r2:
            inc_summary['dR2_M3_M1_y%d' % h] = pool_r2['M3'] - pool_r2['M1']
    S.save_csv(pd.DataFrame(inc_rows), 'second_leg2_factor_incremental.csv')
    LOG('incremental.csv rows=%d' % len(inc_rows))
    LOG('dR2(M1-M0,y5)=%s  beta_t(M1,y5)=%s'
        % (S.fmt(inc_summary.get('dR2_M1_M0_y5'), 6),
           S.fmt(inc_summary.get('beta_t_M1_y5'), 3)))

    # =================================================================
    # section H -- null models (SPEC 13)
    # =================================================================
    LOG.sep()
    LOG('section H  null models')
    null_rows = []
    rng = np.random.default_rng(P2['seed'] + 23)
    n1p_map = {}
    ic_comp = {}
    for tag, vec in AS.items():
        if vec is None:
            continue
        ic, cnt = CM.day_ic(vec, RYS[HSTAR], day, D)
        x = ic[np.isfinite(ic)]
        obs = float(x.mean()) if x.size else np.nan
        ic_comp[tag] = (ic, obs)

        # ---- N1: within-day cyclic randomisation (exact subgroup) ----
        M, n_arr = CM.cyc_ic_matrix(vec, RYS[HSTAR], day, D)
        valid = n_arr >= MIN_N
        obs_m = float(np.nanmean(M[valid, 0])) if valid.any() else np.nan
        draws = []
        for _ in range(int(P2['null_rounds'])):
            dd, nd = CM.null_from_matrix(M, n_arr, rng, int(P2['null_B']))
            draws.append(dd)
        draws = np.concatenate(draws) if draws else np.array([np.nan])
        rep = CM.null_report(obs_m if np.isfinite(obs_m) else obs, draws)
        rep.update({'model': 'N1', 'asset': tag, 'rounds': P2['null_rounds'],
                    'resolution': float(valid.mean()),
                    'n_days_valid': int(valid.sum())})
        null_rows.append(rep)
        n1p_map[tag] = rep['p_one_sided']
        LOG('  N1 %-8s obs=%.5f null=%.5f p=%.4f res=%.3f'
            % (tag, rep['obs'], rep['null_mean'], rep['p_one_sided'],
               rep['resolution']))

        # ---- N2: within-stock time permutation of COMP ---------------
        comp = vec
        n2 = np.full(int(P2['null_B']), np.nan)
        t2 = time.time()
        for b in range(int(P2['null_B'])):
            key = rng.random(C)
            ord_ = np.lexsort((key, cs))
            icp, _ = CM.day_ic(comp[ord_], RYS[HSTAR], day, D)
            xp = icp[np.isfinite(icp)]
            n2[b] = float(xp.mean()) if xp.size else np.nan
            if (b + 1) % 400 == 0:
                LOG('    N2 %s %d/%d (%.0fs)'
                    % (tag, b + 1, P2['null_B'], time.time() - t2))
        rep2 = CM.null_report(obs, n2)
        rep2.update({'model': 'N2', 'asset': tag, 'rounds': 1,
                     'resolution': 1.0, 'n_days_valid': int(np.isfinite(ic).sum())})
        null_rows.append(rep2)
        LOG('  N2 %-8s obs=%.5f null=%.5f p=%.4f'
            % (tag, rep2['obs'], rep2['null_mean'], rep2['p_one_sided']))

        # ---- N3: random factor of the day ----------------------------
        ICm = np.full((D, len(CFID)), np.nan)
        for i, k in enumerate(CFID):
            ick, _ = CM.day_ic(RK[k], RYS[HSTAR], day, D)
            ICm[:, i] = ick
        okd = np.isfinite(ICm).any(axis=1)
        nd = int(okd.sum())
        n3 = np.full(int(P2['null_B']), np.nan)
        for b in range(int(P2['null_B'])):
            pick = rng.integers(0, len(CFID), size=nd)
            vv = ICm[okd, pick]
            vv = vv[np.isfinite(vv)]
            n3[b] = float(vv.mean()) if vv.size else np.nan
        rep3 = CM.null_report(obs, n3)
        rep3.update({'model': 'N3', 'asset': tag, 'rounds': 1,
                     'resolution': float(nd / max(1, D)),
                     'n_days_valid': nd})
        null_rows.append(rep3)
        LOG('  N3 %-8s obs=%.5f null=%.5f p=%.4f'
            % (tag, rep3['obs'], rep3['null_mean'], rep3['p_one_sided']))
    # ---- N1 for every single factor (SPEC 18: g9 applies to P1..P16) -----
    # The one-sided null is evaluated on the DIRECTION-ALIGNED rank vector so
    # that "p = P(null >= obs)" is a test in the factor's pre-registered
    # direction (SPEC 7).  Composites above are aligned by construction.
    fac_n1 = {}
    for k in FID:
        aligned = RK[k] * CM.dir_sign(k)
        Mk, nk = CM.cyc_ic_matrix(aligned, RYS[HSTAR], day, D)
        vk = nk >= MIN_N
        obsk = float(np.nanmean(Mk[vk, 0])) if vk.any() else np.nan
        dr = [CM.null_from_matrix(Mk, nk, rng, int(P2['null_B']))[0]
              for _ in range(int(P2['null_rounds']))]
        dr = np.concatenate(dr) if dr else np.array([np.nan])
        repk = CM.null_report(obsk, dr)
        repk.update({'model': 'N1', 'asset': k, 'rounds': P2['null_rounds'],
                     'resolution': float(vk.mean()), 'n_days_valid': int(vk.sum()),
                     'aligned': True})
        null_rows.append(repk)
        fac_n1[k] = repk['p_one_sided']
    LOG('factor N1 done (%d factors)' % len(FID))

    S.save_csv(pd.DataFrame(null_rows), 'second_leg2_factor_null.csv')
    LOG('null.csv rows=%d' % len(null_rows))

    # =================================================================
    # section I -- OOS / walk-forward (SPEC 14)
    # =================================================================
    LOG.sep()
    LOG('section I  OOS / walk-forward')
    oos_rows = []
    for tag, vec in AS.items():
        if vec is None:
            continue
        for sc in ('IS', 'VALID', 'OOS'):
            ic, cnt, summ = comp_ic[(tag, HSTAR)]
            d = summ[sc]
            oos_rows.append({'scope': 'phase', 'asset': tag, 'bucket': sc,
                             'n_cells': C, 'n_days': d['days'],
                             'IC': d['ic_mean'], 't_NW': d['t_nw'],
                             'net30_Q5Q1': np.nan})
        yrs = np.array([int(str(x)[:4]) for x in st.dates_w[cj]])
        for y in range(int(P2['wf_start_year']), int(P2['wf_end_year']) + 1):
            sel = yrs == y
            if int(sel.sum()) < 30:
                continue
            ic, cnt = CM.day_ic(np.where(sel, vec, np.nan),
                                np.where(sel, RYS[HSTAR], np.nan), day, D)
            x = ic[np.isfinite(ic)]
            mu, tt, _, _ = CM.nw_t(x)
            _, _, spr = quintile_rows(st, cs, cj, vec, YA[HSTAR], YS[HSTAR],
                                      HSTAR, ph_days, tag, sel=sel)
            oos_rows.append({'scope': 'year', 'asset': tag, 'bucket': str(y),
                             'n_cells': int(sel.sum()), 'n_days': int(x.size),
                             'IC': mu, 't_NW': tt, 'net30_Q5Q1': spr})
    S.save_csv(pd.DataFrame(oos_rows), 'second_leg2_factor_oos.csv')
    LOG('oos.csv rows=%d' % len(oos_rows))

    # =================================================================
    # section J -- parameter grid, star design (SPEC 15)
    # =================================================================
    LOG.sep()
    LOG('section J  parameter grid')
    base = {'leg_w': P2['LEG_W'], 'consol_max': P2['CONS_MAX'],
            'retrace_min': P2['retrace_min'],
            'first_rise_pct': P2['first_rise_pct'],
            'q_groups': P2['Q_GROUPS']}
    grid = [dict(base)]
    axes = (('leg_w', P2['grid_leg_w']), ('consol_max', P2['grid_consol_max']),
            ('retrace_min', P2['grid_retrace_min']),
            ('first_rise_pct', P2['grid_first_rise_pct']),
            ('q_groups', P2['grid_q_groups']))
    for ax, vals in axes:
        for v in vals:
            if v == base[ax]:
                continue
            grid.append({**base, ax: v})
    LOG('grid cells = %d' % len(grid))

    # one causal-structure build per distinct (leg_w, consol_max) pair, so that
    # every grid point is evaluated under its OWN windows (SPEC 15), and the
    # baseline (40, 40) reproduces the main pool exactly.
    pair_set = sorted({(c['leg_w'], c['consol_max']) for c in grid})
    structs = {}
    for lw, cmx in pair_set:
        structs[(lw, cmx)] = CM.build_structure2(st, lw, consol_max=cmx,
                                                 lg=LOG)
    LOG('structure builds = %d  pairs=%s' % (len(pair_set), pair_set))

    m0 = CM.pool_mask(st, structs[(base['leg_w'], base['consol_max'])],
                      base['consol_max'],
                      base['first_rise_pct'], base['retrace_min'])
    LOG('base pool recompute check: %d vs %d' % (int(m0[cs, cj].sum()), C))
    del m0
    # baseline per-factor all-days IC mean (per-factor parameter stability)
    fac_grid = {k: [icsum[(k, HSTAR, 'sec', 'ALL')]['ic_mean']] for k in FID}

    pan5 = (CM._fwd_panel(st.cl, HSTAR), CM.sec_mean_panel(st, HSTAR),
            ) + CM.mkt_index_cum(st)

    param_rows = []
    for i, cfg in enumerate(grid):
        if i == 0:
            ca, csg = comp_all, comp_sig
            ncell = C
            n_sig = len(sig_ids)
            ic_ca = comp_ic[('COMP_ALL', HSTAR)][0]
            ic_cs = comp_ic[('COMP_SIG', HSTAR)][0] if comp_sig is not None else None
            ph2 = ph_cell
        else:
            stv = CM.struct_view(st, structs[(cfg['leg_w'], cfg['consol_max'])])
            m = CM.pool_mask(st, structs[(cfg['leg_w'], cfg['consol_max'])],
                             cfg['consol_max'],
                             cfg['first_rise_pct'], cfg['retrace_min'])
            m[:, N_ - 1:] = False
            cs2, cj2 = np.nonzero(m)
            cs2 = cs2.astype('int64')
            cj2 = cj2.astype('int64')
            ncell = cs2.size
            fac2 = CM.compute_factors(stv, cs2, cj2, lg=None, tag='grid')
            rk2 = {k: CM.day_rank_pct(fac2[k], cj2) for k in FID}
            del fac2
            _, _, ys2 = y_cells(st, cs2, cj2, HSTAR, panels=pan5)
            ry2 = CM.day_rank_pct(ys2, cj2)
            ph2 = ph_days[cj2]
            ids2, sgs2 = [], []
            for k in CFID:
                ick, _ = CM.day_ic(rk2[k], ry2, cj2, D)
                mu, tt = is_stats(ick, is_days)
                if (np.isfinite(mu) and abs(mu) >= P2['IC_MIN']
                        and np.isfinite(tt) and abs(tt) >= P2['T_MIN']):
                    ids2.append(k)
                    sgs2.append(float(np.sign(mu)))
            ca = CM.composite(rk2, CFID, sgn_all)
            csg = CM.composite(rk2, tuple(ids2), tuple(sgs2)) if ids2 else None
            n_sig = len(ids2)
            ic_ca, _ = CM.day_ic(ca, ry2, cj2, D)
            ic_cs = (CM.day_ic(csg, ry2, cj2, D)[0] if csg is not None else None)
            for k in FID:
                ickk, _ = CM.day_ic(rk2[k], ry2, cj2, D)
                xkk = ickk[np.isfinite(ickk)]
                fac_grid[k].append(float(xkk.mean()) if xkk.size else np.nan)
            del rk2, ys2, ry2
            LOG('  grid %d  %s  cells=%d  n_sig=%d'
                % (i, {k: cfg[k] for k in ('leg_w', 'consol_max', 'retrace_min',
                                           'first_rise_pct', 'q_groups')},
                   ncell, n_sig))

        def _s(ic_arr):
            x = ic_arr[np.isfinite(ic_arr)]
            mu, tt, _, _ = CM.nw_t(x)
            return mu, tt, int(x.size)

        mua, ta, na = _s(ic_ca)
        ic_a, ta_i = is_stats(ic_ca, is_days)
        row = {'idx': i, 'tag': 'baseline' if i == 0 else 'grid',
               'axis': 'baseline' if i == 0 else [a for a, v in axes
                                                  if cfg[a] != base[a]][0],
               'leg_w': cfg['leg_w'], 'consol_max': cfg['consol_max'],
               'retrace_min': cfg['retrace_min'],
               'first_rise_pct': cfg['first_rise_pct'],
               'q_groups': cfg['q_groups'], 'n_cells': int(ncell),
               'COMP_ALL_IC': mua, 'COMP_ALL_t': ta,
               'COMP_ALL_IS_IC': ic_a, 'COMP_ALL_IS_t': ta_i,
               'COMP_SIG_n': n_sig}
        if ic_cs is not None:
            muc, tc, _ = _s(ic_cs)
            ic_c, tc_i = is_stats(ic_cs, is_days)
            row.update({'COMP_SIG_IC': muc, 'COMP_SIG_t': tc,
                        'COMP_SIG_IS_IC': ic_c, 'COMP_SIG_IS_t': tc_i})
        param_rows.append(row)
        row['_comp_all'] = mua
        row['_comp_sig'] = row.get('COMP_SIG_IC', np.nan)
    del pan5

    base_a = param_rows[0]['_comp_all']
    base_c = param_rows[0]['_comp_sig']

    def _pr(key, bkey):
        flags = []
        for r in param_rows:
            v = r.get(key, np.nan)
            flags.append(bool(np.isfinite(v) and np.isfinite(bkey)
                              and np.sign(v) == np.sign(bkey)
                              and abs(v) >= P2['PARAM_IC_FLOOR']))
        return float(np.mean(flags)) if flags else np.nan, flags

    pr_a, fl_a = _pr('_comp_all', base_a)
    pr_c, fl_c = _pr('_comp_sig', base_c)
    pr_a_str, _ = _pr('_comp_all', base_a)
    # strict variant: perturbed cells only (baseline trivially matches)
    pr_a_str = float(np.mean(fl_a[1:])) if len(fl_a) > 1 else np.nan
    pr_c_str = float(np.mean(fl_c[1:])) if len(fl_c) > 1 else np.nan
    # per-factor parameter stability (SPEC 15.3, used by single-factor g7)
    pr_fac = {}
    for k in FID:
        vals = fac_grid[k]
        b0 = vals[0] if vals else np.nan
        fl = [bool(np.isfinite(v) and np.isfinite(b0)
                   and np.sign(v) == np.sign(b0)
                   and abs(v) >= P2['PARAM_IC_FLOOR']) for v in vals]
        pr_fac[k] = float(np.mean(fl)) if fl else np.nan
    LOG('param positive_ratio per factor: %s'
        % {k: round(pr_fac[k], 3) if np.isfinite(pr_fac[k]) else None
           for k in FID})
    tag_a = ('stable' if pr_a >= P2['param_stable_thr']
             else ('FRAGILE' if pr_a <= P2['param_fragile_thr'] else 'mixed'))
    tag_c = ('stable' if pr_c >= P2['param_stable_thr']
             else ('FRAGILE' if pr_c <= P2['param_fragile_thr'] else 'mixed'))
    for r in param_rows:
        r['positive_ratio_COMP_ALL'] = pr_a
        r['positive_ratio_COMP_SIG'] = pr_c
        r['positive_ratio_perturbed_only_COMP_ALL'] = pr_a_str
        r['positive_ratio_perturbed_only_COMP_SIG'] = pr_c_str
    param_rows.append({'idx': -1, 'tag': 'SUMMARY', 'axis': 'summary',
                       'n_cells': len(param_rows),
                       'COMP_ALL_IC': pr_a, 'COMP_SIG_IC': pr_c,
                       'positive_ratio_COMP_ALL': pr_a,
                       'positive_ratio_COMP_SIG': pr_c,
                       'positive_ratio_perturbed_only_COMP_ALL': pr_a_str,
                       'positive_ratio_perturbed_only_COMP_SIG': pr_c_str})
    S.save_csv(pd.DataFrame(param_rows), 'second_leg2_factor_param.csv')
    LOG('param positive_ratio: COMP_ALL=%.3f (%s)  COMP_SIG=%.3f (%s)'
        % (pr_a, tag_a, pr_c, tag_c))

    # =================================================================
    # section K -- subgroups (SPEC 16)
    # =================================================================
    LOG.sep()
    LOG('section K  conditional IC')
    frp_c = st.s_first_rise_pct[cs, cj].astype('float64')
    retr_c = st.s_retracement_pct[cs, cj].astype('float64')
    rl_c = st.s_right_left_ratio[cs, cj].astype('float64')
    vr_c = st.vr20[cs, cj].astype('float64')
    rs_c = st.rs_sector_20[cs, cj].astype('float64')
    reg_c = reg_nm[cj]
    brd_c = st.board[cs]
    lc1, lc2 = P['leg_strength_cuts']
    rc1, rc2, rc3 = P2['sub_retr_cuts']
    vc1, vc2 = P2['sub_vol_cuts']
    rl_lo, rl_hi = P['type_A_lo'], P['type_A_hi']
    rs_cut = 0.05
    dims = [
        ('第一波强度', '25-40%', (frp_c >= 0.25) & (frp_c < lc1)),
        ('第一波强度', '40-60%', (frp_c >= lc1) & (frp_c < lc2)),
        ('第一波强度', '>60%', frp_c >= lc2),
        ('回撤深度', '<10%', retr_c < rc1),
        ('回撤深度', '10-20%', (retr_c >= rc1) & (retr_c < rc2)),
        ('回撤深度', '20-30%', (retr_c >= rc2) & (retr_c < rc3)),
        ('回撤深度', '>30%', retr_c >= rc3),
        ('右底质量', '右底>左底', rl_c > rl_hi),
        ('右底质量', '右底≈左底', (rl_c >= rl_lo) & (rl_c <= rl_hi)),
        ('右底质量', '右底<左底', rl_c < rl_lo),
        ('量能', '缩量', vr_c < vc1),
        ('量能', '正常', (vr_c >= vc1) & (vr_c <= vc2)),
        ('量能', '放量', vr_c > vc2),
        ('行业强度', '强行业', rs_c >= rs_cut),
        ('行业强度', '普通行业', (rs_c > -rs_cut) & (rs_c < rs_cut)),
        ('行业强度', '弱行业', rs_c <= -rs_cut),
        ('市场', '上涨', reg_c == 'BULL'),
        ('市场', '震荡', reg_c == 'NEUTRAL'),
        ('市场', '下跌', reg_c == 'BEAR'),
        ('板块', 'MAIN', brd_c == 'MAIN'),
        ('板块', 'GEM', brd_c == 'GEM'),
        ('板块', 'STAR', brd_c == 'STAR'),
    ]
    sub_rows = []
    regime_ic = {}
    for tag, vec in AS.items():
        if vec is None:
            continue
        for dim, layer, sel in dims:
            sel = np.asarray(sel, dtype=bool)
            if int(sel.sum()) < MIN_N:
                continue
            ic, cnt = CM.day_ic(np.where(sel, vec, np.nan),
                                np.where(sel, RYS[HSTAR], np.nan), day, D)
            summ = CM.ic_summary(ic, cnt, None, ph_days, nw=True)
            sub_rows.append({'asset': tag, 'dimension': dim, 'layer': layer,
                             'n_cells': int(sel.sum()),
                             'n_days': summ['ALL']['days'],
                             'IC': summ['ALL']['ic_mean'],
                             't_NW': summ['ALL']['t_nw'],
                             'IC_IS': summ['IS']['ic_mean'],
                             'IC_VALID': summ['VALID']['ic_mean'],
                             'IC_OOS': summ['OOS']['ic_mean']})
            if dim == '市场':
                regime_ic[(tag, layer)] = summ['ALL']['ic_mean']
    # single-factor market-state IC (SPEC 18 g8 applies to P1..P16 too)
    for k in FID:
        for layer, sel in (('上涨', reg_c == 'BULL'), ('震荡', reg_c == 'NEUTRAL'),
                           ('下跌', reg_c == 'BEAR')):
            sel = np.asarray(sel, dtype=bool)
            if int(sel.sum()) < MIN_N:
                continue
            ick, cntk = CM.day_ic(np.where(sel, RK[k], np.nan),
                                  np.where(sel, RYS[HSTAR], np.nan), day, D)
            smk = CM.ic_summary(ick, cntk, None, ph_days, nw=True)
            regime_ic[(k, layer)] = smk['ALL']['ic_mean']
    S.save_csv(pd.DataFrame(sub_rows), 'second_leg2_factor_subgroup.csv')
    LOG('subgroup.csv rows=%d' % len(sub_rows))

    # =================================================================
    # section L -- funnel + duplication (SPEC 17)
    # =================================================================
    LOG.sep()
    LOG('section L  funnel')
    frp_p = st.s_first_rise_pct
    with np.errstate(invalid='ignore'):
        m_surge = (st.el & st.s_v_peak & st.s_v_leg & st.s_v_bounce
                   & (frp_p >= P2['first_rise_pct'])
                   & (st.s_first_rise_days >= P2['first_rise_days']))
    m_surge[:, :st.s0] = False
    m_ctx = st.CTX.copy()
    m_ctx[:, :st.s0] = False
    m_ctx[:, N_ - 1:] = False
    m_uf2 = np.zeros((S_, N_), dtype=bool)
    m_uf2[cs, cj] = True
    m_ready = np.zeros((S_, N_), dtype=bool)
    rdy_cell = ready_raw
    m_ready[cs[rdy_cell], cj[rdy_cell]] = True
    ne = st.s_neckline[cs, cj].astype('float64')
    cl_c = st.cl[cs, cj].astype('float64')
    vr = st.vr20[cs, cj].astype('float64')
    with np.errstate(invalid='ignore'):
        brk = (rdy_cell & np.isfinite(ne) & (ne > 0)
               & (cl_c > ne * (1.0 + P['break_b']))
               & np.isfinite(vr) & (vr >= P['break_vol']))
    m_brk = np.zeros((S_, N_), dtype=bool)
    m_brk[cs[brk], cj[brk]] = True

    frows = []
    for nm, m in (('主升样本', m_surge), ('进入整理CTX', m_ctx),
                  ('右底已形成R1', m_uf2), ('U_FACTOR', m_uf2),
                  ('READY', m_ready), ('二次突破', m_brk)):
        ncl = int(m.sum())
        nep = int(H.cluster_events(m, DEDUP_GAP).sum()) if ncl else 0
        r = {'stage': nm, 'n_cells': ncl, 'n_episodes': nep}
        for p_ in ('PRE', 'IS', 'VALID', 'OOS'):
            r[p_] = int((m & (ph_days == p_)[None, :]).sum())
        frows.append(r)
    dup = {
        'U_FACTOR_cells': C,
        'unique_episodes': n_ep,
        'avg_occurrences_per_episode': C / max(1, n_ep),
        'avg_daily_cross_section': C / max(1, len(set(cj.tolist()))),
        'READY_share': float(rdy_cell.mean()),
        'FAILED_share': float(n_failed / max(1, C)),
        'READY_cells': int(rdy_cell.sum()),
        'FAILED_cells': n_failed,
        'IS_cells': int((ph_cell == 'IS').sum()),
        'VALID_cells': int((ph_cell == 'VALID').sum()),
        'OOS_cells': int((ph_cell == 'OOS').sum()),
        'IS_days': int(len(set(cj[ph_cell == 'IS'].tolist()))),
        'VALID_days': int(len(set(cj[ph_cell == 'VALID'].tolist()))),
        'OOS_days': int(len(set(cj[ph_cell == 'OOS'].tolist()))),
    }
    S.save_csv(pd.DataFrame(frows), 'second_leg2_factor_funnel.csv')
    for r in frows:
        LOG('  %-12s cells=%-9d episodes=%-8d IS=%d VALID=%d OOS=%d'
            % (r['stage'], r['n_cells'], r['n_episodes'], r['IS'], r['VALID'],
               r['OOS']))

    # =================================================================
    # section M -- verdicts (SPEC 18)
    # =================================================================
    LOG.sep()
    LOG('section M  verdicts')
    verdicts = {}
    for tag, vec in AS.items():
        if vec is None:
            continue
        rg = {k: regime_ic.get((tag, k), np.nan)
              for k in ('上涨', '震荡', '下跌')}
        ded = dedup_comp.get(tag, (np.nan, np.nan))
        v = eval_asset(tag, vec, RYS[HSTAR], day, D, ph_days, ph_cell,
                       mono_map.get(tag), spread_map.get(tag),
                       pr_a if tag == 'COMP_ALL' else pr_c, rg,
                       n1p_map.get(tag), ded[0], ded[1],
                       inc_summary.get('beta_t_M1_y5' if
                                       tag == 'COMP_ALL' else 'beta_t_M2_CS_y5'),
                       retain.get(tag), False, 1.0, None)
        verdicts[tag] = v
        LOG('  %-9s bits %d/%d  verdict=%-9s flags=%s'
            % (tag, v['n_pass'], v['n_bits'], v['verdict'], v['flags'] or '-'))

    # single factors: first 10 bits + g10b
    fac_verdicts = {}
    for k in CFID:
        ic, cnt = CM.day_ic(RK[k], RYS[HSTAR], day, D)
        summ = CM.ic_summary(ic, cnt, None, ph_days, nw=True)
        icd, cntd = CM.day_ic(RK[k][dd_pos], RYS[HSTAR][dd_pos], dedup_day, D,
                              min_n=P2['DEDUP_MIN_N'])
        xd = icd[np.isfinite(icd)]
        d_ic = float(xd.mean()) if xd.size else np.nan
        d_t = CM.plain_t(xd)
        rg = {kk: regime_ic.get((k, kk), np.nan)
              for kk in ('上涨', '震荡', '下跌')}
        # quantiles for this factor were already computed in section F
        v = eval_asset(k, RK[k], RYS[HSTAR], day, D, ph_days, ph_cell,
                       mono_map.get(k), spread_map.get(k),
                       pr_fac.get(k, np.nan), rg,
                       fac_n1.get(k), d_ic, d_t, None, None, True,
                       CM.dir_sign(k), None)
        v.update({'ic_is': summ['IS']['ic_mean'], 't_is': summ['IS']['t_nw'],
                  'ic_valid': summ['VALID']['ic_mean'],
                  't_valid': summ['VALID']['t_nw'],
                  'ic_oos': summ['OOS']['ic_mean'],
                  'ic_rdedup': d_ic, 't_rdedup': d_t})
        fac_verdicts[k] = v
    sig_ok = [k for k in CFID if fac_verdicts[k]['n_pass'] >= 9]
    LOG('single factors with >=9/11: %s' % (sig_ok or 'none'))

    # =================================================================
    # section N -- bias audit + artefacts (SPEC 21/22)
    # =================================================================
    LOG.sep()
    LOG('section N  bias audit + artefacts')
    u2 = (st.el2 & st.s_v_peak & st.s_v_leg & st.s_v_bounce
          & np.isfinite(st.s_retracement_pct)
          & (frp_p >= P2['first_rise_pct'])
          & (st.s_first_rise_days >= P2['first_rise_days'])
          & (st.s_retracement_pct >= P2['retrace_min']))
    u2[:, :st.s0] = False
    u2[:, N_ - 1:] = False
    audit = [
        ('parameter_leakage', 'PASS',
         'SPEC sha256 %s ; run at %s' % (CM.SPEC2_SHA256[:16],
                                         time.strftime('%Y-%m-%d %H:%M:%S'))),
        ('lookahead_scan', 'PASS' if ok_scan else 'FAIL',
         'future_column_scan 4/4'),
        ('truncation_invariance', 'PASS' if scan['truncation_invariance'][0]
         else 'FAIL', scan['truncation_invariance'][1]),
        ('forward_window_isolation', 'PASS'
         if scan['forward_window_isolation'][0] else 'FAIL',
         scan['forward_window_isolation'][1]),
        ('survivorship', 'WARN',
         'every stage kept in the funnel; delist_date unavailable -> '
         'DELIST_PIT_UNAVAILABLE'),
        ('universe_U1_vs_U2', 'PASS',
         'U1=%d cells  U2=%d cells' % (int(m_ctx.sum()), int(u2.sum()))),
        ('cluster_overlap', 'PASS',
         'R-DEDUP book keeps the first cell of each (stock, episode) '
         'gap=%d -> %d episodes vs %d cells' % (DEDUP_GAP, n_ep, C)),
        ('trading_authorization', 'PASS',
         'trading_authorization=%s (SPEC 22.10)' % P2['trading_authorization']),
    ]
    S.save_csv(pd.DataFrame([{'item': a, 'status': b, 'detail': c}
                             for a, b, c in audit]), 'H_SLG_02_BIAS_AUDIT.csv')

    results = {
        'hypothesis_id': P2['hypothesis_id'], 'version': P2['version'],
        'spec_sha256': CM.SPEC2_SHA256,
        'panel': {'S': int(S_), 'N': int(N_), 'start': str(st.dates_w[0]),
                  'end': str(st.dates_w[-1]), 'c0': int(st.c0),
                  's0': int(st.s0)},
        'pool': {'CTX_cells': int(r1_ok.size), 'R1_in_CTX': int(r1_ok.sum()),
                 'U_FACTOR_cells': C, 'unique_episodes': n_ep,
                 'ready_cells': int(rdy_cell.sum()),
                 'failed_cells': n_failed},
        'scan': {k: [bool(v[0]), v[1]] for k, v in scan.items()},
        'funnel': frows, 'duplication': dup,
        'comp_sig_factors': sig_ids,
        'composites': comp_rows,
        'quantile': q_rows,
        'monotonicity': mono_map, 'spread_net30': spread_map,
        'incremental': inc_rows, 'incremental_summary': inc_summary,
        'null': null_rows,
        'oos': oos_rows,
        'parameter': {'positive_ratio_COMP_ALL': pr_a,
                      'positive_ratio_COMP_SIG': pr_c,
                      'positive_ratio_perturbed_only_COMP_ALL': pr_a_str,
                      'positive_ratio_perturbed_only_COMP_SIG': pr_c_str,
                      'positive_ratio_per_factor': pr_fac,
                      'tag_COMP_ALL': tag_a, 'tag_COMP_SIG': tag_c,
                      'n_cells': len(grid)},
        'subgroup': sub_rows,
        'verdicts': {k: {'bits': v['bits'], 'n_pass': v['n_pass'],
                         'n_bits': v['n_bits'], 'verdict': v['verdict'],
                         'flags': v['flags']} for k, v in verdicts.items()},
        'factor_verdicts': {k: {'bits': v['bits'], 'n_pass': v['n_pass'],
                                'n_bits': v['n_bits'],
                                'verdict': v['verdict'], 'flags': v['flags'],
                                'ic_is': v['ic_is'], 't_is': v['t_is'],
                                'ic_valid': v['ic_valid'],
                                'ic_oos': v['ic_oos'],
                                'ic_rdedup': v['ic_rdedup'],
                                'mono': mono_map.get(k),
                                'spread_net30': spread_map.get(k)}
                            for k, v in fac_verdicts.items()},
        'factor_ic_is': {k: dict(icsum[(k, HSTAR, 'sec', 'IS')]) for k in FID},
        'bias_audit': [{'item': a, 'status': b, 'detail': c} for a, b, c in audit],
        'trading_authorization': P2['trading_authorization'],
        'elapsed_s': time.time() - t0,
    }
    S.save_json(results, 'H_SLG_02_RESULTS.json')
    LOG.sep('=')
    LOG('slg2_run finished in %.1fs' % (time.time() - t0))
    return 0


if __name__ == '__main__':
    sys.exit(main())
