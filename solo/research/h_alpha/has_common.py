# -*- coding: utf-8 -*-
"""H-ALPHA-SOURCE-01 common layer.

Research ID : H-ALPHA-SOURCE-01
Title       : HVT / W7 alpha source attribution
Question    : where did the historical HVT-BULL / W7 returns come from?
              Candidate selection / Event selection / Holding delay / Entry timing.

Design (frozen in H_ALPHA_SOURCE_01_SPEC.md before any result was seen):

  * The object under study is the FROZEN production ledger of each system.
    Nothing in hvt_bull/, w7_*.py, trade_execution_engine.py or the shared
    cache is modified, re-run or re-parameterised.
      HVT : report_daily/te_backtest_events_20250101_20260828.csv
      W7  : report_daily/te3_v31_events_20240101_20260828.csv
  * All returns are RECOMPUTED on the shared qfq research panel so that the
    observed leg and every counterfactual leg share one price basis:
      entry = open[t_decision + 1],  exit = close[entry + h]
  * Matching / counterfactual machinery is reused from the project's mature
    frameworks (hef_common PIT industry, hve_common panel + eligibility,
    hrbp_common block/cluster bootstrap, hrbp_null strata sampler).
"""
import os
import sys
import json
import time
import warnings

import numpy as np
import pandas as pd

np.seterr(divide='ignore', invalid='ignore')
warnings.filterwarnings('ignore')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
HVE_DIR = os.path.join(ROOT, 'research', 'hve')
HRBP_DIR = os.path.join(ROOT, 'research', 'h_rbp')
HEF_DIR = os.path.join(ROOT, 'research', 'h_earn_fwd')
for _p in (HVE_DIR, HRBP_DIR, HEF_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hve_common as H                                          # noqa: E402
from hef_common import load_industry_map, industry_map_fast     # noqa: E402
from hrbp_common import (cluster_boot_diff, tail_table, block_boot_mean,  # noqa: E402,E501
                         regime_by_day, fmt, fmtp)

OUT = HERE
DATA = os.path.join(OUT, 'data')
os.makedirs(DATA, exist_ok=True)
REPORT_DAILY = os.path.join(ROOT, 'report_daily')

HVT_LEDGER = 'te_backtest_events_20250101_20260828.csv'
W7_LEDGER = 'te3_v31_events_20240101_20260828.csv'

HVT_SUMMARY = 'te_backtest_20250101_20260828_summary.json'
W7_SUMMARYS = ('te3_v31_20240101_20260828_summary.json',
               'te3_v31_events_20240101_20260828_summary.json')

# =====================================================================
# PREREG_A -- frozen before any H-ALPHA-SOURCE-01 result was observed
# =====================================================================
PREREG_A = {
    'hypothesis_id': 'H-ALPHA-SOURCE-01',
    'version': '1.0',
    'created': '2026-09-30',
    'title': 'HVT / W7 alpha source attribution',

    # ---- frozen objects ----------------------------------------------
    'systems': ('HVT', 'W7'),
    'hvt_ledger': HVT_LEDGER,
    'w7_ledger': W7_LEDGER,
    'hvt_action_field': 'next_day_action',
    'hvt_actions': ('BUY', 'BUY_ON_CONFIRM'),
    'hvt_event_field': 'signal_date',        # T0 天量日 (HVT anchor)
    'hvt_decision_field': 'decision_date',
    'hvt_lag_field': 'decision_lag',
    'hvt_top_field': 'top3_proxy',           # see hvt_top_flag()
    'w7_action_field': 'action',
    'w7_actions': ('PRIMARY BUY', 'CONDITIONAL BUY'),
    'w7_event_field': 'event_date',          # EXTREME_CHURN 双>=P99 日
    'w7_decision_field': 'signal_date',
    'w7_top_field': 'top3_proxy',

    # ---- entry / exit -------------------------------------------------
    'entry_rule': 'open[t_decision + 1]   (production convention, verified: '
                  'actual_entry == raw open[decision_date+1] in 7396/7396)',
    'exit_rule': 'close[entry + h] / open[entry] - 1, no stop',
    'horizons': (3, 5, 10, 20),
    'primary_horizon': 10,
    'note_exit': 'The production risk overlay (structural stop / right-tail '
                 'DD / double stop) is NOT part of this experiment: the study '
                 'attributes the SIGNAL, not the money-management layer.',

    # ---- delay ladder, frozen before any result ----------------------
    'delays': (0, 1, 3, 5, 10),

    # ---- overlap control ---------------------------------------------
    'overlap_guard_sessions': 20,   # == max horizon; one obs per lifecycle

    # ---- universe (panel side, applied identically to every arm) ------
    'min_list_days': 250,
    'exclude_st': True,
    'exclude_delist': True,
    'exclude_bse': True,
    'long_suspension_days': 60,

    # ---- matching ----------------------------------------------------
    'mv_quantiles': 3,
    'liq_quantiles': 3,
    'match_level1_keys': ('trade_date', 'board', 'mv_bucket', 'liq_bucket'),
    'match_level1_industry': 'same SW L1 preferred, coarsened cell when empty',
    'match_level2_keys': ('same stock', 'same calendar month'),
    'match_excl_win': 5,
    'match_tries': 20,

    # ---- costs -------------------------------------------------------
    'cost_bp': (0, 10, 20, 30, 50),
    'primary_cost_bp': 30,
    'cost_charge': 'observed leg only -- the counterfactual is a non-traded '
                   'benchmark, so charging it would make the increment '
                   'cost-invariant by construction.  Every level uses the '
                   'same 30bp rule.',

    # ---- statistics --------------------------------------------------
    'n_perm': 1000,
    'boot_B': 1000,
    'seed': 20261101,
    'tail_levels': (0.01, 0.05, 0.10),
    'min_n': 30,
    'regime_index': '000300.SH',

    # ---- phases (driven by what the frozen ledgers actually cover) ----
    'phase_IS': (2024, 2024),
    'phase_VALID': (2025, 2025),
    'phase_OOS': (2026, 2026),
    'wf_train_years': 1,
    'wf_valid_years': 1,
    'wf_test_years': 1,

    'trading_authorization': 'NO',
}


def phase_of_year(y):
    for nm in ('IS', 'VALID', 'OOS'):
        a, b = PREREG_A['phase_%s' % nm]
        if a <= y <= b:
            return nm
    return 'OUT'


# =====================================================================
# logging / io
# =====================================================================
class Log(object):
    def __init__(self, name):
        self.name = name
        self.path = os.path.join(OUT, '%s.log' % name)
        self.t0 = time.time()
        with open(self.path, 'w', encoding='utf-8'):
            pass

    def __call__(self, msg):
        line = '[%7.1fs] %s' % (time.time() - self.t0, msg)
        print(line)
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')

    def sep(self, ch='-', n=76):
        self(ch * n)


def save_csv(df, name, to_data=True):
    p = os.path.join(DATA, name) if to_data else os.path.join(OUT, name)
    df.to_csv(p, index=False, encoding='utf-8-sig')
    return p


def save_json(obj, name, to_data=True):
    p = os.path.join(DATA, name) if to_data else os.path.join(OUT, name)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)
    return p


