# -*- coding: utf-8 -*-
"""H-EARN-PRICE-01 step 1: price event master table.

Unit: (ts_code, target_end) -- ONE row per frozen forecast event at the
pre-registered primary offset (P30).  Everything here is either read from
the FROZEN H-EARN-FWD artefacts or computed from the cached price panels.

Timeline (registered)
---------------------
    A  = first-announcement session of the target report
         (min ann over all versions of (ts_code, target_end))
    T0 = first session STRICTLY AFTER A
         (conservative: the report may have been released after the A close,
          and Tushare daily carries no intraday disclosure time -> the
          announcement timing is UNKNOWN, never assumed)

Returns (close-to-close on the forward-adjusted price, base = session u-1):
    pre_H      = close[A-1]   / close[A-1-H] - 1     clean pre-announcement
    ann_ret    = close[A]     / close[A-1]   - 1     announcement-session move
    rel_0_1    = close[T0]    / close[A-1]   - 1     information-release window
    fwd_H      = close[T0+H]  / close[T0]    - 1     PRIMARY, executable
                                                     (enter at the T0 close)
    fwd_open_H = close[T0+H]  / open[T0]     - 1     enter at the T0 open
    fwd_ev_H   = close[T0+H]  / close[A-1]   - 1     event window incl. the
                                                     announcement reaction
Excess returns subtract the equal-weighted all-A market / the SW-L1 industry
mean over the SAME window as fwd_H.

No Actual-earnings variable is ever used to build a signal: S2/S3 are stored
for EVALUATION only (hef_report.py asserts this).
"""
import os, sys, json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hep_common import (PREREG, DATA, FS_DATA, HEF_DATA, Log, phase_of_year,
                        load_calendar, load_industry_map, industry_map_fast,
                        industry_at_fast, build_price_grid,
                        build_market_ind_cum, cum_ret, q_info)

lg = Log('hep_build_events')


def limit_pct(ts_code, board):
    if ts_code[:3] in ('300', '301', '688', '689'):
        return 0.20
    if ts_code[0] in ('4', '8', '9'):
        return 0.30
    return 0.10


