# -*- coding: utf-8 -*-
"""H-EARN-FWD step 1: versioned quarterly series + earnings event master.

PIT discipline:
- quarterly.parquet keeps EVERY announcement version (ts, end, ann, cum
  values, single-quarter values differenced against the prior report that
  was already in force at THIS announcement date).  Feature construction
  later does merge_asof(ann <= P) -> strictly point-in-time.
- fina_indicator has no per-version ann_date; its values are joined to the
  (ts, end) announcement date from the three statements.  Restatement risk
  for fina-only fields is accepted and logged (DOCUMENTED).
- PQ fina cache has THREE file families: fina_indicator_<ts>_<end> (base,
  cumulative levels), fina_indicator_q_<ts>_<end> (single-quarter), and
  fina_indicator_yoy_<ts>_<end> (yoy growth, NO level fields).  We load the
  BASE family only (glob [0-9]*): dp_sq differencing assumes cumulative
  levels, and the yoy family sorted last in filename order used to win the
  keep='last' dedup, silently nulling every value column (DOCUMENTED).
"""
import os, sys, glob
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hef_common import (CD, PQ, DATA, Log, q_info, q_add, Q_MAP,
                        phase_of_year, load_income, num2str_date)

lg = Log('hef_build_events')


def load_family_lean(base_dirs_patterns, want):
    dfs = []
    for base, pat in base_dirs_patterns:
        for p in glob.glob(os.path.join(base, pat)):
            try:
                d = pd.read_parquet(p)
            except Exception:
                continue
            if 'ts_code' not in d.columns:
                continue
            cols = [c for c in want if c in d.columns]
            dfs.append(d[cols].drop_duplicates())
    df = pd.concat(dfs, ignore_index=True)
    for c in ('ann_date', 'f_ann_date', 'end_date'):
        if c in df.columns:
            df[c] = num2str_date(df[c])
    if 'ts_code' in df.columns:
        df['ts_code'] = df['ts_code'].astype('object')
    return df


def latest_ann_table(df, val_cols, tag):
    cols = ['ts_code', 'end_date', 'ann'] + [c for c in val_cols if c in df.columns]
    t = df[cols].dropna(subset=['ann']).copy()
    for c in val_cols:
        if c in t.columns:
            t[c] = pd.to_numeric(t[c], errors='coerce')
    t = t.drop_duplicates(subset=['ts_code', 'end_date', 'ann'], keep='last')
    t = t.sort_values(['ts_code', 'ann']).reset_index(drop=True)
    lg('  %s versions: %d rows, %d stocks' % (tag, len(t), t['ts_code'].nunique()))
    return t


def asof_prior(t_prev, cur, on_end_map):
    """merge_asof helper: for each cur row (ts, end_cur, ann_cur) take the
    latest prev row with (ts, end_prev=map(end_cur)) and ann_prev <= ann_cur."""
    prev = t_prev.rename(columns={'end_date': 'end_prev', 'ann': 'ann_prev'})
    cur = cur.copy()
    cur['end_prev'] = cur['end_date'].map(on_end_map)
    for c in ('ts_code', 'end_prev'):
        cur[c] = cur[c].astype('object')
        prev[c] = prev[c].astype('object')
    cur['_ann'] = pd.to_numeric(cur['ann'], errors='coerce')
    prev['_ann'] = pd.to_numeric(prev['ann_prev'], errors='coerce')
    cur = cur.sort_values('_ann')
    prev = prev.sort_values('_ann')
    m = pd.merge_asof(
        cur[['ts_code', 'end_date', 'ann', 'end_prev', '_ann']],
        prev, left_on='_ann', right_on='_ann',
        by=['ts_code', 'end_prev'], direction='backward',
        allow_exact_matches=True)
    return m.set_index(['ts_code', 'end_date', 'ann'])


