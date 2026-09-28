# -*- coding: utf-8 -*-
"""H-EARN-FWD step 6: forecast skill + null battery + incremental tests.

PROVENANCE NOTE
  The original specification text (skill section, null-model section,
  incremental section, PASS-gate section) was NOT retained in the
  workspace or in memory.  Everything below is either
    (a) literally preregistered in hef_common.PREREG -- N3 seasonal rule,
        N5 momentum rule, offsets, NW lags, min cohort size, IC floor,
        bucket widths; or
    (b) a transparently labelled reconstruction of the standard
        no-information ladder.
  Every reconstructed row carries `recon=1` and its own descriptive name;
  null indices beyond N3/N5 are explicitly marked RECON so that no reader
  can mistake them for preregistered definitions.

Contents
  A. skill table        rank IC + NW t + ICIR, AUC, hit rate, decile
                        monotonicity, top/bottom bucket spread
  B. null battery       N1 random-rank permutation (z-test)
                        N3 seasonal rule        (preregistered)
                        N5 momentum rule        (preregistered)
                        N6 industry-nowcast-only forecast
                        N7 label-shuffle refit  (integrity control)
  C. factor IC          single-feature cohort IC -> redundancy / leakage
                        diagnostic
  D. incremental        nested information blocks (nowcast -> market ->
                        company) and guidance on/off
  E. target robustness  headline model scored against the momentum-
                        expectation target instead of the seasonal one

Outputs (data/): eval_skill.parquet, eval_null.parquet,
                 eval_factor_ic.parquet, eval_incremental.parquet
"""
import os, sys, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hef_common import (DATA, PREREG, Log, spearman_ic, auc_score,
                        ic_stats)
from hef_forecast import F_NUM, mk_model, design, schemes

lg = Log('hef_eval')
SEED = int(PREREG['seed'])
BIN = {'positive_surprise': True, 'strong_surprise': True,
       'model_surprise_pct': False}


# ------------------------------------------------------------- A. skill
def cohort_metrics(gc, binary):
    y = pd.to_numeric(gc['y'], errors='coerce').to_numpy(float)
    p = pd.to_numeric(gc['pred'], errors='coerce').to_numpy(float)
    m = np.isfinite(y) & np.isfinite(p)
    y, p = y[m], p[m]
    n = len(y)
    if n < PREREG['ic_min_n']:
        return None
    ys, ps = pd.Series(y), pd.Series(p)
    d = {'ic': ys.corr(ps, method='spearman'), 'n': n}
    r = ps.rank(pct=True)
    for b in PREREG['group_buckets']:
        d['spread_%02d' % int(round(b * 100))] = float(
            ys[r >= 1 - b].mean() - ys[r <= b].mean())
    if binary:
        d['auc'] = auc_score(ys, ps)
        d['hit'] = float(((ps > 0.5) == (ys > 0.5)).mean())
    else:
        d['auc'] = np.nan
        d['hit'] = float((np.sign(ps) == np.sign(ys)).mean())
    q = pd.qcut(r, 10, labels=False, duplicates='drop')
    mm = ys.groupby(q).mean()
    d['mono'] = (spearman_ic(np.arange(len(mm)), mm.to_numpy(), min_n=5)[0]
                 if len(mm) >= 5 else np.nan)
    return d


def skill_block(P, label):
    rows = []
    for k, g in P.groupby(['target', 'offset', 'scheme', 'model'],
                          sort=False):
        tgt, off, sch, kind = k
        ds = [cohort_metrics(gc, BIN.get(tgt, False))
              for _c, gc in g.groupby('cs_key', sort=False)]
        ds = [d for d in ds if d]
        if not ds:
            continue
        D = pd.DataFrame(ds)
        mu, tt, icir, nn = ic_stats(D['ic'].tolist(),
                                    PREREG['nw_lag_by_offset'][off])
        row = {'block': label, 'target': tgt, 'offset': off, 'scheme': sch,
               'model': kind, 'n_cohorts': nn, 'ic': mu, 'ic_t': tt,
               'icir': icir, 'ic_pos_share': float((D['ic'] > 0).mean()),
               'auc': float(D['auc'].mean()), 'hit': float(D['hit'].mean()),
               'mono': float(D['mono'].mean()), 'n_pred': int(len(g))}
        for b in PREREG['group_buckets']:
            row['spread_%02d' % int(round(b * 100))] = float(
                D['spread_%02d' % int(round(b * 100))].mean())
        rows.append(row)
    return pd.DataFrame(rows)