# =====================================================================
# panel
# =====================================================================
_GRID = None


def grid(lg=None, use_cache=True):
    global _GRID
    if _GRID is None:
        _GRID = H.build_grid(lg=lg, use_cache=use_cache)
        if 'first_idx' not in _GRID:
            tr = _GRID['traded']
            f = np.full(tr.shape[0], tr.shape[1] + 1, dtype='int64')
            any_ = tr.any(axis=1)
            f[any_] = np.argmax(tr[any_], axis=1)
            _GRID['first_idx'] = f.astype('int32')
    return _GRID


def eligibility(lg=None):
    """(S,N) bool -- the common tradable universe for every arm.

    hve_common.eligibility (traded / non-ST / non-delisting / aged /
    no long suspension hole) AND the BSE exclusion.  Identical for the
    observed leg, the matched random stock and the random date legs.
    """
    g = grid(lg=lg)
    el = H.eligibility(g) & (g['board'] != 'BSE')[:, None]
    if PREREG_A['exclude_bse']:
        pass
    if lg:
        lg('eligibility cells %.4f  (S=%d N=%d)' % (float(el.mean()),
                                                    el.shape[0], el.shape[1]))
    return el


def _scatter(g, vals, si, di):
    M = np.full(g['close'].shape, np.nan, dtype='float32')
    M[si, di] = vals
    return M


def mv_panel(g, lg=None):
    """(S,N) float32 total_mv (万元) from the shared basic panel."""
    b = pd.read_parquet(
        os.path.join(H.FS_DATA, 'basic_panel.parquet'),
        columns=['ts_code', 'trade_date', 'total_mv'])
    b['ts_code'] = b['ts_code'].astype(str)
    b['trade_date'] = b['trade_date'].astype(str)
    s2i, d2i = g['s2i'], g['d2i']
    b = b[b['ts_code'].isin(s2i) & b['trade_date'].isin(d2i)]
    si = b['ts_code'].map(s2i).to_numpy()
    di = b['trade_date'].map(d2i).to_numpy().astype('int64')
    out = _scatter(g, pd.to_numeric(b['total_mv'], errors='coerce')
                   .to_numpy('float64'), si, di)
    if lg:
        lg('mv panel cells %.4f' % float(np.isfinite(out).mean()))
    return out