def main():
    lg('loading income ...')
    inc = load_income()
    lg('income rows %d stocks %d' % (len(inc), inc['ts_code'].nunique()))
    inc = inc[inc['end_date'] >= '20150331'].copy()
    inc_t = latest_ann_table(
        inc, ['revenue', 'total_revenue', 'n_income', 'n_income_attr_p',
              'operate_profit'], 'income')
    inc_t['ts_code'] = inc_t['ts_code'].astype('object')

    # every version row is a candidate event
    ver = inc_t.copy()
    lg('version rows: %d' % len(ver))

    lg('PIT differencing vs prior quarter in-force version ...')
    m1 = asof_prior(inc_t[['ts_code', 'end_date', 'ann', 'n_income_attr_p',
                           'revenue', 'total_revenue']], ver,
                    lambda x: q_add(x, -1))
    ver['np_prev_cum'] = m1['n_income_attr_p'].reindex(ver.set_index(
        ['ts_code', 'end_date', 'ann']).index).values
    ver['rev_prev_cum'] = m1['revenue'].reindex(ver.set_index(
        ['ts_code', 'end_date', 'ann']).index).values
    qn = ver['end_date'].astype(str).str[4:].map(lambda x: Q_MAP[x][1])
    ver['q_num'] = qn
    ver['np_sq'] = np.where(qn == 1, ver['n_income_attr_p'],
                            ver['n_income_attr_p'] - ver['np_prev_cum'])
    ver['rev_sq'] = np.where(qn == 1, ver['revenue'],
                             ver['revenue'] - ver['rev_prev_cum'])
    ver.loc[ver['q_num'] != 1, 'np_sq'] = ver.loc[
        ver['q_num'] != 1, 'np_sq'].where(ver['np_prev_cum'].notna())
    ver.loc[ver['q_num'] != 1, 'rev_sq'] = ver.loc[
        ver['q_num'] != 1, 'rev_sq'].where(ver['rev_prev_cum'].notna())

    # ---------------- balance / cashflow announcement coverage ----------
    lg('loading balance + cashflow (announcement dates, receivable/inventory/ocf) ...')
    bal = load_family_lean([(CD, 'balance_??????_??.parquet'),
                            (PQ, 'balance_*.parquet')],
                           ['ts_code', 'ann_date', 'f_ann_date', 'end_date',
                            'report_type', 'accounts_receiv', 'inventories',
                            'total_assets', 'fix_assets', 'goodwill',
                            'total_hldr_eqy_exc_min_int'])
    cf = load_family_lean([(CD, 'cashflow_??????_??.parquet'),
                           (PQ, 'cashflow_*.parquet')],
                          ['ts_code', 'ann_date', 'f_ann_date', 'end_date',
                           'report_type', 'n_cashflow_act'])
    bal_p = prep_quiet(bal)
    cf_p = prep_quiet(cf)
    lg('balance rows %d, cashflow rows %d' % (len(bal_p), len(cf_p)))

    # ---------------- fina_indicator (no versioning; latest values) ------
    lg('loading fina_indicator ...')
    fi = load_family_lean([(PQ, 'fina_indicator_[0-9]*.parquet')],
                          ['ts_code', 'end_date', 'profit_dedt',
                           'gross_margin', 'roe', 'debt_to_assets', 'roic'])
    fi = fi.drop_duplicates(subset=['ts_code', 'end_date'], keep='last')
    fi = fi[fi['end_date'] >= '20150331']
    lg('fina_indicator (ts,end) rows %d stocks %d' % (len(fi), fi['ts_code'].nunique()))
    lg('fina coverage: profit_dedt %.3f gross_margin %.3f roe %.3f '
       'debt_to_assets %.3f roic %.3f'
       % tuple(fi[c].notna().mean() for c in
               ('profit_dedt', 'gross_margin', 'roe', 'debt_to_assets', 'roic')))

    # announcement date per (ts,end): income first, balance/cashflow fallback
    ann3 = pd.concat([
        inc_t[['ts_code', 'end_date', 'ann']],
        bal_p[['ts_code', 'end_date', 'ann']],
        cf_p[['ts_code', 'end_date', 'ann']]], ignore_index=True)
    ann3 = ann3.dropna().sort_values('ann').groupby(
        ['ts_code', 'end_date']).tail(1)
    fi = fi.merge(ann3.rename(columns={'ann': 'ann'}), on=['ts_code', 'end_date'],
                  how='left')
    fi = fi.dropna(subset=['ann'])
    lg('fina with announcement date: %d' % len(fi))

    # deducted profit single quarter (latest snapshot; versions unavailable)
    fi4 = fi[['ts_code', 'end_date', 'profit_dedt']].copy()
    fi4['prev1'] = fi4['end_date'].map(lambda x: q_add(x, -1))
    fi4 = fi4.merge(fi4[['ts_code', 'end_date',
                         'profit_dedt']].rename(columns={
        'end_date': 'prev1', 'profit_dedt': 'dp_prev_cum'}),
        on=['ts_code', 'prev1'], how='left')
    qn2 = fi4['end_date'].astype(str).str[4:].map(lambda x: Q_MAP[x][1])
    fi4['dp_sq'] = np.where(qn2 == 1, fi4['profit_dedt'],
                            fi4['profit_dedt'] - fi4['dp_prev_cum'])
    fi4.loc[qn2 != 1, 'dp_sq'] = fi4.loc[qn2 != 1, 'dp_sq'].where(
        fi4['dp_prev_cum'].notna())
    fi = fi.merge(fi4[['ts_code', 'end_date', 'dp_sq']], on=['ts_code', 'end_date'],
                  how='left')

    # ---------------- merge everything into versioned master -------------
    lg('merging balance/cashflow/fina into versioned master ...')
    # balance & cashflow: keep the version with LATEST ann <= income ann of
    # same (ts,end)?  simpler: keep balance row whose ann <= ver.ann, latest
    bal_t = bal_p[['ts_code', 'end_date', 'ann', 'accounts_receiv',
                   'inventories', 'total_assets', 'fix_assets', 'goodwill',
                   'total_hldr_eqy_exc_min_int']].copy()
    bal_t['_ann'] = pd.to_numeric(bal_t['ann'], errors='coerce')
    bal_t = bal_t.drop(columns=['ann'])
    bal_t = bal_t.sort_values('_ann')
    vk = ver[['ts_code', 'end_date', 'ann']].copy()
    vk['_ann'] = pd.to_numeric(vk['ann'], errors='coerce')
    mv = pd.merge_asof(
        vk.sort_values('_ann'), bal_t, left_on='_ann', right_on='_ann',
        by=['ts_code', 'end_date'], direction='backward',
        allow_exact_matches=True).set_index(
        ['ts_code', 'end_date', 'ann'])
    for c in ('accounts_receiv', 'inventories', 'total_assets', 'fix_assets',
              'goodwill', 'total_hldr_eqy_exc_min_int'):
        ver[c] = mv[c].reindex(ver.set_index(['ts_code', 'end_date', 'ann']).index).values

    cf_t = cf_p[['ts_code', 'end_date', 'ann', 'n_cashflow_act']].copy()
    cf_t['_ann'] = pd.to_numeric(cf_t['ann'], errors='coerce')
    cf_t = cf_t.drop(columns=['ann'])
    cf_t = cf_t.sort_values('_ann')
    mc = pd.merge_asof(
        vk.sort_values('_ann'), cf_t, left_on='_ann', right_on='_ann',
        by=['ts_code', 'end_date'], direction='backward',
        allow_exact_matches=True).set_index(['ts_code', 'end_date', 'ann'])
    ver['n_cashflow_act'] = mc['n_cashflow_act'].reindex(
        ver.set_index(['ts_code', 'end_date', 'ann']).index).values

    fi_s = fi[['ts_code', 'end_date', 'profit_dedt', 'gross_margin',
               'roe', 'debt_to_assets', 'roic', 'dp_sq']]
    ver = ver.merge(fi_s, on=['ts_code', 'end_date'], how='left')

    ver['anchor_year'] = ver['ann'].astype(str).str[:4].astype(int)
    ver['phase'] = ver['anchor_year'].map(phase_of_year)
    ver['q_str'] = ver['end_date'].map(lambda x: q_info(x)[2])
    ver = ver.sort_values(['ts_code', 'end_date', 'ann']).reset_index(drop=True)

    ver.to_parquet(os.path.join(DATA, 'quarterly_versions.parquet'), index=False)
    lg('quarterly_versions.parquet saved: %d rows %d stocks'
       % (len(ver), ver['ts_code'].nunique()))

    ev = ver[ver['anchor_year'] >= 2018][
        ['ts_code', 'end_date', 'q_str', 'ann', 'anchor_year', 'phase',
         'np_sq', 'rev_sq', 'dp_sq']].copy()
    ev.to_parquet(os.path.join(DATA, 'events.parquet'), index=False)
    lg('events.parquet saved: %d rows' % len(ev))

    lg('events by phase:')
    lg(ev.groupby('phase').size().to_string())
    lg('events by anchor_year:')
    lg(ev.groupby('anchor_year').size().to_string())
    lg('np_sq non-null: %.3f  rev_sq non-null: %.3f  dp_sq non-null: %.3f'
       % (ev['np_sq'].notna().mean(), ev['rev_sq'].notna().mean(),
          ev['dp_sq'].notna().mean()))
    lg('DONE')


def prep_quiet(df):
    if 'report_type' in df.columns:
        df = df[df['report_type'] == '1']
    df['ann'] = df.get('f_ann_date')
    df['ann'] = df['ann'].fillna(df.get('ann_date'))
    for c in ('ann_date', 'f_ann_date', 'end_date', 'ann'):
        if c in df.columns:
            df[c] = num2str_date(df[c])
    df = df.dropna(subset=['end_date', 'ann'])
    df = df[df['end_date'].str[-4:].isin(Q_MAP.keys())]
    df = df.drop_duplicates(subset=['ts_code', 'end_date', 'ann'], keep='last')
    return df


if __name__ == '__main__':
    main()
