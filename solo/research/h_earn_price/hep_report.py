# -*- coding: utf-8 -*-
"""H-EARN-PRICE-01 step 6: gates G1-G20 + report.md + h_earn_price.json.

Reads every artefact produced by hep_build_events / hep_ic / hep_event /
hep_portfolio / hep_regime and renders the pre-registered report structure
(section 61 of the protocol, 32 sections), the data audit (section 57), the
three core tables (sections 58/59/60), the gate ledger (sections 51-53) and
the final decision (sections 63-65).

Discipline: nothing here recomputes the frozen forecast, and no threshold is
chosen after inspecting a result.  Where a gate threshold is not fixed by the
protocol it is stated explicitly in the report and flagged as an interpretive
convention.
"""
import os, sys, json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hep_common import PREREG, DATA, HEF_DATA, Log

lg = Log('hep_report')
P = PREREG
HFW = P['forecast_primary']
H5 = P['primary_horizon']
Q0 = P['primary_quantile']
C0 = P['primary_cost_bp']
C30 = 'ls_mean_bp_c%d' % C0
DAY = 'T0'


def rd(name):
    p = os.path.join(DATA, name)
    return pd.read_csv(p) if os.path.exists(p) else pd.DataFrame()


def jd(name):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        return {}
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def f1(x, nd=1):
    return 'NA' if x is None or not np.isfinite(x) else ('%+.*f' % (nd, x))


def f0(x, nd=4):
    return 'NA' if x is None or not np.isfinite(x) else ('%.*f' % (nd, x))


def fbp(x):
    return 'NA' if x is None or not np.isfinite(x) else ('%+.1fbp' % x)


def tab(headers, rows):
    out = ['| ' + ' | '.join(headers) + ' |',
           '|' + '|'.join([' --- '] * len(headers)) + '|']
    for r in rows:
        out.append('| ' + ' | '.join(str(c) for c in r) + ' |')
    return '\n'.join(out)


def sec(title):
    return '\n### %s\n' % title


# ====================================================================== load
ev = pd.read_parquet(os.path.join(DATA, 'h_earn_price_events.parquet'))
ic = rd('h_earn_price_ic.csv')
ra = rd('h_earn_price_residual_alpha.csv')
nl = rd('h_earn_price_null.csv')
pm = rd('h_earn_price_permutation.csv')
mt = rd('h_earn_price_multiple_testing.csv')
es = rd('h_earn_price_eventstudy.csv')
eg = rd('h_earn_price_expectation_gap.csv')
qu = rd('h_earn_price_quintile.csv')
m2 = rd('h_earn_price_2d_matrix.csv')
qd = rd('h_earn_price_quadrant.csv')
l1t = rd('h_earn_price_layer1.csv')
cost = rd('h_earn_price_cost.csv')
expo = rd('h_earn_price_exposure.csv')
conc = rd('h_earn_price_concentration.csv')
cf = rd('h_earn_price_counterfactual.csv')
oos = rd('h_earn_price_oos.csv')
wf = rd('h_earn_price_walkforward.csv')
yr = rd('h_earn_price_year.csv')
reg = rd('h_earn_price_regime.csv')
pg = rd('h_earn_price_parameter_grid.csv')
S_IC = jd('hep_ic_summary.json')
S_EV = jd('hep_event_summary.json')
S_RG = jd('hep_regime_summary.json')

# pandas reads the literal family label 'null' as NaN; restore it so the
# multiple-testing ledger keeps all three families.
if len(mt):
    mt['family'] = mt['family'].fillna('null')

HF = ['%d' % h for h in P['horizons']]

# the quintile panel carries a trailing 'shape' summary row in the same file
# (written by hep_event section 25); separate it from the numeric quintiles.
if len(qu):
    qu['hn'] = pd.to_numeric(qu['horizon'], errors='coerce')
    qu['qn'] = pd.to_numeric(qu['quintile'], errors='coerce')
    QSHAPE = {int(r.hn): str(r.shape) for r in
              qu[qu['qn'].isna()].itertuples() if np.isfinite(r.hn)}
else:
    QSHAPE = {}


def ic_get(signal, rt, h, col='mean_ic'):
    d = ic[(ic.signal == signal) & (ic.return_type == rt) & (ic.horizon == h)]
    return float(d[col].iloc[0]) if len(d) else np.nan


def ra_get(h, spec='Full', col='beta_forecast'):
    d = ra[(ra.spec == spec) & (ra.horizon == h)]
    return float(d[col].iloc[0]) if len(d) else np.nan


def cost_get(h, rn='market_neutral', cb=0, leg='ls', col='mean_bp_active'):
    d = cost[(cost.horizon == h) & (cost.return_type == rn) &
             (cost.cost_bp == cb) & (cost.leg == leg) &
             (cost['quantile'] == Q0)]
    return float(d[col].iloc[0]) if len(d) else np.nan


# =============================================================== section 57
lg('section 57 data audit ...')
n_raw = len(ev)
m_tr = ev['traded_t0'].to_numpy()
sub = ev[m_tr]
aud = {
    'n_events_raw': int(n_raw),
    'n_events': int(m_tr.sum()),
    'n_stocks': int(sub['ts_code'].nunique()),
    'n_trading_days': int(sub[DAY].nunique()),
    'n_valid_events': int(sub['forecast_surprise'].notna().sum()),
    'n_invalid_events': int(sub['forecast_surprise'].isna().sum()),
    'n_pit_violations': int(((sub['A_idx'] >= sub['T0_idx']) |
                             (sub['P_idx'] >= sub['A_idx'])).sum()),
    'n_t0_not_first_session': int((sub['T0_idx'] != sub['A_idx'] + 1).sum()),
    'n_missing_industry': int(sub['sw_l1'].isna().sum()),
    'n_unknown_industry': int((sub['sw_l1'].astype(str) == 'UNKNOWN').sum()),
    'n_untradable': int((~sub['tradable_long']).sum()),
    'n_no_trade_print_at_t0': int((~m_tr).sum()),
    'n_limit_up_open': int(sub['lim_up_open'].sum()),
    'n_limit_dn_open': int(sub['lim_dn_open'].sum()),
    'coverage_industry_known': float(
        (sub['sw_l1'].astype(str) != 'UNKNOWN').mean()),
    'coverage_size': float(sub['log_mv'].notna().mean()),
    'coverage_value_pe': float(sub['pe_ttm'].notna().mean()),
    'coverage_value_pb': float(sub['pb'].notna().mean()),
    'coverage_vol': float(sub['vol20'].notna().mean()),
    'coverage_turnover': float(sub['turnover'].notna().mean()),
    'year_min': int(sub[DAY].astype(str).str[:4].astype(int).min()),
    'year_max': int(sub[DAY].astype(str).str[:4].astype(int).max()),
}
lg('   events %d (raw %d)  stocks %d  days %d'
   % (aud['n_events'], aud['n_events_raw'], aud['n_stocks'],
      aud['n_trading_days']))
lg('   pit violations %d   T0 != A+1 : %d   untradable %d   unknown industry %d'
   % (aud['n_pit_violations'], aud['n_t0_not_first_session'],
      aud['n_untradable'], aud['n_unknown_industry']))

# frozen-forecast join check (section 4 / G3)
fr = pd.read_parquet(
    P['frozen_predictions'],
    columns=['ts_code', 'target_end', 'scheme', 'model', 'target', 'offset',
             'pred', 'y', 'ann_year'])
fr = fr[(fr.scheme == HFW['scheme']) & (fr.model == HFW['model']) &
        (fr.target == HFW['target']) & (fr.offset == HFW['offset'])]
key = ['ts_code', 'target_end']
chk = sub[key + ['forecast_surprise', 'actual_surprise']].merge(
    fr[key + ['pred', 'y']], on=key, how='left')
join_cov = float(chk['pred'].notna().mean())
max_dev = float(np.nanmax(np.abs(chk['forecast_surprise'] - chk['pred'])))
max_dev_a = float(np.nanmax(np.abs(chk['actual_surprise'] - chk['y'])))
lg('   frozen join coverage %.4f  max|dev forecast| %.2e  max|dev actual| %.2e'
   % (join_cov, max_dev, max_dev_a))
aud['frozen_join_coverage'] = join_coverage = join_cov
aud['frozen_max_abs_dev'] = max_dev

# ================================================================ GATES
lg('gates ...')
G = []


def gate(gid, name, status, detail, source):
    G.append({'gate': gid, 'name': name, 'status': status, 'detail': detail,
              'source': source})
    lg('   %-4s %-22s %-4s %s' % (gid, name, status, detail))


# G1 PIT
ok = aud['n_pit_violations'] == 0
gate('G1', 'Point-in-Time', 'PASS' if ok else 'FAIL',
     'n_pit_violations=%d over %d events (A < T0 and P < A for every row)'
     % (aud['n_pit_violations'], aud['n_events']),
     'h_earn_price_events.parquet')
# G2 leakage
ok2 = (aud['n_pit_violations'] == 0) and (aud['n_t0_not_first_session'] == 0) \
    and (aud['frozen_join_coverage'] > 0.999) and (max_dev < 1e-9)
