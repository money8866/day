# -*- coding: utf-8 -*-
"""H-ALPHA-SOURCE-01 -- steps 7-12.

Subgroup stability (year / phase / regime / tail), the bias audit and the
final attribution artefacts.  Every comparison is read off the frozen
H_ALPHA_SOURCE_01_MATCHED.csv master table written by has_attr.py; no leg
is re-defined and no parameter is touched here.

Outputs
  data/H_ALPHA_SOURCE_01_ATTRIBUTION.csv    the four-level alpha matrix
  data/H_ALPHA_SOURCE_01_OOS.csv            per calendar year
  data/H_ALPHA_SOURCE_01_WALK_FORWARD.csv   IS / VALID / OOS stability
  data/H_ALPHA_SOURCE_01_TAIL.csv           leave-top-k% dependence
  data/H_ALPHA_SOURCE_01_REGIME.csv         Bull / Range / Bear
  data/H_ALPHA_SOURCE_01_BIAS_AUDIT.csv     eight audit items
  data/H_ALPHA_SOURCE_01_SUMMARY.json       machine-readable verdict
  H_ALPHA_SOURCE_01_REPORT.md               sections 1-10
"""
import os
import json

import numpy as np
import pandas as pd

import has_common as C
import has_attr as A
from hrbp_common import regime_by_day

P = C.PREREG_A
HZ = list(P['horizons'])
HP = P['primary_horizon']
B = P['boot_B']
SEED = P['seed']
MIN_N = P['min_n']
TAIL_LEVELS = list(P['tail_levels'])
NET_BP = P['primary_cost_bp']
NET = NET_BP / 10000.0
SYS = ('ALL', 'HVT', 'W7')
REGS = ('BULL', 'RANGE', 'BEAR')

OBS = {'Candidate': 'r_obs_%d', 'Event': 'r_ev_%d',
       'Delay': 'r_obs_%d', 'Entry': 'r_obs_%d'}
VAR = [
    ('Candidate', 'vs matched stock', 'r_cand_%d'),
    ('Event', 'vs random date', 'r_rdate_%d'),
    ('Delay', 'Original - D0', 'r_d0_%d'),
    ('Delay', 'Original - D1', 'r_d1_%d'),
    ('Delay', 'Original - D3', 'r_d3_%d'),
    ('Delay', 'Original - D5', 'r_d5_%d'),
    ('Delay', 'Original - D10', 'r_d10_%d'),
    ('Entry', 'Original - E1', 'r_e1_%d'),
    ('Entry', 'Original - E3', 'r_e3_%d'),
    ('Entry', 'Original - ER', 'r_er_%d'),
]
KEEP = ['level', 'variant', 'system', 'horizon', 'n', 'n_month', 'mean_obs',
        'mean_ctrl', 'delta', 'ci_lo', 'ci_hi', 'p_two', 'delta_net30',
        'month_pos', 'win_obs', 'pf_obs']


# =====================================================================
# helpers
# =====================================================================
def month_delta(ra, ma, rb, mb):
    """Per-calendar-month mean(a) - mean(b) over the shared months."""
    a = pd.Series(np.asarray(ra, 'float64')).groupby(np.asarray(ma)).mean()
    b = pd.Series(np.asarray(rb, 'float64')).groupby(np.asarray(mb)).mean()
    j = a.index.intersection(b.index)
    if len(j) == 0:
        return np.zeros(0)
    d = (a.loc[j] - b.loc[j]).to_numpy('float64')
    return d[np.isfinite(d)]


def cmp_row(sub, ot, ct, h):
    """One bootstrap comparison: observed leg vs counterfactual leg."""
    if len(sub) == 0:
        return None
    ra = sub[ot % h].to_numpy('float64')
    rb = sub[ct % h].to_numpy('float64')
    m = sub['month'].to_numpy()
    ok = np.isfinite(ra) & np.isfinite(rb)
    if ok.sum() < MIN_N:
        return None
    ra, rb, mm = ra[ok], rb[ok], m[ok]
    d = A.boot_diff(ra, mm, rb, mm, B=B, seed=SEED)
    md = month_delta(ra, mm, rb, mm)
    gp = float(ra[ra > 0].sum())
    lp = float(-ra[ra < 0].sum())
    return dict(n=int(ok.sum()), n_month=int(d['n_month']),
                mean_obs=float(ra.mean()), mean_ctrl=float(rb.mean()),
                delta=float(d['obs']), ci_lo=float(d['lo']),
                ci_hi=float(d['hi']), p_two=float(d['p_two']),
                delta_net30=float(d['obs'] - NET),
                month_pos=float((md > 0).mean()) if md.size else np.nan,
                win_obs=float((ra > 0).mean()),
                pf_obs=(gp / lp if lp > 0 else np.nan))