def liq_panel(g, lg=None):
    """(S,N) float32 -- 20-session trailing mean turnover value (amount)."""
    out = H._roll_mean(g['amount'], 20).astype('float32')
    if lg:
        lg('liq panel (amount MA20) cells %.4f' % float(np.isfinite(out).mean()))
    return out


def industry_panel(g, lg=None):
    """(S,N) int32 SW-L1 id (PIT), -1 when unknown.  ids map name->id."""
    im = load_industry_map()
    fast = industry_map_fast(im)
    S, N = g['close'].shape
    dates = g['dates']
    ids, out = {}, np.full((S, N), -1, dtype='int32')
    for s, code in enumerate(g['codes']):
        lst = fast.get(code)
        if not lst:
            continue
        for a, b, l1, _l2 in lst:
            i0 = int(np.searchsorted(dates, a, side='left'))
            i1 = int(np.searchsorted(dates, b, side='right')) - 1
            if i1 < i0:
                continue
            if l1 not in ids:
                ids[l1] = len(ids)
            out[s, i0:i1 + 1] = ids[l1]
    if lg:
        lg('industry panel  known cells %.4f  n_l1 %d'
           % (float((out >= 0).mean()), len(ids)))
    return out, ids


def tercile_ids(el, val, nq=3, lg=None, tag=''):
    """(S,N) int8 cross-sectional bucket id in [0,nq), -1 outside."""
    S, N = el.shape
    out = np.full((S, N), -1, dtype='int8')
    cuts = np.linspace(0.0, 1.0, nq + 1)[1:-1]
    for d in range(N):
        col = val[:, d]
        m = el[:, d] & np.isfinite(col)
        v = col[m]
        if v.size < nq * 5:
            continue
        qs = np.quantile(v, cuts)
        out[m, d] = np.searchsorted(qs, v, side='right').astype('int8')
    if lg:
        lg('bucket %-4s cells %.4f' % (tag, float((out >= 0).mean())))
    return out


# =====================================================================
# ledgers -> normalised observation tables
# =====================================================================
def _date_int(s):
    return pd.to_numeric(s, errors='coerce').astype('Int64')


def hvt_top_flag(d):
    """The ledger's own daily Top-N proxy (te_backtest.py L417-425).

    max_buy_candidates = 3; ranked by execution_score then buyability
    inside each decision_date.  This reproduces the script's rule rather
    than trusting a flag that is not written to the CSV.
    """
    es = pd.to_numeric(d['execution_score'], errors='coerce').fillna(-1.0)
    bb = pd.to_numeric(d['buyability'], errors='coerce').fillna(-1.0)
    t = pd.DataFrame({'dd': d['decision_date'], 'es': es, 'bb': bb})
    t = t.sort_values(['dd', 'es', 'bb'], ascending=[True, False, False])
    keep = t.groupby('dd').head(3).index
    return d.index.isin(keep)


def load_hvt(lg=None):
    """Frozen HVT-BULL own-trade set, deduplicated to one obs per event."""
    p = os.path.join(REPORT_DAILY, HVT_LEDGER)
    d = pd.read_csv(p, low_memory=False)
    n0 = len(d)
    d = d[d['next_day_action'].isin(PREREG_A['hvt_actions'])].copy()
    if lg:
        lg('HVT ledger %d rows -> actionable %d' % (n0, len(d)))
    d['ts_code'] = d['ts_code'].astype(str)
    d['ev_date'] = _date_int(d['signal_date'])
    d['dec_date'] = _date_int(d['decision_date'])
    d['lag'] = pd.to_numeric(d['decision_lag'], errors='coerce')
    d['top3'] = hvt_top_flag(d)
    d['primary_only'] = d['next_day_action'] == 'BUY_ON_CONFIRM'
    return _finalise(d, 'HVT', lg)


def load_w7(lg=None):
    """Frozen W7 (trade_execution V3.1) own-trade set, one obs per event."""
    p = os.path.join(REPORT_DAILY, W7_LEDGER)
    d = pd.read_csv(p, low_memory=False)
    n0 = len(d)
    d = d[d['action'].isin(PREREG_A['w7_actions'])].copy()
    if lg:
        lg('W7 ledger %d rows -> actionable %d' % (n0, len(d)))
    d['ts_code'] = d['code'].astype(str)
    d['ev_date'] = _date_int(d['event_date'])
    d['dec_date'] = _date_int(d['signal_date'])
    d['lag'] = np.nan
    d['exec'] = pd.to_numeric(d['exec'], errors='coerce').fillna(-1.0)
    d = d.sort_values(['ts_code', 'dec_date', 'ev_date', 'exec'],
                      ascending=[True, True, True, False])
    d['top3'] = True
    d['primary_only'] = d['action'] == 'PRIMARY BUY'
    return _finalise(d, 'W7', lg)


