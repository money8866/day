# -*- coding: utf-8 -*-
"""首板后「第一次回撤 + 收盘强弱 / 量能」候选 —— 可交易(事件链)口径定向验证

背景
  _tr_core.py 的 L1/L2 候选空间在 **条件化(锚点 T0)** 口径下出现若干正向"孤岛"：
    · D2b  首次收盘回撤<=-2% 且当日收盘位置>=0.6   T+5 超额 +4.03pp (N=3545)
    · V2   当前量/首板量 最高档                        T+5 超额 +3.98pp (N=10968)
  但 T0 锚点不可交易（条件包含 T0 之后才可见的信息）。
  本脚本把这些候选改写为 **事件日收盘进场** 的可交易口径，并分期检验。

纪律
  · 候选全部来自任务书 §10 (D2 收盘强弱) / §12 (量能) 预登记空间，未新增条件组合；
  · 不做阈值寻优，只做「同号 + 样本充足」的稳健性检查；
  · TRAIN 只用于确认方向，VALID / OOS 只做检验。
"""
import os
import warnings
import numpy as np
import pandas as pd

import _tr_lib as L

warnings.filterwarnings('ignore', category=RuntimeWarning)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'out')
DATE = '20260925'
HOR = L.HOR
COST = L.COST_DEF
KMAX = L.KMAX

print('载入窗口矩阵 ...')
m = L.load_mats(os.path.join(OUT, 'tr_mats.npz'))
base = pd.read_parquet(os.path.join(OUT, 'tr_base.parquet'))
n = len(base)
yy = base['yr'].values
regime = base['regime'].astype(str).values
clean = base['clean'].values.astype(bool)
mass = (base['listed_days'].values >= 60) & clean
PERIOD = {'TRAIN': yy <= 2023, 'VALID': (yy >= 2024) & (yy <= 2025), 'OOS': yy >= 2026}
ALLP = ['ALL', 'TRAIN', 'VALID', 'OOS']
print('合格样本', int(mass.sum()))

dd = m['dd']; cp = m['cp']; vr = m['vr']
cl = m['cl']; amt = m['amt']; isl = m.get('isl')
ar = np.arange(n)

fd2 = L.first_disagree(dd, 0.02)
fd3 = L.first_disagree(dd, 0.03)
fdL2 = L.first_disagree(m['ddl'], 0.02)


def at(mat, off):
    return L.value_at(mat, off)


def sub_ok(mat, off, lo):
    """off>=1 且 mat[off] >= lo"""
    v = L.value_at(mat, off)
    return (off > 0) & np.isfinite(v) & (v >= lo)


# ── 候选（全部为「事件日收盘进场」）──────────────────────────────
CAND = []
CAND.append(('E_Q1_首次回撤2%_收盘位置>=0.6', fd2, sub_ok(cp, fd2, 0.6)))
CAND.append(('E_Q2_首次回撤3%_收盘位置>=0.6', fd3, sub_ok(cp, fd3, 0.6)))
CAND.append(('E_Q3_首次回撤2%_收盘位置<0.6', fd2, sub_ok(cp, fd2, -9) & ~sub_ok(cp, fd2, 0.6)))
CAND.append(('E_Q4_首次回撤2%_全部', fd2, fd2 > 0))
CAND.append(('E_Q5_首次盘中回撤2%_全部', fdL2, fdL2 > 0))
# 量能候选：首板后第 k 日单日量比（仅用当日及之前）
for lab, thr, ge in (('E_V1_回撤2%后2日_量比>=1.2', 1.2, True),
                     ('E_V2_回撤2%后2日_量比<=0.8', 0.8, False)):
    k = fd2.astype(np.int64) + 2
    ok = (fd2 > 0) & (k <= 5)
    kk = np.clip(k, 0, KMAX)
    v = vr[ar, kk]
    ok = ok & np.isfinite(v) & ((v >= thr) if ge else (v <= thr))
    CAND.append((lab, np.where(ok, kk, -1).astype(np.int8), ok))

# 量比定义（K 线自身量比 vr = vo/vma20），仅用于报告说明