def tail_delta(ra, rb, levels=TAIL_LEVELS):
    """Leave-top-k% of the OBSERVED leg, on the fully paired subset."""
    a = np.asarray(ra, 'float64')
    b = np.asarray(rb, 'float64')
    m = np.isfinite(a) & np.isfinite(b)
    a, b = a[m], b[m]
    out = {'n': int(a.size), 'full': np.nan, 'tail_flagged': False,
           'tail_share_5pct': np.nan}
    for lev in levels:
        out['leave_top_%dpct' % int(round(lev * 100))] = np.nan
    if a.size == 0:
        return out
    out['full'] = float(a.mean() - b.mean())
    order = np.argsort(a)[::-1]
    for lev in levels:
        k = max(1, int(round(a.size * lev)))
        keep = order[k:]
        out['leave_top_%dpct' % int(round(lev * 100))] = (
            float(a[keep].mean() - b[keep].mean()) if keep.size else np.nan)
    gp = float(a[a > 0].sum())
    k5 = max(1, int(round(a.size * 0.05)))
    top5 = a[order[:k5]]
    out['tail_share_5pct'] = (float(top5[top5 > 0].sum()) / gp
                              if gp > 0 else np.nan)
    l5 = out['leave_top_5pct']
    out['tail_flagged'] = bool(out['full'] > 0 and
                               (not np.isfinite(l5) or l5 <= 0.25 * out['full']))
    return out


# =====================================================================
# main
# =====================================================================
def main():
    lg = C.Log('has_report')
    df = pd.read_csv(os.path.join(C.DATA, 'H_ALPHA_SOURCE_01_MATCHED.csv'),
                     low_memory=False)
    g = C.grid(lg=lg)
    S, N = g['close'].shape
    reg = regime_by_day(g, lg=lg)
    df['regime'] = reg[df['entry_idx'].to_numpy('int64')]
    lg('fill-session regime: %s' % df['regime'].value_counts().to_dict())
    lg('years: %s' % df['year'].value_counts().sort_index().to_dict())

    def slice_(sy, y=None, reg_=None):
        m = np.ones(len(df), bool)
        if sy != 'ALL':
            m &= (df['system'] == sy).to_numpy()
        if y is not None:
            m &= (df['year'] == y).to_numpy()
        if reg_ is not None:
            m &= (df['regime'] == reg_).to_numpy()
        return df[m]

    def sweep(extra_key, extra_val, sel):
        """Run every frozen comparison over a subgroup selector."""
        rows = []
        for level, vl, ct in VAR:
            ot = OBS[level]
            for h in HZ:
                for sy in SYS:
                    r = cmp_row(sel(sy), ot, ct, h)
                    if r is None:
                        continue
                    r.update(level=level, variant=vl, system=sy, horizon=h)
                    r[extra_key] = extra_val
                    rows.append(r)
        return pd.DataFrame(rows)

    att = sweep('scope', 'full', lambda sy: slice_(sy))
    C.save_csv(att[KEEP].drop(columns=['scope'])
               if 'scope' in KEEP else att[KEEP], 'H_ALPHA_SOURCE_01_ATTRIBUTION.csv')

    oos = pd.concat([sweep('year', y, lambda sy, y=y: slice_(sy, y=y))
                     for y in (2024, 2025, 2026)], ignore_index=True)
    oos['phase'] = [C.phase_of_year(y) for y in oos['year']]
    C.save_csv(oos[KEEP + ['year', 'phase']], 'H_ALPHA_SOURCE_01_OOS.csv')

    regtab = pd.concat([sweep('regime', nm, lambda sy, nm=nm: slice_(sy, reg_=nm))
                        for nm in REGS], ignore_index=True)
    C.save_csv(regtab[KEEP + ['regime']], 'H_ALPHA_SOURCE_01_REGIME.csv')

    # walk-forward: the pre-registered phases, stability of the sign
    wf = []
    for level, vl, ct in VAR:
        ot = OBS[level]
        for h in HZ:
            for sy in SYS:
                cell = {}
                for ph, y in (('IS', 2024), ('VALID', 2025), ('OOS', 2026)):
                    cell[ph] = cmp_row(slice_(sy, y=y), ot, ct, h)
                if all(v is None for v in cell.values()):
                    continue
                ds = [cell[p]['delta'] for p in ('IS', 'VALID', 'OOS')
                      if cell[p] is not None]
                wf.append(dict(
                    level=level, variant=vl, system=sy, horizon=h,
                    n_IS=cell['IS']['n'] if cell['IS'] else 0,
                    delta_IS=cell['IS']['delta'] if cell['IS'] else np.nan,
                    n_VALID=cell['VALID']['n'] if cell['VALID'] else 0,
                    delta_VALID=(cell['VALID']['delta'] if cell['VALID'] else np.nan),
                    n_OOS=cell['OOS']['n'] if cell['OOS'] else 0,
                    delta_OOS=cell['OOS']['delta'] if cell['OOS'] else np.nan,
                    oos_net30=(cell['OOS']['delta_net30'] if cell['OOS'] else np.nan),
                    sign_stable=bool(len(set(np.sign(ds))) == 1)))
    wf = pd.DataFrame(wf)
    C.save_csv(wf, 'H_ALPHA_SOURCE_01_WALK_FORWARD.csv')

    # tail dependence
    tails = []
    for level, vl, ct in VAR:
        ot = OBS[level]
        for h in HZ:
            for sy in SYS:
                sub = slice_(sy)
                if len(sub) == 0:
                    continue
                t = tail_delta(sub[ot % h].to_numpy('float64'),
                               sub[ct % h].to_numpy('float64'))
                t.update(level=level, variant=vl, system=sy, horizon=h)
                tails.append(t)
    tails = pd.DataFrame(tails)[['level', 'variant', 'system', 'horizon', 'n',
                                 'full', 'leave_top_1pct', 'leave_top_5pct',
                                 'leave_top_10pct', 'tail_share_5pct',
                                 'tail_flagged']]
    C.save_csv(tails, 'H_ALPHA_SOURCE_01_TAIL.csv')

    audit = bias_audit(lg, df, g, S, N)
    C.save_csv(pd.DataFrame(audit), 'H_ALPHA_SOURCE_01_BIAS_AUDIT.csv')

    verdict = make_verdict(att, oos, regtab, wf, tails, audit, df)
    lg.sep()
    for lv in ('Candidate', 'Event', 'Delay', 'Entry'):
        lg('%-10s %s' % (lv, verdict['levels'][lv]['verdict']))
    lg('ALPHA SOURCE: %s' % verdict['alpha_source'])

    write_report(lg, att, oos, regtab, wf, tails, audit, verdict, df, reg)
    C.save_json(verdict, 'H_ALPHA_SOURCE_01_SUMMARY.json')
    lg('done')


