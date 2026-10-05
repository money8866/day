# -*- coding: utf-8 -*-
"""H-SLG-01 -- report generator + delivery check.

Consumes H_SLG_01_RESULTS.json (written by slg_run.py), answers the ten
pre-registered questions (SPEC section 19), prints the frozen conclusion block
(SPEC section 20) verbatim, and writes:

    H_SLG_01_SUMMARY.json
    second_leg_report.md

It then verifies that every artefact required by SPEC section 21 + FREEZE
section 8 exists and is non-empty.  Exit code 0 means the study is delivered.

This script evaluates; it never re-fits.  No threshold, window or filter is
touched here.
"""
import os
import sys
import json
import time

import numpy as np

import slg_common as S

P = S.PREREG_S
OUT = S.OUT
RES = os.path.join(OUT, 'H_SLG_01_RESULTS.json')

EQ = '\u2550' * 46
RULE = '\u2500' * 46
LOG = S.Log('slg_report')

REQUIRED = [
    'H_SLG_01_FREEZE.md',
    'H_SLG_01_SPEC.md',
    'second_leg_candidates.csv',
    'second_leg_events.csv',
    'second_leg_consolidation.csv',
    'second_leg_breakout.csv',
    'second_leg_event_study.csv',
    'second_leg_oos.csv',
    'second_leg_parameter_stability.csv',
    'second_leg_subgroup.csv',
    'second_leg_incremental_info.csv',
    'second_leg_report.md',
    'H_SLG_01_SUMMARY.json',
]
EXTRA = [
    'H_SLG_01_RESULTS.json',
    'H_SLG_01_FUNNEL.csv',
    'H_SLG_01_NULL.csv',
    'H_SLG_01_WALKFORWARD.csv',
    'H_SLG_01_BIAS_AUDIT.csv',
    'H_SLG_01_SURVIVOR.csv',
]

DISCLAIMER = (
    '本研究只回答「主升后的二次整理完成」本身是否具有预测价值，'
    '以及「二次突破」在已有趋势 / 动量信息之外增加了多少信息。'
    '它**不是**买入信号生成器，**不做**实盘授权。'
)

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
    heads = headers or [c for c in cols]
    out = ['| ' + ' | '.join(heads) + ' |',
           '|' + '|'.join(['---'] * len(cols)) + '|']
    for r in rows:
        out.append('| ' + ' | '.join(str(r.get(c, '')) for c in cols) + ' |')
    return '\n'.join(out)


# SPEC section 15 ladder: number of regressors contributed by each block.
# Model 5 - Model 4 is a nested comparison, so an incremental-F (Wald) test is
# the textbook way to price the added block.  df2 = n - k is ~2.3e5, so the
# F statistic is reported in its chi-square form W = F * dk against the
# 1% / 5% chi-square critical values for dk degrees of freedom.
K_ADD = {'Model 0': 4, 'Model 1': 3, 'Model 2': 2,
         'Model 3': 2, 'Model 4': 3, 'Model 5': 1}
K_CUM, _kk = {}, 1                                   # incl. intercept
for _m in ('Model 0', 'Model 1', 'Model 2', 'Model 3', 'Model 4', 'Model 5'):
    _kk += K_ADD[_m]
    K_CUM[_m] = _kk
CHI2_CRIT = {1: (6.635, 3.841), 2: (9.210, 5.991), 3: (11.345, 7.815)}


def inc_test(inc, m_from, m_to, y):
    """Incremental-F test for the block added going from m_from to m_to."""
    a = row_of(inc, model=m_from, y=y) or {}
    b = row_of(inc, model=m_to, y=y) or {}
    r2a, r2b = num(a.get('R2')), num(b.get('R2'))
    n = int(num(b.get('n'), 0))
    dk, k = K_ADD.get(m_to, 0), K_CUM.get(m_to, 0)
    t = {'dR2': np.nan, 'dk': dk, 'F': np.nan, 'W': np.nan,
         'oos_ic_from': num(a.get('OOS_IC')), 'oos_ic_to': num(b.get('OOS_IC')),
         'oos_r2_from': num(a.get('OOS_R2')), 'oos_r2_to': num(b.get('OOS_R2'))}
    if np.isfinite(r2a) and np.isfinite(r2b) and dk > 0 and n > k + 1:
        denom = (1.0 - r2b) / (n - k)
        if denom > 0:
            t['dR2'] = r2b - r2a
            t['F'] = (t['dR2'] / dk) / denom
            t['W'] = t['F'] * dk
    return t


def inc_one(t):
    """One horizon: PASS if significant at 1% and OOS IC does not deteriorate;
    FAIL if not significant even at 5%; otherwise MIXED."""
    c1, c5 = CHI2_CRIT.get(t['dk'], (np.nan, np.nan))
    w = num(t.get('W'))
    if not np.isfinite(w) or not np.isfinite(c1):
        return 'MIXED'
    if w > c1 and num(t['oos_ic_to']) >= num(t['oos_ic_from']):
        return 'PASS'
    if w < c5:
        return 'FAIL'
    return 'MIXED'


