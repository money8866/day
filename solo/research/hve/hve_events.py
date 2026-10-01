# -*- coding: utf-8 -*-
"""hve_events -- spec sections 4 / 5 / 6 / 7.

Deliverable
    01_hve_event_dataset.csv  -- the HVE *definition* study: one row per
    pre-registered candidate definition (volume indicator x threshold x
    event family x de-duplication setting) with its sample size, its
    IS / VALID / OOS outcome and its net-of-cost outcome.

Why the definition study is the "event dataset"
    Section 37-II asks the HVE-definition study to output
    候选定义 / 样本量 / OOS结果 / Robust Range.  That is exactly this table.
    The per-event rows for the pre-registered primary definition are written
    to data/hve_primary_events.parquet and consumed by hve_path / hve_hvt /
    hve_w7; they are an intermediate, not a numbered deliverable.

Discipline
    * No threshold is selected by its own performance.  The definition used
      downstream (PRIMARY) is fixed by PREREG before this file runs.  On top
      of it the table also reports SELECTED_BY_IS, i.e. the candidate that a
      naive "pick the best in-sample threshold" protocol would have chosen,
      together with its OOS outcome: that is the section 25 trap test, and it
      is reported, never acted upon.
    * Every mask uses data up to and including the event session only.
"""
import os
import sys
import json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hve_common import (PREREG, Log, build_grid, build_indicators,   # noqa
                        eligibility, cluster_events, cluster_events_ranked,
                        fwd_matrix, mfe_mae_matrix, perf_stats, save_csv,
                        save_json, DATA, Q_IS, Q_VALID, Q_OOS,
                        CACHE_VERSION)                               # noqa

lg = Log('hve_events')
F32 = 'float32'

# ---------------------------------------------------------------------------
# Checkpoint.  A full pass is ~130 candidate definitions and takes ~16 min on
# a grid of 12.3M cells; losing that to a closed terminal is a pure waste.
# Every section dumps its rows and is skipped when the checkpoint key still
# matches -- the key covers everything that would change a row, so editing a
# threshold or the primary definition automatically invalidates the file.
# ---------------------------------------------------------------------------
CKPT = os.path.join(DATA, 'hve_events_ckpt.json')


def _ckpt_key():
    # CACHE_VERSION is part of the key: a checkpoint written against a
    # different indicator definition would otherwise be resumed silently.
    return json.dumps({'primary': PRIMARY,
                       'cache_version': CACHE_VERSION,
                       'vol_ind': list(PREREG['volume_indicators']),
                       'ratio': list(PREREG['ratio_extended']),
                       'pct_thr': list(PREREG['pct_thresholds']),
                       'gap': list(PREREG['cluster_gap']),
                       'anchor': list(PREREG['cluster_anchor']),
                       'families': list(PREREG['event_types']),
                       'seed': PREREG['seed']}, sort_keys=True)

# ---------------------------------------------------------------------------
# Pre-registered primary definition (fixed before any result was inspected).
# It is the anchor of the downstream modules; it is NOT the best candidate.
# ---------------------------------------------------------------------------
PRIMARY = {'indicator': 'VR20', 'thr': 2.0, 'family': 'HVE_C',
           'gap': PREREG['cluster_gap_primary'],
           'anchor': PREREG['cluster_anchor_primary']}