# --------------------------------------------------- B. null battery
def null_random_rank(P, n_seed=30):
    """N1 RECON: within-cohort permutation of the headline prediction."""
    lg('--- N1 random-rank permutation (%d seeds)' % n_seed)
    sub = P[P['target'] == PREREG['target_a']]
    rows = []
    for k, g in sub.groupby(['offset', 'scheme', 'model'], sort=False):
        off, sch, kind = k
        lag = PREREG['nw_lag_by_offset'][off]
        coh = [gc for _c, gc in g.groupby('cs_key', sort=False)
               if gc['y'].notna().sum() >= PREREG['ic_min_n']]
        if len(coh) < 5:
            continue
        act = [c for c in (spearman_ic(gc['pred'], gc['y'])[0]
                           for gc in coh) if np.isfinite(c)]
        mu_act, t_act, _, _ = ic_stats(act, lag)
        rng = np.random.default_rng(SEED)
        null = []
        for _s in range(n_seed):
            ics = []
            for gc in coh:
                p = gc['pred'].to_numpy(float).copy()
                rng.shuffle(p)
                ic, _n = spearman_ic(pd.Series(p, index=gc.index), gc['y'])
                if np.isfinite(ic):
                    ics.append(ic)
            if ics:
                null.append(float(np.mean(ics)))
        N = pd.Series(null).dropna()
        z = ((mu_act - N.mean()) / N.std()) if N.std() > 0 else np.nan
        rows.append({'test': 'N1_RANDOM_RANK', 'recon': 1,
                     'target': PREREG['target_a'], 'offset': off,
                     'scheme': sch, 'model': kind, 'n_cohorts': len(coh),
                     'actual_ic': mu_act, 'actual_t': t_act,
                     'null_mean': float(N.mean()), 'null_sd': float(N.std()),
                     'null_p95': float(N.quantile(0.95)), 'z': z,
                     'notes': 'within-cohort permutation, %d seeds' % n_seed})
    return pd.DataFrame(rows)


def null_rule_rules(t, f):
    """N3 / N5 REGISTERED expectation rules, scored as competing
    forecasts of the headline target.

    The headline target measures the deviation of realised profit from the
    preregistered seasonal expectation E_sea.  A rule that simply *is* the
    seasonal expectation therefore predicts the deviation to be exactly
    zero -- degenerate by construction (all ties), which is the reason a
    real model is needed at all.  The momentum rule predicts
    (E_mom - E_sea)/den_sea, which is non-degenerate and is the honest
    naive competitor.

    Two framings are reported: FULL history (all anchor years pooled) and
    SCHEME-matched (each walk-forward test window on its own), the latter
    being the apples-to-apples comparison against the fitted models.
    """
    lg('--- N3/N5 preregistered expectation rules')
    rows = []
    d = t[t['model_surprise_pct'].notna()].copy()
    d = d.merge(f[['ts_code', 'target_end', 'offset', 'ann_year']],
                on=['ts_code', 'target_end', 'offset'], how='left')
    d['pred_N3'] = 0.0
    d['pred_N5'] = np.where(d['den_sea'] > 0,
                            (d['E_mom'] - d['E_sea']) / d['den_sea'], np.nan)
    win = [('FULL', PREREG['burnin_anchor_years'][0], 9999)]
    for s, a, b, tst in schemes():
        win.append((s, tst if tst is not None else b + 1,
                    tst if tst is not None else 9999))
    for name, col, note in (
            ('N3_SEASONAL_RW', 'pred_N3',
             'preregistered: NP[t-4]*(1+med yoy4) -> deviation = 0 '
             'by construction (degenerate, IC undefined)'),
            ('N5_MOMENTUM_RW', 'pred_N5',
             'preregistered: NP[t-4]*(1+last yoy) -> '
             '(E_mom-E_sea)/den_sea (naive competitor)')):
        for off in PREREG['prediction_offsets']:
            for tag, a, b in win:
                g = d[(d['offset'] == off) & (d['ann_year'] >= a)
                      & (d['ann_year'] <= b)]
                ics = []
                for _c, gc in g.groupby('target_end', sort=False):
                    ic, _n = spearman_ic(gc[col], gc['model_surprise_pct'])
                    if np.isfinite(ic):
                        ics.append(ic)
                mu, tt, icir, nn = ic_stats(ics,
                                            PREREG['nw_lag_by_offset'][off])
                if nn < 5 and tag != 'FULL':
                    continue
                rows.append({'test': name, 'recon': 1, 'target':
                             PREREG['target_a'], 'offset': off, 'scheme': tag,
                             'model': 'RULE', 'n_cohorts': nn, 'actual_ic': mu,
                             'actual_t': tt, 'null_mean': np.nan,
                             'null_sd': np.nan, 'null_p95': np.nan,
                             'z': np.nan, 'notes': note})
    return pd.DataFrame(rows)