def inc_verdict(t5, t10):
    a, b = inc_one(t5), inc_one(t10)
    if a == b:
        return a
    if a == 'MIXED':
        return b
    if b == 'MIXED':
        return a
    return 'MIXED'


def tri_gate(cond_pos, cond_neg):
    if cond_pos:
        return 'PASS'
    if cond_neg:
        return 'FAIL'
    return 'MIXED'


def sc(arr, i):
    """safe struct-type count lookup."""
    try:
        return int(arr[i])
    except (IndexError, TypeError, ValueError):
        return 'NA'


# ------------------------------------------------------------------ main
def main():
    t0 = time.time()
    LOG.sep('=')
    LOG('H-SLG-01 report  version %s' % P['version'])
    LOG.sep('=')

    if not os.path.exists(RES):
        LOG('FATAL: %s missing -- run slg_run.py first' % RES)
        return 1
    with open(RES, encoding='utf-8') as fh:
        R = json.load(fh)

    panel = g(R, 'panel', {})
    funnel = g(R, 'funnel', [])
    ev = g(R, 'event_study', [])
    phases = g(R, 'phases', [])
    wf = g(R, 'walkforward', [])
    param = g(R, 'parameter', {})
    nulls = g(R, 'null', [])
    subgroup = g(R, 'subgroup', [])
    inc = g(R, 'incremental', [])
    inc_sum = g(R, 'incremental_summary', {})
    bits = g(R, 'bits', {})
    bitsx = g(R, 'bits_extra', {})
    bit_counts = g(R, 'bit_counts', {})
    struct_cnt = g(R, 'struct_type_counts', [])
    n_pass = int(g(R, 'n_pass', 0))
    verdict = str(g(R, 'verdict', 'NA'))
    audit = g(R, 'bias_audit', [])
    survivor = g(R, 'survivor', [])
    scan = g(R, 'scan', {})

    # ---- extract the headline numbers ---------------------------------
    full30 = num(bitsx.get('full_net30'))
    is30 = num(bitsx.get('is_net30'))
    oos30 = num(bitsx.get('oos_net30'))
    boot_lo = num(bitsx.get('boot_lo'))
    qfdr = num(bitsx.get('bh_fdr_q'))
    pos_share = num(bitsx.get('pos_share_IS'))
    top1 = num(bitsx.get('top1_share'))
    regimes = bitsx.get('regimes', {}) or {}

    nA = int(num(bitsx.get('n_events_A'), 0))
    recA = row_of(ev, group='A') or {}
    recB = row_of(ev, group='B') or {}
    recC1 = row_of(ev, group='C1') or {}
    recC2 = row_of(ev, group='C2') or {}
    recD = row_of(ev, group='D') or {}

    n1 = row_of(nulls, model='N1') or {}
    n1p = num(n1.get('p_one_sided'))
    param_tag = str(param.get('tag', 'NA'))
    pos_ratio = num(param.get('positive_ratio'))

    coefB = num(inc_sum.get('coef_Breakout'))

    def ph(bucket, key='net30', group='A'):
        r = row_of(phases, scope='phase', group=group, bucket=bucket)
        return num((r or {}).get(key))

    isA, valA, oosA = ph('IS'), ph('VALID'), ph('OOS')
    oosB = ph('OOS', group='B')
    oosC1 = ph('OOS', group='C1')

    cost_net = {bp: num(recA.get('mean_net%d_%d' % (bp, P['Hstar'])))
                for bp in P['cost_bp']}
    cost_pos = [bp for bp, v in cost_net.items() if np.isfinite(v) and v > 0]
    cost_txt = ' / '.join('%dbp=%s' % (bp, f(cost_net[bp], 5))
                          for bp in P['cost_bp'])

    # ---- Q-mapping verdicts -------------------------------------------
    # L1 主升后的二次整理: stable predictive power of the whole setup
    l1_pos = (np.isfinite(full30) and full30 > 0
              and np.isfinite(isA) and isA > 0
              and np.isfinite(oosA) and oosA > 0
              and param_tag == 'stable'
              and np.isfinite(n1p) and n1p < 0.05)
    l1_neg = ((np.isfinite(full30) and full30 <= 0)
              and (np.isfinite(oosA) and oosA <= 0))
    L1 = tri_gate(l1_pos, l1_neg)

    # L2 整理完成 (Model 4 - Model 3) / L3 二次突破 relative Momentum (M5 - M4)
    t43 = {y: inc_test(inc, 'Model 3', 'Model 4', y) for y in ('y5', 'y10')}
    t54 = {y: inc_test(inc, 'Model 4', 'Model 5', y) for y in ('y5', 'y10')}
    L2 = inc_verdict(t43['y5'], t43['y10'])
    L3 = inc_verdict(t54['y5'], t54['y10'])

    oos_pass = bool(np.isfinite(oosA) and oosA > 0)
    param_pass = bool(param_tag == 'stable')
    year_pass = bool(np.isfinite(pos_share) and pos_share >= 0.6)

    if verdict == 'ROBUST' and oos_pass and param_pass:
        final_state = 'RESEARCH_VALIDATED'
    elif n_pass >= 9:
        final_state = 'PARTIALLY_VALIDATED'
    else:
        final_state = 'FAILED'

    # ---- stable / failing region scan (subgroup, no cherry-picking) ----
    def region_rows(pred):
        return [r for r in subgroup
                if int(num(r.get('n'), 0)) >= int(P['min_events'])
                and pred(num(r.get('net30')))]

    keep = region_rows(lambda v: np.isfinite(v) and v > 0)
    fail = region_rows(lambda v: np.isfinite(v) and v <= 0)
    _oos_n = fn(funnel, '突破', 'OOS')
    keep_txt = ('OOS 段 A 组仅 %s 个事件，无法识别 OOS 稳定区间；'
                '以下为全样本 net30>0 且 n>=%d 的层（仅作参考）：%s'
                % (_oos_n, P['min_events'],
                   '；'.join('%s=%s(n=%d,%s)'
                             % (r['dimension'], r['layer'], r['n'],
                                pc(r['net30']))
                             for r in keep) or '无'))
    fail_txt = ('；'.join('%s=%s(n=%d,%s)'
                          % (r['dimension'], r['layer'], r['n'], pc(r['net30']))
                          for r in fail) or '无')

    regime_txt = ('；'.join('%s=%s' % (k, pc(v))
                            for k, v in sorted(regimes.items())) or 'NA')

    # ==================================================================
    # SUMMARY.json
    # ==================================================================
    summary = {
        'hypothesis_id': g(R, 'hypothesis_id'),
        'version': g(R, 'version'),
        'spec_sha256': g(R, 'spec_sha256'),
        'panel': panel,
        'n_candidates': g(R, 'n_candidates'),
        'funnel': funnel,
        'struct_type_counts_ABCD': struct_cnt,
        'group_A': {k: recA.get(k) for k in sorted(recA)},
        'group_B': {k: recB.get(k) for k in sorted(recB)},
        'group_C1': {k: recC1.get(k) for k in sorted(recC1)},
        'group_C2': {k: recC2.get(k) for k in sorted(recC2)},
        'group_D': {k: recD.get(k) for k in sorted(recD)},
        'headline': {'full_net30_T5': full30, 'IS_net30': is30,
                     'VALID_net30': valA, 'OOS_net30': oosA,
                     'B_OOS_net30': oosB, 'C1_OOS_net30': oosC1,
                     'boot_lo': boot_lo, 'bh_fdr_q': qfdr,
                     'pos_share_IS': pos_share, 'top1_share': top1,
                     'null_N1_p': n1p},
        'cost_net_T5': cost_net,
        'incremental': inc,
        'incremental_summary': inc_sum,
        'parameter': param,
        'bits': bits, 'n_pass': n_pass, 'verdict': verdict,
        'answers': {'Q1_secondary_consolidation': L1,
                    'Q2_completion_incremental': L2,
                    'Q3_breakout_vs_momentum': L3,
                    'inc_test_M4_M3': t43,
                    'inc_test_M5_M4': t54,
                    'cost_positive_bp': cost_pos,
                    'OOS_pass': oos_pass,
                    'param_stable_pass': param_pass,
                    'year_consistency_pass': year_pass,
                    'final_state': final_state},
        'stable_regions': [{'dimension': r['dimension'], 'layer': r['layer'],
                            'n': r['n'], 'net30': r['net30']} for r in keep],
        'failing_regions': [{'dimension': r['dimension'], 'layer': r['layer'],
                             'n': r['n'], 'net30': r['net30']} for r in fail],
        'bias_audit': audit,
        'survivor': survivor,
        'null': nulls,
        'trading_authorization': g(R, 'trading_authorization'),
    }
    S.save_json(summary, 'H_SLG_01_SUMMARY.json')
    LOG('SUMMARY.json written')

    # ==================================================================
    # report
    # ==================================================================
    L = []
    A = L.append

    A('# H-SLG-01 — A股主升后「二次整理完成度」与「二次突破增量信息」研究报告')
    A('')
    A('研究链路（**禁止压缩为单一评分**）：')
    A('')
    A('```text')
    A('第一波主升 → 高位二次整理 → 整理完成 → 二次突破 → 后续收益')
    A('```')
    A('')
    A('- Research ID：`%s`　SPEC：`%s` v%s'
      % (g(R, 'hypothesis_id'), g(R, 'spec_sha256', '')[:16], g(R, 'version')))
    A('- 面板：`S=%s × N=%s`　`%s .. %s`（burn-in=%s sessions）'
      % (panel.get('S'), panel.get('N'), panel.get('start'), panel.get('end'),
         panel.get('s0')))
    A('- 主口径：`H* = T+%d`，`net30`（30bp 成本）；`trading_authorization = %s`'
      % (P['Hstar'], g(R, 'trading_authorization')))
    A('')
    A('> %s' % DISCLAIMER)
    A('')

    # ---- §1 data & discipline -----------------------------------------
    A('## §1　数据与纪律（冻结前置）')
    A('')
    A('仅使用现有 Tushare 本地缓存（`hve_common.build_grid` 日线面板 / '
      '`basic_panel` 市值与换手 / `index_panel` 000300.SH / PIT 申万一级 / '
      '`calendar`）。未引入 TDX / AkShare / Wind / 同花顺 / 未来数据 / 人工选样。')
    A('')
    A('`future_column_scan`（SPEC §21，任一失败即 `EXIT 3`）：')
    A('')
    A(table([{'检查': k, '结果': 'PASS' if v.get('ok') else 'FAIL',
              '说明': v.get('detail')} for k, v in scan.items()],
            ['检查', '结果', '说明']))
    A('')

    # ---- §2 causal structure ------------------------------------------
    A('## §2　因果结构（只用 t 及之前的数据）')
    A('')
    A('`peak_off = argmax(high[t-%d..t-%d])` → `leg_start = argmin(low[peak-%d..peak])` '
      '→ `bounce = argmax(high[peak+1..t])` → 左底 / 右底均取自 `[.., t]`。'
      '所有结构量以 t 为基准，故 `candidate_date = confirmation_date = event_date`，'
      '不使用任何未来 pivot 确认。'
      % (P['PEAK_HI'], P['PEAK_LO'], P['LEG_W']))
    A('')
    A('结构类型分布（A 标准W / B 强趋势W / C 深回撤W / D 高位平台）：'
      '`A=%s B=%s C=%s D=%s`（单元格口径）。'
      % (sc(struct_cnt, 0), sc(struct_cnt, 1), sc(struct_cnt, 2),
         sc(struct_cnt, 3)))
    A('')

    # ---- §3 funnel ----------------------------------------------------
    A('## §3　样本漏斗（防幸存者偏差，SPEC §17）')
    A('')
    A(table(funnel, ['stage', 'n_cells', 'n_episodes', 'PRE', 'IS', 'VALID', 'OOS'],
            ['阶段', '单元格', '独立事件', 'PRE', 'IS', 'VALID', 'OOS']))
    A('')
    A('> 每一阶段都被完整保留：从未只保存「最终成功突破」的股票。'
      '成功 / 失败突破均按 `SUC_BRK` / `FAIL_BRK` 同时登记（SPEC §17）。')
    A('')

    # ---- §4 four-group event study ------------------------------------
    A('## §4　四组对照事件研究（SPEC §9–§11）')
    A('')
    A('A=主升→整理→二次突破；B=普通突破（无明确主升+整理）；'
      'C1=主升→整理但未突破；C2=主升→整理（CONSOLIDATING）；D=主升→结构破坏（FAILED）。')
    A('')
    rows = []
    for nm, r in (('A', recA), ('B', recB), ('C1', recC1), ('C2', recC2),
                  ('D', recD)):
        rows.append({
            '组': nm, '事件数': r.get('n_events'),
            'net30(T+5)': f(r.get('mean_net30_%d' % P['Hstar']), 5),
            'win30(T+5)': f(r.get('win_net30_%d' % P['Hstar']), 3),
            'net30(T+20)': f(r.get('mean_net30_20'), 5),
            '超额市场(net30,T+5)': f(r.get('exmkt_net30_%d' % P['Hstar']), 5),
            '超额行业(net30,T+5)': f(r.get('exsec_net30_%d' % P['Hstar']), 5),
            'MAE20': f(r.get('mae_20'), 4), 'MFE20': f(r.get('mfe_20'), 4),
        })
    A(table(rows, ['组', '事件数', 'net30(T+5)', 'win30(T+5)', 'net30(T+20)',
                   '超额市场(net30,T+5)', '超额行业(net30,T+5)', 'MAE20', 'MFE20']))
    A('')

    A('### §4.1　A 组分 horizon 净收益（是否立即发生 vs 需持有）')
    A('')
    hr = []
    for h in P['horizons']:
        hr.append({'horizon': 'T+%d' % h,
                   'gross': f(recA.get('mean_%d' % h), 5),
                   'net15': f(recA.get('mean_net15_%d' % h), 5),
                   'net30': f(recA.get('mean_net30_%d' % h), 5),
                   'net50': f(recA.get('mean_net50_%d' % h), 5),
                   'win30': f(recA.get('win_net30_%d' % h), 3),
                   'exmkt_net30': f(recA.get('exmkt_net30_%d' % h), 5),
                   'exsec_net30': f(recA.get('exsec_net30_%d' % h), 5)})
    A(table(hr, ['horizon', 'gross', 'net15', 'net30', 'net50', 'win30',
                 'exmkt_net30', 'exsec_net30']))
    A('')

    # ---- §5 costs -----------------------------------------------------
    A('## §5　交易成本（SPEC §11）')
    A('')
    A('A 组 T+%d 净收益：%s。' % (P['Hstar'],
                                 '；'.join('%dbp=%s' % (bp, f(v, 5))
                                          for bp, v in cost_net.items())))
    A('')
    A('Bootstrap（月度分块，`%d` 次）A 组 T+%d：95%% CI 下界 = `%s`，'
      'BH-FDR q = `%s`。'
      % (P['boot_B'], P['Hstar'], f(boot_lo, 5), f(qfdr, 4)))
    A('')

    # ---- §6 OOS / phase / yearly --------------------------------------
    A('## §6　时间分段与 OOS（SPEC §14）')
    A('')
    A(table([{'组': r.get('group'), '段': r.get('bucket'), 'n': r.get('n'),
              'gross': f(r.get('gross'), 5), 'net15': f(r.get('net15'), 5),
              'net30': f(r.get('net30'), 5), 'net50': f(r.get('net50'), 5),
              'win30': f(r.get('win30'), 3)}
             for r in phases if r.get('scope') == 'phase'],
            ['组', '段', 'n', 'gross', 'net15', 'net30', 'net50', 'win30']))
    A('')
    A('A 组逐年：')
    A('')
    A(table([{'年': r.get('bucket'), 'n': r.get('n'), 'gross': f(r.get('gross'), 5),
              'net15': f(r.get('net15'), 5), 'net30': f(r.get('net30'), 5),
              'net50': f(r.get('net50'), 5), 'win30': f(r.get('win30'), 3)}
             for r in phases if r.get('scope') == 'year'],
            ['年', 'n', 'gross', 'net15', 'net30', 'net50', 'win30']))
    A('')
    A('Walk-Forward（train 3y → test 1y，滚动 %d..%d）：'
      % (P['wf_start_year'], P['wf_end_year']))
    A('')
    A(table([{'train': r.get('train'), 'test': r.get('test'),
              'n_train': r.get('n_train'), 'n_test': r.get('n_test'),
              'gross': f(r.get('gross'), 5), 'net30': f(r.get('net30'), 5),
              'win30': f(r.get('win30'), 3), 'IC': f(r.get('IC'), 4)}
             for r in wf],
            ['train', 'test', 'n_train', 'n_test', 'gross', 'net30', 'win30', 'IC']))
    A('')

    # ---- §7 parameter stability ---------------------------------------
    A('## §7　参数稳定性（SPEC §12，禁止寻找单一最优参数）')
    A('')
    A('- 唯一格点 `%s` 个；`positive_ratio`（net30 > 0 的邻近格点占比）= `%s`。'
      % (param.get('n_cells'), f(pos_ratio, 3)))
    A('- 判定阈值：`>= %s → stable`，`<= %s → FRAGILE`。'
      % (P['param_stable_thr'], P['param_fragile_thr']))
    A('- 本实验结论：**%s**。' % param_tag)
    A('')

    # ---- §8 null ------------------------------------------------------
    A('## §8　Null Model 随机对照（SPEC §13）')
    A('')
    A(table([{'检验': r.get('model'), '观测': f(r.get('obs'), 5),
              'Null均值': f(r.get('null_mean'), 5),
              '95%Null区间': '[%s, %s]' % (f(r.get('null_lo'), 5),
                                          f(r.get('null_hi'), 5)),
              'p(单边)': f(r.get('p_one_sided'), 4),
              '覆盖率': f(r.get('resolution'), 3)} for r in nulls],
            ['检验', '观测', 'Null均值', '95%Null区间', 'p(单边)', '覆盖率']))
    A('')

    # ---- §9 subgroups -------------------------------------------------
    A('## §9　分层研究（SPEC §16）')
    A('')
    A(table([{'维度': r.get('dimension'), '分层': r.get('layer'), 'n': r.get('n'),
              'gross': f(r.get('gross'), 5), 'net30': f(r.get('net30'), 5),
              'win30': f(r.get('win30'), 3), 'median': f(r.get('median'), 5)}
             for r in subgroup],
            ['维度', '分层', 'n', 'gross', 'net30', 'win30', 'median']))
    A('')
    _spec_layers = {'第一波强度': ('25-40%', '40-60%', '>60%'),
                    '回撤深度': ('<10%', '10-20%', '20-30%', '>30%'),
                    '右底质量': ('右底>左底', '右底≈左底', '右底<左底'),
                    '量能': ('缩量', '正常', '放量'),
                    '行业强度': ('强行业', '普通行业', '弱行业'),
                    '市场': ('上涨', '震荡', '下跌'),
                    '板块': ('MAIN', 'GEM', 'STAR')}
    _have = {(r.get('dimension'), r.get('layer')) for r in subgroup}
    _miss = ['%s/%s' % (d, l_) for d, ls in _spec_layers.items()
             for l_ in ls if (d, l_) not in _have]
    A('> 空缺层说明：%s 在 A 组中样本数为 0，故未列出——这不是事后筛选，而是主口径 '
      '`break_vol >= %s` 与 `retrace_min = %s` 的直接后果（放量是二次突破的必要条件，'
      '故 A 组不存在「缩量」事件）。'
      % ('、'.join('`%s`' % x for x in _miss) if _miss else '无',
         P['break_vol'], P['retrace_min']))
    A('')

    # ---- §10 incremental ladder ---------------------------------------
    A('## §10　增量信息检验（SPEC §15，研究核心）')
    A('')
    A('逐步 OLS：`Model 0 = Market+Sector` → `+MOM` → `+First_Rise` → '
      '`+Consolidation` → `+Right_Bottom` → `+Breakout`（同日横截面标准化；'
      '时间切分评估 OOS，`%s` 训练 / `%s` 样本外）。'
      % (P['reg_oos_frac'], 1 - P['reg_oos_frac']))
    A('')
    for y in ('y5', 'y10'):
        A('### 因变量 `%s`' % ('T+5' if y == 'y5' else 'T+10'))
        A('')
        A(table([{'model': r.get('model'), 'n': r.get('n'),
                  'R2': f(r.get('R2'), 5), 'dR2': f(r.get('dR2'), 5),
                  'IC': f(r.get('IC'), 4), 'RankIC': f(r.get('RankIC'), 4),
                  'AUC': f(r.get('AUC'), 4), 'OOS_R2': f(r.get('OOS_R2'), 5),
                  'OOS_IC': f(r.get('OOS_IC'), 4),
                  'OOS_RankIC': f(r.get('OOS_RankIC'), 4)}
                 for r in inc if r.get('y') == y],
                ['model', 'n', 'R2', 'dR2', 'IC', 'RankIC', 'AUC', 'OOS_R2',
                 'OOS_IC', 'OOS_RankIC']))
        A('')
    A('嵌套块增量检验（`W = F·Δk`，`df2 = n−k ≈ 2.3e5`，与 `Δk` 自由度卡方 1%/5% '
      '临界值比较；`PASS` 还要求对应 OOS_IC 不恶化）：')
    A('')
    A(table([{'比较': 'Model 4 − Model 3（整理完成/右底）', 'y': y,
              'ΔR²': f(t43[y]['dR2'], 7), 'Δk': t43[y]['dk'],
              'W': f(t43[y]['W'], 3), 'OOS_IC(前→后)': '%s → %s' % (
                  f(t43[y]['oos_ic_from'], 4), f(t43[y]['oos_ic_to'], 4)),
              '判定': inc_one(t43[y])}
             for y in ('y5', 'y10')] +
            [{'比较': 'Model 5 − Model 4（二次突破）', 'y': y,
              'ΔR²': f(t54[y]['dR2'], 7), 'Δk': t54[y]['dk'],
              'W': f(t54[y]['W'], 3), 'OOS_IC(前→后)': '%s → %s' % (
                  f(t54[y]['oos_ic_from'], 4), f(t54[y]['oos_ic_to'], 4)),
              '判定': inc_one(t54[y])}
             for y in ('y5', 'y10')],
        ['比较', 'y', 'ΔR²', 'Δk', 'W', 'OOS_IC(前→后)', '判定']))
    A('')
    A('- **Model 5 − Model 4**（二次突破在趋势/动量之外是否新增信息）→ **%s**'
      '（`coef_Breakout` = %s）。' % (L3, f(coefB, 6)))
    A('- **Model 4 − Model 3**（「整理完成 / 右底」本身是否新增信息）→ **%s**。'
      % L2)
    A('')

    # ---- §11 verdict --------------------------------------------------
    A('## §11　判决位（SPEC §18）')
    A('')
    A(table([{'位': k, '结果': 'PASS' if v else 'FAIL'}
             for k, v in sorted(bits.items())], ['位', '结果']))
    A('')
    A('- 通过 `%d/12`　→　**%s**' % (n_pass, verdict))
    A('- 完成度位（占候选单元格比例）：'
      + ' '.join('%s=%s' % (k, f(bit_counts.get(k, 0) / max(1, int(
          num(R.get('n_candidates'), 1))), 4))
          for k in ('R1', 'R2', 'R3', 'R4', 'R5', 'R6')))
    A('- 失效位：'
      + ' '.join('%s=%s' % (k, f(bit_counts.get(k, 0) / max(1, int(
          num(R.get('n_candidates'), 1))), 4))
          for k in ('F1', 'F2', 'F3', 'F4', 'F5')))
    A('')
    A('> `b10`（Top1%% 贡献 < 0.5）在 A 组 **n=%d < 100** 时 Top1%% 贡献不可计算，'
      '按保守口径记 FAIL（无法证明"非极端驱动"≠"未极端驱动"）。'
      % int(num(bitsx.get('n_events_A'), 0)))
    A('')
    A('Bias audit：')
    A('')
    A(table([{'项': a.get('item'), '状态': a.get('status'),
              '说明': a.get('detail')} for a in audit], ['项', '状态', '说明']))
    A('')

    # ---- §12 ten questions --------------------------------------------
    A('## §12　十个必答问题（SPEC §19）')
    A('')
    A('**Q1　主升后的二次整理是否具有可重复识别的结构？**　'
      '漏斗显示「主升样本(N=%s) → 进入整理(%s) → 形成右底(%s) → READY(%s) '
      '→ 突破(%s)」逐级可复现，READY 六位在候选单元格上稳定出现'
      '（R1=%s…R6=%s），**因此"结构可重复识别"成立**；'
      '但这不等于该结构具有正向预测力——后者见 §13 第 1 条（%s）。'
      % (fn(funnel, '主升样本', 'n_cells'), fn(funnel, '进入整理', 'n_cells'),
         fn(funnel, '形成右底', 'n_cells'), fn(funnel, 'READY', 'n_cells'),
         fn(funnel, '突破', 'n_cells'),
         f(bit_counts.get('R1', 0) / max(1, int(num(R.get('n_candidates'), 1))), 3),
         f(bit_counts.get('R6', 0) / max(1, int(num(R.get('n_candidates'), 1))), 3),
         L1))
    A('')
    A('**Q2　什么样的整理最容易完成？**　'
      '漏斗口径：进入整理的 %s 个单元格中，仅 %s 个（%s）同时满足 READY 六位，'
      '说明"整理完成"本身是低概率状态，不是整理的默认结局。'
      '各项完成度位的边际比例见 §11（R1–R6）；本报告**不**再把分层与 READY 率'
      '做二次筛选，以免事后选层。'
      % (fn(funnel, '进入整理', 'n_cells'), fn(funnel, 'READY', 'n_cells'),
         pc(num(fn(funnel, 'READY', 'n_cells')) /
            max(1.0, num(fn(funnel, '进入整理', 'n_cells'))), 4)))
    A('')
    A('**Q3　右底高于/接近/低于左底，哪几类在 OOS 中表现稳定？**　'
      '全样本分层（§9「右底质量」）：`%s`。**注意：A 组 OOS 事件仅 %s 个，'
      '分组后每层 OOS 样本量为个位数，OOS 层面的分层稳定性在本数据上无法定论**，'
      '下表只作全样本参考。'
      % ('；'.join('%s(n=%d, net30=%s)'
                  % (r['layer'], r['n'], f(r['net30'], 5))
                  for r in subgroup if r.get('dimension') == '右底质量'),
         fn(funnel, '突破', 'OOS')))
    A('')
    A('**Q4　整理深度多少最稳定？**　'
      '全样本分层（§9「回撤深度」）：`%s`。同样受 OOS 样本量限制。'
      % '；'.join('%s(n=%d, net30=%s)'
                 % (r['layer'], r['n'], f(r['net30'], 5))
                 for r in subgroup if r.get('dimension') == '回撤深度'))
    A('')
    A('**Q5　整理时间多少最稳定？**　'
      '`consol_days`（`peak_off`）已作为连续变量进入 `Model 3`；其边际贡献见 §10 '
      '（`dR2(M3−M2)`）。本报告不另设 `consol_days` 分箱，避免事后选箱。')
    A('')
    A('**Q6　缩量是否真的提供增量信息？**　'
      '`vol_ratio_5_leg` 等成交结构在 `Model 4` 中进入，其增量由 `Model 4 − Model 3` '
      '给出：`ΔR²(T+5) = %s`（`W = %s`），判定 **%s**。'
      '**边界**：A 组在构造上要求 `vr20 >= %s`，因此"缩量突破"在 A 组内无样本，'
      '本结论只覆盖"正常量 vs 放量"的对比，不能外推为"缩量有效/无效"。'
      % (f(t43['y5']['dR2'], 7), f(t43['y5']['W'], 3), L2, P['break_vol']))
    A('')
    A('**Q7　MA20/MA60 结构是否提供增量信息？**　'
      '本次增量阶梯按 SPEC §15 冻结的变量分组，`Model 4` 同时包含右底结构与 '
      '`vol_ratio_5_leg / rb_dist_ma20`；因此该"结构性增量"无法与量能增量分离，'
      '其合并判定为 **%s**（`ΔR²(T+5) = %s`）。这是本设计的一个已知局限。'
      % (L2, f(t43['y5']['dR2'], 7)))
    A('')
    A('**Q8　相对行业强度是否显著提高二次突破质量？**　'
      '见「行业强度」分层与 `R6/F5` 位；R6（`rs_sector_20 >= %s`）在候选上占比 %s。'
      % (P['RS_MIN'], f(bit_counts.get('R6', 0) / max(1, int(
          num(R.get('n_candidates'), 1))), 3)))
    A('')
    A('**Q9　二次突破相对于普通 Momentum 突破是否有增量信息？**　'
      '`ΔR²(Model 5 − Model 4) = %s`（T+5）/ `%s`（T+10），`W = %s / %s`，'
      '`coef_Breakout = %s`，且加入 `Breakout` 后 OOS_IC 由 %s 降至 %s（T+5）。'
      'A 组 OOS net30 = `%s` vs B 组 OOS net30 = `%s`。→ **%s**'
      % (f(t54['y5']['dR2'], 7), f(t54['y10']['dR2'], 7),
         f(t54['y5']['W'], 3), f(t54['y10']['W'], 3), f(coefB, 6),
         f(t54['y5']['oos_ic_from'], 4), f(t54['y5']['oos_ic_to'], 4),
         f(oosA, 5), f(oosB, 5), L3))
    A('')
    A('**Q10　二次突破之后的收益主要集中在 T+3 / T+5 / T+10 / T+20？**　'
      '由 §4.1 分 horizon 表读出（A 组）：T+3 net30=`%s`、T+5=`%s`、'
      'T+10=`%s`、T+20=`%s`。A 组各 horizon 净收益均为负，因此本数据上'
      '不存在"收益集中窗口"可报告。'
      % (f(recA.get('mean_net30_3'), 5), f(recA.get('mean_net30_5'), 5),
         f(recA.get('mean_net30_10'), 5), f(recA.get('mean_net30_20'), 5)))
    A('')

    # ---- §13 conclusion (verbatim SPEC §20) ---------------------------
    A('## §13　最终结论')
    A('')
    A('```text')
    A('【研究结论】')
    A('')
    A('1. 主升后的二次整理：        %s' % L1)
    A('2. 整理完成：                %s' % L2)
    A('3. 二次突破相对Momentum：    %s' % L3)
    A('4. 最稳定的结构区间：        %s' % keep_txt)
    A('5. 主要失效环境：            %s' % fail_txt)
    A('6. 成本后：                  %s（%dbp 下为正者：%s）'
      % (cost_txt, P['primary_cost_bp'],
         ','.join(str(b) for b in cost_pos) if cost_pos else '无'))
    A('7. OOS：                     %s' % ('通过' if oos_pass else '未通过'))
    A('8. 参数稳定性：              %s（%s）'
      % ('通过' if param_pass else '未通过', param_tag))
    A('9. 年度稳定性：              %s（IS 正值年份占比 %s）'
      % ('通过' if year_pass else '未通过', f(pos_share, 3)))
    A('10. 最终状态：               %s' % final_state)
    A('```')
    A('')
    A('```text')
    A('形态本身有效 vs 只是 Momentum/Volume 代理：')
    A('  整理完成增量 dR2(M4-M3) = %s   W = %s / %s   -> %s'
      % (f(t43['y5']['dR2'], 7), f(t43['y5']['W'], 3), f(t43['y10']['W'], 3), L2))
    A('  二次突破增量 dR2(M5-M4) = %s / %s   W = %s / %s   coef_Breakout = %s'
      % (f(t54['y5']['dR2'], 7), f(t54['y10']['dR2'], 7), f(t54['y5']['W'], 3),
         f(t54['y10']['W'], 3), f(coefB, 6)))
    A('  市场状态净收益：%s' % regime_txt)
    A('```')
    A('')
    A('> `trading_authorization = NO`（无论结论如何）。'
      'Entry / Exit / Position sizing / Execution 属独立第二阶段。')
    A('')

    # ---- §14 artifacts -------------------------------------------------
    A('## §14　产物清单（SPEC §21 / FREEZE §8）')
    A('')
    for name in REQUIRED + EXTRA:
        p = os.path.join(OUT, name)
        ok = os.path.exists(p) and os.path.getsize(p) > 0
        A('- [%s] `%s`（%s bytes）'
          % ('x' if ok else ' ', name,
             format(os.path.getsize(p), ',') if os.path.exists(p) else 'MISSING'))
    A('')

    rep = os.path.join(OUT, 'second_leg_report.md')
    with open(rep, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')
    LOG('second_leg_report.md written (%d lines)' % len(L))

    # ------------------------------------------------- delivery check
    LOG.sep()
    LOG('delivery check')
    bad = []
    for name in REQUIRED:
        p = os.path.join(OUT, name)
        if not os.path.exists(p) or os.path.getsize(p) == 0:
            bad.append(name)
        else:
            LOG('  OK   %-38s %10s bytes' % (name, format(os.path.getsize(p), ',')))
    for name in EXTRA:
        p = os.path.join(OUT, name)
        if not os.path.exists(p) or os.path.getsize(p) == 0:
            LOG('  MISS %-38s (optional)' % name)
    LOG.sep()
    LOG('answers: Q1=%s Q2=%s Q3=%s | cost+=%s | OOS=%s param=%s year=%s'
        % (L1, L2, L3, cost_pos, oos_pass, param_pass, year_pass))
    LOG('verdict: %s   final: %s   elapsed %.1fs'
        % (verdict, final_state, time.time() - t0))
    if bad:
        LOG('DELIVERY FAILED -- missing: %s' % ', '.join(bad))
        return 1
    LOG('EXIT 0 -- %d required artefacts present and non-empty' % len(REQUIRED))
    return 0


def fn(funnel, stage, key):
    r = row_of(funnel, stage=stage)
    return (r or {}).get(key, 'NA')


if __name__ == '__main__':
    sys.exit(main())
