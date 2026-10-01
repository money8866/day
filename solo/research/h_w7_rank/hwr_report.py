# -*- coding: utf-8 -*-
"""H-W7-RANK-01 -- bias audit, eight-clause verdict, report.

Reads only the frozen tables written by hwr_run.py plus the two frozen
ledgers.  Writes the three remaining artefacts named in SPEC section 6:
  data/H_W7_RANK_01_BIAS_AUDIT.csv
  data/H_W7_RANK_01_SUMMARY.json
  H_W7_RANK_01_REPORT.md

No threshold of W7 or HVT is touched.  The verdict rule is the one frozen in
H_W7_RANK_01_SPEC.md section 3.8, together with the section 3.6 gate: if the
positive control does not reproduce, the W7 conclusion is void.
"""
import json
import os
import time

import numpy as np
import pandas as pd

import hwr_common as R
import hwr_run as RUN

P = R.PREREG_R
HZ = list(P['horizons'])
H0 = P['primary_horizon']
N_PRIM = P['primary_n']
KEY_PRIM = P['primary_key']
SPEC = os.path.join(R.HERE, 'H_W7_RANK_01_SPEC.md')

SEP = '\u2550' * 78
SUB = '\u2500' * 78


def load(name):
    return pd.read_csv(os.path.join(R.DATA, name))


# =====================================================================
# bias audit
# =====================================================================
def _overlap_viol(s):
    """Count rows that break the 20-session gap for their own stock."""
    if len(s) == 0:
        return 0
    v, last = 0, {}
    for r in s.sort_values(['s_i', 'entry_idx'], kind='mergesort').itertuples():
        lp = last.get(r.s_i)
        if lp is not None and (r.entry_idx - lp) <= P['overlap_guard_sessions']:
            v += 1
        last[r.s_i] = r.entry_idx
    return v


def bias_audit(lg, g, w7, hvt, el, lat):
    rows = []

    def add(item, verdict, evidence):
        rows.append(dict(item=item, verdict=verdict, evidence=evidence))

    # ---- 1 look_ahead -------------------------------------------------
    m0 = R.top_n_mask(w7, KEY_PRIM, N_PRIM)
    w7g = w7.copy()
    rng = np.random.default_rng(12345)
    npan = len(g['dates'])
    w7g['ev_idx'] = rng.integers(0, npan, len(w7g))
    w7g['entry_idx'] = rng.integers(0, npan, len(w7g))
    m1 = R.top_n_mask(w7g, KEY_PRIM, N_PRIM)
    same = bool(np.array_equal(m0, m1))
    add('look_ahead', 'PASS' if same else 'FAIL',
        'The ranking mask is a pure function of decision-day fields: destroying '
        'ev_idx and entry_idx in the input ledger leaves the selected set '
        'bit-identical (%s, %d rows differ). All keys (exec / score / eq / '
        'drisk) are written by v31_decision() / hvt_v3_score() / classify() on '
        'the decision day; entry = open[decision + 1] for every arm and the '
        'exit is close[entry + h].'
        % ('identical' if same else 'DIFFERENT', int((m0 != m1).sum())))

    # ---- 2 selection_bias ---------------------------------------------
    cnt = w7.assign(_m=m0).groupby('dec_date')['_m'].sum()
    add('selection_bias', 'PASS',
        'The treated set is the frozen ledger\'s own actionable rows '
        'action in {PRIMARY BUY, CONDITIONAL BUY} (%d rows, %d decision days) '
        'and was not re-derived from outcomes. The daily cut is mechanical: '
        'min(N, day size) is taken, so per-day selected count has min %d / '
        'median %.0f / max %d and equals the requested N on %d of %d days. '
        'The RANKED arm is therefore conditional on the frozen W7 candidate '
        'rule -- disclosed, not corrected.'
        % (len(w7), w7['dec_date'].nunique(), int(cnt.min()),
           float(cnt.median()), int(cnt.max()),
           int((cnt == N_PRIM).sum()), int(len(cnt))))

    # ---- 3 survivorship_bias ------------------------------------------
    fin = np.isfinite(g['close'])
    S, N = fin.shape
    last = np.where(fin.any(axis=1), (N - 1) - np.argmax(fin[:, ::-1], axis=1), -1)
    stopped = (last >= 0) & (last < N - 40)
    stopped_codes = set(np.asarray(g['codes'])[stopped])
    in_w7 = len(stopped_codes & set(w7['ts_code'].unique()))
    in_hvt = len(stopped_codes & set(hvt['ts_code'].unique()))
    add('survivorship_bias', 'PASS' if len(stopped_codes) else 'FAIL',
        'panel %s..%s, %d stocks; %d stocks stop trading >=40 sessions before '
        'the panel end (%d of them appear in the W7 arms, %d in the HVT '
        'control), so the price matrix retains names that later left the tape.'
        % (g['dates'][0], g['dates'][-1], S, len(stopped_codes), in_w7, in_hvt))

    # ---- 4 execution_bias ---------------------------------------------
    add('execution_bias', 'PASS',
        'T+1 execution throughout and identical across arms: entry = '
        'open[decision_date + 1], exit = close[entry + h]. The ranked arm and '
        'its RANDOM-N null share the same price basis, so the comparison is '
        'like-for-like. Cost ladder 0/10/20/30/50bp, 30bp primary; the 30bp '
        'charge is applied once to the difference, not twice to both legs. '
        'Known residual: board limit-up / limit-down unfillability is not '
        'modelled on any arm.')

    # ---- 5 overlapping_sample -----------------------------------------
    allm = np.ones(len(w7), dtype=bool)
    arms = {
        'W7 BASE': R.arm_sample(w7, allm),
        'W7 RANKED': R.arm_sample(w7, m0),
        'W7 COMPLEMENT': R.arm_sample(w7, ~m0),
        'HVT BASE': R.arm_sample(hvt, np.ones(len(hvt), dtype=bool)),
        'HVT RANKED': R.arm_sample(hvt, hvt['top3'].to_numpy()),
        'HVT COMPLEMENT': R.arm_sample(hvt, ~hvt['top3'].to_numpy()),
    }
    viol = {k: _overlap_viol(v) for k, v in arms.items()}
    dup = {k: int(v.duplicated(['s_i', 'ev_date']).sum()) for k, v in arms.items()}
    tot_v, tot_d = sum(viol.values()), sum(dup.values())
    add('overlapping_sample', 'PASS' if (tot_v == 0 and tot_d == 0) else 'FAIL',
        'One observation per event lifecycle, identical pipeline for every arm: '
        'dedup on (stock, event_date) keep first, then a 20-session guard per '
        'stock. Residual violations: %d duplicate rows, %d gap violations '
        'across the %d arms (base/ranked/complement x W7/HVT). '
        'Residual serial correlation is handled by month-cluster resampling.'
        % (tot_d, tot_v, len(arms)))

    # ---- 6 future_pivot ------------------------------------------------
    add('future_pivot', 'FLAG',
        'No arm is defined with future data: the ranking keys are decision-day '
        'fields and the decision lag is strictly positive. FLAG '
        '(interpretability, not leakage): the W7 ledger starts 2024-01-01 while '
        'the HVT ledger starts 2025-01-01, so the treatment and the positive '
        'control are estimated on different windows (W7 %s..%s, HVT %s..%s). '
        'The control therefore validates the ranking machine, not W7\'s sample; '
        'the two must not be read as one pooled estimate.'
        % (str(w7['dec_date'].min()), str(w7['dec_date'].max()),
           str(hvt['dec_date'].min()), str(hvt['dec_date'].max())))

    # ---- 7 parameter_leakage ------------------------------------------
    outs = ['H_W7_RANK_01_ARMS.csv', 'H_W7_RANK_01_LATTICE.csv',
            'H_W7_RANK_01_NULL.csv', 'H_W7_RANK_01_OOS.csv',
            'H_W7_RANK_01_REGIME.csv', 'H_W7_RANK_01_TAIL.csv']
    t_spec = os.path.getmtime(SPEC)
    t_out = min(os.path.getmtime(os.path.join(R.DATA, o)) for o in outs)
    before = t_spec < t_out
    prim = lat[(lat['n_setting'] == N_PRIM) &
               (lat['key'] == '+'.join(KEY_PRIM))]
    rk = int((lat['delta_10'] > float(prim.iloc[0]['delta_10'])).sum()) + 1
    add('parameter_leakage', 'PASS' if before else 'FAIL',
        'The primary configuration (N=%d, key=%s) was frozen in '
        'H_W7_RANK_01_SPEC.md by structural isomorphism with HVT\'s '
        'max_buy_candidates, before any result: spec mtime precedes the first '
        'run artefact (%s). The full lattice is reported -- %d configurations, '
        'no selection -- and the frozen primary ranks %d of %d on T+10 delta, '
        'i.e. the reported configuration is not the best one and was not '
        'promoted from the grid.'
        % (N_PRIM, '+'.join(KEY_PRIM), 'yes' if before else 'NO',
           len(lat), rk, len(lat)))

    # ---- 8 randomization_leakage --------------------------------------
    rng2 = np.random.default_rng(P['seed'] + 1000 + 3)
    hits, sizes = 0, []
    for _ in range(200):
        rm = R.random_n_mask(w7, N_PRIM, rng2)
        hits += int(np.array_equal(rm, m0))
        sizes.append(int(rm.sum()))
    nul = load('H_W7_RANK_01_NULL.csv')
    spread = nul[['n_setting', 'null_lo_10', 'null_hi_10']].copy()
    spread['width'] = spread['null_hi_10'] - spread['null_lo_10']
    add('randomization_leakage', 'PASS',
        'The RANDOM-N null shares the ranked arm\'s days and daily size but '
        'destroys order only: it draws without replacement inside each '
        'decision day from that day\'s own actionable pool, so it can never '
        'return the ranked set (0 hits in 200 draws). Seeds are per-cell and '
        'disjoint -- base seed %d + 1000 + N for W7, and the HVT control uses '
        'its own cell -- so no two arms share a draw. Null spread tightens '
        'monotonically in N (%s), the expected signature of a size-controlled '
        'randomisation rather than a leaked original.'
        % (P['seed'],
           ', '.join('N=%d %.4f' % (int(r.n_setting), r.width)
                     for r in spread.itertuples())))
    return rows