def null_label_shuffle(f, y, n_seed=5):
    """N7 RECON: refit RIDGE on a permuted label -> IC must collapse."""
    lg('--- N7 label-shuffle refit (RIDGE, %d seeds)' % n_seed)
    X = design(f)
    Xv = X.to_numpy()
    rows = []
    rng = np.random.default_rng(SEED)
    for off in PREREG['prediction_offsets']:
        for scheme, a, b, test in schemes():
            tr = ((f['offset'] == off) & (f['ann_year'] >= a)
                  & (f['ann_year'] <= b) & pd.notna(y)).to_numpy()
            te = ((f['offset'] == off)
                  & ((f['ann_year'] == test) if test is not None
                     else (f['ann_year'] > b))).to_numpy()
            if tr.sum() < 200 or te.sum() < 20:
                continue
            yv = y.to_numpy()
            ics = []
            for _s in range(n_seed):
                yp = yv[tr].copy()
                rng.shuffle(yp)
                m = mk_model('RIDGE')
                m.fit(Xv[tr], yp)
                pr = m.predict(Xv[te])
                r = pd.DataFrame({'cs_key': f['target_end'].to_numpy()[te],
                                  'y': yv[te], 'pred': pr})
                for _c, gc in r.groupby('cs_key', sort=False):
                    ic, _n = spearman_ic(gc['pred'], gc['y'])
                    if np.isfinite(ic):
                        ics.append(ic)
            mu, tt, _, nn = ic_stats(ics, PREREG['nw_lag_by_offset'][off])
            rows.append({'test': 'N7_LABEL_SHUFFLE', 'recon': 1,
                         'target': PREREG['target_a'], 'offset': off,
                         'scheme': scheme, 'model': 'RIDGE',
                         'n_cohorts': nn, 'actual_ic': mu, 'actual_t': tt,
                         'null_mean': 0.0, 'null_sd': np.nan,
                         'null_p95': np.nan, 'z': np.nan,
                         'notes': 'y permuted before fit; IC must be ~0'})
    return pd.DataFrame(rows)


# --------------------------------------------------- C. factor IC
def factor_ic(f):
    lg('--- single-feature cohort IC vs headline target')
    d = f[f['model_surprise_pct'].notna()]
    rows = []
    for off in PREREG['prediction_offsets']:
        g = d[d['offset'] == off].reset_index(drop=True)
        cols = [c for c in F_NUM if c in g.columns]
        yv = pd.to_numeric(g['model_surprise_pct'],
                           errors='coerce').to_numpy(float)
        pos = [np.asarray(ix)
               for ix in g.groupby('target_end').indices.values()]
        for c in cols:
            a = pd.to_numeric(g[c], errors='coerce').to_numpy(float)
            series = []
            for ix in pos:
                x, yy = a[ix], yv[ix]
                m = np.isfinite(x) & np.isfinite(yy)
                if m.sum() < PREREG['ic_min_n']:
                    continue
                ic, _n = spearman_ic(x[m], yy[m])
                if np.isfinite(ic):
                    series.append(ic)
            mu, tt, icir, nn = ic_stats(series,
                                        PREREG['nw_lag_by_offset'][off])
            rows.append({'feature': c, 'offset': off, 'ic': mu, 'ic_t': tt,
                         'icir': icir, 'n_cohorts': nn})
    return pd.DataFrame(rows)


