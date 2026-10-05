# -*- coding: utf-8 -*-
"""H-SLG-01 -- main run: every computation and every CSV.

Chain under study (H_SLG_01_SPEC.md section 0, never compressed into a score):

    第一波主升 -> 高位二次整理 -> 整理完成 -> 二次突破 -> 后续收益

Sections mirror the SPEC: causal structure (5) / completion states (6-7) /
breakout (8) / four control groups (9) / forward returns + costs (10-11) /
parameter perturbation (12) / null models (13) / OOS + walk-forward (14) /
incremental models (15) / subgroups (16) / survivorship funnel (17) /
verdict bits (18).

Only frozen thresholds from PREREG_S are consumed.  No label column is ever
used as a T-day signal; future_column_scan asserts that (SPEC 21, EXIT 3).
"""
import os
import sys
import json
import time
import math

import numpy as np
import pandas as pd

import slg_common as S
import hve_common as H
import mce_common as MC

P = S.PREREG_S
OUT = S.OUT
LOG = S.Log('slg_run')

CHUNK = 250000          # flat-cell chunk for the completion-variable sweep
GAP = 5                 # cluster gap for independent events

REG_ENC = {'BULL': 0, 'NEUTRAL': 1, 'BEAR': 2, 'NA': 3}


# =====================================================================
# small helpers
# =====================================================================
def eq_nan(a, b):
    a, b = np.asarray(a), np.asarray(b)
    if a.shape != b.shape:
        return False
    fa, fb = np.isfinite(a), np.isfinite(b)
    if not np.array_equal(fa, fb):
        return False
    return bool(np.array_equal(a[fa], b[fb]))


def phase_of(d):
    y = int(str(d)[:4])
    if y <= 2019:
        return 'PRE'
    if y <= 2024:
        return 'IS'
    if y == 2025:
        return 'VALID'
    return 'OOS'


def t_p(t):
    try:
        t = float(t)
    except (TypeError, ValueError):
        return np.nan
    if not np.isfinite(t):
        return np.nan
    return math.erfc(abs(t) / math.sqrt(2.0))


def fwd_cl(st, s, j, h):
    """close-to-close forward return on the sliced panel (label only)."""
    N = st.N
    a = np.asarray(j, dtype='int64')
    b = a + int(h)
    ok = (b >= 0) & (b <= N - 1)
    cc = np.clip(a, 0, N - 1)
    cb = np.clip(b, 0, N - 1)
    p0 = st.cl[s, cc].astype('float64')
    p1 = st.cl[s, cb].astype('float64')
    with np.errstate(invalid='ignore'):
        r = np.where((p0 > 0) & np.isfinite(p1), p1 / p0 - 1.0, np.nan)
    return np.where(ok, r, np.nan)


def maxdd_cl(st, s, j, K=20):
    a = np.asarray(j, dtype='int64')
    off = np.arange(1, K + 1)
    cols = a[:, None] + off[None, :]
    ok = cols <= st.N - 1
    cc = np.clip(cols, 0, st.N - 1)
    p0 = st.cl[s, a].astype('float64')
    cr = (st.cl[s[:, None], cc].astype('float64')
          / np.where(p0 > 0, p0, np.nan)[:, None] - 1.0)
    cr = np.where(ok, cr, np.nan)
    run = np.maximum.accumulate(np.where(np.isfinite(cr), cr, -np.inf), axis=1)
    return np.nanmin(np.where(np.isfinite(cr), cr - run, np.nan), axis=1)


def episode_bounds(cs, cj, gap):
    """positions in (cs,cj) (C-order sorted) marking episode starts / ends."""
    n = cs.size
    if n == 0:
        return np.array([], dtype='int64'), np.array([], dtype='int64')
    newep = np.empty(n, dtype=bool)
    newep[0] = True
    if n > 1:
        same = (cs[1:] == cs[:-1])
        newep[1:] = ~(same & ((cj[1:] - cj[:-1]) <= gap))
    starts = np.flatnonzero(newep)
    ends = np.r_[starts[1:] - 1, n - 1]
    return starts.astype('int64'), ends.astype('int64')


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


def agg(r, months=None, do_boot=True):
    d = S.stats(r, do_boot=do_boot, months=months)
    d['gross'] = d.get('mean')
    return d


def xs_tercile(v, day):
    """cross-sectional 0/1/2 tercile inside each day, -1 when undefined."""
    ser = pd.Series(np.asarray(v, dtype='float64'))
    dser = pd.Series(np.asarray(day))

    def _f(x):
        out = np.full(x.size, -1, dtype='float64')
        m = np.isfinite(x.to_numpy())
        if int(m.sum()) >= 3:
            vv = x.to_numpy()[m]
            q1, q2 = np.percentile(vv, [100.0 / 3.0, 200.0 / 3.0])
            out[m] = np.where(vv <= q1, 0.0, np.where(vv <= q2, 1.0, 2.0))
        return pd.Series(out, index=x.index)

    return ser.groupby(dser).transform(_f).to_numpy()


def brk_labels(st, s_, j_):
    """SUC_BRK / FAIL_BRK labels (SPEC 17) -- future information only."""
    lo_w, _ = H.win_post(st.lo, s_, j_, P['label_brk_window'])
    cl_w, _ = H.win_post(st.cl, s_, j_, P['label_brk_window'])
    ne = st.s_neckline[s_, j_].astype('float64')
    with np.errstate(invalid='ignore'):
        minlo = np.nanmin(lo_w, axis=1)
        maxcl = np.nanmax(cl_w, axis=1)
        fail = np.where(np.isfinite(minlo), minlo < ne * (1.0 - P['label_fail_drop']),
                        False)
        suc = np.where(np.isfinite(maxcl),
                       (~fail) & (maxcl >= ne * (1.0 + P['label_suc_rise'])), False)
    return suc.astype(bool), fail.astype(bool)


# =====================================================================
# future column scan (SPEC 21); any failure -> EXIT 3
# =====================================================================
def future_column_scan(g, st, cs, cj, label_keys):
    LOG('future_column_scan')
    res = {}

    # 1 truncation invariance -----------------------------------------
    gt = S.truncate(g, 120)
    i0 = int(np.searchsorted(gt['dates'], P['study_start']))
    c0 = max(0, i0 - int(P['burn_in_sessions']))
    sl = slice(c0, gt['close'].shape[1])
    st_t = S.build_structure(gt['high'][:, sl], gt['low'][:, sl])
    n_cmp = st_t['peak_off'].shape[1]
    pairs = [('peak_off', st.s_peak_off), ('leg_off', st.s_leg_off),
             ('bounce_off', st.s_bounce_off), ('lb_off', st.s_lb_off),
             ('rb_off', st.s_rb_off), ('first_rise_days', st.s_first_rise_days),
             ('first_rise_pct', st.s_first_rise_pct),
             ('retracement_pct', st.s_retracement_pct),
             ('right_left_ratio', st.s_right_left_ratio),
             ('neckline', st.s_neckline), ('struct_type', st.s_struct_type)]
    bad = [k for k, a in pairs if not eq_nan(a[:, :n_cmp], st_t[k])]
    res['truncation_invariance'] = (len(bad) == 0,
                                    'cols=%d mismatched=%s' % (n_cmp, bad or 'none'))

    # 2 backward window proof ----------------------------------------
    k = min(cs.size, 20000)
    s_, j_ = cs[:k], cj[:k]
    po = st.s_peak_off[s_, j_].astype('int64')
    ne = st.s_neckline[s_, j_].astype('float64')
    hp = st.hi[s_, j_ - po].astype('float64')
    ok_lag = bool((po >= P['CONS_MIN']).all()) if po.size else False
    ok_px = bool(np.allclose(ne, hp, rtol=0, atol=1e-5, equal_nan=True))
    res['backward_window_proof'] = (
        ok_lag and ok_px,
        'min_peak_off=%d  neckline==high[peak_day]=%s (n=%d)'
        % (int(po.min()) if po.size else -1, ok_px, k))

    # 3 forward window isolation -------------------------------------
    inter = set(S.SIGNAL_COLUMNS) & set(S.LABEL_COLUMNS)
    res['forward_window_isolation'] = (
        len(inter) == 0,
        'signal/label intersection=%s' % (sorted(inter) or 'empty'))

    # 4 label columns marked -----------------------------------------
    unmarked = [c for c in label_keys if c not in S.LABEL_COLUMNS]
    res['label_columns_marked'] = (len(unmarked) == 0,
                                   'unmarked=%s' % (unmarked or 'none'))

    ok = True
    for kk in ('truncation_invariance', 'backward_window_proof',
               'forward_window_isolation', 'label_columns_marked'):
        good, detail = res[kk]
        ok &= bool(good)
        LOG('  %-26s %-4s  %s' % (kk, 'OK' if good else 'FAIL', detail))
    return ok, res