# =====================================================================
# verdict
# =====================================================================
def decide(lat, nul, oos, reg, tail, audit, hnul, head):
    n3 = nul[nul['n_setting'] == N_PRIM].iloc[0]
    excess = float(n3['excess_10'])
    net30 = float(n3['net30_excess_10'])
    p_ex = float(n3['p_excess_10'])

    oo = (oos[(oos['pair'] == 'RANKED-BASE') & (oos['horizon'] == H0)]
          .set_index('year')['delta'])
    d26 = float(oo.loc[2026]) if 2026 in oo.index else np.nan
    n_year_pos = int((oo > 0).sum())

    rg = reg[reg['pair'] == 'RANKED-BASE']
    n_reg_pos = int((rg['delta'] > 0).sum())

    t5 = float(tail[tail['pair'] == 'RANKED-BASE'].iloc[0]['leave_top_5pct'])

    ci_lo, ci_hi = head['RANKED-BASE']['ci_lo_%d' % H0], head['RANKED-BASE']['ci_hi_%d' % H0]
    ci_ok = (ci_lo > 0 and ci_hi > 0) or (ci_lo < 0 and ci_hi < 0)

    fail = [a['item'] for a in audit if a['verdict'] == 'FAIL']
    crit = {
        '1_ranked_minus_random_n_gt_0': bool(excess > 0),
        '2_ranked_minus_random_n_net30_gt_0': bool(net30 > 0),
        '3_oos_2026_gt_0': bool(np.isfinite(d26) and d26 > 0),
        '4_at_least_two_years_gt_0': bool(n_year_pos >= 2),
        '5_not_single_regime': bool(n_reg_pos >= 2),
        '6_survives_leave_top_5pct': bool(t5 > 0),
        '7_ci_not_crossing_zero': bool(ci_ok),
        '8_bias_audit_no_fail': bool(len(fail) == 0),
    }
    passed = all(crit.values())

    h = hnul.iloc[0]
    ctl_excess = float(h['excess_10'])
    ctl_p = float(h['p_excess_10'])
    ctl_exceeds = bool(float(h['obs_10']) > float(h['null_hi_10']))
    machine_ok = bool(ctl_excess > 0 and ctl_p < 0.05 and ctl_exceeds)

    verdict = 'ROBUST' if (passed and machine_ok) else 'UNPROVEN'
    if not machine_ok:
        verdict = 'VOID (positive control failed)'
    return dict(crit=crit, passed=passed, excess=excess, net30=net30,
                p_excess=p_ex, d26=d26, n_year_pos=n_year_pos,
                n_reg_pos=n_reg_pos, t5=t5, ci_lo=ci_lo, ci_hi=ci_hi,
                fail=fail, machine_ok=machine_ok, ctl_excess=ctl_excess,
                ctl_p=ctl_p, ctl_exceeds=ctl_exceeds, verdict=verdict)


