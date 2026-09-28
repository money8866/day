# -*- coding: utf-8 -*-
"""H-EARN-FWD step 3: define the three surprise targets.

Unit: (ts_code, target_end, offset) -- one row per features_company row that
has a usable actual result.

Consensus is UNAVAILABLE (report_rc is FY-annual only), so per PREREG the
headline expectation is a pre-registered *model expectation* and the target
is MODEL_SURPRISE:

    E_sea(T)  = NP[T-4] * (1 + med_yoy4_at_P)      # = null model N3
    E_mom(T)  = NP[T-4] * (1 + yoy_at_P)           # = null model N5
    denom     = max(|E|, 0.20 * med|NP| last 8q at P)
    MODEL_SURPRISE = (NP[T] - E) / denom

The expectation is offset specific: a P60 information set differs from P20,
so the same quarter can carry different surprises per offset.  That is the
standard PEAD-style framing (surprise vs the expectation prevailing at P).

Targets
  A  model_surprise_pct  continuous MODEL_SURPRISE (E_sea), winsorised on
     TRAIN-phase quantiles only (OOS/VALID untouched by any future bound)
  B  positive_surprise   MODEL_SURPRISE > 0
  C  strong_surprise     MODEL_SURPRISE >= 75th pct of its (T, offset) cohort
                         (cohort needs >= PREREG min_xs names)

Aux (clearly labelled, never the main target)
  surp_guide  = (NP[T] - guide_mid) / max(|guide_mid|, floor*med|NP|)
  surp_mom    = (NP[T] - E_mom)   / denom_mom      (N5 benchmark variant)

Actuals use the FIRST announcement version (as reported to the market); later
restatements cover 0.5% of (ts,end) and are deliberately ignored here.
"""
import os, sys, json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hef_common import (DATA, PREREG, Log, q_add, winsorize)

lg = Log('hef_build_targets')


