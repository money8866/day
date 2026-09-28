# -*- coding: utf-8 -*-
"""H-EARN-FWD step 2: company feature snapshots at P60/P30/P20.

Unit: one forecast row per (ts_code, target quarter T, offset).  P = trade
date `offset` trading days before the target report's FIRST announcement
(ann_T, hindsight anchor per PREREG; all features strictly PIT at P).

PIT construction (DOCUMENTED):
- Layer 1 (last known quarter L): merge_asof over quarterly_versions
  (every announcement version) with ann <= P, ties broken to the max
  end_date disclosed that day -> L and the version IN FORCE at P.
- Layer 2 (history lags): first-announcement version per (ts, end); lag
  chains are shifted by end-date order.  Every lag value is gated per row
  by ann_lagk <= P (leakage gate G2); yoy uses signed denominator
  (cur - lag)/|lag| to stay finite when the base is negative.
- ver-based history uses first-ann versions: restatements affect only
  0.5% of (ts,end) (max 3 versions) and Layer 1 handles the most recent
  quarter with full version correctness.
- 14.8% of announcement occasions disclose >1 end (annual + Q1 same day);
  keying history by (ts,end) keeps quarterly cadence intact.
- Same-P cross-section: mkt_ret20 = median ret20 at P; ind_ret20 = mean
  ret20 within SW-L1 at P (same-date info only).
- Industry nowcast: within each (SW-L1, quarter) pool, the expanding
  median of first-ann np yoy over peers whose announcement is already
  public at P (ann <= P); target quarter is T-1, falling back to T-2 when
  no T-1 peer has reported yet (frequent at P60).
- st_flag: namechange history unavailable in cache -> ST_PIT_UNAVAILABLE
  (current-snapshot names refused: they would leak future ST status).
- Market features at suspended stocks: merge_asof takes the last trade
  date <= P; features masked NaN if that trade date is >10 days stale.
"""
import os, sys, json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hef_common import (DATA, FS_DATA, PREREG, Log, q_add, q_info,
                        phase_of_year, load_calendar, trade_offset,
                        load_industry_map, industry_map_fast,
                        load_price_panel, load_basic_panel,
                        load_forecast, load_stk_basic)

lg = Log('hef_build_features')
D = pd.to_datetime

# Layer-1 snapshot columns (last known quarter, version in force at P)
L_RENAME = {'np_sq': 'L_np_sq', 'rev_sq': 'L_rev_sq', 'dp_sq': 'L_dp_sq',
            'gross_margin': 'L_gross_margin', 'total_assets': 'L_ta',
            'accounts_receiv': 'L_ar', 'inventories': 'L_inv',
            'fix_assets': 'L_fa', 'goodwill': 'L_goodwill',
            'revenue': 'L_rev_cum', 'n_income_attr_p': 'L_np_cum',
            'n_cashflow_act': 'L_ocf'}


def _ind_arrays(segs):
    """SW industry segments -> numpy arrays sorted by out_date."""
    ins = np.array([int(s[0]) for s in segs], dtype='int64')
    outs = np.array([int(s[1]) for s in segs], dtype='int64')
    l1 = np.array([str(s[2]) for s in segs], dtype='object')
    l2 = np.array([str(s[3]) for s in segs], dtype='object')
    o = np.argsort(outs, kind='stable')
    return ins[o], outs[o], l1[o], l2[o]


def _ind_at(pack, dates_int):
    """Vectorised SW-L1/L2 lookup at YYYYMMDD int dates -> (l1, l2) arrays."""
    nn = len(dates_int)
    if pack is None or nn == 0:
        u = np.full(nn, 'UNKNOWN', dtype='object')
        return u, u.copy()
    ins, outs, l1, l2 = pack
    j = np.searchsorted(outs, dates_int, side='left')
    valid = j < len(outs)
    jj = np.where(valid, j, 0)
    ok = valid & (ins[jj] <= dates_int) & (dates_int <= outs[jj])
    return (np.where(ok, l1[jj], 'UNKNOWN'),
            np.where(ok, l2[jj], 'UNKNOWN'))


