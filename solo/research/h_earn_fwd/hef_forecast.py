# -*- coding: utf-8 -*-
"""H-EARN-FWD step 4: company-level forecast models -> predictions.

Design matrix
  every PIT numeric feature is converted to its cross-sectional percentile
  rank *within the (target_end, offset) cohort*, centred on 0 (missing -> 0).
  A cohort is the natural prediction unit: all names report the same quarter
  at the same horizon, so the transform is scale-free, outlier-proof and
  time-neutral.  It uses peer FEATURE values only, never the target; peers
  inside one cohort differ in P by at most the spread of announcement dates.
  Dummies (board, SW-L1 fixed effects) are kept raw; quarter/offset/phase are
  constant inside a cohort and therefore carry no cross-sectional information.

Models (PREREG)
  continuous target A : RIDGE, GBM
  binary targets B, C : LOGIT, GBM_CLS, RF
  GBM is implemented with LightGBM (params mapped from PREREG reg_gbm:
  300 trees / depth 3 / lr 0.05 / min_child_samples 40 / subsample 0.8);
  sklearn's GradientBoosting takes ~5 min per fit on this host, LightGBM
  fits the identical specification in about a second.

Schemes
  TRAINFIX  train 2019-2022, predict 2023-2026 (preregistered headline split)
  W1..W6    preregistered rolling 2-year windows, test the following year
"""
import os, sys, json, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hef_common import DATA, PREREG, Log, spearman_ic, auc_score, ic_stats

lg = Log('hef_forecast')
SEED = int(PREREG['seed'])

F_NUM = [
    # company growth / quality (PIT, scale-free)
    'np_yoy', 'rev_yoy', 'dp_yoy', 'np_qoq', 'rev_qoq',
    'np_yoy_med4', 'np_yoy_med8', 'rev_yoy_med4',
    'gm_trend', 'L_gross_margin',
    'ta_yoy', 'ar_yoy', 'inv_yoy', 'fix_yoy',
    'accrual_ar', 'accrual_inv', 'ocf_np', 'ar_to_rev', 'goodwill_ta',
    'gm_lag1', 'gm_lag2', 'gm_lag3', 'gm_lag4',
    'yoy_np_lag1', 'yoy_np_lag2', 'yoy_np_lag3',
    'yoy_rev_lag1', 'yoy_rev_lag2', 'yoy_rev_lag3',
    'ann_delay', 'staleness_days',
    # market
    'ret20', 'ret60', 'vol20', 'turnover_rate', 'volume_ratio',
    'pe_ttm', 'pb', 'ps_ttm', 'log_mv', 'log_circ_mv',
    'mkt_ret20', 'ind_ret20',
    # industry nowcast (SW-L1 peers already public at P)
    'ind_np_yoy_med', 'ind_n_ann', 'ind_used_q',
    # pre-announcement guidance
    'guide_to_mv', 'guide_gap_td', 'guide_present',
    # listing
    'listed_days',
]
MODELS = {'model_surprise_pct': ('RIDGE', 'GBM'),
          'positive_surprise': ('LOGIT', 'GBM_CLS', 'RF'),
          'strong_surprise': ('LOGIT', 'GBM_CLS', 'RF')}


def mk_model(kind):
    if kind == 'RIDGE':
        from sklearn.linear_model import Ridge
        return Ridge(alpha=float(PREREG['ridge_alpha']))
    if kind == 'LOGIT':
        from sklearn.linear_model import LogisticRegression
        return LogisticRegression(C=1.0, max_iter=2000)
    if kind in ('GBM', 'GBM_CLS'):
        import lightgbm as lgb
        g = dict(PREREG['reg_gbm'])
        kw = dict(n_estimators=int(g['n_estimators']),
                  max_depth=int(g['max_depth']),
                  learning_rate=float(g['learning_rate']),
                  min_child_samples=int(g['min_samples_leaf']),
                  subsample=float(g['subsample']), subsample_freq=1,
                  num_leaves=2 ** int(g['max_depth']), verbosity=-1,
                  random_state=SEED, deterministic=True, n_jobs=-1)
        return (lgb.LGBMClassifier(**kw) if kind == 'GBM_CLS'
                else lgb.LGBMRegressor(**kw))
    if kind == 'RF':
        from sklearn.ensemble import RandomForestClassifier
        return RandomForestClassifier(n_estimators=150, max_depth=8,
                                      min_samples_leaf=40, n_jobs=-1,
                                      random_state=SEED)
    raise ValueError(kind)


def design(f):
    """rank-transform numeric features inside (target_end, offset) cohorts."""
    key = ['target_end', 'offset']
    X = f.groupby(key)[F_NUM].rank(pct=True) - 0.5
    X = X.fillna(0.0).astype('float32')
    dum = pd.get_dummies(f['board'].fillna('NA'), prefix='bd', dtype='float32')
    if dum.shape[1] > 1:
        dum = dum.drop(columns=dum.columns[-1])
    ind = pd.get_dummies(f['sw_l1'].fillna('UNKNOWN'), prefix='sw',
                         dtype='float32')
    if ind.shape[1] > 1:
        ind = ind.drop(columns=ind.columns[-1])
    X = pd.concat([X, dum, ind], axis=1)
    return X


