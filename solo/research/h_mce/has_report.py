# -*- coding: utf-8 -*-
"""H-MCE-01 -- report + delivery check.

Reads H_MCE_01_RESULTS.json (produced by mce_run.py) and writes
multi_candle_event_report.md, then verifies that every artefact required by
SPEC section 20 exists.  Exit 0 only when the report is written and all
artefacts are present.
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, 'H_MCE_01_RESULTS.json')
REPORT = os.path.join(HERE, 'multi_candle_event_report.md')

CORE = ('E01', 'E02', 'E03', 'E04', 'E05', 'E06', 'E07', 'E08', 'E09', 'E10')
HSTAR = 5
COST = 30.0

HARD = ('multi_candle_event_definitions.json',
        'multi_candle_event_samples.csv',
        'multi_candle_event_results.csv',
        'multi_candle_event_oos.csv',
        'multi_candle_event_yearly.csv',
        'multi_candle_event_regime.csv',
        'multi_candle_event_parameter_stability.csv',
        'multi_candle_event_null_model.csv',
        'multi_candle_event_momentum_compare.csv',
        'multi_candle_event_report.md')
SOFT = ('H_MCE_01_SUMMARY.json', 'H_MCE_01_RESULTS.json',
        'H_MCE_01_BIAS_AUDIT.csv', 'H_MCE_01_BENCHMARK.csv',
        'H_MCE_01_INCREMENTAL.csv', 'H_MCE_01_SIZE.csv',
        'H_MCE_01_SURVIVOR.csv')

L = []          # report lines


def w(s=''):
    L.append(s)


def f(x, nd=4):
    if x is None:
        return 'NA'
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    if v != v:
        return 'nan'
    return ('%.' + str(nd) + 'f') % v


def fp(x, nd=1):
    if x is None:
        return 'NA'
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    if v != v:
        return 'nan'
    return ('%.' + str(nd) + 'f%%') % (100.0 * v)


def sgn(x, nd=4):
    s = f(x, nd)
    return s if s.startswith('-') else ('+' + s)


def pct(x, nd=1):
    return fp(x, nd)


def keyed(rows, *keys):
    d = {}
    for r in rows:
        d[tuple(r[k] for k in keys)] = r
    return d


def main():
    if not os.path.exists(RES):
        print('missing %s -- run mce_run.py first' % os.path.basename(RES))
        return 2
    with open(RES, 'r', encoding='utf-8') as fh:
        R = json.load(fh)

    S = R['summary']
    meta = R['meta']
    spec = R['spec']
    res = keyed(R['results'], 'event', 'horizon')
    oos = keyed(R['oos'], 'event', 'horizon', 'scope')
    yly = R['yearly']
    reg = keyed(R['regime'], 'event', 'regime', 'horizon')
    bmk = keyed(R['benchmark'], 'event', 'horizon', 'benchmark')
    mom = R['momentum']
    nulls = R['null_summary']
    stab = R['stability']['summary']
    judg = R['judgment']
    inc = R['incremental']
    bmA = R['bmA']

    # =================================================================
    w('# H-MCE-01  A股多K线价格—成交量组合事件的增量信息检验')
    w()
    w('Research ID: `%s`　Version: `%s`　Created: `%s`'
      % (spec['hypothesis_id'], spec['version'], spec['created']))
    w()
    w('SPEC SHA256: `%s`' % meta['spec_sha256'])
    w()
    w('面板: S=%d 只 × N=%d 交易日, `%s .. %s`'
      % (meta['S'], meta['N'], meta['panel_range'][0], meta['panel_range'][1]))
    w()
    w('研究窗口: `%s .. %s`（cols=%d, burn-in=%d）'
      % (meta['window'][0], meta['window'][1], meta['N'], meta['s0']))
    w()
    w('IS: `%s .. %s`　OOS: `%s .. %s`'
      % (meta['is'][0], meta['is'][1], meta['oos'][0], meta['oos'][1]))
    w()
    w('宇宙: `%s`（U1 主口径，剔除北交所）'
      % meta['universe'])
    w()
    w('成本场景: 0 / 15 / 30 / 50 bp，主口径 **30bp**；'
      '主 horizon `H* = T+5`；交易授权: `%s`'
      % meta['trading_authorization'])
    w()
    w('U1 window eligible cells = %d'
      % meta['n_eligible_cells_window'])
    w()
    w('\u2550' * 40)
    w()
    w('## 1. Executive Summary')
    w()
    w('- 研究事件数: **10**（E01–E10）')
    w('- 主检验数: **%d**（10 事件 × 5 horizon），BH-FDR q<%.2f 后显著: **%d**'
      % (S['n_primary_tests'], spec['fdr_q'], S['n_fdr_significant']))
    w('- OOS（2024–最新）Net30(T+5) > 0 的事件: **%d / 10**'
      % len(S['oos_positive_events']))
    w('- 扣除 30bp 成本后 Net30(T+5) > 0 的事件: **%d / 10**'
      % len(S['cost_positive_events_H5']))
    w('- ROBUST = %d　PROMISING = %d　FRAGILE = %d　NO EDGE = %d'
      % (S['counts']['ROBUST'], S['counts']['PROMISING'],
         S['counts']['FRAGILE'], S['counts']['NO EDGE']))
    w('- 整体结论: **%s**' % S['overall'])
    w()
    w('\u2500' * 40)
    w()

    # ---------------------------------------------------------------
    w('## 2. 未来函数审计（future_column_scan，任一失败即 FATAL）')
    w()
    w('| check | verdict | value |')
    w('|---|---|---|')
    for c in R['future_scan']:
        w('| %s | %s | %s |' % (c['check'], c['verdict'], c['value']))
    w()
    for c in R['future_scan']:
        w('- %s — %s' % (c['check'], c['detail']))
    w()
    w('\u2500' * 40)
    w()

    # ---------------------------------------------------------------
    w('## 3. Benchmark A（全 A 合格股票日的无条件均值）')
    w()
    w('| horizon | N | FULL | IS | OOS |')
    w('|---|---|---|---|---|')
    for h in ('1', '3', '5', '10', '20'):
        b = bmA[h]
        w('| T+%s | %d | %s | %s | %s |'
          % (h, b['n'], pct(b['FULL']), pct(b['IS']), pct(b['OOS'])))
    w()
    w('以上为**毛收益**均值（全样本、同 phase）。所有事件收益必须相对 30bp '
      '成本与上述基准阅读。')
    w()
    w('\u2500' * 40)
    w()

    # ---------------------------------------------------------------
    w('## 4. 十大事件结果（主 horizon T+5）')
    w()
    w('| Event | N | IS T+5 net30 | OOS T+5 net30 | OOS Win30 | 30bp 后 | '
      'Stability | Status |')
    w('|---|---|---|---|---|---|---|---|')
    for nm in CORE:
        r = res[(nm, 5)]
        st = stab[nm]
        tag = ('stable' if st['stable'] else
               ('FRAGILE' if st['fragile'] else 'mixed'))
        w('| %s | %d | %s | %s | %s | %s | %s (%.2f) | %s |'
          % (nm, r['n'], sgn(r['is_net30']), sgn(r['oos_net30']),
             pct(r['oos_win30']), sgn(r['net30']), tag,
             (st['positive_ratio'] if st['positive_ratio'] is not None else
              float('nan')), judg[nm]['verdict']))
    w()
    w('全 horizon 明细（毛收益 / 30bp 后 / 胜率 / 月度聚类 95% CI / FDR）：')
    w()
    w('| Event | H | N | Gross | Net30 | Win30 | 95% CI (net30) | t | '
      'raw p | FDR q | FDR sig |')
    w('|---|---|---|---|---|---|---|---|---|---|---|')
    for nm in CORE:
        for h in (1, 3, 5, 10, 20):
            r = res[(nm, h)]
            w('| %s | T+%d | %d | %s | %s | %s | [%s, %s] | %s | %s | %s | %s |'
              % (nm, h, r['n'], sgn(r['gross']), sgn(r['net30']),
                 pct(r['win30']),
                 sgn(r['boot_lo'] - COST / 10000.0),
                 sgn(r['boot_hi'] - COST / 10000.0),
                 f(r['tstat'], 2), f(r['raw_p'], 4), f(r['fdr_q'], 4),
                 'Y' if r['fdr_sig'] else '-'))
    w()
    w('MAE / MFE / 最大回撤（T+5 路径，毛）：')
    w()
    w('| Event | MAE | MFE | MaxDrawdown |')
    w('|---|---|---|---|')
    for nm in CORE:
        r = res[(nm, 5)]
        w('| %s | %s | %s | %s |' % (nm, sgn(r['mae']), sgn(r['mfe']),
                                     f(r['mdd'])))
    w()
    w('判决位（12 布尔位，+ 为真）：')
    w()
    w('| Event | b1 is | b2 oos | b3 full | b4 ci | b5 fdr | b6 yrs | b7 par | '
      'b8 reg | b9 null | b10 tail | b11 bench | b12 mom | 命中 | Verdict |')
    w('|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|')
    order = ['b1_is_pos', 'b2_oos_pos', 'b3_full_pos', 'b4_boot_ci_pos',
             'b5_fdr_sig', 'b6_years_consist', 'b7_param_stable',
             'b8_regime_ok', 'b9_null_ok', 'b10_not_tail', 'b11_beats_bench',
             'b12_momentum_incr']
    for nm in CORE:
        bits = judg[nm]['bits']
        cells = ' | '.join('+' if bits[k] else '-' for k in order)
        w('| %s | %s | %d/12 | %s |' % (nm, cells, judg[nm]['n_true'],
                                        judg[nm]['verdict']))
    w()
    w('b6 years_pos_frac（IS 2019–2023 年内 Net30(T+5)>0 的年份占比）：')
    w()
    w('| Event | ' + ' | '.join(CORE) + ' |')
    w('|---' * 11 + '|')
    w('| frac | ' + ' | '.join(f(judg[nm]['years_pos_frac'], 2)
                               for nm in CORE) + ' |')
    w()
    w('\u2500' * 40)
    w()

    # ---------------------------------------------------------------
    w('## 5. 三个基准比较（T+5，30bp 后）')
    w()
    w('| Event | Event net30 | A net30 | B net30 | C net30 | ΔB | ΔC |')
    w('|---|---|---|---|---|---|---|')
    for nm in CORE:
        r = res[(nm, 5)]
        w('| %s | %s | %s | %s | %s | %s | %s |'
          % (nm, sgn(r['net30']), sgn(r['benchA_net30']),
             sgn(r['benchB_net30']), sgn(r['benchC_net30']),
             sgn(r['deltaB_net30']), sgn(r['deltaC_net30'])))
    w()
    w('Benchmark A = 全 A 合格股票日；B = 相同**前置条件**但无该 K 线组合；'
      'C = 同股票且 |Δt|≤20 会话匹配。')
    w()
    w('> E10 的 Benchmark B 依规范退化为 A（E10 无「前置」部分），已在表中显式标注。')
    w()
    w('\u2500' * 40)
    w()

    # ---------------------------------------------------------------
    w('## 6. 年度稳定性（Net30(T+5) / 胜率 / 样本数）')
    w()
    years = sorted(set(r['year'] for r in yly))
    for nm in CORE:
        w('### %s' % nm)
        w()
        w('| ' + ' | '.join(str(y) for y in years) + ' |')
        w('|---' * len(years) + '|')
        row = []
        for y in years:
            hit = [r for r in yly
                   if r['event'] == nm and r['year'] == y and r['horizon'] == 5]
            row.append('%s (n=%d, w=%s)'
                       % (sgn(hit[0]['net30']), hit[0]['n'],
                          pct(hit[0]['win30'], 0)) if hit else 'NA')
        w('| ' + ' | '.join(row) + ' |')
        w()
    w('\u2500' * 40)
    w()

    # ---------------------------------------------------------------
    w('## 7. 市场状态（BULL / NEUTRAL / BEAR）')
    w()
    w('规则: `000300.SH` BULL = Index>MA20>MA60；BEAR = Index<MA20<MA60；'
      'else NEUTRAL。下表为 T+5 Net30。')
    w()
    w('| Event | BULL | NEUTRAL | BEAR |')
    w('|---|---|---|---|')
    for nm in CORE:
        row = []
        for lab in ('BULL', 'NEUTRAL', 'BEAR'):
            r = reg.get((nm, lab, 5))
            row.append('%s (n=%d)' % (sgn(r['net30']), r['n']) if r else 'NA')
        w('| %s | %s |' % (nm, ' | '.join(row)))
    w()
    w('\u2500' * 40)
    w()

    # ---------------------------------------------------------------
    w('## 8. 参数稳定性（预注册扰动格点，Net30(T+5) > 0 的比例）')
    w()
    w('| Event | cells | valid | positive_ratio | stable | fragile | '
      'baseline N | baseline net30 |')
    w('|---|---|---|---|---|---|---|---|')
    for nm in CORE:
        st = stab[nm]
        w('| %s | %d | %d | %s | %s | %s | %s | %s |'
          % (nm, st['n_cells'], st['n_valid'], f(st['positive_ratio'], 3),
             'Y' if st['stable'] else '-', 'Y' if st['fragile'] else '-',
             st['baseline_n'], sgn(st['baseline_net30'])))
    w()
    w('阈值: positive_ratio ≥ %.2f 记 stable；≤ %.2f 记 FRAGILE。'
      % (spec['param_stable_thr'], spec['param_fragile_thr']))
    w()
    w('> 注: E05 / E06 / E07 / E09 的扰动轴含其**确认阶段**阈值'
      '（pull / recover / vr12 / vr13），因此该 4 个事件的格点样本为「完整形态」'
      '样本；主表仍按 T 日信号口径报告。此差异为规范 §4 的阶段定义所致，已注明。')
    w()
    w('\u2500' * 40)
    w()

    # ---------------------------------------------------------------
    w('## 9. Null Model（随机对照，T+5）')
    w()
    w('| Event | obs | N1 null | N1 p | N2 null | N2 p | N2 adopted | '
      'N3 null | N3 p |')
    w('|---|---|---|---|---|---|---|---|---|')
    for nm in CORE:
        n1, n2, n3 = nulls[nm]['N1'], nulls[nm]['N2'], nulls[nm]['N3']
        w('| %s | %s | %s | %s | %s | %s | %s | %s | %s |'
          % (nm, sgn(n1['obs']), sgn(n1['null_mean']), f(n1['p_one_sided'], 4),
             sgn(n2['null_mean']), f(n2['p_one_sided'], 4),
             'Y' if (n2['resolution'] is not None and n2['resolution'] >= 0.99)
             else '-',
             sgn(n3['null_mean']), f(n3['p_one_sided'], 4)))
    w()
    w('N1 = 全样本 eligible cell 随机抽（股票×日期）；'
      'N2 = 保持 stock×年份×市场状态×前置涨幅桶、打乱 K 线标签；'
      'N3 = 同 stock 内随机打乱事件日。resolution 为匹配池命中率。')
    w()
    w('\u2500' * 40)
    w()

    # ---------------------------------------------------------------
    w('## 10. Momentum Decomposition（相对 MOM5/10/20 Top 十分位的增量）')
    w()
    for ww in (5, 10, 20):
        w('### MOM%d Top 十分位（同日横截面）' % ww)
        w()
        w('| Event | H | event gross | MOM%d gross | delta | event net30 |'
          ' delta net30 |' % ww)
        w('|---|---|---|---|---|---|---|')
        for nm in CORE:
            for h in (5, 10):
                hit = [r for r in mom if r['event'] == nm
                       and r['horizon'] == h and r['mom_window'] == ww]
                if not hit:
                    continue
                r = hit[0]
                w('| %s | T+%d | %s | %s | %s | %s | %s |'
                  % (nm, h, sgn(r['event_gross']), sgn(r['mom_topdecile_gross']),
                     sgn(r['delta_gross']), sgn(r['event_net30']),
                     sgn(r['delta_net30'])))
        w()
    w('\u2500' * 40)
    w()

    # ---------------------------------------------------------------
    w('## 11. 增量信息模型 A/B/C/D（R² 与 ΔR²）')
    w()
    w('A: fwd ~ MOM20；B: +volume_ratio_20；C: +event_dummy(10)；'
      'D: +regime_dummies。')
    w()
    w('| H | Model | N | R² | ΔR² |')
    w('|---|---|---|---|---|')
    for h in (5, 10):
        for r in inc['rows']:
            if r['horizon'] != h:
                continue
            w('| T+%d | %s | %d | %s | %s |'
              % (h, r['model'], r['n'], f(r['r2'], 6),
                 '-' if r['delta_r2'] is None else sgn(r['delta_r2'], 6)))
    w()
    w('Model C 中 event_dummy 系数与按日历月聚类的稳健 t 值：')
    w()
    w('| Event | coef(T+5) | t(T+5) | coef(T+10) | t(T+10) |')
    w('|---|---|---|---|---|')
    for nm in CORE:
        c5 = inc['detail']['5']['event_coef'][nm]
        c10 = inc['detail']['10']['event_coef'][nm]
        w('| %s | %s | %s | %s | %s |'
          % (nm, sgn(c5['coef'], 5), f(c5['t'], 2),
             sgn(c10['coef'], 5), f(c10['t'], 2)))
    w()
    w('\u2500' * 40)
    w()

    # ---------------------------------------------------------------
    w('## 12. 分股票维度（市值三分位）与尾部集中度')
    w()
    sizes = R['size']
    w('| Event | LARGE net30 | MID net30 | SMALL net30 | Top1% | Top5% | Top10% |')
    w('|---|---|---|---|---|---|---|')
    for nm in CORE:
        cell = {}
        for r in sizes:
            if r['event'] == nm:
                cell[r['size']] = r
        w('| %s | %s | %s | %s | %s | %s | %s |'
          % (nm, sgn(cell.get('LARGE', {}).get('net30')),
             sgn(cell.get('MID', {}).get('net30')),
             sgn(cell.get('SMALL', {}).get('net30')),
             fp(cell.get('TOP_1pc', {}).get('gross'), 1),
             fp(cell.get('TOP_5pc', {}).get('gross'), 1),
             fp(cell.get('TOP_10pc', {}).get('gross'), 1)))
    w()
    w('Top x% = 该分位正收益之和 / 全样本正收益之和（T+5，毛）。')
    w()
    w('\u2500' * 40)
    w()

    # ---------------------------------------------------------------
    w('## 13. 幸存者偏差审计（U1 vs U2）')
    w()
    sm = R['survivor']['meta']
    w('- 面板是否包含已退市股票: **保留其历史打印**（之后为 NaN）')
    w('- delist 点位标志: `%s`' % sm['delist_flag'])
    w('- 早停股票 gone = %d 只' % sm['gone_codes'])
    w('- 幸存者偏差: **%s**' % sm['survivorship_bias'])
    w()
    w('| Event | U1 N | U1 net30 | U2 N | U2 net30 | Δ net30 |')
    w('|---|---|---|---|---|---|')
    for r in R['survivor']['rows']:
        w('| %s | %d | %s | %d | %s | %s |'
          % (r['event'], r['u1_n'], sgn(r['u1_net30_t5']), r['u2_n'],
             sgn(r['u2_net30_t5']), sgn(r['delta_net30'])))
    w()
    w('\u2500' * 40)
    w()

    # ---------------------------------------------------------------
    w('## 14. 偏差与多重检验审计')
    w()
    w('| check | verdict | value |')
    w('|---|---|---|')
    for b in R['bias']:
        w('| %s | %s | %s |' % (b['check'], b['verdict'], b['value']))
    w()
    for b in R['bias']:
        w('- %s — %s' % (b['check'], b['detail']))
    w()
    w('\u2550' * 40)
    w()

    # ---------------------------------------------------------------
    # Q1..Q7
    def core_val(nm, field, h=5):
        return res[(nm, h)][field]

    is_ok = [nm for nm in CORE if core_val(nm, 'is_net30') > 0]
    oos_ok = [nm for nm in CORE if core_val(nm, 'oos_net30') > 0]
    cost_ok = [nm for nm in CORE if core_val(nm, 'net30') > 0]
    fdr_ok = [nm for nm in CORE if res[(nm, 5)]['fdr_sig']]
    bench_ok = [nm for nm in CORE if res[(nm, 5)]['deltaB_net30'] > 0
                and res[(nm, 5)]['deltaC_net30'] > 0]
    mom_ok = [nm for nm in CORE
              if inc['detail']['5']['event_coef'][nm]['coef'] > 0]
    mom_delta_ok = []
    for nm in CORE:
        hits = [r for r in mom if r['event'] == nm and r['horizon'] == 5]
        if hits and all(r['delta_net30'] > 0 for r in hits):
            mom_delta_ok.append(nm)

    w('## 15. 必答问题 Q1–Q7')
    w()
    w('### Q1　10 个多K线组合中，哪些在 IS 有效？')
    w()
    w('IS(2019-01..2023-12) Net30(T+5) > 0 的事件: %s'
      % ('、'.join(is_ok) if is_ok else '**无**'))
    w()
    w('| Event | IS N | IS gross | IS net30 | IS win30 |')
    w('|---|---|---|---|---|')
    for nm in CORE:
        r = res[(nm, 5)]
        w('| %s | %d | %s | %s | %s |'
          % (nm, r['is_n'], sgn(r['is_gross']), sgn(r['is_net30']),
             pct(r['is_win30'])))
    w()
    w('### Q2　哪些在 OOS 仍然有效？')
    w()
    w('OOS(2024-01..20260924) Net30(T+5) > 0 的事件: %s'
      % ('、'.join(oos_ok) if oos_ok else '**无**'))
    w()
    w('| Event | OOS N | OOS gross | OOS net30 | OOS win30 |')
    w('|---|---|---|---|---|')
    for nm in CORE:
        r = res[(nm, 5)]
        w('| %s | %d | %s | %s | %s |'
          % (nm, r['oos_n'], sgn(r['oos_gross']), sgn(r['oos_net30']),
             pct(r['oos_win30'])))
    w()
    w('### Q3　哪些扣除 30bp 成本后仍然有效？')
    w()
    w('FULL Net30(T+5) > 0 的事件: %s'
      % ('、'.join(cost_ok) if cost_ok else '**无**'))
    w()
    w('同时通过 BH-FDR(q<%.2f) 的事件: %s'
      % (spec['fdr_q'], '、'.join(fdr_ok) if fdr_ok else '**无**'))
    w()
    w('| Event | Gross(T+5) | Net0 | Net15 | Net30 | Net50 |')
    w('|---|---|---|---|---|---|')
    for nm in CORE:
        r = res[(nm, 5)]
        w('| %s | %s | %s | %s | %s | %s |'
          % (nm, sgn(r['gross']), sgn(r['net0']), sgn(r['net15']),
             sgn(r['net30']), sgn(r['net50'])))
    w()
    w('### Q4　哪些只是传统技术分析看起来漂亮、但实际上没有增量信息？')
    w()
    no_incr = [nm for nm in CORE if nm not in bench_ok]
    w('相对「相同前置条件」（Benchmark B）或「同股票相近时间」（Benchmark C）'
      '没有正增量的事件: %s'
      % ('、'.join(no_incr) if no_incr else '**无**'))
    w()
    w('其中 FULL 为正（形态看似「漂亮」）却未能同时胜过 B 与 C 的: %s'
      % ('、'.join(nm for nm in cost_ok if nm not in bench_ok) or '**无**'))
    w()
    w('### Q5　哪些形态其实只是 Momentum 的另一种表达？')
    w()
    w('控制 MOM20 后 event_dummy 系数 ≤ 0 的事件: %s'
      % ('、'.join(nm for nm in CORE if nm not in mom_ok) or '**无**'))
    w()
    w('MOM5/10/20 三个窗口、T+5 的 delta_net30 全部 ≤ 0（即被动量十分位反向覆盖）'
      '的事件: %s'
      % ('、'.join(nm for nm in CORE if nm not in mom_delta_ok) or '**无**'))
    w()
    w('### Q6　哪些形态在加入 MA20 / MA60 / Volume / Market Regime 以后才有效？')
    w()
    w('以「T 日信号」与「其确认阶段（E0x_CONF）」在 OOS 的 net30 对比作为证据'
      '（确认阶段嵌入了量能/均线/后续结构信息）：')
    w()
    w('| T 日事件 | OOS net30 | 确认事件 | OOS net30 | 结论 |')
    w('|---|---|---|---|---|')
    pairs = [('E01', 'E02'), ('E03', 'E03_CONF'), ('E04', 'E04_CONF'),
             ('E05', 'E05_CONF'), ('E06', 'E06_CONF'), ('E07', 'E07_CONF'),
             ('E08', 'E08_CONF'), ('E09', 'E09_CONF'), ('E10', 'E10_CONF')]
    for a, b in pairs:
        ra = oos.get((a, 5, 'OOS'))
        rb = oos.get((b, 5, 'OOS'))
        if not ra or not rb:
            continue
        concl = ('T 日即有效' if ra['net30'] > 0 else
                 ('仅确认后有效' if rb['net30'] > 0 else '两者皆无效'))
        w('| %s | %s | %s | %s | %s |'
          % (a, sgn(ra['net30']), b, sgn(rb['net30']), concl))
    w()
    w('（E02 是 E01 的确认事件，故与 E01 配对。）')
    w()
    w('### Q7　哪些形态真正提供了相对于简单动量策略的增量信息？')
    w()
    both = [nm for nm in CORE if nm in mom_ok and nm in bench_ok]
    w('同时满足 (a) 控制 MOM20 后 event_dummy 系数 > 0，'
      '(b) 胜过 Benchmark B 与 C 的事件: %s'
      % ('、'.join(both) if both else '**无**'))
    w()
    w('\u2550' * 40)
    w()

    # ---------------------------------------------------------------
    w('## 16. 最终结论（科研语言）')
    w()
    for nm in CORE:
        w('- **%s**: %s（命中 %d/12）'
          % (nm, judg[nm]['verdict'], judg[nm]['n_true']))
    w()
    if S['counts']['ROBUST'] == 0 and S['counts']['PROMISING'] == 0:
        w('> Evidence **does not support** a stable, repeatable, OOS-valid '
          'incremental return advantage for any of the ten strictly defined '
          'multi-candle price–volume events, beyond simple momentum, volume '
          'and market regime.')
        w('>')
        w('> **未发现稳定优势**（STOP；不为这 10 个形态继续调参优化）。')
    else:
        w('> Evidence **partially supports** the following candidate(s); they '
          'require an independent phase-2 study before any trading use.')
        w()
        for nm in CORE:
            if judg[nm]['verdict'] in ('ROBUST', 'PROMISING'):
                w('- `%s-%s` → %s' % (spec['hypothesis_id'], nm,
                                      judg[nm]['verdict']))
    w()
    w('必须区分: **形态本身有效** 与 **形态只是 Momentum / Volume 的代理变量**。'
      '本报告第 10、11、15-Q5、15-Q7 节给出的即是这一区分的证据。')
    w()
    w('`trading_authorization = %s`。本阶段**禁止**直接把任何形态转化为实盘 BUY 信号；'
      '第二阶段才允许研究 Entry / Exit / T+3 / T+5 / Position sizing / Execution。'
      % meta['trading_authorization'])
    w()
    w('\u2500' * 40)
    w()

    # ---------------------------------------------------------------
    w('## 17. 交付校验')
    w()
    ok = True
    w('| artefact | status |')
    w('|---|---|')
    for fn in HARD:
        # the report itself is written below; verify it after the write
        e = (True if fn == os.path.basename(REPORT)
             else os.path.exists(os.path.join(HERE, fn)))
        ok = ok and e
        w('| %s | %s |' % (fn, 'OK' if e else 'MISSING'))
    w()
    w('补充产物:')
    w()
    for fn in SOFT:
        e = os.path.exists(os.path.join(HERE, fn))
        ok = ok and e
        w('- %s: %s' % (fn, 'OK' if e else 'MISSING'))
    w()

    with open(REPORT, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')

    ok = ok and os.path.exists(REPORT)

    print('report written: %s  (%d lines)'
          % (os.path.basename(REPORT), len(L)))
    print('artefacts: %d hard + %d supplementary'
          % (len(HARD), len(SOFT)))
    if not ok:
        print('DELIVERY CHECK FAILED')
        return 1
    print('DELIVERY CHECK PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
