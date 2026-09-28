# -*- coding: utf-8 -*-
"""H-EARN-FWD step 5: industry layer (I1/I2/I3) + company Revenue x Margin.

Two sister experiments on top of the company-layer headline:

A. INDUSTRY LAYER -- can a whole SW-L1 industry's next-quarter surprise be
   forecast?  Cohort = (SW-L1, target_end, offset); the industry target is the
   cross-sectional mean of company MODEL_SURPRISE in that industry-quarter.
   Feature ladders (interpretable, nested):
     I1  peer nowcast only (ind_np_yoy_med, ind_n_ann, ind_used_q)
     I2  I1 + industry aggregates of member fundamentals at P
     I3  I2 + market state (index cross-section breadth/dispersion at P)
   Aggregation over members uses each member's PIT feature value; only the
   PIT-clean numeric block is aggregated (never actuals).

B. REVENUE x MARGIN -- structural decomposition of the profit surprise:
     R1  forecast next-quarter revenue growth   g_rev(T) = rev(T)/rev(T-4)-1
     M1  forecast next-quarter net margin       m(T)     = NP(T)/rev(T)
     P1  implied profit = rev(T-4)*(1+g_hat)*m_hat, scored against the same
         preregistered expectation/denominator as the headline target.
   P1 is a *derived* forecast (no separate fit); it answers whether the
   structural path beats the direct surprise model.

Same design matrix, schemes and offsets as hef_forecast.py.
"""
import os, sys, json, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hef_common import DATA, PREREG, Log, q_add, spearman_ic, ic_stats
from hef_forecast import F_NUM, mk_model, design, schemes

lg = Log('hef_layers')

IND_AGG = ['np_yoy', 'np_yoy_med4', 'rev_yoy', 'gm_trend', 'accrual_ar',
           'ocf_np', 'ar_to_rev', 'ta_yoy', 'ret60', 'vol20',
           'turnover_rate', 'log_mv', 'L_gross_margin']
I1_F = ['ind_np_yoy_med', 'ind_n_ann', 'ind_used_q']


def skill(P, tgt, label):
    rows = []
    for (off, sch, kind), g in P.groupby(['offset', 'scheme', 'model'],
                                         sort=False):
        ics = []
        for _k, gc in g.groupby('cs_key', sort=False):
            if gc['y'].notna().sum() < PREREG['ic_min_n']:
                continue
            ic, _n = spearman_ic(gc['pred'], gc['y'])
            if np.isfinite(ic):
                ics.append(ic)
        mu, tt, icir, n = ic_stats(ics, PREREG['nw_lag_by_offset'][off])
        rows.append({'block': label, 'target': tgt, 'offset': off,
                     'scheme': sch, 'model': kind, 'n_cohorts': n,
                     'ic': mu, 'ic_t': tt, 'icir': icir, 'n_pred': len(g)})
    return rows


def run(Xv, y, base, meta, kinds, tag):
    """fit every (scheme, offset, model) and return long predictions."""
    out = []
    for off in PREREG['prediction_offsets']:
        for scheme, a, b, test in schemes():
            tr = ((base['offset'] == off) & (base['ann_year'] >= a)
                  & (base['ann_year'] <= b) & pd.notna(y)).to_numpy()
            te = ((base['offset'] == off)
                  & ((base['ann_year'] == test) if test is not None
                     else (base['ann_year'] > b))).to_numpy()
            if tr.sum() < 100 or te.sum() < 5:
                continue
            for kind in kinds:
                m = mk_model(kind)
                m.fit(Xv[tr], y.to_numpy()[tr])
                pr = (m.predict_proba(Xv[te])[:, 1] if kind != 'GBM'
                      and kind != 'RIDGE' else m.predict(Xv[te]))
                r = meta.loc[te].copy()
                r['scheme'] = scheme
                r['model'] = kind
                r['target'] = tag
                r['y'] = y.to_numpy()[te]
                r['pred'] = pr
                out.append(r)
    return out


