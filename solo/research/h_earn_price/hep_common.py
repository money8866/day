# -*- coding: utf-8 -*-
"""H-EARN-PRICE-01 common: preregistered constants + loaders.

Hypothesis H-EARN-PRICE-01
    Forecastable Earnings Surprise -> Incremental Price Alpha
Strictly ISOLATED from H-EARN-FWD V1.0.

Discipline
----------
* The H-EARN-FWD model is FROZEN.  This layer only READS its saved
  predictions (predictions_company.parquet) and its saved target
  (targets.parquet).  It never retrains, never re-derives features,
  never edits labels / hyper-parameters / sample.
* Data source = the existing Tushare cache (fundamental_surprise_alpha
  panels).  No re-download, no source switching to chase a result.
* Point-in-Time: the target report announcement must be strictly after
  every information item used to form the signal.  Any violation -> FAIL.

Causal chain under test
-----------------------
    Forecast -> Expected Surprise -> Market Expectation Gap
             -> Price Repricing  -> Excess Return
Each layer is tested separately (see hep_report.py section 28).
"""
import os, sys, json, time
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
HEF = os.path.abspath(os.path.join(HERE, '..', 'h_earn_fwd'))
HEF_DATA = os.path.join(HEF, 'data')
FS_DATA = os.path.abspath(os.path.join(HERE, '..',
                                       'fundamental_surprise_alpha', 'data'))
CD = r'D:\mystock\cache_daily'
PQ = os.path.join(CD, 'parquet')
OUT = HERE
DATA = os.path.join(OUT, 'data')
os.makedirs(DATA, exist_ok=True)

sys.path.insert(0, HEF)
# read-only reuse of generic (model-agnostic) data loaders / statistics
from hef_common import (load_calendar, trade_offset, load_industry_map,   # noqa
                        industry_map_fast, industry_at_fast,             # noqa
                        load_price_panel, load_basic_panel,                # noqa
                        load_stk_basic, spearman_ic, ic_stats, ols_resid,  # noqa
                        dummies, demean_by, winsorize, zs, rank_pct,       # noqa
                        fmt, pct, q_info, q_add, pearson_r, auc_score,     # noqa
                        num2str_date)

