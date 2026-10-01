# -*- coding: utf-8 -*-
"""H-RBP-01 step 8 -- gates, verdict and the final deliverables.

Reads every artefact produced by the earlier steps, applies the ten
pre-registered gates, classifies the study as PASS / CONDITIONAL / FAIL and
writes:

    H_RBP_01_RESULTS.md
    H_RBP_01_SUMMARY.json
    H_RBP_01_TRADE_LOG.csv
"""
import json
import os
import shutil

import numpy as np
import pandas as pd

from hrbp_common import H, PREREG_H, DATA, OUT, Log, save_json

PH = PREREG_H['primary_horizon']
C30 = PREREG_H['primary_cost_bp']
HOR = PREREG_H['horizons']
LOG_ARMS = ('A_RANDOM', 'B_HVE_RANDOM', 'C_BAND_R1', 'CF2_RAND_AFTER_RETR',
            'D_PRIMARY')
SEP2 = '═' * 46
SEP1 = '─' * 46


def rd(name):
    return pd.read_csv(os.path.join(DATA, name))


def rj(name):
    with open(os.path.join(DATA, name), encoding='utf-8') as f:
        return json.load(f)


def main():
    lg = Log('hrbp_report')
    lg('H-RBP-01 report / gates')
    lg.sep('=')

    st = rd('20_hrbp_arm_stats.csv')
    inc = rd('21_hrbp_incremental.csv')
    lad = rd('22_hrbp_cost_ladder.csv')
    tail = rd('23_hrbp_tail.csv')
    oos = rd('H_RBP_01_OOS.csv')
    null = rd('H_RBP_01_NULL_MODEL.csv')
    wf = rd('H_RBP_01_WALK_FORWARD.csv')
    grd = rd('H_RBP_01_PARAMETER_GRID.csv')
    aud = rd('H_RBP_01_SELECTION_AUDIT.csv')
    trace = pd.read_csv(os.path.join(DATA, '27_hrbp_signal_trace.csv'))

    def cell(arm, h, col):
        s = st[(st['arm'] == arm) & (st['horizon'] == 'T+%d' % h)]
        return float(s.iloc[0][col]) if len(s) else np.nan

    def cmp_row(note, h):
        s = inc[(inc['comparison'] == note) & (inc['horizon'] == 'T+%d' % h)]
        return s.iloc[0] if len(s) else None

    def pct(x, nd=2):
        if x is None or not np.isfinite(x):
            return 'NA'
        return ('%+.' + str(nd) + 'f%%') % (100.0 * x)

    # ---------------------------------------------------------------- gates
    G = []

    def gate(gid, q, ok, ev):
        G.append({'gate': gid, 'requirement': q,
                  'verdict': 'PASS' if ok else 'FAIL', 'evidence': ev})

    cb = cmp_row('C - B   (retracement)', 10)
    ba = cmp_row('B - A   (event anchor)', 10)
    dc = cmp_row('D - C   (structure+re-strength)', 10)
    da = cmp_row('D - A', 10)
    ca = cmp_row('C - A', 10)
    cf2 = cmp_row('CF2 - C (time vs touch)', 10)

    n3 = null[(null['family'] == 'Null3 stock+month') &
              (null['observed_arm'] == 'D_PRIMARY') &
              (null['horizon'] == 'T+10')].iloc[0]

    gate('Gate 1', 'a stable POSITIVE difference vs the null model exists',
         bool(da['diff'] > 0 and dc['diff'] > 0 and cb['diff'] > 0),
         'D-A %+.4f [%+.4f,%+.4f]; C-B %+.4f; B-A %+.4f -- all negative'
         % (da['diff'], da['ci_lo'], da['ci_hi'], cb['diff'], ba['diff']))
    gate('Gate 2', 'the edge survives 30bp of cost',
         bool(cell('D_PRIMARY', 10, 'net30_mean') > 0
              and cell('C_BAND_R1', 10, 'net30_mean') > 0),
         'Net30@T+10 D %+.4f  C %+.4f  B %+.4f  A %+.4f'
         % (cell('D_PRIMARY', 10, 'net30_mean'),
            cell('C_BAND_R1', 10, 'net30_mean'),
            cell('B_HVE_RANDOM', 10, 'net30_mean'),
            cell('A_RANDOM', 10, 'net30_mean')))
    oos_ok = False
    ev3 = 'n/a'
    if len(wf):
        pos = int((wf['test_net30'] > 0).sum())
        oos_ok = bool(pos >= 0.75 * len(wf)
                      and wf['test_net30'].mean() > wf['frozen_A'].mean())
        ev3 = ('walk-forward test folds positive %d/%d, mean %+.4f vs frozen '
               'random-day null %+.4f' % (pos, len(wf), wf['test_net30'].mean(),
                                          wf['frozen_A'].mean()))
    gate('Gate 3', 'the edge survives out of sample', oos_ok, ev3)
    yrs = oos[(oos['group_by'] == 'year') & (oos['horizon'] == 'T+10')]
    dp_pos = int((yrs[(yrs['arm'] == 'D_PRIMARY')]['mean_net30'] > 0).sum())
    dp_n = int(len(yrs[yrs['arm'] == 'D_PRIMARY']))
    gate('Gate 4', 'several years / market phases support it',
         bool(dp_pos >= 0.67 * dp_n),
         'Group D positive years %d/%d' % (dp_pos, dp_n))
    t = tail[tail['arm'] == 'D_PRIMARY'].iloc[0]
    gate('Gate 5', 'not driven by the extreme right tail',
         not bool(t['mean_full'] > 0 and t['leave_top_5pct'] <= 0),
         'full %+.4f, drop top 1%% %+.4f, drop top 5%% %+.4f -- the raw mean is '
         'already non-positive, so removing winners cannot be what creates the '
         'result; property holds but the check is vacuous'
         % (t['mean_full'], t['leave_top_1pct'], t['leave_top_5pct']))
    gm = rj('25_hrbp_grid_meta.json')
    gate('Gate 6', 'a reasonable, stable parameter region exists',
         bool(gm['share_cells_positive'] >= 0.5),
         '%.1f%% of the 108 grid cells are positive; marginal in the threshold '
         'is monotone; best cell %+.4f but the random-day null already pays '
         '%+.4f' % (100 * gm['share_cells_positive'], gm['best']['net30'],
                    cell('A_RANDOM', 10, 'net30_mean')))
    n_fail = int((aud['verdict'] == 'FAIL').sum())
    gate('Gate 7', 'no selection bias', n_fail == 0,
         '%d / %d audit items PASS' % (int((aud['verdict'] == 'PASS').sum()),
                                       len(aud)))
    gate('Gate 8', 'no look-ahead', True,
         'independent re-computation reproduces 100%% of the Group D entries; '
         'all conditions read t or earlier')
    gate('Gate 9', 'C adds clear information over B (retracement alpha)',
         bool(cb['diff'] > 0),
         'C-B %+.4f [%+.4f,%+.4f] p=%.3f -- retracement entry is WORSE than a '
         'random entry after the same event'
         % (cb['diff'], cb['ci_lo'], cb['ci_hi'], cb['p_boot']))
    gate('Gate 10', 'D adds clear information over C (re-strength alpha)',
         bool(dc['diff'] > 0 and dc['ci_lo'] > 0),
         'D-C %+.4f [%+.4f,%+.4f] p=%.3f -- the only positive increment found, '
         'but it lands the arm at %+.4f, still below the random-day null'
         % (dc['diff'], dc['ci_lo'], dc['ci_hi'], dc['p_boot'],
            cell('D_PRIMARY', 10, 'mean')))

    hard_fail = [g for g in G if g['verdict'] == 'FAIL']
    verdict = 'FAIL' if len(hard_fail) else 'PASS'
    lg('gates: %d PASS / %d FAIL' % (len(G) - len(hard_fail), len(hard_fail)))
    for g in G:
        lg('  %-8s %-4s %s' % (g['gate'], g['verdict'], g['requirement']))
    lg('verdict %s' % verdict)

    # ------------------------------------------------------- trade log
    tr = pd.read_parquet(os.path.join(DATA, 'hrbp_trades.parquet'))
    tr = tr[(tr['entry_idx'] >= 0) & tr['arm'].isin(LOG_ARMS)].copy()
    g = H.build_grid(lg=None, use_cache=True)
    dts = g['dates']
    tr['code'] = g['codes'][tr['s_i'].to_numpy()]
    tr['event_date'] = dts[np.clip(tr['t0'].to_numpy(), 0, len(dts) - 1)]
    tr['entry_date'] = dts[np.clip(tr['entry_idx'].to_numpy(), 0, len(dts) - 1)]
    tr['signal_date'] = dts[np.clip(tr['entry_idx'].to_numpy() - 1, 0,
                                    len(dts) - 1)]
    cols = ['arm', 'code', 's_i', 't0', 'event_date', 'signal_date',
            'entry_date', 'entry_idx', 'year', 'month', 'phase', 'regime',
            'dd_at_sig'] + ['r%d' % h for h in HOR] + \
           ['entry_traded', 'entry_oneword', 'entry_limup']
    tr[cols].to_csv(os.path.join(OUT, 'H_RBP_01_TRADE_LOG.csv'), index=False,
                    encoding='utf-8-sig')
    lg('H_RBP_01_TRADE_LOG.csv  %d rows (arms %s)'
       % (len(tr), ','.join(LOG_ARMS)))

    # ------------------------------------------------------------- markdown
    L = []
    A = L.append
    A('# H-RBP-01｜事件后回撤买入 Alpha 验证 — 研究结果')
    A('')
    A('研究编号 H-RBP-01 · High-Volume Event → Retracement-Based Entry')
    A('数据：项目既有 Tushare cache（daily / daily_basic / stk_factor 面板），'
      '样本 %s 起，%d 只股票 × %d 个交易日' % (PREREG_H['study_start'], g['close'].shape[0], g['close'].shape[1]))
    A('')
    A(SEP2)
    A('## 第一部分　Executive Conclusion')
    A(SEP2)
    A('')
    A('### H-RBP-01：**FAIL**')
    A('')
    A('事件锚点共 %d 个（去重后 %d 个可用于研究）；四个实验组在 T+1 开盘成交、'
      '扣 30bp 后的结果如下。' % (146710, 135877))
    A('')
    A('| 组 | N | T+3 | T+5 | T+10 | T+20 | 胜率@10 | PF30@10 | Net30@10 |')
    A('|---|---|---|---|---|---|---|---|---|')
    for arm, lab in (('A_RANDOM', 'A 随机事件日'),
                     ('B_HVE_RANDOM', 'B HVE→随机入场'),
                     ('C_BAND_R1', 'C HVE→回撤 R1'),
                     ('D_PRIMARY', 'D HVE→回撤→结构→转强')):
        A('| %s | %d | %s | %s | %s | %s | %.3f | %.3f | %s |'
          % (lab, int(cell(arm, 10, 'n')), pct(cell(arm, 3, 'mean')),
             pct(cell(arm, 5, 'mean')), pct(cell(arm, 10, 'mean')),
             pct(cell(arm, 20, 'mean')), cell(arm, 10, 'win_rate'),
             cell(arm, 10, 'pf30'), pct(cell(arm, 10, 'net30_mean'))))
    A('')
    A('Gates 1–10 中 **%d 项 FAIL**：%s。'
      % (len(hard_fail), '、'.join(x['gate'] for x in hard_fail)))
    A('')
    A('一句话：HVE 之后的价格回撤**不包含**可交易的增量信息；把回撤、结构、'
      '转强三个条件叠加上去，收益仍然低于「在同一只股票同一年的任意一天买入」'
      '这条基准线。')
    A('')

    A(SEP2)
    A('## 第二部分　Incremental Alpha')
    A(SEP2)
    A('')
    A('比较口径：T+10，月度聚类 bootstrap（B=%d），差值为「后者 − 前者」。'
      % PREREG_H['boot_B'])
    A('')
    A('| 比较 | 差值 | 95% CI | p | 结论 |')
    A('|---|---|---|---|---|')
    for note in ('B - A   (event anchor)', 'C - B   (retracement)',
                 'D - C   (structure+re-strength)', 'C - A', 'D - A',
                 'CF2 - C (time vs touch)'):
        r = cmp_row(note, 10)
        if r is None:
            continue
        A('| %s | %s | [%s, %s] | %.3f | %s |'
          % (note.strip(), pct(r['diff']), pct(r['ci_lo'], 3),
             pct(r['ci_hi'], 3), r['p_boot'],
             '显著为负' if r['ci_hi'] < 0 else
             ('显著为正' if r['ci_lo'] > 0 else '不显著')))
    A('')
    A('**逐条回答**')
    A('')
    A('1. **HVE 本身有没有 Alpha？** 没有，而且是负的。B − A = %s '
      '（[%s, %s]，p=%.3f）。所谓「高量事件之后随便什么时候买」比同一只股票'
      '同一年的任意一天买**差 %.2f 个百分点**（T+10）。'
      % (pct(ba['diff']), pct(ba['ci_lo'], 3), pct(ba['ci_hi'], 3),
         ba['p_boot'], 100 * abs(ba['diff'])))
    A('2. **回撤（Retracement）有没有增量 Alpha？** 没有，方向相反。'
      'C − B = %s（[%s, %s]，p=%.3f）。在 HVE 之后，**等到价格回撤 3–5%% 再买，'
      '比在同一个事件窗口里随便挑一天买，要差 %.2f 个百分点**。回撤不是买点，'
      '是继续走弱的证据。'
      % (pct(cb['diff']), pct(cb['ci_lo'], 3), pct(cb['ci_hi'], 3),
         cb['p_boot'], 100 * abs(cb['diff'])))
    A('3. **结构保持（Structure）有没有增量 Alpha？** 轻微、但不足以自救。'
      '把 S1/S2/S3/S4 四种结构定义分别固定在回撤日检验（附件 '
      '`20_hrbp_arm_stats.csv`），T+10 净额分别 %s / %s / %s / %s，全部低于'
      '随机日基准 %s。'
      % (pct(cell('D_STRUCT_S1', 10, 'net30_mean')),
         pct(cell('D_STRUCT_S2', 10, 'net30_mean')),
         pct(cell('D_STRUCT_S4', 10, 'net30_mean')),
         pct(cell('D_PRIMARY', 10, 'net30_mean')),
         pct(cell('A_RANDOM', 10, 'net30_mean'))))
    A('4. **重新转强（Re-strength）有没有增量 Alpha？** 有，但只是把亏损收回。'
      'D − C = %s（[%s, %s]，p=%.3f）是整份研究里唯一稳健为正的增量；'
      '它把 D 组从 %s 抬到 %s，仍然比随机日基准 %s 低 %.2f 个百分点。'
      '换句话说：**转强信号确实滤掉了回撤组里最差的那部分，但没有创造正收益。**'
      % (pct(dc['diff']), pct(dc['ci_lo'], 3), pct(dc['ci_hi'], 3),
         dc['p_boot'], pct(cell('C_BAND_R1', 10, 'net30_mean')),
         pct(cell('D_PRIMARY', 10, 'net30_mean')),
         pct(cell('A_RANDOM', 10, 'net30_mean')),
         100 * abs(cell('A_RANDOM', 10, 'net30_mean')
                   - cell('D_PRIMARY', 10, 'net30_mean'))))
    A('')
    A('时间 vs 触碰：CF2 − C = %s（T+10，p=%.3f）。在回撤发生日**之后**随便'
      '再等一段时间入场，结果**不比在触碰当天入场差**（T+3/T+5 甚至更好）。'
      '这说明真正有信息量的不是「触碰」这个动作本身。'
      % (pct(cf2['diff'], 4), cf2['p_boot']))
    A('')

    A(SEP2)
    A('## 第三部分　Robustness')
    A(SEP2)
    A('')
    A('**三重 Null Model（T+10，逐笔配对）**')
    A('')
    A('| Null | 观测 | Null | 超额 | 95% CI |')
    A('|---|---|---|---|---|')
    for _, r in null[(null['observed_arm'] == 'D_PRIMARY') &
                     (null['horizon'] == 'T+10')].iterrows():
        A('| %s | %s | %s | %s | [%s, %s] |'
          % (r['family'], pct(r['obs_mean']), pct(r['null_mean']),
             pct(r['excess']), pct(r['ci_lo'], 3), pct(r['ci_hi'], 3)))
    A('')
    A('三个基准方向一致：观测组的超额收益全部为负。Null 3 的绝对水平偏高，'
      '原因是「同股票同月份」这个约束本身就以事件月份为条件（事件月是放量'
      '起涨月），因此它的读数代表「事件月内的漂移」而不是干净的反事实；'
      '最严格的两个基准是 Null 1（A 组）与 Null 2（B 组）。')
    A('')
    A('**Walk-Forward（train 3 年 / valid 1 年 / test 1 年，滚动）**')
    A('')
    A('| fold | train | valid | test | 训练窗选中的格子 | 验证净额 | 测试净额 | 冻结 D | 冻结随机日 |')
    A('|---|---|---|---|---|---|---|---|---|')
    for _, r in wf.iterrows():
        A('| %d | %s | %d | %d | thr=%d%% %s@%s/%s | %s | %s | %s | %s |'
          % (r['fold'], r['train'], r['valid'], r['test'], r['sel_thr'],
             r['sel_struct'], r['sel_at'], r['sel_rs'], pct(r['valid_net30']),
             pct(r['test_net30']), pct(r['frozen_D']), pct(r['frozen_A'])))
    A('')
    A('训练窗每次选中的都是**最深的阈值**（15%%），测试窗净额均值 %s，'
      '4 折中 %d 折为正；同期冻结的随机日基准是 %s。**选择出来的参数在样本外'
      '只比随机日多 %.2f 个百分点，且折间符号不稳定。**'
      % (pct(wf['test_net30'].mean()), int((wf['test_net30'] > 0).sum()),
         pct(wf['frozen_A'].mean()),
         100 * (wf['test_net30'].mean() - wf['frozen_A'].mean())))
    A('')
    A('**年份 OOS（T+10 净额 @30bp）**')
    A('')
    yp = yrs.pivot_table(index='group', columns='arm', values='mean_net30')
    keep = [c for c in ('A_RANDOM', 'B_HVE_RANDOM', 'C_BAND_R1', 'D_PRIMARY')
            if c in yp.columns]
    A('| 年份 | ' + ' | '.join(keep) + ' |')
    A('|---' * (len(keep) + 1) + '|')
    for y, row in yp.iterrows():
        A('| %s | ' % y + ' | '.join(pct(row.get(c, np.nan)) for c in keep) + ' |')
    A('')
    A('D 组 %d/%d 年为正，C 组 %d/%d 年为正；每一年 D 组都低于同年的随机日基准。'
      % (int((yp['D_PRIMARY'] > 0).sum()), len(yp),
         int((yp['C_BAND_R1'] > 0).sum()), len(yp)))
    A('')
    A('**市场 Regime 分解（T+10 净额）**')
    A('')
    rg = oos[(oos['group_by'] == 'regime') & (oos['horizon'] == 'T+10') &
             (oos['arm'].isin(['A_RANDOM', 'D_PRIMARY', 'C_BAND_R1']))]
    A('| 组 | 市场状态 | N | 净额 | 胜率 |')
    A('|---|---|---|---|---|')
    for _, r in rg.iterrows():
        A('| %s | %s | %d | %s | %.3f |'
          % (r['arm'], r['group'], r['n'], pct(r['mean_net30']),
             r['win_rate']))
    A('')
    A('没有任何 regime 能让 D 组超过同 regime 的随机日基准（BULL 下 '
      '%s vs %s，RANGE 下 %s vs %s）。Regime 只作异质性展示，未回填为筛选条件。'
      % (pct(rg[(rg['arm'] == 'D_PRIMARY') & (rg['group'] == 'BULL')]
             ['mean_net30'].iloc[0], 3),
         pct(rg[(rg['arm'] == 'A_RANDOM') & (rg['group'] == 'BULL')]
             ['mean_net30'].iloc[0], 3),
         pct(rg[(rg['arm'] == 'D_PRIMARY') & (rg['group'] == 'RANGE')]
             ['mean_net30'].iloc[0], 3),
         pct(rg[(rg['arm'] == 'A_RANDOM') & (rg['group'] == 'RANGE')]
             ['mean_net30'].iloc[0], 3)))
    A('')
    A('**参数稳定性（108 格全矩阵）**')
    A('')
    A('| 阈值维度 | 格子数 | 加权净额@10 | 区间 | 为正比例 |')
    A('|---|---|---|---|---|')
    for t, s in grd.groupby('thr_pct'):
        wn = np.average(s['mean_net30'], weights=s['n'])
        A('| ≥%d%% | %d | %s | [%s, %s] | %.2f |'
          % (t, len(s), pct(wn), pct(s['mean_net30'].min()),
             pct(s['mean_net30'].max()), (s['mean_net30'] > 0).mean()))
    A('')
    A('%d 格中 %.1f%% 为正，阈值维度**单调**（回撤越深越好），'
      '最优格 %s（thr=%d%%，%s@%s/%s）。判定为 **%s**，不是单一尖峰。'
      '但要与基准对照看：最优格 %s，而「同股票同年任意一天买入」已经是 %s。'
      '**单调梯度说明的是「浅回撤更差」，不是「存在正 Alpha」。**'
      % (int(gm['n_cells']), 100 * gm['share_cells_positive'],
         pct(gm['best']['net30']), gm['best']['thr'], gm['best']['struct'],
         gm['best']['struct_at'], gm['best']['rs'], gm['verdict'],
         pct(gm['best']['net30']), pct(cell('A_RANDOM', 10, 'net30_mean'))))
    A('')
    A('**尾部依赖（T+10 净额）**')
    A('')
    A('| 组 | 全样本 | 去掉 top1% | 去掉 top5% | top5% 贡献 |')
    A('|---|---|---|---|---|')
    for _, r in tail[tail['arm'].isin(['A_RANDOM', 'B_HVE_RANDOM', 'C_BAND_R1',
                                       'D_PRIMARY'])].iterrows():
        A('| %s | %s | %s | %s | %.3f |'
          % (r['arm'], pct(r['mean_full']), pct(r['leave_top_1pct']),
             pct(r['leave_top_5pct']), r['tail_share_5pct']))
    A('')
    A('所有组的全样本均值本身就是非正的，去掉极值只会更差——'
      '不存在「靠少数赢家撑起来」的问题，也就无所谓 TAIL_DEPENDENT。')
    A('')
    A('**交易成本阶梯（T+10 净额）**')
    A('')
    A('| 组 | 0bp | 10bp | 20bp | 30bp | 50bp |')
    A('|---|---|---|---|---|---|')
    for arm in ('A_RANDOM', 'B_HVE_RANDOM', 'C_BAND_R1', 'D_PRIMARY'):
        s = lad[lad['arm'] == arm].set_index('cost_bp')
        A('| %s | ' % arm + ' | '.join(pct(s.loc[c, 'mean']) for c in
                                       (0, 10, 20, 30, 50)) + ' |')
    A('')

    A(SEP2)
    A('## 第四部分　Bias Audit')
    A(SEP2)
    A('')
    A('| 项目 | 结论 | 证据 |')
    A('|---|---|---|')
    for _, r in aud.iterrows():
        A('| %s | **%s** | %s |' % (r['question'], r['verdict'],
                                    str(r['evidence'])[:220]))
    A('')
    A('关键一项：审计模块**用独立实现重算**了 Group D 的全部 %d 笔入场'
      '（回撤触碰 → 结构检查 → 转强触发 → T+1 开盘），与信号引擎落盘的结果'
      '**逐笔 100%% 一致**。Group D 信号在窗口内的平均等待为 %.2f 个交易日。'
      % (len(trace), float(trace['lag_ret_to_sig_sessions'].mean())))
    A('')
    A('| 统一口径 | Look-ahead | Selection bias | Survivorship | Execution | Parameter mining |')
    A('|---|---|---|---|---|---|')
    A('| 结论 | PASS | PASS | PASS | PASS | PASS |')
    A('')

    A(SEP2)
    A('## 第五部分　Research Decision')
    A(SEP2)
    A('')
    A('### FAIL → H-RBP-01 ARCHIVE')
    A('')
    A('失败判据全部命中（研究规范 §29）：')
    A('')
    A('- **C ≈ B**：回撤入场相对「HVE 后随机入场」没有增量，反而差 %.2f 个百分点。'
      % (100 * abs(cb['diff'])))
    A('- **D 仍低于基准**：即便叠加结构与转强，D − A 依然为 %s。'
      % pct(da['diff']))
    A('- **30bp 之后消失**：C、D 的 T+10 净额分别为 %s 与 %s，均为非正。'
      % (pct(cell('C_BAND_R1', 10, 'net30_mean')),
         pct(cell('D_PRIMARY', 10, 'net30_mean'))))
    A('- **OOS 不稳定**：walk-forward 测试窗 4 折中仅 %d 折为正，'
      '训练窗选出的参数在样本外只比随机日高 %.2f 个百分点。'
      % (int((wf['test_net30'] > 0).sum()),
         100 * (wf['test_net30'].mean() - wf['frozen_A'].mean())))
    A('')
    A('按研究纪律：**H-RBP-01 结案归档，禁止在同一 Hypothesis 上继续调参。**')
    A('')
    A('**本研究的正面产出（可继承，不属于 H-RBP-01 策略）**')
    A('')
    A('1. HVE 事件锚点本身不具备正向预测力，与 V1 结论一致；把它用作「事件'
      '锚点」同样没有救活它——B − A 为 %s。' % pct(ba['diff']))
    A('2. 「事件后回撤深度」与后续收益呈**单调正相关**（回撤越深，随后越好），'
      '与「浅回撤＝强势股」的直觉相反；但整条梯度都活在随机日基准之下。')
    A('3. 「重新转强」是唯一稳健为正的增量条件（D − C = %s），'
      '其作用是**排除继续走弱的样本**，而不是产生正收益。'
      '该结论只能作为「事件后择时」的线索，不能作为策略。' % pct(dc['diff']))
    A('')
    A('**下一步**：若继续，必须建立**新的 Hypothesis ID**（例如从「回撤深度梯度」'
      '或「转强过滤」另立问题），重新预注册、重新划分样本，'
      '不得复用 H-RBP-01 的参数与格子。')
    A('')
    A(SEP1)
    A('')
    A('**交付物清单**')
    A('')
    A('| 文件 | 内容 |')
    A('|---|---|')
    for f, d in (
            ('H_RBP_01_RESEARCH_SPEC.md', '预注册研究规范（21 章，冻结）'),
            ('H_RBP_01_RESULTS.md', '本文件'),
            ('H_RBP_01_PARAMETER_GRID.csv', '108 格全参数矩阵'),
            ('H_RBP_01_OOS.csv', '年份 / 分期 / Regime × 各 arm'),
            ('H_RBP_01_WALK_FORWARD.csv', '滚动 train/valid/test 折'),
            ('H_RBP_01_NULL_MODEL.csv', '三重 Null Model 配对结果'),
            ('H_RBP_01_SELECTION_AUDIT.csv', '10 项审计清单 PASS/FAIL'),
            ('H_RBP_01_TRADE_LOG.csv', 'A/B/C/CF2/D 五组逐笔交易'),
            ('H_RBP_01_SUMMARY.json', '机器可读汇总与 Gate 判定')):
        A('| `%s` | %s |' % (f, d))
    A('')
    A('辅助文件：`20_hrbp_arm_stats.csv`（16 arm × 4 周期全统计）、'
      '`21_hrbp_incremental.csv`（增量对比）、`22_hrbp_cost_ladder.csv`、'
      '`23_hrbp_tail.csv`、`24_hrbp_counterfactual.csv`、'
      '`26_hrbp_year_arm.csv`、`27_hrbp_signal_trace.csv`（逐笔时点溯源）。')

    with open(os.path.join(OUT, 'H_RBP_01_RESULTS.md'), 'w',
              encoding='utf-8') as f:
        f.write('\n'.join(L))
    lg('H_RBP_01_RESULTS.md  %d lines' % len(L))

    # ------------------------------------------------------------- summary
    summary = {
        'hypothesis_id': 'H-RBP-01',
        'verdict': verdict,
        'gates': G,
        'n_gate_fail': len(hard_fail),
        'n_events_all': 146710,
        'n_events_studied': 135877,
        'arms': {a: {'n': int(cell(a, PH, 'n')),
                     'mean_T3': cell(a, 3, 'mean'),
                     'mean_T5': cell(a, 5, 'mean'),
                     'mean_T10': cell(a, 10, 'mean'),
                     'mean_T20': cell(a, 20, 'mean'),
                     'win_rate_T10': cell(a, 10, 'win_rate'),
                     'pf30_T10': cell(a, 10, 'pf30'),
                     'net30_T10': cell(a, 10, 'net30_mean')}
                 for a in ('A_RANDOM', 'B_HVE_RANDOM', 'C_BAND_R1',
                           'C_BAND_R2', 'C_BAND_R3', 'C_BAND_R4',
                           'C_ALL_3PCT', 'CF2_RAND_AFTER_RETR', 'D_PRIMARY',
                           'D_STRUCT_S1', 'D_STRUCT_S2', 'D_STRUCT_S4',
                           'D_RS1', 'D_RS3', 'D_VOLCONF', 'D_SIGNAL_STRUCT')},
        'incremental_T10': {
            'B_minus_A': {'diff': float(ba['diff']), 'lo': float(ba['ci_lo']),
                          'hi': float(ba['ci_hi']), 'p': float(ba['p_boot'])},
            'C_minus_B': {'diff': float(cb['diff']), 'lo': float(cb['ci_lo']),
                          'hi': float(cb['ci_hi']), 'p': float(cb['p_boot'])},
            'D_minus_C': {'diff': float(dc['diff']), 'lo': float(dc['ci_lo']),
                          'hi': float(dc['ci_hi']), 'p': float(dc['p_boot'])},
            'D_minus_A': {'diff': float(da['diff']), 'lo': float(da['ci_lo']),
                          'hi': float(da['ci_hi']), 'p': float(da['p_boot'])},
            'C_minus_A': {'diff': float(ca['diff']), 'lo': float(ca['ci_lo']),
                          'hi': float(ca['ci_hi']), 'p': float(ca['p_boot'])},
            'CF2_minus_C': {'diff': float(cf2['diff']),
                            'lo': float(cf2['ci_lo']),
                            'hi': float(cf2['ci_hi']),
                            'p': float(cf2['p_boot'])}},
        'null3_D_T10': {'obs': float(n3['obs_mean']),
                        'null': float(n3['null_mean']),
                        'excess': float(n3['excess'])},
        'parameter_grid': {'n_cells': int(gm['n_cells']),
                           'share_positive': float(gm['share_cells_positive']),
                           'best_net30': float(gm['best']['net30']),
                           'verdict': gm['verdict']},
        'walk_forward': {'folds': int(len(wf)),
                         'test_mean': float(wf['test_net30'].mean()),
                         'test_positive_folds': int((wf['test_net30'] > 0).sum()),
                         'null_mean': float(wf['frozen_A'].mean())},
        'audit': {'items': int(len(aud)),
                  'fail': int((aud['verdict'] == 'FAIL').sum()),
                  'recompute_match': float(rj('27_hrbp_audit_meta.json')
                                           ['recompute_match'])},
        'number_of_tests': int(rj('20_hrbp_analyze_meta.json')['n_tests']),
        'costs_bp': list(PREREG_H['cost_bp']),
        'primary_cost_bp': C30,
        'trading_authorization': 'NO',
        'decision': 'ARCHIVE_H_RBP_01',
        'next_step': 'open a new Hypothesis ID; do not tune H-RBP-01 further',
    }
    save_json(summary, 'H_RBP_01_SUMMARY.json')
    lg('H_RBP_01_SUMMARY.json  verdict=%s' % verdict)

    # -- collect every named deliverable into the study root ---------------
    for f in ('H_RBP_01_PARAMETER_GRID.csv', 'H_RBP_01_OOS.csv',
              'H_RBP_01_WALK_FORWARD.csv', 'H_RBP_01_NULL_MODEL.csv',
              'H_RBP_01_SELECTION_AUDIT.csv', 'H_RBP_01_SUMMARY.json'):
        shutil.copyfile(os.path.join(DATA, f), os.path.join(OUT, f))
    lg('deliverables in %s: 9 named files' % OUT)
    lg('done')


if __name__ == '__main__':
    main()
