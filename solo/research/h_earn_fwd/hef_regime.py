# -*- coding: utf-8 -*-
"""H-EARN-FWD step 7: regime stability + parameter stability.

Regime stability
  rank IC / AUC of the headline forecast broken out by anchor year
  (2019..2026) and by preregistered phase (TRAIN / VALID / OOS /
  LIVE-LIKE), separately per offset and model.  A forecast rule that
  only works in one regime is not a forecast rule.

Parameter stability
  the preregistered hyper-parameters are perturbed one at a time on the
  TRAINFIX scheme and the resulting IC is compared with the headline
  setting.  Stability is evidence against a knife-edge / over-fitted
  configuration.  Ladder: ridge alpha {1,10,100}; LightGBM depth
  {2,3,5} x learning rate {0.03,0.05,0.10}; the PREREG value is marked.

NOT RUN (declared, not faked)
  the offset neighbourhood (45, 10) requires a feature snapshot at those
  horizons; only P60/P30/P20 were built, so the neighbourhood test is
  reported as NOT_BUILT rather than estimated.

Outputs (data/): eval_regime.parquet, eval_param_stability.parquet
"""
import os, sys, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hef_common import DATA, PREREG, Log, spearman_ic, auc_score, ic_stats
from hef_forecast import F_NUM, mk_model, design, schemes

lg = Log('hef_regime')
SEED = int(PREREG['seed'])
LAG = PREREG['nw_lag_by_offset']


def prep():
    f = pd.read_parquet(os.path.join(DATA, 'features_company.parquet'))
    t = pd.read_parquet(os.path.join(DATA, 'targets.parquet'))
    for d in (f, t):
        d['ts_code'] = d['ts_code'].astype(str)
        d['target_end'] = d['target_end'].astype(str)
    f['ann_year'] = f['ann_year'].astype(int)
    ycols = ['ts_code', 'target_end', 'offset', 'model_surprise_pct',
             'positive_surprise', 'strong_surprise']
    f = f.merge(t[ycols], on=['ts_code', 'target_end', 'offset'], how='left')
    f['log_mv'] = np.log(pd.to_numeric(f['total_mv'], errors='coerce'))
    f['log_circ_mv'] = np.log(pd.to_numeric(f['circ_mv'], errors='coerce'))
    f['guide_present'] = (f['guide_mid'].notna()
                          & f['guide_is_target'].fillna(False).astype(bool)
                          ).astype('float64')
    return f


def year_ic(P):
    rows = []
    for (tgt, off, sch, kind, yr), g in P.groupby(
            ['target', 'offset', 'scheme', 'model', 'ann_year'], sort=False):
        bin_ = tgt != 'model_surprise_pct'
        ics, aucs = [], []
        for _c, gc in g.groupby('target_end', sort=False):
            ic, _n = spearman_ic(gc['pred'], gc['y'])
            if np.isfinite(ic):
                ics.append(ic)
            if bin_:
                a = auc_score(gc['y'], gc['pred'])
                if np.isfinite(a):
                    aucs.append(a)
        mu, tt, icir, nn = ic_stats(ics, LAG[off])
        rows.append({'target': tgt, 'offset': off, 'scheme': sch,
                     'model': kind, 'ann_year': int(yr), 'n_cohorts': nn,
                     'ic': mu, 'ic_t': tt, 'icir': icir,
                     'auc': float(np.mean(aucs)) if aucs else np.nan,
                     'n_pred': int(len(g))})
    return pd.DataFrame(rows)


def param_stability(f, y):
    lg('--- parameter stability (TRAINFIX, headline target)')
    from sklearn.linear_model import Ridge
    import lightgbm as lgb
    X = design(f)
    Xv = X.to_numpy()
    yv = y.to_numpy()
    scheme, a, b, test = schemes()[0]
    grid = []
    for al in (1.0, 10.0, 100.0):
        grid.append(('RIDGE alpha=%g' % al, 'RIDGE',
                     Ridge(alpha=al), al == PREREG['ridge_alpha']))
    g0 = PREREG['reg_gbm']
    for dep in (2, 3, 5):
        for lr in (0.03, 0.05, 0.10):
            kw = dict(n_estimators=int(g0['n_estimators']), max_depth=dep,
                      learning_rate=lr,
                      min_child_samples=int(g0['min_samples_leaf']),
                      subsample=float(g0['subsample']), subsample_freq=1,
                      num_leaves=2 ** dep, verbosity=-1, random_state=SEED,
                      deterministic=True, n_jobs=-1)
            grid.append(('GBM depth=%d lr=%.2f' % (dep, lr), 'GBM',
                         lgb.LGBMRegressor(**kw),
                         dep == g0['max_depth'] and lr == g0['learning_rate']))
    rows = []
    for off in PREREG['prediction_offsets']:
        tr = ((f['offset'] == off) & (f['ann_year'] >= a)
              & (f['ann_year'] <= b) & pd.notna(y)).to_numpy()
        te = ((f['offset'] == off) & (f['ann_year'] > b)).to_numpy()
        for name, fam, mdl, is_prereg in grid:
            mdl.fit(Xv[tr], yv[tr])
            r = pd.DataFrame({'cs_key': f['target_end'].to_numpy()[te],
                              'y': yv[te], 'pred': mdl.predict(Xv[te])})
            ics = []
            for _c, gc in r.groupby('cs_key', sort=False):
                ic, _n = spearman_ic(gc['pred'], gc['y'])
                if np.isfinite(ic):
                    ics.append(ic)
            mu, tt, icir, nn = ic_stats(ics, LAG[off])
            rows.append({'family': fam, 'config': name, 'offset': off,
                         'scheme': scheme, 'is_prereg': int(is_prereg),
                         'n_cohorts': nn, 'ic': mu, 'ic_t': tt, 'icir': icir})
    return pd.DataFrame(rows)


def main():
    t0 = time.time()
    f = prep()
    y = pd.to_numeric(f['model_surprise_pct'], errors='coerce')
    Pc = pd.read_parquet(os.path.join(DATA, 'predictions_company.parquet'))
    Pl = pd.read_parquet(os.path.join(DATA, 'predictions_layers.parquet'))
    Pl['ann_year'] = Pl['ann_year'].astype(int)
    P = pd.concat([Pc, Pl], ignore_index=True)

    R = year_ic(P)
    R.to_parquet(os.path.join(DATA, 'eval_regime.parquet'), index=False)
    head = R[(R['target'] == 'model_surprise_pct')
             & (R['scheme'] == 'TRAINFIX')]
    lg('regime rows %d; TRAINFIX headline IC by anchor year:\n%s'
       % (len(R), head.pivot_table(index='model', columns='ann_year',
                                   values='ic').round(4).to_string()))
    strong = R[(R['target'] == 'strong_surprise') & (R['scheme'] == 'TRAINFIX')]
    lg('TRAINFIX strong-surprise AUC by anchor year:\n%s'
       % strong.pivot_table(index='model', columns='ann_year',
                            values='auc').round(4).to_string())

    S = param_stability(f, y)
    S.to_parquet(os.path.join(DATA, 'eval_param_stability.parquet'),
                 index=False)
    lg('parameter stability rows %d:\n%s'
       % (len(S), S.pivot_table(index=['family', 'config'], columns='offset',
                                values='ic').round(4).to_string()))
    lg('NOT_BUILT: offset neighbourhood (45,10) -- feature snapshots exist '
       'only at P60/P30/P20; test declared, not estimated.')
    lg('DONE regime in %.1fs' % (time.time() - t0))


if __name__ == '__main__':
    main()