# ---------------------------------------------------------------- PREREG
PREREG = {
    'hypothesis_id': 'H-EARN-PRICE-01',
    'created': '2026-09-26',
    'version': 'V1.0',
    'title': 'Forecastable Earnings Surprise -> Incremental Price Alpha',
    'isolation': 'H-EARN-FWD V1.0 model FROZEN; price layer is read-only on '
                 'its saved predictions; no retraining / relabelling / '
                 're-featurisation / sample editing of any kind.',

    # ---- frozen forecast source (H-EARN-FWD saved artefacts) -----------
    'frozen_predictions': os.path.join(HEF_DATA, 'predictions_company.parquet'),
    'frozen_targets': os.path.join(HEF_DATA, 'targets.parquet'),
    'frozen_events': os.path.join(HEF_DATA, 'events.parquet'),
    'forecast_primary': {'scheme': 'TRAINFIX', 'model': 'GBM',
                         'target': 'model_surprise_pct', 'offset': 30},
    'forecast_alt_model': ['RIDGE'],
    'forecast_alt_offset': [20, 60],
    # walk-forward stitched frozen forecast: year -> H-EARN-FWD WF scheme
    'forecast_wf_stitch': {2021: 'W1', 2022: 'W2', 2023: 'W3',
                           2024: 'W4', 2025: 'W5', 2026: 'W6'},

    # ---- event timing --------------------------------------------------
    'anchor_rule': 'A = first-announcement date of the target report '
                   '(min ann over versions per ts_code x target_end).',
    'announcement_timing': 'UNKNOWN (Tushare daily carries no intraday '
                           'disclosure time); never assumed.',
    't0_rule': 'T0 = first trading session STRICTLY AFTER A. Conservative: '
               'a report stamped A may have been released after the A close, '
               'so A itself is never treated as post-announcement.',
    'entry_regime_primary': 'ANNOUNCE',
    'entry_regimes': {
        'ANNOUNCE': 'enter at the open of T0 (first session where the info '
                    'is definitely public), exit at close of T0+H',
        'PREDICT': 'enter at the close of P+1 (forecast confirmed at the P '
                   'close), exit at close of P+1+H  [Phase-A runner]',
    },

    # ---- windows / horizons -------------------------------------------
    'event_offsets': (-20, -10, -5, -3, -1, 0, 1, 3, 5, 10, 20),
    'horizons': (1, 3, 5, 10, 20),
    'primary_horizon': 5,
    'pre_windows': (20, 10, 5),
    'momentum': (20, 60),

    # ---- portfolio -----------------------------------------------------
    'quantiles': (0.10, 0.20),
    'primary_quantile': 0.10,
    'cost_bp': (0, 10, 20, 30, 50),
    'primary_cost_bp': 30,
    'cost_note': 'round-trip: commission 2.5bp/side + stamp 5bp/sell + '
                 'slippage/impact 7.5bp/side = 30bp round trip at the '
                 'primary setting; scaled linearly for the ladder.',

    # ---- controls ------------------------------------------------------
    'industry_control': 'SW-L1 (PIT at the decision session)',
    'size_control': 'log(total_mv) at the decision session',
    'value_control': 'pe_ttm (fallback na) and pb',
    'vol_control': '20d realised vol of daily returns ending at the '
                   'decision session',
    'turnover_control': 'turnover_rate at the decision session',
    'control_date': 'announcement session A (last info date at the T0 '
                    'decision); strictly PIT for the T0 close.',

    # ---- phases (inherited from the frozen forecast) -------------------
    'phases': (('VALID', 2023, 2024), ('OOS', 2025, 2025),
               ('LIVE-LIKE', 2026, 2026)),
    'oos_years': (2025, 2026),
    'wf_windows': (('WF1', 2021, 2021), ('WF2', 2022, 2022),
                   ('WF3', 2023, 2023), ('WF4', 2024, 2024),
                   ('WF5', 2025, 2025), ('WF6', 2026, 2026)),

    # ---- regimes (pre-defined, independent of the forecast) ------------
    'regime_rule': 'market state on the event date: BULL if the index level '
                   'is above its own 120d MA and its 60d return > +5%; BEAR if '
                   'below the 120d MA and 60d return < -5%; else NORMAL.',

    # ---- statistics ----------------------------------------------------
    'min_xs': 20,
    'ic_min_n': 15,
    'winsor_ret': (0.01, 0.99),
    'n_perm': 1000,
    'fdr_q': 0.05,
    'seed': 20261001,
    'industry_concentration_threshold': 0.30,
    'tail_share_flag': 0.50,

    # ---- discipline ----------------------------------------------------
    'trading_authorization': 'NO',
    'st_kept': True,
    'delisted_kept': True,
    'bj_kept': True,
    'note': 'A-share short selling is not generally executable; all long-short '
            'results are research diagnostics, not tradeable claims.',
}


def phase_of_year(y):
    for tag, a, b in PREREG['phases']:
        if a <= y <= b:
            return tag
    return 'PRE'


# ---------------------------------------------------------------- logging
class Log(object):
    def __init__(self, name):
        self.name = name
        self.path = os.path.join(OUT, '%s.log' % name)
        self.t0 = time.time()

    def __call__(self, msg):
        line = '[%7.1fs] %s' % (time.time() - self.t0, msg)
        print(line)
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')


# ------------------------------------------------------------- price grid
def build_price_grid(cal):
    """Wide per-stock price matrices indexed by the calendar position.

    Returns dict with
      dates  : (N,) 'YYYYMMDD' object array (calendar order)
      d2i    : dict date -> position
      s2i    : dict ts_code -> row
      codes  : (S,) ts_code array
      close  : (S,N) float32, forward-filled (suspension -> last close)
      open   : (S,N) float32, forward-filled
      traded : (S,N) bool   , an actual session print exists that day
    """
    cal = cal.sort_values('idx').reset_index(drop=True)
    dates = cal['trade_date'].astype(str).to_numpy()
    N = len(dates)
    d2i = {d: i for i, d in enumerate(dates)}

    p = pd.read_parquet(os.path.join(FS_DATA, 'price_panel.parquet'),
                        columns=['ts_code', 'trade_date', 'qfq_close',
                                 'qfq_open'])
    p['ts_code'] = p['ts_code'].astype(str)
    p['trade_date'] = p['trade_date'].astype(str)
    codes = np.sort(p['ts_code'].unique())
    s2i = {c: i for i, c in enumerate(codes)}
    S = len(codes)
    si = p['ts_code'].map(s2i).to_numpy()
    di = p['trade_date'].map(d2i)
    ok = di.notna().to_numpy()
    si, di = si[ok], di[ok].astype('int64')

    close = np.full((S, N), np.nan, dtype='float32')
    open_ = np.full((S, N), np.nan, dtype='float32')
    close[si, di] = p['qfq_close'].to_numpy()[ok]
    open_[si, di] = p['qfq_open'].to_numpy()[ok]
    traded = ~np.isnan(close)

    close_ff = pd.DataFrame(close).ffill(axis=1).to_numpy(dtype='float32')
    open_ff = pd.DataFrame(open_).ffill(axis=1).to_numpy(dtype='float32')
    return {'dates': dates, 'd2i': d2i, 's2i': s2i, 'codes': codes,
            'close': close_ff, 'open': open_ff, 'traded': traded}


