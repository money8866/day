# -*- coding: utf-8 -*-
"""H-SLG-02 -- report generator + delivery check.

Consumes H_SLG_02_RESULTS.json (written by slg2_run.py) plus the nine CSV
artefacts, answers the ten pre-registered questions (SPEC section 19), prints
the frozen conclusion block (SPEC section 20) verbatim, and writes:

    H_SLG_02_SUMMARY.json
    H_SLG_02_REPORT.md

It then verifies that every artefact required by SPEC section 21 + FREEZE
section 8 exists and is non-empty.  Exit code 0 means the study is delivered.

This script evaluates; it never re-fits.  No threshold, window, factor
definition or direction is touched here (SPEC section 22).
"""
import os
import sys
import json
import time

import numpy as np
import pandas as pd

import slg_common as S
import slg2_common as CM

P = S.PREREG_S
P2 = CM.PREREG2
OUT = S.OUT
RES = os.path.join(OUT, 'H_SLG_02_RESULTS.json')

EQ = '\u2550' * 46
RULE = '\u2500' * 46
LOG = S.Log('slg2_report')

HSTAR = CM.HSTAR2

REQUIRED = [
    'second_leg2_factor_ic.csv',
    'second_leg2_factor_quantile.csv',
    'second_leg2_factor_composite.csv',
    'second_leg2_factor_incremental.csv',
    'second_leg2_factor_oos.csv',
    'second_leg2_factor_param.csv',
    'second_leg2_factor_null.csv',
    'second_leg2_factor_subgroup.csv',
    'second_leg2_factor_funnel.csv',
    'H_SLG_02_REPORT.md',
]
EXTRA = [
    'H_SLG_02_RESULTS.json',
    'H_SLG_02_SUMMARY.json',
    'H_SLG_02_SPEC.md',
    'H_SLG_02_FREEZE.md',
    'H_SLG_02_SCAN.json',
    'H_SLG_02_BIAS_AUDIT.csv',
]

DISCLAIMER = (
    '本研究只回答「主升后整理完成度」作为**连续横截面因子**是否具有稳定预测力，'
    '以及它在动量信息之外增加了多少信息。'
    '它**不是**买入信号生成器，**不做**实盘授权（`trading_authorization = NO`）。'
)

# SPEC 18 pre-registered factor direction (§7 of the SPEC / FREEZE 5.1)
FDIR = dict(zip(CM.FACTOR_IDS, (d for _, _, d in CM.FACTOR_SPEC)))
FID = CM.FACTOR_IDS

# SPEC 19 Q3..Q7 grouping of P1..P16 onto the question that owns them
GROUPS = {
    'Q3': ('量能收缩', ('P1', 'P2', 'P3')),
    'Q4': ('波动收敛', ('P4',)),
    'Q5': ('MA20/MA60结构', ('P5', 'P6', 'P7', 'P8', 'P9')),
    'Q6': ('相对行业强度', ('P14',)),
    'Q7': ('右底质量/接近阻力', ('P10', 'P11', 'P12', 'P13')),
}


# ------------------------------------------------------------------ helpers
def f(x, nd=4):
    return S.fmt(x, nd)


def pc(x, nd=2):
    return S.fmtp(x, nd)


def num(x, d=np.nan):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return d
    return v if np.isfinite(v) else d


def g(d, key, default=None):
    if not isinstance(d, dict):
        return default
    v = d.get(key, default)
    return default if v is None else v


def row_of(rows, **kw):
    for r in rows:
        if all(r.get(k) == v for k, v in kw.items()):
            return r
    return None


def table(rows, cols, headers=None):
    heads = headers or list(cols)
    out = ['| ' + ' | '.join(heads) + ' |',
           '|' + '|'.join(['---'] * len(cols)) + '|']
    for r in rows:
        out.append('| ' + ' | '.join(str(r.get(c, '')) for c in cols) + ' |')
    return '\n'.join(out)


def sgn(x):
    v = num(x)
    return 0.0 if not np.isfinite(v) else float(np.sign(v))


def same3(d, keys=('ic_is', 'ic_valid', 'ic_oos')):
    v = [num(d.get(k)) for k in keys]
    if not all(np.isfinite(x) for x in v):
        return False
    s = [np.sign(x) for x in v]
    return s[0] != 0 and s[0] == s[1] == s[2]


def load_csv(name):
    p = os.path.join(OUT, name)
    if not os.path.exists(p) or os.path.getsize(p) == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


def df_rows(df, **kw):
    if df.empty:
        return []
    m = pd.Series(True, index=df.index)
    for k, v in kw.items():
        if k not in df.columns:
            return []
        m &= (df[k] == v)
    return df[m].to_dict('records')


def get(df, **kw):
    r = df_rows(df, **kw)
    return r[0] if r else {}


