# -*- coding: utf-8 -*-
"""
H-TREND-PULLBACK-01  P6：汇总报告（§59 / §60）

职责：
    汇总 P1 / P1c / P2 / P3 / P4 / P5 已落盘产物，产出
        1) report.md                人读报告
        2) h_trend_pullback.json    机读汇总（含 Gate 判定表）
    本脚本只做汇总与如实呈现，不重算、不调参、不改阈值、不预设方向。

Gate 纪律（§51 / §52 / §55）：
    · Gate 表只列「本管线已实际评估」的门控（P4: G8/G9/G10/G12；P5: G13/G14/G15）。
    · 未评估的 Gate 编号一律标注 NOT_EVALUATED，绝不臆造判据。
      G1-G18 的权威定义在用户提交规格 §51/§52，该规格未落盘（见 CAVEATS）。
    · 致命 Gate 集合取自 PREREG['lethal_gates']；其中 G14（成本）已 FAIL
      → 最终状态不可能为 PASS，且不得通过改阈值救回。
    · G12 / G13 亦 FAIL，如实呈现。
"""
from __future__ import annotations

import os
import re
import json
import datetime

import numpy as np
import pandas as pd

from hp_common import PREREG, prereg_hash, HERE, OUTD, DATA, PAN

REPORT_MD = os.path.join(HERE, 'report.md')
REPORT_JSON = os.path.join(HERE, 'h_trend_pullback.json')

LETHAL = tuple(PREREG['lethal_gates'])

# P4 输出的 Gate 到 §节 的归属（编号取自 hp_model.py 的 Gate 段，不新增编号）
P4_LAYER = {'G8': 'P4/§40', 'G9': 'P4/§41', 'G10': 'P4/§42', 'G12': 'P4/§39'}


# ══════════════════════════════════════════════════════════ 小工具

def f(x, n=4, sign=True):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return 'NA'
    return ('%+.*f' if sign else '%.*f') % (n, float(x))


def pct(x, n=2):
    if x is None:
        return 'NA'
    return '%.*f%%' % (n, 100.0 * float(x))


def rd(name):
    """读取 out/ 下产物；缺失则返回 None 并登记（不静默）"""
    fp = os.path.join(OUTD, name)
    if not os.path.exists(fp):
        UNAVAILABLE.append('产物缺失: out/%s' % name)
        return None
    return pd.read_csv(fp)


def _log(name):
    fp = os.path.join(OUTD, name)
    if not os.path.exists(fp):
        UNAVAILABLE.append('日志缺失: out/%s' % name)
        return ''
    with open(fp, 'r', encoding='utf-8') as fh:
        return fh.read()


def rx(text, pat, cast=float, grp=1):
    """从日志抓一个数/串；抓不到返回 None 并登记"""
    m = re.search(pat, text)
    if not m:
        UNAVAILABLE.append('日志未匹配: %s' % pat)
        return None
    v = m.group(grp)
    return cast(v) if cast is not None else v


def rx3(text, pat):
    """从日志抓三个数（p25/p50/p75 之类的三元组）；抓不到返回 None 并登记"""
    m = re.search(pat, text)
    if not m:
        UNAVAILABLE.append('日志未匹配: %s' % pat)
        return None
    return m.groups()


def rx2(text, pat):
    """从日志抓两组数（如「整型计数 + 百分比」）；抓不到返回 None 并登记"""
    m = re.search(pat, text)
    if not m:
        UNAVAILABLE.append('日志未匹配: %s' % pat)
        return None
    return (m.group(1), m.group(2))


def nrows(path):
    try:
        import pyarrow.parquet as pq
        return int(pq.read_metadata(path).num_rows)
    except Exception:
        return None


# P3 判定规则（逐字复刻 hp_gates.py 第 462–464 行，报告层不另立规则）
def v_mom(adj, t):
    return 'PASS' if (adj > PREREG['momentum_threshold'] and t > 0) else 'FAIL'


def v_null(real, nul, p):
    return 'PASS' if (real > nul and p < PREREG['perm_alpha']) else 'FAIL'


# 盈亏平衡成本：净收益 = gross - 单边bp × 2 / 1e4 → 平衡点单边 bp = gross × 1e4 / 2
def breakeven_bps(gross):
    return None if gross is None else gross * 1e4 / 2.0


UNAVAILABLE = []


# ══════════════════════════════════════════════════════════ 读入产物

root = {}
root['prereg'] = dict(PREREG)
root['prereg_hash'] = prereg_hash()

meta = {}
try:
    with open(os.path.join(PAN, '_meta.json'), 'r', encoding='utf-8') as fh:
        _m = json.load(fh)
    meta = {'NCAL': _m.get('NCAL'), 'NCODES': _m.get('NCODES'), 'n_ind': _m.get('n_ind'),
            'date_start': (_m.get('dates') or [None])[0],
            'date_end': (_m.get('dates') or [None])[-1],
            'built_at': _m.get('built_at'), 'prereg_hash': _m.get('prereg_hash')}
except Exception as e:
    UNAVAILABLE.append('面板 meta 读取失败: %s' % e)

if meta.get('prereg_hash') and meta['prereg_hash'] != root['prereg_hash']:
    UNAVAILABLE.append('prereg_hash 不一致: meta=%s code=%s'
                       % (meta['prereg_hash'], root['prereg_hash']))

trend_days = nrows(os.path.join(DATA, 'hp_trenddays.parquet'))
e1_rows = nrows(os.path.join(DATA, 'hp_events.parquet'))
e2_rows = nrows(os.path.join(DATA, 'hp_events_e2.parquet'))

ev_log = _log('_hp_events.txt')
pan_log = _log('_hp_panel.txt')
mod_log = _log('_hp_model.txt')
cos_log = _log('_hp_costs.txt')

# 面板描述
panel = {
    'n_trade_days': rx(pan_log, r'日历 (\d+) 交易日', int),
    'phases': rx(pan_log, r'分期 TRAIN (\d+) / VALID (\d+) / OOS (\d+) / LIVE-LIKE (\d+)', None),
    'n_rows_quote': rx(pan_log, r'行情 (\d+) 行 / (\d+) 只', None),
    'sw_cov': rx(pan_log, r'SW L1 覆盖 (\d+) / (\d+)（UNKNOWN (\d+)），行业数 (\d+)', None),
    'univ_pass': rx2(pan_log, r'通过\s+(\d+)\s+([\d.]+)%'),
    'univ_st': rx2(pan_log, r'ST/\*ST\s+(\d+)\s+([\d.]+)%'),
    'univ_susp': rx2(pan_log, r'停牌/无成交\s+(\d+)\s+([\d.]+)%'),
    'univ_new': rx2(pan_log, r'上市不足60日\s+(\d+)\s+([\d.]+)%'),
    'univ_bj': rx2(pan_log, r'北交所\s+(\d+)\s+([\d.]+)%'),
    'univ_na': rx2(pan_log, r'数据缺失\(MA60\)\s+(\d+)\s+([\d.]+)%'),
    'univ_total': rx(pan_log, r'合计单元格 (\d+)', int),
    'regime_days': rx(pan_log, r'Regime: BEAR (\d+) / NORMAL (\d+) / BULL (\d+)', None),
    'trend_leg': rx(ev_log, r'trend leg (\d+)', int),
    'episode': rx(ev_log, r'Episode (\d+)', int),
    'in_ep_share': rx(ev_log, r'强趋势日内属于回撤 Episode 的比例 ([\d.]+)%', float),
    'depth_at_sig': rx3(ev_log, r'depth_at_sig\s+p25 ([\d.]+) / p50 ([\d.]+) / p75 ([\d.]+)'),
    'depth_final': rx3(ev_log, r'depth_final\s+p25 ([\d.]+) / p50 ([\d.]+) / p75 ([\d.]+)'),
    'resolved': rx(ev_log, r'resolved ([\d.]+)', float),
    'reacc1': rx(ev_log, r'reacc1 ([\d.]+)', float),
    'reacc2': rx(ev_log, r'reacc2 ([\d.]+)', float),
    'entry_ok': rx(ev_log, r'entry_ok ([\d.]+)', float),
}

