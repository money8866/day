# -*- coding: utf-8 -*-
"""H-W7-RANK-01 common layer.

Research ID : H-W7-RANK-01
Title       : W7 daily cross-sectional relative ranking -- independent increment

Hypothesis (frozen in H_W7_RANK_01_SPEC.md before any result):

  H0  the within-day ordering of W7's actionable list carries no information;
      taking the top N is equivalent to taking a random N of the same days.
  H1  controlling for the number of trades (same day, same N), ordering the
      list by a fixed key and taking the top N still adds a stable increment.

The frozen W7 backtest ledger contains NO daily cross-sectional truncation
(w7_te_v3_backtest.py:426 records that production W7 does have a daily board
cut which the backtest does not model).  The HVT sibling does:
hvt_bull/te_backtest.py:530-543 keeps the top `max_buy_candidates = 3` per
decision_date by (execution_score desc, buyability desc).

Nothing here modifies W7 or HVT.  Every arm shares one price basis:
entry = open[decision + 1],  exit = close[entry + h].
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
HALPHA = os.path.join(ROOT, 'research', 'h_alpha')
for _p in (HALPHA, os.path.join(ROOT, 'research', 'h_rbp'),
           os.path.join(ROOT, 'research', 'hve'), os.path.join(ROOT, 'research', 'h_earn_fwd')):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import has_common as C                                          # noqa: E402
import has_attr as A                                            # noqa: E402
from hrbp_common import regime_by_day, tail_table               # noqa: E402

OUT = HERE
DATA = os.path.join(OUT, 'data')
os.makedirs(DATA, exist_ok=True)

HZ = list(C.PREREG_A['horizons'])
HZ0 = C.PREREG_A['primary_horizon']

# =====================================================================
# PREREG_R -- frozen before any H-W7-RANK-01 result was observed
# =====================================================================
PREREG_R = {
    'hypothesis_id': 'H-W7-RANK-01',
    'version': '1.0',
    'created': '2026-09-30',
    'parent': 'H-ALPHA-SOURCE-01',

    'primary_n': 3,
    'primary_key': ('exec', 'eq'),
    'lattice_n': (1, 2, 3, 5, 10),
    'lattice_keys': (
        ('exec', 'eq'), ('exec', 'score'), ('score',), ('exec',), ('eq',),
        ('drisk_asc', 'exec'),
    ),

    'sort_tiebreak': 'ts_code (stable mergesort) -- fixed here, not data driven',
    'overlap_guard_sessions': C.PREREG_A['overlap_guard_sessions'],
    'horizons': tuple(HZ),
    'primary_horizon': HZ0,
    'cost_bp': C.PREREG_A['cost_bp'],
    'primary_cost_bp': C.PREREG_A['primary_cost_bp'],

    'B_null': 1000,
    'seed': C.PREREG_A['seed'],
    'tail_levels': C.PREREG_A['tail_levels'],
    'min_n': 30,

    'phase_IS': C.PREREG_A['phase_IS'],
    'phase_VALID': C.PREREG_A['phase_VALID'],
    'phase_OOS': C.PREREG_A['phase_OOS'],

    'trading_authorization': 'NO',
}

W7_KEYS = ('exec', 'score', 'eq', 'drisk')


# =====================================================================
# logging / io
# =====================================================================
class Log(object):
    def __init__(self, name):
        self.path = os.path.join(OUT, '%s.log' % name)
        self.t0 = time.time()
        with open(self.path, 'w', encoding='utf-8'):
            pass

    def __call__(self, msg):
        line = '[%7.1fs] %s' % (time.time() - self.t0, msg)
        print(line)
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')

    def sep(self, ch='-', n=78):
        self(ch * n)


def save_csv(df, name):
    p = os.path.join(DATA, name)
    df.to_csv(p, index=False, encoding='utf-8-sig')
    return p


def save_json(obj, name):
    p = os.path.join(DATA, name)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1, default=str)
    return p


# =====================================================================
# ledger loading -- raw actionable rows, BEFORE dedup / overlap guard
# =====================================================================
def _attach(d, lg=None, tag=''):
    """Panel indices for a raw ledger slice.  No dedup, no overlap guard:
    those are applied per arm so every arm gets the identical rule."""
    g = C.grid()
    d2i, s2i = g['d2i'], g['s2i']
    N = len(g['dates'])
    d = d[d['ts_code'].isin(s2i)].copy()
    d['s_i'] = d['ts_code'].map(s2i).astype('int64')
    d['ev_idx'] = d['ev_date'].astype('str').map(d2i)
    d['dec_idx'] = d['dec_date'].astype('str').map(d2i)
    d = d[d['ev_idx'].notna() & d['dec_idx'].notna()].copy()
    d['ev_idx'] = d['ev_idx'].astype('int64')
    d['dec_idx'] = d['dec_idx'].astype('int64')
    d['entry_idx'] = d['dec_idx'] + 1
    d = d[(d['entry_idx'] >= 0) & (d['entry_idx'] <= N - 2)].reset_index(drop=True)
    if lg:
        lg('  %s raw actionable on panel: %d rows / %d decision days'
           % (tag, len(d), d['dec_date'].nunique()))
    return d


def load_w7(lg=None):
    p = os.path.join(C.REPORT_DAILY, C.W7_LEDGER)
    d = pd.read_csv(p, low_memory=False)
    n0 = len(d)
    d = d[d['action'].isin(C.PREREG_A['w7_actions'])].copy()
    if lg:
        lg('W7 ledger %d rows -> actionable %d' % (n0, len(d)))
    d['ts_code'] = d['code'].astype(str)
    d['ev_date'] = C._date_int(d['event_date'])
    d['dec_date'] = C._date_int(d['signal_date'])
    for c in W7_KEYS:
        d[c] = pd.to_numeric(d[c], errors='coerce')
    return _attach(d, lg, 'W7')


def load_hvt(lg=None):
    p = os.path.join(C.REPORT_DAILY, C.HVT_LEDGER)
    d = pd.read_csv(p, low_memory=False)
    n0 = len(d)
    d = d[d['next_day_action'].isin(C.PREREG_A['hvt_actions'])].copy()
    if lg:
        lg('HVT ledger %d rows -> actionable %d' % (n0, len(d)))
    d['ts_code'] = d['ts_code'].astype(str)
    d['ev_date'] = C._date_int(d['signal_date'])
    d['dec_date'] = C._date_int(d['decision_date'])
    d['exec'] = pd.to_numeric(d['execution_score'], errors='coerce')
    d['eq'] = pd.to_numeric(d['buyability'], errors='coerce')
    d['top3'] = C.hvt_top_flag(d)          # te_backtest.py:530-543 rule
    return _attach(d, lg, 'HVT')


# =====================================================================
# ranking / arms
# =====================================================================
def _sort_frame(d, key):
    t = d
    cols, asc = [], []
    for k in key:
        if k.endswith('_asc'):
            cols.append(k[:-4])
            asc.append(True)
        else:
            cols.append(k)
            asc.append(False)
    out = t[['dec_date', 'ts_code'] + cols].copy()
    for c in cols:
        out[c] = pd.to_numeric(out[c], errors='coerce')
    out['_ord'] = np.arange(len(out))
    out = out.sort_values(['dec_date'] + cols + ['ts_code', '_ord'],
                          ascending=[True] + asc + [True, True],
                          kind='mergesort')
    return out


def top_n_mask(d, key, n, day_col='dec_date'):
    """Boolean mask: the top `n` rows of every decision day by `key`."""
    out = _sort_frame(d, key)
    idx = out.groupby(day_col, sort=False).head(n)['_ord'].to_numpy()
    m = np.zeros(len(d), dtype=bool)
    m[idx] = True
    return m


def random_n_mask(d, n, rng, day_col='dec_date'):
    """Boolean mask: n rows drawn uniformly without replacement per day."""
    m = np.zeros(len(d), dtype=bool)
    ordv = np.arange(len(d))
    codes = pd.factorize(d[day_col], sort=False)[0]
    order = np.argsort(codes, kind='mergesort')
    start, end = 0, 0
    M = codes.max() + 1 if len(codes) else 0
    for c in range(M):
        while end < len(codes) and codes[order[end]] == c:
            end += 1
        blk = order[start:end]
        k = min(n, blk.size)
        if k > 0:
            m[rng.choice(blk, size=k, replace=False)] = True
        start = end
    return m


def arm_sample(d, mask, lg=None, tag=''):
    """dedup(stock,event) + 20-session overlap guard -- identical for every arm."""
    s = d[mask].copy()
    s = s.sort_values(['s_i', 'ev_date', 'dec_date'], kind='mergesort')
    s = s.drop_duplicates(['s_i', 'ev_date'], keep='first')
    if s.empty:
        return s
    keep, last = [], {}
    for r in s.itertuples():
        lp = last.get(r.s_i)
        if lp is None or (r.entry_idx - lp) > PREREG_R['overlap_guard_sessions']:
            keep.append(r.Index)
            last[r.s_i] = r.entry_idx
    s = s.loc[keep].copy()
    g = C.grid()
    s['month'] = [int(g['dates'][i][:6]) for i in s['entry_idx']]
    s['year'] = [int(g['dates'][i][:4]) for i in s['entry_idx']]
    if lg:
        lg('  arm %-24s n=%4d  days=%d' % (tag, len(s), s['dec_date'].nunique()))
    return s.reset_index(drop=True)


def rets_of(g, s, h):
    return C.ret_open(g, s['s_i'].to_numpy('int64'),
                      s['entry_idx'].to_numpy('int64'), h)


def rets_matrix(g, s):
    return {h: rets_of(g, s, h) for h in HZ}


def cost_net(r, bp=None):
    bp = PREREG_R['primary_cost_bp'] if bp is None else bp
    return np.asarray(r, 'float64') - bp / 10000.0


def compare(ra, ma, rb, mb, B=None):
    B = PREREG_R['B_null'] if B is None else B
    return A.boot_diff(ra, ma, rb, mb, B=B, seed=PREREG_R['seed'])


def regime_of(g):
    return regime_by_day(g)


def phase_of_year(y):
    for nm in ('IS', 'VALID', 'OOS'):
        a, b = PREREG_R['phase_%s' % nm]
        if a <= y <= b:
            return nm
    return 'OUT'
