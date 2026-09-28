# -*- coding: utf-8 -*-
"""H-EARN-FWD step 8: point-in-time leakage audit + data coverage ledger.

Column semantics that the gates depend on
  p_date        the prediction date P
  last_end      end_date of the most recent report already public at P
  last_ann      first announcement date of that report   (must be <= P)
  h_first_ann   first announcement of last_end's history row -- i.e. the
                SAME report as last_ann; it is NOT the target report.
  ann_lagk      first announcement date of the k-th report back in history
  guide_ann     announcement date of the visible earnings pre-announcement
  stale_td      trading days between the market snapshot and P

Gates (a violation in a PIT gate invalidates the study)
  L1  last visible report already public          last_ann   <= P
  L2  lag-k value only where its own ann public   ann_lagk   <= P
  L3  guidance already public                     guide_ann  <= P
  L4  market snapshot fresh enough               stale_td   <= 10 td
  L5  unique (ts_code, target_end, offset)
  L6  TARGET report strictly after P              ann_target >  P
      (the core no-look-ahead invariant: the number we are asked to
       forecast must not have been published when the features were cut)
  L7  target actual is never a feature            np_T not in the design

Outputs (data/): leakage_audit.csv / .parquet, coverage_ledger.csv
"""
import os, sys, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hef_common import DATA, PREREG, Log

lg = Log('hef_audit')


def main():
    t0 = time.time()
    f = pd.read_parquet(os.path.join(DATA, 'features_company.parquet'))
    f['ts_code'] = f['ts_code'].astype(str)
    n = len(f)
    num = lambda s: pd.to_numeric(s, errors='coerce')
    lg('features rows %d cols %d' % (n, f.shape[1]))
    res = []

    def gate(code, desc, ev, bad, tol=0.0, note=''):
        res.append({'gate': code, 'check': desc, 'n_rows': int(n),
                    'n_evaluated': int(ev), 'n_violation': int(bad),
                    'violation_rate': (round(float(bad) / ev, 8) if ev
                                       else np.nan),
                    'tolerance': tol, 'pass': int(bad <= tol * ev
                                                  if ev else 1),
                    'note': note})

    # L1 last visible report already public
    la, pd_ = num(f['last_ann']), f['_pd']
    gate('L1', 'last visible report public (last_ann <= p_date)',
         int(la.notna().sum()), int((la > pd_).sum()),
         note='Layer-1 as-of join; also asserted at build time')

    # L2 lag-k values gated by their own announcement
    ev = bad = 0
    for k in range(1, 9):
        ac = 'ann_lag%d' % k
        if ac not in f.columns:
            continue
        a = num(f[ac])
        for vc in [c for c in ('np_sq_lag%d' % k, 'rev_sq_lag%d' % k,
                               'dp_sq_lag%d' % k, 'yoy_np_lag%d' % k,
                               'yoy_rev_lag%d' % k, 'gm_lag%d' % k)
                   if c in f.columns]:
            has = num(f[vc]).notna()
            ok = a.notna()
            ev += int((has & ok).sum())
            bad += int((has & ok & (a > pd_)).sum())
    gate('L2', 'lag-k value only where ann_lagk <= p_date', ev, bad,
         note='np/rev/dp/yoy/gm lag values, k=1..8')

    # L3 guidance already public
    ga = num(f['guide_ann'].replace('', np.nan))
    gm = num(f['guide_mid'])
    gate('L3', 'guidance public (guide_ann <= p_date)',
         int((gm.notna() & ga.notna()).sum()),
         int((gm.notna() & ga.notna() & (ga > pd_)).sum()),
         note='pre-announcement is the strongest single input; '
              'published before P only')

    # L4 market staleness (quality gate, small tolerance declared)
    st = num(f['stale_td'])
    gate('L4', 'market snapshot <= 10 trading days stale',
         int(st.notna().sum()), int((st > 10).sum()), tol=0.001,
         note='tolerance 0.1%% of rows; as-of panel, never forward-filled')

    # L5 key uniqueness
    gate('L5', 'unique (ts_code, target_end, offset)', n,
         int(f.duplicated(['ts_code', 'target_end', 'offset']).sum()))

    # L6 target report strictly after P
    v = pd.read_parquet(os.path.join(DATA, 'quarterly_versions.parquet'),
                        columns=['ts_code', 'end_date', 'ann'])
    v['ts_code'] = v['ts_code'].astype(str)
    v['end_date'] = v['end_date'].astype(str)
    v['_a'] = num(v['ann'])
    v = (v.dropna(subset=['_a'])
         .sort_values(['ts_code', 'end_date', '_a'])
         .drop_duplicates(['ts_code', 'end_date'], keep='first'))
    amap = pd.Series(v['_a'].to_numpy(),
                     index=pd.MultiIndex.from_arrays([v['ts_code'],
                                                      v['end_date']]))
    atgt = amap.reindex(pd.MultiIndex.from_arrays(
        [f['ts_code'], f['target_end']])).to_numpy()
    okm = np.isfinite(atgt) & np.isfinite(pd_.to_numpy(float))
    gate('L6', 'target report announced after P (ann_target > p_date)',
         int(okm.sum()), int((okm & (atgt <= pd_.to_numpy(float))).sum()),
         note='CORE no-look-ahead invariant; P is defined as target '
              'announcement minus the offset in trading days')

    # L7 the target actual is not in the design matrix
    from hef_forecast import F_NUM
    leaked = [c for c in F_NUM if c in ('np_T', 'E_sea', 'den_sea',
                                        'surp_sea_raw', 'model_surprise_pct',
                                        'positive_surprise', 'strong_surprise')]
    gate('L7', 'target actual / target-derived column not in design',
         len(F_NUM), len(leaked),
         note='design matrix is built from F_NUM only; %d features'
              % len(F_NUM))

    A = pd.DataFrame(res)
    A.to_csv(os.path.join(DATA, 'leakage_audit.csv'), index=False)
    A.to_parquet(os.path.join(DATA, 'leakage_audit.parquet'), index=False)
    lg('leakage audit:\n%s'
       % A[['gate', 'check', 'n_evaluated', 'n_violation',
            'violation_rate', 'pass']].to_string(index=False))
    lg('ALL PIT GATES PASS: %s' % bool(A['pass'].all()))

    cov_spots = ['L_np_sq', 'np_yoy', 'np_yoy_med8', 'rev_yoy', 'gm_trend',
                 'ocf_np', 'accrual_ar', 'ret20', 'ret60', 'vol20',
                 'turnover_rate', 'pe_ttm', 'pb', 'total_mv', 'mkt_ret20',
                 'ind_ret20', 'ind_np_yoy_med', 'ind_n_ann', 'guide_mid',
                 'guide_to_mv', 'listed_days', 'sw_l1']
    rows = []
    for c in cov_spots:
        if c not in f.columns:
            continue
        s = f[c]
        rows.append({'field': c,
                     'coverage': round(float(pd.notna(s).mean()), 4),
                     'non_null': int(pd.notna(s).sum()),
                     'n_unique': int(s.nunique(dropna=True))})
    C = pd.DataFrame(rows)
    C.to_csv(os.path.join(DATA, 'coverage_ledger.csv'), index=False)
    lg('coverage ledger:\n%s' % C.to_string(index=False))
    lg('DONE audit in %.1fs' % (time.time() - t0))


if __name__ == '__main__':
    main()