def main():
    feats = os.path.join(DATA, 'features_company.parquet')
    vers = os.path.join(DATA, 'quarterly_versions.parquet')
    lg('PREREG target_a=%s target_b=%s target_c=%s strong_q=%s floor=%s'
       % (PREREG['target_a'], PREREG['target_b'], PREREG['target_c'],
          PREREG['strong_q'], PREREG['surprise_denom_floor']))

    keep = ['ts_code', 'target_end', 'offset', 'p_date', 'phase', 'np_yoy',
            'np_yoy_med4', 'np_absmed8', 'guide_mid', 'guide_is_target',
            'board', 'sw_l1']
    f = pd.read_parquet(feats, columns=keep)
    f['ts_code'] = f['ts_code'].astype(str)
    f['target_end'] = f['target_end'].astype(str)
    lg('features rows %d' % len(f))

    v = pd.read_parquet(vers, columns=['ts_code', 'end_date', 'ann', 'np_sq'])
    v['ts_code'] = v['ts_code'].astype(str)
    v['end_date'] = v['end_date'].astype(str)
    v = v.dropna(subset=['np_sq', 'ann'])
    v = v.sort_values(['ts_code', 'end_date', 'ann'])
    first = v.drop_duplicates(subset=['ts_code', 'end_date'], keep='first')
    lg('versions %d -> first-announcement (ts,end) %d'
       % (len(v), len(first)))
    act = pd.Series(first['np_sq'].to_numpy(dtype='float64'),
                    index=pd.MultiIndex.from_arrays(
                        [first['ts_code'], first['end_date']]))

    keys = pd.MultiIndex.from_arrays([f['ts_code'], f['target_end']])
    f['np_T'] = act.reindex(keys).to_numpy()
    e4 = f['target_end'].map({e: q_add(e, -4) for e in f['target_end'].unique()})
    keys4 = pd.MultiIndex.from_arrays([f['ts_code'], e4.to_numpy()])
    f['np_T4'] = act.reindex(keys4).to_numpy()
    lg('actual NP[T] coverage %.3f, NP[T-4] %.3f'
       % (f['np_T'].notna().mean(), f['np_T4'].notna().mean()))

    floor = float(PREREG['surprise_denom_floor'])
    absm = pd.to_numeric(f['np_absmed8'], errors='coerce')
    for tag, yoy in (('sea', pd.to_numeric(f['np_yoy_med4'], errors='coerce')),
                     ('mom', pd.to_numeric(f['np_yoy'], errors='coerce'))):
        E = f['np_T4'] * (1.0 + yoy)
        den = np.maximum(E.abs(), floor * absm.fillna(0.0))
        den = den.where(den > 0, np.nan)
        f['E_%s' % tag] = E.where(E.notna() & den.notna())
        f['den_%s' % tag] = den
        f['surp_%s_raw' % tag] = (f['np_T'] - E) / den
        lg('expectation %s: E cov %.3f, surprise cov %.3f'
           % (tag, float(f['E_%s' % tag].notna().mean()),
              float(f['surp_%s_raw' % tag].notna().mean())))

    # ---- Target A: continuous, winsorised on TRAIN bounds only ----------
    base = f['surp_sea_raw'].notna()
    tr = base & (f['phase'] == 'TRAIN')
    lo, hi = PREREG['winsor']
    t_lo = float(f.loc[tr, 'surp_sea_raw'].quantile(lo))
    t_hi = float(f.loc[tr, 'surp_sea_raw'].quantile(hi))
    f['model_surprise_pct'] = f['surp_sea_raw'].clip(t_lo, t_hi)
    f.loc[~base, 'model_surprise_pct'] = np.nan
    lg('TRAIN winsor bounds for target A: %.4f / %.4f (n_train %d)'
       % (t_lo, t_hi, int(tr.sum())))

    # ---- Target B: sign -------------------------------------------------
    f['positive_surprise'] = (f['surp_sea_raw'] > 0).where(base)

    # ---- Target C: cohort top quartile ----------------------------------
    q = float(PREREG['strong_q'])
    minn = int(PREREG['min_xs'])
    grp = f.groupby(['target_end', 'offset'])['surp_sea_raw']
    q75 = grp.quantile(q)
    cnt = grp.count()
    idx = pd.MultiIndex.from_arrays([f['target_end'], f['offset']])
    f['surp_pct_rank'] = grp.rank(pct=True)
    big = (cnt.reindex(idx).to_numpy() >= minn) & base
    f['strong_surprise'] = ((f['surp_sea_raw'] >= q75.reindex(idx).to_numpy())
                            & big).astype('float64').where(big)
    lg('cohorts thinner than min_xs=%d: %d (%d labelled rows unlabelled)'
       % (minn, int((cnt < minn).sum()), int((base & ~big).sum())))
    lg('cohort size (T,offset): %s'
       % {k: round(float(x), 1)
          for k, x in cnt.describe(percentiles=[.05, .5]).items()})

    # ---- aux: guidance-implied surprise ---------------------------------
    gm = pd.to_numeric(f['guide_mid'], errors='coerce')
    okg = f['guide_is_target'].fillna(False).astype(bool) & gm.notna() & base
    dg = np.maximum(gm.abs(), floor * absm.fillna(0.0)).where(lambda s: s > 0)
    f['surp_guide'] = ((f['np_T'] - gm) / dg).where(okg)
    lg('aux guide surprise coverage %.3f' % float(f['surp_guide'].notna().mean()))

    out_cols = ['ts_code', 'target_end', 'offset', 'p_date', 'phase', 'board',
                'sw_l1', 'np_T', 'np_T4', 'E_sea', 'E_mom', 'den_sea',
                'den_mom', 'surp_sea_raw', 'surp_mom_raw', 'surp_guide',
                'model_surprise_pct', 'positive_surprise', 'strong_surprise',
                'surp_pct_rank']
    t = f[out_cols].copy()
    t.to_parquet(os.path.join(DATA, 'targets.parquet'), index=False)
    lg('saved targets.parquet rows %d cols %d' % (len(t), t.shape[1]))

    lab = base & f['np_T'].notna()
    lg('labelled rows %d (%.3f of features)' % (int(lab.sum()),
                                                float(lab.mean())))
    lg('target A  %s' % json.dumps({k: round(float(x), 4) for k, x in
                                    f.loc[lab, 'model_surprise_pct']
                                    .describe(percentiles=[.01, .25, .5, .75,
                                                           .99]).items()}))
    lg('target B  positive rate %.4f (n %d)'
       % (float(f.loc[lab, 'positive_surprise'].mean()), int(lab.sum())))
    lg('target C  strong rate %.4f'
       % float(f.loc[lab, 'strong_surprise'].mean()))
    for c in ('model_surprise_pct', 'positive_surprise', 'strong_surprise'):
        s = f.loc[lab].groupby('phase')[c].agg(['mean', 'count'])
        lg('%s by phase:\n%s' % (c, s.to_string()))
    for c in ('model_surprise_pct', 'positive_surprise', 'strong_surprise'):
        s = f.loc[lab].groupby('offset')[c].agg(['mean', 'count'])
        lg('%s by offset:\n%s' % (c, s.to_string()))
    lg('surp vs yoy_med4 corr %.4f; surp vs np_yoy corr %.4f'
       % (float(f.loc[lab, 'surp_sea_raw'].corr(
           pd.to_numeric(f.loc[lab, 'np_yoy_med4'], errors='coerce'))),
          float(f.loc[lab, 'surp_sea_raw'].corr(
              pd.to_numeric(f.loc[lab, 'np_yoy'], errors='coerce')))))
    lg('DONE targets')


if __name__ == '__main__':
    main()