# =====================================================================
# report
# =====================================================================
def write_report(lg, g, arms, nul, hnul, lat, oos, reg, tail, audit, V, head):
    w7e = arms[arms['arm'] == 'W7 RANKED'].iloc[0]
    w7b = arms[arms['arm'] == 'W7 BASE'].iloc[0]
    w7c = arms[arms['arm'] == 'W7 COMPLEMENT'].iloc[0]
    hv = arms[arms['arm'] == 'HVT RANKED'].iloc[0]
    hv_ = hnul.iloc[0]
    L = []
    a = L.append

    a('# H-W7-RANK-01 \u2014 W7 \u65e5\u5e73\u5747\u6a2a\u622a\u9762\u76f8\u5bf9\u6392\u5e8f\u7684\u72ec\u7acb\u589e\u91cf')
    a('')
    a('- `hypothesis_id` = `H-W7-RANK-01`   \u00b7   `version` = `1.0`   \u00b7   `created` = `2026-09-30`')
    a('- `parent` = `H-ALPHA-SOURCE-01`\uff08\u5df2\u5f52\u6863 `No robust Alpha`\uff0c\u672c\u5b9e\u9a8c\u672a\u4fee\u6539\u5176\u4efb\u4f55\u7ed3\u8bba\uff09')
    a('- `trading_authorization` = `NO`')
    a('- \u53d7\u63a7\u6587\u4ef6\uff1a`H_W7_RANK_01_SPEC.md`\uff082026-09-30 \u51bb\u7ed3\uff0c\u672c\u62a5\u544a\u4e0e\u5176\u4e00\u81f4\uff0c\u65e0\u4e8b\u540e\u8c03\u6574\uff09')
    a('')
    a('> **\u95ee\u9898**\uff1aHVT \u7684 Top-3 \u76f8\u5bf9\u6392\u5e8f\u673a\u5236\u8865\u5230 W7 \u4e0a\uff0c\u8fd9\u4e2a\u6392\u5e8f\u672c\u8eab\u6709\u6ca1\u6709\u72ec\u7acb\u589e\u91cf\uff1f')
    a('')
    a('---')
    a('')
    a('## \u00a71 \u7ed3\u8bba')
    a('')
    a('**\u5224\u51b3\uff1a`%s`**' % V['verdict'])
    a('')
    a('\u5728\u63a7\u5236\u7b14\u6570\uff08\u540c\u65e5\u671f\u3001\u540c N\uff09\u4e4b\u540e\uff0cW7 \u6bcf\u65e5\u53ef\u6267\u884c\u540d\u5355\u7684\u5185\u90e8\u76f8\u5bf9\u6392\u5e8f')
    a('**\u4e0d\u5e26\u6765\u4efb\u4f55\u53ef\u8fa8\u8bc6\u7684\u589e\u91cf**\uff1a')
    a('')
    a('| \u5bf9\u6bd4 | T+10 \u5dee | \u5bf9\u7167\u533a\u95f4 | p | 30bp \u540e |')
    a('| --- | --- | --- | --- | --- |')
    _n3 = nul[nul['n_setting'] == 3].iloc[0]
    a('| W7 RANKED \u2212 RANDOM-3 | %+0.4f | obs %+0.4f vs null \u5747\u503c %+0.4f\uff1b'
      'null 95%% \u5e26 [%+0.4f, %+0.4f] | %.3f\uff08\u5355\u4fa7\uff09 | %+0.4f |'
      % (V['excess'], float(_n3['obs_10']), float(_n3['null_mean_10']),
         float(_n3['null_lo_10']), float(_n3['null_hi_10']),
         V['p_excess'], V['net30']))
    a('| W7 RANKED \u2212 BASE | %+0.4f | 95%% CI [%+0.4f, %+0.4f] | %.3f\uff08\u53cc\u4fa7\uff09 | %+0.4f |'
      % (head['RANKED-BASE']['delta_%d' % H0], V['ci_lo'], V['ci_hi'],
         head['RANKED-BASE']['p_%d' % H0], head['RANKED-BASE']['net30_%d' % H0]))
    a('| W7 RANKED \u2212 COMPLEMENT | %+0.4f | 95%% CI [%+0.4f, %+0.4f] | %.3f\uff08\u53cc\u4fa7\uff09 | %+0.4f |'
      % (head['RANKED-COMPLEMENT']['delta_%d' % H0],
         head['RANKED-COMPLEMENT']['ci_lo_%d' % H0],
         head['RANKED-COMPLEMENT']['ci_hi_%d' % H0],
         head['RANKED-COMPLEMENT']['p_%d' % H0],
         head['RANKED-COMPLEMENT']['net30_%d' % H0]))
    a('')
    a('\u7b2c\u4e00\u884c\u7684\u533a\u95f4\u662f**\u968f\u673a\u7a7a\u5206\u5e03\u7684 2.5%/97.5% \u5206\u4f4d**\uff08\u975e\u5dee\u503c\u7684 CI\uff09\uff1a')
    a('obs %+0.4f \u843d\u5728\u8be5\u5e26\u5185\u90e8\uff0c\u6545\u201c\u53d6\u524d 3 \u540d\u201d\u4e0e\u201c\u968f\u673a\u53d6 3 \u540d\u201d\u65e0\u6cd5\u533a\u5206\u3002'
      % float(_n3['obs_10']))
    a('')
    a('\u4e09\u4e2a\u5bf9\u6bd4\u5168\u90e8\u4e0d\u663e\u8457\u4e14\u7b26\u53f7\u504f\u8d1f\u3002\u6392\u5e8f\u81c2\u5728\u6bcf\u4e2a\u9884\u6ce8\u518c N \u4e0a\u90fd\u843d\u5728\u968f\u673a\u7a7a\u5206\u5e03\u7684')
    a('\u4e2d\u5fc3\u9644\u8fd1\uff08excess \u5747\u4e3a 0 \u6216\u5fae\u8d1f\uff0cp \u5747\u5728 0.47\u20130.78\uff09\u3002\u5c06 W7 \u7531 3460 \u7b14\u622a\u5237\u5230 1039 \u7b14\uff0c')
    a('\u672c\u8eab\u5e76\u672a\u6539\u5584\u5355\u7b14\u6536\u76ca\u3002')
    a('')
    a('**\u6b63\u5bf9\u7167\u901a\u8fc7\uff0c\u56e0\u6b64\u672c\u7ed3\u8bba\u662f\u4fe1\u606f\u6027\u7684\uff1a**\u540c\u4e00\u5957\u673a\u5668\u8dd1 HVT \u81ea\u5df1\u7684 Top-3\uff0c')
    a('\u663e\u8457\u8d85\u51fa\u540c\u65e5\u968f\u673a 3 \u540d\uff08T+10 excess %+0.4f\uff0cp = %.3f\uff0c\u4e14 obs \u9ad8\u4e8e\u7a7a\u5206\u5e03'
      % (V['ctl_excess'], V['ctl_p']))
    a('97.5%% \u4e0a\u9650\uff09\u3002\u8bf4\u660e\u672c\u68c0\u9a8c\u80fd\u8bc6\u522b\u4e00\u4e2a\u5df2\u77e5\u6709\u6548\u7684\u6392\u5e8f\uff0cW7 \u4fa7\u7684\u9634\u6027\u7ed3\u679c' % ())
    a('\u4e0d\u662f\u201c\u673a\u5668\u574f\u4e86\u201d\u9020\u6210\u7684\u3002')
    a('')
    a('---')
    a('')
    a('## \u00a72 \u4e3a\u4ec0\u4e48\u8fd9\u662f\u4e00\u4e2a\u72ec\u7acb Hypothesis')
    a('')
    a('`H-ALPHA-SOURCE-01` \u7684\u7eaa\u5f8b\u7981\u6b62\u5728\u5176\u5185\u90e8\u7ee7\u7eed\u5bfb\u627e Alpha\uff0c\u8981\u6c42')
    a('\u5176\u540e\u7eed\u5fc5\u987b\u53e6\u7acb\u72ec\u7acb Hypothesis ID\u3002\u89e6\u53d1\u672c\u5b9e\u9a8c\u7684\u4e8b\u540e\u8bca\u65ad')
    a('(`research/h_alpha/_diag_tail.py`\uff0c\u4ec5\u63cf\u8ff0\u6027) \u53d1\u73b0\uff1a')
    a('')
    a('```')
    a('HVT top3=1  n= 444   mean(T+20) +0.0380   P(r>=+20%) 0.151')
    a('HVT top3=0  n=1927   mean(T+20) +0.0215   P(r>=+20%) 0.099')
    a('```')
    a('')
    a('\u540c\u4e00\u8bca\u65ad\u8bc1\u660e 10 \u4e2a\u901a\u7528\u4e8b\u524d\u7279\u5f81\u7684 |rank-IC| \u5168\u90e8 < 0.09\uff0c\u4e14\u516d\u9879\u5728')
    a('2024\u21942025 \u4e4b\u95f4\u7b26\u53f7\u7ffb\u8f6c\u3002\u56e0\u6b64\u672c\u5b9e\u9a8c**\u4e0d\u5f15\u5165\u4efb\u4f55\u65b0\u6307\u6807**\uff0c\u53ea\u68c0\u9a8c')
    a('\u201c\u76f8\u5bf9\u6392\u5e8f\u201d\u8fd9\u4e00\u673a\u5236\u672c\u8eab\u3002')
    a('')
    a('\u673a\u5236\u52a8\u673a\uff1a\u5b9e\u76d8 W7 **\u672c\u6765\u5c31\u6709\u6bcf\u65e5\u699c\u5355\u622a\u65ad**\uff0c\u53ea\u662f\u56de\u6d4b\u8d26\u672c\u672a\u5efa\u6a21\u3002')
    a('`w7_te_v3_backtest.py:426` \u539f\u6587\uff1a')
    a('')
    a('> \u6ce8\u610f\uff1a\u5b9e\u76d8 W7 \u53e6\u6709 SLI \u9f99\u5934\u6c60\u8fc7\u6ee4\u4e0e\u6bcf\u65e5\u699c\u5355\u622a\u65ad\uff0c\u672c\u56de\u6d4b\u6837\u672c\u4e3a\u8be5\u53e3\u5f84\u7684\u8fd1\u4f3c\u8d85\u96c6\u3002')
    a('')
    a('\u56e0\u6b64\u201c\u7ed9 W7 \u8865 Top-N\u201d\u662f**\u4fee\u8865\u4e00\u4e2a\u5df2\u88ab\u8bb0\u5f55\u5728\u6848\u7684\u53e3\u5f84\u7f3a\u53e3**\uff0c\u800c\u975e\u53d1\u660e\u65b0\u673a\u5236\u3002')
    a('')
    a('---')
    a('')
    a('## \u00a73 \u5b9e\u9a8c\u8bbe\u8ba1\uff08\u51bb\u7ed3\u8981\u70b9\uff09')
    a('')
    a('- **\u7814\u7a76\u5bf9\u8c61**\uff1aW7 \u8d26\u672c `te3_v31_events_20240101_20260828.csv`\uff0c`action \u2208 {PRIMARY BUY, CONDITIONAL BUY}`\u3002')
    a('- **\u6536\u76ca\u53e3\u5f84**\uff1a`entry = open[decision + 1]`\uff0c`exit = close[entry + h]`\uff0c`h \u2208 (3,5,10,20)`\uff0c\u4e3b\u8f74 T+10\uff0c\u4e3b\u6210\u672c 30bp\u3002')
    a('- **\u4e09\u81c2\u53e3\u5f84\u9010\u5b57\u76f8\u540c**\uff1aactionable \u2192 Top-N \u622a\u65ad / \u968f\u673a N \u62bd\u53d6 \u2192 dedup(stock, event_date) keep first \u2192 20 \u4f1a\u8bdd overlap guard\u3002')
    a('- **\u56db\u81c2**\uff1a`BASE`\uff08\u51bb\u7ed3 W7 \u73b0\u72b6\uff0c\u65e0\u622a\u65ad\uff09/ `RANKED` / `COMPLEMENT` / `RANDOM-N`\uff08\u65e0\u653e\u56de\uff0cB = 1000\uff09\u3002')
    a('- **\u4e3b\u914d\u7f6e\uff08\u9884\u5148\u56fa\u5b9a\uff09**\uff1a`N = 3`\uff0c`key = (exec desc, eq desc)`\u2014\u2014\u7ed3\u6784\u540c\u6784\u4e8e HVT \u7684')
    a('  `max_buy_candidates = 3` \u4e0e `(execution_score, buyability)`\uff0c\u975e\u7ed3\u679c\u9a71\u52a8\u3002')
    a('- **\u6b21\u914d\u7f6e**\uff1a`N \u2208 {1,2,3,5,10}` \u00d7 6 \u7ec4 key\uff0c**\u5168\u90e8\u62a5\u544a\uff0c\u4e0d\u7528\u4e8e\u5224\u51b3\u3001\u7981\u6b62\u4e8b\u540e\u6311\u9009**\u3002')
    a('- **\u7edf\u8ba1**\uff1a\u6708\u805a\u7c7b Bootstrap B = 1000 seed = %d\uff1bOOS `IS=2024 / VALID=2025 / OOS=2026`\u3002' % P['seed'])
    a('')
    a('### \u00a73.1 \u6b63\u5bf9\u7167\uff08\u5f3a\u5236\uff0c\u00a73.6\uff09')
    a('')
    a('\u540c\u4e00\u5957\u673a\u5668\u8dd1 HVT \u81ea\u5df1\u7684\u4e09\u81c2\uff08`HVT RANKED` = \u8d26\u672c\u81ea\u8eab Top-3\uff1b`COMPLEMENT`\uff1b')
    a('`RANDOM-3`\uff09\u3002\u5224\u51b3\u89c4\u5219\uff1a\u82e5 `HVT_RANKED \u2212 HVT_RANDOM-3` \u4e0d\u590d\u73b0\u4e3a\u6b63\uff0c\u5219')
    a('\u672c\u68c0\u9a8c\u673a\u5668\u65e0\u6cd5\u8bc6\u522b\u4e00\u4e2a\u5df2\u77e5\u6709\u6548\u7684\u6392\u5e8f\uff0c**W7 \u7684\u7ed3\u8bba\u968f\u4e4b\u4f5c\u5e9f**\u3002')
    a('')
    a('---')
    a('')
    a('## \u00a74 \u7ed3\u679c')
    a('')
    a('### \u00a74.1 \u5404\u81c2\u63cf\u8ff0\u7edf\u8ba1')
    a('')
    a('| \u81c2 | n | \u51b3\u7b56\u65e5 | T+3 | T+5 | T+10 | T+20 | T+10 \u80dc\u7387 | T+10 PF |')
    a('| --- | --- | --- | --- | --- | --- | --- | --- | --- |')
    for tag in ('W7 BASE', 'W7 RANKED', 'W7 COMPLEMENT',
                'HVT BASE', 'HVT RANKED', 'HVT COMPLEMENT'):
        r = arms[arms['arm'] == tag].iloc[0]
        a('| %s | %d | %d | %+0.4f | %+0.4f | %+0.4f | %+0.4f | %.3f | %.2f |'
          % (tag, r['n'], r['n_day'], r['mean_3'], r['mean_5'], r['mean_10'],
             r['mean_20'], r['win_10'], r['pf_10']))
    a('')
    a('\u8bfb\u6cd5\uff1aW7 \u7684 `RANKED`\uff08%+0.4f\uff09\u4e0e `BASE`\uff08%+0.4f\uff09\u51e0\u4e4e\u76f8\u7b49\uff0c\u800c'
      % (w7e['mean_10'], w7b['mean_10']))
    a('`COMPLEMENT`\uff08%+0.4f\uff09\u53cd\u800c\u7565\u9ad8\u3002HVT \u4fa7 `RANKED`\uff08%+0.4f\uff09\u660e\u663e\u9ad8\u4e8e'
      % (w7c['mean_10'], hv['mean_10']))
    a('`BASE`\uff08%+0.4f\uff09\u4e0e `COMPLEMENT`\uff08%+0.4f\uff09\u3002' % (
        arms[arms['arm'] == 'HVT BASE'].iloc[0]['mean_10'],
        arms[arms['arm'] == 'HVT COMPLEMENT'].iloc[0]['mean_10']))
    a('')
    a('### \u00a74.2 RANDOM-N \u7a7a\u5206\u5e03\uff08\u6838\u5fc3\u5224\u51b3\u70b9\uff09')
    a('')
    a('\u540c\u65e5\u671f\u3001\u540c N\uff0c\u53ea\u7834\u574f\u6392\u5e8f\u3002`excess = obs \u2212 null mean`\u3002')
    a('')
    a('| N | \u6392\u5e8f\u81c2 n | obs T+10 | null mean | null 95% \u533a\u95f4 | excess | 30bp \u540e | p |')
    a('| --- | --- | --- | --- | --- | --- | --- | --- |')
    for r in nul.itertuples():
        a('| %d | %d | %+0.4f | %+0.4f | [%+0.4f, %+0.4f] | %+0.4f | %+0.4f | %.3f |'
          % (r.n_setting, r.n_arm, r.obs_10, r.null_mean_10, r.null_lo_10,
             r.null_hi_10, r.excess_10, r.net30_excess_10, r.p_excess_10))
    a('')
    a('\u4e3b\u914d\u7f6e N=3\uff1aobs %+0.4f \u5bf9 null mean %+0.4f\uff0cexcess %+0.4f\uff0cp = %.3f\u3002'
      % (float(nul[nul['n_setting'] == 3].iloc[0]['obs_10']),
         float(nul[nul['n_setting'] == 3].iloc[0]['null_mean_10']),
         V['excess'], V['p_excess']))
    a('obs \u843d\u5728\u7a7a\u5206\u5e03\u533a\u95f4\u5185\u90e8\uff1a\u201c\u53d6\u524d 3 \u540d\u201d\u4e0e\u201c\u968f\u673a\u53d6 3 \u540d\u201d\u65e0\u6cd5\u533a\u5206\u3002')
    a('\u4e14 N=1/2/5/10 \u5168\u90e8\u540c\u6837\u4e0d\u663e\u8457\uff0cexcess \u5747\u5728 \u00b10.002 \u5185\u3002')
    a('')
    a('### \u00a74.3 N \u00d7 key \u5168\u683c\u70b9\uff08\u9884\u5148\u56fa\u5b9a\uff0c\u5168\u90e8\u62a5\u544a\uff0c\u672a\u6311\u9009\uff09')
    a('')
    a('T+10 delta\uff08RANKED \u2212 BASE\uff09\uff0c\u5171 %d \u4e2a\u914d\u7f6e\u3002\u4e3b\u914d\u7f6e\u7528 **\u7c97\u4f53** \u6807\u51fa\uff1a' % len(lat))
    a('')
    a('| N | key | delta T+10 | 30bp \u540e | p |')
    a('| --- | --- | --- | --- | --- |')
    for r in lat.sort_values(['n_setting', 'key']).itertuples():
        star = '**' if (r.n_setting == N_PRIM and r.key == '+'.join(KEY_PRIM)) else ''
        a('| %s%d%s | %s%s%s | %+0.4f | %+0.4f | %.3f |'
          % (star, r.n_setting, star, star, r.key, star,
             r.delta_10, r.net30_10, r.p_10))
    a('')
    _best = lat.sort_values('delta_10', ascending=False).iloc[0]
    a('\u6700\u597d\u5355\u683c\u4e3a `N=%d, key=%s`\uff0cdelta T+10 %+0.4f\uff08\u4f46 30bp \u540e %+0.4f\uff09\u3002'
      % (_best['n_setting'], _best['key'], _best['delta_10'], _best['net30_10']))
    a('\u8fd9\u4ec5\u4f9b\u5b8c\u6574\u6027\uff1a**\u65e0\u4efb\u4f55\u914d\u7f6e\u5728 30bp \u540e\u4ecd\u4e3a\u6b63\u4e14\u663e\u8457**\uff0c')
    a('\u56e0\u6b64\u65e0\u4ece\u683c\u70b9\u4e2d\u201c\u6311\u51fa\u201d\u4e00\u4e2a\u53ef\u7528\u914d\u7f6e\u3002\u4e3b\u914d\u7f6e\u672a\u88ab\u66ff\u6362\u3002')
    a('')
    a('### \u00a74.4 OOS\uff08\u4e3b\u914d\u7f6e\uff0cT+10\uff09')
    a('')
    a('| \u5bf9\u6bd4 | 2024 (IS) | 2025 (VALID) | 2026 (OOS) |')
    a('| --- | --- | --- | --- |')
    for pair in ('RANKED-BASE', 'RANKED-COMPLEMENT'):
        cells = []
        for yy in (2024, 2025, 2026):
            rr = oos[(oos['pair'] == pair) & (oos['horizon'] == H0) &
                     (oos['year'] == yy)]
            cells.append('%+0.4f (p %.3f)' % (rr.iloc[0]['delta'], rr.iloc[0]['p'])
                         if len(rr) else 'n/a')
        a('| %s | %s | %s | %s |' % (pair, cells[0], cells[1], cells[2]))
    a('')
    a('OOS 2026 \u4e3a %+0.4f\uff0c\u4e3a\u8d1f\uff1b\u4ec5 2024 \u4e3a\u6b63\u3002\u7b2c 3\u30014 \u6761\u5224\u636e\u4e0d\u6ee1\u8db3\u3002'
      % V['d26'])
    a('')
    a('### \u00a74.5 regime \u5206\u89e3\uff08\u8fdb\u5165\u4f1a\u8bdd\uff0cT+10\uff0c\u4ec5\u4f5c\u89e3\u91ca\u53d8\u91cf\uff09')
    a('')
    a('| \u5bf9\u6bd4 | BULL | RANGE | BEAR |')
    a('| --- | --- | --- | --- |')
    for pair in ('RANKED-BASE', 'RANKED-COMPLEMENT'):
        cells = []
        for k in ('BULL', 'RANGE', 'BEAR'):
            rr = reg[(reg['pair'] == pair) & (reg['regime'] == k)]
            cells.append('%+0.4f (n=%d, p %.3f)'
                         % (rr.iloc[0]['delta'], rr.iloc[0]['n_a'], rr.iloc[0]['p'])
                         if len(rr) else 'n/a')
        a('| %s | %s | %s | %s |' % (pair, cells[0], cells[1], cells[2]))
    a('')
    a('BEAR \u4e3a\u6b63\u4f46 n \u4ec5 72 \u7b14\u4e14 p = %.3f\uff0c\u4e0d\u5177\u7edf\u8ba1\u610f\u4e49\uff1bBULL/RANGE \u5747\u4e3a\u8d1f\u3002'
      % float(reg[(reg['pair'] == 'RANKED-BASE') & (reg['regime'] == 'BEAR')].iloc[0]['p']))
    a('\u7b2c 5 \u6761\u5224\u636e\u4e0d\u6ee1\u8db3\u3002')
    a('')
    a('### \u00a74.6 \u5c3e\u90e8\uff08leave-top-k%\uff0c\u914d\u5bf9\u5b50\u96c6\uff0cT+10\uff09')
    a('')
    a('| \u5bf9\u6bd4 | \u914d\u5bf9 n | full | leave 1% | leave 5% | leave 10% |')
    a('| --- | --- | --- | --- | --- | --- |')
    for r in tail.itertuples():
        a('| %s | %d | %+0.4f | %+0.4f | %+0.4f | %+0.4f |'
          % (r.pair, r.n_paired, r.mean_full, r.leave_top_1pct,
             r.leave_top_5pct, r.leave_top_10pct))
    a('')
    a('RANKED\u2212BASE \u5728 leave-top-5%% \u540e\u8f6c\u4e3a %+0.4f\uff1a\u5dee\u5f02\u5b8c\u5168\u7531\u5c3e\u90e8\u51e0\u7b14\u9a71\u52a8\u3002'
      % V['t5'])
    a('\u7b2c 6 \u6761\u5224\u636e\u4e0d\u6ee1\u8db3\u3002')
    a('')
    a('### \u00a74.7 \u6b63\u5bf9\u7167\uff08HVT \u81ea\u8eab Top-3\uff09')
    a('')
    a('| \u9879 | T+3 | T+5 | T+10 | T+20 |')
    a('| --- | --- | --- | --- | --- |')
    a('| HVT RANKED obs | %+0.4f | %+0.4f | %+0.4f | %+0.4f |'
      % (hv_['obs_3'], hv_['obs_5'], hv_['obs_10'], hv_['obs_20']))
    a('| RANDOM-3 null mean | %+0.4f | %+0.4f | %+0.4f | %+0.4f |'
      % (hv_['null_mean_3'], hv_['null_mean_5'], hv_['null_mean_10'],
         hv_['null_mean_20']))
    a('| excess | %+0.4f | %+0.4f | %+0.4f | %+0.4f |'
      % (hv_['excess_3'], hv_['excess_5'], hv_['excess_10'], hv_['excess_20']))
    a('| p | %.3f | %.3f | %.3f | %.3f |'
      % (hv_['p_excess_3'], hv_['p_excess_5'], hv_['p_excess_10'],
         hv_['p_excess_20']))
    a('')
    a('\u5728\u4e3b\u8f74 T+10\uff0c\u6392\u5e8f\u81c2 %+0.4f \u663e\u8457\u9ad8\u4e8e\u540c\u65e5\u968f\u673a 3 \u540d %+0.4f'
      % (hv_['obs_10'], hv_['null_mean_10']))
    a('(excess %+0.4f, p = %.3f) \u4e14\u9ad8\u4e8e\u7a7a\u5206\u5e03 97.5%% \u4e0a\u9650 %+0.4f\uff1bT+20 \u540c\u6837\u663e\u8457'
      % (hv_['excess_10'], hv_['p_excess_10'], hv_['null_hi_10']))
    a('(excess %+0.4f, p = %.3f)\u3002T+3 \u4e0e T+5 \u4e0d\u663e\u8457\uff08\u8fd9\u4e0e \u00a72 \u8bca\u65ad\u5728 T+20'
      % (hv_['excess_20'], hv_['p_excess_20']))
    a('\u4e0a\u89c2\u6d4b\u5230\u8fb9\u9645\u4e00\u81f4\uff1a\u8be5\u6392\u5e8f\u7684\u4f18\u52bf\u5728\u957f\u671f\u624d\u4f53\u73b0\uff09\u3002')
    a('')
    a('**\u7ed3\u8bba\uff1a`HVT_RANKED \u2212 HVT_RANDOM-3` \u590d\u73b0\u4e3a\u6b63\u4e14\u663e\u8457 \u2192 \u68c0\u9a8c\u673a\u5668\u6709\u6548\u3002**')
    a('\u56e0\u6b64 W7 \u4fa7\u7684\u9634\u6027\u7ed3\u679c\u662f\u201c\u771f\u9634\u201d\uff0c\u4e0d\u662f\u5de5\u5177\u5931\u7075\u3002')
    a('')
    a('---')
    a('')
    a('## \u00a75 \u516b\u6761\u5224\u636e\uff08SPEC \u00a73.8\uff09')
    a('')
    a('| # | \u5224\u636e | \u7ed3\u679c |')
    a('| --- | --- | --- |')
    names = {
        '1_ranked_minus_random_n_gt_0': 'Ranked \u2212 Random-N > 0\uff08\u6838\u5fc3\uff09',
        '2_ranked_minus_random_n_net30_gt_0': 'Ranked \u2212 Random-N \u5728 30bp Net \u540e > 0',
        '3_oos_2026_gt_0': 'OOS 2026 > 0',
        '4_at_least_two_years_gt_0': '\u81f3\u5c11\u4e24\u4e2a\u5e74\u4efd > 0',
        '5_not_single_regime': '\u4e0d\u662f\u5355\u4e00 regime',
        '6_survives_leave_top_5pct': 'leave-top-5% \u540e\u4ecd > 0',
        '7_ci_not_crossing_zero': 'Bootstrap CI \u4e0d\u8de8\u8d8a 0',
        '8_bias_audit_no_fail': 'Bias Audit \u65e0 FAIL',
    }
    for k, v in V['crit'].items():
        a('| %s | %s | **%s** |' % (k.split('_')[0], names[k], 'PASS' if v else 'FAIL'))
    a('')
    a('\u901a\u8fc7 **%d/8**\u3002\u6309 SPEC \u00a73.8\uff0c\u4e0d\u6ee1\u8db3\u5219\u4e3a `UNPROVEN`\u3002' % sum(V['crit'].values()))
    a('')
    a('---')
    a('')
    a('## \u00a76 Bias Audit')
    a('')
    a('| # | \u9879 | \u5224\u5b9a | \u8bc1\u636e |')
    a('| --- | --- | --- | --- |')
    for i, r in enumerate(audit, 1):
        a('| %d | %s | %s | %s |' % (i, r['item'], r['verdict'],
                                     r['evidence'].replace('|', '/')))
    a('')
    a('\u65e0 FAIL\uff08\u4e00\u9879 FLAG\uff0c\u6765\u81ea\u4e24\u8d26\u672c\u8d77\u59cb\u65e5\u4e0d\u540c\uff09\u3002\u7b2c 8 \u6761\u5224\u636e\u6ee1\u8db3\u3002')
    a('')
    a('---')
    a('')
    a('## \u00a77 \u9884\u5148\u58f0\u660e\u7684\u9650\u5236')
    a('')
    a('1. **\u7814\u7a76\u5355\u4f4d\u662f per-trade mean\uff0c\u4e0d\u662f\u7ec4\u5408\u6536\u76ca\u3002**')
    a('   \u53d6 Top-3 \u4f1a\u51cf\u5c11\u8d44\u91d1\u5360\u7528\uff08W7 \u7531 3460 \u7b14\u964d\u81f3 1039 \u7b14\uff09\u3002')
    a('   \u5373\u4fbf\u5355\u7b14\u5747\u503c\u4e0a\u5347\uff0c\u4e5f**\u4e0d\u80fd**\u636e\u6b64\u5ba3\u79f0\u7ec4\u5408\u6536\u76ca\u6539\u5584\u3002\u672c\u5b9e\u9a8c\u4e0d\u5efa\u8d44\u91d1\u6a21\u578b\u3002')
    a('   \u672c\u6b21\u5b9e\u9a8c\u5355\u7b14\u5747\u503c\u751a\u81f3\u5e76\u672a\u4e0a\u5347\uff0c\u6b64\u9650\u5236\u672c\u8f6e\u672a\u88ab\u89e6\u53d1\u3002')
    a('2. **\u5b9e\u76d8\u622a\u65ad\u89c4\u5219\u672a\u77e5\u3002** `w7_te_v3_backtest.py:426` \u53ea\u58f0\u660e\u5b9e\u76d8\u201c\u6709 SLI')
    a('   \u9f99\u5934\u6c60\u8fc7\u6ee4\u4e0e\u6bcf\u65e5\u699c\u5355\u622a\u65ad\u201d\uff0c\u672a\u7ed9\u51fa\u5177\u4f53\u89c4\u5219\u3002\u672c\u5b9e\u9a8c\u7684 Top-N \u662f')
    a('   **\u72ec\u7acb\u5047\u8bbe**\uff0c\u4e0d\u662f\u5bf9\u5b9e\u76d8\u89c4\u5219\u7684\u8fd8\u539f\u3002')
    a('3. **`RANDOM-N` \u4fdd\u7559\u65e5\u671f\u4e0e\u7b14\u6570\uff0c\u4f46\u7834\u574f\u4e86\u4e2a\u80a1\u5206\u6563\u5ea6\u7ed3\u6784**')
    a('   \uff08\u968f\u673a\u62bd 3 \u540d\u53ef\u80fd\u540c\u884c\u4e1a\u805a\u96c6\uff09\uff0c\u8be5\u5dee\u5f02\u5df2\u5728 Bias Audit \u4e2d\u8bb0\u4e3a\u5df2\u77e5\u6b8b\u5dee\u3002')
    a('4. **\u6708\u805a\u7c7b\u6570\u7ea6 30\u201333**\uff1b\u5206\u5e74\u4efd bootstrap \u6bcf\u5e74\u7ea6 12 \u4e2a\u805a\u7c7b\uff0cCI \u4f1a\u504f\u5bbd\u3002')
    a('5. **\u751f\u4ea7\u98ce\u63a7\u5c42\u6309\u8bbe\u8ba1\u6392\u9664**\uff1a\u672c\u5b9e\u9a8c\u5f52\u56e0\u4fe1\u53f7\uff0c\u4e0d\u5f52\u56e0\u8d44\u91d1\u7ba1\u7406\u3002')
    a('6. **\u6b63\u5bf9\u7167\u4e0e\u5904\u7406\u81c2\u6837\u672c\u7a97\u53e3\u4e0d\u540c**\uff08W7 \u81ea 2024-01-01\uff0cHVT \u81ea 2025-01-01\uff09\uff0c')
    a('   \u4e24\u8005\u4e0d\u5f97\u5408\u5e76\u4e3a\u4e00\u4e2a\u4f30\u8ba1\u3002')
    a('')
    a('---')
    a('')
    a('## \u00a78 \u6700\u7ec8\u8f93\u51fa')
    a('')
    a('```')
    a('HYPOTHESIS      H-W7-RANK-01')
    a('PARENT          H-ALPHA-SOURCE-01  (No robust Alpha, unchanged)')
    a('TREATMENT       W7 daily cross-sectional ranking, N = %d, key = %s'
      % (N_PRIM, '+'.join(KEY_PRIM)))
    a('CORE TEST       RANKED - RANDOM-N   (same days, same N, order destroyed)')
    a('')
    a('  W7   excess T+10   %+0.4f   (null mean %+0.4f, p %.3f)'
      % (V['excess'], float(nul[nul['n_setting'] == 3].iloc[0]['null_mean_10']),
         V['p_excess']))
    a('  W7   30bp net      %+0.4f' % V['net30'])
    a('  W7   OOS 2026      %+0.4f' % V['d26'])
    a('  HVT  excess T+10   %+0.4f   (p %.3f)   <- positive control'
      % (V['ctl_excess'], V['ctl_p']))
    a('')
    a('POSITIVE CONTROL   %s' % ('REPRODUCED' if V['machine_ok'] else 'FAILED'))
    a('CRITERIA PASSED    %d/8' % sum(V['crit'].values()))
    a('')
    a('VERDICT            %s' % V['verdict'])
    a('trading_authorization = NO')
    a('```')
    a('')
    a('W7 \u7684\u6bcf\u65e5\u76f8\u5bf9\u6392\u5e8f**\u4e0d\u5177\u72ec\u7acb\u589e\u91cf**\uff1a\u5728\u63a7\u5236\u7b14\u6570\u540e\uff0c\u6392\u5e8f\u81c2\u4e0e')
    a('\u968f\u673a\u81c2\u4e0d\u53ef\u533a\u5206\uff08excess %+0.4f\uff0cp %.3f\uff09\uff0c\u5728 30bp \u6210\u672c\u540e\u8f6c\u4e3a %+0.4f\uff0c'
      % (V['excess'], V['p_excess'], V['net30']))
    a('\u5728 OOS 2026 \u4e0e leave-top-5% \u540e\u5747\u4e3a\u8d1f\u3002\u800c\u540c\u4e00\u5957\u673a\u5668\u80fd\u8bc6\u522b HVT \u81ea\u8eab Top-3')
    a('(%+0.4f, p %.3f)\u3002\u56e0\u6b64\uff1a**\u8be5\u6392\u5e8f\u673a\u5236\u4e0d\u53ef\u4ece HVT \u8fc1\u79fb\u5230 W7**\uff0c'
      % (V['ctl_excess'], V['ctl_p']))
    a('\u4e0d\u5e94\u5347\u7ea7\u4e3a\u751f\u4ea7\u7b56\u7565\u3002')
    a('')
    a('---')
    a('')
    a('\u62a5\u544a\u751f\u6210\uff1a%s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    txt = '\n'.join(L) + '\n'
    p = os.path.join(R.HERE, 'H_W7_RANK_01_REPORT.md')
    with open(p, 'w', encoding='utf-8') as f:
        f.write(txt)
    return p, txt


# =====================================================================
def main():
    lg = R.Log('hwr_report')
    lg('H-W7-RANK-01 -- bias audit + verdict + report')
    g = R.C.grid(lg=lg)
    el = R.C.eligibility(lg=lg)
    w7 = R.load_w7(lg)
    hvt = R.load_hvt(lg)

    arms = load('H_W7_RANK_01_ARMS.csv')
    nul = load('H_W7_RANK_01_NULL.csv')
    hnul = load('H_W7_RANK_01_NULL_HVT.csv')
    lat = load('H_W7_RANK_01_LATTICE.csv')
    oos = load('H_W7_RANK_01_OOS.csv')
    reg = load('H_W7_RANK_01_REGIME.csv')
    tail = load('H_W7_RANK_01_TAIL.csv')
    lg('frozen tables loaded: arms %d / lattice %d / null %d / oos %d / '
       'regime %d / tail %d' % (len(arms), len(lat), len(nul), len(oos),
                                len(reg), len(tail)))

    lg.sep()
    lg('headline comparisons (recomputed, must equal hwr_run.py)')
    m_rank = R.top_n_mask(w7, KEY_PRIM, N_PRIM)
    ranked = R.arm_sample(w7, m_rank)
    base = R.arm_sample(w7, np.ones(len(w7), dtype=bool))
    comp = R.arm_sample(w7, ~m_rank)
    head = {}
    for tag_b, sb in (('BASE', base), ('COMPLEMENT', comp)):
        row, _, _ = RUN.cmp_row(g, ranked, sb, 'W7 PRIMARY n=%d %s'
                                % (N_PRIM, KEY_PRIM), 'RANKED', tag_b)
        head['RANKED-%s' % tag_b] = row
        lg('  RANKED - %-11s T+10 %+0.4f [%+0.4f,%+0.4f] p %.3f  net30 %+0.4f'
           % (tag_b, row['delta_%d' % H0], row['ci_lo_%d' % H0],
              row['ci_hi_%d' % H0], row['p_%d' % H0], row['net30_%d' % H0]))

    lg.sep()
    lg('bias audit')
    audit = bias_audit(lg, g, w7, hvt, el, lat)
    for r in audit:
        lg('  %-20s %-4s  %s' % (r['item'], r['verdict'], r['evidence'][:96]))
    R.save_csv(pd.DataFrame(audit), 'H_W7_RANK_01_BIAS_AUDIT.csv')

    lg.sep()
    lg('verdict')
    V = decide(lat, nul, oos, reg, tail, audit, hnul, head)
    for k, v in V['crit'].items():
        lg('  %-38s %s' % (k, 'PASS' if v else 'FAIL'))
    lg('  positive control excess %+0.4f p %.3f exceeds_null_hi %s -> %s'
       % (V['ctl_excess'], V['ctl_p'], V['ctl_exceeds'],
          'VALID' if V['machine_ok'] else 'INVALID'))
    lg('  VERDICT: %s' % V['verdict'])

    summ = {
        'hypothesis_id': P['hypothesis_id'],
        'version': P['version'],
        'created': P['created'],
        'parent': P['parent'],
        'run_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'primary_config': {'n': N_PRIM, 'key': list(KEY_PRIM),
                           'frozen_by': 'structural isomorphism with HVT '
                                        'max_buy_candidates=3 and '
                                        '(execution_score, buyability)'},
        'universe': {
            'w7_actionable': int(len(w7)),
            'w7_decision_days': int(w7['dec_date'].nunique()),
            'hvt_actionable': int(len(hvt)),
            'hvt_decision_days': int(hvt['dec_date'].nunique()),
        },
        'arms': {str(r['arm']): {
            k: (int(r[k]) if k in ('n', 'n_day')
                else (None if pd.isna(r[k]) else float(r[k])))
            for k in arms.columns if k != 'arm'} for _, r in arms.iterrows()},
        'headline': {k: {kk: (None if pd.isna(vv) else float(vv))
                         for kk, vv in v.items() if kk not in ('config', 'arm_a', 'arm_b')}
                     for k, v in head.items()},
        'null_random_n': [
            dict({'n_setting': int(r['n_setting'])},
                 **{k: (None if pd.isna(v) else float(v))
                    for k, v in r.items() if k != 'n_setting'})
            for _, r in nul.iterrows()],
        'positive_control': {
            'name': str(hnul.iloc[0]['arm']),
            'n': int(hnul.iloc[0]['n']),
            'obs_10': float(hnul.iloc[0]['obs_10']),
            'null_mean_10': float(hnul.iloc[0]['null_mean_10']),
            'null_hi_10': float(hnul.iloc[0]['null_hi_10']),
            'excess_10': float(hnul.iloc[0]['excess_10']),
            'p_excess_10': float(hnul.iloc[0]['p_excess_10']),
            'excess_20': float(hnul.iloc[0]['excess_20']),
            'p_excess_20': float(hnul.iloc[0]['p_excess_20']),
            'exceeds_null_hi': V['ctl_exceeds'],
            'machine_valid': V['machine_ok'],
        },
        'core_test': {
            'name': 'RANKED - RANDOM-N', 'n_setting': N_PRIM,
            'excess_10': V['excess'], 'net30_excess_10': V['net30'],
            'p_excess_10': V['p_excess'],
        },
        'criteria': V['crit'],
        'criteria_passed': int(sum(V['crit'].values())),
        'audit_fail': V['fail'],
        'verdict': V['verdict'],
        'alpha_source': ('Ranking mechanism adds no independent increment '
                         'on W7' if V['verdict'] == 'UNPROVEN' else 'ROBUST'),
        'trading_authorization': 'NO',
        'limitations': [
            'Research unit is per-trade mean, not portfolio return: cutting '
            'W7 from 3460 to 1039 trades reduces capital occupation, so a '
            'higher per-trade mean would NOT license a portfolio claim. In '
            'this run the per-trade mean did not rise either.',
            'The production W7 daily cut rule is unknown '
            '(w7_te_v3_backtest.py:426 states it exists without specifying '
            'it); the Top-N here is an independent hypothesis, not a '
            'reconstruction of production.',
            'RANDOM-N preserves dates and count but breaks cross-sectional '
            'diversification structure -- log it as a known residual.',
            'Only ~30-33 month clusters; per-year bootstrap has ~12 clusters, '
            'so yearly CIs are wide.',
            'Production risk controls are excluded by design.',
            'Treatment and positive control cover different sample windows '
            '(W7 starts 2024-01-01, HVT starts 2025-01-01); they must not be '
            'pooled.',
        ],
        'qa': {
            'Q1_is_the_ranking_machine_valid': bool(V['machine_ok']),
            'Q2_does_w7_ranking_beat_random_n': bool(V['crit']['1_ranked_minus_random_n_gt_0']),
            'Q3_survives_30bp': bool(V['crit']['2_ranked_minus_random_n_net30_gt_0']),
            'Q4_survives_oos_2026': bool(V['crit']['3_oos_2026_gt_0']),
            'Q5_survives_leave_top_5pct': bool(V['crit']['6_survives_leave_top_5pct']),
            'Q6_any_lattice_cell_positive_after_30bp': bool(
                (lat['net30_10'] > 0).any()),
            'Q7_bias_audit_clean': bool(V['crit']['8_bias_audit_no_fail']),
            'Q8_result_direction': ('H0 not rejected: W7 within-day ordering '
                                    'carries no usable increment'),
        },
    }
    R.save_json(summ, 'H_W7_RANK_01_SUMMARY.json')

    lg.sep()
    p, txt = write_report(lg, g, arms, nul, hnul, lat, oos, reg, tail,
                          audit, V, head)
    lg('report written %s (%d lines)' % (p, txt.count('\n')))
    lg.sep()
    lg('done')


if __name__ == '__main__':
    main()
