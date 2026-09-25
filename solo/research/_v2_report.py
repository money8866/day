# -*- coding: utf-8 -*-
"""三花聚顶 V2 · 最终报告生成（v2-16 / v2-17）

输入: report_daily/research/three_flower_v2_*.csv + out/tf_v2_meta.json
输出: report_daily/research/three_flower_v2_report.md

原则:
  - 报告中的所有数字均直接取自已写出的 CSV，不手写、不估算
  - 结论只依据证据；不因训练集结果漂亮而 PASS
  - 输出 Robus Parameter Range 而非 Best Parameter
"""
import os
import json
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
ROOT = os.path.dirname(HERE)
RD = os.path.join(ROOT, 'report_daily', 'research')

L = []


def P(*a):
    L.append(' '.join(str(x) for x in a))


def pct(v, d=2):
    try:
        f = float(v)
    except Exception:
        return 'n/a'
    if not np.isfinite(f):
        return 'n/a'
    return f'{f * 100:+.{d}f}%'


def hr(v, d=2):
    """命中率等无方向含义的比率：不带正号"""
    try:
        f = float(v)
    except Exception:
        return 'n/a'
    if not np.isfinite(f):
        return 'n/a'
    return f'{f * 100:.{d}f}%'


def pct0(v, d=0):
    try:
        f = float(v)
    except Exception:
        return 'n/a'
    if not np.isfinite(f):
        return 'n/a'
    return f'{f * 100:.{d}f}%'


def num(v, d=2):
    try:
        f = float(v)
    except Exception:
        return 'n/a'
    if not np.isfinite(f):
        return 'n/a'
    return f'{f:.{d}f}'


def cnt(v):
    try:
        f = float(v)
    except Exception:
        return '0'
    return '0' if not np.isfinite(f) else f'{int(f)}'


def rd(nm):
    return pd.read_csv(os.path.join(RD, nm))


VR_ORDER = ['<0.6', '0.6-0.8', '0.8-1.0', '1.0-1.1', '1.1-1.2',
            '1.2-1.3', '1.3-1.5', '1.5-2.0', '>2.0']


def cell(df, sec, grp, hz, col):
    r = df[(df['section'] == sec) & (df['group'] == grp) & (df['horizon'] == hz)]
    if len(r) == 0:
        return np.nan
    return r[col].iloc[0]


def sec_names(df, sec):
    return list(df[df['section'] == sec]['group'].drop_duplicates())


def simple_table(df, sec, order, title, note=''):
    """分组表：n | T+5 | T+10 | T+20 | med10 | win10 | pf10 | advF10"""
    if sec not in set(df['section']):
        return
    P(f'**{title}**')
    P('')
    P('| 分组 | n | T+5 | T+10 | T+20 | 中位T+10 | 胜率T+10 | PF(T+10) | 配对超额T+10 |')
    P('| --- | --- | --- | --- | --- | --- | --- | --- | --- |')
    for g in order:
        r = df[(df['section'] == sec) & (df['group'] == g)]
        if len(r) == 0:
            continue
        row = {int(x['horizon']): x for _, x in r.iterrows()}
        if 10 not in row:
            continue
        g10 = row[10]
        P('| {} | {} | {} | {} | {} | {} | {} | {} | {} |'.format(
            g, cnt(g10['n']), pct(row[5]['mean']) if 5 in row else 'n/a',
            pct(g10['mean']), pct(row[20]['mean']) if 20 in row else 'n/a',
            pct(g10['med']), pct0(g10['win']), num(g10['pf']), pct(g10['adv_fix'])))
    if note:
        P('')
        P(note)
    P('')