# ── 产物表
ev = rd('hp_single_events.csv')
ic = rd('hp_single_ic.csv')
bands = rd('hp_single_bands.csv')
g_base = rd('hp_gates_baseline.csv')
g_match = rd('hp_gates_match.csv')
g_null = rd('hp_gates_null.csv')
m_eval = rd('hp_model_eval.csv')
m_coef = rd('hp_model_coef.csv')
m_wf = rd('hp_model_wf.csv')
m_year = rd('hp_model_year.csv')
m_regime = rd('hp_model_regime.csv')
m_grid = rd('hp_model_grid.csv')
gates_m = rd('hp_model_gates.csv')
cs_event = rd('hp_costs_event.csv')
cs_score = rd('hp_costs_score.csv')
cs_tail = rd('hp_costs_tail.csv')
cs_null = rd('hp_costs_null.csv')
cs_perm = rd('hp_costs_perm.csv')
cs_q = rd('hp_costs_quantile.csv')
cs_fdr = rd('hp_costs_fdr.csv')
gates_c = rd('hp_costs_gates.csv')


# ══════════════════════════════════════════════════════════ Gate 汇总

gates = []          # 已评估
if gates_m is not None:
    for r in gates_m.itertuples():
        g = str(r.gate)
        gates.append({
            'gate': g,
            'layer': P4_LAYER.get(g, 'P4'),
            'criterion': str(r.desc),
            'lethal': bool(g in LETHAL),
            'evidence': ('value=%s' % f(r.value, 4)),
            'verdict': str(r.verdict),
        })
if gates_c is not None:
    for r in gates_c.itertuples():
        g = str(r.gate)
        gates.append({
            'gate': g,
            'layer': str(r.layer),
            'criterion': str(r.criterion),
            'lethal': bool(int(r.lethal) == 1 or g in LETHAL),
            'evidence': ('value=%s' % f(r.value, 4)),
            'verdict': str(r.verdict),
        })

evaluated_ids = {g['gate'] for g in gates}
ALL_GATES = ['G%d' % i for i in range(1, 19)]
not_evaluated = [g for g in ALL_GATES if g not in evaluated_ids]

n_fail = sum(1 for g in gates if g['verdict'] == 'FAIL')
lethal_fail = [g['gate'] for g in gates if g['verdict'] == 'FAIL' and g['lethal']]
lethal_ne = [g for g in not_evaluated if g in LETHAL]
non_lethal_fail = [g['gate'] for g in gates if g['verdict'] == 'FAIL' and not g['lethal']]

# 结论依据所用的已核验数值（全部取自落盘产物，不在报告层重算）
_e1_gross = (cs_event[(cs_event.tag == 'E1') & (cs_event.horizon == 5) & (cs_event.bps == 0)].iloc[0]['gross_mean']
             if cs_event is not None else None)
_breakeven = breakeven_bps(_e1_gross)
_e1_net_focus = (cs_event[(cs_event.tag == 'E1') & (cs_event.horizon == 5)
                          & (cs_event.bps == PREREG['cost_focus_bps'])].iloc[0]['mean']
                 if cs_event is not None else None)
_e1_leave = (cs_tail[(cs_tail.tag == 'E1') & (cs_tail.tail_pct == 0.05)].iloc[0]['leave_mean']
             if cs_tail is not None else None)

if lethal_fail:
    final_status = 'FAIL'
    status_basis = ('致命 Gate %s FAIL（§52：致命 Gate 任一失败即不得进入策略层）；'
                    '事件层价格 Alpha 不成立——E1 h=5 毛均值 %s，低于 B1 全市场基线 %s；'
                    '动量控制 adj_alpha %s、行业控制 adj_alpha %s（P3 判据「> 0 且 t > 0」两条均 FAIL）；'
                    '盈亏平衡单边成本仅 %.1f bps（往返 %.1f bps），@%d bps 净收益 %s；'
                    'Leave-Top5%% 后均值 %s。非致命失败：%s。'
                    '已评估 %d 项，另有 %d 项未评估（规格未落盘，不得臆造）。'
                    % ('/'.join(lethal_fail),
                       f(_e1_gross),
                       f(g_base[g_base.baseline == 'B1_AllStocks'].iloc[0]['mean']) if g_base is not None else 'NA',
                       f(g_match[(g_match.tag == 'B4_MomentumMatched') & (g_match.h == 5)].iloc[0]['adj_alpha'])
                       if g_match is not None else 'NA',
                       f(g_match[(g_match.tag == 'B5_IndustryMomentumMatched') & (g_match.h == 5)].iloc[0]['adj_alpha'])
                       if g_match is not None else 'NA',
                       _breakeven if _breakeven is not None else float('nan'),
                       2.0 * _breakeven if _breakeven is not None else float('nan'),
                       PREREG['cost_focus_bps'], f(_e1_net_focus), f(_e1_leave),
                       '/'.join(non_lethal_fail) if non_lethal_fail else '无',
                       len(gates), len(not_evaluated)))
elif not_evaluated == [] and n_fail == 0:
    final_status = 'PASS'
    status_basis = '全部 Gate 评估通过。'
else:
    final_status = 'CONDITIONAL'
    status_basis = ('无致命 Gate 失败，但存在非致命失败（%s）或未评估项（%d 项）。'
                    % ('/'.join(non_lethal_fail) if non_lethal_fail else '无', len(not_evaluated)))


# ══════════════════════════════════════════════════════════ 报告正文

R = []
A = R.append