def _finalise(d, system, lg=None):
    """Attach panel indices, dedup to one observation per event lifecycle."""
    g = grid()
    d2i, s2i = g['d2i'], g['s2i']
    N = len(g['dates'])
    d = d[d['ts_code'].isin(s2i)].copy()
    d['s_i'] = d['ts_code'].map(s2i)
    d['ev_idx'] = d['ev_date'].astype('str').map(d2i)
    d['dec_idx'] = d['dec_date'].astype('str').map(d2i)
    d = d[d['ev_idx'].notna() & d['dec_idx'].notna()].copy()
    d['s_i'] = d['s_i'].astype('int64')
    d['ev_idx'] = d['ev_idx'].astype('int64')
    d['dec_idx'] = d['dec_idx'].astype('int64')
    d['entry_idx'] = d['dec_idx'] + 1
    d = d[(d['entry_idx'] >= 0) & (d['entry_idx'] <= N - 2)]

    # --- overlap control: one observation per event lifecycle ---------
    d = d.sort_values(['s_i', 'ev_date', 'dec_date'])
    d = d.drop_duplicates(['s_i', 'ev_date'], keep='first')
    n_ev = len(d)
    keep = []
    last = {}
    for r in d.itertuples():
        lp = last.get(r.s_i)
        if lp is None or (r.entry_idx - lp) > PREREG_A['overlap_guard_sessions']:
            keep.append(r.Index)
            last[r.s_i] = r.entry_idx
    d = d.loc[keep].copy()
    d['system'] = system
    d['month'] = [int(g['dates'][i][:6]) for i in d['entry_idx']]
    d['year'] = [int(g['dates'][i][:4]) for i in d['entry_idx']]
    d['phase'] = d['year'].map(phase_of_year)
    if lg:
        lg('%s: %d obs after dedup(%d) + overlap guard(%d sessions)'
           % (system, len(d), n_ev, PREREG_A['overlap_guard_sessions']))
        lg('  by year   %s' % d['year'].value_counts().sort_index().to_dict())
        lg('  top3 flag %d / %d' % (int(d['top3'].sum()), len(d)))
    cols = ['system', 'ts_code', 's_i', 'ev_idx', 'dec_idx', 'entry_idx',
            'ev_date', 'dec_date', 'lag', 'top3', 'primary_only',
            'month', 'year', 'phase']
    return d[cols].reset_index(drop=True)


def load_obs(lg=None):
    a, b = load_hvt(lg), load_w7(lg)
    return pd.concat([a, b], ignore_index=True)


# =====================================================================
# returns
# =====================================================================
def ret_open(g, si, ei, h):
    """close[ei + h] / open[ei] - 1  (NaN outside the panel)."""
    N = g['close'].shape[1]
    si = np.asarray(si, dtype='int64')
    ei = np.asarray(ei, dtype='int64')
    j = ei + int(h)
    ok = (ei >= 0) & (ei <= N - 1) & (j >= 0) & (j <= N - 1)
    a = np.clip(ei, 0, N - 1)
    b = np.clip(j, 0, N - 1)
    p0 = g['open'][si, a].astype('float64')
    p1 = g['close'][si, b].astype('float64')
    r = np.where((p0 > 0) & np.isfinite(p1) & (p1 > 0), p1 / p0 - 1.0, np.nan)
    return np.where(ok, r, np.nan)


def ret_close_obs(g, si, ei, h):
    """Diagnostic only: the ledger's own convention (decision-day close
    basis), kept so the textbook baseline can be reported side by side."""
    N = g['close'].shape[1]
    si = np.asarray(si, dtype='int64')
    ei = np.asarray(ei, dtype='int64')
    j = ei + int(h)
    ok = (ei >= 0) & (ei <= N - 1) & (j >= 0) & (j <= N - 1)
    a = np.clip(ei, 0, N - 1)
    b = np.clip(j, 0, N - 1)
    p0 = g['close'][si, a].astype('float64')
    p1 = g['close'][si, b].astype('float64')
    r = np.where((p0 > 0) & np.isfinite(p1) & (p1 > 0), p1 / p0 - 1.0, np.nan)
    return np.where(ok, r, np.nan)