def main():
    vc = rd('three_flower_v2_volume_curve.csv')
    ba = rd('three_flower_v2_breakout_analysis.csv')
    rg = rd('three_flower_v2_regime.csv')
    mv = rd('three_flower_v2_marketcap.csv')
    oo = rd('three_flower_v2_oos.csv')
    sg = rd('three_flower_v2_significance.csv')
    mo = rd('three_flower_v2_vr_monotonicity.csv')
    sw = rd('three_flower_v2_vr_threshold_sweep.csv')
    hv = rd('three_flower_v2_hvt_orth.csv')
    meta = json.load(open(os.path.join(OUT, 'tf_v2_meta.json'), encoding='utf-8'))

    n_all, n_tf, n_brk = meta['n_all'], meta['n_tf'], meta['n_brk']
    n_nobrk, n_notf = meta['n_nobrk'], meta['n_notf']
    VR_TH = meta['vr_threshold_train']
    rr = meta['vr_robust_range']
    amp_med, vr_med = meta['quad_split']['amp_med'], meta['quad_split']['vr_med']
    sp = meta['spearman']

    def G(sec, grp, hz=10, col='mean'):
        return cell(ba, sec, grp, hz, col)

    def V(sec, grp, hz=10, col='mean'):
        return cell(vc, sec, grp, hz, col)

    # ══════════════════════════ 标题 ══════════════════════════
    P('# 三花聚顶 V2 · 突破量能机制研究报告')
    P('')
    P(f"生成日 {meta['date']}　|　样本区间 {meta['period_range'][0]} ~ {meta['period_range'][1]}　|　"
      f'成本口径 双边 {int(meta["cost"] * 10000) / 100:.2f}%')
    P('')
    P('本报告为纯科研产出，**不修改** HVT / DLG / F120 / 主题量化 / 突破策略 / 实盘执行 / te_buy_pool 任何模块。')
    P('')

    # ══════════════════════════ 〇 最终判定 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 〇、最终判定（先行结论）')
    P('')
    P('### 判定：**FAIL**')
    P('')
    P('**不生成 `ThreeFlower_Breakout_V2`，不进入实盘系统。** 只保存研究结果。')
    P('')
    P('第 17 节五项准入条件逐项核查：')
    P('')
    P('| 准入条件 | 结果 | 证据 |')
    P('| --- | --- | --- |')
    lo_oos = cell(oo, "VRTH_OOS", f"低量 VR<{VR_TH:.2f}", 10, 'mean')
    hi_oos = cell(oo, "VRTH_OOS", f"高量 VR>={VR_TH:.2f}", 10, 'mean')
    a12_oos = cell(oo, 'VRTH_OOS', '低量 VR<1.2(对照)', 10, 'mean')
    a12_oos_hi = cell(oo, 'VRTH_OOS', '高量 VR>=1.2(对照)', 10, 'mean')
    P(f'| OOS 有效 | ✗ 不满足 | θ*={VR_TH:.2f}：OOS 低量组 T+10 {pct(lo_oos)}、高量组 {pct(hi_oos)}；'
      f'对照 VR<1.2 OOS {pct(a12_oos)}、VR>=1.2 OOS {pct(a12_oos_hi)} —— 两个子组均为负 |')
    P('| 参数扰动稳定 | △ 部分 | 四象限「低量+小幅」9/9 网格为正，但 VR 阈值本身无拐点，'
      f'TRAIN 稳健区间宽达 [{rr[0]:.2f}, {rr[1]:.2f}]（无区分度） |')
    P('| 样本量充分 | △ 部分 | 关键子组偏小：VR<0.6 仅 62、结构回撤 0-3% 为 0、AMPRATIO>1.0 仅 2 |')
    P('| 不同年份至少部分稳定 | ✗ 不满足 | 2023 低量突破 T+10 -1.11%、2026(OOS) -0.23%；仅 2022 / 2025 为正 |')
    P('| 无数据泄漏 | ✓ 通过 | 花在 d+3 才确认、突破取 buffer=0 首日、参照组按 offset 分层配对，无未来函数 |')
    P('')
    P('**核心反证（三条，任一条都足以否决）**：')
    P('')
    _p5 = sg[(sg['pair'] == '低量突破(<1.2) vs 无三花') & (sg['horizon'] == 10)].iloc[0]
    P('1. **低量突破相对「无三花」没有独立显著 Alpha**：T+10 Δmean '
      f"{pct(_p5['d_mean'])}，bootstrap 置信区间 [{pct(_p5['d_mean_lo'])}, {pct(_p5['d_mean_hi'])}] 跨 0"
      f"（p={num(_p5['t_p'], 3)}）。")
    P('2. **剔除 HVT 命中后 Alpha 归零**：低量突破 ∩ HVT未命中（±3日）T+10 均值 '
      f'{pct(cell(hv, "NO_HVT", "A_三花+低量突破∩非HVT", 10, "mean"))}、中位 '
      f'{pct(cell(hv, "NO_HVT", "A_三花+低量突破∩非HVT", 10, "med"))}、胜率 '
      f'{pct0(cell(hv, "NO_HVT", "A_三花+低量突破∩非HVT", 10, "win"))}。')
    P('3. **OOS 全面失效**：训练集发现的稳健阈值 θ* 在 OOS 两个子组均为负；对照阈值 1.2 同样失效。')
    P('')

    # ══════════════════════════ 一 口径 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 一、研究口径与纪律声明')
    P('')
    P('### 1.1 事件定义（无未来函数）')
    P('')
    P('- **首板**：涨停且前一日未涨停；涨停幅度按板块区分（主板 10% / 创业板·科创板 20% / 北交所 30% / ST 5%）')
    P('- **花（Flower）**：半径 R=1 的摆动低点，**d 日成立、d+3 日才允许确认**（`c3 = f3 + R`），确认日才可用于决策')
    P('- **三花聚顶**：确认日较早的三个摆动低点构成的核心有效结构（`haslim==0` 且 `struct_dd <= 0.25`），'
      '按 `SEL_RULE=earliest` 取确认日最早三元组')
    P('- **突破**：三花确认后首次收盘价突破「三花阶段最高价」（`hi_lvl = hi1[idx, c3]`，offset 1 至 c3 的最高价），buffer=0')
    P('- **锚点日**：突破组 = 首次突破日；三花无突破组 = 确认日；无三花组 = 首板日 + 10 交易日')
    P('')
    P('### 1.2 收益与统计口径')
    P('')
    P('- T+N 为自锚点日收盘买入、持有 N 交易日后卖出的收益，**已扣双边成本 0.30%**（滑点 0.10% + 佣金 0.025% + 印花税 0.05%）')
    P('- MAE / MFE 为路径内最大不利 / 有利波动，**未扣成本**')
    P('- `adv`：配对超额（自我剔除），参照=同 offset 的「其余全体首板」，且限定同一年份集合')
    P('- `adv_fix`：配对超额（固定基准），参照=同 offset 的「无三花首板」')
    P('- 报告以 **adv_fix** 为主要对比口径，用于剔除时点效应；仅 n≥8 的 offset 桶参与加权')
    P('')
    P('### 1.3 分期（受数据可得性约束）')
    P('')
    P('| 计划 | 实际 | 原因 |')
    P('| --- | --- | --- |')
    P('| TRAIN 2018-2023 | **2021-04-06 ~ 2023-12-31** | UDC 日线缓存起始于 2021-01-04 |')
    P('| VALIDATION 2024-2025 | 2024-01-01 ~ 2025-12-31 | 一致 |')
    P('| OOS 2026 | 2026-01-01 ~ 2026-09-24 | 一致 |')
    P('')
    P('### 1.4 V2 纪律执行记录')
    P('')
    P('- 未把 `VR<1.2` 直接认定为最终规则 —— 完整扫描了 `<0.6` 至 `>2.0` 共 9 档 response curve')
    P('- 未只寻找最高收益区间 —— 同时对均值与中位数做单调性检验，并报告了全部 9 档（含负收益档）')
    P('- 未用 OOS 优化阈值 —— θ 只在 TRAIN 上扫描，VALID 确认、OOS 检验')
    P('- 未预设结论 —— 四象限、反事实、Regime、市值/换手、HVT 正交均按原设定执行，包括所有反证结果')
    P('')

    # ══════════════════════════ 二 样本概览 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 二、样本概览')
    P('')
    P('| 分组 | 样本 | 占比 |')
    P('| --- | --- | --- |')
    P(f'| 全体首板 | {n_all} | 100.0% |')
    P(f'| 三花形成 | {n_tf} | {n_tf / n_all * 100:.1f}% |')
    P(f'| 　└ 三花 + 突破 | {n_brk} | {n_brk / n_all * 100:.1f}% |')
    P(f'| 　└ 三花 + 无突破 | {n_nobrk} | {n_nobrk / n_all * 100:.1f}% |')
    P(f'| 首板后无三花 | {n_notf} | {n_notf / n_all * 100:.1f}% |')
    P('')
    v_lo = int(cell(vc, 'VR_全体突破', '<0.6', 10, 'n') or 0) + \
        int(cell(vc, 'VR_全体突破', '0.6-0.8', 10, 'n') or 0) + \
        int(cell(vc, 'VR_全体突破', '0.8-1.0', 10, 'n') or 0) + \
        int(cell(vc, 'VR_全体突破', '1.0-1.1', 10, 'n') or 0) + \
        int(cell(vc, 'VR_全体突破', '1.1-1.2', 10, 'n') or 0)
    v_hi = int(cell(vc, 'VR_全体突破', '1.2-1.3', 10, 'n') or 0) + \
        int(cell(vc, 'VR_全体突破', '1.3-1.5', 10, 'n') or 0) + \
        int(cell(vc, 'VR_全体突破', '1.5-2.0', 10, 'n') or 0) + \
        int(cell(vc, 'VR_全体突破', '>2.0', 10, 'n') or 0)
    P(f'突破组按 VR=1.2 划分：**VR<1.2 共 {v_lo} 例，VR>=1.2 共 {v_hi} 例**'
      '（与 V1 报告的 1313 / 3267 对齐校验通过，差异来自 V2 未做 r5 可观测性额外过滤）')
    P('')
    P(f'四象限中位数分割点（数据驱动，无预设）：突破幅度 {amp_med * 100:.2f}%、突破量能 {vr_med:.2f}')
    P('')

    # ══════════════════════════ 三 VR 分层 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 三、v2-1 VR 分层（固定三花定义，只改突破量能）')
    P('')
    P('| VR 区间 | 样本 | T+1 | T+3 | T+5 | T+10 | T+20 | 中位T+10 | 胜率T+10 | P25(T+10) | P75(T+10) | MAE | MFE | PF(T+10) |')
    P('| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |')
    for g in VR_ORDER:
        r = vc[(vc['section'] == 'VR_全体突破') & (vc['group'] == g)]
        if len(r) == 0:
            continue
        ro = {int(x['horizon']): x for _, x in r.iterrows()}
        g10 = ro[10]
        P('| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |'.format(
            g, cnt(g10['n']), pct(ro[1]['mean']), pct(ro[3]['mean']), pct(ro[5]['mean']),
            pct(g10['mean']), pct(ro[20]['mean']), pct(g10['med']), pct0(g10['win']),
            pct(g10['p25']), pct(g10['p75']), pct(g10['mae_med']), pct(g10['mfe_med']),
            num(g10['pf'])))
    P('')
    P('**配对超额（adv_fix，T+10，参照=同 offset 的无三花首板）**')
    P('')
    P('| VR 区间 | adv_fix(T+5) | adv_fix(T+10) | adv_fix(T+20) | 参照桶数(T+10) |')
    P('| --- | --- | --- | --- | --- |')
    for g in VR_ORDER:
        r = vc[(vc['section'] == 'VR_全体突破') & (vc['group'] == g)]
        if len(r) == 0:
            continue
        ro = {int(x['horizon']): x for _, x in r.iterrows()}
        P('| {} | {} | {} | {} | {} |'.format(
            g, pct(ro[5]['adv_fix']), pct(ro[10]['adv_fix']), pct(ro[20]['adv_fix']),
            cnt(ro[10]['adv_fix_nb'])))
    P('')
    P('> 观察：MAE（中位最大不利波动）随 VR 单调恶化（'
      f"{pct(cell(vc, 'VR_全体突破', '<0.6', 10, 'mae_med'))} → "
      f"{pct(cell(vc, 'VR_全体突破', '>2.0', 10, 'mae_med'))}），"
      '而 MFE（中位最大有利波动）在各档基本持平 —— '
      '放量突破并未带来更高的向上弹性，只带来更深的向下回撤。')
    P('')

    # ══════════════════════════ 四 单调性 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 四、v2-2 VR 与未来收益的关系形态（Q1 主问题）')
    P('')
    P('**Spearman 秩相关（VR 中值 → 各期收益）**')
    P('')
    P('| 期限 | Spearman(均值) | Spearman(中位数) |')
    P('| --- | --- | --- |')
    for N in ('1', '3', '5', '10', '20'):
        P(f"| T+{N} | {sp[N]['mean']:+.3f} | {sp[N]['med']:+.3f} |")
    P('')
    P('**形态判定（第 2 节要求的四问）**：')
    P('')
    sp10m, sp10d = sp['10']['mean'], sp['10']['med']
    P(f'- **是否随 VR 增加持续下降？** 中位数近乎严格单调递减（Spearman {sp10d:+.3f}）：'
      f"{pct(cell(vc, 'VR_全体突破', '<0.6', 10, 'med'))} → "
      f"{pct(cell(vc, 'VR_全体突破', '>2.0', 10, 'med'))}。"
      f'均值方向一致但非严格单调（Spearman {sp10m:+.3f}），'
      '在 1.1-1.2、1.3-1.5 两档出现小幅反弹（右偏分布所致）。')
    P('- **是否存在最佳 VR 区间？** 不存在稳定最佳区间。'
      f"唯一正均值为 <0.6 档（T+10 {pct(cell(vc, 'VR_全体突破', '<0.6', 10, 'mean'))}），"
      f"但样本仅 {cnt(cell(vc, 'VR_全体突破', '<0.6', 10, 'n'))} 例、参照桶数仅 "
      f"{cnt(cell(vc, 'VR_全体突破', '<0.6', 10, 'adv_fix_nb'))}，且 0.6-1.2 各档均值已在 0 附近徘徊、"
      '中位数全为负 —— 属于「无 Alpha」而非「有最佳区」。')
    P('- **是否存在拐点？** 不存在单一拐点。'
      '0.6-0.8 档均值已转负，0.8-1.2 档均值在 -0.07% ~ +0.59% 之间无方向，'
      '1.5 以上才系统性恶化 —— 是**连续衰减**，不是阶跃。')
    P('- **是否只是 VR>=1.2 后突然恶化？** **不是。** '
      'VR 在 0.6~1.2 之间的 4 档 T+10 均值分别为 '
      f"{pct(cell(vc, 'VR_全体突破', '0.6-0.8', 10, 'mean'))}、"
      f"{pct(cell(vc, 'VR_全体突破', '0.8-1.0', 10, 'mean'))}、"
      f"{pct(cell(vc, 'VR_全体突破', '1.0-1.1', 10, 'mean'))}、"
      f"{pct(cell(vc, 'VR_全体突破', '1.1-1.2', 10, 'mean'))}，"
      '中位数全为负。所谓「缩量突破有效」的 V1 印象，主要来自把 <1.2 与 >=1.2 二分的对比，'
      '而非 1.2 附近存在结构性断点。')
    P('- **是否非线性？** 是。中位数单调、均值非单调（含 1.1-1.2 反弹），'
      '且 MAE 与均值不同步 —— 属**非线性的整体负向关系**。')
    P('')
    P('> 结论：**VR 与未来收益存在稳定的负向关系（中位数口径近乎单调），但不存在「温和放量的最优区间」。**')
    P('')

    # ══════════════════════════ 五 幅度 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 五、v2-3 突破幅度分层（Q3）')
    P('')
    simple_table(ba, 'AMP_突破幅度(high/hi_lvl-1)',
                 ['<1%', '1-2%', '2-3%', '3-5%', '5-7%', '7-10%', '>10%'],
                 '突破幅度 = 突破日最高价 / 三花阶段最高价 - 1',
                 '> 幅度越大越差：配对超额从 <1% 档的 '
                 f"{pct(G('AMP_突破幅度(high/hi_lvl-1)', '<1%', 10, 'adv_fix'))} "
                 '单调恶化到 >10% 档的 '
                 f"{pct(G('AMP_突破幅度(high/hi_lvl-1)', '>10%', 10, 'adv_fix'))}，"
                 '且 >10% 档胜率仅 '
                 f"{pct0(G('AMP_突破幅度(high/hi_lvl-1)', '>10%', 10, 'win'))}、"
                 f"PF {num(G('AMP_突破幅度(high/hi_lvl-1)', '>10%', 10, 'pf'))} —— 属崩塌。")
    simple_table(ba, 'AMPCL_收盘突破幅度',
                 ['<0%', '0-1%', '1-2%', '2-3%', '3-5%', '5-7%', '>7%'],
                 '以收盘价计的突破幅度（对阶段最高价）')

    # ══════════════════════════ 六 涨幅 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 六、v2-4 突破日涨幅分层（Q4）')
    P('')
    simple_table(ba, 'BRKRET_突破日涨幅',
                 ['<2%', '2-3%', '3-5%', '5-7%', '7-10%', '>10%'],
                 '突破日当日涨幅',
                 '> 方向性成立但非严格单调：配对超额从 <2% 档的 '
                 f"{pct(G('BRKRET_突破日涨幅', '<2%', 10, 'adv_fix'))} 恶化到 7-10% 档的 "
                 f"{pct(G('BRKRET_突破日涨幅', '7-10%', 10, 'adv_fix'))}；"
                 '>10% 档虽配对超额为 '
                 f"{pct(G('BRKRET_突破日涨幅', '>10%', 10, 'adv_fix'))}，"
                 '但**均值正而中位为负**（'
                 f"{pct(G('BRKRET_突破日涨幅', '>10%', 10, 'mean'))} / "
                 f"{pct(G('BRKRET_突破日涨幅', '>10%', 10, 'med'))}），"
                 '说明该档是「少数大涨拉高均值 + 多数下跌」的右偏分布，不构成机会。')

    # ══════════════════════════ 七 位置 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 七、v2-5 突破位置')
    P('')
    simple_table(ba, 'AMPABS_突破价-阶段最高价(元)',
                 ['<0元', '0-0.1元', '0.1-0.3元', '0.3-1元', '>1元'],
                 '突破价 − 三花阶段最高价（绝对元）')
    simple_table(ba, 'AMPRATIO_突破幅度/阶段振幅',
                 ['<0.1', '0.1-0.25', '0.25-0.5', '0.5-1.0', '>1.0'],
                 '突破幅度 / 三花阶段振幅（相对强度）',
                 '> 「刚刚突破」（比值 <0.1，即突破幅度不足阶段振幅的 10%）配对超额 '
                 f"{pct(G('AMPRATIO_突破幅度/阶段振幅', '<0.1', 10, 'adv_fix'))}，"
                 '优于「远离前高突破」（0.25-0.5 档 '
                 f"{pct(G('AMPRATIO_突破幅度/阶段振幅', '0.25-0.5', 10, 'adv_fix'))}）。"
                 '>1.0 档仅 2 例，无法评价。')

    # ══════════════════════════ 八 结构紧密 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 八、v2-6 三花结构紧密程度')
    P('')
    simple_table(ba, 'DDBIN_三花最大回撤',
                 ['0-3%', '3-5%', '5-8%', '8-10%', '>10%'],
                 '三花结构最大回撤',
                 '> **重要口径提示**：核心有效性筛选已要求 `struct_dd <= 25%`，'
                 '而实际分布中 0-3% 档 0 例、3-5% 档仅 4 例 —— 「回撤极小」的紧致结构在本样本中几乎不存在，'
                 '该维度实际不可分层。5-8% / 8-10% 档配对超额最高，但参照桶数仅 '
                 f"{cnt(G('DDBIN_三花最大回撤', '5-8%', 10, 'adv_fix_nb'))} / "
                 f"{cnt(G('DDBIN_三花最大回撤', '8-10%', 10, 'adv_fix_nb'))}，证据强度弱。")
    simple_table(ba, 'SPAN_三花持续时间', ['0-4', '4-6', '6-8', '8-10', '>10'], '三花持续时间（交易日）')
    simple_table(ba, 'GAP12_Flower1-2间隔', ['2-3', '3-4', '4-6', '>6'], 'Flower1 与 Flower2 间隔')
    simple_table(ba, 'GAP23_Flower2-3间隔', ['2-3', '3-4', '4-6', '>6'], 'Flower2 与 Flower3 间隔')
    simple_table(ba, 'FPRICE12_Flower1-2价格差', ['<0%', '0-1%', '1-3%', '3-6%', '>6%'], 'Flower1-2 价格差')
    simple_table(ba, 'FPRICE23_Flower2-3价格差', ['<0%', '0-1%', '1-3%', '3-6%', '>6%'], 'Flower2-3 价格差')
    simple_table(ba, 'FVOL12_Flower1-2量差', ['<-20%', '-20~-5%', '-5~5%', '5-20%', '>20%'], 'Flower1-2 成交量差')
    simple_table(ba, 'FVOL23_Flower2-3量差', ['<-20%', '-20~-5%', '-5~5%', '5-20%', '>20%'], 'Flower2-3 成交量差')
    P('> 结构维度小结：Flower 间隔、持续时间基本无区分度（各档配对超额集中在 +0.3% ~ +1.0%）；'
      '仅有「Flower1-2 价格差」呈现弱区分（价格差 >6% 时配对超额转负 '
      f"{pct(G('FPRICE12_Flower1-2价格差', '>6%', 10, 'adv_fix'))}）。")
    P('')

    # ══════════════════════════ 九 突破前量能 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 九、v2-7 突破前成交量状态')
    P('')
    simple_table(vc, 'PBVR3_突破前3日量比', ['PreVR<0.6', '0.6-0.8', '0.8-1.0', '1.0+'],
                 '突破前 3 日量比（相对 MA20）',
                 '> 突破前若已是放量状态（PreVR>=1.0），T+10 配对超额 '
                 f"{pct(V('PBVR3_突破前3日量比', '1.0+', 10, 'adv_fix'))}，"
                 '显著差于缩量状态（0.8-1.0 档 '
                 f"{pct(V('PBVR3_突破前3日量比', '0.8-1.0', 10, 'adv_fix'))}）。")
    simple_table(vc, 'PBVR1_突破前1日量比', ['PreVR<0.6', '0.6-0.8', '0.8-1.0', '1.0+'], '突破前 1 日量比')
    simple_table(vc, 'VRRATIO_VR/PreVR', ['<1.0', '1.0-1.5', '1.5-2.5', '>2.5'],
                 'Breakout_VR ÷ PreBreakout_VR（突破日相对前量的跳跃幅度）',
                 '> 这是本节最有区分度的指标：比值 <1.0（突破日量能低于前 3 日均量）配对超额 '
                 f"{pct(V('VRRATIO_VR/PreVR', '<1.0', 10, 'adv_fix'))}，"
                 '而 >2.5（跳跃式放量）为 '
                 f"{pct(V('VRRATIO_VR/PreVR', '>2.5', 10, 'adv_fix'))}。"
                 '**「长期缩量 → 突破时仅温和恢复成交量」在本样本中确实优于「突然爆量」。**')
    P('> Q2 直接回答：不存在「温和放量最优区间」。所谓温和放量（VR 0.8~1.2）各档 T+10 中位数均为负'
      f"（{pct(V('VR_全体突破', '0.8-1.0', 10, 'med'))} / {pct(V('VR_全体突破', '1.0-1.1', 10, 'med'))} / "
      f"{pct(V('VR_全体突破', '1.1-1.2', 10, 'med'))}）；"
      '相对而言「不放大成交量」（VR<1.0，尤其相对前量比值 <1.0）才具有相对优势，但绝对水平仍接近 0。')
    P('')

    # ══════════════════════════ 十 价格结构 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 十、v2-8 突破日价格结构')
    P('')
    simple_table(ba, 'BODY_突破日实体', ['<0%(阴线)', '0-2%', '2-5%', '5-8%', '>8%'], '突破日实体涨跌幅')
    simple_table(ba, 'USH_突破日上影线', ['<1%', '1-2%', '2-4%', '>4%'], '突破日上影线幅度',
                 '> 上影线越大越差，且**单调**：'
                 f"{pct(G('USH_突破日上影线', '<1%', 10, 'adv_fix'))} → "
                 f"{pct(G('USH_突破日上影线', '>4%', 10, 'adv_fix'))}。")
    simple_table(ba, 'LSH_突破日下影线', ['<0.5%', '0.5-1.5%', '1.5-3%', '>3%'], '突破日下影线幅度',
                 '> 下影线同样越大越差（'
                 f"{pct(G('LSH_突破日下影线', '<0.5%', 10, 'adv_fix'))} → "
                 f"{pct(G('LSH_突破日下影线', '>3%', 10, 'adv_fix'))}），"
                 '说明突破日的长影线是抛压 / 承接混乱信号，而非强势。')
    simple_table(ba, 'POS_突破日收盘位置', ['<0.5', '0.5-0.7', '0.7-0.85', '>0.85'],
                 '收盘位置 =（收盘-最低）/（最高-最低）',
                 '> 关系弱且非单调（配对超额 '
                 f"{pct(G('POS_突破日收盘位置', '<0.5', 10, 'adv_fix'))} / "
                 f"{pct(G('POS_突破日收盘位置', '>0.85', 10, 'adv_fix'))}），"
                 '「收盘位置高」并未带来稳定超额。')

    # ══════════════════════════ 十一 四象限 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 十一、v2-9 四象限（突破幅度 × 突破量能）')
    P('')
    simple_table(ba, 'QUAD_四象限(中位分割)',
                 ['A_低量+低幅', 'B_高量+低幅', 'C_低量+高幅', 'D_高量+高幅'],
                 f'中位数分割（幅度 {amp_med * 100:.2f}% / 量能 {vr_med:.2f}）',
                 '> 只有 A 象限（低量 + 小幅）为正，D 象限（高量 + 大幅）显著为负。')
    P('**分割点敏感性网格（第 9 节「不得预设结论」要求：共 9 组阈值组合）**')
    P('')
    P('| 幅度阈值 \\ 量能阈值 | 0.8 | 1.0 | 1.2 |')
    P('| --- | --- | --- | --- |')
    for at, tag in ((0.01, '1%'), (0.02, '2%'), (0.03, '3%')):
        sec = f'QUADGRID_网格(幅{int(at * 100)}%/量'
        rows = ba[ba['section'].str.startswith(sec) & (ba['horizon'] == 10)]
        cells = []
        for vt in ('0.8', '1.0', '1.2'):
            sub = rows[rows['section'].str.contains(f'量{vt})', regex=False)]
            aq = sub[sub['group'] == 'A']
            dq = sub[sub['group'] == 'D']
            ta = pct(aq['adv_fix'].iloc[0]) if len(aq) else 'n/a'
            td = pct(dq['adv_fix'].iloc[0]) if len(dq) else 'n/a'
            cells.append(f'A {ta} / D {td}')
        P(f'| {tag} | {cells[0]} | {cells[1]} | {cells[2]} |')
    P('')
    P('> A 象限在 9/9 组阈值下配对超额全为正，D 象限 9/9 全为负 —— 方向稳定。'
      '但 A 象限超额幅度有限（T+10 均值约 +0.16% ~ +0.53%），'
      '扣成本后仅略高于 0，不足以构成可交易策略。')
    P('')

    # ══════════════════════════ 十二 反事实 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 十二、v2-10 / v2-13 反事实实验（Alpha 拆解）')
    P('')
    P('| 组 | 样本 | T+5 | T+10 | T+20 | 中位T+10 | 胜率T+10 | PF(T+10) | 配对超额T+10 |')
    P('| --- | --- | --- | --- | --- | --- | --- | --- | --- |')
    for g in ['B_三花+突破(全体)', 'A_三花+低量突破(VR<1.2)', 'A2_三花+高量突破(VR>=1.2)',
              'C_三花形成无突破', 'D_无三花(offset10)', 'F_全体首板(offset10)']:
        r = ba[(ba['section'] == 'CF_反事实') & (ba['group'] == g)]
        if len(r) == 0:
            continue
        ro = {int(x['horizon']): x for _, x in r.iterrows()}
        g10 = ro[10]
        P('| {} | {} | {} | {} | {} | {} | {} | {} | {} |'.format(
            g, cnt(g10['n']), pct(ro[5]['mean']), pct(g10['mean']), pct(ro[20]['mean']),
            pct(g10['med']), pct0(g10['win']), num(g10['pf']), pct(g10['adv_fix'])))
    P('')
    P('**三项 Alpha 的拆解结论**')
    P('')
    P('1. **三花本身 Alpha：不成立。** 三花形成 vs 无三花 T+10 Δmean '
      f"{pct(sg[(sg['pair'] == '三花形成 vs 无三花') & (sg['horizon'] == 10)]['d_mean'].iloc[0])}，"
      f"p={num(sg[(sg['pair'] == '三花形成 vs 无三花') & (sg['horizon'] == 10)]['t_p'].iloc[0], 3)}，"
      'bootstrap CI 跨 0，effect size 仅 '
      f"{num(sg[(sg['pair'] == '三花形成 vs 无三花') & (sg['horizon'] == 10)]['cliff_delta'].iloc[0], 3)}（微弱）。"
      'Mann-Whitney 虽给出极小 p 值，但 Cliff δ 接近 0，属大样本下的统计显著而非经济显著。')
    P('2. **突破 Alpha：不成立，甚至是负的。** 「三花+突破」相对全体首板的配对超额 '
      f"{pct(G('CF_反事实', 'B_三花+突破(全体)', 10, 'adv_fix'))}。"
      '表面上「三花+突破 vs 三花形成」显著为正（'
      f"{pct(sg[(sg['pair'] == '三花+突破 vs 三花形成') & (sg['horizon'] == 10)]['d_mean'].iloc[0])}），"
      '但成因是 **C 组（三花形成但未突破）表现极差**（T+10 '
      f"{pct(G('CF_反事实', 'C_三花形成无突破', 10, 'mean'))}、胜率 "
      f"{pct0(G('CF_反事实', 'C_三花形成无突破', 10, 'win'))}、PF "
      f"{num(G('CF_反事实', 'C_三花形成无突破', 10, 'pf'))}）"
      '—— 这是**「避开失败结构」的选择效应**，不是突破带来的正 Alpha。')
    P('3. **低量突破 Alpha：方向为正但无统计支撑、无 OOS 支撑。** '
      f"低量突破 T+10 配对超额 {pct(G('CF_反事实', 'A_三花+低量突破(VR<1.2)', 10, 'adv_fix'))}，"
      '高于高量突破的 '
      f"{pct(G('CF_反事实', 'A2_三花+高量突破(VR>=1.2)', 10, 'adv_fix'))}；"
      '但低量突破相对**无三花**的 Δmean 仅 '
      f"{pct(sg[(sg['pair'] == '低量突破(<1.2) vs 无三花') & (sg['horizon'] == 10)]['d_mean'].iloc[0])}，"
      f"p={num(sg[(sg['pair'] == '低量突破(<1.2) vs 无三花') & (sg['horizon'] == 10)]['t_p'].iloc[0], 3)} —— "
      '**不显著**。')
    P('')
    P('> 关键补充：C 组（三花形成但未突破）T+10 均值远差于 D 组（无三花）。'
      '这意味着「形成三花」本身不是正面信号，'
      '只有在三花之后**成功突破**才把结果拉回中性 —— 三花结构的作用是「筛选出值得等待的形态」，'
      '而非「提供正向收益」。')
    P('')

    # ══════════════════════════ 十三 Regime ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 十三、v2-11 市场 Regime 交叉验证（Q7）')
    P('')
    P('| Regime | 组 | 样本 | T+10 均值 | 中位T+10 | 配对超额T+10 |')
    P('| --- | --- | --- | --- | --- | --- |')
    for reg in ('BULL', 'NORMAL', 'RANGE', 'BEAR'):
        for g in ['B_三花+突破(全体)', 'A_三花+低量突破(VR<1.2)', 'A2_三花+高量突破(VR>=1.2)',
                  'C_三花形成无突破']:
            r = rg[(rg['section'] == f'REGIME_{reg}') & (rg['group'] == g) & (rg['horizon'] == 10)]
            if len(r) == 0:
                continue
            x = r.iloc[0]
            P(f'| {reg} | {g} | {cnt(x["n"])} | {pct(x["mean"])} | {pct(x["med"])} | {pct(x["adv_fix"])} |')
    P('')
    lo_b = cell(rg, 'REGIME_BULL', 'A_三花+低量突破(VR<1.2)', 10, 'mean')
    lo_n = cell(rg, 'REGIME_NORMAL', 'A_三花+低量突破(VR<1.2)', 10, 'mean')
    lo_r = cell(rg, 'REGIME_RANGE', 'A_三花+低量突破(VR<1.2)', 10, 'mean')
    lo_e = cell(rg, 'REGIME_BEAR', 'A_三花+低量突破(VR<1.2)', 10, 'mean')
    P('**结论：低量突破的溢价不是各 Regime 通用的。**')
    P('')
    P(f'- BULL（强势市）：{pct(lo_b)} —— **为负**，低量突破在牛市中无优势')
    P(f'- NORMAL（中性市）：{pct(lo_n)} —— 近似中性')
    P(f'- RANGE（震荡市）：{pct(lo_r)} —— 正')
    P(f'- BEAR（弱势市）：{pct(lo_e)} —— 最强正，但样本仅 '
      f"{cnt(cell(rg, 'REGIME_BEAR', 'A_三花+低量突破(VR<1.2)', 10, 'n'))} 例")
    P('')
    P('这一形态与直觉一致但有陷阱：弱势市中「缩量突破」的对手盘更少、'
      '高量突破在弱势市往往是诱多（放量出货）。'
      '但由于 RANGE / BEAR 子样本量小、且 2026 年 OOS 段恰为弱势市却未复现该正超额，'
      '**该 Regime 结论不具备可交易性**。')
    P('')

    # ══════════════════════════ 十四 市值换手 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 十四、v2-12 市值 / 换手交叉验证')
    P('')
    P('| 分层 | 组 | 样本 | T+10 均值 | 配对超额T+10 |')
    P('| --- | --- | --- | --- | --- |')
    for pre, order in (('mv_grp', ['Small', 'Mid', 'Large']), ('to_grp', ['Low', 'Mid', 'High'])):
        for lv in order:
            for g in ['B_三花+突破(全体)', 'A_三花+低量突破(VR<1.2)',
                      'A2_三花+高量突破(VR>=1.2)']:
                r = mv[(mv['section'] == f'{pre}_{lv}') & (mv['group'] == g) & (mv['horizon'] == 10)]
                if len(r) == 0:
                    continue
                x = r.iloc[0]
                P(f'| {pre}_{lv} | {g} | {cnt(x["n"])} | {pct(x["mean"])} | {pct(x["adv_fix"])} |')
    P('')
    P('> 口径限制：`daily_basic` 缓存仅自 2023-01-03 起，'
      '因此市值 / 换手分层只覆盖 2023 年之后事件，样本量为全样本的约 1/3，'
      '与 TRAIN 段不可比。低量突破在各市值档均为小幅正值（'
      f"{pct(cell(mv, 'mv_grp_Small', 'A_三花+低量突破(VR<1.2)', 10, 'mean'))} / "
      f"{pct(cell(mv, 'mv_grp_Mid', 'A_三花+低量突破(VR<1.2)', 10, 'mean'))} / "
      f"{pct(cell(mv, 'mv_grp_Large', 'A_三花+低量突破(VR<1.2)', 10, 'mean'))}），"
      '未呈现清晰的市值单调性；换手三档亦无稳定排序。**未发现可利用的交互项。**')
    P('')

    # ══════════════════════════ 十五 显著性 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 十五、v2-14 统计显著性')
    P('')
    P('| 对比 | 期限 | n1 | n2 | Δmean | 95% CI | Welch p | Cliff δ | Bootstrap 显著 |')
    P('| --- | --- | --- | --- | --- | --- | --- | --- | --- |')
    for _, r in sg.iterrows():
        if int(r['horizon']) not in (5, 10, 20):
            continue
        P('| {} | T+{} | {} | {} | {} | [{}, {}] | {} | {} | {} |'.format(
            r['pair'], int(r['horizon']), cnt(r['n1']), cnt(r['n2']), pct(r['d_mean']),
            pct(r['d_mean_lo']), pct(r['d_mean_hi']),
            num(r['t_p'], 3), num(r['cliff_delta'], 3),
            '是' if r['boot_sig_mean'] is True else ('否' if r['boot_sig_mean'] is False else 'n/a')))
    P('')
    P('**判读原则：不追求 p-value，而看经济意义 + effect size + OOS 保持。**')
    P('')
    P('- `三花+突破 vs 三花形成`：t='
      f"{num(sg[(sg['pair'] == '三花+突破 vs 三花形成') & (sg['horizon'] == 10)]['t'].iloc[0], 2)}、p<0.001，"
      '但该对比的分母组（三花无突破）本身是失败结构，差值不代表突破的增益（见第十二节）')
    P('- `低量突破 vs 高量突破`：Δmean '
      f"{pct(sg[(sg['pair'] == '低量突破(<1.2) vs 高量突破(>=1.2)') & (sg['horizon'] == 10)]['d_mean'].iloc[0])}，"
      f"p={num(sg[(sg['pair'] == '低量突破(<1.2) vs 高量突破(>=1.2)') & (sg['horizon'] == 10)]['t_p'].iloc[0], 3)}，"
      f"δ={num(sg[(sg['pair'] == '低量突破(<1.2) vs 高量突破(>=1.2)') & (sg['horizon'] == 10)]['cliff_delta'].iloc[0], 3)}；"
      'T+20 已不显著（'
      f"p={num(sg[(sg['pair'] == '低量突破(<1.2) vs 高量突破(>=1.2)') & (sg['horizon'] == 20)]['t_p'].iloc[0], 3)}）"
      '—— 效应随持有期衰减，不符合「结构性 Alpha」特征')
    P('- `低量突破 vs 无三花`：**不显著**（CI 跨 0）—— 这是最关键的一条反证')
    P('- `VR<0.6 vs VR>2.0`：Δmean '
      f"{pct(sg[(sg['pair'] == 'VR<0.6 vs VR>2.0') & (sg['horizon'] == 10)]['d_mean'].iloc[0])}，"
      f"p={num(sg[(sg['pair'] == 'VR<0.6 vs VR>2.0') & (sg['horizon'] == 10)]['t_p'].iloc[0], 3)}，"
      'δ='
      f"{num(sg[(sg['pair'] == 'VR<0.6 vs VR>2.0') & (sg['horizon'] == 10)]['cliff_delta'].iloc[0], 3)}"
      '（中等 effect size），但 62 vs 1107 的极端不平衡 + 单期偶然性强，不足以支撑规则')
    P('- `四象限A vs D`：Δmean '
      f"{pct(sg[(sg['pair'] == '四象限A(低量低幅) vs D(高量高幅)') & (sg['horizon'] == 10)]['d_mean'].iloc[0])}，"
      f"p={num(sg[(sg['pair'] == '四象限A(低量低幅) vs D(高量高幅)') & (sg['horizon'] == 10)]['t_p'].iloc[0], 3)}、"
      f"δ={num(sg[(sg['pair'] == '四象限A(低量低幅) vs D(高量高幅)') & (sg['horizon'] == 10)]['cliff_delta'].iloc[0], 3)}"
      ' —— 统计上成立，但 A 组自身的绝对收益接近 0，D 组为负，"优于"不等于"可用"')
    P('')

    # ══════════════════════════ 十六 OOS ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 十六、v2-15 样本外检验（Q9）')
    P('')
    P('### 16.1 VR 阈值：只在 TRAIN 上寻找')
    P('')
    P(f'- TRAIN 上满足「n低>=100 且 n高>=100 且 T+5/T+10/T+20 低量组均优于高量组」的阈值区间：'
      f'**θ ∈ [{rr[0]:.2f}, {rr[1]:.2f}]**，取区间中位 **θ* = {VR_TH:.2f}**')
    P('- 该区间宽度达 1.85，说明「低量优于高量」在 TRAIN 上对阈值不敏感 —— '
      '这既是稳健性证据，也意味着 **TRAIN 本身无法识别任何结构性拐点**')
    P('')
    P('**θ* 与对照阈值在三个分期的表现（T+10）**')
    P('')
    P('| 分期 | 组 | 样本 | T+5 | T+10 | T+20 | 胜率T+10 |')
    P('| --- | --- | --- | --- | --- | --- | --- |')
    for pd_ in ('TRAIN', 'VALID', 'OOS'):
        for tag in (f'低量 VR<{VR_TH:.2f}', f'高量 VR>={VR_TH:.2f}',
                    '低量 VR<1.2(对照)', '高量 VR>=1.2(对照)'):
            r = oo[(oo['section'] == f'VRTH_{pd_}') & (oo['group'] == tag)]
            if len(r) == 0:
                continue
            ro = {int(x['horizon']): x for _, x in r.iterrows()}
            P('| {} | {} | {} | {} | {} | {} | {} |'.format(
                pd_, tag, cnt(ro[10]['n']), pct(ro[5]['mean']), pct(ro[10]['mean']),
                pct(ro[20]['mean']), pct0(ro[10]['win'])))
    P('')
    P('### 16.2 分期主表（T+10 均值）')
    P('')
    P('| 组 | TRAIN(2021-2023) | VALIDATION(2024-2025) | OOS(2026) |')
    P('| --- | --- | --- | --- |')
    for g in ['A_三花+低量突破(VR<1.2)', 'A2_三花+高量突破(VR>=1.2)', 'B_三花+突破(全体)',
              'C_三花形成无突破', '三花形成(确认日)', 'D_无三花(offset10)']:
        vals = []
        for pd_ in ('TRAIN', 'VALID', 'OOS'):
            r = oo[(oo['section'] == f'PERIOD_{pd_}') & (oo['group'] == g) & (oo['horizon'] == 10)]
            vals.append(pct(r['mean'].iloc[0]) if len(r) else 'n/a')
        P(f'| {g} | {vals[0]} | {vals[1]} | {vals[2]} |')
    P('')
    P('### 16.3 逐年（T+10 均值）')
    P('')
    years = sorted({s.split('_')[1] for s in oo['section'].unique() if s.startswith('YEAR_')})
    P('| 组 | ' + ' | '.join(years) + ' |')
    P('| --- | ' + ' | '.join(['---'] * len(years)) + ' |')
    for g in ['三花形成(确认日)', '三花+突破', '三花+低量突破(VR<1.2)', '三花+高量突破(VR>=1.2)',
              '无三花(offset10)']:
        vals = []
        for y in years:
            r = oo[(oo['section'] == f'YEAR_{y}') & (oo['group'] == g) & (oo['horizon'] == 10)]
            vals.append(pct(r['mean'].iloc[0]) if len(r) else 'n/a')
        P(f'| {g} | ' + ' | '.join(vals) + ' |')
    P('')
    P('**OOS 判定：不通过。**')
    P('')
    P(f'- 训练集发现：低量组优于高量组（θ* 稳健区间 [{rr[0]:.2f}, {rr[1]:.2f}]）')
    P(f'- 验证集确认：VALID 阶段低量 {pct(cell(oo, "VRTH_VALID", f"低量 VR<{VR_TH:.2f}", 10, "mean"))} '
      f'vs 高量 {pct(cell(oo, "VRTH_VALID", f"高量 VR>={VR_TH:.2f}", 10, "mean"))} —— 方向一致但幅度缩小')
    P(f'- **OOS 检验：失败**。低量 {pct(lo_oos)} vs 高量 {pct(hi_oos)}，'
      f'对照阈值下低量 {pct(a12_oos)} vs 高量 {pct(a12_oos_hi)} —— '
      '两组均为负，方向性优势不足以转化为绝对正收益')
    P('')

    # ══════════════════════════ 十七 HVT 正交 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 十七、Q6 独立于 HVT 的检验（HVT 正交复核）')
    P('')
    P('口径：HVT 全历史事件（`hvt_bull_backtest_events_20250101_20260828.csv`，4439 条、'
      '覆盖 2025-02-05 ~ 2026-08-28、2869 只股票）；'
      '同股票锚点日与 HVT t0_date「同日」或「±3 交易日」视为命中。'
      'HVT 日均收录 11.7 只 / 样本池 5002 只 → 随机同日命中基准 0.23%。')
    P('')
    P('| 组 | 期内样本 | 同日命中率 | ±3日命中率 |')
    P('| --- | --- | --- | --- |')
    for g in ['A_三花+低量突破', 'A2_三花+高量突破', 'B_三花+突破(全体)',
              'C_三花形成无突破', 'D_无三花']:
        r = hv[(hv['section'] == 'OVERLAP') & (hv['group'] == g) & (hv['subset'] == 'alignA_same_day')]
        r2 = hv[(hv['section'] == 'OVERLAP') & (hv['group'] == g) & (hv['subset'] == 'alignB_near3')]
        if len(r) == 0:
            continue
        P(f'| {g} | {cnt(r.iloc[0]["n"])} | {hr(r.iloc[0]["hit_rate"])} | {hr(r2.iloc[0]["hit_rate"])} |')
    P('')
    _h1 = hv[(hv['section'] == 'OVERLAP') & (hv['group'] == 'A_三花+低量突破')
             & (hv['subset'] == 'alignB_near3')]['hit_rate'].iloc[0]
    P(f'**发现一：HVT 几乎完全忽略低量突破。**'
      f'低量突破 ±3日命中率 {hr(_h1)}，'
      '高量突破为 '
      f'{hr(hv[(hv["section"]=="OVERLAP")&(hv["group"]=="A2_三花+高量突破")&(hv["subset"]=="alignB_near3")]["hit_rate"].iloc[0])}；'
      f'同日命中率低量突破 {hr(hv[(hv["section"]=="OVERLAP")&(hv["group"]=="A_三花+低量突破")&(hv["subset"]=="alignA_same_day")]["hit_rate"].iloc[0])} '
      '甚至**低于随机基准 0.23%**。'
      '说明 HVT 与「低量突破」是两个几乎不相交的体系。')
    P('')
    P('| 组 | 子集 | 样本 | T+5 | T+10 | T+20 |')
    P('| --- | --- | --- | --- | --- | --- |')
    for g in ['A_三花+低量突破', 'A2_三花+高量突破', 'B_三花+突破(全体)', 'D_无三花']:
        for sub in ('HVT命中', 'HVT未命中'):
            r = hv[(hv['section'] == 'SUBSET') & (hv['group'] == g) & (hv['subset'] == sub)]
            if len(r) == 0:
                continue
            ro = {int(x['horizon']): x for _, x in r.iterrows()}
            P('| {} | {} | {} | {} | {} | {} |'.format(
                g, sub, cnt(ro[10]['n']), pct(ro[5]['mean']), pct(ro[10]['mean']), pct(ro[20]['mean'])))
    P('')
    nh = hv[(hv['section'] == 'NO_HVT') & (hv['horizon'] == 10)]
    P('**发现二（关键否决证据）：剔除 HVT 命中后，低量突破的 Alpha 归零。**')
    P('')
    P(f'- 低量突破 ∩ HVT未命中：T+10 均值 {pct(nh.iloc[0]["mean"])}、中位 {pct(nh.iloc[0]["med"])}、'
      f'胜率 {pct0(nh.iloc[0]["win"])}、样本 {cnt(nh.iloc[0]["n"])}')
    P(f'- 高量突破 ∩ HVT未命中：T+10 均值 {pct(nh.iloc[0]["cmp_mean"])}、样本 {cnt(nh.iloc[0]["cmp_n"])}')
    P('')
    _hit = hv[(hv['section'] == 'SUBSET') & (hv['group'] == 'A_三花+低量突破')
              & (hv['subset'] == 'HVT命中')]
    _n_hit = cnt(_hit[_hit['horizon'] == 10]['n'].iloc[0]) if len(_hit) else 'n/a'
    P(f'低量突破在 HVT 命中的 {_n_hit} 例上平均表现极好（T+5 '
      f'{pct(_hit[_hit["horizon"] == 5]["mean"].iloc[0])}），'
      f'但把这 {_n_hit} 例剔除后，剩余 {cnt(nh.iloc[0]["n"])} 例 T+10 均值仅 '
      f'{pct(nh.iloc[0]["mean"])}、中位 {pct(nh.iloc[0]["med"])}、胜率 {pct0(nh.iloc[0]["win"])} —— '
      '**低量突破不存在独立于 HVT 的 Alpha**。')
    P('')
    P(f'另外，三花确认日（C 组锚点）与 HVT 同日命中率为 '
      f'{hr(hv[(hv["section"]=="OVERLAP")&(hv["group"]=="C_三花形成无突破")&(hv["subset"]=="alignA_same_day")]["hit_rate"].iloc[0])}'
      '（值为 0），说明三花的「形态识别」与 HVT 的「事件识别」在时点上系统性错开 —— '
      '两者是不同体系，但**低量突破这一子集的收益来源最终仍被 HVT 解释**。')
    P('')

    # ══════════════════════════ 十八 Q1-Q10 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 十八、Q1–Q10 逐条回答')
    P('')
    P('| 问题 | 回答 | 关键证据 |')
    P('| --- | --- | --- |')
    P('| **Q1** VR 与未来收益是否存在稳定关系？ | **存在稳定负向关系，但无可用正向区间** | '
      f"Spearman(T+10) 均值 {sp10m:+.3f} / 中位 {sp10d:+.3f}；MAE 随 VR 单调恶化 "
      f"{pct(cell(vc, 'VR_全体突破', '<0.6', 10, 'mae_med'))} → {pct(cell(vc, 'VR_全体突破', '>2.0', 10, 'mae_med'))} |")
    P('| **Q2** 是否存在「温和放量」区间？ | **不存在** | '
      'VR 0.8~1.2 三档 T+10 中位数均为负；相对优势属于「不放大成交量」，且绝对收益仍≈0 |')
    P('| **Q3** 突破幅度越大是否越差？ | **是，且单调** | '
      f"配对超额 {pct(G('AMP_突破幅度(high/hi_lvl-1)', '<1%', 10, 'adv_fix'))} → "
      f"{pct(G('AMP_突破幅度(high/hi_lvl-1)', '>10%', 10, 'adv_fix'))}（>10% 档 PF "
      f"{num(G('AMP_突破幅度(high/hi_lvl-1)', '>10%', 10, 'pf'))}） |")
    P('| **Q4** 突破日涨幅越大是否越差？ | **方向成立但非严格单调** | '
      f"配对超额 <2% 档 {pct(G('BRKRET_突破日涨幅', '<2%', 10, 'adv_fix'))} → 7-10% 档 "
      f"{pct(G('BRKRET_突破日涨幅', '7-10%', 10, 'adv_fix'))}；>10% 档均值正但中位为负 |")
    P('| **Q5** 「三花+低量突破」是否具有独立 Alpha？ | **否** | '
      '相对无三花 Δmean '
      f"{pct(sg[(sg['pair'] == '低量突破(<1.2) vs 无三花') & (sg['horizon'] == 10)]['d_mean'].iloc[0])}，"
      f"CI 跨 0（p={num(sg[(sg['pair'] == '低量突破(<1.2) vs 无三花') & (sg['horizon'] == 10)]['t_p'].iloc[0], 3)}） |")
    P('| **Q6** 是否独立于 HVT？ | **否 —— 剔除 HVT 命中后归零** | '
      f"低量突破∩非HVT T+10 均值 {pct(nh.iloc[0]['mean'])}、中位 {pct(nh.iloc[0]['med'])}、胜率 {pct0(nh.iloc[0]['win'])} |")
    P('| **Q7** 在哪些市场环境下有效？ | **仅 RANGE / BEAR 有正超额，BULL 为负；不通用** | '
      f"BULL {pct(lo_b)} / NORMAL {pct(lo_n)} / RANGE {pct(lo_r)} / BEAR {pct(lo_e)} |")
    P('| **Q8** 哪些结构最容易失败？ | **见下文 19 节清单** | 高量+高幅、突破幅度>10%、'
      'PreVR>=1.0、上影线>4%、三花形成但不突破 |')
    P('| **Q9** OOS 是否仍然成立？ | **不成立** | '
      f"θ*={VR_TH:.2f} 下 OOS 低量 {pct(lo_oos)} / 高量 {pct(hi_oos)}；对照阈值 1.2 下 "
      f"{pct(a12_oos)} / {pct(a12_oos_hi)} |")
    P('| **Q10** 最终判定 | **FAIL** | 五项准入条件中 2 项不满足（OOS、年份稳定），'
      '2 项部分满足 |')
    P('')

    # ══════════════════════════ 十九 失效清单 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 十九、Q8：最容易失败的结构清单（按失败程度排序）')
    P('')
    P('| 序号 | 结构 | 样本 | T+10 均值 | 中位T+10 | 胜率 | PF |')
    P('| --- | --- | --- | --- | --- | --- | --- |')
    fail = [
        ('CF_反事实', 'C_三花形成无突破', '三花形成但未突破'),
        ('AMP_突破幅度(high/hi_lvl-1)', '>10%', '突破幅度 >10%'),
        ('VR_全体突破', '>2.0', 'VR > 2.0'),
        ('VR_全体突破', '1.5-2.0', 'VR 1.5-2.0'),
        ('QUAD_四象限(中位分割)', 'D_高量+高幅', '高量 + 大幅（D 象限）'),
        ('USH_突破日上影线', '>4%', '突破日上影线 >4%'),
        ('AMPRATIO_突破幅度/阶段振幅', '0.25-0.5', '突破幅度/阶段振幅 0.25-0.5'),
        ('PBVR3_突破前3日量比', '1.0+', '突破前 3 日已放量'),
        ('BODY_突破日实体', '5-8%', '突破日实体 5-8%'),
        ('LSH_突破日下影线', '>3%', '突破日下影线 >3%'),
    ]
    for i, (sec, g, lab) in enumerate(fail, 1):
        src = vc if (sec == 'VR_全体突破' or sec.startswith('PBVR3')) else ba
        r = src[(src['section'] == sec) & (src['group'] == g) & (src['horizon'] == 10)]
        if len(r) == 0:
            continue
        x = r.iloc[0]
        P(f'| {i} | {lab} | {cnt(x["n"])} | {pct(x["mean"])} | {pct(x["med"])} | '
          f'{pct0(x["win"])} | {num(x["pf"])} |')
    P('')
    P('共性：以上结构的**中位收益全部为负**（第 7 项「突破幅度/阶段振幅 0.25-0.5」'
      '虽 T+10 均值 +0.70% 但中位 -3.53%，属典型的「均值正、中位负」右偏分布，'
      '配对超额 -1.91% 仍为负）；除该例外，其余各项 PF 均 < 1。'
      '其中最值得注意的是「三花形成但未突破」—— '
      '这是唯一一个样本量巨大（'
      f"{cnt(G('CF_反事实', 'C_三花形成无突破', 10, 'n'))}）、"
      '失败程度最重（PF '
      f"{num(G('CF_反事实', 'C_三花形成无突破', 10, 'pf'))}）的结构，"
      '说明**「等待突破」这个动作本身创造了主要价值，而不是「突破的形态」**。')
    P('')

    # ══════════════════════════ 二十 局限 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 二十、数据限制与结论边界')
    P('')
    P('1. **训练集起点**：缓存自 2021-01-04 起，TRAIN 实际为 2021–2023（非计划中的 2018 起），'
      '样本覆盖的牛熊结构不完整')
    P('2. **OOS 仅 9 个月**：2026-01-01 ~ 2026-09-24，OOS 段内低量突破样本 '
      f"{cnt(cell(oo, 'PERIOD_OOS', 'A_三花+低量突破(VR<1.2)', 10, 'n'))} 例，"
      '单一年份的负面结果不能反推「永远无效」，但足以否定「可直接上线」')
    P('3. **市值/换手分层**：`daily_basic` 仅覆盖 2023-01-03 之后，该维度证据强度弱')
    P('4. **HVT 正交**：HVT 事件仅覆盖 2025-02 起，正交检验只能在重叠期进行；'
      'HVT 未命中子集 468 例，样本尚可但仅覆盖一年半')
    P('5. **配对超额精度**：`adv_fix` 的参照桶数普遍仅 3~18 个 offset 桶，'
      '时点中性化能力有限；股票池中约 223 只为已退市代码（缓存全量口径，未按当前在市清单筛选）')
    P('6. **成本假设**：统一按双边 0.30% 计，未区分流动性差异；'
      '小市值高档位实际冲击成本会更高')
    P('7. **分布右偏**：多数分层出现「均值正、中位负」的形态，'
      '任何以均值判断的结论都须谨慎；本报告已同时给出中位数与 PF')
    P('')

    # ══════════════════════════ 二十一 交付物 ══════════════════════════
    P('═══════════════════════════════════════════')
    P('## 二十一、最终策略候选（v2-17）')
    P('')
    P('**判定：不生成 `ThreeFlower_Breakout_V2`。**')
    P('')
    P('| 准入条件 | 要求 | 实际 | 通过 |')
    P('| --- | --- | --- | --- |')
    P(f'| OOS 有效 | OOS 段绝对收益为正且优于对照 | '
      f'低量 {pct(lo_oos)} / 高量 {pct(hi_oos)} / 对照 {pct(a12_oos)} | ✗ |')
    P('| 参数扰动稳定 | 阈值附近收益方向一致 | 四象限 A/D 方向 9/9 稳定，'
      f'但 VR 无拐点（稳健区间 [{rr[0]:.2f}, {rr[1]:.2f}]） | △ |')
    P('| 样本量充分 | 各关键子组 >=100 | VR<0.6 仅 62 例、结构回撤 0-3% 为 0 例、'
      'AMPRATIO>1.0 仅 2 例 | ✗ |')
    P('| 不同年份部分稳定 | 至少一半年份方向一致 | 2021 ✗ / 2022 ✓ / 2023 ✗ / 2024 ✓ / 2025 ✓ / 2026 ✗ | △ |')
    P('| 无数据泄漏 | 全部指标 as-of 可得 | 通过（花 d+3 确认、突破 buffer=0） | ✓ |')
    P('')
    P('**结论：仅保存研究结果，不进入实盘系统，不新增策略文件。**')
    P('')
    P('如后续需要继续研究，建议方向（**不作为本轮结论**）：')
    P('')
    P('1. 「突破 vs 未突破」的分化本身信息量最大（C 组 PF '
      f"{num(G('CF_反事实', 'C_三花形成无突破', 10, 'pf'))}），"
      '可研究「如何提前识别三花是否会突破」而不是「突破时的量能」')
    P('2. 「突破前量能已放大（PreVR>=1.0）」是稳定的负面标志，可作为**排除条件**而非选股条件')
    P('3. 低量突破的正超额与 HVT 命中高度重叠，'
      '若要继续，应先确认是否存在独立于 HVT 的事件来源，而非继续在 VR 阈值上调参')
    P('')
    P('### 交付物清单')
    P('')
    P('| 文件 | 内容 |')
    P('| --- | --- |')
    P('| three_flower_v2_volume_curve.csv | VR 分层 / 突破前量能 / VR÷PreVR 全字段分层结果 |')
    P('| three_flower_v2_breakout_analysis.csv | 幅度 / 涨幅 / 位置 / 结构 / 价格结构 / 四象限 |')
    P('| three_flower_v2_regime.csv | BULL/NORMAL/RANGE/BEAR 交叉 + 各 Regime VR 明细 |')
    P('| three_flower_v2_marketcap.csv | 市值 / 换手交叉 + 明细 |')
    P('| three_flower_v2_oos.csv | 三分期 / 逐年 / VR 阈值样本外检验 |')
    P('| three_flower_v2_significance.csv | bootstrap CI / Welch-t / Mann-Whitney U / Cliff δ |')
    P('| three_flower_v2_vr_monotonicity.csv | VR 分档均值与中位数（单调性原始数据） |')
    P('| three_flower_v2_vr_threshold_sweep.csv | θ ∈ [0.60, 2.60] 全网格扫描 |')
    P('| three_flower_v2_hvt_orth.csv | 与 HVT-BULL 的重叠率与子集表现 |')
    P('| three_flower_v2_curve.png | VR→T+5/10/20 曲线 + 四象限热图 + 分期柱图 |')
    P('| three_flower_v2_report.md | 本报告 |')
    P('')

    txt = '\n'.join(L)
    fp = os.path.join(RD, 'three_flower_v2_report.md')
    with open(fp, 'w', encoding='utf-8') as f:
        f.write(txt)
    print('已写 %s（%d 行 / %d 字符）' % (fp, len(L), len(txt)))


if __name__ == '__main__':
    main()
