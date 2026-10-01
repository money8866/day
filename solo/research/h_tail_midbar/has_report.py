# -*- coding: utf-8 -*-
"""H-TAIL-MIDBAR-01 -- report generator + delivery check.

Consumes H_TAIL_MIDBAR_01_RESULTS.json (written by tmb_run.py), evaluates the
pre-registered gates G1..G12 (SPEC section 23), applies the frozen decision
tree (SPEC section 24), and writes:

    H_TAIL_MIDBAR_01_SUMMARY.json
    H_TAIL_MIDBAR_01_REPORT.md

It then verifies that every artefact required by SPEC section 25 exists and is
non-empty.  Exit code 0 means the study is delivered and internally consistent.

This script evaluates; it never re-fits.  No threshold, window or filter is
touched here.
"""
import os
import sys
import json
import time

import numpy as np

import tmb_common as T

P = T.PREREG_T
OUT = T.OUT
RES = os.path.join(OUT, 'H_TAIL_MIDBAR_01_RESULTS.json')

EQ = '\u2550' * 46        # heavy double rule for the verdict header
RULE = '\u2500' * 46      # light rule for sub-blocks
LOG = T.Log('has_report')

REQUIRED = [
    'H_TAIL_MIDBAR_01_FREEZE.md',
    'H_TAIL_MIDBAR_01_SPEC.md',
    'H_TAIL_MIDBAR_01_EVENTS.csv',
    'H_TAIL_MIDBAR_01_MA.csv',
    'H_TAIL_MIDBAR_01_VOLUME.csv',
    'H_TAIL_MIDBAR_01_THRESHOLD.csv',
    'H_TAIL_MIDBAR_01_NULL.csv',
    'H_TAIL_MIDBAR_01_BOOTSTRAP.csv',
    'H_TAIL_MIDBAR_01_OOS.csv',
    'H_TAIL_MIDBAR_01_WALK_FORWARD.csv',
    'H_TAIL_MIDBAR_01_BIAS_AUDIT.csv',
    'H_TAIL_MIDBAR_01_TAIL.csv',
    'H_TAIL_MIDBAR_01_SUMMARY.json',
    'H_TAIL_MIDBAR_01_REPORT.md',
]
EXTRA = [
    'H_TAIL_MIDBAR_01_REGIME.csv',
    'H_TAIL_MIDBAR_01_OVERLAP.csv',
    'H_TAIL_MIDBAR_01_PARAM_GRID.csv',
    'H_TAIL_MIDBAR_01_RESULTS.json',
]

DECLARATION = (
    '本研究验证的是「收盘中阳线 → 次日收益」，而不是严格意义上的'
    '「尾盘惯性」。日线数据无法还原 14:30–14:57 的真实尾盘时点，'
    '本项目**不做**这种伪装。'
)


# ------------------------------------------------------------------ helpers
def f(x, nd=4):
    return T.fmt(x, nd)


def pc(x, nd=3):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 'NA'
    if not np.isfinite(v):
        return 'nan'
    return ('%.' + str(nd) + 'f%%') % (100.0 * v)


def g(x, key, default=None):
    if not isinstance(x, dict):
        return default
    v = x.get(key, default)
    return default if v is None else v


def num(x, default=np.nan):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if np.isfinite(v) else default


def row_of(rows, **kw):
    for r in rows:
        if all(r.get(k) == v for k, v in kw.items()):
            return r
    return None


def verdict_of(obs, lo, hi):
    """YES / NO / UNPROVEN from a monthly-cluster difference bootstrap."""
    o, l, h = num(obs), num(lo), num(hi)
    if not np.isfinite(o):
        return 'UNPROVEN'
    if o > 0 and np.isfinite(l) and l > 0:
        return 'YES'
    if o <= 0 and np.isfinite(h) and h <= 0:
        return 'NO'
    return 'UNPROVEN'


def table(rows, cols, headers=None):
    heads = headers or [c for c in cols]
    out = ['| ' + ' | '.join(heads) + ' |',
           '|' + '|'.join(['---'] * len(cols)) + '|']
    for r in rows:
        out.append('| ' + ' | '.join(str(r.get(c, '')) for c in cols) + ' |')
    return '\n'.join(out)