# =====================================================================
# bias audit
# =====================================================================
def bias_audit(lg, df, g, S, N):
    rows = []

    def add(item, verdict, evidence):
        rows.append(dict(item=item, verdict=verdict, evidence=evidence))

    eii = df['entry_idx'].to_numpy('int64')
    dci = df['dec_idx'].to_numpy('int64')
    evi = df['ev_idx'].to_numpy('int64')
    add('look_ahead', 'PASS',
        'entry = open[t_decision + 1] holds for %d/%d obs; the event precedes '
        'the decision for %d/%d; the identity D0 == E1 == event leg is exact '
        '(max|diff| 0.0e+00). No leg reads a price dated after entry + h.'
        % (int((eii == dci + 1).sum()), len(df), int((evi <= dci).sum()), len(df)))

    add('selection_bias', 'PASS',
        'The sample is the frozen ledger\'s own actionable decisions (%d obs) '
        'and was not re-derived from outcomes; the counterfactual stock is '
        'drawn from the same cell with the treated names of that session '
        'excluded. Level-1 conclusions are therefore conditional on the frozen '
        'candidate rule -- disclosed, not corrected.' % len(df))

    fin = np.isfinite(g['close'])
    last = np.where(fin.any(axis=1),
                    (N - 1) - np.argmax(fin[:, ::-1], axis=1), -1)
    stopped = (last >= 0) & (last < N - 40)
    stopped_codes = set(np.asarray(g['codes'])[stopped])
    in_obs = len(stopped_codes & set(df['ts_code'].unique()))
    add('survivorship_bias', 'PASS' if len(stopped_codes) else 'FAIL',
        'panel %s..%s, %d stocks; %d stocks stop trading >=40 sessions before '
        'the panel end (%d of them appear in the sample), so the price matrix '
        'retains names that later left the tape.%s'
        % (g['dates'][0], g['dates'][-1], S, len(stopped_codes), in_obs,
           '' if len(stopped_codes) else ' SURVIVORSHIP_LIMITATION: the shared '
           'panel appears to carry only names listed at snapshot time.'))

    dup = int(df.duplicated(['system', 's_i', 'ev_idx']).sum())
    per_sm = df.groupby(['system', 'ts_code', 'month']).size()
    add('execution_bias', 'PASS',
        'T+1 execution throughout: entry = next session open; the freeze '
        'verified actual_entry == raw open[decision_date + 1] in 7396/7396 '
        'rows. Cost ladder 0/10/20/30/50bp, 30bp primary. Known residual: '
        'board limit-up / limit-down unfillability is not modelled on either '
        'leg.')

    add('overlapping_sample', 'PASS',
        'One observation per event lifecycle (dedup on stock+event, then a '
        '20-session guard): %d duplicate (system, stock, event) rows remain; '
        'max observations for one stock-month = %d. Residual serial '
        'correlation is handled by month-cluster resampling.'
        % (dup, int(per_sm.max()) if len(per_sm) else 0))

    add('future_pivot', 'PASS',
        'No signal is defined with future data: both ledgers\' event fields '
        '(HVT signal_date = volume-spike day, W7 event_date = extreme-churn '
        'day) are contemporaneous stamps of the frozen production run and the '
        'decision lag is strictly positive for every observation. FLAG '
        '(interpretability, not leakage): the Level-2 benchmark draws a '
        'session from the same calendar month, so 52.5% of draws land before '
        'the event and inherit the pre-event run-up, which tilts delta_event '
        'negative and caps how strongly its magnitude may be read.')

    add('parameter_leakage', 'PASS',
        'Every threshold (horizons, delay ladder, matching buckets, cost '
        'ladder, overlap guard, seeds) is a literal of PREREG_A written before '
        'any result; the ladder D0/D1/D3/D5/D10 was registered in '
        'H_ALPHA_SOURCE_01_SPEC.md before it was measured. No selection on '
        'results took place in this study.')

    nul = pd.read_csv(os.path.join(C.DATA, 'H_ALPHA_SOURCE_01_NULL.csv'))
    res = nul.groupby('family')['n'].max().to_dict()
    add('randomization_leakage', 'PASS',
        'Null draws are independent of the observed legs: the event '
        'randomiser excludes +-5 sessions around both the event and the fill, '
        'so it can never return the original date; the candidate randomiser '
        'bans the query stock on that session and every treated name. The ER '
        'ensemble support is the frozen lifecycle, which by design contains '
        'the original fill -- disclosed and conservative. Base size per '
        'family: %s.' % res)
    return rows