def main():
    cal = load_calendar()
    offs = PREREG['prediction_offsets']
    lg('PREREG offsets %s primary %s' % (offs, PREREG['primary_offset']))

    # ---------------- forecast units: (ts, target T) ----------------
    ev = pd.read_parquet(os.path.join(DATA, 'events.parquet'))
    ev['ts_code'] = ev['ts_code'].astype(str)
    ev['end_date'] = ev['end_date'].astype(str)
    ev['ann'] = ev['ann'].astype(str)
    u = ev.groupby(['ts_code', 'end_date'], as_index=False)['ann'].min()
    u['ann_year'] = u['ann'].astype(str).str[:4].astype(int)
    u = u[(u['ann_year'] >= 2019) & (u['ann_year'] <= 2026)].copy()
    lg('forecast units (ts,T) ann 2019..2026: %d' % len(u))

    # prediction dates P per offset
    pmap = {}
    for a in u['ann'].unique():
        for k in offs:
            pmap[(a, k)] = trade_offset(cal, a, k)
    frames = []
    for k in offs:
        d = u.copy()
        d['offset'] = k
        d['p_date'] = d['ann'].map(lambda a: pmap[(a, k)])
        frames.append(d)
    f = pd.concat(frames, ignore_index=True)
    f = f.dropna(subset=['p_date'])
    f['p_date'] = f['p_date'].astype(str)
    f = f.rename(columns={'end_date': 'target_end', 'ann': 'ann_T'})
    f['_pd'] = f['p_date'].astype('int64')
    f['_tann'] = pd.to_numeric(f['ann_T'])
    assert (f['_pd'] < f['_tann']).all(), 'P must precede target ann'
    f['t_year'] = f['target_end'].str[:4].astype(int)
    qmap_q = {e: q_info(e)[2] for e in f['target_end'].unique()}
    f['q_str_T'] = f['target_end'].map(qmap_q)
    f['phase'] = f['ann_year'].map(phase_of_year)
    f['t_prev'] = f['target_end'].map(lambda x: q_add(x, -1))
    lg('prediction rows: %d  (offsets: %s)'
       % (len(f), f.groupby('offset').size().to_dict()))
    lg('rows by phase: %s' % f.groupby('phase').size().to_dict())

    # ---------------- versions / first-ann history ----------------
    ver = pd.read_parquet(os.path.join(DATA, 'quarterly_versions.parquet'))
    for c in ('ts_code', 'end_date', 'ann'):
        ver[c] = ver[c].astype('object')
    ver['_ann'] = pd.to_numeric(ver['ann'], errors='coerce')
    ver = ver.dropna(subset=['_ann'])
    vcols = ['ts_code', 'end_date', '_ann', 'ann', 'np_sq', 'rev_sq', 'dp_sq',
             'gross_margin', 'roe', 'debt_to_assets', 'roic',
             'accounts_receiv', 'inventories', 'total_assets', 'fix_assets',
             'goodwill', 'total_hldr_eqy_exc_min_int', 'n_cashflow_act',
             'n_income_attr_p', 'revenue']
    ver[vcols] = ver[vcols].apply(lambda s: pd.to_numeric(s, errors='coerce')
                                  if s.name not in ('ts_code', 'end_date',
                                                    'ann') else s)

    # Layer 1: last known quarter L, version in force at P
    lg('Layer 1 asof: last known quarter in force at P ...')
    right = ver[vcols].rename(columns={'end_date': 'last_end',
                                       'ann': 'last_ann', '_ann': '_pd'})
    right = right.sort_values(['_pd', 'ts_code', 'last_end'])
    right['ts_code'] = right['ts_code'].astype('object')
    left = f[['ts_code', '_pd']].sort_values(['_pd', 'ts_code']).copy()
    left['ts_code'] = left['ts_code'].astype('object')
    m = pd.merge_asof(left, right, on='_pd', by='ts_code',
                      direction='backward', allow_exact_matches=True)
    # pandas 3.x merge_asof resets the index: result rows follow LEFT's
    # sorted order, so relabel with left.index then realign onto f by label.
    m.index = left.index
    for c in m.columns:
        if c not in ('ts_code', '_pd'):
            f[c] = m[c].reindex(f.index)
    # Layer-1 snapshot columns carry the L_ prefix
    f = f.rename(columns=L_RENAME)
    ok1 = f['last_end'].notna()
    assert (pd.to_numeric(f.loc[ok1, 'last_ann'], errors='coerce')
            <= f.loc[ok1, '_pd']).all(), 'Layer1 ann must be <= P'
    lg('Layer 1 matched: %d / %d rows have last known quarter'
       % (int(ok1.sum()), len(f)))

    # Layer 2: first-ann history with lag chains
    lg('Layer 2: first-announcement history lags ...')
    H = ver.sort_values(['ts_code', 'end_date', '_ann']).drop_duplicates(
        subset=['ts_code', 'end_date'], keep='first').copy()
    H = H.sort_values(['ts_code', 'end_date']).reset_index(drop=True)
    g = H.groupby('ts_code', sort=False)
    for k in range(1, 9):
        for c in ('np_sq', 'rev_sq', 'dp_sq'):
            H['%s_lag%d' % (c, k)] = g[c].shift(k)
        H['end_lag%d' % k] = g['end_date'].shift(k)
        H['ann_lag%d' % k] = g['_ann'].shift(k)
    e4 = {e: q_add(e, -4) for e in H['end_date'].unique()}
    base4 = g['np_sq'].shift(4)
    H['yoy_np'] = np.where((H['end_lag4'] == H['end_date'].map(e4))
                           & (base4.abs() > 0),
                           (H['np_sq'] - base4) / base4.abs(), np.nan)
    base4r = g['rev_sq'].shift(4)
    H['yoy_rev'] = np.where((H['end_lag4'] == H['end_date'].map(e4))
                            & (base4r.abs() > 0),
                            (H['rev_sq'] - base4r) / base4r.abs(), np.nan)
    for k in range(1, 8):
        H['yoy_np_lag%d' % k] = g['yoy_np'].shift(k)
        H['yoy_rev_lag%d' % k] = g['yoy_rev'].shift(k)
    for k in range(1, 5):
        H['gm_lag%d' % k] = g['gross_margin'].shift(k)
    H['ta_lag4'] = g['total_assets'].shift(4)
    H['ar_lag4'] = g['accounts_receiv'].shift(4)
    H['inv_lag4'] = g['inventories'].shift(4)
    H['fa_lag4'] = g['fix_assets'].shift(4)
    H['h_first_ann'] = H['ann']
    H['ann_delay'] = (D(H['ann'].astype(str))
                      - D(H['end_date'].astype(str))).dt.days
    hcols = (['ts_code', 'end_date', 'h_first_ann', 'ann_delay']
             + ['%s_lag%d' % (c, k) for c in ('np_sq', 'rev_sq', 'dp_sq')
                for k in range(1, 9)]
             + ['end_lag%d' % k for k in range(1, 9)]
             + ['ann_lag%d' % k for k in range(1, 9)]
             + ['yoy_np'] + ['yoy_np_lag%d' % k for k in range(1, 8)]
             + ['yoy_rev'] + ['yoy_rev_lag%d' % k for k in range(1, 8)]
             + ['gm_lag%d' % k for k in range(1, 5)]
             + ['ta_lag4', 'ar_lag4', 'inv_lag4', 'fa_lag4'])
    lg('H history rows %d, lag cols %d' % (len(H), len(hcols) - 4))
    f = f.merge(H[hcols].rename(columns={'end_date': 'last_end'}),
                on=['ts_code', 'last_end'], how='left')

    # leakage gates: every lag input announced before P
    f['h_ann_num'] = pd.to_numeric(f['h_first_ann'], errors='coerce')
    okP = {0: f['h_ann_num'] <= f['_pd']}
    for k in range(1, 9):
        okP[k] = (f['ann_lag%d' % k] <= f['_pd']) & f['ann_lag%d' % k].notna()

    # value columns must be masked where the source announcement is not yet
    # public at P (*_lag* value columns are otherwise raw copies of history;
    # the ann_lag*/end_lag* calendar columns stay raw for gating/diagnostics)
    for k in range(1, 9):
        msk = ~okP[k]
        vcols_k = ['np_sq_lag%d' % k, 'rev_sq_lag%d' % k, 'dp_sq_lag%d' % k]
        if k <= 7:
            vcols_k += ['yoy_np_lag%d' % k, 'yoy_rev_lag%d' % k]
        if k <= 4:
            vcols_k += ['gm_lag%d' % k]
        for c in vcols_k:
            if c in f.columns:
                f.loc[msk, c] = np.nan
    for c in ('yoy_np', 'yoy_rev'):
        if c in f.columns:
            f.loc[~okP[0], c] = np.nan
    for c in ('ta_lag4', 'ar_lag4', 'inv_lag4', 'fa_lag4'):
        if c in f.columns:
            f.loc[~okP[4], c] = np.nan

    # derived financial features
    f['prev_end'] = f['last_end'].map(
        {e: q_add(e, -1) for e in f['last_end'].dropna().unique()})
    f['is_t_prev_known'] = f['last_end'] == f['t_prev']
    gate4 = (f['end_lag4'] == f['last_end'].map(
        {e: q_add(e, -4) for e in f['last_end'].dropna().unique()}))
    gate1 = f['end_lag1'] == f['prev_end']

    def sgn_yoy(cur, lag, gate, okp):
        v = (cur - lag) / lag.abs()
        bad = ~(gate & okp & lag.abs().gt(0) & cur.notna() & lag.notna())
        return v.mask(bad)

    f['np_yoy'] = sgn_yoy(f['L_np_sq'], f['np_sq_lag4'], gate4, okP[4])
    f['rev_yoy'] = sgn_yoy(f['L_rev_sq'], f['rev_sq_lag4'], gate4, okP[4])
    f['dp_yoy'] = sgn_yoy(f['L_dp_sq'], f['dp_sq_lag4'], gate4, okP[4])
    f['np_qoq'] = sgn_yoy(f['L_np_sq'], f['np_sq_lag1'], gate1, okP[1])
    f['rev_qoq'] = sgn_yoy(f['L_rev_sq'], f['rev_sq_lag1'], gate1, okP[1])

    yoy_np_cols = ['yoy_np'] + ['yoy_np_lag%d' % k for k in range(1, 8)]
    yoy_rev_cols = ['yoy_rev'] + ['yoy_rev_lag%d' % k for k in range(1, 8)]
    ym = f[yoy_np_cols].copy()
    ym_rev = f[yoy_rev_cols].copy()
    for j in range(8):
        k = 0 if j == 0 else j
        ym.iloc[:, j] = ym.iloc[:, j].mask(~okP[k])
        ym_rev.iloc[:, j] = ym_rev.iloc[:, j].mask(~okP[k])
    f['np_yoy_med4'] = np.nanmedian(ym.iloc[:, :4].values, axis=1)
    f['np_yoy_med8'] = np.nanmedian(ym.values, axis=1)
    f['rev_yoy_med4'] = np.nanmedian(ym_rev.iloc[:, :4].values, axis=1)
    absm = pd.DataFrame(index=f.index)
    for k in range(1, 8):
        absm['a%d' % k] = f['np_sq_lag%d' % k].abs().mask(~okP[k])
    absm['a0'] = f['L_np_sq'].abs()
    f['np_absmed8'] = np.nanmedian(absm.values, axis=1)

    gmmean = np.nanmean(np.column_stack(
        [f['gm_lag%d' % k].mask(~okP[k]).values for k in range(1, 5)]),
        axis=1)
    f['gm_trend'] = f['L_gross_margin'] - gmmean
    for tag, cur, lag in (('ta', 'L_ta', 'ta_lag4'),
                          ('ar', 'L_ar', 'ar_lag4'),
                          ('inv', 'L_inv', 'inv_lag4'),
                          ('fix', 'L_fa', 'fa_lag4')):
        f['%s_yoy' % tag] = sgn_yoy(f[cur], f[lag], gate4, okP[4])
    f['goodwill_ta'] = np.where(f['L_ta'] > 0, f['L_goodwill'] / f['L_ta'],
                                np.nan)
    f['ar_to_rev'] = np.where(f['L_rev_cum'].abs() > 0,
                              f['L_ar'] / f['L_rev_cum'].abs(), np.nan)
    f['ocf_np'] = np.where(f['L_np_cum'].abs() > 0,
                           f['L_ocf'] / f['L_np_cum'].abs(), np.nan)
    f['accrual_ar'] = f['ar_yoy'] - f['rev_yoy']
    f['accrual_inv'] = f['inv_yoy'] - f['rev_yoy']
    f['staleness_days'] = (D(f['p_date']) - D(f['last_ann'].astype(str))
                           ).dt.days
    lg('financial features done; np_yoy nn %.3f med4 nn %.3f'
       % (f['np_yoy'].notna().mean(), f['np_yoy_med4'].notna().mean()))

    # ---------------- market features (PIT at P) ----------------
    lg('market panel: loading price/basic cache ...')
    px = load_price_panel()[['ts_code', 'trade_date', 'qfq_close', 'vol']]
    bs = load_basic_panel().drop(columns=['close'], errors='ignore')
    for dfx in (px, bs):
        dfx['ts_code'] = dfx['ts_code'].astype(str)
        dfx['trade_date'] = dfx['trade_date'].astype(str)
    for c in [c for c in bs.columns if c not in ('ts_code', 'trade_date')]:
        bs[c] = pd.to_numeric(bs[c], errors='coerce')
    px['qfq_close'] = pd.to_numeric(px['qfq_close'], errors='coerce')
    px['vol'] = pd.to_numeric(px['vol'], errors='coerce')
    pan = px.merge(bs, on=['ts_code', 'trade_date'], how='inner')
    del px, bs
    pan['_d'] = pan['trade_date'].astype('int64')
    pan = pan.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
    gp = pan.groupby('ts_code', sort=False)
    pan['ret20'] = gp['qfq_close'].pct_change(20)
    pan['ret60'] = gp['qfq_close'].pct_change(60)
    pan['vol20'] = gp['vol'].transform(
        lambda s: s.rolling(20, min_periods=10).mean())
    del gp
    lg('panel rows %d stocks %d span %s..%s'
       % (len(pan), pan['ts_code'].nunique(), pan['trade_date'].min(),
          pan['trade_date'].max()))

    MCOLS = [c for c in ('ret20', 'ret60', 'vol20', 'turnover_rate',
                         'volume_ratio', 'pe_ttm', 'pb', 'ps_ttm',
                         'total_mv', 'circ_mv') if c in pan.columns]
    p_d = pan['_d'].to_numpy()
    p_td = pan['trade_date'].to_numpy()
    p_mat = pan[MCOLS].to_numpy(dtype='float64')
    p_idx = pan.groupby('ts_code', sort=False).indices
    # calendar index for trading-day gaps
    cal_idx = {d: i for i, d in enumerate(cal['trade_date'].astype(str))}

    f = f.reset_index(drop=True)
    n = len(f)
    keys_all = f['_pd'].to_numpy()
    pos_ts = f.groupby('ts_code', sort=False).indices
    out_mat = np.full((n, len(MCOLS)), np.nan)
    last_td = np.full(n, None, dtype='object')
    for ts, rows in pos_ts.items():
        pg = p_idx.get(ts)
        if pg is None:
            continue
        k = keys_all[rows]
        j = np.searchsorted(p_d[pg], k, side='right') - 1
        ok = j >= 0
        if not ok.any():
            continue
        sel = rows[ok]
        jj = j[ok]
        out_mat[sel] = p_mat[pg][jj]
        last_td[sel] = p_td[pg][jj]
    for i, c in enumerate(MCOLS):
        f[c] = out_mat[:, i]
    f['last_trade_date'] = pd.Series(last_td).fillna('').astype(str)
    del out_mat, p_mat
    # suspended-stock mask: last trade > 10 trading days before P
    f['stale_td'] = (f['p_date'].map(cal_idx)
                     - f['last_trade_date'].map(cal_idx))
    stale = f['stale_td'] > 10
    for c in MCOLS:
        f.loc[stale, c] = np.nan
    f.loc[stale, 'last_trade_date'] = ''
    lg('market matched %.3f stale>10td %.4f'
       % (float((f['last_trade_date'] != '').mean()), float(stale.mean())))

    # ---------------- industry (SW) at P + cross-section ----------------
    lg('industry map + same-P cross-section ...')
    ind_pack = {ts: _ind_arrays(v)
                for ts, v in industry_map_fast(load_industry_map()).items()}
    l1_arr = np.full(n, 'UNKNOWN', dtype='object')
    l2_arr = np.full(n, 'UNKNOWN', dtype='object')
    for ts, rows in pos_ts.items():
        a, b = _ind_at(ind_pack.get(ts), keys_all[rows])
        l1_arr[rows] = a
        l2_arr[rows] = b
    f['sw_l1'] = l1_arr
    f['sw_l2'] = l2_arr
    lg('SW-L1 mapped %.3f SW-L2 %.3f UNKNOWN %.4f'
       % (float((f['sw_l1'] != 'UNKNOWN').mean()),
          float((f['sw_l2'] != 'UNKNOWN').mean()),
          float((f['sw_l1'] == 'UNKNOWN').mean())))

    pset = set(np.unique(keys_all).tolist())
    sub = pan[pan['_d'].isin(pset)].reset_index(drop=True)
    s_d = sub['_d'].to_numpy()
    sub_l1 = np.full(len(sub), 'UNKNOWN', dtype='object')
    for ts, rows in sub.groupby('ts_code', sort=False).indices.items():
        a, _b = _ind_at(ind_pack.get(ts), s_d[rows])
        sub_l1[rows] = a
    sub['_l1'] = sub_l1
    f['mkt_ret20'] = f['_pd'].map(sub.groupby('_d')['ret20'].median())
    indm = (sub[sub['_l1'] != 'UNKNOWN']
            .groupby(['_d', '_l1'])['ret20'].mean().to_dict())
    f['ind_ret20'] = np.array(
        [indm.get((int(d), l), np.nan)
         for d, l in zip(f['_pd'].to_numpy(), f['sw_l1'].to_numpy())],
        dtype='float64')
    del sub, pan

    # ---------------- industry nowcast (expanding median) ----------------
    lg('industry nowcast: expanding median of first-ann np yoy ...')
    Hn = (H[['ts_code', 'end_date', '_ann', 'yoy_np']]
          .dropna(subset=['yoy_np']).copy())
    Hn['_ann'] = pd.to_numeric(Hn['_ann'], errors='coerce')
    Hn = Hn.dropna(subset=['_ann']).reset_index(drop=True)
    h_d = Hn['_ann'].to_numpy().astype('int64')
    hn_l1 = np.full(len(Hn), 'UNKNOWN', dtype='object')
    for ts, rows in Hn.groupby('ts_code', sort=False).indices.items():
        a, _b = _ind_at(ind_pack.get(ts), h_d[rows])
        hn_l1[rows] = a
    Hn['l1'] = hn_l1
    Hn['_e'] = pd.to_numeric(Hn['end_date'], errors='coerce')
    Hn = (Hn[(Hn['l1'] != 'UNKNOWN') & Hn['_e'].notna()]
          .sort_values(['l1', '_e', '_ann']).reset_index(drop=True))
    # pool = (SW-L1, quarter): peers of one industry-quarter report over
    # time, so the expanding median of the pool *as of P* is exactly the
    # median over peers whose announcement is already public at P.
    gk = Hn.groupby(['l1', '_e'], sort=False)['yoy_np']
    Hn['pool_med'] = gk.expanding().median().reset_index(level=[0, 1],
                                                         drop=True)
    Hn['pool_n'] = (gk.cumcount() + 1).astype('float64')
    qpack = {}
    for (l1v, ev), s2 in Hn.groupby(['l1', '_e'], sort=False):
        qpack[(l1v, int(ev))] = (s2['_ann'].to_numpy().astype('int64'),
                                 s2['pool_med'].to_numpy(dtype='float64'),
                                 s2['pool_n'].to_numpy(dtype='float64'))
    f['_pe1'] = f['t_prev'].astype('int64')
    f['_pe2'] = (f['t_prev'].map({v: q_add(v, -1)
                                  for v in f['t_prev'].unique()})
                 .astype('int64'))
    med1 = np.full(n, np.nan)
    cnt1 = np.full(n, np.nan)
    med2 = np.full(n, np.nan)
    cnt2 = np.full(n, np.nan)
    annu = np.full(n, np.nan)
    for keycol, out in (('_pe1', (med1, cnt1)), ('_pe2', (med2, cnt2))):
        for (l1v, ev), rows in f.groupby(['sw_l1', keycol],
                                         sort=False).indices.items():
            pk = qpack.get((l1v, int(ev)))
            if pk is None:
                continue
            a, md, cn = pk
            j = np.searchsorted(a, keys_all[rows], side='right') - 1
            ok = j >= 0
            if not ok.any():
                continue
            sel = rows[ok]
            jj = j[ok]
            out[0][sel] = md[jj]
            out[1][sel] = cn[jj]
            if keycol == '_pe1':
                annu[sel] = a[jj]
    f['ind_yoy_med_p1'] = med1
    f['ind_n_p1'] = cnt1
    f['ind_yoy_med_p2'] = med2
    f['ind_n_p2'] = cnt2
    f['ind_last_ann'] = annu
    use1 = cnt1 > 0
    f['ind_np_yoy_med'] = np.where(use1, med1, med2)
    f['ind_n_ann'] = np.where(use1, cnt1, cnt2)
    f['ind_used_q'] = np.where(use1, 1.0, np.where(cnt2 > 0, 2.0, np.nan))
    f = f.drop(columns=['_pe1', '_pe2'])
    lg('nowcast: T-1 pool used %.3f, T-2 fallback %.3f, covered %.3f'
       % (float(use1.mean()), float((~use1 & (cnt2 > 0)).mean()),
          float(pd.Series(f['ind_np_yoy_med']).notna().mean())))

    # ---------------- pre-announcement guidance (forecast) ----------------
    lg('guidance: forecast (pre-announcement) asof P ...')
    fc = load_forecast()
    fc['ts_code'] = fc['ts_code'].astype(str)
    fc['_ann'] = pd.to_numeric(fc['ann_date'], errors='coerce')
    fc = fc.dropna(subset=['_ann']).copy()
    fc['_ann'] = fc['_ann'].astype('int64')
    fc['gmid'] = (pd.to_numeric(fc['net_profit_min'], errors='coerce')
                  + pd.to_numeric(fc['net_profit_max'], errors='coerce')) / 2
    fc['_e'] = pd.to_numeric(fc['end_date'], errors='coerce')
    fc = fc.sort_values(['ts_code', '_ann'])
    gpack = {}
    for ts, s3 in fc.groupby('ts_code', sort=False):
        gpack[ts] = (s3['_ann'].to_numpy(),
                     s3['gmid'].to_numpy(dtype='float64'),
                     s3['_e'].to_numpy(dtype='float64'),
                     s3['_ann'].astype(str).to_numpy())
    gm = np.full(n, np.nan)
    ge = np.full(n, np.nan)
    ga = np.full(n, None, dtype='object')
    for ts, rows in pos_ts.items():
        pk = gpack.get(ts)
        if pk is None:
            continue
        a, v, e, ad = pk
        j = np.searchsorted(a, keys_all[rows], side='right') - 1
        ok = j >= 0
        if not ok.any():
            continue
        sel = rows[ok]
        jj = j[ok]
        gm[sel] = v[jj]
        ge[sel] = e[jj]
        ga[sel] = ad[jj]
    f['guide_mid'] = gm
    f['guide_end'] = ge
    f['guide_gap_td'] = (f['p_date'].map(cal_idx)
                         - pd.Series(ga).map(cal_idx))
    f['guide_ann'] = pd.Series(ga).fillna('').astype(str)
    g_end = pd.to_numeric(f['guide_end'], errors='coerce')
    f['guide_is_target'] = g_end == f['target_end'].astype('int64')
    mv = pd.to_numeric(f['total_mv'], errors='coerce')
    f['guide_to_mv'] = np.where(
        f['guide_is_target'] & (mv > 0) & pd.notna(f['guide_mid']),
        pd.to_numeric(f['guide_mid'], errors='coerce') / mv, np.nan)
    lg('guide: any %.3f, target-quarter %.3f, to_mv %.3f'
       % (float(pd.notna(f['guide_mid']).mean()),
          float(f['guide_is_target'].mean()),
          float(pd.notna(f['guide_to_mv']).mean())))

    # ---------------- ST status: PIT history unavailable ----------------
    f['st_flag'] = np.nan
    f['st_pit_status'] = 'ST_PIT_UNAVAILABLE'

    # ---------------- listing metadata (static, PIT safe) ----------------
    sb = load_stk_basic()
    sb['ts_code'] = sb['ts_code'].astype(str)
    sbi = sb.set_index('ts_code')
    ld = pd.to_numeric(f['ts_code'].map(sbi['list_date'].to_dict()),
                       errors='coerce')
    f['list_date'] = ld
    f['listed_days'] = (f['p_date'].map(cal_idx)
                        - ld.map(lambda x: cal_idx.get(str(int(x)), np.nan)
                                 if pd.notna(x) else np.nan))
    if 'industry' in sbi.columns:
        f['ts_industry'] = f['ts_code'].map(sbi['industry'].to_dict())
    code = f['ts_code'].str[:6]
    f['board'] = np.where(
        code.str.startswith(('300', '301')), 'ChiNext',
        np.where(code.str.startswith(('688', '689')), 'STAR',
                 np.where(f['ts_code'].str.endswith('.BJ'), 'BSE', 'Main')))
    lg('board mix: %s' % f.groupby('board').size().to_dict())
    lg('SW-L1 coverage gap (UNKNOWN) %.4f -- sw map has %d stocks only'
       % (float((f['sw_l1'] == 'UNKNOWN').mean()), len(ind_pack)))

    # ---------------- winsorization (PREREG, PIT safe) ----------------
    # unbounded ratio features carry tiny-denominator tails (raw np_yoy spans
    # -3.6e4 .. 1.5e4).  PREREG winsor quantiles are computed *within one
    # cross-section*: rows sharing one P share one information set, so the
    # transform never uses cross-time information.  A thin cross-section
    # (P dates carrying < MINN names) makes the 1%/99% bounds degenerate into
    # the min/max, so those rows are re-keyed onto the most recent date whose
    # cross-section is thick enough -- always a PAST date, hence still PIT.
    lo, hi = PREREG['winsor']
    MINN = 100
    WCOLS = ['np_yoy', 'rev_yoy', 'dp_yoy', 'np_qoq', 'rev_qoq',
             'ta_yoy', 'ar_yoy', 'inv_yoy', 'fix_yoy',
             'accrual_ar', 'accrual_inv', 'goodwill_ta', 'ar_to_rev', 'ocf_np',
             'np_yoy_med4', 'np_yoy_med8', 'rev_yoy_med4',
             'yoy_np', 'yoy_rev', 'ind_yoy_med_p1', 'ind_yoy_med_p2',
             'ind_np_yoy_med']
    WCOLS += ['yoy_np_lag%d' % k for k in range(1, 8)]
    WCOLS += ['yoy_rev_lag%d' % k for k in range(1, 8)]
    WCOLS = [c for c in WCOLS if c in f.columns]
    kd = f['_pd'].astype('int64')
    cnt_d = f.groupby(kd).size()
    thick = np.sort(cnt_d[cnt_d >= MINN].index.to_numpy())
    gi = np.searchsorted(thick, kd.to_numpy(), side='right') - 1
    wkey = np.where(gi >= 0, thick[np.maximum(gi, 0)], kd.to_numpy())
    wser = pd.Series(wkey, index=f.index)
    pre_lo, pre_hi = float(f['np_yoy'].min()), float(f['np_yoy'].max())
    for c in WCOLS:
        v = pd.to_numeric(f[c], errors='coerce')
        ql = v.groupby(wkey).quantile(lo)
        qh = v.groupby(wkey).quantile(hi)
        f[c] = v.clip(lower=wser.map(ql), upper=wser.map(qh))
    lg('winsor %s on %d cols; %d P dates (%d thin, re-keyed %d rows to a '
       'past date); np_yoy %.1f/%.1f -> %.3f/%.3f'
       % (str(PREREG['winsor']), len(WCOLS), int(kd.nunique()),
          int((cnt_d < MINN).sum()), int((wkey != kd.to_numpy()).sum()),
          pre_lo, pre_hi, float(f['np_yoy'].min()), float(f['np_yoy'].max())))

    # ---------------- PIT label-availability filter ----------------
    # The anchor rule takes the target report's *disclosure* date.  A handful
    # of long-suspension shells re-disclosed old annual reports years later,
    # so the anchor (re-issue date) post-dates P while the report content had
    # already been public since the original disclosure.  Those rows carry a
    # look-ahead label and are removed here, at source, so that every
    # downstream artefact is clean.
    vv = pd.read_parquet(os.path.join(DATA, 'quarterly_versions.parquet'),
                         columns=['ts_code', 'end_date', 'ann'])
    vv['ts_code'] = vv['ts_code'].astype(str)
    vv['end_date'] = vv['end_date'].astype(str)
    vv['_a'] = pd.to_numeric(vv['ann'], errors='coerce')
    vv = (vv.dropna(subset=['_a'])
          .sort_values(['ts_code', 'end_date', '_a'])
          .drop_duplicates(['ts_code', 'end_date'], keep='first'))
    a_map = pd.Series(vv['_a'].to_numpy(),
                      index=pd.MultiIndex.from_arrays([vv['ts_code'],
                                                       vv['end_date']]))
    a_tgt = a_map.reindex(pd.MultiIndex.from_arrays(
        [f['ts_code'], f['target_end'].astype(str)])).to_numpy()
    leak = np.isfinite(a_tgt) & (a_tgt <= f['_pd'].to_numpy(float))
    lg('PIT label filter: %d rows dropped where the target report was '
       'already public at P (re-disclosure of long-suspended issuers)'
       % int(leak.sum()))
    if leak.any():
        lg('  dropped sample: %s'
           % f.loc[leak, ['ts_code', 'target_end', 'offset', 'p_date']]
           .head(8).to_dict('records'))
    f = f.loc[~leak].reset_index(drop=True)

    # ---------------- save + coverage ----------------
    feat_path = os.path.join(DATA, 'features_company.parquet')
    f.to_parquet(feat_path, index=False)
    lg('saved %s rows %d cols %d' % (feat_path, len(f), f.shape[1]))
    cov_spots = ['L_np_sq', 'np_yoy', 'np_yoy_med8', 'gm_trend', 'ocf_np',
                 'accrual_ar', 'ret20', 'ret60', 'vol20', 'turnover_rate',
                 'pe_ttm', 'total_mv', 'mkt_ret20', 'ind_ret20',
                 'ind_np_yoy_med', 'guide_mid', 'guide_to_mv', 'listed_days']
    cov = {c: round(float(pd.Series(f[c]).notna().mean()), 4)
           for c in cov_spots if c in f.columns}
    lg('coverage: %s' % json.dumps(cov))
    lg('rows by offset: %s' % f.groupby('offset').size().to_dict())
    lg('rows by phase: %s' % f.groupby('phase').size().to_dict())
    lg('DONE features_company')


if __name__ == '__main__':
    main()