# ── 量能类候选：锚点 = 指标可见日（fd+2 收盘）──────────────────────
FD = fdL2                      # 与 _tr_core 主分歧口径一致
kd = 2
dec = L.decay_metrics(m, FD, kd)
anc_v = np.where(FD > 0, FD, 0) + kd          # 指标窗口结束日 = 可见日
vis = (FD > 0) & (anc_v <= KMAX)
V2m = dec['V2_vov0']; V4m = dec['V4_run']; V6m = dec['V6_dvol_uvol']
for lab, arr, lo, hi in (
        ('E_M1_量/首板量>=1.3', V2m, 1.3, None),
        ('E_M2_量/首板量<=0.5', V2m, None, 0.5),
        ('E_M3_连续缩量<=1日', V4m, None, 1.0),
        ('E_M4_连续缩量>=3日', V4m, 3.0, None),
        ('E_M5_跌量/涨量<=0.5', V6m, None, 0.5),
        ('E_M6_跌量/涨量>=1.5', V6m, 1.5, None)):
    ok = vis & np.isfinite(arr)
    ok &= (arr >= lo) if lo is not None else ok
    ok &= (arr <= hi) if hi is not None else ok
    CAND.append((lab, np.where(ok, anc_v, -1).astype(np.int8), ok))

# ── 基准：按锚点 offset 分层 ────────────────────────────────
BENCH = {}
for pn in ALLP:
    pm = mass & (PERIOD[pn] if pn in PERIOD else np.ones(n, dtype=bool))
    BENCH[pn] = {N: L.offset_bench(m, N, COST, mask=pm, min_bucket=20) for N in HOR}

# 可交易过滤：入场日非涨停 且 成交额 >= 全样本 P10
def tradable(off):
    o = np.clip(off, 0, KMAX)
    a = amt[ar, o]
    v = amt[mass]
    v = v[np.isfinite(v)]
    thr = np.percentile(v, 10) if v.size else np.nan
    notlu = np.ones(n, dtype=bool) if isl is None else (isl[ar, o] == 0)
    return (off > 0) & np.isfinite(a) & (a >= thr) & notlu


def perf(off, mk, pn, cost=COST):
    pm = mass & (PERIOD[pn] if pn in PERIOD else np.ones(n, dtype=bool))
    sel = mk & pm
    out = {'N': int(sel.sum())}
    for N in HOR:
        v = L.fwd_ret(m, off, N, cost)
        st = L.stats(v[sel])
        ex, _, _, t, _ = L.paired_excess(m, sel, off, N, cost, bench=BENCH[pn][N])
        out['t%d_med' % N] = round(st['med'] * 100, 3) if np.isfinite(st['med']) else np.nan
        out['t%d_win' % N] = round(st['win'] * 100, 1) if np.isfinite(st['win']) else np.nan
        out['t%d_pf' % N] = round(st['pf'], 3) if np.isfinite(st['pf']) else np.nan
        out['t%d_ex' % N] = round(ex * 100, 3) if np.isfinite(ex) else np.nan
    return out


ROWS = []
print('\n=========== 可交易(事件链)口径 ===========')
hdr = '%-34s %-6s %6s %8s %8s %7s %8s %8s'
print(hdr % ('候选', '期', 'N', 'T3_med', 'T5_med', 'T5_win', 'T5_pf', 'T5_ex'))
for lab, off, mk in CAND:
    for pn in ALLP:
        d = perf(off, mk, pn)
        r = {'section': 'EDGE_事件链', 'group': lab, 'period': pn, 'cost': '0.255%'}
        r.update(d)
        ROWS.append(r)
        if pn in ('ALL',):
            print(hdr % (lab[:34], pn, d['N'], d['t3_med'], d['t5_med'], d['t5_win'], d['t5_pf'], d['t5_ex']))

# ── 分期明细 ─────────────────────────────────────────────
print('\n--- 分期明细（仅 CAND 前 3 个 + 量能候选）---')
for lab, off, mk in CAND:
    line = []
    for pn in ('TRAIN', 'VALID', 'OOS'):
        d = perf(off, mk, pn)
        line.append('%s N=%d T5=%.3f ex=%.3f' % (pn, d['N'], d['t5_med'], d['t5_ex']))
    print('  %-34s %s' % (lab[:34], ' | '.join(line)))

# ── 可交易性 ─────────────────────────────────────────────
print('\n--- 可交易性（入场日非涨停 + 成交额>=P10）---')
for lab, off, mk in CAND:
    tk = mk & tradable(off)
    d = perf(off, tk, 'ALL')
    print('  %-34s N_all=%5d N_tradable=%5d T5=%7.3f ex=%7.3f win=%.1f pf=%.2f'
          % (lab[:34], int((mk & mass).sum()), d['N'], d['t5_med'], d['t5_ex'], d['t5_win'], d['t5_pf']))
    ROWS.append({'section': 'EDGE_可交易性', 'group': lab, 'period': 'ALL', 'cost': '0.255%', **d})