# =====================================================================
# main
# =====================================================================
def main():
    t0 = time.time()
    LOG.sep('=')
    LOG('H-SLG-01 main run  version %s' % P['version'])
    LOG.sep('=')

    if not S.verify_spec(LOG):
        LOG('FATAL: SPEC hash mismatch -> EXIT 2')
        return 2

    g = S.load_grid(LOG)
    st = S.State(g, LOG)
    S_, N_ = st.S, st.N
    LOG('sliced panel S=%d N=%d  %s..%s  (c0=%d s0=%d)'
        % (S_, N_, st.dates_w[0], st.dates_w[-1], st.c0, st.s0))

    # ---- auxiliary matrices -----------------------------------------
    reg = np.asarray(MC.regime_ma(g, LOG), dtype=object)[st.c0:]
    reg_num = pd.Series(reg).map(lambda v: REG_ENC.get(str(v), 3)).to_numpy()
    mv = np.ascontiguousarray(MC.load_size_mv(g)[:, st.c0:])
    turn = np.ascontiguousarray(load_basic_cols(g, ['turnover_rate'])
                                ['turnover_rate'][:, st.c0:])
    LOG('aux matrices: mv / turnover / regime ready')

    mkt = np.asarray(st.mkt_ret, dtype='float64')
    Z = pd.Series(np.where(np.isfinite(mkt),
                           np.cumprod(1.0 + np.nan_to_num(mkt, nan=0.0)), np.nan)
                  ).ffill().to_numpy()

    n_l1 = len(st.l1s)
    SECR = np.full((n_l1, N_), np.nan)
    for t in range(N_):
        m = st.el[:, t] & (st.ind[:, t] >= 0) & np.isfinite(st.r1[:, t])
        if not m.any():
            continue
        jj = st.ind[m, t].astype('int64')
        vv = st.r1[m, t].astype('float64')
        ss = np.bincount(jj, weights=vv, minlength=n_l1)
        cc = np.bincount(jj, minlength=n_l1).astype('float64')
        SECR[:, t] = np.where(cc > 0, ss / np.maximum(cc, 1e-9), np.nan)
    CLR = np.cumsum(np.log1p(np.nan_to_num(SECR, nan=0.0)), axis=1)
    LOG('sector matrix %d x %d' % SECR.shape)

    p60 = np.full((S_, N_), np.nan, dtype='float32')
    p60[:, 60:] = st.cl[:, :-60]
    with np.errstate(invalid='ignore'):
        r60 = np.where(p60 > 0, st.cl / p60 - 1.0, np.nan).astype('float32')
    del p60

    # ---- candidate base (loosest grid thresholds, SPEC 12) ----------
    frp = st.s_first_rise_pct
    with np.errstate(invalid='ignore'):
        base = (st.el & st.s_v_peak & st.s_v_leg & st.s_v_bounce
                & np.isfinite(st.s_retracement_pct)
                & (frp >= min(P['grid_first_rise_pct']))
                & (st.s_first_rise_days >= min(P['grid_first_rise_days']))
                & (st.s_retracement_pct >= min(P['grid_retrace_min'])))
    consol = np.asarray(st.s_peak_off, dtype='int64')
    base = base & (consol <= P['CONS_MAX'])
    base[:, :st.s0] = False
    base[:, N_ - 1:] = False
    cs, cj = np.nonzero(base)
    cs = cs.astype('int64')
    cj = cj.astype('int64')
    n_cand = cs.size
    LOG('CTX_BASE cells = %d  (State.CTX strict cells = %d)'
        % (n_cand, int(st.CTX.sum())))

    ok, scan = future_column_scan(g, st, cs, cj, list(S.LABEL_COLUMNS))
    S.save_csv(pd.DataFrame([{'check': k, 'status': 'PASS' if v[0] else 'FAIL',
                              'detail': v[1]} for k, v in scan.items()]),
               'H_SLG_01_FUTURE_SCAN.csv')
    if not ok:
        LOG('FATAL: future_column_scan failed -> EXIT 3')
        return 3

    # =================================================================
    # section D -- completion state sweep over the candidate cells
    # =================================================================
    LOG.sep()
    LOG('section D  completion states over %d candidate cells' % n_cand)
    ready = np.zeros(n_cand, dtype=bool)
    failed = np.zeros(n_cand, dtype=bool)
    r5_all = np.full(n_cand, np.nan)
    r10_all = np.full(n_cand, np.nan)
    bit_counts = {k: 0 for k in ('R1', 'R2', 'R3', 'R4', 'R5', 'R6',
                                 'F1', 'F2', 'F3', 'F4', 'F5')}
    code_cnt = np.zeros(5, dtype='int64')
    for a in range(0, n_cand, CHUNK):
        b = min(n_cand, a + CHUNK)
        s_, j_ = cs[a:b], cj[a:b]
        cv = S.completion_vars(st, s_, j_)
        ready[a:b] = cv['READY']
        failed[a:b] = cv['FAILED']
        r5_all[a:b] = fwd_cl(st, s_, j_, P['Hstar'])
        r10_all[a:b] = fwd_cl(st, s_, j_, P['reg_h2'])
        for k in bit_counts:
            bit_counts[k] += int(cv[k].sum())
        code_cnt += np.bincount(st.s_struct_type[s_, j_].astype('int64'),
                                minlength=5)[:5]
        if (a // CHUNK) % 4 == 0:
            LOG('  swept %d/%d (%.1fs)' % (b, n_cand, time.time() - t0))
    consolidating = (~ready) & (~failed)
    LOG('READY=%d FAILED=%d CONSOLIDATING=%d'
        % (int(ready.sum()), int(failed.sum()), int(consolidating.sum())))
    LOG('R bits: ' + ' '.join('%s=%.4f' % (k, bit_counts[k] / max(1, n_cand))
                              for k in ('R1', 'R2', 'R3', 'R4', 'R5', 'R6')))
    LOG('F bits: ' + ' '.join('%s=%.4f' % (k, bit_counts[k] / max(1, n_cand))
                              for k in ('F1', 'F2', 'F3', 'F4', 'F5')))
    LOG('struct type counts A/B/C/D = %s' % code_cnt[1:].tolist())

    def to_mask(sel):
        m = np.zeros((S_, N_), dtype=bool)
        m[cs[sel], cj[sel]] = True
        return m

    base_mask = to_mask(np.ones(n_cand, dtype=bool))
    ready_mask = to_mask(ready)
    failed_mask = to_mask(failed)
    cons_mask = to_mask(consolidating)
    ctx_mask = np.asarray(st.CTX).copy()
    ctx_mask[:, :st.s0] = False
    ctx_mask[:, N_ - 1:] = False

    # ---- breakout (SPEC 8) -----------------------------------------
    ne = st.s_neckline[cs, cj].astype('float64')
    cl_c = st.cl[cs, cj].astype('float64')
    vr = st.vr20[cs, cj].astype('float64')
    brk = {}
    for b_ in P['break_b_grid']:
        brk[b_] = (ready & np.isfinite(ne) & (ne > 0)
                   & (cl_c > ne * (1.0 + b_)))
    brk_main = brk[P['break_b']] & np.isfinite(vr) & (vr >= P['break_vol'])
    LOG('breakout b=0 cells=%d  b=0 & vol>=1.0 cells=%d'
        % (int(brk[P['break_b']].sum()), int(brk_main.sum())))

    # =================================================================
    # section E -- control groups (SPEC 9), clustered to independent events
    # =================================================================
    LOG.sep()
    LOG('section E  control groups')
    br20 = (st.cl > st.hi20_prev) & np.isfinite(st.hi20_prev)
    grpB = (st.el & ~base_mask & br20 & np.isfinite(st.vr20)
            & (st.vr20 >= P['groupB_vol_min']))
    grpB[:, :st.s0] = False
    grpB[:, N_ - 1:] = False

    grp_raw = {
        'A': to_mask(brk_main),
        'B': grpB,
        'C1': to_mask(ready & ~brk[P['break_b']]),
        'C2': cons_mask,
        'D': failed_mask,
    }
    grp = {k: H.cluster_events(v, GAP) for k, v in grp_raw.items()}
    for k in ('A', 'B', 'C1', 'C2', 'D'):
        LOG('  group %-3s raw=%-9d clustered=%d'
            % (k, int(grp_raw[k].sum()), int(grp[k].sum())))

    def cells(mask):
        s_, j_ = np.nonzero(mask)
        return s_.astype('int64'), j_.astype('int64')

    eA_s, eA_j = cells(grp['A'])
    sucA, failA = brk_labels(st, eA_s, eA_j)
    LOG('Group A success=%d failure=%d other=%d'
        % (int(sucA.sum()), int(failA.sum()),
           int((~sucA & ~failA).sum())))

    # =================================================================
    # section F -- survivorship funnel (SPEC 17)
    # =================================================================
    LOG.sep()
    LOG('section F  funnel')
    m_surge = (st.el & st.s_v_peak & st.s_v_leg
               & (frp >= P['first_rise_pct'])
               & (st.s_first_rise_days >= P['first_rise_days'])
               & st.s_v_bounce)
    m_surge[:, :st.s0] = False
    m_surge[:, N_ - 1:] = False
    rb_mask = base_mask & st.s_v_rb
    ph_cells = {p_: np.array([phase_of(d) == p_ for d in st.dates_w])
                for p_ in ('PRE', 'IS', 'VALID', 'OOS')}
    frows = []
    for nm, m in (('主升样本', m_surge), ('进入整理', base_mask),
                  ('形成右底', rb_mask), ('READY', ready_mask),
                  ('突破', to_mask(brk_main))):
        sts, ens = episode_bounds(*cells(m), GAP) if m.any() else \
            (np.array([], dtype='int64'), np.array([], dtype='int64'))
        r = {'stage': nm, 'n_cells': int(m.sum()), 'n_episodes': int(sts.size)}
        for p_ in ('PRE', 'IS', 'VALID', 'OOS'):
            r[p_] = int((m & ph_cells[p_][None, :]).sum())
        frows.append(r)
    for nm, sel in (('成功突破', sucA), ('失败突破', failA)):
        ss, jj = eA_s[sel], eA_j[sel]
        r = {'stage': nm, 'n_cells': int(sel.sum()), 'n_episodes': int(sel.sum())}
        for p_ in ('PRE', 'IS', 'VALID', 'OOS'):
            r[p_] = int(np.sum([phase_of(st.dates_w[int(x)]) == p_ for x in jj]))
        frows.append(r)
    S.save_csv(pd.DataFrame(frows), 'H_SLG_01_FUNNEL.csv')
    for r in frows:
        LOG('  %-12s cells=%-9d episodes=%-8d' % (r['stage'], r['n_cells'],
                                                  r['n_episodes']))

    # =================================================================
    # section G -- candidate episode table
    # =================================================================
    LOG.sep()
    LOG('section G  candidate episodes')
    starts, ends = episode_bounds(cs, cj, GAP)
    n_ep = starts.size
    eid = np.repeat(np.arange(n_ep), np.diff(np.r_[starts, n_cand]))
    ever_ready = np.bincount(eid, weights=ready.astype('float64'),
                             minlength=n_ep) > 0
    ever_brk = np.bincount(eid, weights=brk_main.astype('float64'),
                           minlength=n_ep) > 0
    es_s, es_j = cs[starts], cj[starts]
    ee_s, ee_j = cs[ends], cj[ends]
    LOG('episodes = %d  ever_READY=%d  ever_breakout=%d'
        % (n_ep, int(ever_ready.sum()), int(ever_brk.sum())))
    po_e = st.s_peak_off[ee_s, ee_j].astype('int64')
    bo_e = st.s_bounce_off[ee_s, ee_j].astype('int64')
    lbo_e = st.s_lb_off[ee_s, ee_j].astype('int64')
    rbo_e = st.s_rb_off[ee_s, ee_j].astype('int64')
    leg_e = st.s_leg_off[ee_s, ee_j].astype('int64')
    cand_df = pd.DataFrame({
        'ts_code': st.codes[ee_s], 'board': st.board[ee_s],
        'ep_start_date': st.dates_w[es_j], 'ep_end_date': st.dates_w[ee_j],
        'ep_days': (ee_j - es_j + 1).astype('int64'),
        'phase': [phase_of(d) for d in st.dates_w[ee_j]],
        's_i': ee_s, 't0_end': ee_j,
        'peak_date': st.dates_w[(ee_j - po_e)],
        'leg_start_date': st.dates_w[(ee_j - leg_e)],
        'bounce_date': st.dates_w[(ee_j - bo_e)],
        'left_bottom_date': st.dates_w[(ee_j - lbo_e)],
        'right_bottom_date': st.dates_w[(ee_j - rbo_e)],
        'first_rise_pct': st.s_first_rise_pct[ee_s, ee_j],
        'first_rise_days': st.s_first_rise_days[ee_s, ee_j],
        'retracement_pct': st.s_retracement_pct[ee_s, ee_j],
        'right_left_ratio': st.s_right_left_ratio[ee_s, ee_j],
        'bottom_spacing_days': st.s_bottom_spacing_days[ee_s, ee_j],
        'consol_days': po_e, 'struct_type': st.s_struct_type[ee_s, ee_j],
        'ever_READY': ever_ready.astype(int),
        'ever_breakout': ever_brk.astype(int),
    })
    S.save_csv(cand_df, 'second_leg_candidates.csv')
    LOG('candidates.csv rows=%d' % cand_df.shape[0])
    del cand_df

    # =================================================================
    # section H -- event study (SPEC 10-11) over the five groups
    # =================================================================
    LOG.sep()
    LOG('section H  event study')
    log_rows = []
    ev_cells = {}
    for k in ('A', 'B', 'C1', 'C2', 'D'):
        s_, j_ = cells(grp[k])
        if s_.size == 0:
            continue
        ev_cells[k] = (s_, j_)
        cv = S.completion_vars(st, s_, j_)
        months = st.months(j_)
        rec = {'group': k, 'n_events': int(s_.size),
               'mean_first_rise_pct': float(np.nanmean(st.s_first_rise_pct[s_, j_])),
               'mean_retracement_pct': float(np.nanmean(st.s_retracement_pct[s_, j_]))}
        for h in P['horizons']:
            r = fwd_cl(st, s_, j_, h)
            d = agg(r, months=months, do_boot=(h == P['Hstar']))
            for kk, vv in d.items():
                rec['%s_%s' % (kk, h)] = vv
            b_ = j_ + h
            okb = b_ <= N_ - 1
            mk = np.where(okb, Z[np.clip(b_, 0, N_ - 1)] / Z[j_] - 1.0, np.nan)
            L = st.ind[s_, j_].astype('int64')
            sec = np.full(s_.size, np.nan)
            good = okb & (L >= 0) & (L < n_l1)
            if good.any():
                gg = L[good]
                sec[good] = np.exp(CLR[gg, np.clip(b_[good], 0, N_ - 1)]
                                   - CLR[gg, j_[good]]) - 1.0
            ex_m = r - mk
            ex_s = r - sec
            rec['exmkt_mean_%d' % h] = float(np.nanmean(ex_m))
            rec['exsec_mean_%d' % h] = float(np.nanmean(ex_s))
            rec['exmkt_net30_%d' % h] = (float(np.nanmean(ex_m))
                                         - P['primary_cost_bp'] / 10000.0)
            rec['exsec_net30_%d' % h] = (float(np.nanmean(ex_s))
                                         - P['primary_cost_bp'] / 10000.0)
        mfe, mae = H.mfe_mae_at(g, s_, j_ + st.c0, P['label_brk_window'])
        rec['mae_20'] = float(np.nanmean(mae))
        rec['mfe_20'] = float(np.nanmean(mfe))
        rec['maxdd_20'] = float(np.nanmean(
            maxdd_cl(st, s_, j_, P['label_brk_window'])))
        log_rows.append(rec)
        LOG('  %-3s n=%-7d net30(T5)=%s  win30=%s  exmkt=%s'
            % (k, s_.size, S.fmt(rec.get('mean_net30_%d' % P['Hstar']), 5),
               S.fmt(rec.get('win_net30_%d' % P['Hstar']), 4),
               S.fmt(rec.get('exmkt_net30_%d' % P['Hstar']), 5)))
        del cv
    S.save_csv(pd.DataFrame(log_rows), 'second_leg_event_study.csv')

    def sget(group, key):
        for r in log_rows:
            if r['group'] == group:
                return r.get(key, np.nan)
        return np.nan

    # =================================================================
    # section I -- OOS phases + yearly (SPEC 14.1)
    # =================================================================
    LOG.sep()
    LOG('section I  phases')
    oos_rows = []
    for k in ('A', 'B', 'C1', 'C2', 'D'):
        if k not in ev_cells:
            continue
        s_, j_ = ev_cells[k]
        r5 = fwd_cl(st, s_, j_, P['Hstar'])
        ph = np.array([phase_of(st.dates_w[int(x)]) for x in j_])
        for p_ in ('PRE', 'IS', 'VALID', 'OOS'):
            m = ph == p_
            d = S.stats(r5[m])
            oos_rows.append({'scope': 'phase', 'group': k, 'bucket': p_,
                             'n': int(m.sum()), 'gross': d['mean'],
                             'net15': d['mean_net15'], 'net30': d['mean_net30'],
                             'net50': d['mean_net50'], 'win30': d['win_net30']})
    sA, jA = ev_cells['A']
    r5A = fwd_cl(st, sA, jA, P['Hstar'])
    yrsA = np.array([int(st.dates_w[int(x)][:4]) for x in jA])
    for y in sorted(set(yrsA.tolist())):
        m = yrsA == y
        d = S.stats(r5A[m])
        oos_rows.append({'scope': 'year', 'group': 'A', 'bucket': str(y),
                         'n': int(m.sum()), 'gross': d['mean'],
                         'net15': d['mean_net15'], 'net30': d['mean_net30'],
                         'net50': d['mean_net50'], 'win30': d['win_net30']})
    S.save_csv(pd.DataFrame(oos_rows), 'second_leg_oos.csv')

    # =================================================================
    # section J -- walk forward (SPEC 14.2)
    # =================================================================
    LOG.sep()
    LOG('section J  walk-forward')
    wf_rows = []
    feat_wf = np.column_stack([
        st.s_first_rise_pct[sA, jA].astype('float64'),
        st.s_retracement_pct[sA, jA].astype('float64'),
        st.s_peak_off[sA, jA].astype('float64'),
        st.s_right_left_ratio[sA, jA].astype('float64'),
        st.vr20[sA, jA].astype('float64'),
    ])
    for test_y in range(P['wf_start_year'], P['wf_end_year'] + 1):
        tr = (yrsA >= test_y - P['wf_train_years']) & (yrsA <= test_y - 1)
        te = yrsA == test_y
        if te.sum() < 5:
            continue
        icv = np.nan
        if tr.sum() >= 30:
            sub = np.isfinite(feat_wf[tr]).all(axis=1)
            if sub.sum() >= 30:
                beta, _ = S.ols_fit(feat_wf[tr][sub], r5A[tr][sub])
                Ate = np.column_stack([np.ones(int(te.sum())), feat_wf[te]])
                icv = S.rank_ic(Ate.dot(beta), r5A[te])
        d_te = S.stats(r5A[te])
        wf_rows.append({'train': '%d-%d' % (test_y - P['wf_train_years'],
                                            test_y - 1),
                        'test': str(test_y), 'n_train': int(tr.sum()),
                        'n_test': int(te.sum()), 'gross': d_te['mean'],
                        'net30': d_te['mean_net30'], 'win30': d_te['win_net30'],
                        'IC': icv})
    S.save_csv(pd.DataFrame(wf_rows), 'H_SLG_01_WALKFORWARD.csv')

    # =================================================================
    # section K -- parameter perturbation (SPEC 12)
    # =================================================================
    LOG.sep()
    LOG('section K  parameter stability')
    frp_c = st.s_first_rise_pct[cs, cj].astype('float64')
    frd_c = st.s_first_rise_days[cs, cj].astype('int64')
    cdl_c = consol[cs, cj]
    rmn_c = st.s_retracement_pct[cs, cj].astype('float64')
    dflt = {'first_rise_pct': P['first_rise_pct'],
            'first_rise_days': P['first_rise_days'],
            'consol_max': P['CONS_MAX'], 'retrace_min': P['retrace_min'],
            'break_b': P['break_b'], 'break_vol': P['break_vol']}
    cells_param = [dict(dflt)]
    for ax, vals in (('first_rise_pct', P['grid_first_rise_pct']),
                     ('first_rise_days', P['grid_first_rise_days']),
                     ('consol_max', P['grid_consol_max']),
                     ('retrace_min', P['grid_retrace_min']),
                     ('break_b', P['break_b_grid']),
                     ('break_vol', P['break_vol_grid'])):
        for v in vals:
            if v == dflt[ax]:
                continue
            c = dict(dflt)
            c[ax] = v
            cells_param.append(c)
    for a_ in P['grid_first_rise_pct']:
        for b_ in P['grid_retrace_min']:
            for c_ in P['break_b_grid']:
                cells_param.append({**dflt, 'first_rise_pct': a_,
                                    'retrace_min': b_, 'break_b': c_})
    seen, uniq = set(), []
    for c in cells_param:
        key = tuple(sorted(c.items()))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)
    LOG('unique grid cells = %d' % len(uniq))

    def cell_events(c):
        return (ready & (frp_c >= c['first_rise_pct'])
                & (frd_c >= c['first_rise_days']) & (cdl_c <= c['consol_max'])
                & (rmn_c >= c['retrace_min']) & np.isfinite(ne)
                & (cl_c > ne * (1.0 + c['break_b']))
                & np.isfinite(vr) & (vr >= c['break_vol']))

    prow, pos_flag = [], []
    for i, c in enumerate(uniq):
        m = cell_events(c)
        d = S.stats(r5_all[m])
        prow.append({'idx': i, 'tag': 'baseline' if c == dflt else 'grid',
                     'n': int(m.sum()), 'gross': d['mean'],
                     'net30_T5': d['mean_net30'], 'win30': d['win_net30']})
        pos_flag.append(bool(np.isfinite(d['mean_net30']) and d['mean_net30'] > 0))
    pos_ratio = float(np.mean(pos_flag)) if pos_flag else np.nan
    ptag = ('stable' if pos_ratio >= P['param_stable_thr']
            else ('FRAGILE' if pos_ratio <= P['param_fragile_thr'] else 'mixed'))
    prow.append({'idx': -1, 'tag': 'SUMMARY', 'n': len(uniq),
                 'gross': np.nan, 'net30_T5': pos_ratio, 'win30': np.nan})
    S.save_csv(pd.DataFrame(prow), 'second_leg_parameter_stability.csv')
    LOG('parameter positive_ratio=%.3f -> %s' % (pos_ratio, ptag))

    # =================================================================
    # section L -- null models (SPEC 13)
    # =================================================================
    LOG.sep()
    LOG('section L  null models')
    mv_t = xs_tercile(mv[cs, cj], cj)
    tn_t = xs_tercile(turn[cs, cj], cj)
    ind_c = st.ind[cs, cj].astype('int64')
    reg_c = reg_num[cj].astype('int64')
    frp_b = np.digitize(frp_c, list(P['leg_strength_cuts'])).astype('int64')
    safe = lambda a: np.where(a < 0, 0, a)          # noqa: E731
    key_c = ((((frp_b * 3 + safe(mv_t)) * 3 + safe(tn_t)) * 32
              + np.where(ind_c < 0, 31, ind_c)) * 4 + reg_c).astype('int64')

    frpA = st.s_first_rise_pct[sA, jA].astype('float64')
    frpA_b = np.digitize(frpA, list(P['leg_strength_cuts'])).astype('int64')
    mvA_t = xs_tercile(mv[sA, jA], jA)
    tnA_t = xs_tercile(turn[sA, jA], jA)
    indA = st.ind[sA, jA].astype('int64')
    keyA = ((((frpA_b * 3 + safe(mvA_t)) * 3 + safe(tnA_t)) * 32
             + np.where(indA < 0, 31, indA)) * 4
            + reg_num[jA].astype('int64')).astype('int64')

    order = np.argsort(key_c, kind='stable')
    cf = cs[order] * N_ + cj[order]
    rf = r5_all[order]
    r_flat = np.full(S_ * N_, np.nan, dtype='float64')   # flat-id indexed
    r_flat[cf] = rf
    uq, first = np.unique(key_c[order], return_index=True)
    lo_ = first.astype('int64')
    hi_ = np.r_[first[1:], key_c.size].astype('int64')
    self_flat = cs * N_ + cj
    obsA = float(np.nanmean(r5A))
    n1 = S.null_family(r_flat, cf, lo_, hi_, uq, keyA, self_flat, obsA,
                       B=P['null_B'], seed=P['seed'])
    del r_flat

    rng = np.random.default_rng(P['seed'] + 7)
    peakA = jA - st.s_peak_off[sA, jA].astype('int64')
    n2_means = np.full(P['null_B'], np.nan)
    cover2 = 0
    for b_i in range(P['null_B']):
        jj = rng.integers(peakA + 1, jA + 1)
        cover2 += int((jj > peakA).sum())
        n2_means[b_i] = np.nanmean(fwd_cl(st, sA, jj, P['Hstar']))
    m2 = n2_means[np.isfinite(n2_means)]
    n2 = {'obs': obsA, 'B': int(P['null_B']),
          'resolution': float(cover2 / max(1, P['null_B'] * sA.size)),
          'key_coverage': 1.0,
          'null_mean': float(m2.mean()) if m2.size else np.nan,
          'null_lo': float(np.percentile(m2, 2.5)) if m2.size else np.nan,
          'null_hi': float(np.percentile(m2, 97.5)) if m2.size else np.nan,
          'null_std': float(m2.std(ddof=1)) if m2.size > 1 else np.nan}
    n2['excess'] = n2['obs'] - n2['null_mean']
    n2['p_one_sided'] = float((m2 >= obsA).mean()) if m2.size else np.nan

    regA = reg_num[jA].astype('int64')
    n3_means = np.full(P['null_B'], np.nan)
    for b_i in range(P['null_B']):
        acc = np.full(sA.size, np.nan)
        todo = np.arange(sA.size)
        tries = 0
        while todo.size and tries < 30:
            jc = rng.integers(st.s0, N_ - 6, size=todo.size)
            sc = sA[todo]
            okk = st.el[sc, jc] & (reg_num[jc] == regA[todo])
            hit = todo[okk]
            if hit.size:
                acc[hit] = fwd_cl(st, sc[okk], jc[okk], P['Hstar'])
            todo = todo[~okk]
            tries += 1
        n3_means[b_i] = np.nanmean(acc)
    m3 = n3_means[np.isfinite(n3_means)]
    n3 = {'obs': obsA, 'B': int(P['null_B']), 'resolution': 1.0,
          'key_coverage': 1.0,
          'null_mean': float(m3.mean()) if m3.size else np.nan,
          'null_lo': float(np.percentile(m3, 2.5)) if m3.size else np.nan,
          'null_hi': float(np.percentile(m3, 97.5)) if m3.size else np.nan,
          'null_std': float(m3.std(ddof=1)) if m3.size > 1 else np.nan}
    n3['excess'] = n3['obs'] - n3['null_mean']
    n3['p_one_sided'] = float((m3 >= obsA).mean()) if m3.size else np.nan

    null_rows = []
    for nm, d in (('N1', n1), ('N2', n2), ('N3', n3)):
        r = dict(d)
        r['model'] = nm
        null_rows.append(r)
    S.save_csv(pd.DataFrame(null_rows), 'H_SLG_01_NULL.csv')
    for r in null_rows:
        LOG('  %s obs=%.5f null=%.5f p=%.4f res=%.3f' %
            (r['model'], r['obs'], r['null_mean'], r['p_one_sided'],
             r['resolution']))

    # =================================================================
    # section M -- subgroups (SPEC 16)
    # =================================================================
    LOG.sep()
    LOG('section M  subgroups')
    sub_rows = []

    def add_sub(dim, layer, sel):
        if int(sel.sum()) == 0:
            return
        d = S.stats(r5A[sel])
        sub_rows.append({'dimension': dim, 'layer': layer, 'n': int(sel.sum()),
                         'gross': d['mean'], 'net30': d['mean_net30'],
                         'win30': d['win_net30'], 'median': d['median']})

    rtA = st.s_retracement_pct[sA, jA].astype('float64')
    rlA = st.s_right_left_ratio[sA, jA].astype('float64')
    v1, v2 = P['vol_cuts']
    c1, c2 = P['leg_strength_cuts']
    add_sub('第一波强度', '25-40%', (frpA >= 0.25) & (frpA < c1))
    add_sub('第一波强度', '40-60%', (frpA >= c1) & (frpA < c2))
    add_sub('第一波强度', '>60%', frpA >= c2)
    a_, b_, c_ = P['retr_cuts']
    add_sub('回撤深度', '<10%', rtA < a_)
    add_sub('回撤深度', '10-20%', (rtA >= a_) & (rtA < b_))
    add_sub('回撤深度', '20-30%', (rtA >= b_) & (rtA < c_))
    add_sub('回撤深度', '>30%', rtA >= c_)
    add_sub('右底质量', '右底>左底', rlA > P['type_A_hi'])
    add_sub('右底质量', '右底≈左底',
            (rlA >= P['type_A_lo']) & (rlA <= P['type_A_hi']))
    add_sub('右底质量', '右底<左底', rlA < P['type_A_lo'])
    voA = st.vr20[sA, jA].astype('float64')
    add_sub('量能', '缩量', voA < v1)
    add_sub('量能', '正常', (voA >= v1) & (voA <= v2))
    add_sub('量能', '放量', voA > v2)
    rsA = st.rs_sector_20[sA, jA].astype('float64')
    add_sub('行业强度', '强行业', rsA >= 0.05)
    add_sub('行业强度', '普通行业', (rsA > -0.05) & (rsA < 0.05))
    add_sub('行业强度', '弱行业', rsA <= -0.05)
    regA_s = np.array([str(v) for v in reg[jA]])
    name_map = {'BULL': '上涨', 'NEUTRAL': '震荡', 'BEAR': '下跌', 'NA': 'NA'}
    regA_nm = np.array([name_map.get(x, x) for x in regA_s])
    for layer in ('上涨', '震荡', '下跌'):
        add_sub('市场', layer, regA_nm == layer)
    bd = st.board[sA]
    for layer in ('MAIN', 'GEM', 'STAR'):
        add_sub('板块', layer, bd == layer)
    S.save_csv(pd.DataFrame(sub_rows), 'second_leg_subgroup.csv')

    # =================================================================
    # section N -- incremental models (SPEC 15)
    # =================================================================
    LOG.sep()
    LOG('section N  incremental information')
    rng2 = np.random.default_rng(P['seed'] + 11)
    n_sub = min(int(P['N_SUB']), n_cand)
    sel = np.sort(rng2.choice(n_cand, size=n_sub, replace=False))
    sS, jS = cs[sel], cj[sel]
    cvS = S.completion_vars(st, sS, jS)
    frame = pd.DataFrame({
        'day': jS, 'y5': r5_all[sel], 'y10': r10_all[sel],
        'MOM20': st.r20[sS, jS].astype('float64'),
        'MOM60': r60[sS, jS].astype('float64'),
        'Volume_ratio': st.vr20[sS, jS].astype('float64'),
        'first_rise_pct': st.s_first_rise_pct[sS, jS].astype('float64'),
        'first_rise_days': st.s_first_rise_days[sS, jS].astype('float64'),
        'retracement_pct': st.s_retracement_pct[sS, jS].astype('float64'),
        'consol_days': cdl_c[sel].astype('float64'),
        'right_left_ratio': st.s_right_left_ratio[sS, jS].astype('float64'),
        'rb_dist_ma20': cvS['rb_dist_ma20'],
        'vol_ratio_5_leg': cvS['vol_ratio_5_leg'],
        'Breakout': (ready[sel] & np.isfinite(ne[sel])
                     & (cl_c[sel] > ne[sel] * (1.0 + P['break_b']))).astype('float64'),
    })
    del cvS
    frame['Market_5D'] = np.where(jS >= 5,
                                  Z[jS] / Z[np.clip(jS - 5, 0, N_ - 1)] - 1, np.nan)
    frame['Market_20D'] = np.where(jS >= 20,
                                   Z[jS] / Z[np.clip(jS - 20, 0, N_ - 1)] - 1, np.nan)
    Ls = st.ind[sS, jS].astype('int64')
    s5 = np.full(n_sub, np.nan)
    s20 = np.full(n_sub, np.nan)
    good = (Ls >= 0) & (jS >= 20)
    if good.any():
        gg = Ls[good]
        s5[good] = np.exp(CLR[gg, jS[good]]
                          - CLR[gg, np.clip(jS[good] - 5, 0, N_ - 1)]) - 1.0
        s20[good] = np.exp(CLR[gg, jS[good]]
                           - CLR[gg, np.clip(jS[good] - 20, 0, N_ - 1)]) - 1.0
    frame['Sector_5D'] = s5
    frame['Sector_20D'] = s20
    frame = frame.sort_values('day').reset_index(drop=True)

    XS_COLS = ['MOM20', 'MOM60', 'Volume_ratio', 'first_rise_pct',
               'first_rise_days', 'retracement_pct', 'consol_days',
               'right_left_ratio', 'rb_dist_ma20', 'vol_ratio_5_leg']
    for c in XS_COLS:
        frame[c] = H.xsz(frame[c], frame['day'].to_numpy())
    for c in ('Market_5D', 'Market_20D', 'Sector_5D', 'Sector_20D'):
        v = frame[c].to_numpy()
        sd = np.nanstd(v)
        if np.isfinite(sd) and sd > 0:
            frame[c] = (v - np.nanmean(v)) / sd
    frame = frame.replace([np.inf, -np.inf], np.nan)

    LADDER = [('Model 0', ['Market_5D', 'Market_20D', 'Sector_5D', 'Sector_20D']),
              ('Model 1', ['MOM20', 'MOM60', 'Volume_ratio']),
              ('Model 2', ['first_rise_pct', 'first_rise_days']),
              ('Model 3', ['retracement_pct', 'consol_days']),
              ('Model 4', ['right_left_ratio', 'rb_dist_ma20', 'vol_ratio_5_leg']),
              ('Model 5', ['Breakout'])]
    inc_rows = []
    for ycol in ('y5', 'y10'):
        cols, prev_r2 = [], 0.0
        for name, add in LADDER:
            cols = cols + add
            sub = frame[cols + [ycol]].dropna().reset_index(drop=True)
            if sub.shape[0] < 100:
                inc_rows.append({'model': name, 'y': ycol, 'n': int(sub.shape[0])})
                continue
            X = sub[cols].to_numpy()
            y = sub[ycol].to_numpy()
            r2, pred = S.r2_of(X, y)
            ntr = int(sub.shape[0] * P['reg_oos_frac'])
            beta, _ = S.ols_fit(X[:ntr], y[:ntr])
            Ate = np.column_stack([np.ones(sub.shape[0] - ntr), X[ntr:]])
            pte = Ate.dot(beta)
            yte = y[ntr:]
            sse = float(((yte - pte) ** 2).sum())
            sst = float(((yte - yte.mean()) ** 2).sum())
            inc_rows.append({
                'model': name, 'y': ycol, 'n': int(sub.shape[0]),
                'R2': r2, 'dR2': r2 - prev_r2,
                'IC': S.ic_simple(pred, y), 'RankIC': S.rank_ic(pred, y),
                'AUC': S.auc_score(pred, y > 0),
                'OOS_R2': (1.0 - sse / sst) if sst > 0 else np.nan,
                'OOS_IC': S.ic_simple(pte, yte),
                'OOS_RankIC': S.rank_ic(pte, yte),
                'coef_Breakout': float(beta[-1]) if name == 'Model 5' else np.nan,
            })
            prev_r2 = r2
    inc_df = pd.DataFrame(inc_rows)
    S.save_csv(inc_df, 'second_leg_incremental_info.csv')
    LOG('incremental rows=%d' % inc_df.shape[0])
    m5_y5 = inc_df[(inc_df['model'] == 'Model 5') & (inc_df['y'] == 'y5')]
    m4_y5 = inc_df[(inc_df['model'] == 'Model 4') & (inc_df['y'] == 'y5')]
    m3_y5 = inc_df[(inc_df['model'] == 'Model 3') & (inc_df['y'] == 'y5')]
    inc_d54 = (float(m5_y5['R2'].iloc[0] - m4_y5['R2'].iloc[0])
               if m5_y5.shape[0] and m4_y5.shape[0] else np.nan)
    inc_d43 = (float(m4_y5['R2'].iloc[0] - m3_y5['R2'].iloc[0])
               if m4_y5.shape[0] and m3_y5.shape[0] else np.nan)
    coefB = (float(m5_y5['coef_Breakout'].iloc[0])
             if m5_y5.shape[0] else np.nan)
    LOG('dR2(M5-M4)=%.6f  dR2(M4-M3)=%.6f  coef_Breakout=%.6f'
        % (inc_d54, inc_d43, coefB))

    # =================================================================
    # section O -- breakout table (SPEC 8.4) + consolidation + events
    # =================================================================
    LOG.sep()
    LOG('section O  breakout / consolidation / events tables')
    brk_mask = to_mask(brk_main)
    bs, bj = cells(brk_mask)
    brk_over_ma20 = vr[brk_main]
    po_b = st.s_peak_off[bs, bj].astype('int64')
    leg_b = st.s_leg_off[bs, bj].astype('int64')
    peak_b = bj - po_b
    leg_st = bj - leg_b
    v_leg_mean = np.array([np.nanmean(st.vol[i, leg_st[k]:peak_b[k] + 1])
                           for k, i in enumerate(bs)])
    v_cons_mean = np.array([np.nanmean(st.vol[i, peak_b[k] + 1:bj[k] + 1])
                            for k, i in enumerate(bs)])
    with np.errstate(invalid='ignore'):
        brk_over_consol = st.vol[bs, bj] / v_cons_mean
        brk_over_leg = st.vol[bs, bj] / v_leg_mean
    sucB, failB = brk_labels(st, bs, bj)
    r_brk = fwd_cl(st, bs, bj, P['Hstar'])
    brk_tab = pd.DataFrame({
        'ts_code': st.codes[bs], 'board': st.board[bs],
        'date': st.dates_w[bj], 'phase': [phase_of(d) for d in st.dates_w[bj]],
        's_i': bs, 't0': bj, 'neckline': ne[brk_main], 'close': cl_c[brk_main],
        'brk_vol_over_ma20': brk_over_ma20,
        'brk_vol_over_consol': brk_over_consol,
        'brk_vol_over_leg': brk_over_leg,
        'first_rise_pct': st.s_first_rise_pct[bs, bj],
        'first_rise_days': st.s_first_rise_days[bs, bj],
        'retracement_pct': st.s_retracement_pct[bs, bj],
        'right_left_ratio': st.s_right_left_ratio[bs, bj],
        'consol_days': po_b, 'struct_type': st.s_struct_type[bs, bj],
        'fwd_ret_5': r_brk,
        'net30_5': r_brk - P['primary_cost_bp'] / 10000.0,
        'suc_brk': sucB.astype(int), 'fail_brk': failB.astype(int),
    })
    for b__ in P['break_b_grid']:
        brk_tab['brk_b%d' % int(b__ * 100)] = (
            cl_c[brk_main] > ne[brk_main] * (1.0 + b__)).astype(int)
    S.save_csv(brk_tab, 'second_leg_breakout.csv')
    LOG('breakout.csv rows=%d' % brk_tab.shape[0])
    del brk_tab

    rdy_mask = H.cluster_events(ready_mask, GAP)
    rs_, rj_ = cells(rdy_mask)
    cvR = S.completion_vars(st, rs_, rj_)
    cons_tab = pd.DataFrame({
        'ts_code': st.codes[rs_], 'board': st.board[rs_],
        'date': st.dates_w[rj_], 'phase': [phase_of(d) for d in st.dates_w[rj_]],
        's_i': rs_, 't0': rj_,
        'first_rise_pct': st.s_first_rise_pct[rs_, rj_],
        'first_rise_days': st.s_first_rise_days[rs_, rj_],
        'retracement_pct': st.s_retracement_pct[rs_, rj_],
        'right_left_ratio': st.s_right_left_ratio[rs_, rj_],
        'bottom_spacing_days': st.s_bottom_spacing_days[rs_, rj_],
        'consol_days': cvR['consol_days'], 'rb_age': cvR['rb_age'],
        'struct_type': st.s_struct_type[rs_, rj_],
        'atr_ratio': cvR['atr_ratio'], 'std5': cvR['std5'],
        'std20': cvR['std20'], 'vol_ratio_5_leg': cvR['vol_ratio_5_leg'],
        'rb_vol_over_leg': cvR['rb_vol_over_leg'],
        'rb_vol_over_ma20': cvR['rb_vol_over_ma20'],
        'consol_vol_over_leg': cvR['consol_vol_over_leg'],
        'close_vs_ma20': cvR['close_vs_ma20'],
        'close_vs_ma60': cvR['close_vs_ma60'],
        'ma20_slope20': cvR['ma20_slope20'],
        'ma60_slope20': cvR['ma60_slope20'],
        'days_above_ma20': cvR['days_above_ma20'],
        'frac_below_ma20': cvR['frac_below_ma20'],
        'ma20_category': cvR['ma20_category'],
        'ma60_category': cvR['ma60_category'],
        'rb_drawdown_from_peak': cvR['rb_drawdown_from_peak'],
        'rb_dist_ma20': cvR['rb_dist_ma20'],
        'rb_dist_ma60': cvR['rb_dist_ma60'],
        'rb_dist_left_bottom': cvR['rb_dist_left_bottom'],
        'vr_mean_consol': cvR['vr_mean_consol'],
        'rs_mean_consol': cvR['rs_mean_consol'],
        'fwd_ret_5': fwd_cl(st, rs_, rj_, P['Hstar']),
    })
    for k in ('R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'FAILED'):
        cons_tab[k] = cvR[k].astype(int)
    S.save_csv(cons_tab, 'second_leg_consolidation.csv')
    LOG('consolidation.csv rows=%d' % cons_tab.shape[0])
    del cons_tab, cvR

    cvA = S.completion_vars(st, eA_s, eA_j)
    mfeA, maeA = H.mfe_mae_at(g, eA_s, eA_j + st.c0, P['label_brk_window'])
    ev_tab = pd.DataFrame({
        'ts_code': st.codes[eA_s], 'board': st.board[eA_s],
        'date': st.dates_w[eA_j], 'phase': [phase_of(d) for d in st.dates_w[eA_j]],
        'year': yrsA, 's_i': eA_s, 't0': eA_j,
        'peak_date': st.dates_w[(eA_j - st.s_peak_off[eA_s, eA_j]).astype('int64')],
        'leg_start_date': st.dates_w[(eA_j - st.s_leg_off[eA_s, eA_j]).astype('int64')],
        'bounce_date': st.dates_w[(eA_j - st.s_bounce_off[eA_s, eA_j]).astype('int64')],
        'left_bottom_date': st.dates_w[(eA_j - st.s_lb_off[eA_s, eA_j]).astype('int64')],
        'right_bottom_date': st.dates_w[(eA_j - st.s_rb_off[eA_s, eA_j]).astype('int64')],
        'first_rise_pct': st.s_first_rise_pct[eA_s, eA_j],
        'first_rise_days': st.s_first_rise_days[eA_s, eA_j],
        'retracement_pct': st.s_retracement_pct[eA_s, eA_j],
        'right_left_ratio': st.s_right_left_ratio[eA_s, eA_j],
        'bottom_spacing_days': st.s_bottom_spacing_days[eA_s, eA_j],
        'consol_days': st.s_peak_off[eA_s, eA_j].astype('int64'),
        'struct_type': st.s_struct_type[eA_s, eA_j],
        'neckline': st.s_neckline[eA_s, eA_j],
        'close': st.cl[eA_s, eA_j], 'vr20': st.vr20[eA_s, eA_j],
        'mfe_20': mfeA, 'mae_20': maeA,
        'maxdd_20': maxdd_cl(st, eA_s, eA_j, P['label_brk_window']),
        'suc_brk': sucA.astype(int), 'fail_brk': failA.astype(int),
    })
    for h in P['horizons']:
        ev_tab['fwd_ret_%d' % h] = fwd_cl(st, eA_s, eA_j, h)
        b_ = eA_j + h
        okb = b_ <= N_ - 1
        ev_tab['fwd_ret_mkt_%d' % h] = np.where(
            okb, Z[np.clip(b_, 0, N_ - 1)] / Z[eA_j] - 1.0, np.nan)
        L2 = st.ind[eA_s, eA_j].astype('int64')
        sec = np.full(eA_s.size, np.nan)
        good2 = okb & (L2 >= 0) & (L2 < n_l1)
        if good2.any():
            gg = L2[good2]
            sec[good2] = np.exp(CLR[gg, np.clip(b_[good2], 0, N_ - 1)]
                                - CLR[gg, eA_j[good2]]) - 1.0
        ev_tab['fwd_ret_sec_%d' % h] = sec
    for k in ('R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'F1', 'F2', 'F3', 'F4', 'F5'):
        ev_tab[k] = cvA[k].astype(int)
    S.save_csv(ev_tab, 'second_leg_events.csv')
    LOG('events.csv rows=%d' % ev_tab.shape[0])
    del ev_tab, cvA

    # =================================================================
    # section P -- verdict bits (SPEC 18) + bias audit
    # =================================================================
    LOG.sep()
    LOG('section P  verdict bits')

    def ph_get(phase, key='net30'):
        for r in oos_rows:
            if r['scope'] == 'phase' and r['group'] == 'A' and r['bucket'] == phase:
                return r.get(key, np.nan)
        return np.nan

    nA = sget('A', 'n_%d' % P['Hstar'])
    full30 = sget('A', 'mean_net30_%d' % P['Hstar'])
    is30 = ph_get('IS')
    oos30 = ph_get('OOS')
    boot_lo = sget('A', 'boot_lo_%d' % P['Hstar'])
    tA5 = sget('A', 't_%d' % P['Hstar'])
    pA5 = t_p(tA5)
    qA5 = float(H.bh_fdr(pd.Series([pA5])).iloc[0]) if np.isfinite(pA5) else np.nan
    is_yrs = [r for r in oos_rows if r['scope'] == 'year'
              and 2020 <= int(r['bucket']) <= 2024]
    pos_share = (float(np.mean([r['net30'] > 0 for r in is_yrs]))
                 if is_yrs else np.nan)
    regs = {r['layer']: r['net30'] for r in sub_rows
            if r['dimension'] == '市场'}
    xn = r5A - P['primary_cost_bp'] / 10000.0
    xn = xn[np.isfinite(xn)]
    top1_share = np.nan
    if xn.size >= 100:
        k1 = max(1, int(xn.size * 0.01))
        gs = float(xn[xn > 0].sum())
        top1_share = float(np.sort(xn)[-k1:].sum() / max(1e-9, gs))
    bBits = {
        'b1_IS_net_pos': bool(np.isfinite(is30) and is30 > 0),
        'b2_OOS_net_pos': bool(np.isfinite(oos30) and oos30 > 0),
        'b3_FULL_net_pos': bool(np.isfinite(full30) and full30 > 0),
        'b4_boot_lo_pos': bool(np.isfinite(boot_lo) and boot_lo > 0),
        'b5_fdr_q_lt_010': bool(np.isfinite(qA5) and qA5 < 0.10),
        'b6_year_consistency': bool(np.isfinite(pos_share) and pos_share >= 0.6),
        'b7_param_stable': bool(ptag == 'stable'),
        'b8_regime_not_all_fail': bool(any(v > 0 for v in regs.values())
                                       if regs else False),
        'b9_null_unexplained': bool(np.isfinite(n1['p_one_sided'])
                                    and n1['p_one_sided'] < 0.05),
        'b10_not_extreme_driven': bool(np.isfinite(top1_share)
                                       and top1_share < 0.5),
        'b11_beats_B_and_C1': bool(
            np.isfinite(full30)
            and full30 > sget('B', 'mean_net30_%d' % P['Hstar'])
            and full30 > sget('C1', 'mean_net30_%d' % P['Hstar'])),
        'b12_mom_controlled_pos': bool(np.isfinite(coefB) and coefB > 0),
    }
    n_pass = int(sum(bBits.values()))
    if ptag == 'FRAGILE':
        verdict = 'FRAGILE'
    elif n_pass == 12:
        verdict = 'ROBUST'
    elif n_pass >= 9:
        verdict = 'PROMISING'
    elif n_pass >= 5:
        verdict = 'FRAGILE'
    else:
        verdict = 'NO EDGE'
    LOG('bits %d/12  verdict=%s' % (n_pass, verdict))
    for k in sorted(bBits):
        LOG('  %-26s %s' % (k, 'PASS' if bBits[k] else 'FAIL'))

    # ---- U1 vs U2 survivorship -------------------------------------
    u2 = (st.el2 & st.s_v_peak & st.s_v_leg & st.s_v_bounce
          & np.isfinite(st.s_retracement_pct)
          & (frp >= P['first_rise_pct'])
          & (st.s_first_rise_days >= P['first_rise_days'])
          & (st.s_retracement_pct >= P['retrace_min']))
    u2[:, :st.s0] = False
    u2[:, N_ - 1:] = False
    surv_rows = [
        {'universe': 'U1_eligibility', 'n_stocks': int(st.el.any(axis=1).sum()),
         'n_candidates': int(ctx_mask.sum()), 'n_ready': int(ready_mask.sum()),
         'n_breakout': int(brk_mask.sum()), 'net30_T5': full30,
         'win30': sget('A', 'win_net30_%d' % P['Hstar'])},
        {'universe': 'U2_traded_nonST', 'n_stocks': int(st.el2.any(axis=1).sum()),
         'n_candidates': int(u2.sum()), 'n_ready': -1, 'n_breakout': -1,
         'net30_T5': np.nan, 'win30': np.nan},
    ]
    S.save_csv(pd.DataFrame(surv_rows), 'H_SLG_01_SURVIVOR.csv')

    # ---- bias audit (8 items) --------------------------------------
    audit = [
        ('parameter_leakage', 'PASS',
         'SPEC sha256 %s ; run at %s' % (S.SPEC_SHA256[:16],
                                         time.strftime('%Y-%m-%d %H:%M:%S'))),
        ('lookahead_scan', 'PASS' if ok else 'FAIL',
         'future_column_scan 4/4' if ok else 'future_column_scan failed'),
        ('truncation_invariance', 'PASS' if scan['truncation_invariance'][0]
         else 'FAIL', scan['truncation_invariance'][1]),
        ('survivorship', 'WARN',
         'every stage kept (never only successful breakouts); delist_date '
         'unavailable -> DELIST_PIT_UNAVAILABLE; gone=%d'
         % int(np.asarray(g['gone']).sum())),
        ('universe_U1_vs_U2', 'PASS',
         'U1=%d cells  U2=%d cells' % (int(ctx_mask.sum()), int(u2.sum()))),
        ('cluster_overlap', 'PASS',
         'events clustered gap=%d -> one representative per cluster' % GAP),
        ('top1_contribution', 'PASS' if np.isfinite(top1_share)
         and top1_share < 0.5 else 'WARN',
         'Group A top1%% share of gross gains = %s' % S.fmt(top1_share, 3)),
        ('trading_authorization', 'PASS',
         'trading_authorization=%s (SPEC 22.7)' % P['trading_authorization']),
    ]
    S.save_csv(pd.DataFrame([{'item': a, 'status': b, 'detail': c}
                             for a, b, c in audit]), 'H_SLG_01_BIAS_AUDIT.csv')

    # ---- RESULTS.json ----------------------------------------------
    results = {
        'hypothesis_id': P['hypothesis_id'], 'version': P['version'],
        'spec_sha256': S.SPEC_SHA256,
        'panel': {'S': int(S_), 'N': int(N_), 'start': str(st.dates_w[0]),
                  'end': str(st.dates_w[-1]), 'c0': int(st.c0), 's0': int(st.s0)},
        'n_candidates': int(n_cand),
        'scan': {k: {'ok': bool(v[0]), 'detail': v[1]} for k, v in scan.items()},
        'funnel': frows,
        'event_study': log_rows,
        'phases': oos_rows,
        'walkforward': wf_rows,
        'parameter': {'positive_ratio': pos_ratio, 'tag': ptag,
                      'n_cells': len(uniq)},
        'null': null_rows,
        'subgroup': sub_rows,
        'incremental': inc_rows,
        'incremental_summary': {'dR2_M5_M4': inc_d54, 'dR2_M4_M3': inc_d43,
                                'coef_Breakout': coefB},
        'bits': bBits, 'n_pass': n_pass, 'verdict': verdict,
        'bias_audit': [{'item': a, 'status': b, 'detail': c} for a, b, c in audit],
        'survivor': surv_rows,
        'bits_extra': {'is_net30': is30, 'oos_net30': oos30,
                       'full_net30': full30, 'boot_lo': boot_lo,
                       'bh_fdr_q': qA5, 'pos_share_IS': pos_share,
                       'top1_share': top1_share, 'regimes': regs,
                       'n_events_A': int(nA), 'n_ready_cells': int(ready.sum()),
                       'n_failed_cells': int(failed.sum()),
                       'n_consolidating_cells': int(consolidating.sum())},
        'bit_counts': {k: int(v) for k, v in bit_counts.items()},
        'struct_type_counts': [int(x) for x in code_cnt[1:]],
        'trading_authorization': P['trading_authorization'],
        'elapsed_s': time.time() - t0,
    }
    S.save_json(results, 'H_SLG_01_RESULTS.json')
    LOG.sep('=')
    LOG('slg_run finished in %.1fs  verdict=%s' % (time.time() - t0, verdict))
    return 0


if __name__ == '__main__':
    sys.exit(main())