# --------------------------------------------------- D. incremental
BLOCKS = {
    'B_nowcast': ['ind_np_yoy_med', 'ind_n_ann', 'ind_used_q'],
    'B_nowcast_mkt': ['ind_np_yoy_med', 'ind_n_ann', 'ind_used_q',
                      'ret20', 'ret60', 'vol20', 'turnover_rate',
                      'volume_ratio', 'pe_ttm', 'pb', 'ps_ttm', 'log_mv',
                      'log_circ_mv', 'mkt_ret20', 'ind_ret20'],
    'B_no_guide': [c for c in F_NUM if not c.startswith('guide_')],
    'B_all': list(F_NUM),
}


def incremental(f, y):
    lg('--- incremental nested information blocks (TRAINFIX, RIDGE)')
    key = ['target_end', 'offset']

    def mat(cols):
        Z = f.groupby(key)[cols].rank(pct=True) - 0.5
        Z = Z.fillna(0.0).astype('float32')
        dum = pd.get_dummies(f['board'].fillna('NA'), prefix='bd',
                             dtype='float32')
        if dum.shape[1] > 1:
            dum = dum.drop(columns=dum.columns[-1])
        return pd.concat([Z, dum], axis=1).to_numpy()

    rows = []
    scheme, a, b, test = schemes()[0]
    for off in PREREG['prediction_offsets']:
        tr = ((f['offset'] == off) & (f['ann_year'] >= a)
              & (f['ann_year'] <= b) & pd.notna(y)).to_numpy()
        te = ((f['offset'] == off) & (f['ann_year'] > b)).to_numpy()
        if tr.sum() < 200 or te.sum() < 20:
            continue
        yv = y.to_numpy()
        for name, cols in BLOCKS.items():
            cols = [c for c in cols if c in f.columns]
            for kind in ('RIDGE', 'GBM'):
                Xb = mat(cols)
                m = mk_model(kind)
                m.fit(Xb[tr], yv[tr])
                pr = m.predict(Xb[te])
                r = pd.DataFrame({'cs_key': f['target_end'].to_numpy()[te],
                                  'y': yv[te], 'pred': pr})
                ics = []
                for _c, gc in r.groupby('cs_key', sort=False):
                    ic, _n = spearman_ic(gc['pred'], gc['y'])
                    if np.isfinite(ic):
                        ics.append(ic)
                mu, tt, icir, nn = ic_stats(
                    ics, PREREG['nw_lag_by_offset'][off])
                rows.append({'block': name, 'n_feat': len(cols),
                             'offset': off, 'scheme': scheme, 'model': kind,
                             'n_cohorts': nn, 'ic': mu, 'ic_t': tt,
                             'icir': icir})
    return pd.DataFrame(rows)


# --------------------------------------------------- E. target robustness
def target_robustness(P, t):
    lg('--- headline model scored on the momentum-expectation target')
    d = t[['ts_code', 'target_end', 'offset', 'surp_mom_raw']]
    sub = P[P['target'] == PREREG['target_a']].copy()
    sub = sub.merge(d, on=['ts_code', 'target_end', 'offset'], how='left')
    out = []
    for (off, sch, kind), g in sub.groupby(['offset', 'scheme', 'model'],
                                           sort=False):
        ics = []
        for _c, gc in g.groupby('cs_key', sort=False):
            ic, _n = spearman_ic(gc['pred'], gc['surp_mom_raw'])
            if np.isfinite(ic):
                ics.append(ic)
        mu, tt, icir, nn = ic_stats(ics, PREREG['nw_lag_by_offset'][off])
        out.append({'target': 'surp_mom_raw', 'offset': off, 'scheme': sch,
                    'model': kind, 'n_cohorts': nn, 'ic': mu, 'ic_t': tt,
                    'icir': icir})
    return pd.DataFrame(out)