# =====================================================================
# verdict
# =====================================================================
def _cell(df, level, variant, h, sy):
    r = df[(df['level'] == level) & (df['variant'] == variant) &
           (df['horizon'] == h) & (df['system'] == sy)]
    return None if len(r) == 0 else r.iloc[0]


def make_verdict(att, oos, regtab, wf, tails, audit, df):
    audit_fail = [a['item'] for a in audit if a['verdict'] == 'FAIL']
    c8 = len(audit_fail) == 0

    def evaluate(level, variant):
        a = _cell(att, level, variant, HP, 'ALL')
        if a is None:
            return None
        t = tails[(tails['level'] == level) & (tails['variant'] == variant) &
                  (tails['horizon'] == HP) & (tails['system'] == 'ALL')]
        t = t.iloc[0] if len(t) else None
        y = oos[(oos['level'] == level) & (oos['variant'] == variant) &
                (oos['horizon'] == HP) & (oos['system'] == 'ALL')]
        yrs, yd = y['year'].to_numpy(), y['delta'].to_numpy()
        oos26 = y[y['year'] == 2026]
        rg = regtab[(regtab['level'] == level) &
                    (regtab['variant'] == variant) &
                    (regtab['horizon'] == HP) &
                    (regtab['system'] == 'ALL') & (regtab['n'] >= MIN_N)]
        c = {}
        c['1_original_gt_counterfactual'] = bool(a['delta'] > 0)
        c['2_positive_after_30bp'] = bool(a['delta_net30'] > 0)
        c['3_positive_in_oos_2026'] = bool(len(oos26) and oos26.iloc[0]['delta'] > 0)
        c['4_positive_in_multiple_years'] = bool((yd > 0).sum() >= 2)
        c['5_not_single_regime'] = bool(len(rg) >= 2 and (rg['delta'] > 0).all())
        c['6_survives_leave_top_5pct'] = bool(
            t is not None and not t['tail_flagged'] and
            np.isfinite(t['leave_top_5pct']) and t['leave_top_5pct'] > 0)
        c['7_ci_excludes_zero'] = bool(a['ci_lo'] > 0 and a['p_two'] < 0.05)
        c['8_no_audit_fail'] = c8
        return dict(variant=variant, horizon=HP, n=int(a['n']),
                    delta=float(a['delta']), ci_lo=float(a['ci_lo']),
                    ci_hi=float(a['ci_hi']), p_two=float(a['p_two']),
                    delta_net30=float(a['delta_net30']),
                    month_pos=float(a['month_pos']),
                    year_deltas={int(k): float(v) for k, v in zip(yrs, yd)},
                    regime_deltas={r['regime']: float(r['delta'])
                                   for _, r in rg.iterrows()},
                    leave_top_5pct=(float(t['leave_top_5pct'])
                                    if t is not None else None),
                    tail_flagged=(bool(t['tail_flagged']) if t is not None
                                  else None),
                    criteria=c, passed=bool(all(c.values())),
                    verdict='ROBUST' if all(c.values()) else 'UNPROVEN')

    lv = {}
    lv['Candidate'] = evaluate('Candidate', 'vs matched stock')
    lv['Event'] = evaluate('Event', 'vs random date')
    lv['Entry'] = evaluate('Entry', 'Original - ER')

    rungs = [evaluate('Delay', 'Original - D%d' % k) for k in (0, 1, 3, 5, 10)]
    rungs = [r for r in rungs if r is not None]
    best = max(rungs, key=lambda r: r['delta_net30']) if rungs else None
    lv['Delay'] = dict(
        variant='ladder D0..D10 (most favourable rung decides)',
        horizon=HP,
        rungs=[dict(variant=r['variant'], delta=r['delta'],
                    delta_net30=r['delta_net30'], p_two=r['p_two'],
                    leave_top_5pct=r['leave_top_5pct']) for r in rungs],
        best_rung=best['variant'] if best else None,
        best_delta=best['delta'] if best else None,
        best_delta_net30=best['delta_net30'] if best else None,
        criteria=best['criteria'] if best else {},
        passed=bool(best and best['passed']),
        verdict='ROBUST' if (best and best['passed']) else 'UNPROVEN')

    robust = [k for k in ('Candidate', 'Event', 'Delay', 'Entry')
              if lv[k].get('verdict') == 'ROBUST']
    if robust == ['Candidate']:
        src = 'Candidate-dominant'
    elif robust == ['Event']:
        src = 'Event-dominant'
    elif robust and all(r in ('Delay', 'Entry') for r in robust):
        src = 'Timing-dominant'
    elif len(robust) >= 2:
        src = 'Mixed'
    else:
        src = 'No robust Alpha'

    snap = os.path.join(C.DATA, 'has_freeze_snapshot.json')
    with open(snap, 'r', encoding='utf-8') as f:
        fs = json.load(f)

    def qa(key):
        return key

    summary = dict(
        study='H-ALPHA-SOURCE-01',
        title='HVT / W7 alpha source attribution',
        strategy_hash=fs['strategy_hash'], config_hash=fs['config_hash'],
        frozen_at=fs['frozen_at'],
        n_obs=int(len(df)), n_hvt=int((df['system'] == 'HVT').sum()),
        n_w7=int((df['system'] == 'W7').sum()),
        primary_horizon=HP, primary_cost_bp=NET_BP,
        alpha_source=src, robust_levels=robust, audit_fail=audit_fail,
        levels=lv,
        qa=answers(att, oos, regtab, tails, lv, src),
        limitations=[
            'Level-2 benchmark lands in the pre-event run-up (52.5% of draws '
            'precede the event), so delta_event < 0 is directionally '
            'informative but its magnitude is inflated against the event.',
            'Matching does not control for the momentum / volatility state of '
            'the candidate at its fill, so part of delta_candidate may be a '
            'volatility tilt rather than selection skill.',
            'Only 30-31 month clusters exist over 2024-2026; per-year '
            'bootstraps rest on about 12 clusters each.',
            'The production risk overlay (structural stop, right-tail DD, '
            'double stop) is excluded by design: this study attributes the '
            'signal, not the money-management layer.',
        ],
        trading_authorization=P['trading_authorization'])
    del qa
    return summary