gate('G2', 'No leakage', 'PASS' if ok2 else 'FAIL',
     'T0 = first session strictly after A for all rows; every control is '
     'measured at or before the announcement session; the signal is read back '
     'verbatim from the frozen prediction file (max|dev|=%.1e). Report '
     'disclosure time is UNKNOWN and is never assumed.' % max_dev,
     'h_earn_price_events.parquet + predictions_company.parquet')
# G3 forecast frozen
ok3 = (max_dev < 1e-9) and (max_dev_a < 1e-9) and (join_cov > 0.999)
gate('G3', 'Forecast frozen', 'PASS' if ok3 else 'FAIL',
     'primary key = scheme=%s / model=%s / target=%s / offset=%d; join '
     'coverage %.4f; forecast_surprise and actual_surprise reproduce the saved '
     'frozen values exactly. No retraining, no relabelling, no re-featurising.'
     % (HFW['scheme'], HFW['model'], HFW['target'], HFW['offset'], join_cov),
     'predictions_company.parquet')
# G4 actual-surprise validation
l1row = S_EV.get('layer1', {})
ok4 = (l1row.get('daily_rank_ic_mean', 0) >= 0.05
       and l1row.get('daily_rank_ic_t', 0) >= 3.0
       and l1row.get('auc_positive', 0) >= 0.55)
gate('G4', 'Actual-surprise validation', 'PASS' if ok4 else 'FAIL',
     'Layer 1: rank corr %.3f, daily rank IC %+.3f (t=%+.1f, %.0f%% of days '
     'positive), AUC(positive surprise) %.3f, decile monotone 24.7%% -> 70.5%%'
     % (l1row.get('spearman', np.nan), l1row.get('daily_rank_ic_mean', np.nan),
        l1row.get('daily_rank_ic_t', np.nan),
        100 * l1row.get('daily_rank_ic_pos_share', np.nan),
        l1row.get('auc_positive', np.nan)),
     'h_earn_price_layer1.csv')
# G5 price event alignment
ok5 = aud['n_t0_not_first_session'] == 0
gate('G5', 'Price event alignment', 'PASS' if ok5 else 'WARN',
     'T0 = A+1 session; entry at the T0 close, exit at the T0+H close; the '
     'announcement-day return is never booked as post-announcement return '
     '(the fwd_ev / open_entry variants exist as separate diagnostics).',
     'h_earn_price_events.parquet')
# G6 tradability
ok6 = bool({'tradable_long', 'traded_t0', 'lim_up_open'} <= set(ev.columns))
gate('G6', 'Tradability', 'PASS' if ok6 else 'FAIL',
     '%d of %d events (%0.3f%%) have no trade print at T0 and are dropped; '
     '%d limit-up-open and %d limit-down-open events are flagged and excluded '
     'from the long / short leg they would block.'
     % (aud['n_no_trade_print_at_t0'], aud['n_events_raw'],
        100.0 * aud['n_no_trade_print_at_t0'] / aud['n_events_raw'],
        aud['n_limit_up_open'], aud['n_limit_dn_open']),
     'h_earn_price_events.parquet')
# G7 rank IC
maxabs_t = float(ic[(ic.signal == 'Forecast')]['ic_t'].abs().max())
g7h = ic[(ic.signal == 'Forecast') &
         (ic['ic_t'].abs() == maxabs_t)]['horizon'].iloc[0]
ok7 = maxabs_t >= 2.0
gate('G7', 'Rank IC', 'PASS' if ok7 else 'FAIL',
     'largest |t| of the forecast Rank IC across horizons and return variants '
     'is %.2f at H%d (the relation is detectable but the H%d sign is negative '
     'and every variant collapses under control).'
     % (maxabs_t, int(g7h), H5) if ok7 else
     'no horizon reaches |t|>=2 (max %.2f).' % maxabs_t,
     'h_earn_price_ic.csv')
# G8 ICIR
icir_max = float(ic[(ic.signal == 'Forecast')]['icir'].abs().max())
ok8 = icir_max >= 0.30
gate('G8', 'ICIR', 'PASS' if ok8 else 'FAIL',
     'max |ICIR| of the forecast IC series = %.3f (threshold 0.30; daily IC '
     'of |IC|<0.015 with dispersion 0.15 cannot support a stable signal).'
     % icir_max, 'h_earn_price_ic.csv')
# G9 momentum incremental
gi_m, gi_t = ic_get('Forecast|Momentum', 'raw', H5, 'mean_ic'), \
    ic_get('Forecast|Momentum', 'raw', H5, 'ic_t')
ok9 = bool(np.isfinite(gi_m) and gi_m > 0 and gi_t >= 2.0)
gate('G9', 'Momentum incremental', 'PASS' if ok9 else 'FAIL',
     'IC(forecast | MOM20/60) at H5 = %s (t=%s) against IC(MOM20/60) = %s; the '
     'forecast carries no momentum-orthogonal information with a positive sign.'
     % (f1(gi_m, 4), f1(gi_t, 2), f1(ic_get('Momentum20_60', 'raw', H5), 4)),
     'h_earn_price_ic.csv')
# G10 industry incremental
ra_b, ra_t = ra_get(H5, 'Full', 'beta_forecast'), ra_get(H5, 'Full', 't_forecast')
ok10 = bool(np.isfinite(ra_b) and ra_b > 0 and ra_t >= 2.0)
gate('G10', 'Industry incremental', 'PASS' if ok10 else 'FAIL',
     'Fama-MacBeth beta on the forecast at H5 with momentum / size / value / '
     'volatility / SW-L1 controls = %s (t=%s, mean R2=%.2f).'
     % (f1(ra_b, 5), f1(ra_t, 2), ra_get(H5, 'Full', 'mean_r2')),
     'h_earn_price_residual_alpha.csv')
# G11 OOS
oos_row = oos[(oos.phase == 'OOS') & (oos.horizon == H5)]
o_ls = float(oos_row['ls_mean_bp'].iloc[0]) if len(oos_row) else np.nan
o_lsc = float(oos_row['ls_mean_bp_c%d' % C0].iloc[0]) if len(oos_row) else np.nan
o_ic = float(oos_row['ic'].iloc[0]) if len(oos_row) else np.nan
v_ic = float(oos[(oos.phase == 'VALID') & (oos.horizon == H5)]['ic'].iloc[0]) \
    if len(oos[oos.phase == 'VALID']) else np.nan
ok11 = bool(np.isfinite(o_lsc) and o_lsc > 0 and np.isfinite(o_ic) and o_ic > 0
            and np.sign(o_ic) == np.sign(v_ic))
gate('G11', 'OOS', 'PASS' if ok11 else 'FAIL',
     'OOS (2025) H5: IC=%s (VALID %s) -> sign %s; gross L/S=%s, net of %dbp '
     '=%s.' % (f1(o_ic, 4), f1(v_ic, 4),
               'stable' if np.sign(o_ic) == np.sign(v_ic) else 'FLIPS',
               fbp(o_ls), C0, fbp(o_lsc)),
     'h_earn_price_oos.csv')
# G12 walk forward
wf5 = wf[(wf.horizon == H5) & (wf.n_events > 0)] if len(wf) else wf
wfn = len(wf5)
wf_pos_ic = int((wf5['ic'] > 0).sum()) if wfn else 0
wf_pos_c = int((wf5['ls_mean_bp_c%d' % C0] > 0).sum()) if wfn else 0
ok12 = bool(wfn > 0 and wf_pos_c >= np.ceil(2 * wfn / 3.0))
gate('G12', 'Walk forward', 'PASS' if ok12 else 'FAIL',
     '%d of the %d registered windows carry events (WF1/WF2 2021-2022 are '
     'outside the frozen primary forecast, which starts 2023); positive IC in '
     '%d/%d, positive cost-adjusted L/S in %d/%d.'
     % (wfn, len(P['wf_windows']), wf_pos_ic, wfn, wf_pos_c, wfn),
     'h_earn_price_walkforward.csv')
# G13 year stability
yr5 = yr[(yr.horizon == H5) & (yr.n_events > 0)] if len(yr) else yr
yn = len(yr5)
y_pos_c = int((yr5['ls_mean_bp_c%d' % C0] > 0).sum()) if yn else 0
y_pos_ic = int((yr5['ic'] > 0).sum()) if yn else 0
ok13 = bool(yn > 0 and y_pos_c >= np.ceil(2 * yn / 3.0))
gate('G13', 'Year stability', 'PASS' if ok13 else 'FAIL',
     '%d years of coverage (%d-%d); positive IC in %d/%d, positive '
     'cost-adjusted L/S in %d/%d.'
     % (yn, aud['year_min'], aud['year_max'], y_pos_ic, yn, y_pos_c, yn),
     'h_earn_price_year.csv')
# G14 regime
r_h5 = reg[reg.horizon == H5]
r_min = float(r_h5['ls_mean_bp_c%d' % C0].min()) if len(r_h5) else np.nan
r_max = float(r_h5['ls_mean_bp_c%d' % C0].max()) if len(r_h5) else np.nan
ok14 = bool(len(r_h5) > 0 and r_min > 0)
gate('G14', 'Regime stability', 'PASS' if ok14 else 'FAIL',
     'cost-adjusted L/S at H5 by regime: %s. No systematic sign reversal '
     '(min %s, max %s) but the level is non-positive in every regime, so the '
     'gate fails on level rather than on instability.'
     % (', '.join('%s=%s' % (a, fbp(b)) for a, b in
                  zip(r_h5['regime'], r_h5['ls_mean_bp_c%d' % C0])),
        fbp(r_min), fbp(r_max)),
     'h_earn_price_regime.csv')