def grid_ret(grid, si, i_from, i_to):
    """close-to-close return from position i_from (inclusive base = i_from-1)
    to i_to: close[i_to] / close[i_from-1] - 1, elementwise on row indices si.
    Any position off the calendar -> NaN."""
    N = grid['close'].shape[1]
    a = np.asarray(i_from) - 1
    b = np.asarray(i_to)
    ok = (a >= 0) & (b >= 0) & (a < N) & (b < N)
    a = np.clip(a, 0, N - 1)
    b = np.clip(b, 0, N - 1)
    c0 = grid['close'][si, a]
    c1 = grid['close'][si, b]
    r = np.where(c0 > 0, c1 / c0 - 1.0, np.nan)
    return np.where(ok, r, np.nan)


# --------------------------------------------------------- market / industry
def build_market_ind_cum(cal, lg=None):
    """Daily equal-weighted market return + per-SW-L1 industry return, and
    their cumulative levels (PIT industry membership via merge_asof on the
    stored SW segments).

    Returns (mkt_cum: Series indexed by calendar position,
             ind_cum: DataFrame index=trade_date, columns=l1)
    """
    cal = cal.sort_values('idx').reset_index(drop=True)
    p = pd.read_parquet(os.path.join(FS_DATA, 'price_panel.parquet'),
                        columns=['ts_code', 'trade_date', 'qfq_close'])
    p['ts_code'] = p['ts_code'].astype(str)
    p['trade_date'] = p['trade_date'].astype(str)
    p = p.sort_values(['ts_code', 'trade_date'])
    p['ret'] = p.groupby('ts_code')['qfq_close'].pct_change()

    im = load_industry_map()
    im = im[['ts_code', 'in_date', 'out_date', 'l1']].copy()
    im['_in'] = im['in_date'].astype('int64')
    im = im.sort_values('_in')
    q = p[['ts_code', 'trade_date']].copy()
    q['_d'] = q['trade_date'].astype('int64')
    q = q.sort_values('_d')
    m = pd.merge_asof(q, im, left_on='_d', right_on='_in', by='ts_code',
                      direction='backward')
    m['l1'] = np.where(
        m['out_date'].astype(str).fillna('29991231') >= m['trade_date'],
        m['l1'].fillna('UNKNOWN'), 'UNKNOWN')
    p['l1'] = m['l1'].to_numpy()

    mkt = p.groupby('trade_date')['ret'].mean()
    ind = p.groupby(['trade_date', 'l1'])['ret'].mean().unstack()
    if lg:
        lg('market/industry daily panel: %d days, %d industries, '
           'ind coverage %.3f'
           % (len(mkt), ind.shape[1], float((p['l1'] != 'UNKNOWN').mean())))
    mkt = mkt.reindex(cal['trade_date'].astype(str).to_numpy())
    ind = ind.reindex(cal['trade_date'].astype(str).to_numpy())
    mkt_cum = (1.0 + mkt.fillna(0.0)).cumprod()
    ind_cum = (1.0 + ind.fillna(0.0)).cumprod()
    return mkt_cum, ind_cum, mkt