def industry_layer(f):
    lg('--- industry layer: cohort = (SW-L1, target_end, offset)')
    g = f[(f['sw_l1'] != 'UNKNOWN') & f['model_surprise_pct'].notna()]
    key = ['sw_l1', 'target_end', 'offset']
    agg = g.groupby(key).agg(
        ind_y=('model_surprise_pct', 'mean'),
        n_mem=('model_surprise_pct', 'size'),
        ann_year=('ann_year', 'max'),
        phase=('phase', 'first'))
    for c in IND_AGG:
        agg['ag_' + c] = g.groupby(key)[c].mean()
        agg['sd_' + c] = g.groupby(key)[c].std()
    for c in I1_F:
        agg['ag_' + c] = g.groupby(key)[c].mean()
    agg = agg.reset_index()
    agg = agg[agg['n_mem'] >= 5]
    lg('industry cohorts %d, mean members %.1f, mean target %.4f'
       % (len(agg), agg['n_mem'].mean(), agg['ind_y'].mean()))
    agg['cs_key'] = agg['target_end']
    I1A = ['ag_' + c for c in I1_F]
    sets = {'I1': list(I1A),
            'I2': list(I1A) + ['ag_' + c for c in IND_AGG],
            'I3': list(I1A) + ['ag_' + c for c in IND_AGG]
                  + ['sd_ret60', 'sd_np_yoy', 'sd_turnover_rate']}
    Xa = (agg.groupby(['target_end', 'offset'])[sorted(set(
        sum(sets.values(), [])))]).rank(pct=True) - 0.5
    Xa = Xa.fillna(0.0).astype('float32')
    meta = agg[['sw_l1', 'target_end', 'offset', 'ann_year', 'phase',
                'n_mem']].rename(columns={'sw_l1': 'ts_code'})
    meta['cs_key'] = agg['cs_key'].to_numpy()
    out, rows = [], []
    for name, cols in sets.items():
        cols = [c for c in cols if c in Xa.columns]
        P = pd.concat(run(Xa[cols].to_numpy(), agg['ind_y'], agg, meta,
                          ('RIDGE', 'GBM'), name), ignore_index=True)
        out.append(P)
        rows += skill(P, 'ind_surprise', name)
    return pd.concat(out, ignore_index=True), rows


