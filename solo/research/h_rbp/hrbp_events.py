# -*- coding: utf-8 -*-
"""H-RBP-01 step 1 -- the event anchor table.

HVE is downgraded to an *observation anchor*: this module only defines
`event_date = t0`.  It makes no claim that the anchor carries alpha.

Anchor (spec section 4):
    VR20_excl = Volume[t] / mean(Volume[t-20 : t-1])
    CLV       = (Close - Low) / (High - Low)
    VR20_excl >= 2.00 AND r1 >= 0.02 AND Close > Open AND CLV >= 0.65
then cluster-deduplicated (gap 5 sessions, representative = max amount) so
that "one event" is one independent episode.
"""
import numpy as np
import pandas as pd

from hrbp_common import (H, PREREG_H, DATA, Log, eligibility_h, vr20_excl_panel,
                         save_csv, save_json)

OUT_PQ = DATA + '/hrbp_events.parquet'


def main():
    lg = Log('hrbp_events')
    lg('H-RBP-01 / %s  v%s  spec frozen %s'
       % (PREREG_H['hypothesis_id'], PREREG_H['version'],
          PREREG_H['created']))
    lg.sep('=')

    g = H.build_grid(lg=lg, use_cache=True)
    ind, px, ma = H.build_indicators(g, lg=lg, use_cache=True)
    el = eligibility_h(g, lg=lg)
    S, N = g['close'].shape
    lg('panel S=%d N=%d  %s .. %s' % (S, N, g['dates'][0], g['dates'][-1]))

    vr = vr20_excl_panel(g)
    close, low, high = g['close'], g['low'], g['high']
    hl = (high - low).astype('float64')
    clv = np.where(hl > 0, (close - low) / hl, np.nan)
    r1 = px['r1']
    above_open = (close > g['open'])

    day_ok = np.zeros(N, dtype=bool)
    ds = np.searchsorted(g['dates'], PREREG_H['study_start'], side='left')
    day_ok[ds:] = True

    core = (np.isfinite(vr) & np.isfinite(clv) & np.isfinite(r1) & above_open)
    raw = (core & el & (vr >= PREREG_H['anchor_vr20_min'])
           & (r1 >= PREREG_H['anchor_ret_min'])
           & (clv >= PREREG_H['anchor_clv_min']) & day_ok[None, :])
    lg('raw anchor cells  VR20_excl>=%.2f  r1>=%.2f  CLV>=%.2f : %d'
       % (PREREG_H['anchor_vr20_min'], PREREG_H['anchor_ret_min'],
          PREREG_H['anchor_clv_min'], int(raw.sum())))

    for nm, cond in (('VR20_excl>=2', vr >= PREREG_H['anchor_vr20_min']),
                     ('r1>=2%', r1 >= PREREG_H['anchor_ret_min']),
                     ('Close>Open', above_open),
                     ('CLV>=0.65', clv >= PREREG_H['anchor_clv_min'])):
        m = el & day_ok[None, :] & np.isfinite(vr) & np.isfinite(clv)
        lg('  gate %-12s passes %.4f of the eligible panel'
           % (nm, float((m & cond).sum()) / max(1.0, float(m.sum()))))

    rank = np.where(np.isfinite(g['amount']), g['amount'], -np.inf)
    ded = H.cluster_events_ranked(raw, PREREG_H['anchor_cluster_gap'], rank)
    lg('cluster dedup gap=%d anchor=%s : %d -> %d events'
       % (PREREG_H['anchor_cluster_gap'],
          PREREG_H['anchor_cluster_anchor'], int(raw.sum()), int(ded.sum())))

    ev = H.events_from_mask(g, ded)
    # right-edge flag (spec section 3): the whole path plus T+20 must fit
    need = PREREG_H['window'] + 1 + max(PREREG_H['horizons'])
    ev['t0_last_ok'] = (ev['t0'] + need) <= (N - 1)
    ev['vr20_excl'] = vr[ev['s_i'].to_numpy(), ev['t0'].to_numpy()]
    ev['clv'] = clv[ev['s_i'].to_numpy(), ev['t0'].to_numpy()]
    ev['r1'] = r1[ev['s_i'].to_numpy(), ev['t0'].to_numpy()]
    ev['phase'] = [_ph(y) for y in ev['year']]

    lg('events %d  (%s .. %s)  usable(right edge) %d  dropped %d'
       % (len(ev), ev['t0_date'].min(), ev['t0_date'].max(),
          int(ev['t0_last_ok'].sum()), int((~ev['t0_last_ok']).sum())))
    lg('board mix  %s' % ev['board'].value_counts().to_dict())
    lg('year mix   %s' % ev['year'].value_counts().sort_index().to_dict())

    ev.to_parquet(OUT_PQ, index=False)
    lg('event table %d rows -> %s' % (len(ev), OUT_PQ))

    save_csv(ev.drop(columns=['phase']).head(5000), '10_hrbp_events_sample.csv')
    save_json({
        'hypothesis_id': PREREG_H['hypothesis_id'],
        'anchor_rule': PREREG_H['anchor_rule'],
        'anchor_vr20_excludes_today': PREREG_H['anchor_vr20_excludes_today'],
        'n_raw_cells': int(raw.sum()),
        'n_events': int(len(ev)),
        'n_usable_right_edge': int(ev['t0_last_ok'].sum()),
        'n_drop_right_edge': int((~ev['t0_last_ok']).sum()),
        'board_mix': {str(k): int(v) for k, v in
                      ev['board'].value_counts().items()},
        'year_mix': {str(k): int(v) for k, v in
                     ev['year'].value_counts().sort_index().items()},
        'first': str(ev['t0_date'].min()), 'last': str(ev['t0_date'].max()),
        'lookahead_note': 'the anchor uses only sessions <= t0; no future '
                          'field exists in this table',
    }, '11_hrbp_events_summary.json')
    lg('done')


def _ph(y):
    from hrbp_common import phase_of_year
    return phase_of_year(int(y))


if __name__ == '__main__':
    main()