# ------------------------------------------------------------------ main
def main():
    t0 = time.time()
    LOG.sep('=')
    LOG('H-SLG-02 report  version %s' % P2['version'])
    LOG.sep('=')

    if not os.path.exists(RES):
        LOG('FATAL: %s missing -- run slg2_run.py first' % RES)
        return 1
    with open(RES, encoding='utf-8') as fh:
        R = json.load(fh)

    panel = g(R, 'panel', {})
    pool = g(R, 'pool', {})
    dup = g(R, 'duplication', {})
    funnel = g(R, 'funnel', [])
    scan = g(R, 'scan', {})
    param = g(R, 'parameter', {})
    verdicts = g(R, 'verdicts', {})
    fver = g(R, 'factor_verdicts', {})
    inc_sum = g(R, 'incremental_summary', {})
    nulls = g(R, 'null', [])
    subgroup = g(R, 'subgroup', [])
    audit = g(R, 'bias_audit', [])
    sig_ids = list(g(R, 'comp_sig_factors', []) or [])

    ic_df = load_csv('second_leg2_factor_ic.csv')
    comp_df = load_csv('second_leg2_factor_composite.csv')
    q_df = load_csv('second_leg2_factor_quantile.csv')
    inc_df = load_csv('second_leg2_factor_incremental.csv')
    oos_df = load_csv('second_leg2_factor_oos.csv')
    par_df = load_csv('second_leg2_factor_param.csv')

    n_uf = int(num(pool.get('U_FACTOR_cells'), 0))
    n_ep = int(num(pool.get('unique_episodes'), 0))
    ready_share = num(dup.get('READY_share'))
    fail_share = num(dup.get('FAILED_share'))

    v_all = verdicts.get('COMP_ALL', {}) or {}
    v_sig = verdicts.get('COMP_SIG', {}) or {}

    # ---- per-factor phase IC straight from the R-ALL book -------------
    fac_tab = []
    for k in FID:
        base = {'id': k, 'name': CM.FACTOR_NAME[k], 'dir': FDIR[k]}
        r_is = get(ic_df, asset=k, kind=k, h=HSTAR, book='R-ALL',
                   target='sec', scope='IS')
        r_va = get(ic_df, asset=k, kind=k, h=HSTAR, book='R-ALL',
                   target='sec', scope='VALID')
        r_oo = get(ic_df, asset=k, kind=k, h=HSTAR, book='R-ALL',
                   target='sec', scope='OOS')
        r_dd = get(ic_df, asset=k, kind=k, h=HSTAR, book='R-DEDUP',
                   target='sec', scope='ALL')
        base.update({'ic_is': num(r_is.get('ic_mean')),
                     't_is': num(r_is.get('t_nw')),
                     'ic_val': num(r_va.get('ic_mean')),
                     't_val': num(r_va.get('t_nw')),
                     'ic_oos': num(r_oo.get('ic_mean')),
                     'ic_dd': num(r_dd.get('ic_mean')),
                     'verdict': (fver.get(k, {}) or {}).get('verdict', 'n/a'),
                     'n_pass': (fver.get(k, {}) or {}).get('n_pass'),
                     'flags': ' '.join((fver.get(k, {}) or {}).get('flags', []) or []) or '-',
                     'mono': num((fver.get(k, {}) or {}).get('mono')),
                     'spr': num((fver.get(k, {}) or {}).get('spread_net30'))})
        base['same3'] = same3(base, keys=('ic_is', 'ic_val', 'ic_oos'))
        base['dir_ok'] = bool(np.isfinite(base['ic_is']) and
                              sgn(base['ic_is']) == sgn(1.0 if FDIR[k] == '+'
                                                        else -1.0))
        base['sig_is'] = bool(np.isfinite(base['t_is'])
                              and abs(base['t_is']) >= P2['T_MIN'])
        base['strong'] = bool(base['dir_ok'] and base['sig_is']
                              and same3(base, keys=('ic_is', 'ic_val', 'ic_oos')))
        fac_tab.append(base)

    n_same3 = sum(1 for r in fac_tab if r['same3'])
    n_strong = sum(1 for r in fac_tab if r['strong'])
    strong_list = [r['id'] for r in fac_tab if r['strong']]
    same3_list = ['%s(%s)' % (r['id'], r['name']) for r in fac_tab if r['same3']]

    # ---- group verdicts (Q3..Q7) --------------------------------------
    def grp_verdict(ids):
        st = [fver.get(k, {}).get('bits', {}) for k in ids]
        good = [s for s in st if s]
        if any(s.get('g1_dir') and s.get('g2_is_t') and s.get('g3_valid_same')
               and s.get('g4_oos_same') for s in good):
            return 'PASS'
        if any(s.get('g1_dir') and s.get('g2_is_t') for s in good):
            return 'MIXED'
        return 'FAIL'

    grp = {q: (nm, ids, grp_verdict(ids)) for q, (nm, ids) in GROUPS.items()}

    # ---- COMP vs momentum / single factors ----------------------------
    comp_all_is = get(comp_df, asset='COMP_ALL', h=HSTAR, book='R-ALL',
                      target='sec', scope='IS')
    comp_all_oo = get(comp_df, asset='COMP_ALL', h=HSTAR, book='R-ALL',
                      target='sec', scope='OOS')
    comp_all_va = get(comp_df, asset='COMP_ALL', h=HSTAR, book='R-ALL',
                      target='sec', scope='VALID')
    ic_ca_is = num(comp_all_is.get('ic_mean'))
    ic_ca_va = num(comp_all_va.get('ic_mean'))
    ic_ca_oo = num(comp_all_oo.get('ic_mean'))
    best_fac = max((abs(num(r['ic_is'])) for r in fac_tab), default=np.nan)
    beta_t_m1 = num(inc_sum.get('beta_t_M1_y5'))
    dr2_m1 = num(inc_sum.get('dR2_M1_M0_y5'))
    dr2_m31 = num(inc_sum.get('dR2_M3_M1_y5'))

    # ---- quantiles / cost for COMP_ALL at h* --------------------------
    q_comp = {}
    for qq in range(1, int(P2['Q_GROUPS']) + 1):
        r = get(q_df, asset='COMP_ALL', h=HSTAR, group='Q%d' % qq)
        q_comp[qq] = r
    q_spread = get(q_df, asset='COMP_ALL', h=HSTAR,
                   group='Q%d-Q1' % int(P2['Q_GROUPS']))
    q5 = q_comp.get(int(P2['Q_GROUPS']), {})
    cost_q5 = {bp: (num(q5.get('gross')) if bp == 0
                    else num(q5.get('net%d' % bp))) for bp in P2['cost_bp']}
    spread_net30 = num(q_spread.get('spread_net30'))
    mono_comp = num(q5.get('monotonicity'))
    cost_pos = [bp for bp, v in cost_q5.items() if np.isfinite(v) and v > 0]

    # ---- null ---------------------------------------------------------
    n1_all = row_of(nulls, model='N1', asset='COMP_ALL') or {}
    n2_all = row_of(nulls, model='N2', asset='COMP_ALL') or {}
    n3_all = row_of(nulls, model='N3', asset='COMP_ALL') or {}
    n1p = num(n1_all.get('p_one_sided'))

    # ---- yearly stability (walk-forward book) -------------------------
    yr = [r for r in (g(R, 'oos', []) or []) if r.get('scope') == 'year'
          and r.get('asset') == 'COMP_ALL']
    yr_ic = [(str(r.get('bucket')), num(r.get('IC'))) for r in yr]
    base_sign = sgn(ic_ca_is)
    yr_ok = [y for y, v in yr_ic if np.isfinite(v) and sgn(v) == base_sign
             and base_sign != 0]
    pos_share = (len(yr_ok) / len(yr_ic)) if yr_ic else np.nan
    year_pass = bool(np.isfinite(pos_share) and pos_share >= 0.60)

    # ---- parameter stability -----------------------------------------
    pr_a = num(param.get('positive_ratio_COMP_ALL'))
    pr_c = num(param.get('positive_ratio_COMP_SIG'))
    tag_a = str(param.get('tag_COMP_ALL', 'NA'))
    tag_c = str(param.get('tag_COMP_SIG', 'NA'))
    param_pass = bool(tag_a == 'stable')

    # ---- stable / failing conditional regions ------------------------
    def region(dim_pred, ok):
        out = []
        for r in subgroup:
            if r.get('asset') != 'COMP_ALL' or not dim_pred(r.get('dimension')):
                continue
            i_is, i_va, i_oo = (num(r.get('IC_IS')), num(r.get('IC_VALID')),
                                num(r.get('IC_OOS')))
            if not all(np.isfinite(x) for x in (i_is, i_va, i_oo)):
                continue
            if ok(i_is, i_va, i_oo):
                out.append(r)
        return out

    def _same(a, b, c):
        s = [np.sign(a), np.sign(b), np.sign(c)]
        return s[0] != 0 and s[0] == s[1] == s[2]

    def _broke(a, b, c):
        return np.sign(a) != 0 and np.sign(c) != 0 and np.sign(a) != np.sign(c)

    stable_layers = region(lambda d: True, _same)
    fail_layers = region(lambda d: True, _broke)
    stable_txt = ('；'.join('%s=%s(IS %s / VALID %s / OOS %s)'
                            % (r['dimension'], r['layer'], f(r['IC_IS'], 4),
                               f(r['IC_VALID'], 4), f(r['IC_OOS'], 4))
                            for r in stable_layers) or '无（无任何分层在 IS/VALID/OOS 三段同号）')
    fail_txt = ('；'.join('%s=%s(IS %s → OOS %s)'
                          % (r['dimension'], r['layer'], f(r['IC_IS'], 4),
                             f(r['IC_OOS'], 4))
                          for r in fail_layers) or '无')

    # ---- SPEC 20 answers ---------------------------------------------
    A1 = ('PASS' if n_uf >= 10000 else ('MIXED' if n_uf > 0 else 'FAIL'))
    A2 = ('PASS' if n_strong >= 3 else ('MIXED' if n_strong >= 1 else 'FAIL'))
    if (np.isfinite(ic_ca_is) and np.isfinite(beta_t_m1)
            and beta_t_m1 >= P2['T_MIN'] and np.isfinite(dr2_m1)
            and dr2_m1 > 0 and np.isfinite(ic_ca_oo)
            and sgn(ic_ca_oo) == sgn(ic_ca_is) and sgn(ic_ca_is) != 0):
        A3 = 'PASS'
    elif np.isfinite(ic_ca_is) and np.isfinite(ic_ca_va) and np.isfinite(ic_ca_oo) \
            and _same(ic_ca_is, ic_ca_va, ic_ca_oo):
        A3 = 'MIXED'
    else:
        A3 = 'FAIL'

    oos_pass = bool(np.isfinite(ic_ca_oo)
                    and sgn(ic_ca_oo) == sgn(ic_ca_is) and sgn(ic_ca_is) != 0)
    n_pass_all = int(num(v_all.get('n_pass'), 0))
    n_bits_all = int(num(v_all.get('n_bits'), 12))
    verdict_all = str(v_all.get('verdict', 'NA'))

    if verdict_all == 'ROBUST' and oos_pass and param_pass:
        final_state = 'RESEARCH_VALIDATED'
    elif n_pass_all >= 9:
        final_state = 'PARTIALLY_VALIDATED'
    else:
        final_state = 'FAILED'

    cost_txt = ' / '.join('%dbp Q5 net=%s' % (bp, f(cost_q5[bp], 5))
                          for bp in P2['cost_bp'])

    # ==================================================================
    # SUMMARY.json
    # ==================================================================
    summary = {
        'hypothesis_id': g(R, 'hypothesis_id'),
        'version': g(R, 'version'),
        'spec_sha256': g(R, 'spec_sha256'),
        'panel': panel,
        'pool': pool,
        'duplication': dup,
        'funnel': funnel,
        'comp_sig_factors': sig_ids,
        'factor_table': [{k: (None if (isinstance(r.get(k), float)
                                       and not np.isfinite(r.get(k)))
                              else r.get(k)) for k in
                          ('id', 'name', 'dir', 'ic_is', 't_is', 'ic_val',
                           't_val', 'ic_oos', 'ic_dd', 'same3', 'dir_ok',
                           'sig_is', 'strong', 'verdict', 'n_pass', 'flags')}
                         for r in fac_tab],
        'group_verdicts': {q: {'name': v[0], 'factors': list(v[1]),
                               'verdict': v[2]} for q, v in grp.items()},
        'composites': {'COMP_ALL': {'verdict': verdict_all,
                                    'n_pass': n_pass_all,
                                    'n_bits': n_bits_all,
                                    'flags': v_all.get('flags', []),
                                    'bits': v_all.get('bits', {}),
                                    'ic_is': ic_ca_is, 'ic_valid': ic_ca_va,
                                    'ic_oos': ic_ca_oo,
                                    'ic_best_single_factor_IS': best_fac},
                       'COMP_SIG': {'verdict': str(v_sig.get('verdict', 'NA')),
                                    'n_pass': int(num(v_sig.get('n_pass'), 0)),
                                    'flags': v_sig.get('flags', []),
                                    'bits': v_sig.get('bits', {}),
                                    'n_factors': len(sig_ids)}},
        'quantile_COMP_ALL_h5': {str(q): {k: (None if (isinstance(v, float)
                                                       and not np.isfinite(v))
                                              else v)
                                          for k, v in q_comp[q].items()}
                                  for q in q_comp},
        'spread_Q5_Q1': {k: (None if (isinstance(v, float)
                                      and not np.isfinite(v)) else v)
                         for k, v in q_spread.items()},
        'cost_Q5_net': {str(bp): cost_q5[bp] for bp in P2['cost_bp']},
        'incremental_summary': inc_sum,
        'null': nulls,
        'oos': g(R, 'oos', []),
        'parameter': param,
        'subgroup': subgroup,
        'bias_audit': audit,
        'trading_authorization': g(R, 'trading_authorization'),
        'answers': {
            'Q1_continuous_factor': A1,
            'Q2_single_factor_ic': A2,
            'Q3_volume_contraction': grp['Q3'][2],
            'Q4_volatility_contraction': grp['Q4'][2],
            'Q5_ma_structure': grp['Q5'][2],
            'Q6_relative_sector_strength': grp['Q6'][2],
            'Q7_right_bottom_quality': grp['Q7'][2],
            'Q8_comp_vs_momentum': A3,
            'Q9_incremental_over_momentum': {
                'beta_t_M1_y5': beta_t_m1, 'dR2_M1_M0_y5': dr2_m1,
                'dR2_M3_M1_y5': dr2_m31,
                'significant': bool(np.isfinite(beta_t_m1)
                                    and abs(beta_t_m1) >= P2['T_MIN'])},
            'Q10_regime_stability': {
                'stable_layers': [{'dimension': r['dimension'],
                                   'layer': r['layer']} for r in stable_layers],
                'failing_layers': [{'dimension': r['dimension'],
                                    'layer': r['layer']} for r in fail_layers]},
            'cost_positive_bp_Q5': cost_pos,
            'OOS_pass': oos_pass,
            'param_stable_pass': param_pass,
            'year_consistency_pass': year_pass,
            'final_state': final_state},
        'headline': {'U_FACTOR_cells': n_uf, 'unique_episodes': n_ep,
                     'READY_share': ready_share, 'FAILED_share': fail_share,
                     'COMP_ALL_IS_IC': ic_ca_is, 'COMP_ALL_OOS_IC': ic_ca_oo,
                     'COMP_ALL_verdict': verdict_all,
                     'COMP_ALL_n_pass': n_pass_all,
                     'null_N1_p': n1p,
                     'positive_ratio_COMP_ALL': pr_a,
                     'positive_ratio_COMP_SIG': pr_c,
                     'n_factors_three_phase_same_sign': n_same3,
                     'n_factors_strong': n_strong},
    }
    S.save_json(summary, 'H_SLG_02_SUMMARY.json')
    LOG('SUMMARY.json written')

    # ==================================================================
    # report
    # ==================================================================
    L = []
    A = L.append

    A('# H-SLG-02 — A股主升后「整理完成度」连续因子横截面预测力研究报告')
    A('')
    A('```text')
    A('第一波主升 → 高位整理 → 整理完成度(连续因子) → T+3/5/10/20 横截面超额收益')
    A('```')
    A('')
    A('- Research ID：`%s`　SPEC：`%s` v%s'
      % (g(R, 'hypothesis_id'), str(g(R, 'spec_sha256', ''))[:16],
         g(R, 'version')))
    A('- 面板：`S=%s × N=%s`　`%s .. %s`（burn-in=%s sessions）'
      % (panel.get('S'), panel.get('N'), panel.get('start'), panel.get('end'),
         panel.get('s0')))
    A('- 时间分段：`IS=2020..2024`　`VALID=2025`　`OOS=2026`　'
      '主口径 `h* = T+%d`，`trading_authorization = %s`'
      % (HSTAR, g(R, 'trading_authorization')))
    A('')
    A('> %s' % DISCLAIMER)
    A('')

    # ---- §1 data & discipline -----------------------------------------
    A('## §1　数据与纪律（冻结前置）')
    A('')
    A('仅使用现有 Tushare 本地缓存（`hve_common.build_grid` 日线面板 / '
      '`basic_panel` 市值与换手 / `index_panel` 000300.SH / PIT 申万一级 / '
      '`calendar`）。未引入 TDX / AkShare / Wind / 同花顺 / 未来数据 / 人工选样；'
      '结构定义原样复用 `slg_common`（只读）。')
    A('')
    A('`future_column_scan`（SPEC §21，任一失败即 `EXIT 3`）：')
    A('')
    A(table([{'检查': k, '结果': 'PASS' if v[0] else 'FAIL', '说明': v[1]}
             for k, v in scan.items()], ['检查', '结果', '说明']))
    A('')
    A('> 说明：`truncation_invariance` 的对照重建使用与主状态**完全相同**的冻结窗口'
      '（`LEG_W=%d / CONS_MAX=%d`），否则两侧不可比。'
      % (P['LEG_W'], P['CONS_MAX']))
    A('')

    # ---- §2 causal structure ------------------------------------------
    A('## §2　因果结构与因子池（只用 t 及之前的数据）')
    A('')
    A('`peak_off = argmax(high[t-%d..t-%d])` → `leg_start = argmin(low[peak-%d..peak])` '
      '→ `bounce = argmax(high[peak+1..t])` → 左底 / 右底均取自 `[.., t]`。'
      '所有结构量以 `t` 为基准，故 `candidate_date = confirmation_date = '
      'event_date = label_start`，不使用任何未来 pivot 确认。'
      % (P['PEAK_HI'], P['PEAK_LO'], P['LEG_W']))
    A('')
    A('因子池按 SPEC §6 定义为 `U_FACTOR = CTX & R1`（**禁止**用 `READY` / `FAILED` '
      '筛选，避免用结果定义样本）：')
    A('')
    A(table([{'阶段': r.get('stage'), '单元格': r.get('n_cells'),
              '独立整合理期': r.get('n_episodes'), 'PRE': r.get('PRE'),
              'IS': r.get('IS'), 'VALID': r.get('VALID'), 'OOS': r.get('OOS')}
             for r in funnel],
            ['阶段', '单元格', '独立整合理期', 'PRE', 'IS', 'VALID', 'OOS']))
    A('')
    A('- `U_FACTOR` 单元格 = `%s`；唯一整合理期 = `%s`；'
      '平均每期出现次数 = `%s`；平均每日截面只数 = `%s`。'
      % (f(dup.get('U_FACTOR_cells'), 0), f(dup.get('unique_episodes'), 0),
         f(dup.get('avg_occurrences_per_episode'), 2),
         f(dup.get('avg_daily_cross_section'), 1)))
    A('- `READY` 占 `U_FACTOR` 比例 = `%s`（%s 单元格）；'
      '`FAILED` 占 `U_FACTOR` 比例 = `%s`（%s 单元格）。'
      % (pc(ready_share), f(dup.get('READY_cells'), 0), pc(fail_share),
         f(dup.get('FAILED_cells'), 0)))
    A('- 分段覆盖：IS `%s` 单元格 / `%s` 日；VALID `%s` / `%s` 日；'
      'OOS `%s` / `%s` 日。'
      % (f(dup.get('IS_cells'), 0), f(dup.get('IS_days'), 0),
         f(dup.get('VALID_cells'), 0), f(dup.get('VALID_days'), 0),
         f(dup.get('OOS_cells'), 0), f(dup.get('OOS_days'), 0)))
    A('')
    A('> 与 `H-SLG-01` 的 71 个 A 组事件相比，本口径以 `U_FACTOR` 全池（约 %s 个'
      '单元格）做横截面回归，事件量提升约三个数量级；`READY` 二值状态仅占 %s，'
      '这正是把「完成度」连续化的动机。'
      % (f(n_uf, 0), pc(ready_share)))
    A('')

    # ---- §3 single factors --------------------------------------------
    A('## §3　单因子横截面预测力（SPEC §7 / §8，`P1..P16` 全部报告）')
    A('')
    A('主口径：每日 `Spearman(rank(factor), rank(y_ex_sec))`，`h* = T+%d`；'
      '门槛 `|IC_mean| >= %s` 且 `|t_NW| >= %s`。方向列 `预期` 为 SPEC §7 冻结方向。'
      % (HSTAR, P2['IC_MIN'], P2['T_MIN']))
    A('')
    A(table([{'因子': r['id'], '名称': r['name'], '预期': r['dir'],
              'IS IC': f(r['ic_is'], 5), 'IS t': f(r['t_is'], 2),
              'VALID IC': f(r['ic_val'], 5), 'VALID t': f(r['t_val'], 2),
              'OOS IC': f(r['ic_oos'], 5), 'R-DEDUP IC': f(r['ic_dd'], 5),
              '三段同号': 'Y' if r['same3'] else 'N',
              '判决': r['verdict'], '位': r['n_pass']}
             for r in fac_tab],
            ['因子', '名称', '预期', 'IS IC', 'IS t', 'VALID IC', 'VALID t',
             'OOS IC', 'R-DEDUP IC', '三段同号', '判决', '位']))
    A('')
    A('- 三段（IS/VALID/OOS）同号因子：`%s`。'
      % ('、'.join(same3_list) if same3_list else '无'))
    A('- 同时满足「方向符合预注册 + IS 显著 + 三段同号」的强因子：`%s`（共 %d 个）。'
      % ('、'.join(strong_list) if strong_list else '无', n_strong))
    A('- 单因子失败标记：`SIGN_CONFLICT`（实测方向与 §7 相反）、'
      '`UNSTABLE`（IS 显著但 VALID/OOS 反号）、`EPISODE_SENSITIVE`'
      '（R-ALL 与 R-DEDUP 不一致）逐因子列于上表 `判决` 之外，'
      '完整 flag 见 `H_SLG_02_SUMMARY.json` 的 `factor_table`。')
    A('')
    A('> `P15 ready_count` / `P16 ready_dummy` 是**计数对照**，不进入 `COMP`；'
      '它们的作用是检验「连续完成度」是否只是把 `READY` 换个写法。')
    A('')

    # ---- §4 quantiles & cost ------------------------------------------
    A('## §4　分位收益与成本（SPEC §10）')
    A('')
    A('`COMP_ALL` 在 `h* = T+%d` 的 Q1..Q%d（按因子 rank 分位）：'
      % (HSTAR, int(P2['Q_GROUPS'])))
    A('')
    A(table([{'分位': 'Q%d' % q, 'n': q_comp[q].get('n'),
              'gross': f(q_comp[q].get('gross'), 5),
              'net30': f(q_comp[q].get('net30'), 5),
              'win30': f(q_comp[q].get('win30'), 3),
              'median': f(q_comp[q].get('median'), 5),
              'maxdd': f(q_comp[q].get('maxdd'), 4),
              'exsec': f(q_comp[q].get('exsec'), 5)}
             for q in sorted(q_comp)],
            ['分位', 'n', 'gross', 'net30', 'win30', 'median', 'maxdd', 'exsec']))
    A('')
    A('- 分层单调性（分位均值 vs 分位序号 Spearman）= `%s`；'
      '门槛 `>= %s`。Q%d−Q1 spread = `%s`（gross；成本为常数扣除，'
      '故净 spread 与之相等）。'
      % (f(mono_comp, 3),
         P2['MONO_MIN'], int(P2['Q_GROUPS']), f(num(q_spread.get('spread_gross')), 5)))
    A('- 成本后 Q5 绝对净收益：%s。30bp 下 Q5−Q1 > 0：`%s`。'
      % (cost_txt, 'Y' if spread_net30 > 0 else 'N'))
    A('')

    # ---- §5 composites ------------------------------------------------
    A('## §5　等权合成因子 `COMP`（SPEC §11）')
    A('')
    A('构造公式（**逐字**，等权，权重禁止优化）：')
    A('')
    A('```text')
    A('COMP_ALL = mean( P1..P14 按 SPEC §7 预注册方向取号后 rank 标准化 )')
    A('COMP_SIG = mean( 仅 IS 段满足 |IC|>=%s 且 |t_NW|>=%s 的因子 )'
      % (P2['IC_MIN'], P2['T_MIN']))
    A('```')
    A('')
    A('- `COMP_ALL`：IS IC=`%s`（t=%s）、VALID IC=`%s`、OOS IC=`%s`；'
      '判决 **%s**（%d/%d 位），flags=`%s`。'
      % (f(ic_ca_is, 5), f(get(comp_df, asset='COMP_ALL', h=HSTAR,
                              book='R-ALL', target='sec',
                              scope='IS').get('t_nw'), 2),
         f(ic_ca_va, 5), f(ic_ca_oo, 5), verdict_all, n_pass_all, n_bits_all,
         '、'.join(v_all.get('flags', [])) or '-'))
    A('- `COMP_SIG`：IS 段筛选出 %d 个因子（`%s`）；判决 **%s**（%s 位）。'
      % (len(sig_ids), '、'.join(sig_ids) if sig_ids else '无',
         str(v_sig.get('verdict', 'NA')),
         str(v_sig.get('n_pass', 'NA'))))
    A('- 最强单因子 |IS IC| = `%s`；`COMP_ALL` |IS IC| = `%s`。'
      % (f(best_fac, 5), f(abs(ic_ca_is) if np.isfinite(ic_ca_is) else np.nan, 5)))
    A('')

    # ---- §6 incremental -----------------------------------------------
    A('## §6　增量信息检验（SPEC §12，研究核心）')
    A('')
    A('每日横截面 OLS，因变量 `rank(y_ex_sec)`；`M0 = rank(MOM20)+rank(MOM60)'
      '+rank(RS_sec)+rank(vol_ratio_20)+rank(ln_size)`，`M1 = M0+rank(COMP_ALL)`，'
      '`M2 = M0+rank(COMP_ALL)+rank(COMP_SIG)`，`M3 = M0+rank(P1..P14)`。')
    A('')
    for y in ('y5', 'y10'):
        rr = [r for r in (inc_df.to_dict('records') if not inc_df.empty else [])
              if r.get('y') == y]
        A('### 因变量 `%s`' % ('T+5' if y == 'y5' else 'T+10'))
        A('')
        A(table([{'model': r.get('model'), '加了什么': r.get('comp_added') or '-',
                  'n_days': r.get('n_days'), 'R2': f(r.get('R2'), 5),
                  'ΔR2': f(r.get('dR2'), 6),
                  'β(新增)': f(r.get('beta_comp'), 5),
                  't_NW(β)': f(r.get('t_nw_beta'), 2),
                  'β>0占比': f(r.get('pos_ratio_beta'), 3),
                  'OOS_R2': f(r.get('OOS_R2'), 5),
                  'OOS ΔR2': f(r.get('OOS_dR2'), 6),
                  'OOS rankIC': f(r.get('OOS_rankIC'), 4)}
                 for r in rr],
                ['model', '加了什么', 'n_days', 'R2', 'ΔR2', 'β(新增)',
                 't_NW(β)', 'β>0占比', 'OOS_R2', 'OOS ΔR2', 'OOS rankIC']))
        A('')
    A('- **核心比较 M1 − M0**（控制动量后 `COMP_ALL` 是否仍有增量）：'
      '`β t_NW(T+5) = %s`，`ΔR²(T+5) = %s`。'
      % (f(beta_t_m1, 2), f(dr2_m1, 6)))
    A('- **M3 − M1**（把因子拆开 vs 等权合成）：`ΔR²(T+5) = %s`。'
      % f(dr2_m31, 6))
    A('- OOS 评估按 SPEC §12.5：用 IS 段系数外推至 VALID/OOS 段，见上表 '
      '`OOS_R2 / OOS ΔR2 / OOS rankIC`。')
    A('')

    # ---- §7 null ------------------------------------------------------
    A('## §7　Null Model 随机对照（SPEC §13）')
    A('')
    A(table([{'资产': r.get('asset'), '模型': r.get('model'),
              '观测': f(r.get('obs'), 5),
              'Null 均值': f(r.get('null_mean'), 5),
              '95% Null 区间': '[%s, %s]' % (f(r.get('null_lo'), 5),
                                             f(r.get('null_hi'), 5)),
              'p(单边)': f(r.get('p_one_sided'), 4),
              '覆盖率': f(r.get('resolution'), 3),
              '有效日': r.get('n_days_valid')}
             for r in nulls
             if r.get('asset') in ('COMP_ALL', 'COMP_SIG')],
            ['资产', '模型', '观测', 'Null 均值', '95% Null 区间', 'p(单边)',
             '覆盖率', '有效日']))
    A('')
    A('> `N1`（主判定）在**同日横截面内**循环移位 `COMP`，保持当日分布与日期不变；'
      '`N2` 在个股内时序重排；`N3` 每日随机取一个 `P1..P14` 作对照因子。'
      '`B = %s`，`rounds = %s`，`min_resolution >= %s`。'
      '`COMP_ALL` 的 `N1 p = %s`。'
      % (P2['null_B'], P2['null_rounds'], P2['null_min_resolution'],
         f(n1p, 4)))
    A('')
    A('单因子 `N1`（方向对齐后单边 p，`g9` 位）逐因子见 '
      '`second_leg2_factor_null.csv`（`asset=P1..P16`）。')
    A('')

    # ---- §8 oos / walk-forward ----------------------------------------
    A('## §8　时间分段 / OOS / Walk-Forward（SPEC §14）')
    A('')
    A(table([{'资产': r.get('asset'), '段/年': r.get('bucket'),
              'n_cells': r.get('n_cells'), 'n_days': r.get('n_days'),
              'IC': f(r.get('IC'), 5), 't_NW': f(r.get('t_NW'), 2),
              'Q5−Q1 net30': f(r.get('net30_Q5Q1'), 5)}
             for r in (g(R, 'oos', []) or [])],
            ['资产', '段/年', 'n_cells', 'n_days', 'IC', 't_NW', 'Q5−Q1 net30']))
    A('')
    A('- 年度稳定性（`COMP_ALL` 逐年 IC 与 IS 同号占比）= `%s`（门槛 0.60）→ **%s**。'
      % (f(pos_share, 3), '通过' if year_pass else '未通过'))
    A('')

    # ---- §9 parameter stability ---------------------------------------
    A('## §9　参数稳定性（SPEC §15，禁止寻找单一最优参数）')
    A('')
    A('- 格点设计：基线 + 单轴扰动（star），共 `%s` 格；'
      '轴为 `leg_w / consol_max / retrace_min / first_rise_pct / q_groups`。'
      % param.get('n_cells'))
    A('- `positive_ratio`（格点中与基线同号且 `|IC| >= %s` 的比例）：'
      '`COMP_ALL = %s`（%s）；`COMP_SIG = %s`（%s）。'
      % (P2['PARAM_IC_FLOOR'], f(pr_a, 3), tag_a, f(pr_c, 3), tag_c))
    A('- 判定：`>= %s → stable`，`<= %s → FRAGILE`。'
      % (P2['param_stable_thr'], P2['param_fragile_thr']))
    A('- 每格 `COMP_SIG` 的因子筛选与方向**在该格内部独立**完成（SPEC §15.6）。')
    A('')
    A(table([{'idx': r.get('idx'), '轴': r.get('axis'),
              'leg_w': r.get('leg_w'), 'consol_max': r.get('consol_max'),
              'retrace_min': r.get('retrace_min'),
              'first_rise_pct': r.get('first_rise_pct'),
              'q_groups': r.get('q_groups'), 'cells': r.get('n_cells'),
              'COMP_ALL IC': f(r.get('COMP_ALL_IC'), 5),
              'COMP_SIG IC': f(r.get('COMP_SIG_IC'), 5),
              'COMP_SIG n': r.get('COMP_SIG_n')}
             for r in (par_df.to_dict('records') if not par_df.empty else [])],
            ['idx', '轴', 'leg_w', 'consol_max', 'retrace_min', 'first_rise_pct',
             'q_groups', 'cells', 'COMP_ALL IC', 'COMP_SIG IC', 'COMP_SIG n']))
    A('')

    # ---- §10 subgroups ------------------------------------------------
    A('## §10　分层条件 IC（SPEC §16）')
    A('')
    A(table([{'资产': r.get('asset'), '维度': r.get('dimension'),
              '分层': r.get('layer'), 'n_cells': r.get('n_cells'),
              'n_days': r.get('n_days'), 'IC(ALL)': f(r.get('IC'), 5),
              't_NW': f(r.get('t_NW'), 2), 'IS': f(r.get('IC_IS'), 5),
              'VALID': f(r.get('IC_VALID'), 5), 'OOS': f(r.get('IC_OOS'), 5)}
             for r in subgroup],
            ['资产', '维度', '分层', 'n_cells', 'n_days', 'IC(ALL)', 't_NW',
             'IS', 'VALID', 'OOS']))
    A('')
    A('> 目标是在分层中识别**稳定有效区域**，而非 IC 最高的某一层。'
      '样本数不足（`n_cells < %d`）的层已被主运行跳过。' % P2['MIN_XS_N'])
    A('')

    # ---- §11 verdicts -------------------------------------------------
    A('## §11　判决位（SPEC §18）')
    A('')
    for tag, v in (('COMP_ALL', v_all), ('COMP_SIG', v_sig)):
        if not v:
            continue
        A('**%s**　通过 `%s/%s`　→　**%s**　flags=`%s`'
          % (tag, v.get('n_pass'), v.get('n_bits'), v.get('verdict'),
             '、'.join(v.get('flags', [])) or '-'))
        A('')
        A(table([{'位': k, '结果': 'PASS' if b else 'FAIL'}
                 for k, b in sorted((v.get('bits', {}) or {}).items())],
                ['位', '结果']))
        A('')
    A('单因子判决（`P1..P14`，前 10 位 + `g10b` 替代 `g11/g12`）：')
    A('')
    A(table([{'因子': r['id'], '判决': r['verdict'], '位': r['n_pass'],
              'IS IC': f(r['ic_is'], 5), 'VALID IC': f(r['ic_val'], 5),
              'OOS IC': f(r['ic_oos'], 5), 'R-DEDUP IC': f(r['ic_dd'], 5),
              '单调性': f(r['mono'], 3), 'Q5−Q1': f(r['spr'], 5),
              'flags': r['flags']}
             for r in fac_tab],
            ['因子', '判决', '位', 'IS IC', 'VALID IC', 'OOS IC', 'R-DEDUP IC',
             '单调性', 'Q5−Q1', 'flags']))
    A('')
    A('Bias audit：')
    A('')
    A(table([{'项': a.get('item'), '状态': a.get('status'),
              '说明': a.get('detail')} for a in audit], ['项', '状态', '说明']))
    A('')

    # ---- §12 ten questions --------------------------------------------
    A('## §12　十个必答问题（SPEC §19）')
    A('')
    A('**Q1　整理完成度能否被量化为连续因子？（覆盖度、分布、重复度）**　'
      '能。`U_FACTOR = CTX & R1` 的 %s 个单元格上构造了 14 个连续因子'
      '（P1..P14）+ 2 个 `READY` 计数对照（P15/P16）；'
      '唯一整合理期 %s 个，平均每期出现 %s 次，平均每日截面 %s 只。'
      '二值 `READY` 仅占 %s，连续化确实保留了被二值化丢掉的信息。→ **%s**'
      % (f(n_uf, 0), f(n_ep, 0), f(dup.get('avg_occurrences_per_episode'), 2),
         f(dup.get('avg_daily_cross_section'), 1), pc(ready_share), A1))
    A('')
    A('**Q2　哪些单因子在 IS/VALID/OOS 三段同号且显著？**　'
      '三段同号者共 %d 个：`%s`；其中同时满足预注册方向与 IS 显著（`|t_NW| >= %s`）'
      '的强因子 %d 个：`%s`。→ **%s**'
      % (n_same3, '、'.join(same3_list) if same3_list else '无', P2['T_MIN'],
         n_strong, '、'.join(strong_list) if strong_list else '无', A2))
    A('')
    A('**Q3　量能收缩因子是否提供增量信息？（是否只是"缩量=卖压减少"的错觉）**　'
      '`P1 vol_contract_rb / P2 vol_contract_consol / P3 vol_ratio_5_leg` → **%s**。'
      '边界：本研究**不**把"缩量"解释为"卖压衰减"，只检验其横截面排序信息；'
      '缩量可能同时是流动性下降或关注度下降的代理。'
      % grp['Q3'][2])
    A('')
    A('**Q4　波动收敛因子是否提供增量信息？**　'
      '`P4 atr_ratio`（整理末期 5 日 ATR/Close）→ **%s**。'
      % grp['Q4'][2])
    A('')
    A('**Q5　MA20/MA60 结构因子是否提供增量信息？**　'
      '`P5..P9`（`ma20_pos / ma20_slope / ma20_hold / ma60_pos / ma60_slope`）'
      '→ **%s**。注意这些量与动量基准 `MOM20/MOM60` 存在构造上的重叠，'
      '其"增量"必须读 §6 的 `M1 − M0`，而不是单因子 IC。'
      % grp['Q5'][2])
    A('')
    A('**Q6　相对行业强度因子是否提供增量信息？**　'
      '`P14 rs_sector`（`rs_sector_20`）→ **%s**；该量亦进入 `M0` 作控制变量。'
      % grp['Q6'][2])
    A('')
    A('**Q7　右底质量 / 接近阻力因子是否提供增量信息？**　'
      '`P10 rb_quality / P11 rb_above_ma20 / P12 near_neckline / P13 rb_hold` '
      '→ **%s**。' % grp['Q7'][2])
    A('')
    A('**Q8　等权合成 `COMP` 是否优于最强单因子与动量基准？**　'
      '`COMP_ALL` IS IC=`%s`，最强单因子 |IS IC|=`%s`；'
      '控制动量后 `β t_NW(M1,T+5)=%s`，`ΔR²(M1−M0,T+5)=%s`。→ **%s**'
      % (f(ic_ca_is, 5), f(best_fac, 5), f(beta_t_m1, 2), f(dr2_m1, 6), A3))
    A('')
    A('**Q9　控制动量后 `COMP` 的增量 IC / ΔR² 是多少？是否显著？**　'
      '`M1 − M0`：`ΔR²(T+5)=%s`，`β(COMP_ALL)` 的 `t_NW=%s`（门槛 %s）；'
      '`M3 − M1`：`ΔR²(T+5)=%s`（拆开全部因子 vs 等权合成）。'
      '判定：**%s**。'
      % (f(dr2_m1, 6), f(beta_t_m1, 2), P2['T_MIN'], f(dr2_m31, 6),
         '显著' if (np.isfinite(beta_t_m1) and abs(beta_t_m1) >= P2['T_MIN'])
         else '不显著'))
    A('')
    A('**Q10　`COMP` 的预测力在哪些分层与市场状态下稳定，在哪些环境下失效？**　'
      '三段同号分层：%s。'
      '失效分层（IS 与 OOS 反号）：%s。'
      '三态市场 IC（`IC_IS/VALID/OOS`）见 §10「市场」行。'
      % (stable_txt, fail_txt))
    A('')

    # ---- §13 conclusion (verbatim SPEC §20) ---------------------------
    A('## §13　最终结论')
    A('')
    A('```text')
    A('【研究结论】')
    A('')
    A('1. 整理完成度可否量化为连续因子：   %s' % A1)
    A('2. 单因子横截面预测力：             %s' % A2)
    A('3. 合成因子 COMP 相对动量基准：     %s' % A3)
    A('4. 最稳定的因子与区间：             %s' % (
        ('因子：' + ('、'.join(strong_list) if strong_list else '无')
         + '；分层：' + stable_txt)))
    A('5. 主要失效环境：                   %s' % fail_txt)
    A('6. 成本后：                         %s；30bp 下 Q5−Q1 spread = %s（%s）'
      % (cost_txt, f(num(q_spread.get('spread_net30')), 5),
         '成立' if spread_net30 > 0 else '不成立'))
    A('7. OOS：                            %s（COMP_ALL OOS IC = %s，IS IC = %s）'
      % ('通过' if oos_pass else '未通过', f(ic_ca_oo, 5), f(ic_ca_is, 5)))
    A('8. 参数稳定性：                     %s（positive_ratio = %s，%s）'
      % ('通过' if param_pass else '未通过', f(pr_a, 3), tag_a))
    A('9. 年度稳定性：                     %s（逐年与 IS 同号占比 = %s）'
      % ('通过' if year_pass else '未通过', f(pos_share, 3)))
    A('10. 最终状态：                      %s' % final_state)
    A('```')
    A('')
    A('```text')
    A('因子本身有效 vs 只是 Momentum / Volume 代理：')
    A('  COMP_ALL   IS IC = %-10s  VALID IC = %-10s  OOS IC = %-10s'
      % (f(ic_ca_is, 5), f(ic_ca_va, 5), f(ic_ca_oo, 5)))
    A('  控制动量后 beta t_NW(M1,T+5) = %-8s  dR2(M1-M0,T+5) = %s'
      % (f(beta_t_m1, 2), f(dr2_m1, 6)))
    A('  与最强单因子比较：|IC|_COMP_ALL = %-8s  |IC|_best_factor = %s'
      % (f(abs(ic_ca_is) if np.isfinite(ic_ca_is) else np.nan, 5),
         f(best_fac, 5)))
    A('  判决位：COMP_ALL %s/%s -> %s ；COMP_SIG %s/%s -> %s'
      % (n_pass_all, n_bits_all, verdict_all, v_sig.get('n_pass', 'NA'),
         v_sig.get('n_bits', 'NA'), v_sig.get('verdict', 'NA')))
    A('  市场状态 IC(ALL)：%s'
      % ('；'.join('%s=%s' % (r['layer'], f(r['IC'], 4))
                  for r in subgroup
                  if r.get('asset') == 'COMP_ALL'
                  and r.get('dimension') == '市场') or 'NA'))
    A('```')
    A('')
    A('> `trading_authorization = NO`（无论结论如何）。'
      'Entry / Exit / Position sizing / Execution 属独立第二阶段（SPEC §22.10）。')
    A('')

    # ---- §14 artifacts -------------------------------------------------
    A('## §14　产物清单（SPEC §21 / FREEZE §8）')
    A('')
    for name in REQUIRED + EXTRA:
        if name == 'H_SLG_02_REPORT.md':
            A('- [x] `%s`（本文件，由本次报告生成）' % name)
            continue
        p = os.path.join(OUT, name)
        ok = os.path.exists(p) and os.path.getsize(p) > 0
        A('- [%s] `%s`（%s bytes）'
          % ('x' if ok else ' ', name,
             format(os.path.getsize(p), ',') if os.path.exists(p) else 'MISSING'))
    A('')

    rep = os.path.join(OUT, 'H_SLG_02_REPORT.md')
    with open(rep, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')
    LOG('H_SLG_02_REPORT.md written (%d lines)' % len(L))

    # ------------------------------------------------- delivery check
    LOG.sep()
    LOG('delivery check')
    bad = []
    for name in REQUIRED:
        p = os.path.join(OUT, name)
        if not os.path.exists(p) or os.path.getsize(p) == 0:
            bad.append(name)
        else:
            LOG('  OK   %-38s %12s bytes'
                % (name, format(os.path.getsize(p), ',')))
    for name in EXTRA:
        p = os.path.join(OUT, name)
        if not os.path.exists(p) or os.path.getsize(p) == 0:
            LOG('  MISS %-38s (optional)' % name)
    LOG.sep()
    LOG('answers: Q1=%s Q2=%s Q3=%s Q8=%s | OOS=%s param=%s year=%s'
        % (A1, A2, grp['Q3'][2], A3, oos_pass, param_pass, year_pass))
    LOG('verdict: COMP_ALL=%s COMP_SIG=%s   final: %s   elapsed %.1fs'
        % (verdict_all, str(v_sig.get('verdict', 'NA')), final_state,
           time.time() - t0))
    if bad:
        LOG('DELIVERY FAILED -- missing: %s' % ', '.join(bad))
        return 1
    LOG('EXIT 0 -- %d required artefacts present and non-empty' % len(REQUIRED))
    return 0


if __name__ == '__main__':
    sys.exit(main())