def cost_net(r, bp=None):
    bp = PREREG_A['primary_cost_bp'] if bp is None else bp
    return np.asarray(r, 'float64') - bp / 10000.0


# =====================================================================
# stratified matched sampling
# =====================================================================
BOARD_ID = {'MAIN': 0, 'GEM': 1, 'STAR': 2, 'BSE': 3}


def strata_index(el, mv_b, liq_b, ind, board_id, lg=None):
    """Sorted coarsened-exact matching index.

    cell key = ((day * 4 + board) * 3 + mv_bucket) * 3 + liq_bucket.
    Industry is carried as a member attribute so the caller can prefer a
    same-SW-L1 control inside the cell, and fall back to the whole cell
    only when that subset is empty (recorded, never silently dropped).
    """
    S, N = el.shape
    day = np.arange(N, dtype='int64')
    keys = cell_key(day[None, :], board_id[:, None], mv_b, liq_b)
    ok = el & (mv_b >= 0) & (liq_b >= 0)
    si, di = np.nonzero(ok)
    k = keys[si, di]
    order = np.argsort(k, kind='stable')
    out = {'key': k[order], 's': si[order].astype('int64'),
           'ind': ind[si, di][order].astype('int32')}
    if lg:
        uq, cnt = np.unique(out['key'], return_counts=True)
        lg('  strata: %d members / %d cells (mean %.0f, median %.0f, max %d)'
           % (len(out['key']), len(uq), cnt.mean(), np.median(cnt), cnt.max()))
    return out


def cell_key(days, board_id, mv_b, liq_b):
    return (((np.asarray(days, dtype='int64') * 4
              + np.asarray(board_id, dtype='int64')) * 3
             + np.asarray(mv_b, dtype='int64')) * 3
            + np.asarray(liq_b, dtype='int64'))


def pick_stratum(amap, keys, want_ind, qs, qdays, ban, rng, S):
    """One control per query, from the matched cell.

    exact-industry subset first; the coarsened cell only when that subset
    is empty.  A member is rejected when it is the query's own stock or a
    treated (candidate) observation on the same day.

    returns  ctrl (Q,) stock index or -1,  mode (Q,) 0 exact / 1 coarsened
             / -1 no control available
    """
    K = amap['key']
    lo = np.searchsorted(K, keys, 'left')
    hi = np.searchsorted(K, keys, 'right')
    Q = len(keys)
    ctrl = np.full(Q, -1, dtype='int64')
    mode = np.full(Q, -1, dtype='int8')
    for q in range(Q):
        if hi[q] <= lo[q]:
            continue
        mem = np.arange(lo[q], hi[q])
        ms = amap['s'][mem]
        mi = amap['ind'][mem]
        bad = (ms == qs[q]) | np.isin(qdays[q] * S + ms, ban)
        keep = ~bad
        if not keep.any():
            continue
        ms, mi = ms[keep], mi[keep]
        if want_ind[q] >= 0:
            sub = mi == want_ind[q]
            if sub.any():
                j = rng.integers(0, int(sub.sum()))
                ctrl[q] = ms[sub][j]
                mode[q] = 0
                continue
        ctrl[q] = ms[rng.integers(0, ms.size)]
        mode[q] = 1
    return ctrl, mode


def month_index(g, el, lg=None):
    """(stock * 10000 + YYYYMM) -> eligible sessions, sorted."""
    dates = g['dates']
    si_e, di_e = np.nonzero(el)
    keep = di_e <= (el.shape[1] - 2)
    si_e, di_e = si_e[keep], di_e[keep]
    key = si_e.astype('int64') * 10000 + \
        np.array([int(d[:6]) for d in dates[di_e]], dtype='int64')
    order = np.argsort(key, kind='stable')
    if lg:
        lg('  month index %d eligible (stock,month) sessions' % len(key))
    return {'key': key[order], 'day': di_e[order].astype('int64')}


def pick_month(amap, keys, f_lo, f_hi, rng, tries=20):
    lo = np.searchsorted(amap['key'], keys, 'left')
    hi = np.searchsorted(amap['key'], keys, 'right')
    n = (hi - lo).astype('int64')
    out = np.full(len(keys), -1, dtype='int64')
    pend = np.flatnonzero(n > 0)
    for _ in range(tries):
        if pend.size == 0:
            break
        d = amap['day'][lo[pend] + rng.integers(0, n[pend])]
        bad = (d >= f_lo[pend]) & (d <= f_hi[pend])
        out[pend[~bad]] = d[~bad]
        pend = pend[bad]
    return out