def main():
    g = build_grid(lg=lg)
    ind, px, ma = build_indicators(g, lg=lg)
    el = eligibility(g)
    S, N = g['close'].shape
    lg('grid S=%d N=%d  eligibility %.4f' % (S, N, el.mean()))

    # ---- forward outcomes (computed once, reused by every candidate) ----
    R = {h: fwd_matrix(g, h).astype(F32) for h in (3, 5, 10, 20)}
    K = PREREG['primary_horizon']
    MFE, MAE = mfe_mae_matrix(g, K)
    MFE = MFE.astype(F32)
    MAE = MAE.astype(F32)
    lg('forward matrices ready')

    yrs = g['dates'].astype('U4')
    # 'first' needs no rank matrix: cluster_events picks the leading cell
    # directly.  For the two ranked anchors the grid's own float32 volume /
    # amount columns *are* the ordering, so they are used in place instead
    # of being copied into float64 tables (2 x 99 MB before, 0 MB now).
    RANK = {'first': None, 'max_vol': g['vol'], 'max_amount': g['amount']}

    # ---- gates ---------------------------------------------------------
    pct = g['pct_chg']
    clv, body, ma20 = px['clv'], px['body'], px['ma20']
    pg = PREREG['price_gate_C']
    gate_B = np.abs(pct) >= PREREG['price_gate_B']['abs_pct_chg']
    gate_C = ((pct >= pg['pct_chg_min']) & (clv >= pg['clv_min']) &
              (body >= pg['body_min']) & (g['close'] > ma20))
    lg('gates: B |pct|>=%.2f  %.4f | C  %.4f'
       % (PREREG['price_gate_B']['abs_pct_chg'], (gate_B & el).mean(),
          (gate_C & el).mean()))

    GATE = {'HVE_A': None, 'HVE_B': gate_B, 'HVE_C': gate_C}

    def vol_mask(name, thr, family):
        m = (ind[name] >= thr) & el
        gt = GATE[family]
        return m if gt is None else (m & gt)

    # ---- one evaluation row per candidate definition --------------------
    rows = []
    ck = None
    if os.path.exists(CKPT):
        with open(CKPT, encoding='utf-8') as f:
            ck = json.load(f)
        if ck.get('key') != _ckpt_key():
            lg('checkpoint key mismatch -> recompute everything')
            ck = None
    done = set(ck['done']) if ck else set()
    if ck:
        rows = ck['rows']
        lg('resume from checkpoint: %d rows, sections done %s'
           % (len(rows), sorted(done)))

    def flush(section):
        done.add(section)
        with open(CKPT, 'w', encoding='utf-8') as f:
            json.dump({'key': _ckpt_key(), 'done': sorted(done),
                       'rows': rows}, f, ensure_ascii=False)
        lg('checkpoint %s (%d rows)' % (section, len(rows)))

    def perf_block(si, di, tag, **meta):
        d = {'tag': tag}
        d.update(meta)
        n = len(si)
        d['n_events'] = n
        d['n_stocks'] = int(np.unique(si).size) if n else 0
        yr = yrs[di]
        d['n_years'] = int(np.unique(yr).size)
        d['ev_per_stock_yr'] = (round(n / max(1.0, d['n_stocks'] *
                                             max(1, d['n_years'])), 3)
                                if n else np.nan)
        for h in (3, 5, 20):
            v = R[h][si, di].astype('float64')
            d['mean_r%d' % h] = float(np.nanmean(v)) if n else np.nan
        r10 = R[10][si, di].astype('float64') if n else np.array([])
        s0 = perf_stats(r10, 0)
        s30 = perf_stats(r10, PREREG['primary_cost_bp'])
        for k in ('mean', 'median', 'win_rate', 'pf', 'tail_share'):
            d['r10_%s' % k] = s0[k]
        d['r10_mean_net30'] = s30['mean']
        d['r10_win_net30'] = s30['win_rate']
        d['r10_pf_net30'] = s30['pf']
        d['mfe10_med'] = float(np.nanmedian(MFE[si, di])) if n else np.nan
        d['mae10_med'] = float(np.nanmedian(MAE[si, di])) if n else np.nan
        for ph, (a, b) in (('IS', Q_IS), ('VALID', Q_VALID), ('OOS', Q_OOS)):
            m = (yr >= str(a)) & (yr <= str(b))
            d['n_' + ph] = int(m.sum())
            d['mean_r10_' + ph] = (float(np.nanmean(r10[m]))
                                   if m.any() else np.nan)
            d['win_r10_' + ph] = (float(np.nanmean(r10[m] > 0))
                                  if m.any() else np.nan)
        rows.append(d)
        return d

    def run(tag, section, family, indicator, thr_kind, thr, gap, anchor,
            **meta):
        m = vol_mask(indicator, thr, family)
        n_raw = int(m.sum())
        rk = RANK[anchor]
        m2 = (cluster_events(m, gap) if anchor == 'first'
              else cluster_events_ranked(m, gap, rk))
        si, di = np.nonzero(m2)
        d = perf_block(si, di, tag, section=section, family=family,
                       indicator=indicator, thr_kind=thr_kind, thr=thr,
                       gap=gap, anchor=anchor, n_raw=n_raw, **meta)
        lg('  %-34s raw %8d -> ev %7d  r10 %+.4f  win %.3f  OOS %+.4f'
           % (tag, n_raw, d['n_events'], d['r10_mean'],
              d['r10_win_rate'], d['mean_r10_OOS']))
        return d

    def tagof(section, family, indicator, thr_kind, thr, gap, anchor):
        return '%s|%s|%s=%s|g%d|%s' % (section, family, indicator,
                                       (('%.4g' % thr) if thr_kind == 'PCT'
                                        else ('%.2f' % thr)),
                                       gap, anchor)

    GAP, ANC = PREREG['cluster_gap_primary'], PREREG['cluster_anchor_primary']

    FAM_SHOCKS = (('VR20', 'ABS', 1.5), ('VR20', 'ABS', 2.0),
                  ('VR20', 'ABS', 2.5), ('VR20', 'ABS', 3.0),
                  ('VOL_PCT', 'PCT', 0.95), ('VOL_PCT', 'PCT', 0.99))

    # Section 6 price conditions as thunks, so that only one (S, N) mask is
    # alive at a time instead of ten.
    PC_DEF = {
        'PC_abs_pct3': lambda: np.abs(pct) >= 0.03,
        'PC_up_pct3': lambda: pct >= 0.03,
        'PC_dn_pct3': lambda: pct <= -0.03,
        'PC_close_gt_open': lambda: g['close'] > g['open'],
        'PC_clv70': lambda: clv >= 0.70,
        'PC_clv50': lambda: clv >= 0.50,
        'PC_body30': lambda: body >= 0.30,
        'PC_gt_ma20': lambda: g['close'] > ma20,
        'PC_gt_hi20': lambda: g['close'] > px['hi20'],
        'PC_gt_hi60': lambda: g['close'] > px['hi60'],
    }
    PC_ORDER = list(PC_DEF)

    # ---- section 4: volume shock candidates (HVE-A, volume only) --------
    def sec4():
        lg('== section 4: volume candidate scan ==')
        for nm in PREREG['volume_indicators']:
            is_pct = nm in PREREG['pct_indicators']
            tl = (PREREG['pct_thresholds'] if is_pct
                  else PREREG['ratio_extended'])
            kind = 'PCT' if is_pct else 'ABS'
            for thr in tl:
                run(tagof('S4_VOL_ONLY', 'HVE_A', nm, kind, thr, GAP, ANC),
                    'S4_VOL_ONLY', 'HVE_A', nm, kind, thr, GAP, ANC)

    # ---- section 5: HVE-A / B / C on representative volume shocks -------
    def sec5():
        lg('== section 5: event families ==')
        for nm, kind, thr in FAM_SHOCKS:
            for fam in PREREG['event_types']:
                run(tagof('S5_FAMILY', fam, nm, kind, thr, GAP, ANC),
                    'S5_FAMILY', fam, nm, kind, thr, GAP, ANC)

    # ---- section 6: price condition study on one fixed volume shock -----
    def sec6():
        # The volume leg is held constant (VR20 >= 2.0, HVE-A) and each price
        # condition is added on its own, so the increment is attributable.
        lg('== section 6: price conditions ==')
        base6 = (ind['VR20'] >= 2.0) & el
        si6, di6 = np.nonzero(cluster_events_ranked(base6, GAP, RANK[ANC]))
        perf_block(si6, di6, tagof('S6_PRICE', 'HVE_A', 'VR20', 'ABS', 2.0,
                                   GAP, ANC) + '|no_price_gate',
                   section='S6_PRICE', family='HVE_A', indicator='VR20',
                   thr_kind='ABS', thr=2.0, gap=GAP, anchor=ANC,
                   price_cond='NONE', n_raw=int(base6.sum()))
        # One condition at a time: ten live (S, N) bool masks are 123 MB that
        # this box cannot spare while the whole grid is still resident.
        for k in PC_ORDER:
            m = base6 & PC_DEF[k]()
            si, di = np.nonzero(cluster_events_ranked(m, GAP, RANK[ANC]))
            perf_block(si, di, tagof('S6_PRICE', 'HVE_A', 'VR20', 'ABS', 2.0,
                                     GAP, ANC) + '|' + k,
                       section='S6_PRICE', family='HVE_A', indicator='VR20',
                       thr_kind='ABS', thr=2.0, gap=GAP, anchor=ANC,
                       price_cond=k, n_raw=int(m.sum()))
            del m

    # ---- section 7: de-duplication (gap x anchor) ----------------------
    def sec7():
        lg('== section 7: de-duplication ==')
        for fam in PREREG['event_types']:
            m = vol_mask('VR20', 2.0, fam)
            for gap in PREREG['cluster_gap']:
                for anc in PREREG['cluster_anchor']:
                    m2 = (cluster_events(m, gap) if anc == 'first'
                          else cluster_events_ranked(m, gap, RANK[anc]))
                    si, di = np.nonzero(m2)
                    perf_block(si, di, tagof('S7_DEDUP', fam, 'VR20', 'ABS',
                                             2.0, gap, anc),
                               section='S7_DEDUP', family=fam,
                               indicator='VR20', thr_kind='ABS', thr=2.0,
                               gap=gap, anchor=anc, n_raw=int(m.sum()))
            del m

    for _name, _fn in (('S4_VOL_ONLY', sec4), ('S5_FAMILY', sec5),
                       ('S6_PRICE', sec6), ('S7_DEDUP', sec7)):
        if _name in done:
            lg('== %s: skipped, already in checkpoint ==' % _name)
            continue
        _fn()
        flush(_name)

    # ---- baselines -----------------------------------------------------
    def secbase():
        lg('== baselines ==')
        si_all, di_all = np.nonzero(el)
        perf_block(si_all, di_all, 'BASELINE|all_eligible_days',
                   section='BASELINE', family='RANDOM', indicator='NONE',
                   thr_kind='NONE', thr=np.nan, gap=np.nan, anchor='NONE',
                   n_raw=int(el.sum()))
        del si_all, di_all
        rng = np.random.default_rng(PREREG['seed'])
        eq = np.flatnonzero(el.ravel())
        draws = []
        for _ in range(10):
            pick = rng.choice(eq, size=50000, replace=False)
            s_, d_ = np.unravel_index(pick, (S, N))
            draws.append(perf_stats(R[10][s_, d_].astype('float64'), 0))
        rows.append({'tag': 'BASELINE|random_event_50k_avg10', 'section':
                     'BASELINE', 'family': 'RANDOM', 'indicator': 'NONE',
                     'thr_kind': 'NONE', 'thr': np.nan, 'gap': np.nan,
                     'anchor': 'NONE', 'n_raw': int(el.sum()),
                     'n_events': 50000,
                     'r10_mean': float(np.mean([d['mean'] for d in draws])),
                     'r10_median': float(np.mean([d['median']
                                                  for d in draws])),
                     'r10_win_rate': float(np.mean([d['win_rate']
                                                    for d in draws])),
                     'r10_pf': float(np.mean([d['pf'] for d in draws])),
                     'r10_tail_share': float(np.mean([d['tail_share']
                                                      for d in draws]))})
        del draws, eq
        lg('random-event 50k baseline r10 mean %+.4f win %.3f'
           % (rows[-1]['r10_mean'], rows[-1]['r10_win_rate']))

    if 'BASELINE' in done:
        lg('== baselines: skipped, already in checkpoint ==')
    else:
        secbase()
        flush('BASELINE')

    df = pd.DataFrame(rows)

    # ---- section 25 trap test: what a naive IS-best pick would have done --
    pool = df[(df['section'].isin(('S4_VOL_ONLY', 'S5_FAMILY'))) &
              (df['gap'] == GAP) & (df['anchor'] == ANC) &
              (df['n_IS'] >= PREREG['min_events'])].copy()
    sel = None
    if len(pool):
        j = pool['mean_r10_IS'].idxmax()
        sel = df.loc[j, 'tag']
        df['role'] = np.where(df['tag'] == sel, 'SELECTED_BY_IS', 'CANDIDATE')
        df.loc[df['tag'].str.startswith('BASELINE'), 'role'] = 'BASELINE'
        lg('naive IS-best = %s  (IS %+.4f -> OOS %+.4f)'
           % (sel, df.loc[j, 'mean_r10_IS'], df.loc[j, 'mean_r10_OOS']))
        is_med = float(pool['mean_r10_IS'].median())
        oos_c = float(np.nanmedian(pool['mean_r10_OOS']))
        lg('candidate pool IS median %+.4f | OOS median %+.4f | n_pool %d'
           % (is_med, oos_c, len(pool)))
    else:
        df['role'] = 'CANDIDATE'

    # ---- pre-registered primary ----------------------------------------
    pt = tagof('S5_FAMILY', PRIMARY['family'], PRIMARY['indicator'], 'ABS',
               PRIMARY['thr'], PRIMARY['gap'], PRIMARY['anchor'])
    df.loc[df['tag'] == pt, 'role'] = 'PRIMARY'
    lg('PRIMARY = %s  (role flag set: %d)'
       % (pt, int((df['role'] == 'PRIMARY').sum())))

    # ---- per-event table for the downstream modules ---------------------
    cov = ['VR5', 'VR10', 'VR20', 'VR60', 'VOL_MA20', 'VOL_MA60',
           'AMT_MA20', 'VOL_PCT', 'AMT_PCT', 'TURN', 'TURN_PCT']
    m = vol_mask(PRIMARY['indicator'], PRIMARY['thr'], PRIMARY['family'])
    m2 = cluster_events_ranked(m, PRIMARY['gap'], RANK[PRIMARY['anchor']])
    si, di = np.nonzero(m2)
    ev = pd.DataFrame({'s_i': si.astype('int32'), 't0': di.astype('int32')})
    ev['ts_code'] = g['codes'][si]
    ev['t0_date'] = g['dates'][di]
    ev['year'] = ev['t0_date'].str[:4].astype(int)
    ev['board'] = g['board'][si]
    ev['amount'] = g['amount'][si, di]
    ev['pct_chg'] = g['pct_chg'][si, di]
    for k in ('clv', 'body', 'r1', 'r20', 'r60'):
        ev[k] = px[k][si, di]
    ev['close_gt_ma20'] = (g['close'][si, di] > ma20[si, di])
    ev['close_gt_hi20'] = (g['close'][si, di] > px['hi20'][si, di])
    ev['close_gt_hi60'] = (g['close'][si, di] > px['hi60'][si, di])
    for k in cov:
        ev['ind_' + k] = ind[k][si, di]
    for h in (3, 5, 10, 20):
        ev['r%d' % h] = R[h][si, di]
    ev['mfe10'] = MFE[si, di]
    ev['mae10'] = MAE[si, di]
    ev['phase'] = np.where(ev['year'] < Q_IS[0], 'PRE',
                           np.where(ev['year'] <= Q_IS[1], 'IS',
                                    np.where(ev['year'] <= Q_VALID[1],
                                             'VALID', 'OOS')))
    ev.to_parquet(os.path.join(DATA, 'hve_primary_events.parquet'),
                  index=False)
    lg('primary event table %d rows -> hve_primary_events.parquet' % len(ev))

    save_csv(df, '01_hve_event_dataset.csv')
    save_json({'primary': PRIMARY, 'primary_tag': pt,
               'selected_by_IS': sel,
               'n_candidates': int((df['role'] == 'CANDIDATE').sum()),
               'n_primary_events': int(len(ev)),
               'primary_stats': df[df['role'] == 'PRIMARY'].to_dict('records'),
               'lookahead_note': 'all masks use data <= t0; R/MFE/MAE are '
                                 'outcomes only'},
              '01_hve_event_summary.json')
    lg('rows written %d ; sections %s'
       % (len(df), sorted(df['section'].unique())))

    # Reached only when every section has been written out, so the
    # checkpoint has served its purpose: drop it so the next run recomputes
    # against whatever the code says then.
    if os.path.exists(CKPT):
        os.remove(CKPT)
    lg('done; checkpoint cleared')


if __name__ == '__main__':
    main()