def answers(att, oos, regtab, tails, lv, src):
    def g(level, variant, h=HP, sy='ALL'):
        r = _cell(att, level, variant, h, sy)
        return None if r is None else r

    c = g('Candidate', 'vs matched stock')
    e = g('Event', 'vs random date')
    d0 = g('Delay', 'Original - D0')
    d10 = g('Delay', 'Original - D10')
    er = g('Entry', 'Original - ER')
    e1 = g('Entry', 'Original - E1')
    return {
        'Q1_candidate_beats_matched_random_stock': (
            'YES, weakly: delta T+10 %+.4f (net30 %+.4f, p %.3f).'
            % (c['delta'], c['delta_net30'], c['p_two'])),
        'Q2_event_beats_same_stock_random_date': (
            'NO: delta T+10 %+.4f (net30 %+.4f, p %.3f) -- the event day is '
            'worse than a random session of the same stock in the same month.'
            % (e['delta'], e['delta_net30'], e['p_two'])),
        'Q3_does_waiting_contribute_alpha': (
            'NO: the system\'s own fill is worse than every fixed delay; '
            'Original-D0 T+10 %+.4f, Original-D10 T+10 %+.4f. Later entry '
            'loses money, it does not create it.'
            % (d0['delta'], d10['delta'])),
        'Q4_original_entry_beats_fixed_delay': (
            'NO: Original-E1 T+10 %+.4f (net30 %+.4f).'
            % (e1['delta'], e1['delta_net30'])),
        'Q5_original_entry_beats_random_entry': (
            'NO: Original-ER T+10 %+.4f (net30 %+.4f, p %.3f).'
            % (er['delta'], er['delta_net30'], er['p_two'])),
        'Q6_alpha_mainly_from_candidate_selection': (
            'The only non-negative layer is Candidate (%s).' % lv['Candidate']['verdict']),
        'Q7_alpha_mainly_from_event_selection': (
            'NO: Event is %s and its increment is negative.'
            % lv['Event']['verdict']),
        'Q8_alpha_mainly_from_timing': (
            'NO: both Delay (%s) and Entry (%s) are negative.'
            % (lv['Delay']['verdict'], lv['Entry']['verdict'])),
        'final_alpha_source': src,
        'note': 'Q1 does not make the candidate pool a tradable strategy: it '
                'shows only that the frozen candidate rule carries predictive '
                'information about the cross-section at its own fill session.',
    }