def cum_ret(level, i_from, i_to):
    """level: np array of cumulative levels (calendar order).
    return over [i_from-1, i_to] = level[i_to]/level[i_from-1] - 1."""
    N = len(level)
    a = np.asarray(i_from) - 1
    b = np.asarray(i_to)
    ok = (a >= 0) & (b >= 0) & (a < N) & (b < N)
    a = np.clip(a, 0, N - 1)
    b = np.clip(b, 0, N - 1)
    v0 = np.asarray(level)[a]
    v1 = np.asarray(level)[b]
    return np.where(ok & (v0 > 0), v1 / v0 - 1.0, np.nan)


# ------------------------------------------------------------------ helpers
def event_ar_grid(close, mkt_lvl, si, i0, kmin=-21, kmax=20):
    """Market-adjusted daily abnormal return per event over offsets kmin..kmax.

    AR[k] = stock close-to-close return on calendar day (T0+k) minus the
    equal-weighted market return on the same day.  Returns a DataFrame whose
    rows are aligned to (si, i0) and whose columns are the integer offsets.
    """
    N = close.shape[1]
    out = {}
    for k in range(kmin, kmax + 1):
        a, b = i0 + k - 1, i0 + k
        ok = (a >= 0) & (b <= N - 1)
        aa, bb = np.clip(a, 0, N - 1), np.clip(b, 0, N - 1)
        c0, c1 = close[si, aa].astype(float), close[si, bb].astype(float)
        sret = np.where(c0 > 0, c1 / c0 - 1.0, np.nan)
        m0, m1 = mkt_lvl[aa], mkt_lvl[bb]
        mret = np.where(m0 > 0, m1 / m0 - 1.0, np.nan)
        out[k] = np.where(ok, sret - mret, np.nan)
    return pd.DataFrame(out)[list(range(kmin, kmax + 1))]


def _xsz(s, day):
    """Cross-sectional z-score of s within each day (0 when the day has no
    dispersion).  NaN is preserved."""
    v = pd.to_numeric(pd.Series(s).reset_index(drop=True), errors='coerce')
    g = v.groupby(pd.Series(np.asarray(day)))
    return g.transform(
        lambda x: (x - x.mean()) / x.std() if x.std() else 0.0).to_numpy()


def build_return_sets(close, mkt_lvl, ev, si, i0, kmax, day='T0'):
    """Daily adjusted return panels, one row per event, offsets 1..kmax.

    sets['raw']                      : raw close-to-close
    sets['market_neutral']           : minus the equal-weighted market day
    sets['industry_neutral']         : minus the same-day same-SW-L1 mean
    sets['industry_momentum_neutral']: industry leg, then the per-day residual
                                       on standardised MOM20 / MOM60

    Returns (sets, ctx) with ctx = {'l1','pos','z1','z2','code'}.
    """
    N = close.shape[1]
    raw = event_ar_grid(close, np.ones(N), si, i0, 1, kmax)
    mkt = event_ar_grid(close, mkt_lvl, si, i0, 1, kmax)
    pos = np.asarray(i0)[:, None] + np.arange(1, kmax + 1)[None, :]
    l1 = ev['sw_l1'].astype(str).to_numpy()
    code = pd.factorize(l1)[0]
    ind = mkt.copy()
    for j in range(kmax):
        key = pd.Series(pos[:, j] * 1000 + code)
        g = pd.Series(mkt[j + 1].to_numpy()).groupby(key)
        ind[j + 1] = mkt[j + 1].to_numpy() - g.transform('mean').to_numpy()
    z1 = _xsz(ev['mom20'], ev[day])
    z2 = _xsz(ev['mom60'], ev[day])
    indm = ind.copy()
    for j in range(kmax):
        v = ind[j + 1].to_numpy()
        m = np.isfinite(v) & np.isfinite(z1) & np.isfinite(z2)
        out = v.copy()
        idx = np.flatnonzero(m)
        if len(idx):
            df = pd.DataFrame({'d': pos[idx, j], 'v': v[idx], 'a': z1[idx],
                               'b': z2[idx], 'i': idx})
            for _, gg in df.groupby('d'):
                if len(gg) < 20:
                    continue
                A = np.column_stack([np.ones(len(gg)), gg['a'].to_numpy(),
                                     gg['b'].to_numpy()])
                coef, *_ = np.linalg.lstsq(A, gg['v'].to_numpy(), rcond=None)
                out[gg['i'].to_numpy()] = gg['v'].to_numpy() - A.dot(coef)
        indm[j + 1] = out
    sets = {'raw': raw, 'market_neutral': mkt, 'industry_neutral': ind,
            'industry_momentum_neutral': indm}
    return sets, {'l1': l1, 'pos': pos, 'z1': z1, 'z2': z2, 'code': code}