# ── 滑点敏感性（对 2 个主要候选）──────────────────────────
print('\n--- 滑点敏感性 ---')
for lab, off, mk in (CAND[0], CAND[1], CAND[7]):
    for slip in L.SLIP_ALL:
        c = L.cost_of(slip)
        cells = []
        for pn in ('TRAIN', 'VALID', 'OOS'):
            d = perf(off, mk, pn, cost=c)
            cells.append('%s T5=%.3f ex=%.3f' % (pn, d['t5_med'], d['t5_ex']))
        print('  %-34s slip=%.1f%%  %s' % (lab[:34], slip * 100, ' | '.join(cells)))
        ROWS.append({'section': 'EDGE_滑点', 'group': lab, 'period': 'slip=%.1f%%' % (slip * 100),
                     'cost': '双边%.3f%%' % (c * 100),
                     **perf(off, mk, 'OOS', cost=c)})

# ── 首发日分布 + 逐年（主要候选）──────────────────────────
lab, off, mk = CAND[0]
print('\n--- 首次回撤发生在第几日（%s，ALL）---' % lab)
for k in range(1, 6):
    sk = mk & (off == k) & mass
    if sk.sum() == 0:
        continue
    d = perf(off, sk, 'ALL')
    print('  fd=T+%d  N=%5d  T5_med=%7.3f  T5_ex=%7.3f  T5_win=%5.1f  pf=%.2f'
          % (k, d['N'], d['t5_med'], d['t5_ex'], d['t5_win'], d['t5_pf']))

print('\n--- 逐年（%s）---' % lab)
for y in sorted(set(yy[mass])):
    sk = mk & (yy == y) & mass
    if sk.sum() < 30:
        continue
    v = L.fwd_ret(m, off, 5, COST)
    st = L.stats(v[sk])
    ex, _, _, _, _ = L.paired_excess(m, sk, off, 5, COST, bench=BENCH['ALL'][5])
    print('  %d  N=%5d  T5_med=%7.3f  T5_ex=%7.3f  win=%5.1f  pf=%.2f'
          % (y, st['n'], st['med'] * 100, ex * 100, st['win'] * 100, st['pf']))
    ROWS.append({'section': 'EDGE_逐年', 'group': lab, 'period': str(y), 'cost': '0.255%',
                 'N': st['n'], 't5_med': round(st['med'] * 100, 3),
                 't5_win': round(st['win'] * 100, 1), 't5_pf': round(st['pf'], 3),
                 't5_ex': round(ex * 100, 3)})

# ── Regime（主要候选）────────────────────────────────────
print('\n--- Regime（%s，ALL）---' % lab)
for rg in sorted(set(regime[mass])):
    sk = mk & (regime == rg) & mass
    if sk.sum() < 30:
        continue
    d = perf(off, sk, 'ALL')
    print('  %-8s N=%5d  T5_med=%7.3f  T5_ex=%7.3f  win=%5.1f  pf=%.2f'
          % (rg, d['N'], d['t5_med'], d['t5_ex'], d['t5_win'], d['t5_pf']))
    ROWS.append({'section': 'EDGE_Regime', 'group': lab, 'period': rg, 'cost': '0.255%', **d})

# ── 与 B 组（全部首板/第一次分歧）对照 ─────────────────────
print('\n--- 对照：所有首板（T0 锚点）vs 首次回撤日锚点 ---')
for pn in ALLP:
    pm = mass & (PERIOD[pn] if pn in PERIOD else np.ones(n, dtype=bool))
    o0 = np.zeros(n, dtype=np.int64)
    d0 = perf(o0, np.ones(n, dtype=bool), pn)
    d1 = perf(fd2, fd2 > 0, pn)
    print('  %-6s A组(T0进场) N=%5d T5=%7.3f | 回撤日进场 N=%5d T5=%7.3f'
          % (pn, d0['N'], d0['t5_med'], d1['N'], d1['t5_med']))
    ROWS.append({'section': 'EDGE_对照', 'group': 'A_全部首板@T0', 'period': pn, 'cost': '0.255%', **d0})
    ROWS.append({'section': 'EDGE_对照', 'group': 'B_首次回撤日@fd', 'period': pn, 'cost': '0.255%', **d1})

ed = pd.DataFrame(ROWS)
p = os.path.join(HERE, 'three_stage_rebound_edge_%s.csv' % DATE)
ed.to_csv(p, index=False, encoding='utf-8-sig')
print('\n已写', p, ed.shape)