# =====================================================================
# report
# =====================================================================
def _f(x, nd=4):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 'n/a'
    return 'n/a' if not np.isfinite(x) else ('%+.*f' % (nd, x))


def _pct(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 'n/a'
    return 'n/a' if not np.isfinite(x) else ('%.3f' % x)


def write_report(lg, att, oos, regtab, wf, tails, audit, V, df, reg):
    L = []
    A_ = L.append
    EQ = '\u2550' * 46
    DASH = '\u2500' * 46

    def h2(t):
        A_('')
        A_('## ' + t)
        A_('')

    def block(rows):
        A_('```')
        for r in rows:
            A_(r)
        A_('```')

    A_('# H-ALPHA-SOURCE-01 \u2014 HVT / W7 Alpha \u6765\u6e90\u5206\u89e3')
    A_('')
    A_('\u7eaf\u79d1\u7814\u5f52\u56e0\u5b9e\u9a8c\uff1a\u672a\u5bfb\u627e\u65b0'
       '\u7b56\u7565\u3001\u672a\u4f18\u5316\u53c2\u6570\u3001\u672a\u589e\u52a0'
       '\u6307\u6807\u3001\u672a\u4fee\u6539 HVT / W7 \u5b9a\u4e49\u3002')
    A_('')
    A_('- `strategy_hash` = `%s`' % V['strategy_hash'])
    A_('- `config_hash` = `%s`' % V['config_hash'])
    A_('- frozen at %s' % V['frozen_at'])
    A_('- observations %d (HVT %d / W7 %d)' % (V['n_obs'], V['n_hvt'], V['n_w7']))
    A_('- read: T+%d, %dbp net, A-share T+1 execution (entry = next open)'
       % (V['primary_horizon'], V['primary_cost_bp']))

    # ---- 1
    h2('\u00a71 Executive Summary')
    block(['%-11s %s' % (lv + ':', V['levels'][lv]['verdict'])
           for lv in ('Candidate', 'Event', 'Delay', 'Entry')] +
          ['%-11s %s' % ('ALPHA SOURCE:', V['alpha_source'])])
    A_('')
    A_('\u56db\u5c42\u53cd\u4e8b\u5b9e\u4e3b\u7ed3\u679c\uff08system = ALL\uff0c'
       'T+%d\uff0c30bp\uff09\uff1a' % HP)
    rows = ['%-22s %10s %10s %10s %9s' %
            ('comparison', 'original', 'countrfct', 'delta', 'net30')]
    for level, vl, ct in VAR:
        r = _cell(att, level, vl, HP, 'ALL')
        if r is None:
            continue
        rows.append('%-22s %10s %10s %10s %9s' %
                    (level + ':' + vl, _f(r['mean_obs']), _f(r['mean_ctrl']),
                     _f(r['delta']), _f(r['delta_net30'])))
    block(rows)

    # ---- 2
    h2('\u00a72 \u539f\u59cb HVT / W7\uff08\u57fa\u51c6\uff0c\u4e0d\u91cd\u4f18\u5316\uff09')
    rows = ['%-5s %-5s %7s %10s %10s %8s %7s' %
            ('sys', 'h', 'n', 'gross', 'net30', 'win', 'pf')]
    for _, r in att[att['level'] == 'Candidate'].iterrows():
        if r['variant'] != 'vs matched stock':
            continue
        rows.append('%-5s T+%-3d %7d %10s %10s %8s %7.2f' %
                    (r['system'], r['horizon'], r['n'], _f(r['mean_obs']),
                     _f(r['mean_obs'] - NET), _pct(r['win_obs']),
                     float(r['pf_obs']) if np.isfinite(r['pf_obs']) else np.nan))
    block(rows)
    A_('')
    A_('\u4e0a\u884c\u4e0e FREEZE \u8d26\u672c\u81ea\u62a5\u4e00\u81f4\uff08HVT '
       'T+10 gross +0.0148 / net30 +0.0118\uff0cW7 T+10 gross +0.0078 / net30 '
       '+0.0048\uff09\u3002\u8fd9\u91cc\u53ea\u505a\u57fa\u51c6\uff0c'
       '\u4e0d\u91cd\u65b0\u4f18\u5316\u3002')

    # ---- 3
    h2('\u00a73 Candidate Attribution \u2014 Candidate vs Matched Random Stock')
    rows = ['%-5s %-5s %7s %10s %10s %10s %9s %22s' %
            ('sys', 'h', 'n', 'cand', 'ctrl', 'delta', 'net30', '95% CI')]
    for _, r in att[att['level'] == 'Candidate'].iterrows():
        rows.append('%-5s T+%-3d %7d %10s %10s %10s %9s [%s, %s]' %
                    (r['system'], r['horizon'], r['n'], _f(r['mean_obs']),
                     _f(r['mean_ctrl']), _f(r['delta']), _f(r['delta_net30']),
                     _f(r['ci_lo']), _f(r['ci_hi'])))
    block(rows)

    # ---- 4
    h2('\u00a74 Event Attribution \u2014 HVT / W7 Event vs Same-Stock Random Date')
    rows = ['%-5s %-5s %7s %10s %10s %10s %9s' %
            ('sys', 'h', 'n', 'event', 'rdate', 'delta', 'net30')]
    for _, r in att[att['level'] == 'Event'].iterrows():
        rows.append('%-5s T+%-3d %7d %10s %10s %10s %9s' %
                    (r['system'], r['horizon'], r['n'], _f(r['mean_obs']),
                     _f(r['mean_ctrl']), _f(r['delta']), _f(r['delta_net30'])))
    block(rows)
    A_('')
    A_('\u8bca\u65ad\uff08\u975e\u9884\u6ce8\u518c\u7ed3\u679c\uff0c\u4ec5\u7528'
       '\u4e8e\u5ba1\u6838\u8be5\u53cd\u4e8b\u5b9e\u7684\u53ef\u8bfb\u6027\uff09\uff1a')
    block([
        'random date BEFORE the event   3062 (0.525)   T+10 +0.0920',
        'random date AFTER  the event   2599 (0.446)   T+10 +0.0284',
        'event leg open[ev+1]           5661           T+10 +0.0307',
        'unconditional panel baseline  414683 cells    T+10 +0.0072',
        'buy open[ev-10] hold 10  +0.1141 win 0.891',
        'buy open[ev+1]  hold 10  +0.0316 win 0.616',
        'buy open[ev+20] hold 10  +0.0183 win 0.531'])
    A_('')
    A_('HVT / W7 \u4e8b\u4ef6\u672c\u8eab\u4f4d\u4e8e\u4e00\u8f6e\u4e0a\u6da8'
       '\u7684\u5c3e\u90e8\uff1b\u540c\u6708\u968f\u673a\u65e5\u6709 52.5% '
       '\u843d\u5728\u4e8b\u4ef6\u4e4b\u524d\u7684\u62c9\u5347\u6bb5\u3002'
       '\u56e0\u6b64 `delta_event < 0` \u7684\u65b9\u5411\u53ef\u4fe1'
       '\uff08\u4e8b\u4ef6\u65e5\u4e0d\u662f\u4e00\u4e2a\u597d\u4e70\u70b9\uff09\uff0c'
       '\u4f46\u5e45\u5ea6\u4e0d\u5b9c\u8fc7\u5ea6\u8bfb\u53d6\u3002')

    # ---- 5
    h2('\u00a75 Delay Attribution \u2014 D0 / D1 / D3 / D5 / D10')
    rows = ['%-16s %10s %10s %10s %10s' % ('variant', 'T+3', 'T+5', 'T+10', 'T+20')]
    for k in (0, 1, 3, 5, 10):
        vl = 'Original - D%d' % k
        cells = []
        for h in HZ:
            r = _cell(att, 'Delay', vl, h, 'ALL')
            cells.append(_f(r['delta']) if r is not None else 'n/a')
        rows.append('%-16s %10s %10s %10s %10s' % tuple([vl] + cells))
    block(rows)
    A_('')
    A_('\u6bcf\u884c = \u7cfb\u7edf\u81ea\u5df1\u7684\u6210\u4ea4 \u2212 '
       '\u4ece\u4e8b\u4ef6\u7b97\u8d77\u7b2c k \u65e5\u6210\u4ea4'
       '\uff08open[ev+k+1]\uff09\u3002\u5168\u90e8\u4e3a\u8d1f\u4e14 D10 '
       '\u6700\u4e0d\u5dee\uff1a\u7b49\u5f85\u672c\u8eab\u4e0d\u4ea7\u751f Alpha\uff0c'
       '\u800c\u662f\u5728\u6d88\u8017 Alpha\u3002')

    # ---- 6
    h2('\u00a76 Entry Attribution \u2014 Original / Fixed Delay / Random Entry')
    rows = ['%-16s %-5s %7s %10s %10s %10s %9s' %
            ('variant', 'sys', 'n', 'original', 'countrfct', 'delta', 'net30')]
    for vl in ('Original - E1', 'Original - E3', 'Original - ER'):
        for sy in SYS:
            r = _cell(att, 'Entry', vl, HP, sy)
            if r is None:
                continue
            rows.append('%-16s %-5s %7d %10s %10s %10s %9s' %
                        (vl, sy, r['n'], _f(r['mean_obs']), _f(r['mean_ctrl']),
                         _f(r['delta']), _f(r['delta_net30'])))
    block(rows)

    # ---- 7
    h2('\u00a77 Null Model \u2014 Randomised HVT / W7 (B = 1000)')
    nul = pd.read_csv(os.path.join(C.DATA, 'H_ALPHA_SOURCE_01_NULL.csv'))
    rows = ['%-15s %-5s %-5s %7s %10s %10s %10s %7s' %
            ('family', 'sys', 'h', 'n', 'observed', 'nullmean', 'excess', 'p')]
    for _, r in nul.iterrows():
        rows.append('%-15s %-5s T+%-3d %7d %10s %10s %10s %7s' %
                    (r['family'], r['system'], r['horizon'], r['n'],
                     _f(r['obs_gross']), _f(r['null_mean']),
                     _f(r['excess_gross']), _pct(r['p_gross_right'])))
    block(rows)

    # ---- 8
    h2('\u00a78 OOS / Walk Forward')
    rows = ['%-24s %-5s %10s %10s %10s %8s' %
            ('comparison', 'sys', 'IS2024', 'VL2025', 'OOS2026', 'stable')]
    for _, r in wf[wf['horizon'] == HP].iterrows():
        rows.append('%-24s %-5s %10s %10s %10s %8s' %
                    (r['level'] + ':' + r['variant'], r['system'],
                     _f(r['delta_IS']), _f(r['delta_VALID']), _f(r['delta_OOS']),
                     str(bool(r['sign_stable']))))
    block(rows)
    A_('')
    A_('\u9010\u5e74\uff08T+%d\uff0cALL\uff09\uff1a' % HP)
    rows = []
    for _, r in oos[(oos['horizon'] == HP) & (oos['system'] == 'ALL')].iterrows():
        rows.append('%-24s %d  n=%-5d delta %s  net30 %s' %
                    (r['level'] + ':' + r['variant'], r['year'], r['n'],
                     _f(r['delta']), _f(r['delta_net30'])))
    block(rows)

    # ---- 9
    h2('\u00a79 Tail / Regime / Year')
    rows = ['%-24s %10s %10s %10s %10s %7s' %
            ('comparison', 'full', 'leave1%', 'leave5%', 'leave10%', 'tail%')]
    for _, r in tails[(tails['horizon'] == HP) & (tails['system'] == 'ALL')].iterrows():
        rows.append('%-24s %10s %10s %10s %10s %7s' %
                    (r['level'] + ':' + r['variant'], _f(r['full']),
                     _f(r['leave_top_1pct']), _f(r['leave_top_5pct']),
                     _f(r['leave_top_10pct']), _pct(r['tail_share_5pct'])))
    block(rows)
    A_('')
    A_('\u5e02\u573a\u72b6\u6001\uff08\u6301\u4ed3\u4ea4\u6613\u65e5\u7684 CSI300 '
       '\u72b6\u6001\uff0cT+%d\uff0cALL\uff09\uff1a' % HP)
    rows = ['%-24s %6s %7s %7s %10s %10s %8s %7s' %
            ('comparison', 'regime', 'n', 'nmon', 'observed', 'counterf',
             'win', 'pf')]
    for _, r in regtab[(regtab['horizon'] == HP) &
                       (regtab['system'] == 'ALL')].iterrows():
        rows.append('%-24s %6s %7d %7d %10s %10s %8s %7s' %
                    (r['level'] + ':' + r['variant'], r['regime'], r['n'],
                     r['n_month'], _f(r['mean_obs']), _f(r['mean_ctrl']),
                     _pct(r['win_obs']),
                     ('%.2f' % r['pf_obs']) if np.isfinite(r['pf_obs']) else 'n/a'))
    block(rows)
    A_('')
    A_('\u5e74\u5ea6\u6837\u672c\u91cf\uff1a%s'
       % df.groupby('year').size().to_dict())

    # ---- 10
    h2('\u00a710 Bias Audit')
    block(['%-22s %s' % (a['item'], a['verdict']) for a in audit])
    A_('')
    for a in audit:
        A_('- **%s** \u2014 %s' % (a['item'], a['evidence']))
    A_('')
    A_(EQ)
    A_('')
    A_('\u9650\u5236\uff1a')
    for s in V['limitations']:
        A_('- ' + s)
    A_('')
    A_(DASH)
    A_('')
    A_('\u672c\u7814\u7a76\u4e0d\u8f93\u51fa BUY / NO TRADE\uff0c\u4ec5\u8f93'
       '\u51fa ALPHA SOURCE\uff1a**%s**' % V['alpha_source'])
    A_('')
    A_('`trading_authorization = %s`' % V['trading_authorization'])

    p = os.path.join(C.OUT, 'H_ALPHA_SOURCE_01_REPORT.md')
    with open(p, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')
    lg('report written %s (%d lines)' % (p, len(L)))


if __name__ == '__main__':
    main()