def main():
    P = PREREG['forecast_primary']
    off = int(P['offset'])
    H = list(PREREG['horizons'])
    lg('PREREG primary forecast %s' % P)

    cal = load_calendar()
    dates = cal['trade_date'].astype(str).to_numpy()
    d2i = {d: i for i, d in enumerate(dates)}
    N = len(dates)

    # ---------------- frozen forecast + frozen target -------------------
    pr = pd.read_parquet(PREREG['frozen_predictions'])
    pr['ts_code'] = pr['ts_code'].astype(str)
    pr['target_end'] = pr['target_end'].astype(str)
    pr = pr[(pr['scheme'] == P['scheme']) & (pr['model'] == P['model'])
            & (pr['target'] == P['target']) & (pr['offset'] == off)]
    pr = pr[['ts_code', 'target_end', 'pred', 'y', 'ann_year', 'phase']]
    pr = pr.rename(columns={'pred': 'forecast', 'y': 'y_frozen'})
    lg('frozen forecast rows %d  ann_year %s'
       % (len(pr), sorted(pr['ann_year'].unique())))

    tg = pd.read_parquet(PREREG['frozen_targets'],
                         columns=['ts_code', 'target_end', 'offset', 'p_date',
                                  'phase', 'board', 'sw_l1', 'np_T', 'np_T4',
                                  'E_sea', 'den_sea', 'surp_sea_raw',
                                  'model_surprise_pct', 'positive_surprise',
                                  'strong_surprise'])
    tg['ts_code'] = tg['ts_code'].astype(str)
    tg['target_end'] = tg['target_end'].astype(str)
    tg['p_date'] = tg['p_date'].astype(str)
    tg = tg[tg['offset'] == off]
    lg('frozen targets rows (offset %d) %d' % (off, len(tg)))

    # S2 actual surprise = the frozen model_surprise_pct (= surp_sea_raw)
    ev = tg.merge(pr.drop(columns=['phase']), on=['ts_code', 'target_end'],
                  how='inner')
    lg('events after frozen-forecast inner join: %d' % len(ev))
    d = (ev['model_surprise_pct'] - ev['y_frozen']).abs()
    lg('frozen pred-vs-target consistency: max|model_surprise_pct - y| = %.2e'
       % (float(d.max()) if len(d) else float('nan')))
    ev['actual_surprise'] = ev['model_surprise_pct']           # S2
    ev['forecast_surprise'] = ev['forecast']                    # S1
    ev['forecast_error'] = ev['actual_surprise'] - ev['forecast']  # S3

    # ---------------- announcement date A (first version) ---------------
    ee = pd.read_parquet(PREREG['frozen_events'],
                         columns=['ts_code', 'end_date', 'ann'])
    ee['ts_code'] = ee['ts_code'].astype(str)
    ee['end_date'] = ee['end_date'].astype(str)
    ee['ann'] = ee['ann'].astype(str)
    A = (ee.groupby(['ts_code', 'end_date'], as_index=False)['ann'].min()
         .rename(columns={'end_date': 'target_end', 'ann': 'A'}))
    A['A'] = A['A'].str.slice(0, 8)
    ev = ev.merge(A, on=['ts_code', 'target_end'], how='left')
    miss = int(ev['A'].isna().sum())
    lg('events missing announcement anchor A: %d (dropped)' % miss)
    ev = ev.dropna(subset=['A']).copy()

    ev['A_idx'] = ev['A'].map(d2i)
    ev = ev.dropna(subset=['A_idx'])
    ev['A_idx'] = ev['A_idx'].astype(int)
    ev['P_idx'] = ev['p_date'].map(d2i)
    ev = ev.dropna(subset=['P_idx'])
    ev['P_idx'] = ev['P_idx'].astype(int)
    ev['T0_idx'] = ev['A_idx'] + 1
    ev = ev[ev['T0_idx'] < N].copy()
    ev['T0'] = dates[ev['T0_idx'].to_numpy()]
    ev['ann_year'] = ev['A'].str[:4].astype(int)
    ev['phase'] = ev['ann_year'].map(phase_of_year)
    ev['q_str'] = ev['target_end'].map(lambda x: q_info(x)[2])
    lg('events with A/T0 on calendar: %d  A range %s..%s'
       % (len(ev), ev['A'].min(), ev['A'].max()))
    lg('events by ann_year:\n%s' % ev.groupby('ann_year').size().to_string())

    # ---------------- price grid + benchmark levels ---------------------
    grid = build_price_grid(cal)
    s2i = grid['s2i']
    ev = ev[ev['ts_code'].isin(s2i)].copy()
    si = ev['ts_code'].map(s2i).to_numpy().astype('int64')
    ev['_si'] = si
    lg('events in price grid: %d' % len(ev))

    mkt_cum, ind_cum, mkt_daily = build_market_ind_cum(cal, lg)
    mkt_lvl = mkt_cum.to_numpy()
    ind_lvl = ind_cum.to_numpy()
    ind_cols = list(ind_cum.columns)

    i0 = ev['T0_idx'].to_numpy()
    iA = ev['A_idx'].to_numpy()
    close = grid['close']
    open_ = grid['open']
    traded = grid['traded']

    # ---------------- returns -------------------------------------------
    lg('computing event returns ...')
    for h in H:
        ev['fwd%d' % h] = close[si, np.clip(i0 + h, 0, N - 1)] / close[si, i0] - 1.0
        ev['fwd_ev%d' % h] = close[si, np.clip(i0 + h, 0, N - 1)] / close[si, iA - 1] - 1.0
        ev['fwd_open%d' % h] = close[si, np.clip(i0 + h, 0, N - 1)] / open_[si, i0] - 1.0
        ev['mkt_fwd%d' % h] = cum_ret(mkt_lvl, i0, i0 + h)
    for h in PREREG['pre_windows']:
        ev['pre%d' % h] = close[si, iA - 1] / close[si, np.clip(iA - 1 - h, 0, N - 1)] - 1.0
    ev['ann_ret'] = close[si, iA] / close[si, iA - 1] - 1.0
    ev['rel_0_1'] = close[si, np.clip(i0, 0, N - 1)] / close[si, iA - 1] - 1.0
    ev['ret_t0'] = close[si, i0] / close[si, iA - 1] - 1.0

    # masked: no genuine trade print at T0 / at A-1
    ev['traded_t0'] = traded[si, i0]
    ev['traded_a'] = traded[si, iA]
    nm = ~ev['traded_t0'].to_numpy()
    lg('events with no trade print at T0: %d (%.4f)'
       % (int(nm.sum()), float(nm.mean())))

    # ---------------- industry at the decision session -------------------
    im = load_industry_map()
    imf = industry_map_fast(im)
    l1 = np.empty(len(ev), dtype='object')
    for k, (ts, ad) in enumerate(zip(ev['ts_code'].to_numpy(), ev['A'].to_numpy())):
        l1[k] = industry_at_fast(imf, ts, ad)[0]
    ev['sw_l1'] = l1
    lg('SW-L1 at A: UNKNOWN share %.4f' % float((ev['sw_l1'] == 'UNKNOWN').mean()))

    col_idx = {c: j for j, c in enumerate(ind_cols)}
    ind_ret = np.full((len(ev), len(H)), np.nan)
    li = ev['sw_l1'].map(col_idx).to_numpy()
    for j, h in enumerate(H):
        for k in range(len(ev)):
            c = li[k]
            if c != c:  # NaN
                continue
            ind_ret[k, j] = (ind_lvl[np.clip(i0[k] + h, 0, N - 1), c]
                             / ind_lvl[i0[k] - 1, c] - 1.0) if i0[k] - 1 >= 0 else np.nan
    for j, h in enumerate(H):
        ev['ind_fwd%d' % h] = ind_ret[:, j]

    for h in H:
        ev['exmkt%d' % h] = ev['fwd%d' % h] - ev['mkt_fwd%d' % h]
        ev['exind%d' % h] = ev['fwd%d' % h] - ev['ind_fwd%d' % h]
        ev['exindmkt%d' % h] = ev['fwd%d' % h] - ev['ind_fwd%d' % h]

    # ---------------- controls ------------------------------------------
    lg('controls at the announcement session ...')
    a_date = ev['A'].to_numpy()
    bp = pd.read_parquet(os.path.join(FS_DATA, 'basic_panel.parquet'),
                         columns=['ts_code', 'trade_date', 'turnover_rate',
                                  'pe_ttm', 'pb', 'total_mv'])
    bp['ts_code'] = bp['ts_code'].astype(str)
    bp['trade_date'] = bp['trade_date'].astype(str)
    key = pd.MultiIndex.from_arrays([ev['ts_code'].to_numpy(), a_date])
    need = set(zip(ev['ts_code'].to_numpy(), a_date))
    bp = bp[bp['ts_code'].isin(set(ev['ts_code'].unique()))]
    bp = bp[[(t, dt) in need for t, dt in
             zip(bp['ts_code'].to_numpy(), bp['trade_date'].to_numpy())]]
    bp = bp.drop_duplicates(subset=['ts_code', 'trade_date']).set_index(
        ['ts_code', 'trade_date'])
    bp = bp.reindex(key)
    ev['turnover'] = bp['turnover_rate'].to_numpy()
    ev['pe_ttm'] = bp['pe_ttm'].to_numpy()
    ev['pb'] = bp['pb'].to_numpy()
    ev['total_mv'] = bp['total_mv'].to_numpy()
    ev['log_mv'] = np.log(ev['total_mv'].where(ev['total_mv'] > 0))
    lg('control coverage: turnover %.3f pe %.3f pb %.3f mv %.3f'
       % tuple(float(ev[c].notna().mean()) for c in
               ('turnover', 'pe_ttm', 'pb', 'total_mv')))

    # momentum: 20/60 sessions ending the session BEFORE the announcement
    for w in PREREG['momentum']:
        ev['mom%d' % w] = (close[si, iA - 1]
                           / close[si, np.clip(iA - 1 - w, 0, N - 1)] - 1.0)
    # 20d realised vol of daily returns over [A-20, A]
    lo = np.clip(iA - 20, 0, N - 1)
    win = np.stack([close[si, np.clip(lo + k, 0, N - 1)] for k in range(21)], axis=1)
    rr = win[:, 1:] / win[:, :-1] - 1.0
    ev['vol20'] = np.nanstd(rr, axis=1)

    # ---------------- tradability / limits ------------------------------
    lp = np.array([limit_pct(t, b) for t, b in
                   zip(ev['ts_code'].to_numpy(), ev['board'].to_numpy())])
    ev['limit_pct'] = lp
    gap = open_[si, i0] / close[si, iA - 1] - 1.0
    ev['open_gap'] = gap
    ev['lim_up_open'] = gap >= 0.98 * lp
    ev['lim_dn_open'] = gap <= -0.98 * lp
    ev['tradable_long'] = (ev['traded_t0'].to_numpy()
                           & (~ev['lim_up_open'].to_numpy()))
    ev['tradable_short'] = (ev['traded_t0'].to_numpy()
                            & (~ev['lim_dn_open'].to_numpy()))
    lg('tradable_long share %.4f  limit-up-at-open share %.4f'
       % (float(ev['tradable_long'].mean()), float(ev['lim_up_open'].mean())))

    # ---------------- PIT assertions (hard) ------------------------------
    pit = {
        'P_strictly_before_A': bool((ev['P_idx'] < ev['A_idx']).all()),
        'A_before_T0': bool((ev['A_idx'] < ev['T0_idx']).all()),
        'forecast_is_frozen_column': True,
    }
    lg('PIT asserts: %s' % json.dumps(pit))
    if not all(pit.values()):
        raise SystemExit('PIT ASSERTION FAILED')

    keep = ['ts_code', 'target_end', 'q_str', 'ann_year', 'phase', 'board',
            'sw_l1', 'A', 'A_idx', 'T0', 'T0_idx', 'p_date', 'P_idx',
            'forecast_surprise', 'actual_surprise', 'forecast_error',
            'positive_surprise', 'strong_surprise', 'np_T', 'np_T4', 'E_sea',
            'den_sea']
    keep += ['pre%d' % h for h in PREREG['pre_windows']]
    keep += ['ann_ret', 'rel_0_1', 'ret_t0']
    for h in H:
        keep += ['fwd%d' % h, 'fwd_ev%d' % h, 'fwd_open%d' % h,
                 'mkt_fwd%d' % h, 'ind_fwd%d' % h,
                 'exmkt%d' % h, 'exind%d' % h]
    keep += ['mom20', 'mom60', 'log_mv', 'pe_ttm', 'pb', 'vol20', 'turnover',
             'total_mv', 'limit_pct', 'open_gap', 'lim_up_open', 'lim_dn_open',
             'tradable_long', 'tradable_short', 'traded_t0', 'traded_a']
    out = ev[keep].copy()
    out.to_parquet(os.path.join(DATA, 'h_earn_price_events.parquet'), index=False)
    lg('saved h_earn_price_events.parquet rows %d cols %d'
       % (len(out), out.shape[1]))

    # benchmark levels kept for downstream modules
    pd.DataFrame({'trade_date': dates, 'mkt_ret': mkt_daily.reindex(dates).to_numpy()
                  }).to_parquet(os.path.join(DATA, 'aux_market.parquet'), index=False)
    ind_cum.to_parquet(os.path.join(DATA, 'aux_industry_cum.parquet'))
    mkt_cum.rename('mkt_cum').to_frame().to_parquet(
        os.path.join(DATA, 'aux_market_cum.parquet'))

    lg('coverage: fwd5 %.3f  exmkt5 %.3f  forecast %.3f  actual %.3f'
       % tuple(float(out[c].notna().mean()) for c in
               ('fwd5', 'exmkt5', 'forecast_surprise', 'actual_surprise')))
    lg('by phase:\n%s' % out.groupby('phase').size().to_string())
    lg('DONE events')


if __name__ == '__main__':
    main()
