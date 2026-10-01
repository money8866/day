# -*- coding: utf-8 -*-
"""H-ALPHA-SOURCE-01 -- POST-HOC tail diagnostic.

Question raised after the frozen study closed:
    "is there a rule that finds the big winners?"

This file is NOT part of the pre-registered design.  It re-reads the frozen
master table H_ALPHA_SOURCE_01_MATCHED.csv and describes the right tail of the
observed legs.  No leg, threshold, horizon, cost or sample is re-defined and
no result of this file feeds back into the frozen study, the SPEC or the
report.  Everything below is DESCRIPTIVE / IN-SAMPLE and cannot support a
trading claim on its own -- it can only nominate hypotheses.

All features are computed strictly at or before the DECISION session
(dec_idx = entry_idx - 1), so nothing reads a price the trader could not
have seen at the entry open.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import has_common as C                                          # noqa: E402

OUT, DATA = C.OUT, C.DATA


# ---------------------------------------------------------------------
# rolling helpers (trailing window ending at d, inclusive)
# ---------------------------------------------------------------------
def _cum(M):
    return np.cumsum(np.where(np.isfinite(M), M, 0.0), axis=1)


def roll_mean(M, w):
    S, N = M.shape
    X = np.where(np.isfinite(M), M, 0.0)
    V = np.isfinite(M).astype('float64')
    cx, cv = _cum(X), _cum(V)
    sx, sv = cx.copy(), cv.copy()
    sx[:, w:] = cx[:, w:] - cx[:, :-w]
    sv[:, w:] = cv[:, w:] - cv[:, :-w]
    out = np.full((S, N), np.nan)
    ok = sv >= max(1, int(w * 0.8))
    out[ok] = sx[ok] / sv[ok]
    return out


def roll_std(M, w):
    S, N = M.shape
    X = np.where(np.isfinite(M), M, 0.0)
    V = np.isfinite(M).astype('float64')
    cx, cx2, cv = _cum(X), _cum(X * X), _cum(V)
    def win(c):
        o = c.copy()
        o[:, w:] = c[:, w:] - c[:, :-w]
        return o
    s1, s2, n = win(cx), win(cx2), win(cv)
    m = np.divide(s1, n, out=np.full_like(s1, np.nan), where=n > 0)
    var = np.divide(s2, n, out=np.full_like(s2, np.nan), where=n > 0) - m * m
    out = np.full((S, N), np.nan)
    ok = (n >= max(1, int(w * 0.8))) & (var > 0)
    out[ok] = np.sqrt(var[ok])
    return out


def roll_max(M, w):
    """max over [d-w+1 .. d]."""
    S, N = M.shape
    acc = np.full((S, N), -np.inf, dtype='float64')
    Mm = np.where(np.isfinite(M), M, -np.inf).astype('float64')
    for k in range(w):
        sh = np.full((S, N), -np.inf)
        if k == 0:
            sh = Mm
        else:
            sh[:, k:] = Mm[:, :N - k]
        acc = np.maximum(acc, sh)
    acc[:, :w - 1] = np.nan
    acc[~np.isfinite(acc)] = np.nan
    return acc


def at(A, si, di):
    return A[si, di]


# ---------------------------------------------------------------------
def qtab(lg, df, col, ycol, q=5, tag=''):
    """quintile profile of the observed return, plus matched-control delta."""
    v = df[col].to_numpy('float64')
    m = np.isfinite(v)
    if m.sum() < q * 20:
        return None
    qs = np.quantile(v[m], np.linspace(0, 1, q + 1)[1:-1])
    b = np.searchsorted(qs, v, side='right')
    rows = []
    for k in range(q):
        s = df[(b == k) & m]
        if len(s) < 20:
            continue
        y = s[ycol].to_numpy('float64')
        rc = s['r_cand_%s' % ycol.split('_')[-1]].to_numpy('float64')
        rows.append({
            'feature': col, 'q': k + 1, 'n': len(s),
            'lo': qs[k - 1] if k else v[m].min(),
            'hi': qs[k] if k < q - 1 else v[m].max(),
            'mean': np.nanmean(y), 'median': np.nanmedian(y),
            'win': float(np.nanmean(y > 0)),
            'ctrl': np.nanmean(rc), 'delta': np.nanmean(y) - np.nanmean(rc),
        })
    t = pd.DataFrame(rows)
    lg('')
    lg('  %s   quintiles of %s' % (tag, col))
    lg('    q     n      lo       hi       mean     median    win    ctrl     delta')
    for _, r in t.iterrows():
        lg('    %d  %5d  %+8.3f %+8.3f  %+8.4f %+8.4f  %.3f  %+7.4f  %+8.4f'
           % (r['q'], r['n'], r['lo'], r['hi'], r['mean'], r['median'],
              r['win'], r['ctrl'], r['delta']))
    # rank information coefficient (Spearman) on the finite subset
    ic = pd.Series(v[m]).corr(pd.Series(df[ycol].to_numpy('float64')[m]),
                              method='spearman')
    lg('    rank-IC (Spearman) vs %s : %+0.4f' % (ycol, ic))
    t['rank_ic'] = ic
    return t


def main():
    lg = C.Log('_diag_tail')
    lg('H-ALPHA-SOURCE-01 tail diagnostic -- descriptive only, '
       'no result feeds back into the frozen design')
    g = C.grid(lg=lg)
    el = C.eligibility(lg=lg)
    S, N = g['close'].shape

    df = pd.read_csv(os.path.join(DATA, 'H_ALPHA_SOURCE_01_MATCHED.csv'))
    lg('master table %d obs  (HVT %d / W7 %d)'
       % (len(df), (df['system'] == 'HVT').sum(), (df['system'] == 'W7').sum()))

    si = df['s_i'].to_numpy('int64')
    di = df['dec_idx'].to_numpy('int64')          # decision session == last known
    close = g['close'].astype('float64')
    turn = g['turnover'].astype('float64') if 'turnover' in g else None
    amt = g['amount'].astype('float64')
    pct = g['pct_chg'].astype('float64')

    # ---------------- how heavy is the tail? -------------------------
    lg.sep()
    lg('A. tail shape of the observed legs (T+10 / T+20, gross)')
    for h in (10, 20):
        y = df['r_obs_%d' % h].to_numpy('float64')
        y = y[np.isfinite(y)]
        pos = y[y > 0]
        lg('  T+%d  n=%d  mean %+0.4f  median %+0.4f  win %.3f'
           % (h, y.size, y.mean(), np.median(y), float((y > 0).mean())))
        lg('        p50 %+0.4f  p75 %+0.4f  p90 %+0.4f  p95 %+0.4f  '
           'p99 %+0.4f  max %+0.4f'
           % tuple(np.percentile(y, [50, 75, 90, 95, 99, 100])))
        lg('        skew %+0.4f  excess-kurt %+0.4f'
           % (float(pd.Series(y).skew()), float(pd.Series(y).kurt())))
        for lv in (0.01, 0.05, 0.10):
            k = int(np.ceil(y.size * lv))
            top = np.sort(y)[::-1][:k]
            lg('        top %-4.0f%% (n=%4d): mean %+0.4f   share of total '
               'positive return %.1f%%'
               % (lv * 100, k, top.mean(),
                  100.0 * top.sum() / pos.sum()))

    # ---------------- ex-ante features (<= decision session) ---------
    lg.sep()
    lg('B. ex-ante features, all measured at the decision session')
    feat = {}
    feat['price_dec'] = at(close, si, di)
    feat['mom20'] = at(close, si, di) / at(close, si, di - 20) - 1.0
    feat['mom60'] = at(close, si, di) / at(close, si, di - 60) - 1.0
    feat['mom120'] = at(close, si, di) / at(close, si, di - 120) - 1.0
    rmax = roll_max(close, 120)
    feat['dist_hi120'] = at(close, si, di) / at(rmax, si, di) - 1.0
    rv = roll_std(pct, 20)
    feat['vol20'] = at(rv, si, di)
    if turn is not None:
        feat['turn20'] = at(roll_mean(turn, 20), si, di)
    ra = roll_mean(amt, 20)
    feat['amt20'] = at(ra, si, di)
    feat['vr'] = at(amt, si, di) / at(ra, si, di - 1)
    for k, v in feat.items():
        df[k] = v
    df['lag'] = df['lag'].astype('float64')
    df['top3f'] = df['top3'].astype('float64')

    lg('')
    lg('  feature coverage: ' + '  '.join(
        '%s %.3f' % (k, float(np.isfinite(df[k]).mean())) for k in feat))

    # ---------------- univariate profile ----------------------------
    lg.sep()
    lg('C. quintile profile inside the frozen candidate pool')
    tabs = []
    for col in ['mom20', 'mom60', 'mom120', 'dist_hi120', 'vol20',
                'turn20', 'vr', 'amt20', 'price_dec', 'lag']:
        if col not in df:
            continue
        t = qtab(lg, df, col, 'r_obs_20', tag='ALL')
        if t is not None:
            t['system'] = 'ALL'
            tabs.append(t)

    # ---------------- the system's own ranking ----------------------
    lg.sep()
    lg('D. does the system\'s own Top-3 flag find the tail?')
    for sy in ('ALL', 'HVT', 'W7'):
        s = df if sy == 'ALL' else df[df['system'] == sy]
        for k in (0.0, 1.0):
            z = s[s['top3f'] == k]
            y = z['r_obs_20'].to_numpy('float64')
            lg('  %-4s top3=%d  n=%4d  mean %+0.4f  median %+0.4f  win %.3f  '
               'P(r>=+10%%) %.3f  P(r>=+20%%) %.3f'
               % (sy, int(k), len(z), np.nanmean(y), np.nanmedian(y),
                  float(np.nanmean(y > 0)), float(np.nanmean(y >= 0.10)),
                  float(np.nanmean(y >= 0.20))))

    # ---------------- what the winners look like ---------------------
    lg.sep()
    lg('E. profile of the winners (top 5% of r_obs_20) vs the rest')
    y = df['r_obs_20'].to_numpy('float64')
    thr = np.nanquantile(y, 0.95)
    win = df[y >= thr]
    rest = df[y < thr]
    lg('  threshold r_obs_20 >= %+0.4f   winners n=%d (%.1f%%)'
       % (thr, len(win), 100.0 * len(win) / len(df)))
    lg('')
    lg('  %-12s %12s %12s %10s' % ('feature', 'winner', 'rest', 'ratio'))
    for col in ['mom20', 'mom60', 'mom120', 'dist_hi120', 'vol20',
                'turn20', 'vr', 'amt20', 'price_dec', 'lag']:
        if col not in df:
            continue
        a = win[col].to_numpy('float64')
        b = rest[col].to_numpy('float64')
        a, b = a[np.isfinite(a)], b[np.isfinite(b)]
        lg('  %-12s %12.4f %12.4f %10.2f'
           % (col, np.median(a), np.median(b),
              np.median(a) / np.median(b) if np.median(b) else np.nan))
    lg('')
    lg('  board mix      winners %s' % win['system'].value_counts().to_dict())
    lg('  system mix     winners %s'
       % win['system'].value_counts(normalize=True).round(3).to_dict())
    lg('  system mix     rest    %s'
       % rest['system'].value_counts(normalize=True).round(3).to_dict())
    lg('  year mix       winners %s'
       % win['year'].value_counts(normalize=True).round(3).to_dict())
    lg('  year mix       rest    %s'
       % rest['year'].value_counts(normalize=True).round(3).to_dict())

    # ---------------- persistence: does it repeat out of sample? ----
    lg.sep()
    lg('F. would the in-sample gradient have repeated? (year by year, T+20)')
    for col in ['mom20', 'mom60', 'dist_hi120', 'vol20', 'turn20', 'vr']:
        if col not in df:
            continue
        line = '  %-12s ' % col
        for yy in sorted(df['year'].unique()):
            s = df[df['year'] == yy]
            tab = qtab(None, s, col, 'r_obs_20') if False else None
            v = s[col].to_numpy('float64')
            m = np.isfinite(v)
            if m.sum() < 100:
                line += ' %d: n/a' % yy
                continue
            qs = np.quantile(v[m], [0.2, 0.8])
            lo = s[(v <= qs[0])]['r_obs_20'].mean()
            hi = s[(v >= qs[1])]['r_obs_20'].mean()
            line += '  %d lo %+0.3f hi %+0.3f (hi-lo %+0.3f)' % (
                yy, lo, hi, hi - lo)
        lg(line)

    lg.sep()
    lg('NOTE: in-sample, descriptive, N=%d with ~%d tail observations.  No '
       'threshold was tuned and no rule is proposed.  Any candidate rule '
       'must be registered under a NEW hypothesis id and pass its own OOS.'
       % (len(df), int(len(win))))
    lg('done')


if __name__ == '__main__':
    main()