def schemes():
    out = [('TRAINFIX', 2019, 2022, None)]
    for tag, a, b, t in PREREG['walkforward']:
        out.append((tag, int(a), int(b), int(t)))
    return out


def main():
    f = pd.read_parquet(os.path.join(DATA, 'features_company.parquet'))
    t = pd.read_parquet(os.path.join(DATA, 'targets.parquet'))
    f['ts_code'] = f['ts_code'].astype(str)
    f['target_end'] = f['target_end'].astype(str)
    f['ann_year'] = f['ann_year'].astype(int)
    t['ts_code'] = t['ts_code'].astype(str)
    t['target_end'] = t['target_end'].astype(str)
    ycols = ['ts_code', 'target_end', 'offset', 'np_T'] + list(MODELS.keys())
    f = f.merge(t[ycols], on=['ts_code', 'target_end', 'offset'], how='left')
    lg('rows %d, labelled A %.3f' % (len(f), f['model_surprise_pct'].notna().mean()))

    f['log_mv'] = np.log(pd.to_numeric(f['total_mv'], errors='coerce'))
    f['log_circ_mv'] = np.log(pd.to_numeric(f['circ_mv'], errors='coerce'))
    f['guide_present'] = (f['guide_mid'].notna()
                          & f['guide_is_target'].fillna(False).astype(bool)
                          ).astype('float64')
    miss = [c for c in F_NUM if c not in f.columns]
    assert not miss, 'missing features: %s' % miss
    X = design(f)
    lg('design matrix %d x %d' % X.shape)

    Xv = X.to_numpy()
    out = []
    t0 = time.time()
    for off in PREREG['prediction_offsets']:
        for scheme, a, b, test in schemes():
            for tgt, kinds in MODELS.items():
                yv = pd.to_numeric(f[tgt], errors='coerce').to_numpy()
                tr = ((f['offset'] == off) & (f['ann_year'] >= a)
                      & (f['ann_year'] <= b) & pd.notna(yv)).to_numpy()
                te = ((f['offset'] == off)
                      & ((f['ann_year'] == test) if test is not None
                         else (f['ann_year'] > b))).to_numpy()
                if tr.sum() < 200 or te.sum() < 20:
                    continue
                for kind in kinds:
                    m = mk_model(kind)
                    m.fit(Xv[tr], yv[tr])
                    pr = (m.predict_proba(Xv[te])[:, 1] if kind in
                          ('LOGIT', 'GBM_CLS', 'RF') else m.predict(Xv[te]))
                    r = f.loc[te, ['ts_code', 'target_end', 'offset',
                                   'ann_year', 'phase']].copy()
                    r['scheme'] = scheme
                    r['model'] = kind
                    r['target'] = tgt
                    r['y'] = yv[te]
                    r['pred'] = pr
                    out.append(r)
    lg('%d fit/predict blocks in %.1fs' % (len(out), time.time() - t0))
    P = pd.concat(out, ignore_index=True)
    P.to_parquet(os.path.join(DATA, 'predictions_company.parquet'), index=False)
    lg('saved predictions_company.parquet rows %d' % len(P))

    # ---- compact readout: per-cohort skill -------------------------------
    rows = []
    for (tgt, off, sch, kind), g in P.groupby(['target', 'offset', 'scheme',
                                               'model'], sort=False):
        ics, aucs = [], []
        for _k, gc in g.groupby('target_end', sort=False):
            if gc['y'].notna().sum() < PREREG['ic_min_n']:
                continue
            ic, _n = spearman_ic(gc['pred'], gc['y'])
            if np.isfinite(ic):
                ics.append(ic)
            if tgt != 'model_surprise_pct':
                a_ = auc_score(gc['y'], gc['pred'])
                if np.isfinite(a_):
                    aucs.append(a_)
        mu, tt, icir, n = ic_stats(ics, PREREG['nw_lag_by_offset'][off])
        rows.append({'target': tgt, 'offset': off, 'scheme': sch, 'model': kind,
                     'n_cohorts': n, 'ic': mu, 'ic_t': tt, 'icir': icir,
                     'auc': float(np.mean(aucs)) if aucs else np.nan,
                     'n_pred': len(g)})
    R = pd.DataFrame(rows)
    R.to_parquet(os.path.join(DATA, 'predictions_company_skill.parquet'),
                 index=False)
    lg('skill table rows %d' % len(R))
    for sch in R['scheme'].unique():
        sub = R[R['scheme'] == sch]
        lg('--- %s: mean IC by target/model/offset\n%s'
           % (sch, sub.pivot_table(index=['target', 'model'], columns='offset',
                                   values='ic').round(4).to_string()))
    lg('OOS/live phases present: %s'
       % P.groupby('phase').size().to_dict())
    lg('DONE forecast')


if __name__ == '__main__':
    main()