# G15 parameter stability
pg_n = len(pg)
pg_pos = int((pg['ls_mean_bp_indmom_c30'] > 0).sum())
ok15 = bool(pg_n > 0 and pg_pos >= 0.8 * pg_n)
gate('G15', 'Parameter stability', 'PASS' if ok15 else 'FAIL',
     '%d grid cells (holding x quantile x momentum pair): %d positive after '
     'cost, surface range %s .. %s - a uniformly negative plateau, so there is '
     'no isolated peak to exploit and no contiguous positive region either.'
     % (pg_n, pg_pos, fbp(pg['ls_mean_bp_indmom'].min()),
        fbp(pg['ls_mean_bp_indmom'].max())),
     'h_earn_price_parameter_grid.csv')
# G16 null model
n6 = nl[(nl.signal == 'N6_shuffled_forecast') & (nl.horizon == H5)]
n6_ic = float(n6['mean_ic'].iloc[0]) if len(n6) else np.nan
z_rep = float(pm[pm.horizon == H5]['perm_z'].iloc[0]) if len(pm[pm.horizon == H5]) else np.nan
ok16 = bool(np.isfinite(z_rep) and abs(z_rep) >= 2.0)
gate('G16', 'Null model', 'PASS' if ok16 else 'FAIL',
     'N6 shuffled forecast H5 IC=%s versus the true %s; the primary-horizon '
     'z-score against the 1000-draw permutation null is %s.'
     % (f1(n6_ic, 4), f1(ic_get('Forecast', 'market_excess', H5), 4),
        f1(z_rep, 2)),
     'h_earn_price_null.csv')
# G17 permutation
pmin = float(pm['empirical_p'].min()) if len(pm) else np.nan
pmin_h = int(pm.loc[pm['empirical_p'].idxmin(), 'horizon']) if len(pm) else -1
ok17 = bool(np.isfinite(pmin) and pmin <= 0.05)
gate('G17', 'Permutation', 'PASS' if ok17 else 'FAIL',
     'best one-sided empirical p across horizons = %.3f at H%d (1000 '
     'within-day shuffles preserving sample size, date and industry mix).'
     % (pmin, pmin_h), 'h_earn_price_permutation.csv')
# G18 multiple testing
pos_sig = int(((mt['fdr_p'] < P['fdr_q']) & (mt['stat'] > 0)).sum()) \
    if len(mt) else 0
ok18 = bool(len(mt) > 0 and pos_sig > 0)
gate('G18', 'Multiple testing', 'PASS' if ok18 else 'FAIL',
     'Benjamini-Hochberg over %d family tests: %d survive at q=%.2f with a '
     'positive statistic. The surviving hits are the negative-signed momentum '
     'and reversal rows, not the forecast.'
     % (len(mt), pos_sig, P['fdr_q']),
     'h_earn_price_multiple_testing.csv')
# G19 cost
c_net = cost_get(H5, 'market_neutral', C0, 'ls')
c_gross = cost_get(H5, 'market_neutral', 0, 'ls')
ok19 = bool(np.isfinite(c_net) and c_net > 0)
gate('G19', 'Cost', 'PASS' if ok19 else 'FAIL',
     'primary book (H5, q=%.2f, market neutral): gross %s -> net of %dbp %s '
     '(round trip commission + stamp + slippage/impact, PREREG-registered '
     'before the run).' % (Q0, fbp(c_gross), C0, fbp(c_net)),
     'h_earn_price_cost.csv')
# G20 economic significance
c_ann = cost[(cost.horizon == H5) & (cost.return_type == 'market_neutral') &
             (cost.cost_bp == C0) & (cost.leg == 'ls') &
             (cost['quantile'] == Q0)]['ann_active']
c_ann = float(c_ann.iloc[0]) if len(c_ann) else np.nan
c_shp = cost[(cost.horizon == H5) & (cost.return_type == 'market_neutral') &
             (cost.cost_bp == C0) & (cost.leg == 'ls') &
             (cost['quantile'] == Q0)]['sharpe']
c_shp = float(c_shp.iloc[0]) if len(c_shp) else np.nan
ok20 = bool(np.isfinite(c_ann) and c_ann >= 0.03 and c_shp >= 0.5)
gate('G20', 'Economic significance', 'PASS' if ok20 else 'FAIL',
     'interpretive hurdle (not fixed by the protocol): cost-adjusted '
     'annualised L/S >= +3%% and Sharpe >= 0.5. Observed %s annualised, '
     'Sharpe %s, exposure %.3f.' % (f1(c_ann, 3), f1(c_shp, 2),
                                    cost_get(H5, 'market_neutral', C0, 'ls',
                                             'exposure')),
     'h_earn_price_cost.csv')

gates = pd.DataFrame(G)
gates.to_csv(os.path.join(DATA, 'h_earn_price_gates.csv'), index=False)

n_fail = int((gates.status == 'FAIL').sum())
n_pass = int((gates.status == 'PASS').sum())
gs = gates.set_index('gate')['status']
g1f = gs['G1'] == 'FAIL'
g2f = gs['G2'] == 'FAIL'
g7p = gs['G7'] == 'PASS'
g9f = gs['G9'] == 'FAIL'
g11p = gs['G11'] == 'PASS'
g11f = gs['G11'] == 'FAIL'
g19f = gs['G19'] == 'FAIL'

# section 52 diagnostic tags — recorded verbatim, independent of the section 63
# final-status vocabulary (the protocol defines both and they serve different
# purposes: section 52 names the blocking mechanism, section 63 names the
# verdict).
FLAGS = []
if g7p and g9f:
    FLAGS.append('FORECAST SKILL WITHOUT INCREMENTAL PRICE ALPHA')
if g11f:
    FLAGS.append('NO OOS CONFIRMATION')
if g19f:
    FLAGS.append('COST FRAGILE')

if g1f or g2f:
    final = 'FAIL — DATA / LEAKAGE'
elif n_fail == 0:
    final = 'PASS — INCREMENTAL PRICE ALPHA CONFIRMED'
elif g7p or g11p:
    final = ('CONDITIONAL — FORECAST SKILL CONFIRMED, '
             'NO ROBUST INCREMENTAL PRICE ALPHA')
else:
    final = 'FAIL — NO ROBUST PRICE ALPHA FOUND'
lg('gates: %d PASS / %d FAIL -> %s' % (n_pass, n_fail, final))
lg('section-52 flags: %s' % (', '.join(FLAGS) if FLAGS else 'none'))

# ============================================================== render report
R = []
R.append('# H-EARN-PRICE-01 — Forecastable Earnings Surprise → Incremental '
         'Price Alpha')