def rev_margin_layer(f, t):
    lg('--- revenue x margin layer')
    v = pd.read_parquet(os.path.join(DATA, 'quarterly_versions.parquet'),
                        columns=['ts_code', 'end_date', 'ann', 'np_sq',
                                 'rev_sq'])
    v = v.dropna(subset=['ann']).copy()
    v['ts_code'] = v['ts_code'].astype(str)
    v['end_date'] = v['end_date'].astype(str)
    v = v.sort_values(['ts_code', 'end_date', 'ann']).drop_duplicates(
        subset=['ts_code', 'end_date'], keep='first')
    act_rev = pd.Series(v['rev_sq'].to_numpy(dtype='float64'),
                        index=pd.MultiIndex.from_arrays(
                            [v['ts_code'], v['end_date']]))
    keys = pd.MultiIndex.from_arrays([f['ts_code'], f['target_end']])
    f['rev_T'] = act_rev.reindex(keys).to_numpy()
    e4 = f['target_end'].map({e: q_add(e, -4) for e in f['target_end'].unique()})
    f['rev_T4'] = act_rev.reindex(
        pd.MultiIndex.from_arrays([f['ts_code'], e4.to_numpy()])).to_numpy()
    f['g_rev'] = np.where(f['rev_T4'].abs() > 0,
                          f['rev_T'] / f['rev_T4'] - 1.0, np.nan)
    f['m_T'] = np.where(f['rev_T'].abs() > 0, f['np_T'] / f['rev_T'], np.nan)
    lg('g_rev coverage %.3f, m_T coverage %.3f'
       % (f['g_rev'].notna().mean(), f['m_T'].notna().mean()))

    X = design(f).to_numpy()
    tcols = ['ts_code', 'target_end', 'offset']
    meta = f[['ts_code', 'target_end', 'offset', 'ann_year', 'phase']].copy()
    meta['cs_key'] = f['target_end']
    out, rows = [], []
    for tag, col, kinds in (('R1_g_rev', 'g_rev', ('RIDGE', 'GBM')),
                            ('M1_margin', 'm_T', ('RIDGE', 'GBM'))):
        y = pd.to_numeric(f[col], errors='coerce')
        P = pd.concat(run(X, y, f, meta, kinds, tag), ignore_index=True)
        out.append(P)
        rows += skill(P, col, tag)

    # P1: implied profit from R1 x M1 (RIDGE path, per scheme/offset)
    lg('--- P1 derived forecast: rev(T-4)*(1+g_hat)*m_hat')
    A = pd.concat(out, ignore_index=True)
    gp = A[A['model'] == 'RIDGE']
    j = meta.assign(_i=np.arange(len(meta)))
    kk = ['ts_code', 'target_end', 'offset', 'scheme']
    gh = (gp[gp['target'] == 'R1_g_rev'][kk + ['pred']]
          .rename(columns={'pred': 'g_hat'}))
    mh = (gp[gp['target'] == 'M1_margin'][kk + ['pred']]
          .rename(columns={'pred': 'm_hat'}))
    b = (j[['ts_code', 'target_end', 'offset', '_i']]
         .merge(gh, on=['ts_code', 'target_end', 'offset'], how='inner')
         .merge(mh, on=['ts_code', 'target_end', 'offset', 'scheme'],
                how='inner'))
    np_hat = (f['rev_T4'].to_numpy()[b['_i'].to_numpy()]
              * (1.0 + b['g_hat'].to_numpy()) * b['m_hat'].to_numpy())
    surp_hat = (np_hat - f['E_sea'].to_numpy()[b['_i'].to_numpy()]) / \
        f['den_sea'].to_numpy()[b['_i'].to_numpy()]
    P1 = meta.iloc[b['_i'].to_numpy()].copy()
    P1['scheme'] = b['scheme'].to_numpy()
    P1['model'] = 'STRUCT'
    P1['target'] = 'P1_profit'
    P1['pred'] = surp_hat
    P1['y'] = f['model_surprise_pct'].to_numpy()[b['_i'].to_numpy()]
    out.append(P1)
    rows += skill(P1, 'model_surprise_pct', 'P1_profit')
    return pd.concat(out, ignore_index=True), rows


def main():
    f = pd.read_parquet(os.path.join(DATA, 'features_company.parquet'))
    t = pd.read_parquet(os.path.join(DATA, 'targets.parquet'))
    for d in (f, t):
        d['ts_code'] = d['ts_code'].astype(str)
        d['target_end'] = d['target_end'].astype(str)
    f['ann_year'] = f['ann_year'].astype(int)
    add = ['model_surprise_pct', 'positive_surprise', 'strong_surprise',
           'np_T', 'E_sea', 'den_sea']
    f = f.merge(t[['ts_code', 'target_end', 'offset'] + add],
                on=['ts_code', 'target_end', 'offset'], how='left')
    f['log_mv'] = np.log(pd.to_numeric(f['total_mv'], errors='coerce'))
    f['log_circ_mv'] = np.log(pd.to_numeric(f['circ_mv'], errors='coerce'))
    f['guide_present'] = (f['guide_mid'].notna()
                          & f['guide_is_target'].fillna(False).astype(bool)
                          ).astype('float64')

    t0 = time.time()
    Pi, ri = industry_layer(f)
    Pr, rr = rev_margin_layer(f, t)
    lg('layers fitted in %.1fs' % (time.time() - t0))
    P = pd.concat([Pi, Pr], ignore_index=True)
    P.to_parquet(os.path.join(DATA, 'predictions_layers.parquet'), index=False)
    R = pd.DataFrame(ri + rr)
    R.to_parquet(os.path.join(DATA, 'predictions_layers_skill.parquet'),
                 index=False)
    lg('predictions_layers rows %d; skill rows %d' % (len(P), len(R)))
    for blk in R['block'].unique():
        sub = R[R['block'] == blk]
        lg('--- %s: IC by target/model x offset\n%s'
           % (blk, sub.pivot_table(index=['target', 'model'], columns='offset',
                                   values='ic').round(4).to_string()))
    lg('DONE layers')


if __name__ == '__main__':
    main()