def main():
    t0 = time.time()
    Pc = pd.read_parquet(os.path.join(DATA, 'predictions_company.parquet'))
    Pc['cs_key'] = Pc['target_end'].astype(str)
    Pl = pd.read_parquet(os.path.join(DATA, 'predictions_layers.parquet'))
    Pl['cs_key'] = Pl['cs_key'].astype(str)
    lg('predictions loaded company %d layers %d' % (len(Pc), len(Pl)))

    S = pd.concat([skill_block(Pc, 'COMPANY'), skill_block(Pl, 'LAYER')],
                  ignore_index=True)
    S.to_parquet(os.path.join(DATA, 'eval_skill.parquet'), index=False)
    lg('A. skill rows %d' % len(S))
    prim = S[(S['scheme'] == 'TRAINFIX') & (S['target'] == 'model_surprise_pct')]
    lg('TRAINFIX headline IC (rows = model, cols = offset)\n%s'
       % prim.pivot_table(index='model', columns='offset',
                          values='ic').round(4).to_string())
    best = S[(S['scheme'] == 'TRAINFIX') & (S['target'] == 'strong_surprise')]
    lg('TRAINFIX strong-surprise AUC\n%s'
       % best.pivot_table(index='model', columns='offset',
                          values='auc').round(4).to_string())

    t = pd.read_parquet(os.path.join(DATA, 'targets.parquet'))
    t['ts_code'] = t['ts_code'].astype(str)
    t['target_end'] = t['target_end'].astype(str)
    f = pd.read_parquet(os.path.join(DATA, 'features_company.parquet'))
    f['ts_code'] = f['ts_code'].astype(str)
    f['target_end'] = f['target_end'].astype(str)
    f['ann_year'] = f['ann_year'].astype(int)
    f = f.merge(t[['ts_code', 'target_end', 'offset', 'model_surprise_pct']],
                on=['ts_code', 'target_end', 'offset'], how='left')
    f['log_mv'] = np.log(pd.to_numeric(f['total_mv'], errors='coerce'))
    f['log_circ_mv'] = np.log(pd.to_numeric(f['circ_mv'], errors='coerce'))
    f['guide_present'] = (f['guide_mid'].notna()
                          & f['guide_is_target'].fillna(False).astype(bool)
                          ).astype('float64')
    y = pd.to_numeric(f['model_surprise_pct'], errors='coerce')

    Nl = [null_random_rank(Pc), null_rule_rules(t, f),
          null_label_shuffle(f, y)]
    N = pd.concat(Nl, ignore_index=True)
    N.to_parquet(os.path.join(DATA, 'eval_null.parquet'), index=False)
    lg('B. null rows %d; N1 z-scores:\n%s'
       % (len(N), N[N['test'] == 'N1_RANDOM_RANK']
          .pivot_table(index=['scheme', 'model'], columns='offset',
                       values='z').round(2).to_string()))

    FI = factor_ic(f)
    FI.to_parquet(os.path.join(DATA, 'eval_factor_ic.parquet'), index=False)
    tp = FI[FI['offset'] == PREREG['primary_offset']].nlargest(8, 'ic')
    lg('C. top single-feature IC at offset %d:\n%s'
       % (PREREG['primary_offset'],
          tp[['feature', 'ic', 'ic_t']].round(4).to_string(index=False)))

    I = incremental(f, y)
    I.to_parquet(os.path.join(DATA, 'eval_incremental.parquet'), index=False)
    lg('D. incremental blocks (RIDGE, TRAINFIX):\n%s'
       % I[I['model'] == 'RIDGE'].pivot_table(
           index='block', columns='offset', values='ic').round(4).to_string())

    R = target_robustness(Pc, t)
    R.to_parquet(os.path.join(DATA, 'eval_target_robust.parquet'), index=False)
    lg('E. target robustness (momentum-expectation target), TRAINFIX:\n%s'
       % R[R['scheme'] == 'TRAINFIX'].pivot_table(
           index='model', columns='offset', values='ic').round(4).to_string())
    lg('DONE eval in %.1fs' % (time.time() - t0))


if __name__ == '__main__':
    main()