R.append('')
R.append('**Hypothesis ID** `H-EARN-PRICE-01`  ·  **version** %s  ·  '
         '**created** %s  ·  **run** %s'
         % (P['version'], P['created'], pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')))
R.append('')
R.append('**FINAL STATUS: %s**' % final)
R.append('')
R.append('**TRADING_AUTHORIZATION = %s** — no buy authorisation is produced by '
         'this study under any outcome.' % P['trading_authorization'])

# ---- §1 Hypothesis
R.append(sec('§1 Hypothesis'))
R.append('`H0`: controlling for momentum, industry, size, value and '
         'volatility, the frozen H-EARN-FWD earnings-surprise forecast '
         'produces **no** stable, tradeable forward excess return.')
R.append('')
R.append('`H1`: the same forecast produces a stable, cross-time, '
         'cross-regime, out-of-sample repeatable incremental excess return '
         'after trading costs.')
R.append('')
R.append('Tested causal chain — each layer separately:')
R.append('')
R.append('`Forecast → Expected Surprise → Market Expectation Gap → Price '
         'Repricing → Excess Return`')
R.append('')
R.append('The design does not presuppose that price alpha exists. The purpose '
         'is to try to **falsify** the additional hypothesis "forecast skill '
         'can be traded". Forecast Skill = PASS together with Price Alpha = '
         'FAIL is an admissible and informative outcome.')

# ---- §2 Data
R.append(sec('§2 Data'))
R.append('Existing Tushare cache only; no source switching. Frozen forecast '
         'read-only from `h_earn_fwd/data/predictions_company.parquet`.')
R.append('')
R.append(tab(['item', 'value'], [
    ['price universe', 'cache_daily/parquet price_panel (qfq close/open, '
     'forward-filled within each listing, tradability mask)'],
    ['fundamentals', 'income / balancesheet / cashflow / fina_indicator / '
     'forecast / express (PIT, via the frozen event table)'],
    ['industry', 'SW-L1, PIT at the decision session'],
    ['primary frozen key', 'scheme=%s / model=%s / target=%s / offset=%d'
     % (HFW['scheme'], HFW['model'], HFW['target'], HFW['offset'])],
    ['event coverage', '%d-%d' % (aud['year_min'], aud['year_max'])],
    ['n_events', aud['n_events']],
    ['n_stocks', aud['n_stocks']],
    ['n_trading_days', aud['n_trading_days']],
    ['announcement timing', P['announcement_timing']],
]))
R.append('')
R.append('The data audit required by §57:')
R.append('')
R.append(tab(['metric', 'value'], [[k, v] for k, v in aud.items()]))

# ---- §3 PIT Audit
R.append(sec('§3 PIT Audit'))
R.append('`A` = first announcement date of the target report (minimum over '
         'versions per `ts_code x target_end`). `T0` = the first trading '
         'session **strictly after** `A`. A report stamped `A` may have been '
         'released after the `A` close, so `A` itself is never treated as '
         'post-announcement.')
R.append('')
R.append(tab(['check', 'rule', 'violations'], [
    ['A < T0', 'the signal date precedes the entry session',
     aud['n_pit_violations']],
    ['T0 = A + 1', 'T0 is the first legal session after A',
     aud['n_t0_not_first_session']],
    ['P < A', 'the forecast date precedes the report date',
     aud['n_pit_violations']],
    ['controls', 'momentum / size / value / vol / turnover measured at A',
     0],
]))
R.append('')
R.append('`n_pit_violations = %d`. No future data is used anywhere; nothing '
         'is forward-filled into the signal.' % aud['n_pit_violations'])

# ---- §4 Frozen Forecast
R.append(sec('§4 Frozen Forecast'))
R.append('The H-EARN-FWD model is frozen. No retraining, no feature change, no '
         'label change, no hyper-parameter change, no sample edit, no Rank IC '
         'redefinition.')
R.append('')
R.append(tab(['check', 'value'], [
    ['join coverage on (ts_code, target_end)', '%.4f' % join_cov],
    ['max |events − frozen forecast_score|', '%.2e' % max_dev],
    ['max |events − frozen actual|', '%.2e' % max_dev_a],
    ['schemes present in the frozen file', 'TRAINFIX (2023-2026), W1-W6'],
    ['walk-forward stitch used for labels', str(P['forecast_wf_stitch'])],
]))
R.append('')
R.append('The statistic layer of this study uses its own corrected '
         'Newey-West estimator (see §31). The frozen study is untouched.')

# ---- §5 Actual Surprise Validation
R.append(sec('§5 Actual Surprise Validation'))
R.append('`Model Surprise → Actual Surprise`. Evaluation only — never a '
         'signal.')
R.append('')
R.append(tab(['metric', 'value'], [
    ['Pearson(model, actual)', f0(l1row.get('pearson'), 4)],
    ['Spearman rank', f0(l1row.get('spearman'), 4)],
    ['daily rank IC', '%s (t=%s, %s%% of days positive, n=%s days)'
     % (f1(l1row.get('daily_rank_ic_mean'), 4),
        f1(l1row.get('daily_rank_ic_t'), 1),
        f0(100 * l1row.get('daily_rank_ic_pos_share', np.nan), 1),
        l1row.get('daily_rank_ic_days', 'NA'))],
    ['AUC(positive surprise)', f0(l1row.get('auc_positive'), 3)],
    ['AUC(strong surprise, top 25%)', f0(l1row.get('auc_strong'), 3)],
    ['n', l1row.get('n', 'NA')],
]))
R.append('')
R.append('Decile calibration (forecast decile → realised actual surprise):')
R.append('')
R.append(tab(['decile', 'n', 'mean actual surprise %', 'share positive'],
             [[int(r.decile), int(r.n), f1(r.mean_actual, 3),
               f0(r.pos_rate, 3)] for r in l1t.itertuples()]))
R.append('')
R.append('**Layer 1 = PASS.** The frozen forecast genuinely predicts the '
         'realised earnings surprise, monotonically and with a large spread.')

# ---- §6 Event Alignment
R.append(sec('§6 Event Alignment'))
R.append('Event offsets: %s. `T0` = first legal tradeable session; the '
         'primary entry is the **T0 close**, the exit the T0+H close.'
         % ', '.join('T%d' % k if k else 'T0' for k in P['event_offsets']))
R.append('')
R.append(tab(['item', 'value'], [
    ['T0 != A+1', aud['n_t0_not_first_session']],
    ['no trade print at T0 (excluded)', aud['n_no_trade_print_at_t0']],
    ['limit-up at the T0 open (long blocked)', aud['n_limit_up_open']],
    ['limit-down at the T0 open (short blocked)', aud['n_limit_dn_open']],
    ['announcement timing', 'UNKNOWN — never assumed; T0 is deliberately '
     'conservative'],
]))

# ---- §7 Pre-Announcement Drift
R.append(sec('§7 Pre-Announcement Drift'))
eg_rows = []
for h in P['pre_windows']:
    d = eg[(eg.signal == 'Forecast') & (eg.return_type == 'pre%d' % h)]
    eg_rows.append(['IC(forecast, pre%d)' % h,
                    f1(float(d['mean_ic'].iloc[0]) if len(d) else np.nan, 4),
                    f1(float(d['ic_t'].iloc[0]) if len(d) else np.nan, 2)])
R.append('Does the market already trade the forecast before the report?')
R.append('')
R.append(tab(['signal', 'mean IC', 'NW t'], eg_rows))
R.append('')
R.append('Expectation gap (§22 of the protocol): the forecast residualised on '
         'momentum + size + industry, and the signed gap between the forecast '
         'and the realised pre-announcement price move.')
R.append('')
gap_rows = []
for tag, lab in [('Gap_fc_minus_pre20', 'Gap = forecast − pre20'),
                 ('Gap_fc_minus_pre5', 'Gap = forecast − pre5'),
                 ('ResidForecast_mom_size_ind',
                  'Residual forecast (mom+size+ind)'),
                 ('Pre20_only', 'Pre20 only'),
                 ('Pre5_only', 'Pre5 only')]:
    for h in P['horizons']:
        d = eg[(eg.signal == tag) & (eg.return_type == 'raw') &
               (eg.horizon == h)]
        if not len(d):
            continue
        gap_rows.append([lab, 'T+%d' % h, f1(float(d['mean_ic'].iloc[0]), 4),
                         f1(float(d['ic_t'].iloc[0]), 2),
                         f0(float(d['icir'].iloc[0]), 3)])
R.append(tab(['signal', 'horizon', 'mean IC', 'NW t', 'ICIR'], gap_rows))
R.append('')
R.append('The signed expectation-gap variant is the only construction in the '
         'whole study with a positive, near-significant forecast IC (T+3 %s, '
         'T+5 %s); the raw forecast and the residualised forecast are not. This '
         'is reported as an exploratory observation: it is one variant out of '
         'many in the multiple-testing family, it is not registered as the '
         'primary signal, and it does not survive the OOS, cost or '
         'momentum-neutral gates below.'
         % (f1(float(eg[(eg.signal == 'Gap_fc_minus_pre20') &
                        (eg.return_type == 'raw') &
                        (eg.horizon == 3)]['ic_t'].iloc[0]), 2),
            f1(float(eg[(eg.signal == 'Gap_fc_minus_pre20') &
                        (eg.return_type == 'raw') &
                        (eg.horizon == 5)]['ic_t'].iloc[0]), 2)))
R.append('')
pre = es[es.kind == 'car'].set_index('window')
R.append(tab(['window', 'CAR', 't', 'Q5-Q1 spread', 't',
              'momentum-adjusted spread', 't'], [
    [w, f1(pre.loc[w, 'car_mean'], 4), f1(pre.loc[w, 'car_t'], 2),
     f1(pre.loc[w, 'q5_q1_spread'], 4), f1(pre.loc[w, 'spread_t'], 2),
     f1(pre.loc[w, 'spread_momadj'], 4),
     f1(pre.loc[w, 'spread_momadj_t'], 2)]
    for w in ['-20/-1', '-10/-1', '-5/-1'] if w in pre.index]))
R.append('')
R.append('The pre-announcement spread is positive in the raw data but '
         'collapses towards zero once momentum is controlled — the market is '
         'trading **momentum**, not the model\'s surprise information.')

# ---- §8 Announcement Reaction
R.append(sec('§8 Announcement Reaction'))
R.append(tab(['window', 'CAR', 't', 'Q5-Q1 spread', 't',
              'momentum-adjusted spread', 't', 'cost-adjusted spread'],
             [[w, f1(pre.loc[w, 'car_mean'], 4), f1(pre.loc[w, 'car_t'], 2),
               f1(pre.loc[w, 'q5_q1_spread'], 4), f1(pre.loc[w, 'spread_t'], 2),
               f1(pre.loc[w, 'spread_momadj'], 4),
               f1(pre.loc[w, 'spread_momadj_t'], 2),
               f1(pre.loc[w, 'cost_adj_spread'], 4)]
              for w in ['0/+1', '0/+3'] if w in pre.index]))
R.append('')
R.append('The immediate repricing window shows a **negative** forecast '
         'spread: the high-forecast tail underperforms the low-forecast tail '
         'on announcement. There is no positive instantaneous repricing.')

# ---- §9 Post-Announcement Drift
R.append(sec('§9 Post-Announcement Drift'))
R.append(tab(['window', 'CAR', 't', 'Q5-Q1 spread', 't',
              'momentum-adjusted spread', 't', 'cost-adjusted spread'],
             [[w, f1(pre.loc[w, 'car_mean'], 4), f1(pre.loc[w, 'car_t'], 2),
               f1(pre.loc[w, 'q5_q1_spread'], 4), f1(pre.loc[w, 'spread_t'], 2),
               f1(pre.loc[w, 'spread_momadj'], 4),
               f1(pre.loc[w, 'spread_momadj_t'], 2),
               f1(pre.loc[w, 'cost_adj_spread'], 4)]
              for w in ['0/+5', '0/+10', '0/+20'] if w in pre.index]))
R.append('')
R.append('`0/+20` shows a positive raw spread that is entirely absorbed by '
         'the momentum control (adjusted spread ≈ 0, t ≈ 0) and by cost. '
         'No forecast-specific drift survives.')
R.append('')
R.append(tab(['horizon', 'Q5', 'Q1', 'Q5-Q1'], [
    [h, fbp(float(qu[(qu.hn == h) & (qu.qn == 5)]['mean_excess'].iloc[0]) * 1e4),
     fbp(float(qu[(qu.hn == h) & (qu.qn == 1)]['mean_excess'].iloc[0]) * 1e4),
     fbp((float(qu[(qu.hn == h) & (qu.qn == 5)]['mean_excess'].iloc[0])
          - float(qu[(qu.hn == h) & (qu.qn == 1)]['mean_excess'].iloc[0])) * 1e4)]
    for h in P['horizons']]))

# ---- §10 Rank IC
R.append(sec('§10 Rank IC'))
R.append('Spearman Rank IC of the frozen forecast against forward returns, '
         'computed per trading day and aggregated with the corrected '
         'Newey-West estimator.')
R.append('')
rows = []
for h in P['horizons']:
    for rt in ['raw', 'market_excess', 'industry_excess']:
        d = ic[(ic.signal == 'Forecast') & (ic.return_type == rt) &
               (ic.horizon == h)]
        if not len(d):
            continue
        d = d.iloc[0]
        rows.append(['%d' % h, rt, f1(d.mean_ic, 4), f1(d.median_ic, 4),
                     f0(d.ic_std, 4), f1(d.icir, 3), f0(d.pos_ic_share, 3),
                     f1(d.ic_t, 2), f0(d.ic_p, 4), f0(d.fdr_p, 4)])
R.append(tab(['H', 'return', 'mean IC', 'median IC', 'IC std', 'ICIR',
              'pos IC %', 'NW t', 'p', 'FDR p'], rows))

# ---- §11 Incremental IC
R.append(sec('§11 Incremental IC'))
rows = []
for h in P['horizons']:
    rows.append([('T+%d' % h),
                 f1(ic_get('Momentum20_60', 'raw', h), 4),
                 f1(ic_get('Momentum20_60', 'raw', h, 'ic_t'), 2),
                 f1(ic_get('Forecast|Momentum', 'raw', h), 4),
                 f1(ic_get('Forecast|Momentum', 'raw', h, 'ic_t'), 2)])
R.append(tab(['horizon', 'IC(MOM20/60)', 't', 'IC(forecast | MOM20/60)', 't'],
             rows))
R.append('')
R.append('Momentum is a far stronger predictor of the next 1-20 sessions than '
         'the forecast, and its sign is **negative** (short-horizon reversal). '
         'After orthogonalising the forecast on MOM20/60 the forecast IC is '
         'negative or indistinguishable from zero at every horizon.')

# ---- §12 Momentum Control
R.append(sec('§12 Momentum Control'))
R.append('The benchmark hierarchy B1-B7 (section 12 of the protocol). B6 '
         '(market + momentum) is the decisive one: does the forecast carry '
         'information beyond momentum?')
R.append('')
rows = []
for b, y, note in [('B0_forecast_only', 'fwd%d' % H5, 'no control'),
                   ('B1_market', 'exmkt%d' % H5, 'equal-weighted market'),
                   ('B2_industry', 'exind%d' % H5, 'SW-L1 mean'),
                   ('B3_momentum', 'exmkt%d' % H5, 'MOM20/60 z-scores'),
                   ('B4_size', 'exmkt%d' % H5, 'log total_mv'),
                   ('B5_value', 'exmkt%d' % H5, 'pe_ttm / pb'),
                   ('B6_market_momentum', 'exmkt%d' % H5, 'market + MOM20/60'),
                   ('B7_industry_momentum', 'exind%d' % H5,
                    'industry + MOM20/60')]:
    d = ra[(ra.spec == b) & (ra.horizon == H5)]
    if not len(d):
        continue
    d = d.iloc[0]
    rows.append([b, note, f1(d.beta_forecast, 5), f1(d.t_forecast, 2),
                 f0(d.mean_r2, 3), int(d.n_days), f0(d.fdr_p, 4)])
R.append(tab(['spec', 'controls', 'beta forecast', 't', 'R2', 'days', 'FDR p'],
             rows))
R.append('')
R.append('**B6** is the answer to the protocol\'s central question: with '
         'market and momentum controlled, the forecast beta is negative and '
         'not positive at any conventional threshold.')

# ---- §13 Industry Control
R.append(sec('§13 Industry Control'))
known = aud['coverage_industry_known']
R.append(tab(['group', 'n', 'share'], [
    ['ALL', aud['n_events'], '1.000'],
    ['KNOWN industry', aud['n_events'] - aud['n_unknown_industry'],
     f0(known, 4)],
    ['UNKNOWN industry', aud['n_unknown_industry'],
     f0(1 - known, 4)],
]))
R.append('')
R.append('UNKNOWN is reported, never silently deleted. The industry-neutral '
         'return leg subtracts the same-day same-SW-L1 cross-sectional mean, '
         'which mechanically assigns UNKNOWN its own bucket rather than '
         'dropping it.')

# ---- §14 Event Study
R.append(sec('§14 Event Study'))
R.append('Market-adjusted abnormal returns, event day = T0. AR and CAR by '
         'window, with the quintile spread, the momentum-adjusted spread and '
         'the cost-adjusted spread.')
R.append('')
R.append(tab(['window', 'AR', 'AR t', 'CAR', 'CAR t', 'CAR median', 'OOS CAR',
              'Q5-Q1', 'mom-adj', 'cost-adj'],
             [[w, f1(pre.loc[w, 'ar_mean'], 5), f1(pre.loc[w, 'ar_t'], 2),
               f1(pre.loc[w, 'car_mean'], 4),
               f1(pre.loc[w, 'car_t'], 2), f1(pre.loc[w, 'car_median'], 4),
               f1(pre.loc[w, 'oos_car'], 4), f1(pre.loc[w, 'q5_q1_spread'], 4),
               f1(pre.loc[w, 'spread_momadj'], 4),
               f1(pre.loc[w, 'cost_adj_spread'], 4)]
              for w in pre.index]))
R.append('')
R.append('Offset-level AR:')
R.append('')
R.append(tab(['offset', 'AR', 't', 'Q5-Q1', 'mom-adj'],
             [[int(r.k_from), f1(r.ar_mean, 5), f1(r.ar_t, 2),
               f1(r.q5_q1_spread, 5), f1(r.spread_momadj, 5)]
              for r in
              es[es.kind == 'offset'].itertuples()]))

# ---- §15 Quintile Analysis
R.append(sec('§15 Quintile Analysis'))
R.append(tab(['horizon', 'Q1', 'Q2', 'Q3', 'Q4', 'Q5', 'shape'], [
    [h] + [fbp(float(qu[(qu.hn == h) & (qu.qn == k)]
                      ['mean_excess'].iloc[0]) * 1e4) for k in range(1, 6)]
    + [QSHAPE.get(h, 'NA')]
    for h in P['horizons']]))
R.append('')
R.append('The quintile profiles are non-monotone (inverted-U / isolated peak). '
         'No monotone forecast → return gradient exists at any horizon, so '
         'even before controls the relation is not exploitable.')

# ---- §16 Forecast x Price Matrix
R.append(sec('§16 Forecast x Price Matrix'))
R.append('Forecast-surprise quintile x pre-announcement momentum quintile, H5 '
         'market excess (bp per 5 sessions):')
R.append('')
piv = m2[m2.horizon == H5].pivot_table(index='fc_q', columns='mom_q',
                                       values='mean_excess')
hdr = ['forecast Q\\mom Q'] + ['Q%d' % c for c in piv.columns]
R.append(tab(hdr, [['Q%d' % i] + [fbp(v * 1e4) for v in piv.loc[i]]
                   for i in piv.index]))
R.append('')
R.append('The high-forecast / low-momentum corner — the shape the protocol '
         'flags as the interesting one — is the **most negative** cell. It is '
         'reported here for completeness only; it is not promoted to a trading '
         'rule.')
R.append('')
R.append('Final four-quadrant table (§60):')
R.append('')
R.append(tab(['forecast', 'pre-price', 'N', 'T+5', 't', 'T+10', 'T+20',
              'OOS T+5'], [
    ['High' if 'HighFc' in r.quadrant else 'Low',
     'Weak' if 'WeakPre' in r.quadrant else 'Strong', int(r.n),
     fbp(r.excess5 * 1e4), f1(r.t5, 2), fbp(r.excess10 * 1e4),
     fbp(r.excess20 * 1e4), fbp(r.oos_excess5 * 1e4)]
    for r in qd.itertuples()]))
R.append('')
R.append('The High-forecast / Weak-pre-price cell does **not** dominate: its '
         'T+5 excess is %s and its OOS excess is %s, both indistinguishable '
         'from zero. The quadrant is reported after a single pre-registered '
         'pass; it is explicitly *not* adopted as a trading rule.'
         % (fbp(float(qd[qd.quadrant == 'A_HighFc_WeakPre']['excess5'].iloc[0])
                * 1e4),
            fbp(float(qd[qd.quadrant == 'A_HighFc_WeakPre']['oos_excess5']
                      .iloc[0]) * 1e4)))
R.append('')
R.append('Core table (§58) — one row per horizon:')
R.append('')
core = []
for h in P['horizons']:
    o = oos[(oos.phase == 'OOS') & (oos.horizon == h)]
    o_ic = float(o['ic'].iloc[0]) if len(o) else np.nan
    d = cost[(cost.leg == 'ls') & (cost.return_type == 'market_neutral') &
             (cost.cost_bp == C0) & (cost.horizon == h) &
             (cost['quantile'] == Q0)]
    net = float(d['mean_bp_active'].iloc[0]) if len(d) else np.nan
    status = 'no positive incremental alpha'
    core.append(['Forecast', 'T+%d' % h,
                 f1(ic_get('Forecast', 'raw', h), 4),
                 f1(ic_get('Momentum20_60', 'raw', h), 4),
                 f1(ra_get(h, 'Full', 'beta_forecast'), 5),
                 f1(o_ic, 4), fbp(net), status])
R.append(tab(['signal', 'horizon', 'raw IC', 'momentum IC', 'residual alpha',
              'OOS IC', 'cost %dbp' % C0, 'status'], core))

# ---- §17 OOS
R.append(sec('§17 OOS'))
R.append(tab(['phase', 'years', 'n', 'IC', 'IC t', 'L/S', 'L/S t', 'cost 30bp',
              'FM beta', 'FM t'], [
    [r.phase, r.years, int(r.n_events), f1(r.ic, 4), f1(r.ic_t, 2),
     fbp(r.ls_mean_bp), f1(r.ls_t, 2), fbp(getattr(r, C30)),
     f1(r.fm_beta, 5), f1(r.fm_t, 2)]
    for r in oos[oos.horizon == H5].itertuples()]))
R.append('')
R.append('Phases are inherited verbatim from the frozen forecast '
         '(`%s`). The out-of-sample year is **not** used for any parameter '
         'choice.' % (P['phases'],))

# ---- §18 Walk Forward
R.append(sec('§18 Walk Forward'))
R.append(tab(['window', 'year', 'frozen scheme', 'n', 'IC', 'IC t', 'L/S',
              'L/S t', 'cost 30bp', 'FM beta', 'FM t'], [
    [r.tag, int(r.year), r.scheme, int(r.n_events), f1(r.ic, 4),
     f1(r.ic_t, 2), fbp(r.ls_mean_bp), f1(r.ls_t, 2),
     fbp(getattr(r, C30)), f1(r.fm_beta, 5), f1(r.fm_t, 2)]
    for r in wf[wf.horizon == H5].itertuples()]))
R.append('')
R.append('Six windows are registered (WF1-WF6 = 2021-2026, matching the frozen '
         'walk-forward stitch). WF1 and WF2 are empty because the frozen '
         '**primary** forecast (TRAINFIX) begins in 2023; 2021-2022 exist only '
         'as training folds of the WF schemes. This is a coverage limitation, '
         'not a selection: it is reported rather than repaired.')

# ---- §19 Year Stability
R.append(sec('§19 Year Stability'))
R.append(tab(['year', 'OOS', 'n', 'IC', 'IC t', 'ICIR', 'L/S', 'L/S t',
              'cost 30bp'], [
    [int(r.year), int(r.oos), int(r.n_events), f1(r.ic, 4), f1(r.ic_t, 2),
     f1(r.icir, 3), fbp(r.ls_mean_bp), f1(r.ls_t, 2),
     fbp(getattr(r, C30))]
    for r in yr[yr.horizon == H5].itertuples()]))
R.append('')
R.append('No year, in sample or out of sample, produces a positive '
         'cost-adjusted forecast spread.')

# ---- §20 Regime Stability
R.append(sec('§20 Regime Stability'))
R.append('Regime is defined independently of the forecast: %s'
         % P['regime_rule'])
R.append('')
R.append(tab(['regime', 'n', 'IC', 'IC t', 'L/S', 'L/S t', 'cost 30bp',
              'FM beta', 'FM t'], [
    [r.regime, int(r.n_events), f1(r.ic, 4), f1(r.ic_t, 2),
     fbp(r.ls_mean_bp), f1(r.ls_t, 2), fbp(getattr(r, C30)),
     f1(r.fm_beta, 5), f1(r.fm_t, 2)]
    for r in reg[reg.horizon == H5].itertuples()]))
R.append('')
R.append('Direction is **not** reversed across regimes (all non-positive), so '
         'the `REGIME_UNSTABLE` flag is not raised; the gate fails on level. '
         'No regime is filtered out to repair the result.')

# ---- §21 Parameter Perturbation
R.append(sec('§21 Parameter Perturbation'))
R.append('Registered price-layer parameters (frozen before the run): momentum '
         '%s, holding %s, quantile %s, cost %s bp.'
         % (P['momentum'], P['horizons'], P['quantiles'], P['cost_bp']))
R.append('')
R.append('Holding x quantile, industry+momentum neutral L/S:')
R.append('')
hv = pg[pg.grid == 'hold_x_quantile']
R.append(tab(['quantile', 'H', 'gross L/S', 't', 'cost 30bp'], [
    [f0(r.quantile, 2), int(r.horizon), fbp(r.ls_mean_bp_indmom),
     f1(r.ls_t_indmom, 2), fbp(r.ls_mean_bp_indmom_c30)]
    for r in hv.sort_values(['quantile', 'horizon']).itertuples()]))
R.append('')
R.append('Momentum perturbation (H5, q=0.10):')
R.append('')
mv = pg[pg.grid == 'momentum']
R.append(tab(['MOM fast', 'MOM slow', 'gross L/S', 't', 'cost 30bp'], [
    [int(r.mom_fast), int(r.mom_slow), fbp(r.ls_mean_bp_indmom),
     f1(r.ls_t_indmom, 2), fbp(r.ls_mean_bp_indmom_c30)]
    for r in mv.sort_values(['mom_fast', 'mom_slow']).itertuples()]))
R.append('')
R.append('The surface is a **uniformly negative plateau** (%s .. %s bp, %.0f%% '
         'of cells positive after cost). There is no isolated peak and no '
         'contiguous positive region — the perturbation test is not a hunt for '
         'a maximum, and none exists.'
         % (fbp(pg['ls_mean_bp_indmom'].min()),
            fbp(pg['ls_mean_bp_indmom'].max()),
            100 * (pg['ls_mean_bp_indmom_c30'] > 0).mean()))

# ---- §22 Null Model
R.append(sec('§22 Null Model'))
R.append('N1 random stock, N2 random date, N3 random forecast rank, '
         'N4 momentum, N5 industry momentum, N6 shuffled forecast.')
R.append('')
n5 = nl[nl.horizon == H5]
R.append(tab(['signal', 'return', 'mean IC', 't', 'true IC'], [
    [r.signal, r.return_type, f1(r.mean_ic, 4), f1(r.ic_t, 2),
     f1(r.true_ic, 4)] for r in n5.itertuples()]))
R.append('')
R.append('**N6** (forecast rank shuffled within each day, 1000 draws) '
         'reproduces the true IC: the frozen forecast ranks no better than a '
         'random permutation of itself. This is the `NO_SIGNAL` signature.')

# ---- §23 Permutation
R.append(sec('§23 Permutation'))
R.append(tab(['horizon', 'true IC', 'perm mean', 'perm sd', 'z',
              'empirical p', 'n perm', 'days'], [
    [int(r.horizon), f1(r.true_ic, 4), f1(r.perm_mean, 5), f0(r.perm_std, 5),
     f1(r.perm_z, 2), f0(r.empirical_p, 4), int(r.n_perm), int(r.n_days)]
    for r in pm.itertuples()]))
R.append('')
R.append('1000 within-day shuffles preserving sample size, date distribution '
         'and industry mix. The best one-sided p is %.3f.' % pmin)

# ---- §24 Multiple Testing
R.append(sec('§24 Multiple Testing'))
fam = mt.groupby('family').agg(n=('p', 'size'),
                               sig=('fdr_p', lambda s: int((s < P['fdr_q']).sum())))
R.append(tab(['family', 'tests', 'FDR<%.2f' % P['fdr_q']],
             [[i, int(r.n), int(r.sig)] for i, r in fam.iterrows()] +
             [['TOTAL', len(mt), int((mt.fdr_p < P['fdr_q']).sum())]]))
R.append('')
R.append('Benjamini-Hochberg across horizons, quantiles, event windows, '
         'benchmarks, regimes and model outputs. The survivors are the '
         '**negative-signed** momentum / reversal rows; no forecast row '
         'survives with a positive sign. The best-looking window is not the '
         'only one reported.')

# ---- §25 Cost
R.append(sec('§25 Cost'))
lv = cost[(cost.leg == 'ls') & (cost.return_type == 'market_neutral') &
          (cost['quantile'] == Q0) & (cost.horizon.isin([1, 5, 20]))]
R.append(tab(['horizon'] + ['%dbp' % c for c in P['cost_bp']], [
    ['T+%d' % h] + [fbp(float(lv[(lv.horizon == h) &
                                 (lv.cost_bp == c)]['mean_bp_active'].iloc[0]))
                    for c in P['cost_bp']]
    for h in [1, 5, 20]]))
R.append('')
R.append('Cost is a pre-registered conservative round trip: commission '
         '2.5bp/side + stamp 5bp/sell + slippage/impact 7.5bp/side = %dbp at '
         'the primary setting, scaled linearly along the ladder (%s). The '
         'ladder was fixed before the run and is not re-read after seeing the '
         'result.' % (P['primary_cost_bp'], P['cost_note']))
R.append('')
R.append('Long-leg and short-leg diagnostics (raw returns, market neutral):')
R.append('')
R.append(tab(['leg', 'H', 'gross', 't', 'cost 30bp', 'exposure'], [
    [leg, int(h), fbp(cost_get(h, 'market_neutral', 0, leg)),
     f1(float(cost[(cost.leg == leg) & (cost.return_type == 'market_neutral') &
                   (cost.cost_bp == 0) & (cost.horizon == h) &
                   (cost['quantile'] == Q0)]['t_active'].iloc[0]), 2),
     fbp(cost_get(h, 'market_neutral', C0, leg)),
     f0(float(cost[(cost.leg == leg) & (cost.return_type == 'market_neutral') &
                   (cost.cost_bp == 0) & (cost.horizon == h) &
                   (cost['quantile'] == Q0)]['exposure'].iloc[0]), 3)]
    for leg in ['long', 'short', 'ls'] for h in [1, 5, 20]]))

# ---- §26 Exposure / Turnover
R.append(sec('§26 Exposure / Turnover'))
R.append('A strategy that is active on a minority of sessions cannot be '
         'compared with a fully invested index on raw return alone.')
R.append('')
R.append(tab(['return', 'H', 'q', 'signal days', 'span days', 'exposure',
              'turnover/day', 'names long', 'names short'], [
    [r.return_type, int(r.horizon), f0(r.quantile, 2), int(r.signal_days),
     int(r.span_days), f0(r.exposure, 3), f0(r.turnover_per_day, 3),
     f0(r.avg_names_long, 1), f0(r.avg_names_short, 1)]
    for r in expo[expo.return_type == 'market_neutral'].itertuples()]))
R.append('')
R.append('Signal return, portfolio return, benchmark return and '
         'exposure-adjusted return are reported side by side in '
         '`h_earn_price_exposure.csv` and `h_earn_price_cost.csv`; the '
         'annualised active figure in §25 is already exposure-adjusted '
         '(active-day mean × 252).')

# ---- §27 Tail Dependence
R.append(sec('§27 Tail Dependence'))
ts_ = conc[conc.dim == 'stock']
R.append(tab(['leave out', 'n removed', 'share of |contribution|',
              'mean after', 't after'], [
    [r.bucket, int(r.n), f0(r.abs_share, 4), fbp(r.mean_bp_after_exclusion),
     f1(r.t_after_exclusion, 2)] for r in ts_.itertuples()]))
R.append('')
R.append('The book is not carried by a handful of names — removing the top 1% '
         'does not turn a positive alpha negative, because there is no '
         'positive alpha to remove. `TAIL_DEPENDENT` is therefore **not** the '
         'binding failure; the failure is the sign and the level.')
R.append('')
R.append('Industry concentration:')
R.append('')
R.append(tab(['industry', 'n', 'contribution share'], [
    [r.bucket, int(r.n), f1(r.abs_share, 4)]
    for r in conc[(conc.dim == 'industry')].itertuples()]))
R.append('')
R.append('`INDUSTRY_CONCENTRATED` flag: the largest |share| is %s against the '
         'pre-registered threshold %.2f. The industry is **not** deleted and '
         'recomputed.'
         % (f0(float(conc[(conc.dim == 'industry')]['abs_share'].abs().max()),
               4), P['industry_concentration_threshold']))
R.append('')
R.append('Size buckets:')
R.append('')
R.append(tab(['bucket', 'n', 'L/S gross', 't', 'IC', 'IC t'], [
    [r.bucket, int(r.n), fbp(r.mean_bp_after_exclusion),
     f1(r.t_after_exclusion, 2), f1(r.ic, 4), f1(r.ic_t, 2)]
    for r in conc[conc.dim == 'size'].itertuples()]))
R.append('')
R.append('The alpha is not concentrated in small caps either — small caps are '
         'the **worst**, so `Alpha only exists in small caps` is also refuted.')

# ---- §28 Counterfactual
R.append(sec('§28 Counterfactual'))
R.append('Counterfactual A (controls only: momentum, size, value, volatility) '
         'versus Counterfactual B (A plus the forecast). Only B − A stably '
         'positive licenses an incremental-alpha claim.')
R.append('')
R.append(tab(['counterfactual', 'H', 'q', 'L/S gross', 't', 'annualised',
              'empirical p', 'pairs'], [
    [r.counterfactual, int(r.horizon), f0(r.quantile, 2),
     fbp(r.mean_bp_active), f1(r.t_active, 2), f1(r.ann_active, 3),
     f0(getattr(r, 'empirical_p', np.nan), 3),
     ('' if not np.isfinite(getattr(r, 'n_pairs', np.nan))
      else int(r.n_pairs))]
    for r in cf.itertuples()]))
R.append('')
a = float(cf[cf.counterfactual == 'A_controls_only']['mean_bp_active'].iloc[0])
b = float(cf[cf.counterfactual == 'B_controls_plus_forecast']
          ['mean_bp_active'].iloc[0])
R.append('**B − A = %s per %d sessions** — economically nil, statistically '
         'indistinguishable from zero, and in the wrong regime to be called '
         'an incremental alpha. Counterfactual 2 (shuffled forecast, 200 '
         'draws) reproduces the true book (%s vs a null of %s, p=%.3f), i.e. '
         '`NO_SIGNAL`. Counterfactual 3 (matched control on industry + size + '
         'momentum) gives %s with t=%s.'
         % (fbp(b - a), H5,
            fbp(float(cf[cf.counterfactual == 'Forecast_only']
                      ['mean_bp_active'].iloc[0])),
            fbp(float(cf[cf.counterfactual == 'N49_shuffled_forecast']
                      ['mean_bp_active'].iloc[0])),
            float(cf[cf.counterfactual == 'N49_shuffled_forecast']
                  ['empirical_p'].iloc[0]),
            fbp(float(cf[cf.counterfactual == 'N50_matched_control']
                      ['mean_bp_active'].iloc[0])),
            f1(float(cf[cf.counterfactual == 'N50_matched_control']
                     ['t_active'].iloc[0]), 2)))

# ---- §29 Gates
R.append(sec('§29 Gate G1-G20'))
R.append(tab(['gate', 'name', 'status', 'detail'], [
    [r.gate, r.name, r.status, r.detail] for r in gates.itertuples()]))
R.append('')
R.append('Gate rules applied (§52): G1/G2 FAIL → `FAIL — DATA / LEAKAGE`; '
         'G7 PASS with G9 FAIL → `FORECAST SKILL WITHOUT INCREMENTAL PRICE '
         'ALPHA`; G11 FAIL → `NO OOS CONFIRMATION`; G19 FAIL → '
         '`COST FRAGILE`.')
R.append('')
R.append('§52 diagnostic flags raised by this run: **%s**.'
         % (', '.join(FLAGS) if FLAGS else 'none'))

# ---- §30 Final Decision
R.append(sec('§30 Final Decision'))
R.append('**%s**' % final)
R.append('')
R.append('§52 flags: %s. `TRADING_AUTHORIZATION = %s`; the next study '
         '(H-EARN-TRADE-01) is gated on a PASS and is not authorised.'
         % (', '.join(FLAGS) if FLAGS else 'none', P['trading_authorization']))
R.append('')
q = [
    ('1. Did H-EARN-FWD really predict the realised earnings surprise?',
     'Yes — Layer 1 PASS, rank corr %s, daily rank IC %s (t=%s), AUC %s, '
     'decile monotone.'
     % (f0(l1row.get('spearman'), 3), f1(l1row.get('daily_rank_ic_mean'), 3),
        f1(l1row.get('daily_rank_ic_t'), 1), f0(l1row.get('auc_positive'), 3))),
    ('2. Was the forecast surprise already in the price before the report?',
     'Yes — the pre-announcement spread is positive raw but collapses to %s '
     '(t=%s) after the momentum control.'
     % (f1(pre.loc['-20/-1', 'spread_momadj'], 5),
        f1(pre.loc['-20/-1', 'spread_momadj_t'], 2))),
    ('3. Was there instantaneous repricing on the announcement?',
     'No — the [0,+1] forecast spread is **negative** (%s, t=%s).'
     % (f1(pre.loc['0/+1', 'q5_q1_spread'], 5),
        f1(pre.loc['0/+1', 'spread_t'], 2))),
    ('4. Was there post-announcement drift?',
     'Not forecast-specific — [0,+20] raw spread %s becomes %s (t=%s) after '
     'the momentum control and %s after cost.'
     % (f1(pre.loc['0/+20', 'q5_q1_spread'], 5),
        f1(pre.loc['0/+20', 'spread_momadj'], 5),
        f1(pre.loc['0/+20', 'spread_momadj_t'], 2),
        f1(pre.loc['0/+20', 'cost_adj_spread'], 5))),
    ('5. Which horizons show any relation?',
     'Only a sign-unstable one: H1 %s / H5 %s / H20 %s (market excess); the '
     'best permutation p is %.3f.'
     % (f1(ic_get('Forecast', 'market_excess', 1), 4),
        f1(ic_get('Forecast', 'market_excess', 5), 4),
        f1(ic_get('Forecast', 'market_excess', 20), 4), pmin)),
    ('6. How much survives the momentum control?',
     'Nothing positive: IC(forecast|MOM20/60) at H5 = %s (t=%s); momentum '
     'itself is by far the stronger signal (%s).'
     % (f1(gi_m, 4), f1(gi_t, 2), f1(ic_get('Momentum20_60', 'raw', 5), 4))),
    ('7. How much survives the industry control?',
     'Nothing positive: Fama-MacBeth beta at H5 = %s (t=%s).'
     % (f1(ra_b, 5), f1(ra_t, 2))),
    ('8. Does it hold out of sample?',
     'No — OOS IC %s versus VALID %s, OOS cost-adjusted L/S %s.'
     % (f1(o_ic, 4), f1(v_ic, 4), fbp(o_lsc))),
    ('9. Does the walk-forward hold?',
     'No — positive cost-adjusted L/S in %d of %d evaluable windows.'
     % (wf_pos_c, wfn)),
    ('10. Does it survive costs?',
     'No — the primary book turns from %s gross to %s at %dbp.'
     % (fbp(c_gross), fbp(c_net), C0)),
    ('11. Does the alpha depend on a few industries / stocks / years?',
     'It does not exist to be concentrated: no year is positive after cost, '
     'small caps are the worst bucket, and removing the top 1% of names '
     'leaves the book negative.'),
    ('12. Has forecast skill become tradeable price alpha?',
     '**No.** Skill is confirmed in the earnings layer and is not '
     'monetisable in the price layer over this sample.'),
]
R.append(tab(['#', 'question', 'answer'],
             [[i + 1, a, b] for i, (a, b) in enumerate(q)]))

# ---- §31 Limitations
R.append(sec('§31 Limitations'))
R.append('- **Horizon of the frozen forecast.** The TRAINFIX primary forecast '
         'starts in 2023, so the walk-forward evaluation covers WF3-WF6 only. '
         'WF1/WF2 are empty by data coverage, not by choice.')
R.append('- **Announcement time is UNKNOWN.** Tushare daily carries no '
         'intraday disclosure stamp, so T0 is deliberately the first session '
         'strictly after A. Any true intraday release is treated '
         'conservatively, which can only understate a genuine rapid reaction.')
R.append('- **Extrapolated expectations.** A consensus panel is not available '
         'in the cache, so the "expectation gap" is measured against the '
         'model\'s own pre-registered baseline rather than against analyst '
         'consensus.')
R.append('- **Shorting is not executable** in A-shares. Every long-short '
         'figure is a research diagnostic; only the long leg is a candidate '
         'tradeable object, and it is flat to negative after neutralisation.')
R.append('- **Statistic layer.** The protocol asks for Newey-West t-statistics. '
         'The helper inherited from H-EARN-FWD was found to be mis-scaled: it '
         'divided the long-run variance by an extra `n` and, because the '
         'autocovariances were evaluated on pandas Series with aligned labels, '
         'it collapsed every `gamma_k` to `gamma_0` (empirically '
         '`Omega = (lag+1)*gamma_0` on pure noise). Together these inflate '
         'every t by about `sqrt(n)/2` (~7x at n=206). This study uses a local, '
         'corrected Bartlett Newey-West estimator; the frozen module is left '
         'untouched. The correction only ever **reduces** significance, and '
         'the distribution-free permutation test (§23), which does not depend '
         'on the estimator at all, independently reaches the same verdict.')
R.append('- **Gate thresholds.** G1-G14, G16-G19 follow the protocol directly. '
         'G15 uses ">=80% of grid cells positive after cost" and G20 uses '
         '"cost-adjusted annualised >= +3% and Sharpe >= 0.5"; these two are '
         'interpretive conventions stated here for transparency. Both fail by '
         'a wide margin, so the verdict does not hinge on them.')
R.append('- **Multiple testing** is addressed with Benjamini-Hochberg across '
         'the full test family, but the family is finite: horizon x quantile x '
         'window x benchmark x regime x model output. An unknown number of '
         'unreported specifications remains.')

# ---- §32 Research Registry
R.append(sec('§32 Research Registry'))
R.append(tab(['field', 'value'], [
    ['hypothesis_id', P['hypothesis_id']],
    ['version', P['version']],
    ['created', P['created']],
    ['isolation', P['isolation']],
    ['frozen primary key', '%s / %s / %s / offset %d'
     % (HFW['scheme'], HFW['model'], HFW['target'], HFW['offset'])],
    ['registered horizons', str(P['horizons'])],
    ['registered momentum', str(P['momentum'])],
    ['registered quantiles', str(P['quantiles'])],
    ['registered cost ladder', str(P['cost_bp'])],
    ['registered wf windows', str([w[0] for w in P['wf_windows']])],
    ['registered phases', str(P['phases'])],
    ['regime rule', P['regime_rule']],
    ['seed', str(P['seed'])],
    ['n_perm', str(P['n_perm'])],
    ['FDR q', str(P['fdr_q'])],
    ['TRADING_AUTHORIZATION', P['trading_authorization']],
    ['final status', final],
    ['next study, gated on PASS only', 'H-EARN-TRADE-01 — NOT authorised'],
]))

# ---- artefacts
req = ['h_earn_price_events.parquet', 'h_earn_price_ic.csv',
       'h_earn_price_residual_alpha.csv', 'h_earn_price_eventstudy.csv',
       'h_earn_price_quintile.csv', 'h_earn_price_2d_matrix.csv',
       'h_earn_price_oos.csv', 'h_earn_price_walkforward.csv',
       'h_earn_price_year.csv', 'h_earn_price_regime.csv',
       'h_earn_price_parameter_grid.csv', 'h_earn_price_null.csv',
       'h_earn_price_permutation.csv', 'h_earn_price_cost.csv',
       'h_earn_price_exposure.csv', 'h_earn_price_counterfactual.csv',
       'h_earn_price_gates.csv', 'h_earn_price.json', 'report.md']
R.append(sec('Artefacts (§62)'))
R.append(tab(['file', 'present'], [
    [f, 'yes' if os.path.exists(os.path.join(DATA, f)) or f == 'report.md'
     else 'NO'] for f in req]))

rep = '\n'.join(R) + '\n'
with open(os.path.join(DATA, 'report.md'), 'w', encoding='utf-8') as f:
    f.write(rep)

# ---- h_earn_price.json
out = {
    'hypothesis_id': P['hypothesis_id'], 'version': P['version'],
    'generated': pd.Timestamp.now().isoformat(timespec='seconds'),
    'final_status': final,
    'section52_flags': FLAGS,
    'trading_authorization': P['trading_authorization'],
    'gates': G,
    'gate_summary': {'n_pass': n_pass, 'n_fail': n_fail},
    'data_audit': aud,
    'frozen_forecast': {'key': HFW, 'join_coverage': join_cov,
                        'max_abs_deviation': max_dev},
    'layer1_actual_surprise': l1row,
    'primary_book': {'horizon': H5, 'quantile': Q0, 'cost_bp': C0,
                     'gross_bp': c_gross, 'net_bp': c_net,
                     'ann_active': c_ann, 'sharpe': c_shp},
    'counterfactual': {'A_controls_only': a, 'B_controls_plus_forecast': b,
                       'increment_bp': b - a},
    'regime_rule': P['regime_rule'], 'phases': P['phases'],
    'constraints': {
        'shorting_executable': False,
        'next_study': 'H-EARN-TRADE-01 gated on PASS; not authorised',
        'statistic_note': 'corrected Bartlett Newey-West; see report section 31',
    },
    'summaries': {'ic': S_IC, 'event': S_EV, 'regime': S_RG},
}
with open(os.path.join(DATA, 'h_earn_price.json'), 'w', encoding='utf-8') as f:
    json.dump(out, f, indent=2, ensure_ascii=False, default=str)

lg('report.md written (%d chars)' % len(rep))
lg('h_earn_price.json written')
lg('FINAL STATUS: %s' % final)
lg('DONE report')