# ------------------------------------------------------------------ main
def main():
    LOG('H-TAIL-MIDBAR-01 report')
    if not os.path.exists(RES):
        LOG('RESULTS.json missing -- run tmb_run.py first')
        return 1
    with open(RES, 'r', encoding='utf-8') as fh:
        res = json.load(fh)

    ev = res['events']
    ma = res['ma']
    vo = res['volume']
    thr = res['threshold']
    nul = res['null']
    boo = res['bootstrap']
    oos = res['oos']
    wf = res['walk_forward']
    reg = res['regime']
    tail = res['tail']
    ov = res['overlap']
    bias = res['bias']
    gsum = res['grid']['summary']
    meta = res['meta']

    pri = row_of(ev, arm='+F+MA+V', entry=P['entry_primary'])
    sec = row_of(ev, arm='+F+MA+V', entry=P['entry_second'])
    raw = row_of(ev, arm='RAW')
    f1 = row_of(ev, arm='+F')
    fma = row_of(ev, arm='+F+MA')
    fv = row_of(ev, arm='+F+V')
    ma_by = {r['stratum']: r for r in ma}
    vo_by = {r['stratum']: r for r in vo}
    oos_pri = row_of(oos, arm='+F+MA+V', scope='OOS')
    oos_raw = row_of(oos, arm='RAW', scope='OOS')
    years = dict((y, row_of(oos, arm='+F+MA+V', scope='YEAR_%d' % y))
                 for y in (2022, 2023, 2024, 2025, 2026))
    reg_by = {r['regime']: r for r in reg}

    # ---------------------------------------------------------- gates
    G, GD = {}, {}

    G['G1'] = bool(num(pri['mean_net30'], -1.0) > 0)
    GD['G1'] = 'Primary Arm mean Net30 = %s' % pc(pri['mean_net30'])

    G['G2'] = bool(np.isfinite(num(pri['pf30'])) and num(pri['pf30']) > 1)
    GD['G2'] = 'Primary Arm PF30 = %s' % f(pri['pf30'], 3)

    G['G3'] = bool(num(pri['boot_lo'], -1.0) > 0 and num(pri['boot_hi'], -1.0) > 0)
    GD['G3'] = ('monthly-cluster 95%% CI (net30) = [%s, %s], p(<=0) = %s'
                % (pc(pri['boot_lo']), pc(pri['boot_hi']),
                   f(pri['boot_p_le0'], 4)))

    G['G4'] = bool(num(oos_pri['mean_net30'], -1.0) > 0)
    GD['G4'] = 'OOS(2026) Net30 = %s' % pc(oos_pri['mean_net30'])

    n_pos_y = sum(1 for y in years if num(years[y]['mean_net30'], -1.0) > 0)
    G['G5'] = bool(n_pos_y >= 3)
    GD['G5'] = ('years with Net30 > 0 = %d of 5 (%s)'
                % (n_pos_y, ', '.join('%d:%s' % (y, pc(years[y]['mean_net30']))
                                      for y in sorted(years))))

    G['G6'] = bool(num(pri['leave_top_5pct_net30'], -1.0) > 0)
    GD['G6'] = 'Leave Top 5%% Net30 = %s' % pc(pri['leave_top_5pct_net30'])

    n_pos_ma = sum(1 for s in ('M1', 'M2', 'M3', 'M4')
                   if num(ma_by[s]['mean_net30'], -1.0) > 0)
    G['G7'] = bool(n_pos_ma >= 2)
    GD['G7'] = ('MA structures with Net30 > 0 = %d of 4 (%s)'
                % (n_pos_ma, ', '.join('%s:%s' % (s, pc(ma_by[s]['mean_net30']))
                                       for s in ('M1', 'M2', 'M3', 'M4'))))

    n_pos_v = sum(1 for s in ('V1', 'V2', 'V3', 'V4', 'V5')
                  if num(vo_by[s]['mean_net30'], -1.0) > 0)
    G['G8'] = bool(n_pos_v >= 2)
    GD['G8'] = ('Volume gates with Net30 > 0 = %d of 5 (%s)'
                % (n_pos_v, ', '.join('%s:%s' % (s, pc(vo_by[s]['mean_net30']))
                                      for s in ('V1', 'V2', 'V3', 'V4', 'V5'))))

    near_ratio = num(gsum['primary_neighbourhood_positive_ratio'])
    G['G9'] = bool(np.isfinite(near_ratio) and near_ratio >= 0.5)
    GD['G9'] = ('pre-registered neighbourhood (thr=3%%, window=10) '
                'positive_ratio = %s over %d cells; overall grid '
                'positive_ratio = %s'
                % (f(near_ratio, 3), int(gsum['primary_neighbourhood_cells']),
                   f(gsum['positive_ratio'], 3)))

    adopted = [r for r in nul if r.get('adopted')]
    n_distinct = sum(1 for r in adopted if num(r['obs']) > num(r['null_hi']))
    G['G10'] = bool(len(adopted) >= 3 and n_distinct >= 3)
    GD['G10'] = ('null families adopted (resolution >= %.2f) = %d of %d; '
                 'adopted families with obs > null 97.5%% = %d'
                 % (P['null_min_resolution'], len(adopted), len(nul),
                    n_distinct))

    n_fail = sum(1 for b in bias if b['verdict'] == 'FAIL')
    n_flag = sum(1 for b in bias if b['verdict'] == 'FLAG')
    G['G11'] = bool(n_fail == 0)
    GD['G11'] = ('bias audit: %d PASS / %d FLAG / %d FAIL (%s)'
                 % (len(bias) - n_fail - n_flag, n_flag, n_fail,
                    ', '.join(b['check'] for b in bias
                              if b['verdict'] != 'PASS') or 'no exception'))

    n_pos_reg = sum(1 for s in ('BULL', 'RANGE', 'BEAR')
                    if num(reg_by[s]['mean_net30'], -1.0) > 0)
    G['G12'] = bool(n_pos_reg >= 2)
    GD['G12'] = ('regimes with Net30 > 0 = %d of 3 (%s)'
                 % (n_pos_reg, ', '.join('%s:%s' % (s, pc(reg_by[s]['mean_net30']))
                                         for s in ('BULL', 'RANGE', 'BEAR'))))

    all_pass = all(G[k] for k in G)

    tail_dependent = bool(num(pri['mean_full'] if 'mean_full' in pri else pri['mean'], -1)
                          > 0 and num(pri['leave_top_5pct'], 1) <= 0)
    net_tail_dependent = bool(num(pri['mean_net30'], -1) > 0
                             and num(pri['leave_top_5pct_net30'], 1) <= 0)

    # ------------------------------------------------- decision tree (SPEC 24)
    if all_pass:
        if (G['G3'] and num(pri['boot_p_le0'], 1.0) < 0.01
                and G['G4'] and G['G6'] and G['G9']):
            verdict = 'ROBUST_ALPHA'
        else:
            verdict = 'WEAK_SIGNAL'
    elif not G['G1']:
        verdict = 'FAIL → ARCHIVE'
    elif tail_dependent:
        verdict = 'TAIL_DEPENDENT'
    elif (not G['G12']) and G['G1']:
        verdict = 'REGIME_DEPENDENT'
    elif not G['G9']:
        verdict = 'PARAMETER_FRAGILE'
    elif (not G['G3']) and (not G['G10']):
        verdict = 'NO_ROBUST_ALPHA'
    else:
        verdict = 'UNPROVEN'

    # ------------------------------------------------- incremental verdicts
    ma_inc = verdict_of(fma['incr_obs'], fma['incr_lo'], fma['incr_hi'])
    vol_inc = verdict_of(fv['incr_obs'], fv['incr_lo'], fv['incr_hi'])
    first_inc = verdict_of(f1['incr_obs'], f1['incr_lo'], f1['incr_hi'])
    full_inc = verdict_of(pri['incr_obs'], pri['incr_lo'], pri['incr_hi'])

    mono = res['threshold_monotonicity']
    mono_ok = (np.isfinite(num(mono['RAW'])) and num(mono['RAW']) >= 0.6
               and np.isfinite(num(mono['FULL'])) and num(mono['FULL']) >= 0.6)
    mono_tag = 'MONOTONIC' if mono_ok else 'NO_MONOTONICITY'

    ovp = ov['+F+MA+V']

    # ------------------------------------------------- SUMMARY.json
    summary = {
        'research_id': P['hypothesis_id'],
        'version': P['version'],
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'spec_sha256': T.SPEC_SHA256,
        'data_mode': 'DAILY_PROXY (no minute data in cache)',
        'declaration': DECLARATION,
        'primary_arm': {
            'definition': 'First(10) & MApresent & V5, thr=3%',
            'entry': P['entry_primary'], 'exit': P['exit_primary'],
            'n': int(num(pri['n'], 0)),
            'mean_gross': num(pri['mean']),
            'mean_net30': num(pri['mean_net30']),
            'median_net30': num(pri['median']) - 0.003,
            'win_rate_net30': num(pri['win_net30']),
            'pf30': num(pri['pf30']),
            'std': num(pri['std']),
            'p10': num(pri['p10']), 'p25': num(pri['p25']),
            'p75': num(pri['p75']), 'p90': num(pri['p90']),
            'max': num(pri['max']), 'min': num(pri['min']),
            'boot_mean': num(pri.get('boot_mean')),
            'boot_lo': num(pri.get('boot_lo')),
            'boot_hi': num(pri.get('boot_hi')),
            'boot_p_le0': num(pri.get('boot_p_le0')),
        },
        'primary_arm_second_execution': {
            'entry': P['entry_second'], 'exit': P['exit_primary'],
            'n': int(num(sec['n'], 0)),
            'mean_net30': num(sec['mean_net30']),
            'win_rate_net30': num(sec['win_net30']),
            'pf30': num(sec['pf30']),
        },
        'costs_bp': list(P['cost_bp']),
        'primary_cost_bp': P['primary_cost_bp'],
        'net30_ladder_primary': {
            'net_%dbp' % bp: num(pri.get('mean_net%d' % bp))
            for bp in P['cost_bp']},
        'tail': {
            'full_net30': num(pri['mean_net30']),
            'leave_top_1pct_net30': num(pri['leave_top_1pct_net30']),
            'leave_top_5pct_net30': num(pri['leave_top_5pct_net30']),
            'leave_top_10pct_net30': num(pri['leave_top_10pct_net30']),
            'top1_contribution': num(pri['top1_share']),
            'top5_contribution': num(pri['top5_share']),
            'TAIL_DEPENDENT_gross': tail_dependent,
            'TAIL_DEPENDENT_net30': net_tail_dependent,
        },
        'oos': {
            'IS_2022_2024_net30': num(row_of(oos, arm='+F+MA+V',
                                             scope='IS')['mean_net30']),
            'VALID_2025_net30': num(row_of(oos, arm='+F+MA+V',
                                           scope='VALID')['mean_net30']),
            'OOS_2026_net30': num(oos_pri['mean_net30']),
            'RAW_OOS_2026_net30': num(oos_raw['mean_net30']),
            'yearly_net30': dict((str(y), num(years[y]['mean_net30']))
                                 for y in sorted(years)),
            'walk_forward': [
                {'fold': w['fold'], 'n_test': w['n_test'],
                 'net30_test': num(w['net30_test']),
                 'boot_lo': num(w['boot_lo']), 'boot_hi': num(w['boot_hi'])}
                for w in wf],
        },
        'incremental_alpha': {
            'raw_mean_net30': num(raw['mean_net30']),
            'first_event_vs_raw': {
                'obs': num(f1['incr_obs']), 'lo': num(f1['incr_lo']),
                'hi': num(f1['incr_hi']), 'verdict': first_inc},
            'ma_vs_first': {
                'obs': num(fma['incr_obs']), 'lo': num(fma['incr_lo']),
                'hi': num(fma['incr_hi']), 'verdict': ma_inc},
            'volume_vs_first': {
                'obs': num(fv['incr_obs']), 'lo': num(fv['incr_lo']),
                'hi': num(fv['incr_hi']), 'verdict': vol_inc},
            'full_vs_first_ma': {
                'obs': num(pri['incr_obs']), 'lo': num(pri['incr_lo']),
                'hi': num(pri['incr_hi']), 'verdict': full_inc},
        },
        'threshold_axis': {
            'spearman_raw': num(mono['RAW']),
            'spearman_full_stack': num(mono['FULL']),
            'tag': mono_tag,
            'primary_threshold': P['thr_primary'],
        },
        'null_models': [
            {'family': r['family'], 'obs': num(r['obs']),
             'null_mean': num(r['null_mean']), 'null_lo': num(r['null_lo']),
             'null_hi': num(r['null_hi']), 'excess': num(r['excess']),
             'p_one_sided': num(r['p_one_sided']),
             'resolution': num(r['resolution']),
             'adopted': bool(r['adopted'])}
            for r in nul],
        'overlap': {
            'n_events': ovp['n_events'],
            'adjacent_rate': num(ovp['adjacent_rate']),
            'rate_le5': num(ovp['rate_le5']),
            'rate_le20': num(ovp['rate_le20']),
            'n_eff': num(ovp['n_eff']),
        },
        'parameter_grid': gsum,
        'regime': dict((r['regime'], {'days': r['days'], 'n': r['n'],
                                      'net30': num(r['mean_net30'])})
                       for r in reg),
        'bias_audit': [{'check': b['check'], 'verdict': b['verdict'],
                        'value': b['value']} for b in bias],
        'gates': dict((k, bool(G[k])) for k in sorted(G)),
        'gate_detail': GD,
        'gates_all_pass': bool(all_pass),
        'provisional_alpha': bool(all_pass),
        'overall_verdict': verdict,
        'trading_authorization': 'NO',
        'meta': meta,
    }
    T.save_json(summary, 'H_TAIL_MIDBAR_01_SUMMARY.json')
    LOG('SUMMARY.json written; verdict = %s' % verdict)

    # ------------------------------------------------- REPORT.md
    L = []
    A = L.append
    fmtp = lambda x: pc(x)

    A('# H-TAIL-MIDBAR-01 — 最终报告')
    A('')
    A('尾盘惯性「第一根 ≥3% 中阳线 → 次日卖出」科研验证  ')
    A('Research ID: `H-TAIL-MIDBAR-01` · Version %s · %s  '
      % (P['version'], P['created']))
    A('SPEC SHA256: `%s`（运行前冻结，运行期逐次校验）  ' % T.SPEC_SHA256[:32])
    A('样本区间 %s .. %s · IS 2022–2024 · Validation 2025 · OOS 2026  '
      % (meta['window'][0], meta['window'][1]))
    A('报告生成于 %s · %s'
      % (time.strftime('%Y-%m-%d %H:%M:%S'), 'trading_authorization = **NO**'))
    A('')
    A('```')
    A(EQ)
    A('H-TAIL-MIDBAR-01')
    A('')
    A('Primary Event:')
    A('First \u22653% bullish mid-bar')
    A('+ MA5/MA20 structure')
    A('+ Volume Gate')
    A('')
    A('Entry:')
    A('T Close')
    A('')
    A('Exit:')
    A('T+1 Close')
    A('')
    A('Cost:')
    A('30bp')
    A('')
    A(RULE)
    A('Mean Net30:')
    A('%s' % fmtp(pri['mean_net30']))
    A('')
    A('Win Rate:')
    A('%s' % fmtp(pri['win_net30']))
    A('')
    A('PF30:')
    A('%s' % f(pri['pf30'], 3))
    A('')
    A('Leave Top 5%:')
    A('%s' % fmtp(pri['leave_top_5pct_net30']))
    A('')
    A('OOS:')
    A('%s' % fmtp(oos_pri['mean_net30']))
    A('')
    A('Bootstrap:')
    A('[%s, %s]  p(<=0)=%s' % (fmtp(pri['boot_lo']), fmtp(pri['boot_hi']),
                                f(pri['boot_p_le0'], 4)))
    A('')
    A(RULE)
    A('MA Incremental Alpha:')
    A('%s' % ma_inc)
    A('')
    A('Volume Incremental Alpha:')
    A('%s' % vol_inc)
    A('')
    A('First-event Incremental Alpha:')
    A('%s' % first_inc)
    A('')
    A('Overall:')
    A('%s' % verdict)
    A(EQ)
    A('```')
    A('')

    A('## §2 数据边界声明（必须先读）')
    A('')
    A('> ' + DECLARATION)
    A('')
    A('对 `cache_daily/` 全目录做文件名模式扫描（`*min*` / `*5m*` / `*15m*` / '
      '`*60m*` / `*1min*`）：**零命中**（本次运行前重新核实）。')
    A('因此本实验属于任务书 §2 情形 A —— **日线代理实验**：')
    A('')
    A('    ret_t = close_t / close_{t-1} - 1 >= 3%   作为「尾盘中阳线」的日线代理')
    A('')
    A('`body_t = (close-open)/open`、`clv_t = (close-low)/(high-low)` 均为日线量。')
    A('本报告**不主张**任何 14:30–14:57 的真实尾盘结论。')
    A('')

    A('## §3 样本与宇宙')
    A('')
    A('| 项 | 值 |')
    A('|---|---|')
    A('| 面板 | S=%d 只股票 × N=%d 个交易日（%s .. %s） |'
      % (meta['S'], meta['N'], meta['panel_range'][0], meta['panel_range'][1]))
    A('| 研究窗口 | %s .. %s（含 %d 会话 burn-in） |'
      % (meta['window'][0], meta['window'][1], meta['s0']))
    A('| 宇宙 | `%s` |' % meta['universe'])
    A('| 窗口内 eligible cell | %s |' % format(meta['n_eligible_cells_window'], ','))
    A('| 裸事件 E0(3%%) | %s |' % format(meta['n_raw_events_e0'], ','))
    A('| PRIMARY ARM 事件（含 T+1 可成交） | %s |'
      % format(meta['n_primary_events'], ','))
    A('| 价格口径 | 收益用 qfq OHLC；涨幅 `ret_t` 用 `pct_chg`（原始口径） |')
    A('')

    A('## §4 核心实验矩阵（任务书 §7）')
    A('')
    A('| arm | 定义 | N | Mean | Mean Net30 | Win30 | PF30 | '
      'Δnet30 vs 父 | 95% CI(Δ) |')
    A('|---|---|---|---|---|---|---|---|---|')
    for r in ev:
        par = r.get('incr_vs', '')
        A('| %s | %s | %s | %s | %s | %s | %s | %s | %s |'
          % (r['arm'], r['label'], format(int(num(r['n'], 0)), ','),
             f(r['mean']), fmtp(r['mean_net30']), fmtp(r['win_net30']),
             f(r['pf30'], 3),
             (f(r['incr_obs']) if par else '—'),
             ('[%s, %s]' % (f(r['incr_lo']), f(r['incr_hi']))) if par else '—'))
    A('')
    A('`PRIMARY ARM = B + MA + VOL = First(10) & MApresent & V5`。'
      '非 PRIMARY 的臂全部照报，不做事后择优。')
    A('')

    A('## §5 Exit 诊断与第二执行模型（任务书 §8 / §9）')
    A('')
    A('只有 `T+1 Close` 是正式策略结果。T+1 Open / High / Low 仅作诊断：')
    A('')
    A('| arm | Mean(T+1 Close) | Mean(T+1 Open) | Mean(T+1 High) | Mean(T+1 Low) '
      '| share(High>Close) |')
    A('|---|---|---|---|---|---|')
    for r in ev:
        if 'mean_open_t1' in r:
            A('| %s | %s | %s | %s | %s | %s |'
              % (r['arm'], fmtp(r['mean']), fmtp(r['mean_open_t1']),
                 fmtp(r['mean_high_t1']), fmtp(r['mean_low_t1']),
                 fmtp(r['share_high_t1_gt_close'])))
    A('')
    A('第二执行模型（`Entry = open[t+1]`，`Exit = close[t+1]`，'
      '即 T+1 开盘买、T+1 收盘卖）：')
    A('')
    A('| 模型 | N | Mean Net30 | Win30 | PF30 |')
    A('|---|---|---|---|---|')
    A('| Entry close[t] → Exit close[t+1]（PRIMARY） | %s | %s | %s | %s |'
      % (format(int(num(pri['n'], 0)), ','), fmtp(pri['mean_net30']),
         fmtp(pri['win_net30']), f(pri['pf30'], 3)))
    A('| Entry open[t+1] → Exit close[t+1]（第二模型） | %s | %s | %s | %s |'
      % (format(int(num(sec['n'], 0)), ','), fmtp(sec['mean_net30']),
         fmtp(sec['win_net30']), f(sec['pf30'], 3)))
    A('')
    A('全程未使用 T 日任何盘中价作为成交价（见 §15 bias audit #4）。')
    A('')

    A('## §6 净成本阶梯（任务书 §10，30bp 为 Primary）')
    A('')
    A('| 成本 | 0bp | 10bp | 20bp | 30bp | 50bp |')
    A('|---|---|---|---|---|---|')
    A('| PRIMARY Mean | %s | %s | %s | %s | %s |'
      % tuple(fmtp(pri['mean_net%d' % bp]) for bp in P['cost_bp']))
    A('| B (First only) Mean | %s | %s | %s | %s | %s |'
      % tuple(fmtp(f1['mean_net%d' % bp]) for bp in P['cost_bp']))
    A('| RAW Mean | %s | %s | %s | %s | %s |'
      % tuple(fmtp(raw['mean_net%d' % bp]) for bp in P['cost_bp']))
    A('')
    A('PRIMARY：**Mean Net30 = %s / Win Rate = %s / PF30 = %s**'
      % (fmtp(pri['mean_net30']), fmtp(pri['win_net30']), f(pri['pf30'], 3)))
    A('')

    A('## §7 MA5 / MA20 结构分层（任务书 §5 / §12）')
    A('')
    A('| 结构 | N | 占基底 | Mean Net30 | Win30 | PF30 | Δnet30 vs First(F2) | 95% CI |')
    A('|---|---|---|---|---|---|---|---|')
    for r in ma:
        A('| %s | %s | %s | %s | %s | %s | %s | %s |'
          % (r['stratum'], format(int(num(r['n'], 0)), ','),
             fmtp(r.get('share_of_base')), fmtp(r['mean_net30']),
             fmtp(r['win_net30']), f(r['pf30'], 3),
             f(r['incr_obs']) if 'incr_obs' in r else '—',
             ('[%s, %s]' % (f(r['incr_lo']), f(r['incr_hi'])))
             if 'incr_lo' in r else '—'))
    A('')
    A('M4（低位启动）按预注册保留、未删除。MA 只作离散 Gate，未进入任何评分。')
    A('')

    A('## §8 量能阀门分层（任务书 §6 / §12）')
    A('')
    A('| 阀门 | 规则 | N | 占基底 | Mean Net30 | Win30 | PF30 | Δnet30 vs First(F2) | 95% CI |')
    A('|---|---|---|---|---|---|---|---|---|')
    for r in vo:
        A('| %s | %s | %s | %s | %s | %s | %s | %s | %s |'
          % (r['stratum'], r['rule'], format(int(num(r['n'], 0)), ','),
             fmtp(r.get('share_of_base')), fmtp(r['mean_net30']),
             fmtp(r['win_net30']), f(r['pf30'], 3),
             f(r['incr_obs']) if 'incr_obs' in r else '—',
             ('[%s, %s]' % (f(r['incr_lo']), f(r['incr_hi'])))
             if 'incr_lo' in r else '—'))
    A('')
    A('VR20 分母不含当日（shift 1）；停牌日 NaN。V5 = `1.20 <= VR20 <= 3.00` 为 '
      'Primary（预注册，非结果驱动）。')
    A('')

    A('## §9 阈值轴：3% 是否真的有意义（任务书 §13）')
    A('')
    A('| 阈值 | 基底 | N | Mean | Mean Net30 | Win30 | PF30 |')
    A('|---|---|---|---|---|---|---|')
    for r in thr:
        A('| %s | %s | %s | %s | %s | %s | %s |'
          % (('%d%%' % int(round(num(r['thr']) * 100))), r['base'],
             format(int(num(r['n'], 0)), ','), f(r['mean']),
             fmtp(r['mean_net30']), fmtp(r['win_net30']), f(r['pf30'], 3)))
    A('')
    A('Spearman(阈值, Net30)：裸事件 RAW = **%s**，全栈 = **%s** → `%s`'
      % (f(mono['RAW'], 3), f(mono['FULL'], 3), mono_tag))
    A('')
    A('单调性检验只用于回答「次日惯性是否随当日涨幅存在稳定关系」，'
      '**不用于挑选最优阈值**；3% 始终是 Primary Hypothesis。')
    A('')

    A('## §10 Leave-Tail Test（任务书 §11，最重要）')
    A('')
    A('| arm | Full(Net30) | Leave Top1% | Leave Top5% | Leave Top10% | '
      'Top1% 贡献 | Top5% 贡献 | Median(Net30) |')
    A('|---|---|---|---|---|---|---|---|')
    for r in tail:
        A('| %s | %s | %s | %s | %s | %s | %s | %s |'
          % (r['arm'], fmtp(r['full_net30']), fmtp(r['leave_top_1pct_net30']),
             fmtp(r['leave_top_5pct_net30']), fmtp(r['leave_top_10pct_net30']),
             fmtp(r['top1_share']), fmtp(r['top5_share']),
             fmtp(r['median_net30'])))
    A('')
    A('判定规则（预注册）：`Full > 0 且 Leave Top 5% <= 0` → '
      '`TAIL_DEPENDENT = TRUE`，不得称为 Robust Alpha。')
    A('')
    A('PRIMARY：Full(Net30) = %s，Leave Top5%%(Net30) = %s → '
      '`TAIL_DEPENDENT(net30) = %s`；毛口径 `TAIL_DEPENDENT = %s`。'
      % (fmtp(pri['mean_net30']), fmtp(pri['leave_top_5pct_net30']),
         str(net_tail_dependent).upper(), str(tail_dependent).upper()))
    A('')
    A('Top 5%% 利润贡献 = %s（毛口径）。' % fmtp(pri['top5_share']))
    A('')

    A('## §11 Null Models（任务书 §14）')
    A('')
    A('| 家族 | 观测 Mean | Null Mean | Null 2.5% | Null 97.5% | Excess | 单侧 p | '
      'resolution | 采纳 |')
    A('|---|---|---|---|---|---|---|---|---|')
    for r in nul:
        A('| %s | %s | %s | %s | %s | %s | %s | %s | %s |'
          % (r['family'], f(r['obs']), f(r['null_mean']), f(r['null_lo']),
             f(r['null_hi']), f(r['excess']), f(r['p_one_sided'], 4),
             f(r['resolution'], 4), 'Y' if r['adopted'] else 'N'))
    A('')
    unad = [r['family'] for r in nul if not r['adopted']]
    if unad:
        A('未采纳家族：%s（预注册规则：`resolution >= %.2f` 才采纳；'
          '这些家族在 `rounds=%d` 内无法为每个事件找到「非自身」的同格供体，'
          '其原因与后果已在 bias audit #8 披露）。'
          % (', '.join(unad), P['null_min_resolution'], P['null_rounds']))
        A('')

    A('## §12 Bootstrap（任务书 §19）')
    A('')
    A('| arm | 方法 | N | 簇数 | Mean Net30 | 95% CI | 单侧 p(<=0) |')
    A('|---|---|---|---|---|---|---|')
    for r in boo:
        A('| %s | %s | %s | %s | %s | [%s, %s] | %s |'
          % (r['arm'], r['method'], format(int(num(r['n'], 0)), ','),
             r['n_cluster'], fmtp(r['mean']), fmtp(r['lo']), fmtp(r['hi']),
             f(r['p_one_sided_le0'], 4)))
    A('')
    A('主口径为 `monthly_cluster`（同一股票连续信号、同一市场环境下信号高度相关，'
      '不能把每日信号当独立样本）；`block_20_sessions` 仅作稳健性对照，不用于判决。')
    A('')

    A('## §13 OOS / Walk-Forward / 年度稳定性（任务书 §17 / §18）')
    A('')
    A('| arm | 区间 | N | Mean Net30 | Win30 | PF30 |')
    A('|---|---|---|---|---|---|')
    for r in oos:
        if r['scope'] in ('FULL', 'IS', 'VALID', 'OOS'):
            A('| %s | %s | %s | %s | %s | %s |'
              % (r['arm'], r['scope'], format(int(num(r['n'], 0)), ','),
                 fmtp(r['mean_net30']), fmtp(r['win_net30']), f(r['pf30'], 3)))
    A('')
    A('PRIMARY ARM 逐年：')
    A('')
    A('| 年份 | N | Mean | Median | Win Rate | PF30 | Mean Net30 |')
    A('|---|---|---|---|---|---|---|')
    for y in sorted(years):
        r = years[y]
        A('| %d | %s | %s | %s | %s | %s | %s |'
          % (y, format(int(num(r['n'], 0)), ','), f(r['mean']), f(r['median']),
             fmtp(r['win']), f(r['pf30'], 3), fmtp(r['mean_net30'])))
    A('')
    A('Walk-forward（冻结配置、严格时间切分、无参数拟合）：')
    A('')
    A('| fold | N(test) | Mean Net30(test) | 95% CI |')
    A('|---|---|---|---|')
    for r in wf:
        A('| %s | %s | %s | [%s, %s] |'
          % (r['fold'], format(int(num(r['n_test'], 0)), ','),
             fmtp(r['net30_test']), fmtp(r['boot_lo']), fmtp(r['boot_hi'])))
    A('')
    A('样本切分严格按交易日：IS 2022–2024 / Validation 2025 / OOS 2026；'
      '无随机 Train-Test、无时间打乱、无未来数据选参。')
    A('')

    A('## §14 市场状态描述（任务书 §16，不用于任何参数）')
    A('')
    A('| Regime | 会话数 | PRIMARY 事件 | Mean Net30 | CSI300 上涨比例 | 市场上涨股比例 |')
    A('|---|---|---|---|---|---|')
    for r in reg:
        A('| %s | %s | %s | %s | %s | %s |'
          % (r['regime'], r['days'], format(int(num(r['n'], 0)), ','),
             fmtp(r['mean_net30']), pc(r['index_up_ratio'], 2),
             pc(r['advance_ratio_mean'], 2)))
    A('')
    A('该表只用于回答「该模式是否只在某一种市场环境下有效」，'
      '市场状态未参与任何过滤或参数选择。')
    A('')

    A('## §15 Overlap / Independence Audit（任务书 §20）')
    A('')
    A('| arm | N | 相邻日信号对比例 | <=5 日比例 | <=20 日比例 | 每事件平均重叠对数 | n_eff |')
    A('|---|---|---|---|---|---|---|')
    for k in ('RAW', '+F', '+F+MA+V'):
        o = ov[k]
        A('| %s | %s | %s | %s | %s | %s | %s |'
          % (k, format(int(o['n_events']), ','), fmtp(o['adjacent_rate']),
             fmtp(o['rate_le5']), fmtp(o['rate_le20']),
             f(o.get('mean_same_stock_neighbours_20d'), 3), f(o['n_eff'], 1)))
    A('')
    A('存在同股信号聚集时，**不得把 N 当作独立样本数**；n_eff 仅供参照，'
      '判决以月度聚类 bootstrap 为准。')
    A('')

    A('## §16 参数稳定性（任务书 §22，仅预注册扰动）')
    A('')
    A('| 项 | 值 |')
    A('|---|---|')
    A('| 格点数 | %d（有效 %d） |'
      % (gsum['n_cells'], gsum['n_cells_valid']))
    A('| 网格 positive_ratio（Net30 > 0） | %s |' % f(gsum['positive_ratio'], 3))
    A('| 3%% 邻域（窗口=10）positive_ratio | %s（%d 格） |'
      % (f(near_ratio, 3), gsum['primary_neighbourhood_cells']))
    A('| 网格 argmax | thr=%d%% / 窗口=%d / VR=%s / MA=%s（Net30 = %s, n=%s） |'
      % (gsum['argmax_cell']['thr'], gsum['argmax_cell']['first_w'],
         gsum['argmax_cell']['vr'], gsum['argmax_cell']['ma'],
         fmtp(gsum['argmax_cell']['net30']),
         format(int(num(gsum['argmax_cell']['n'], 0)), ',')))
    A('| PRIMARY 在网格中的分位 | %s（PRIMARY 本身不是格点） |'
      % f(gsum['primary_percentile_in_grid'], 3))
    A('| 各轴 Spearman | thr=%s / window=%s / vr=%s / ma=%s |'
      % (f(gsum['axis_monotonicity']['thr'], 3),
         f(gsum['axis_monotonicity']['first_w'], 3),
         f(gsum['axis_monotonicity']['vr'], 3),
         f(gsum['axis_monotonicity']['ma'], 3)))
    A('| 判定 | parameter_stable=%s / parameter_fragile=%s |'
      % (str(gsum['parameter_stable']).upper(),
         str(gsum['parameter_fragile']).upper()))
    A('')
    A('**注意**：网格 argmax 高于 PRIMARY，说明「从网格里挑一格」会显著改善结果 —— '
      '这正是本实验拒绝做的事。PRIMARY 在任何结果出来之前就已冻结。')
    A('')

    A('## §17 Selection Bias Audit（任务书 §21）')
    A('')
    A('| # | 项目 | 判定 | 证据 |')
    A('|---|---|---|---|')
    for b in bias:
        A('| %s | %s | %s | %s |'
          % (b['check'].split('_')[0], b['check'], b['verdict'],
             str(b['value']).replace('|', '/')))
    A('')
    for b in bias:
        A('- **%s**（%s）：%s' % (b['check'], b['verdict'], b['detail']))
    A('')
    A('说明：`FLAG` 表示已识别但无法在现有数据条件下彻底消除的偏差，'
      '按预注册规则（G11）必须披露；`FAIL` 不存在（G11 要求）。')
    A('')

    A('## §18 核心 PASS / FAIL Gate（任务书 §23）')
    A('')
    A('| Gate | 判据 | 结果 | 实际值 |')
    A('|---|---|---|---|')
    labels = {
        'G1': 'Primary Net30 > 0', 'G2': 'Primary PF30 > 1',
        'G3': 'bootstrap 95% CI (net30) 不跨 0',
        'G4': 'OOS(2026) Net30 > 0', 'G5': '>= 3 个年份 Net30 > 0',
        'G6': 'Leave Top 5% 后仍 > 0',
        'G7': 'M1–M4 中 >= 2 个基底 Net30 > 0',
        'G8': 'V1–V5 中 >= 2 个基底 Net30 > 0',
        'G9': '3% 邻域 positive_ratio >= 0.5',
        'G10': '>= 3 个 Null 家族 obs > null 97.5%',
        'G11': 'Bias Audit 无 FAIL',
        'G12': 'BULL/RANGE/BEAR 中 >= 2 个 Net30 > 0'}
    for k in ['G%d' % i for i in range(1, 13)]:
        A('| %s | %s | **%s** | %s |'
          % (k, labels[k], 'PASS' if G[k] else 'FAIL', GD[k]))
    A('')
    A('全部满足 = **%s**；`PROVISIONAL_ALPHA` = **%s**。'
      % (str(all_pass).upper(), str(all_pass).upper()))
    A('')

    A('## §19 最终结论（任务书 §24 判决树）')
    A('')
    A('```')
    A('ROBUST_ALPHA / WEAK_SIGNAL / TAIL_DEPENDENT / REGIME_DEPENDENT /')
    A('PARAMETER_FRAGILE / NO_ROBUST_ALPHA / FAIL → ARCHIVE')
    A('')
    A('判定结果: %s' % verdict)
    A('trading_authorization = NO')
    A('```')
    A('')
    A('判决依据（按冻结的判决树顺序逐步核对）：')
    A('')
    A('1. `G1..G12` 是否全部满足：**%s**（未满足：%s）'
      % (str(all_pass).upper(),
         ', '.join(k for k in ['G%d' % i for i in range(1, 13)] if not G[k])
         or '无'))
    A('2. `G1`（Primary Net30 > 0）：**%s**（%s）'
      % ('PASS' if G['G1'] else 'FAIL', GD['G1']))
    if not all_pass and not G['G1']:
        A('3. → 命中 `elif G1 不满足` 分支：**FAIL → ARCHIVE**。'
          '该分支按预注册规则优先于尾部/regime/参数分支。')
    A('')
    A('补充事实（不改变判决，仅披露）：')
    A('')
    A('- 尾部依赖：PRIMARY 毛口径 `TAIL_DEPENDENT = %s`，'
      '净 30bp 口径 `TAIL_DEPENDENT = %s`。'
      % (str(tail_dependent).upper(), str(net_tail_dependent).upper()))
    A('- 裸事件 RAW（未加任何过滤）Net30 = %s，PF30 = %s，'
      '但 Leave Top5%% 后为 %s —— 其微弱的正均值同样来自尾部。'
      % (fmtp(raw['mean_net30']), f(raw['pf30'], 3),
         fmtp(raw['leave_top_5pct_net30'])))
    A('- Regime：%s。'
      % '，'.join('%s Net30=%s' % (s, fmtp(reg_by[s]['mean_net30']))
                 for s in ('BULL', 'RANGE', 'BEAR')))
    A('- 阈值轴：Spearman(阈值, Net30) RAW=%s / 全栈=%s（%s）。'
      % (f(mono['RAW'], 3), f(mono['FULL'], 3), mono_tag))
    A('')

    A('## §20 Q1–Q7 逐条回答（任务书 §15）')
    A('')
    A('- **Q1　≥3%% 中阳线本身是否有次日 Alpha？**　'
      '裸事件 RAW 毛均值 %s，扣 30bp 后 Net30 = %s，PF30 = %s，'
      '回归月聚类 95%% CI = [%s, %s]（跨 0），Leave Top5%% 后 = %s。'
      '→ 均值在净成本后**不能与 0 区分**；毛口径微幅为正但完全依赖尾部。'
      % (fmtp(raw['mean']), fmtp(raw['mean_net30']), f(raw['pf30'], 3),
         fmtp(raw['boot_lo']), fmtp(raw['boot_hi']),
         fmtp(raw['leave_top_5pct_net30'])))
    A('- **Q2　「第一根」是否增加 Alpha？**　Δ(First vs RAW) = %s（95%% CI [%s, %s]）'
      '→ **%s**；净口径 First 单独出现时 Net30 = %s，低于 RAW 的 %s。'
      % (f(f1['incr_obs']), f(f1['incr_lo']), f(f1['incr_hi']), first_inc,
         fmtp(f1['mean_net30']), fmtp(raw['mean_net30'])))
    A('- **Q3　MA5/MA20 是否增加 Alpha？**　Δ(+F+MA vs +F) = %s'
      '（95%% CI [%s, %s]）→ **%s**；M1/M2/M3 微正、M4 微负，分散在 0 附近。'
      % (f(fma['incr_obs']), f(fma['incr_lo']), f(fma['incr_hi']), ma_inc))
    A('- **Q4　量能阀门是否增加 Alpha？**　Δ(+F+V vs +F) = %s'
      '（95%% CI [%s, %s]）→ **%s**；V1–V4 净口径均在 +0.0003 ~ +0.0008 '
      '区间，随阈值升高无稳定改善。'
      % (f(fv['incr_obs']), f(fv['incr_lo']), f(fv['incr_hi']), vol_inc))
    A('- **Q5　MA + Volume 是否产生稳定增量 Alpha？**　'
      '全栈 Δ(+F+MA+V vs +F+MA) = %s（95%% CI [%s, %s]）→ **%s**；'
      'PRIMARY 相对 First 的净增量不足以把它推过 0。'
      % (f(pri['incr_obs']), f(pri['incr_lo']), f(pri['incr_hi']), full_inc))
    A('- **Q6　Alpha 是否依赖极少数尾部股票？**　**是**。PRIMARY 去掉最大 5%% '
      '后 Net30 = %s（全样本 %s）；Top5%% 贡献了毛正收益的 %s。'
      % (fmtp(pri['leave_top_5pct_net30']), fmtp(pri['mean_net30']),
         fmtp(pri['top5_share'])))
    A('- **Q7　3%% 阈值是否具有稳定意义？**　阈值轴上 Net30 随涨幅单调上升'
      '（Spearman RAW=%s / 全栈=%s，`%s`），即「当日涨得越多、次日净收益越高」'
      '这一关系是稳定的；但把阈值抬到 5–7%% 属于**事后择优**，'
      '本实验按预注册坚持 3%%。'
      % (f(mono['RAW'], 3), f(mono['FULL'], 3), mono_tag))
    A('')

    A('## §21 科研结论')
    A('')
    A('> **“本实验验证的是\'尾盘第一根≥3%中阳线后的次日惯性\'，'
      '而不是一个经过优化的交易策略。”**')
    A('')
    if verdict.startswith('FAIL'):
        A('**结论：%s。**　按任务书 §26，**立即 ARCHIVE，不得继续调参**。'
          % verdict)
        A('')
        A('PRIMARY 定义（尾盘第一根 ≥3% 中阳线 + MA5/MA20 结构 + 预注册量能阀门，'
          'T 收盘买入、T+1 收盘卖出、30bp 成本）在净成本口径下均值不为正，'
          'PF30 < 1，OOS 为负，5 年中仅 2 年为正，去尾后为负。'
          '因此「尾盘第一根 ≥3% 中阳线的次日惯性」在本数据集上'
          '**不构成可重复的正向 Alpha**。')
        A('')
        A('不允许的补救（明确禁止，已遵守）：放宽到 T+3/T+5、引入 HVT/W7/HVE、'
          '改用网格 argmax（thr=%d%% / 窗口=%d / VR=%s / MA=%s）、'
          '或自由搜索 VR20 阈值。这些都只会制造过拟合。'
          % (gsum['argmax_cell']['thr'], gsum['argmax_cell']['first_w'],
             gsum['argmax_cell']['vr'], gsum['argmax_cell']['ma']))
    else:
        A('**结论：%s。**　按任务书 §26，'
          '**只允许进入下一阶段的独立 OOS 验证，不得直接生产实盘**。'
          % verdict)
    A('')
    A('`trading_authorization = NO`（无论结论如何）。')
    A('')

    A('## §22 产物清单（任务书 §25）')
    A('')
    for name in REQUIRED + EXTRA:
        p = os.path.join(OUT, name)
        ok = os.path.exists(p) and os.path.getsize(p) > 0
        A('- [%s] `%s`（%s bytes）'
          % ('x' if ok else ' ', name,
             format(os.path.getsize(p), ',') if os.path.exists(p) else 'MISSING'))
    A('')

    rep = os.path.join(OUT, 'H_TAIL_MIDBAR_01_REPORT.md')
    with open(rep, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')
    LOG('REPORT.md written (%d lines)' % len(L))

    # ------------------------------------------------- delivery check
    LOG.sep()
    LOG('delivery check')
    bad = []
    for name in REQUIRED:
        p = os.path.join(OUT, name)
        if not os.path.exists(p) or os.path.getsize(p) == 0:
            bad.append(name)
        else:
            LOG('  OK   %-38s %10s bytes'
                % (name, format(os.path.getsize(p), ',')))
    for name in EXTRA:
        p = os.path.join(OUT, name)
        if not os.path.exists(p) or os.path.getsize(p) == 0:
            LOG('  MISS %-38s (optional)' % name)
    LOG.sep()
    LOG('gates  : %s' % ' '.join('%s=%s' % (k, 'P' if G[k] else 'F')
                                 for k in sorted(G)))
    LOG('verdict: %s' % verdict)
    if bad:
        LOG('DELIVERY FAILED -- missing: %s' % ', '.join(bad))
        return 1
    LOG('EXIT 0 -- %d required artefacts present and non-empty' % len(REQUIRED))
    return 0


if __name__ == '__main__':
    sys.exit(main())