def winsor_xs(df, col, by, lo=None, hi=None):
    lo = PREREG['winsor_ret'][0] if lo is None else lo
    hi = PREREG['winsor_ret'][1] if hi is None else hi
    g = df.groupby(by)[col]
    ql, qh = g.transform('quantile', lo), g.transform('quantile', hi)
    return df[col].clip(ql, qh)


def ic_stats(ics, lag):
    """mean, Newey-West(lag) t-stat, ICIR, n  -- CORRECTED estimator.

    Local override of the helper re-exported from the H-EARN-FWD module.  The
    inherited version has two defects that inflate every t-statistic by about
    sqrt(n_days)/2 (roughly 7x at n=206: a zero-mean iid series returns t ~ 3):

      A. the long-run variance is divided by an extra n
         (t = mu*n/sqrt(Omega) instead of mu*sqrt(n)/sqrt(Omega));
      B. every autocovariance term is computed with pandas Series labels, so
         the two operands re-align on the shared index and gamma_k collapses to
         gamma_0 - empirically Omega = (lag+1)*gamma_0 on pure noise.

    The frozen study is left untouched.  Here we use the textbook Newey-West
    Bartlett kernel: Omega = gamma_0 + 2*sum_{i=1..L}(1 - i/(L+1))*gamma_i and
    Var(mean) = Omega / n.  This only ever SHRINKS significance, so no result
    is flattered by the correction.
    """
    x = pd.Series(ics).dropna().astype(float)
    if len(x) < 5:
        return np.nan, np.nan, np.nan, len(x)
    d = x.to_numpy(dtype=float)
    n = len(d)
    mu = float(d.mean())
    d = d - mu
    L = int(min(lag, n - 1))
    om = float(d @ d) / n
    for i in range(1, L + 1):
        om += 2.0 * (1.0 - i / (L + 1.0)) * float(d[i:] @ d[:-i]) / n
    var = om / n
    t = mu / np.sqrt(var) if var > 0 else np.nan
    sd = float(d.std(ddof=1))
    return mu, float(t), (mu / sd if sd > 0 else np.nan), n


def bh_fdr(pvals):
    """Benjamini-Hochberg adjusted p-values (NaN preserved)."""
    p = pd.Series(pvals, dtype=float)
    m = p.notna()
    v = p[m].to_numpy()
    n = len(v)
    if n == 0:
        return p
    order = np.argsort(v)
    ranked = v[order]
    adj = ranked * n / (np.arange(n) + 1.0)
    adj = np.minimum.accumulate(adj[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.clip(adj, 0, 1)
    r = pd.Series(np.nan, index=p.index, dtype=float)
    r[m] = out
    return r


def nw_tstat(x, lag):
    mu, t, icir, n = ic_stats(x, lag)
    return mu, t, icir, n


def spearman_with_p(pred, actual, min_n=None):
    """Spearman IC + two-sided p from the t approximation."""
    pred, actual = pd.Series(pred, dtype=float), pd.Series(actual, dtype=float)
    m = pred.notna() & actual.notna() & np.isfinite(pred) & np.isfinite(actual)
    n = int(m.sum())
    mn = min_n or PREREG['ic_min_n']
    if n < mn:
        return np.nan, np.nan, n
    r = pred[m].corr(actual[m], method='spearman')
    if not np.isfinite(r) or abs(r) >= 1:
        return r, np.nan, n
    t = r * np.sqrt((n - 2) / max(1e-12, 1 - r * r))
    from math import erf
    p = 2 * (1 - 0.5 * (1 + erf(abs(t) / np.sqrt(2))))
    return float(r), float(p), n


if __name__ == '__main__':
    lg = Log('hep_common_selftest')
    lg('PREREG %s v%s' % (PREREG['hypothesis_id'], PREREG['version']))
    lg('primary forecast %s' % PREREG['forecast_primary'])
    cal = load_calendar()
    lg('calendar %s .. %s n=%d' % (cal['trade_date'].iloc[0],
                                   cal['trade_date'].iloc[-1], len(cal)))
    lg('trade_offset(A=%s, -30) = %s'
       % ('20250829', trade_offset(cal, '20250829', 30)))
    lg('selftest OK')