A('# H-TREND-PULLBACK-01')
A('')
A('**Strong Trend → First Pullback → Re-acceleration（趋势回撤后的二次启动 Alpha 自动科研）**')
A('')
A('| 项 | 值 |')
A('| --- | --- |')
A('| hypothesis_id | %s |' % root['prereg']['hypothesis_id'])
A('| version | %s |' % root['prereg']['version'])
A('| prereg_hash | `%s` |' % root['prereg_hash'])
A('| 生成时间 | %s |' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
A('| 最终状态 | **%s** |' % final_status)
A('| TRADING_AUTHORIZATION | `%s` |' % root['prereg']['trading_authorization'])
A('| 隔离声明 | 自包含；不 import TL-01 / HVT / ARCHIVE / H-EARN-* 等既有研究模块（§0） |')
A('')
A('本报告由 `hp_report.py`（P6）汇总 P1–P5 已落盘产物生成，只做汇总，不重算、不调参、不改阈值。')
A('')

# ── 1 预注册摘要
A('## 1. 预注册摘要（跑数前冻结）')
A('')
A('| 项 | 冻结值 |')
A('| --- | --- |')
A('| 强趋势定义 | `close > ma20 > ma60` **且** `slope20 > 0 且 slope60 >= 0`（T3 `ret20>0` 仅辅助） |')
A('| 回撤定义 | Episode 内首个 `Depth >= %.0f%% 且 Duration >= %d` 之日 = E1 信号日 |'
  % (100 * root['prereg']['pb_min_depth'], root['prereg']['pb_min_days']))
A('| 再启动定义 | `close > pullback_start_price`（E2；辅助口径 `break_previous_high`） |')
A('| 入场锚定 | 次日开盘 `open[k+1]`，目标 `exe_H = close[k+H]/open[k+1]-1`（主口径）；`raw_H` 仅描述 |')
A('| 分期 | TRAIN 2018–2022 / VALID 2023–2024 / OOS 2025 / LIVE-LIKE 2026 |')
A('| 主视界 | H_MAIN = 5（多视界 h3/h5/h10 全报） |')
A('| 成本 | 往返 = 单边 bp × 2；focus = %d bps |' % root['prereg']['cost_focus_bps'])
A('| 尾部 | Leave-Top%.0f%%；右尾 top1%%/5%%/10%% |' % (100 * root['prereg']['tail_leave_pct']))
A('| 随机化 | seed=%d，n_sim=%d，n_perm=%d |'
  % (root['prereg']['seed'], root['prereg']['n_sim'], root['prereg']['n_perm']))
A('| 多重检验 | BH-FDR，alpha=%.2f，families=%s |'
  % (root['prereg']['fdr_alpha'], ', '.join(root['prereg']['fdr_families'])))
A('| 致命 Gate | %s |' % ', '.join(LETHAL))
A('| 核心反事实 | %s（随机回撤） |' % root['prereg']['core_counterfactual'])
A('')

# ── 2 数据与样本
A('## 2. 数据与样本')
A('')
A('| 项 | 值 |')
A('| --- | --- |')
A('| 交易日 | %s（%s ~ %s） |'
  % (meta.get('NCAL'), meta.get('date_start'), meta.get('date_end')))
A('| 股票数 | %s |' % meta.get('NCODES'))
A('| 行业数（SW-L1） | %s |' % meta.get('n_ind'))
A('| 趋势日面板 | %s 行 |' % ('{:,}'.format(trend_days) if trend_days else 'NA'))
A('| E1（回撤观察）事件 | %s 条 |' % ('{:,}'.format(e1_rows) if e1_rows else 'NA'))
A('| E2（再启动）事件 | %s 条 |' % ('{:,}'.format(e2_rows) if e2_rows else 'NA'))
A('| trend leg / Episode | %s / %s |' % (panel['trend_leg'], panel['episode']))
A('| 强趋势日落在回撤 Episode 内 | %s |'
  % (pct(panel['in_ep_share'] / 100.0) if panel['in_ep_share'] is not None else 'NA'))
A('| Episode 消解 / 收复close[h] / 突破Peak | %s / %s / %s |'
  % (pct(panel['resolved']), pct(panel['reacc1']), pct(panel['reacc2'])))
A('| 入场可行率（次日可交易） | %s |' % pct(panel['entry_ok']))
A('')
if panel['univ_pass']:
    A('Universe 剔除（§5，不得静默删除）：')
    A('')
    A('| 类别 | 单元格 | 占比 |')
    A('| --- | --- | --- |')
    for lbl, key in (('通过', 'univ_pass'), ('ST/*ST', 'univ_st'), ('停牌/无成交', 'univ_susp'),
                     ('上市不足60日', 'univ_new'), ('北交所', 'univ_bj'), ('数据缺失(MA60)', 'univ_na')):
        v = panel[key]
        if v:
            A('| %s | %s | %.2f%% |' % (lbl, '{:,}'.format(int(v[0])), float(v[1])))
    if panel['univ_total']:
        A('| **合计单元格** | **%s** | — |' % '{:,}'.format(panel['univ_total']))
    A('')
if panel['depth_at_sig'] and panel['depth_final']:
    A('回撤深度：信号日 p25/p50/p75 = %s / %s / %s；Episode 末 p25/p50/p75 = %s / %s / %s'
      '（信号日深度显著浅于 Episode 最终深度，说明 E1 是「回撤刚启动」而非「回撤已充分」）。'
      % (panel['depth_at_sig'] + panel['depth_final']))
    A('')

# ── 3 事件层基础证据
A('## 3. 事件层基础证据（次日开盘进场，无成本）')
A('')
if ev is not None:
    for anchor in ('E1', 'E2'):
        sub = ev[ev['anchor'] == anchor]
        A('**%s**' % anchor)
        A('')
        A('| horizon | n | 日 | mean | median | win | t(NW) |')
        A('| --- | --- | --- | --- | --- | --- | --- |')
        for r in sub[sub['phase'] == 'ALL'].itertuples():
            A('| %d | %s | %s | %s | %s | %.3f | %s |'
              % (r.horizon, '{:,}'.format(int(r.n_event)), int(r.n_day),
                 f(r.mean), f(r.median), r.winrate, f(r.t_nw, 2)))
        A('')
    A('分期（h=5，mean）：')
    A('')
    A('| 分期 | E1 mean | E1 n | E2 mean | E2 n |')
    A('| --- | --- | --- | --- | --- |')
    for ph in ('TRAIN', 'VALID', 'OOS', 'LIVE-LIKE'):
        a = ev[(ev.anchor == 'E1') & (ev.horizon == 5) & (ev.phase == ph)]
        b = ev[(ev.anchor == 'E2') & (ev.horizon == 5) & (ev.phase == ph)]
        A('| %s | %s | %s | %s | %s |'
          % (ph, f(a.iloc[0]['mean']) if len(a) else 'NA', '{:,}'.format(int(a.iloc[0]['n_event'])) if len(a) else 'NA',
             f(b.iloc[0]['mean']) if len(b) else 'NA', '{:,}'.format(int(b.iloc[0]['n_event'])) if len(b) else 'NA'))
    A('')
if bands is not None:
    d = bands[(bands.anchor == 'E1') & (bands.kind == 'depth') & (bands.horizon == 5)]
    if len(d):
        A('回撤深度带（h=5，E1，全报不择优）：')
        A('')
        A('| 深度带 | n | mean | median |')
        A('| --- | --- | --- | --- |')
        for r in d.itertuples():
            A('| %s | %s | %s | %s |' % (r.band, '{:,}'.format(int(r.n)), f(r.mean), f(r.median)))
        A('')
        A('注：深度带内 mean 随深度非单调（0.03–0.05 与 0.05–0.08 为正、0.10+ 转零），中位数全带为负；'
          '不得据此择优（§3 禁止预设「越深越好/越浅越好」）。')
        A('')

# ── 4 单因子筛查
A('## 4. 横截面单因子筛查（§P2）')
A('')
if ic is not None:
    h5 = ic[ic['horizon'] == 5].copy()
    h5['abs_ic'] = h5['ic_all'].abs()
    h5 = h5.sort_values('abs_ic', ascending=False).head(15)
    A('h=5，|IC_all| 前 15（逐日 Spearman，各期 NW t 全报）：')
    A('')
    A('| feature | grp | IC_all | AUC_all | t_TRAIN | t_VALID | t_OOS | t_LIVE |')
    A('| --- | --- | --- | --- | --- | --- | --- | --- |')
    for _, r in h5.iterrows():
        A('| %s | %s | %s | %s | %s | %s | %s | %s |'
          % (r['feature'], r['group'], f(r['ic_all']), f(r['auc_all']),
             f(r['ic_t_TRAIN'], 2), f(r['ic_t_VALID'], 2),
             f(r['ic_t_OOS'], 2), f(r['ic_t_LIVE-LIKE'], 2)))
    A('')
    A('注：单因子 IC 为**负号为主**（回撤越深 / 波动越高 / 换手越高 → 未来 5 日越弱），'
      '且 LIVE-LIKE（2026）期普遍失去显著性；`since_pb`/`ep_days` IC ≈ 0（时长本身无信息）。')
    A('')

# ── 5 反事实与门控
A('## 5. 反事实与门控（P3，§26 / §27 / §29）')
A('')
if g_base is not None:
    A('基线对照（h=5）：')
    A('')
    A('| 基线 | n | mean |')
    A('| --- | --- | --- |')
    for r in g_base.itertuples():
        A('| %s | %s | %s |' % (r.baseline, '{:,}'.format(int(r.n)), f(r.mean)))
    A('')
    A('**关键事实**：E1 事件层均值（%s）**低于** B1 全市场基线（%s）——事件本身没有超额。'
      % (f(g_base[g_base.baseline == 'E1_Signal'].iloc[0]['mean']),
         f(g_base[g_base.baseline == 'B1_AllStocks'].iloc[0]['mean'])))
    A('')
if g_match is not None and g_null is not None:
    A('| 检查 | 节 | 判据（逐字取自 hp_gates.py） | 值 | 判定 |')
    A('| --- | --- | --- | --- | --- |')
    b4 = g_match[(g_match.tag == 'B4_MomentumMatched') & (g_match.h == 5)].iloc[0]
    b5 = g_match[(g_match.tag == 'B5_IndustryMomentumMatched') & (g_match.h == 5)].iloc[0]
    n5 = g_null[g_null.h == 5].iloc[0]
    A('| 动量控制 | §26 | adj_alpha > %.1f 且 t > 0 | %s（t %s） | **%s** |'
      % (root['prereg']['momentum_threshold'], f(b4.adj_alpha), f(b4.t_nw, 2),
         v_mom(b4.adj_alpha, b4.t_nw)))
    A('| 行业控制 | §27 | adj_alpha > %.1f 且 t > 0 | %s（t %s） | **%s** |'
      % (root['prereg']['momentum_threshold'], f(b5.adj_alpha), f(b5.t_nw, 2),
         v_mom(b5.adj_alpha, b5.t_nw)))
    A('| 随机回撤 Null | §29 | real > null（单侧）且 p < %.2f | real %s vs null %s，p %.4f | **%s** |'
      % (root['prereg']['perm_alpha'], f(n5.real_mean), f(n5.null_mean), n5.p_real_ge_null,
         v_null(n5.real_mean, n5.null_mean, n5.p_real_ge_null)))
    A('')
    A('解读：**同日动量匹配后 E1 的增量 Alpha ≈ 0，行业动量匹配后 ≈ 0**（点估计微正但 t 为负，'
      '按 P3 冻结判据「adj_alpha > 0 且 t > 0」两条均 **FAIL**）——回撤事件相对「同样处于强趋势、'
      '动量相近」的对照日没有增量；§29 通过只说明它优于「随机选回撤」，不构成增量 Alpha。')
    A('')

# ── 6 多元模型
A('## 6. 多元模型与稳健性（P4，§35–§43）')
A('')
if m_eval is not None:
    # 源产物 hp_model_eval.csv 中 (tag, model, horizon, phase) 存在重复行块（P4 落盘时同一配置写两次）；
    # 此处按主键去重后呈现，并在局限节如实声明，不静默改写源文件。
    _m_eval_dup = int(len(m_eval) - len(m_eval.drop_duplicates(subset=['tag', 'model', 'horizon', 'phase'])))
    m_eval = m_eval.drop_duplicates(subset=['tag', 'model', 'horizon', 'phase'])
    if _m_eval_dup:
        UNAVAILABLE.append('hp_model_eval.csv 含重复行块 %d 行（已按主键去重呈现，源文件未改）' % _m_eval_dup)
    allr = m_eval[(m_eval['tag'] == 'TRAIN_FIT') & (m_eval['phase'] == 'ALL')]
    A('池化 OLS（TRAIN 拟合；特征逐日横截面百分位秩，缺失填 0）：')
    A('')
    A('| model | horizon | n | IC | t | AUC_top10 | Prec10 | mono | 首尾档价差 |')
    A('| --- | --- | --- | --- | --- | --- | --- | --- | --- |')
    for r in allr.itertuples():
        A('| %s | %d | %s | %s | %s | %s | %s | %s | %s |'
          % (r.model, r.horizon, '{:,}'.format(int(r.n_obs)) if np.isfinite(r.n_obs) else 'NA',
             f(r.ic), f(r.ic_t, 2), f(r.auc), f(r.prec10), f(r.mono_rho, 3), f(r.q_spread)))
    A('')
    A('分期（M1_PB, h=5）：')
    A('')
    A('| 分期 | 日 | IC | t | AUC | Prec10 |')
    A('| --- | --- | --- | --- | --- | --- |')
    sub = m_eval[(m_eval['tag'] == 'TRAIN_FIT') & (m_eval.model == 'M1_PB') & (m_eval.horizon == 5)
                 & (m_eval.phase != 'ALL')]
    for ph in ('TRAIN', 'VALID', 'OOS', 'LIVE-LIKE'):
        r = sub[sub.phase == ph]
        if len(r):
            r = r.iloc[0]
            A('| %s | %s | %s | %s | %s | %s |'
              % (ph, int(r.n_day), f(r.ic), f(r.ic_t, 2), f(r.auc), f(r.prec10)))
    A('')
if m_coef is not None:
    m1c = m_coef[m_coef.model == 'M1_PB']
    A('系数符号稳定性（分阶段重估）：符号稳定特征 **%d / %d**。'
      % (int(m1c['sign_stable'].sum()), len(m1c)))
    A('')
if m_wf is not None:
    A('Walk-Forward（训练=目标年前 3 个完整年）：')
    A('')
    A('| 测试年 | n_train | n_test | IC | t | AUC |')
    A('| --- | --- | --- | --- | --- | --- |')
    for r in m_wf.itertuples():
        A('| %d | %s | %s | %s | %s | %s |'
          % (r.test_year, '{:,}'.format(int(r.n_train)), '{:,}'.format(int(r.n_test)),
             f(r.ic), f(r.ic_t, 2), f(r.auc)))
    A('')
    A('为正窗口 %d / %d（阈值 > %.2f）。'
      % (int((m_wf['ic'] > 0).sum()), len(m_wf), root['prereg']['wf_pos_frac']))
    A('')
    A('注：下表「分年」与本表为**同一分数**（§41 规定分年统计统一使用 Walk-Forward 严格样本外分数），'
      '故数值逐行一致，非两次独立验证。2026（LIVE-LIKE）IC 降至 +0.0450（t +1.11，已不显著）。')
    A('')
if m_regime is not None:
    A('Regime（Walk-Forward 分数）：')
    A('')
    A('| regime | 日 | IC | t |')
    A('| --- | --- | --- | --- |')
    for r in m_regime.itertuples():
        A('| %s | %s | %s | %s |' % (r.regime, int(r.n_day), f(r.ic), f(r.ic_t, 2)))
    A('')
if m_grid is not None:
    # 与 hp_model_eval.csv 同类落盘瑕疵：源产物中 (param, value) 存在完全相同的重复行
    # （如 pb_min_days=2.0 出现两次）；按主键去重后呈现，并在局限节如实声明，不静默改写源文件。
    _m_grid_dup = int(len(m_grid) - len(m_grid.drop_duplicates(subset=['param', 'value'])))
    m_grid = m_grid.drop_duplicates(subset=['param', 'value'])
    if _m_grid_dup:
        UNAVAILABLE.append('hp_model_grid.csv 含重复行块 %d 行（已按主键去重呈现，源文件未改）' % _m_grid_dup)
    A('参数邻域敏感性（§43，一次只扰动一个参数，全报）：')
    A('')
    A('| 参数 | 值 | n | mean | median | t | 状态 |')
    A('| --- | --- | --- | --- | --- | --- | --- |')
    for r in m_grid.itertuples():
        if str(r.status) == 'OK':
            A('| %s | %s | %s | %s | %s | %s | %s |'
              % (r.param, r.value, '{:,}'.format(int(r.n_event)),
                 f(r.mean), f(r.median), f(r.t_nw, 2), r.note))
        else:
            A('| %s | %s | — | — | — | — | **%s** |' % (r.param, r.value, r.status))
    A('')
    A('注：`ma_fast` / `ma_slow` 邻域标 `NOT_RUN_NEEDS_RERUN`（改变趋势定义需重跑 P1/P1c），如实标注不静默。')
    A('')

# ── 7 成本 / 尾部 / Null / 置换 / 多重检验
A('## 7. 成本、尾部、Null、置换与多重检验（P5，§44–§50）')
A('')
A('### 7.1 成本敏感性（§44，G14）')
A('')
if cs_event is not None:
    for tag in ('E1', 'E2'):
        sub = cs_event[(cs_event.tag == tag) & (cs_event.horizon == 5)]
        if not len(sub):
            continue
        g = sub[sub.bps == 0].iloc[0]['gross_mean']
        A('**%s h=5**：gross %s → 盈亏平衡**单边**成本 ≈ %.1f bps（往返 %.1f bps）'
          % (tag, f(g), breakeven_bps(g), 2.0 * breakeven_bps(g)))
        A('')
        A('| bps | net mean | median | win | t(NW) |')
        A('| --- | --- | --- | --- | --- |')
        for r in sub.itertuples():
            A('| %d | %s | %s | %.3f | %s |' % (int(r.bps), f(r.mean), f(r.median), r.winrate, f(r.t_nw, 2)))
        A('')
if cs_score is not None:
    s = cs_score[(cs_score.score == 's_m1') & (cs_score.horizon == 5)]
    if len(s):
        A('分数选择层（SAMPLE 内逐日 top10% 等权；未建模重叠持仓资金占用，仅量级参考）：')
        A('')
        A('| bps | 选层净收益 | 净超额 | t |')
        A('| --- | --- | --- | --- |')
        for r in s.itertuples():
            A('| %d | %s | %s | %s |' % (int(r.bps), f(r.sel_mean_net), f(r.alpha_net), f(r.alpha_t_nw, 2)))
        A('')

A('### 7.2 尾部依赖与右尾贡献（§46，G13）')
A('')
if cs_tail is not None:
    A('| tag | n | gross/leave基准 mean | 右尾贡献 top1%/5%/10%（占总额） | leave-top1% | leave-top5% | leave-top10% |')
    A('| --- | --- | --- | --- | --- | --- | --- |')
    for tag in ('E1', 'E2', 's_m1_TOP10', 's_m2_TOP10'):
        sub = cs_tail[cs_tail.tag == tag]
        if not len(sub):
            continue
        vals = {float(r.tail_pct): r for r in sub.itertuples()}
        gross = None
        if tag in ('E1', 'E2') and cs_event is not None:
            gross = cs_event[(cs_event.tag == tag) & (cs_event.horizon == 5)
                             & (cs_event.bps == 0)].iloc[0]['gross_mean']
        if tag.startswith('s_m') and cs_score is not None:
            gross = cs_score[(cs_score.score == tag[:4]) & (cs_score.horizon == 5)
                             & (cs_score.bps == 0)].iloc[0]['sel_mean_gross']
        A('| %s | %s | %s | %.2f / %.2f / %.2f | %s | %s | %s |'
          % (tag, '{:,}'.format(int(vals[0.05].n_left)),
             f(gross) if gross is not None else 'NA',
             vals[0.01].removed_sum_share, vals[0.05].removed_sum_share, vals[0.10].removed_sum_share,
             f(vals[0.01].leave_mean), f(vals[0.05].leave_mean), f(vals[0.10].leave_mean)))
    A('')
    A('注：E1 剔除最优 5%% 后均值转负（leave-top5 %s）→ **收益高度依赖右尾**。'
      % f(cs_tail[(cs_tail.tag == 'E1') & (cs_tail.tail_pct == 0.05)].iloc[0]['leave_mean']))
    A('')

A('### 7.3 Null 模型 N1–N6（§48）')
A('')
if cs_null is not None:
    A('| Null | 单位 | null mean | sd | p(real>=null) | p_two | 备注 |')
    A('| --- | --- | --- | --- | --- | --- | --- |')
    for r in cs_null.itertuples():
        note = ''
        if r.null == 'N1_RandomStock':
            note = '全有效域均值 %s **高于** E1 → 事件层低于全市场基线' % f(r.null_mean)
        if r.null == 'N4_MomentumMatched':
            note = 'matched %s；adj_alpha %s' % (f(r.matched_mean), f(r.adj_alpha))
        if r.null == 'N5_IndustryMomentumMatched':
            note = 'matched %s；adj_alpha %s' % (f(r.matched_mean), f(r.adj_alpha))
        if r.null == 'N6_ShuffledPullbackQuality':
            note = '观测 IC %s（打乱后归零）' % f(r.real_mean)
        A('| %s | %s | %s | %s | %.4f | %s | %s |'
          % (r.null, r.unit, f(r.null_mean), f(r.null_sd, 4),
             r.p_real_ge_null, f(r.p_two) if np.isfinite(getattr(r, 'p_two', np.nan)) else 'NA', note))
    A('')

A('### 7.4 置换检验（§49，G15）与十分位价差（§50）')
A('')
if cs_perm is not None:
    A('| score | 统计量 | 观测 | null mean | sd | 单侧 p | 双侧 p | n_perm |')
    A('| --- | --- | --- | --- | --- | --- | --- | --- |')
    for r in cs_perm.itertuples():
        A('| %s | %s | %s | %s | %s | %.4f | %s | %d |'
          % (r.score, r.stat, f(r.obs), f(r.null_mean), f(r.null_sd, 4),
             r.p_hi, f(r.p_two) if np.isfinite(getattr(r, 'p_two', np.nan)) else 'NA', int(r.n_perm)))
    A('')
    A('注：IC 方向通过置换检验（单侧 p 0.0010），但 **AUC 观测值低于 0.5（0.41 / 0.41）、单侧 p = 1.0000**——'
      '即分数对「未来 top10%% 赢家」的排序方向与随机相反（与 G12 的 `AUC >= %.2f` 判据一致地不达标）。'
      '两表并读：IC 显著 ≠ 可交易的多空区分度。' % root['prereg']['auc_min'])
    A('')
if cs_q is not None:
    A('| score | horizon | 日 | 首尾档价差 | t(NW) |')
    A('| --- | --- | --- | --- | --- |')
    for r in cs_q.itertuples():
        A('| %s | %d | %s | %s | %s |' % (r.score, r.horizon, int(r.n_day), f(r.q_spread), f(r.t_nw, 2)))
    A('')

A('### 7.5 多重检验（§50，BH-FDR alpha=%.2f）' % root['prereg']['fdr_alpha'])
A('')
if cs_fdr is not None:
    A('| family | 检验数 | 原始 p<0.05 | BH 通过（族内） | BH 通过（合并池） |')
    A('| --- | --- | --- | --- | --- |')
    for fam, sub in cs_fdr.groupby('family', sort=False):
        A('| %s | %d | %d | %d | %d |'
          % (fam, len(sub), int((sub.p_raw < 0.05).sum()),
             int(sub.reject.sum()), int(sub.reject_pooled.sum())))
    A('| **合并（pooled）** | %d | %d | — | %d |'
      % (len(cs_fdr), int((cs_fdr.p_raw < 0.05).sum()), int(cs_fdr.reject_pooled.sum())))
    A('')

# ── 8 Gate 汇总
A('## 8. Gate 判定汇总')
A('')
A('### 8.1 本管线已评估的 Gate')
A('')
A('| Gate | 层 | 致命 | 判据 | 证据 | 判定 |')
A('| --- | --- | --- | --- | --- | --- |')
for g in gates:
    A('| %s | %s | %s | %s | %s | **%s** |'
      % (g['gate'], g['layer'], '是' if g['lethal'] else '否',
         str(g['criterion']).replace('|', r'\|'), str(g['evidence']).replace('|', r'\|'), g['verdict']))
A('')
A('已评估 %d 项，失败 %d 项；其中致命 Gate 失败 %s。'
  % (len(gates), n_fail, ('**%s**' % '/'.join(lethal_fail)) if lethal_fail else '无'))
A('')

A('### 8.2 P3 反事实检查（节号已知，G 编号待规格确认）')
A('')
A('P3 脚本已声明其 G 编号须以用户规格 §51/§52 为准，故此处按节号如实列出，**不映射为 G 编号**。')
A('')
A('| 节 | 判据 | 值 | 判定 |')
A('| --- | --- | --- | --- |')
if g_match is not None and g_null is not None:
    b4 = g_match[(g_match.tag == 'B4_MomentumMatched') & (g_match.h == 5)].iloc[0]
    b5 = g_match[(g_match.tag == 'B5_IndustryMomentumMatched') & (g_match.h == 5)].iloc[0]
    n5 = g_null[g_null.h == 5].iloc[0]
    A('| §26 动量控制 | adj_alpha > 0 且 t > 0 | %s（t %s） | **%s** |'
      % (f(b4.adj_alpha), f(b4.t_nw, 2), v_mom(b4.adj_alpha, b4.t_nw)))
    A('| §27 行业控制 | adj_alpha > 0 且 t > 0 | %s（t %s） | **%s** |'
      % (f(b5.adj_alpha), f(b5.t_nw, 2), v_mom(b5.adj_alpha, b5.t_nw)))
    A('| §29 随机回撤 Null | real > null（单侧 p<%.2f） | p %.4f | **%s** |'
      % (root['prereg']['perm_alpha'], n5.p_real_ge_null,
         v_null(n5.real_mean, n5.null_mean, n5.p_real_ge_null)))
A('')

A('### 8.3 未评估的 Gate')
A('')
A('下列 Gate 编号在 `PREREG[\'lethal_gates\']` 中出现（或属于 G1–G18 序列），'
  '但本管线未实现其判据；**权威定义在用户提交规格 §51/§52，该规格未落盘**，'
  '故一律标注 `NOT_EVALUATED`，不臆造判据。')
A('')
A('| Gate | 状态 |')
A('| --- | --- |')
for g in not_evaluated:
    A('| %s%s | NOT_EVALUATED |' % (g, '（致命）' if g in LETHAL else ''))
A('')

A('## 9. 结论')
A('')
A('**最终状态：%s**' % final_status)
A('')
A('判定依据：%s' % status_basis)
A('')
A('关键回答：')
A('')
A('| # | 问题 | 回答 |')
A('| --- | --- | --- |')
A('| 1 | 强趋势中的首次回撤事件本身有没有正收益？ | 有毛收益但**无统计意义且低于全市场基线**：E1 h=5 exe 均值 +0.0021（t −0.08），B1 全市场 +0.0030。 |')
A('| 2 | 扣除动量后是否还有增量？ | **没有**：§26 同日动量匹配 adj_alpha +0.0000（t −1.06），判据「> 0 且 t > 0」→ FAIL。 |')
A('| 3 | 扣除行业后是否还有增量？ | **没有**：§27 同行业+动量匹配 adj_alpha +0.0005（t −0.98），判据「> 0 且 t > 0」→ FAIL。 |')
A('| 4 | 是否优于「随机选回撤」？ | 是（§29 单侧 p 0.0010，PASS），但该对照过弱，不构成增量 Alpha。 |')
A('| 5 | 回撤质量特征有没有横截面排序信息？ | **有**：模型 IC +0.1114（t +11.83），WF 6/6 为正，置换单侧 p 0.0010。但 AUC_top10 仅 0.4099（< 0.5，置换单侧 p 1.0000），OOS 区分度不达标（G12 FAIL）。 |')
A('| 6 | 扣成本后还成立吗？ | **不成立**：盈亏平衡单边成本仅 10.4 bps（往返 20.8 bps）；@30 bps 净 −0.0039（t −3.40）；G14（致命）FAIL。 |')
A('| 7 | 收益是否依赖少数极值？ | **是**：剔除最优 5% 后 E1 均值 −0.0096；G13 FAIL。 |')
A('| 8 | 能否进入策略层？ | **不能**：致命 Gate G14 FAIL；`TRADING_AUTHORIZATION = %s`。 |' % root['prereg']['trading_authorization'])
A('')

A('## 10. 局限与声明')
A('')
for c in (UNAVAILABLE if UNAVAILABLE else []):
    A('- **数据/产物缺口**：%s。' % c)
A('- **规格缺口（重要）**：用户提交的 63 节规格（§0–§63）未落盘，其中 §51/§52 的 '
  'G1–G18 权威定义不可得。本报告 Gate 表只呈现管线实际评估的门控（P4 的 G8/G9/G10/G12、'
  'P5 的 G13/G14/G15），其余编号标 `NOT_EVALUATED`。**最终状态可能随规格补齐而重判。**')
A('- **G4 / G18**：`hp_common.py` 注释标明二者与「momentum-adjusted alpha > 0」相关；'
  '§26 动量控制、§27 行业控制两条结果大概率与其对应，但**编号映射未经规格确认**，故未回填。')
A('- **成本口径**：往返 = 单边 bp × 2（`open[k+1]` 买 / `close[k+H]` 卖各一次）；'
  '未建模冲击成本、涨跌停不可成交与资金占用。')
A('- **分数选择层**：逐日 top10% 等权，未建模重叠持仓与资金约束，仅作量级参考。')
A('- **§43 参数邻域**：`ma_fast` / `ma_slow` 未跑（需重跑 P1/P1c），已如实标注。')
A('- **统计口径**：t 为 Newey-West（lag=19）；事件层与日度序列口径不同，可异号。')
A('- **纪律声明**：本报告不修改任何预注册阈值、不做事后择优、不对失败 Gate 救援（§52/§55）。'
  '`TRADING_AUTHORIZATION = %s`——任何 PASS 亦只代表科研层验证，不代表实盘授权。'
  % root['prereg']['trading_authorization'])
A('')

with open(REPORT_MD, 'w', encoding='utf-8') as fh:
    fh.write('\n'.join(R))


# ══════════════════════════════════════════════════════════ JSON 汇总

json_costs = {}
if cs_event is not None:
    for tag in ('E1', 'E2'):
        sub = cs_event[cs_event.tag == tag]
        json_costs[tag] = []
        for r in sub.itertuples():
            json_costs[tag].append({'horizon': int(r.horizon), 'bps': float(r.bps),
                                    'n': int(r.n), 'gross_mean': float(r.gross_mean),
                                    'net_mean': float(r.mean), 'winrate': float(r.winrate),
                                    't_nw': float(r.t_nw)})
        g5 = cs_event[(cs_event.tag == tag) & (cs_event.horizon == 5) & (cs_event.bps == 0)]
        if len(g5):
            be = breakeven_bps(float(g5.iloc[0]['gross_mean']))
            json_costs[tag + '_breakeven_bps_oneside'] = be
            json_costs[tag + '_breakeven_bps_roundtrip'] = (None if be is None else 2.0 * be)

json_tail = {}
if cs_tail is not None:
    for tag in ('E1', 'E2', 's_m1_TOP10'):
        sub = cs_tail[cs_tail.tag == tag]
        json_tail[tag] = {('leave_top%.0f%%' % (100 * r.tail_pct)): float(r.leave_mean)
                          for r in sub.itertuples()}

json_nulls = []
if cs_null is not None:
    for r in cs_null.itertuples():
        d = {'null': r.null, 'n_unit': int(r.n_unit), 'unit': r.unit,
             'real_mean': float(r.real_mean), 'null_mean': float(r.null_mean),
             'null_sd': float(r.null_sd), 'p_real_ge_null': float(r.p_real_ge_null)}
        if np.isfinite(getattr(r, 'p_two', np.nan)):
            d['p_two'] = float(r.p_two)
        if np.isfinite(getattr(r, 'adj_alpha', np.nan)):
            d['adj_alpha'] = float(r.adj_alpha)
        json_nulls.append(d)

json_perm = []
if cs_perm is not None:
    for r in cs_perm.itertuples():
        d = {'score': r.score, 'stat': r.stat, 'obs': float(r.obs),
             'null_mean': float(r.null_mean), 'null_sd': float(r.null_sd),
             'p_hi': float(r.p_hi), 'n_perm': int(r.n_perm)}
        if np.isfinite(getattr(r, 'p_two', np.nan)):
            d['p_two'] = float(r.p_two)
        json_perm.append(d)

json_fdr = {}
if cs_fdr is not None:
    for fam, sub in cs_fdr.groupby('family', sort=False):
        json_fdr[fam] = {'n_tests': int(len(sub)),
                         'n_raw_p_lt_05': int((sub.p_raw < 0.05).sum()),
                         'n_reject_family': int(sub.reject.sum()),
                         'n_reject_pooled': int(sub.reject_pooled.sum())}
    json_fdr['pooled'] = {'n_tests': int(len(cs_fdr)),
                          'n_raw_p_lt_05': int((cs_fdr.p_raw < 0.05).sum()),
                          'n_reject_pooled': int(cs_fdr.reject_pooled.sum())}

headline = {}
if m_eval is not None:
    r = m_eval[(m_eval['tag'] == 'TRAIN_FIT') & (m_eval.model == 'M1_PB')
               & (m_eval.horizon == 5) & (m_eval.phase == 'ALL')]
    if len(r):
        r = r.iloc[0]
        headline = {'model': 'M1_PB', 'target': 'exe_5 (次日开盘进场, h=5)',
                    'fit': 'TRAIN(2018-2022) pooled OLS', 'n_obs': int(r.n_obs),
                    'ic': float(r.ic), 'ic_t': float(r.ic_t), 'auc_top10': float(r.auc),
                    'prec10': float(r.prec10), 'monotonicity': float(r.mono_rho),
                    'q_spread': float(r.q_spread)}
if g_base is not None:
    headline['e1_event_mean_h5'] = float(g_base[g_base.baseline == 'E1_Signal'].iloc[0]['mean'])
    headline['b1_allstock_mean_h5'] = float(g_base[g_base.baseline == 'B1_AllStocks'].iloc[0]['mean'])
    headline['b2_strongtrend_mean_h5'] = float(g_base[g_base.baseline == 'B2_StrongTrend'].iloc[0]['mean'])

json_gates = [{'gate': g['gate'], 'layer': g['layer'], 'lethal': g['lethal'],
               'criterion': g['criterion'], 'evidence': g['evidence'],
               'verdict': g['verdict']} for g in gates]

json_cf = []
if g_match is not None and g_null is not None:
    b4 = g_match[(g_match.tag == 'B4_MomentumMatched') & (g_match.h == 5)].iloc[0]
    b5 = g_match[(g_match.tag == 'B5_IndustryMomentumMatched') & (g_match.h == 5)].iloc[0]
    n5 = g_null[g_null.h == 5].iloc[0]
    json_cf = [
        {'section': '§26', 'check': 'momentum control',
         'criterion': 'momentum-adjusted alpha > 0 且 t > 0',
         'adj_alpha': float(b4.adj_alpha), 't_nw': float(b4.t_nw),
         'verdict': v_mom(b4.adj_alpha, b4.t_nw)},
        {'section': '§27', 'check': 'industry control',
         'criterion': 'momentum-adjusted alpha > 0 且 t > 0',
         'adj_alpha': float(b5.adj_alpha), 't_nw': float(b5.t_nw),
         'verdict': v_mom(b5.adj_alpha, b5.t_nw)},
        {'section': '§29', 'check': 'random-pullback null',
         'criterion': 'real > null (one-sided p < %.2f)' % PREREG['perm_alpha'],
         'real_mean': float(n5.real_mean), 'null_mean': float(n5.null_mean),
         'p_real_ge_null': float(n5.p_real_ge_null),
         'verdict': v_null(n5.real_mean, n5.null_mean, n5.p_real_ge_null)},
    ]

json_model = {}
if m_eval is not None:
    phases = {}
    sub = m_eval[(m_eval['tag'] == 'TRAIN_FIT') & (m_eval.model == 'M1_PB') & (m_eval.horizon == 5)
                 & (m_eval.phase != 'ALL')]
    for r in sub.itertuples():
        phases[r.phase] = {'n_day': int(r.n_day), 'ic': float(r.ic), 'ic_t': float(r.ic_t),
                           'auc': float(r.auc), 'prec10': float(r.prec10)}
    horizons = {}
    sub = m_eval[(m_eval['tag'] == 'TRAIN_FIT') & (m_eval.model == 'M1_PB') & (m_eval.phase == 'ALL')]
    for r in sub.itertuples():
        horizons['h%d' % r.horizon] = {'ic': float(r.ic), 'ic_t': float(r.ic_t), 'auc': float(r.auc),
                                       'prec10': float(r.prec10), 'mono': float(r.mono_rho)}
    json_model = {'phases': phases, 'horizons': horizons}
if m_wf is not None:
    json_model['walk_forward'] = [{'year': int(r.test_year), 'ic': float(r.ic), 'ic_t': float(r.ic_t),
                                   'auc': float(r.auc)} for r in m_wf.itertuples()]
    json_model['wf_positive_share'] = float((m_wf['ic'] > 0).mean())
if m_year is not None:
    json_model['year'] = [{'year': int(r.year), 'ic': float(r.ic), 'ic_t': float(r.ic_t)}
                          for r in m_year.itertuples()]
if m_regime is not None:
    json_model['regime'] = [{'regime': r.regime, 'ic': float(r.ic), 'ic_t': float(r.ic_t)}
                            for r in m_regime.itertuples()]
if m_coef is not None:
    m1c = m_coef[m_coef.model == 'M1_PB']
    json_model['sign_stable_features'] = int(m1c['sign_stable'].sum())
    json_model['n_features'] = int(len(m1c))

json_grid = []
if m_grid is not None:
    for r in m_grid.itertuples():
        json_grid.append({'param': r.param, 'value': float(r.value), 'status': str(r.status),
                          'n_event': (None if not np.isfinite(getattr(r, 'n_event', np.nan)) else int(r.n_event)),
                          'mean': (None if not np.isfinite(getattr(r, 'mean', np.nan)) else float(r.mean)),
                          't_nw': (None if not np.isfinite(getattr(r, 't_nw', np.nan)) else float(r.t_nw)),
                          'note': str(r.note)})

artifacts = []
for fn, p in (('hp_trenddays.parquet', os.path.join(DATA, 'hp_trenddays.parquet')),
              ('hp_events.parquet', os.path.join(DATA, 'hp_events.parquet')),
              ('hp_events_e2.parquet', os.path.join(DATA, 'hp_events_e2.parquet')),
              ('hp_model_score.parquet', os.path.join(DATA, 'hp_model_score.parquet')),
              ('hp_prereg.json', os.path.join(DATA, 'hp_prereg.json'))):
    if os.path.exists(p):
        artifacts.append({'file': fn, 'bytes': os.path.getsize(p)})
for fn in sorted(os.listdir(OUTD)):
    if fn.endswith('.csv'):
        artifacts.append({'file': 'out/' + fn, 'bytes': os.path.getsize(os.path.join(OUTD, fn))})

root.update({
    'created': '2026-09-27',
    'generated_at': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    'final_status': final_status,
    'status_basis': status_basis,
    'lethal_gates': list(LETHAL),
    'lethal_gate_failed': lethal_fail,
    'lethal_gates_not_evaluated': lethal_ne,
    'lethal_requirement_satisfied': False if lethal_fail else None,
    'n_gates_evaluated': len(gates),
    'n_gates_failed': n_fail,
    'trading_authorization': root['prereg']['trading_authorization'],
    'data_scope': {
        'n_trade_days': meta.get('NCAL'), 'date_start': meta.get('date_start'),
        'date_end': meta.get('date_end'), 'n_codes': meta.get('NCODES'),
        'n_industry_l1': meta.get('n_ind'),
        'trend_days': trend_days, 'e1_events': e1_rows, 'e2_events': e2_rows,
        'phases': root['prereg']['phases'],
        'universe': {k: panel[k] for k in ('univ_pass', 'univ_st', 'univ_susp', 'univ_new',
                                           'univ_bj', 'univ_na')},
        'descriptive': {k: panel[k] for k in ('trend_leg', 'episode', 'in_ep_share',
                                              'depth_at_sig', 'depth_final', 'resolved',
                                              'reacc1', 'reacc2', 'entry_ok', 'regime_days')},
    },
    'gates': json_gates,
    'gates_not_evaluated': not_evaluated,
    'counterfactual_checks': json_cf,
    'headline': headline,
    'model': json_model,
    'param_grid': json_grid,
    'costs': json_costs,
    'tail': json_tail,
    'nulls': json_nulls,
    'permutation': json_perm,
    'fdr': json_fdr,
    'artifacts': artifacts,
    'unavailable': UNAVAILABLE,
    'caveats': [
        '规格缺口：用户提交的 63 节规格（§0–§63）未落盘，§51/§52 的 G1–G18 权威定义不可得；'
        '本报告 Gate 表只呈现管线实际评估的门控，其余编号标 NOT_EVALUATED，最终状态可能随规格补齐而重判。',
        '致命 Gate G14（成本，focus=30bps）FAIL → 最终状态不可能为 PASS，且不得通过改阈值救援（§52/§55）。',
        'G12（OOS 区分度）与 G13（尾部依赖）亦 FAIL。',
        'G4/G18 与 momentum-adjusted alpha 相关（见 hp_common.py 注释）；§26/§27 两条结果大概率对应，'
        '但编号映射未经规格确认，故未回填为 Gate 判定。',
        '成本口径：往返 = 单边 bp × 2；未建模冲击成本、涨跌停不可成交与资金占用。',
        '分数选择层为逐日 top10% 等权，未建模重叠持仓资金占用，仅作量级参考。',
        '§43 参数邻域中 ma_fast / ma_slow 标 NOT_RUN_NEEDS_RERUN（需重跑 P1/P1c），已如实标注。',
        't 统计量为 Newey-West（lag=19）；事件层与日度序列口径不同，可异号。',
        'TRADING_AUTHORIZATION = NO；任何 PASS 亦只代表科研层验证，不代表实盘授权。',
    ],
})

with open(REPORT_JSON, 'w', encoding='utf-8') as fh:
    json.dump(root, fh, ensure_ascii=False, indent=2, default=str)

print('report.md           -> %s' % REPORT_MD)
print('h_trend_pullback.json -> %s' % REPORT_JSON)
print('final_status=%s  gates_evaluated=%d  failed=%d  lethal_failed=%s  lethal_not_evaluated=%s'
      % (final_status, len(gates), n_fail, lethal_fail, lethal_ne))
if UNAVAILABLE:
    print('UNAVAILABLE(%d):' % len(UNAVAILABLE))
    for u in UNAVAILABLE:
        print('  - ' + u)
print('DONE')
