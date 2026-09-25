# -*- coding: utf-8 -*-
"""首板 → 第一次分歧 → 缩量止跌 → 再启动 · 核心实验层

层次（严格按任务书 §27 的 A→B→C→D→E→F 逐层归因）
  L1 分歧定义候选空间      §10  D1 价格 / D2 收盘强弱 / D3 量价 / D4 高低点
  L2 卖压衰减候选 V1–V6    §12  §13 缩量阈值与天数扫描
  L3 结构未破 S1–S8        §14
  L4 再启动 R1–R7          §15
  L5 再启动量能 VR 档      §16
  L6 Entry E1–E6           §18

两种归因口径（互相补充）
  (i)  条件化(锚点T0)：各组都从 T0 收盘进入 → 剔除择时效应，纯测「条件本身的信息含量」
  (ii) 事件链(各自锚点)：各组从自身触发日收盘进入 → 可交易口径

参数选择纪律（§31）
  不做「最优参数」，只做「稳健中心」：在 TRAIN 上找 连续、同号、样本 N>=200 的最长区间，
  取区间中点作为代表值；若不存在长度>=2 的同号连续区间 → 判定「非稳健」，如实标注。
  VALID / OOS 只做检验，不参与任何选择。
"""
import os
import re
import json
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
MIN_N_CORE = 200      # §30 核心结论最小样本
MIN_N_DESC = 50       # §30 仅描述

print('载入窗口矩阵 ...')
m = L.load_mats(os.path.join(OUT, 'tr_mats.npz'))
base = pd.read_parquet(os.path.join(OUT, 'tr_base.parquet'))
n = len(base)
yy = base['yr'].values
regime = base['regime'].values
clean = base['clean'].values.astype(bool)
mass = (base['listed_days'].values >= 60) & clean
PERIOD = {'TRAIN': yy <= 2023, 'VALID': (yy >= 2024) & (yy <= 2025), 'OOS': yy >= 2026}
ALLP = ['ALL', 'TRAIN', 'VALID', 'OOS']
print('样本', n, '| 合格样本', int(mass.sum()), '| 分期', {k: int(v.sum()) for k, v in PERIOD.items()})

BENCH = {}
for pn in ALLP:
    pm = PERIOD[pn] if pn in PERIOD else np.ones(n, dtype=bool)
    BENCH[pn] = {N: L.offset_bench(m, N, COST, mask=mass & pm, min_bucket=20) for N in HOR}

ROWS = []
SELECT = []


def welch(a, b):
    a = a[np.isfinite(a)]; b = b[np.isfinite(b)]
    if a.size < 5 or b.size < 5:
        return np.nan
    se = np.sqrt(a.var(ddof=1) / a.size + b.var(ddof=1) / b.size)
    return float((a.mean() - b.mean()) / se) if se > 0 else np.nan


def ev(mask, anc, period='TRAIN', cost=COST):
    """组指标（含分期基准的同 offset 超额）"""
    pm = mass & (PERIOD[period] if period in PERIOD else np.ones(n, dtype=bool))
    mk = mask & pm
    out = {'n': int(mk.sum())}
    for N in HOR:
        v = L.fwd_ret(m, anc, N, cost)
        st = L.stats(v[mk])
        ex, den, posb, t, nb = L.paired_excess(m, mk, anc, N, cost, bench=BENCH[period][N])
        out['m%d' % N] = st['med']; out['a%d' % N] = st['mean']
        out['w%d' % N] = st['win']; out['f%d' % N] = st['pf']
        out['e%d' % N] = ex; out['t%d' % N] = t; out['b%d' % N] = nb
        out['c%d' % N] = welch(v[mk], v[pm & ~mk])
    return out


def add_row(section, defname, param, mask, anc, mode='条件化', note='', layer=''):
    for pn in ALLP:
        pm = mass & (PERIOD[pn] if pn in PERIOD else np.ones(n, dtype=bool))
        mk = mask & pm
        if mk.sum() == 0:
            continue
        d = ev(mask, anc, pn)
        r = {'section': section, 'layer': layer, 'mode': mode, 'def': defname,
             'param': str(param), 'period': pn, 'note': note}
        r['N'] = d['n']
        for N in HOR:
            r['t%d_med' % N] = round(d['m%d' % N] * 100, 3) if np.isfinite(d['m%d' % N]) else np.nan
            r['t%d_mean' % N] = round(d['a%d' % N] * 100, 3) if np.isfinite(d['a%d' % N]) else np.nan
            r['t%d_win' % N] = round(d['w%d' % N] * 100, 1) if np.isfinite(d['w%d' % N]) else np.nan
            r['t%d_pf' % N] = round(d['f%d' % N], 3) if np.isfinite(d['f%d' % N]) else np.nan
            r['t%d_ex' % N] = round(d['e%d' % N] * 100, 3) if np.isfinite(d['e%d' % N]) else np.nan
            r['t%d_exT' % N] = round(d['t%d' % N], 2) if np.isfinite(d['t%d' % N]) else np.nan
            r['t%d_cT' % N] = round(d['c%d' % N], 2) if np.isfinite(d['c%d' % N]) else np.nan
        ROWS.append(r)


def runs(vals, ns, min_n=MIN_N_CORE):
    """最长同号连续区间（N>=min_n），返回值域下标区间"""
    bi = bj = -1
    i = 0
    while i < len(vals):
        if not np.isfinite(vals[i]) or ns[i] < min_n:
            i += 1
            continue
        j = i
        while (j + 1 < len(vals) and np.isfinite(vals[j + 1]) and ns[j + 1] >= min_n
               and np.sign(vals[j + 1]) == np.sign(vals[i])):
            j += 1
        if (j - i) > (bj - bi):
            bi, bj = i, j
        i = j + 1
    return bi, bj


def pick(section, cands, field=5, min_n=MIN_N_CORE, mode='条件化'):
    """cands: [(label, mask, anc)]；返回 (label, mask, anc, info)；稳健区落在 TRAIN 上判定"""
    vals, ns = [], []
    for lb, mk, anc in cands:
        d = ev(mk, anc, 'TRAIN')
        vals.append(d.get('e%d' % field, np.nan))
        ns.append(d['n'])
    vals, ns = np.array(vals, dtype=float), np.array(ns)
    bi, bj = runs(vals, ns, min_n)
    if bi < 0:
        info = {'robust': False, 'region': '', 'reason': '无 N>=%d 的连续同号区间' % min_n}
        lb, mk, anc = cands[0][0], cands[0][1], cands[0][2]
    else:
        mid = (bi + bj) // 2
        info = {'robust': (bj - bi) >= 1, 'region': '%s ~ %s' % (cands[bi][0], cands[bj][0]),
                'center': cands[mid][0], 'run_len': int(bj - bi + 1),
                'vals': [round(float(v) * 100, 3) for v in vals[bi:bj + 1]],
                'ns': [int(x) for x in ns[bi:bj + 1]]}
        lb, mk, anc = cands[mid][0], cands[mid][1], cands[mid][2]
    SELECT.append({'section': section, 'field': 'T+%d 超额' % field, 'picked': lb,
                   'robust': info['robust'], 'detail': info})
    print('  [%s] 选中 %s | 稳健区 %s | N=%d | 训练 T+%d 超额 %s' %
          (section, lb, info.get('region', '-'), int(ev(mk, anc, 'TRAIN')['n']), field,
           info.get('vals', '-')))
    return lb, mk, anc, info


# ══════════════════════ L1 分歧定义候选空间 §10 ══════════════════════
print('\n[L1] 分歧定义候选空间')
fdC = {th: L.first_disagree(m['dd'], th, kmax=5) for th in L.TH_ALL}
fdL = {th: L.first_disagree(m['ddl'], th, kmax=5) for th in L.TH_ALL}
A0 = np.zeros(n, dtype=np.int64)
cp, vr, hi, lo, cl = m['cp'], m['vr'], m['hi'], m['lo'], m['cl']
C0 = m['C0'].astype(np.float64); H0 = m['H0'].astype(np.float64); PC0 = m['PC0'].astype(np.float64)

cands = []
FDMAP = {}          # 候选定义 → 其"分歧日"偏移数组
for th in L.TH_ALL:
    lb = 'D1a_收盘回撤<=-%.0f%%' % (th * 100)
    cands.append((lb, fdC[th] > 0, A0)); FDMAP[lb] = fdC[th]
for th in L.TH_ALL:
    lb = 'D1b_盘中回撤<=-%.0f%%' % (th * 100)
    cands.append((lb, fdL[th] > 0, A0)); FDMAP[lb] = fdL[th]
for q in (0.2, 0.4, 0.6, 0.8):
    f = np.full(n, -1, dtype=np.int8)
    for k in range(1, 6):
        hit = (f < 0) & np.isfinite(cp[:, k]) & (cp[:, k] < q)
        f[hit] = k
    lb = 'D2_收盘位置<%.1f' % q
    cands.append((lb, f > 0, A0)); FDMAP[lb] = f
for q in (0.6, 0.8):
    for th in (0.02, 0.03):
        f = fdC[th]
        ok = np.zeros(n, dtype=bool)
        for k in range(1, 6):
            s = np.where(f == k)[0]
            ok[s] = np.isfinite(cp[s, k]) & (cp[s, k] >= q)
        lb = 'D2b_回撤%.0f%%且收盘位置>=%.1f' % (th * 100, q)
        cands.append((lb, ok & (f > 0), A0)); FDMAP[lb] = f
for th in (0.02, 0.03):
    for vmax in (1.0, 1.2):
        f = fdC[th]
        ok = np.zeros(n, dtype=bool)
        for k in range(1, 6):
            s = np.where(f == k)[0]
            ok[s] = np.isfinite(vr[s, k]) & (vr[s, k] <= vmax)
        lb = 'D3_回撤%.0f%%且VR<=%.1f' % (th * 100, vmax)
        cands.append((lb, ok & (f > 0), A0)); FDMAP[lb] = f
lo_min = L.wmin(lo, 1, 5)
for cut in (-0.03, -0.05, -0.08):
    cands.append(('D4a_低点/首板收盘<=%.0f%%' % (cut * 100), lo_min / C0 - 1 <= cut, A0))
    cands.append(('D4b_低点/首板前收<=%.0f%%' % (cut * 100), lo_min / PC0 - 1 <= cut, A0))
for lb, mk, anc in cands:
    add_row('L1_分歧定义候选', lb, lb, mk, anc, layer='B', note='')
L1_lb, L1_mk, L1_anc, L1_info = pick('L1_分歧定义候选', cands, field=5)

# ══════════════════════ L2 卖压衰减 §12 §13 ══════════════════════
print('\n[L2] 卖压衰减 V1–V6 + 缩量参数')
FD = FDMAP.get(L1_lb)
if FD is None:      # D4 类候选不含"分歧日"概念 → 主口径回退至 3% 收盘回撤（如实记录）
    FD = fdC[0.03]
    SELECT.append({'section': 'L2_主分歧口径', 'field': '-', 'picked': 'D1a_收盘回撤<=-3%',
                   'robust': False,
                   'detail': {'reason': 'L1 选中 %s（无分歧日概念），主口径回退至 3%% 收盘回撤' % L1_lb}})
    print('  [L2] L1 选中 %s（无分歧日），主口径回退至 D1a 收盘回撤<=-3%%' % L1_lb)
else:
    SELECT.append({'section': 'L2_主分歧口径', 'field': '-', 'picked': L1_lb, 'robust': True,
                   'detail': {'reason': '主口径 fd 直接取自 L1 选中定义'}})
    print('  [L2] 主分歧口径 = %s' % L1_lb)
B_mk = FD > 0
add_row('L2_基准', 'B_有第一次分歧', 'B', B_mk, A0, layer='B')

dec = {}
for kd in (2, 3, 4, 5):
    dec[kd] = L.decay_metrics(m, FD, kd)
    dd_at = L.value_at(m['dd'], FD)
    fdk = np.where(FD > 0, FD, 0) + kd
    for key in ('V1_vr20', 'V2_vov0', 'V3_vovfd', 'V4_run', 'V6_dvol_uvol'):
        v = dec[kd][key]
        bins = ([0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2] if key.startswith('V1')
                else [0.5, 0.7, 0.9, 1.1, 1.3, 1.6] if key.startswith('V2')
                else [0.5, 0.7, 0.9, 1.1, 1.3, 1.6] if key.startswith('V3')
                else [0, 1, 2, 3, 4] if key.startswith('V4')
                else [0.5, 0.8, 1.1, 1.5, 2.0])
        for i, b in enumerate(bins):
            lo_b = -np.inf if i == 0 else bins[i - 1]
            hi_b = np.inf if i == len(bins) - 1 else b
            mk = B_mk & np.isfinite(v) & (v >= lo_b) & (v < hi_b)
            add_row('L2_卖压衰减曲线', '%s_kd%d' % (key, kd), '%s~%s' % (lo_b, hi_b), mk, A0,
                    note='窗口均值口径')
        # 累积阈值（§13）只看 V1
    v1 = dec[kd]['V1_vr20']
    for thr in (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2):
        mk = B_mk & np.isfinite(v1) & (v1 <= thr)
        add_row('L2_缩量阈值', 'C_V1<=%.1f_kd%d' % (thr, kd), 'V1<=%.1f' % thr, mk, A0, layer='C')

# 稳健选择：阈值（在 kd 固定前先各自选）
thr_cands = []
for kd in (2, 3, 4, 5):
    v1 = dec[kd]['V1_vr20']
    for thr in (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2):
        thr_cands.append(('V1<=%.1f(kd=%d)' % (thr, kd), B_mk & np.isfinite(v1) & (v1 <= thr), A0))
C_lb, C_mk, C_anc, C_info = pick('L2_缩量阈值', thr_cands, field=5)
mm = re.search(r'V1<=(?P<thr>[\d.]+)\(kd=(?P<kd>\d+)\)', C_lb)
V_THR, KD = float(mm.group('thr')), int(mm.group('kd'))
print('  缩量主口径：V1(窗口均量/MA20量) <= %.1f，窗口 %d 日' % (V_THR, KD))
v1 = dec[KD]['V1_vr20']
# 连续缩量天数（V4）扫描
for run_n in (1, 2, 3, 4, 5):
    mk = B_mk & (dec[KD]['V4_run'] >= run_n)
    add_row('L2_缩量天数', 'V4_run>=%d' % run_n, 'run>=%d' % run_n, mk, A0, layer='C')

# ══════════════════════ L3 结构未破 §14 ══════════════════════
print('\n[L3] 结构未破 S1–S8')
SF = L.struct_flags(m, FD, KD)
s_keys = ['S1_low_gt_L0', 'S2_low_gt_bodymid', 'S3_low_gt_C0', 'S4_low_gt_pc0',
          'S5_cl_gt_ma5', 'S6_cl_gt_ma10', 'S7_cl_gt_ma20',
          'S8_dd5', 'S8_dd8', 'S8_dd10', 'S8_dd12', 'S8_dd15']
s_cands = []
for k in s_keys:
    mk = C_mk & SF[k]
    add_row('L3_结构未破', k, k, mk, A0, layer='D')
    s_cands.append((k, mk, A0))
S_lb, S_mk, S_anc, S_info = pick('L3_结构未破', s_cands, field=5)
add_row('L3_结构未破无约束', 'C_无结构约束', 'none', C_mk, A0, layer='C')

# ══════════════════════ L4 再启动 §15 ══════════════════════
print('\n[L4] 再启动 R1–R7')
KMIN = np.where(FD > 0, FD, -1) + KD + 1      # 再启动必须在缩量窗口之后
RD = {}
r_cands = []
for rule in ('R1', 'R2', 'R3', 'R4', 'R5', 'R6', 'R7'):
    rd = L.restart_day(m, FD, rule, vt=1.2, kmin=KMIN)
    RD[rule] = rd
    mk = C_mk & (rd > 0)
    add_row('L4_再启动', rule, rule, mk, A0, layer='E', note='再启动日收盘进入（条件化口径另见事件链）')
    r_cands.append((rule, mk, A0))
R_lb, R_mk, R_anc, R_info = pick('L4_再启动', r_cands, field=5)
RD_main = RD[R_lb]
E_anc = np.where(RD_main > 0, RD_main, 0).astype(np.int64)
E_mk = C_mk & (RD_main > 0)
print('  再启动主口径 %s，触发 %d 例' % (R_lb, int(R_mk.sum())))

# ══════════════════════ L5 再启动量能 §16 ══════════════════════
print('\n[L5] 再启动量能确认')
ARR = np.arange(n)


def apply_vt(rd, vt):
    """在再启动信号上追加「当日 VR>=vt」确认；vt<=0 表示不追加确认"""
    if vt <= 0:
        return rd
    vrs = m['vr'][ARR, np.clip(np.where(rd > 0, rd, 0), 0, L.KMAX)]
    return np.where((rd > 0) & np.isfinite(vrs) & (vrs >= vt), rd, -1).astype(np.int8)


vt_cands = []
for vt in (0.0,) + tuple(L.VT_ALL):
    rd = apply_vt(RD_main, vt)
    mk = C_mk & (rd > 0)
    add_row('L5_再启动量能', '%s_vt%.1f' % (R_lb, vt), 'VR>=%.1f' % vt, mk, A0, layer='F',
            note='0.0 = 不追加量能确认')
    vt_cands.append(('%s_vt%.1f' % (R_lb, vt), mk, A0))
VT_lb, VT_mk, VT_anc, VT_info = pick('L5_再启动量能', vt_cands, field=5)
VT = float(VT_lb.split('vt')[1])
RD_conf = apply_vt(RD_main, VT)
print('  量能确认主口径 VR >= %.1f' % VT)

# ══════════════════════ L6 Entry §18 ══════════════════════
print('\n[L6] Entry 候选 E1–E6')
e_cands = []
for rule in ('E1', 'E2', 'E3', 'E4', 'E5', 'E6'):
    if rule == 'E1':                       # 分歧后第一天收盘
        ed = L.entry_day(m, FD, 'E1')
    elif rule in ('E2', 'E3', 'E6'):       # 不含量能确认
        ed = L.entry_day(m, FD, rule, kmin=KMIN)
    else:                                  # E4 = 突破 + VR确认；E5 = E4 + MA20 未破
        bv = apply_vt(L.restart_day(m, FD, 'R2', kmin=KMIN), VT)
        if rule == 'E4':
            ed = bv
        else:
            clv = m['cl'][ARR, np.clip(np.where(bv > 0, bv, 0), 0, L.KMAX)]
            ma20v = m['ama20'][ARR, np.clip(np.where(bv > 0, bv, 0), 0, L.KMAX)]
            ed = np.where((bv > 0) & np.isfinite(clv) & np.isfinite(ma20v) & (clv >= ma20v),
                          bv, -1).astype(np.int8)
    mk = C_mk & (ed > 0)
    add_row('L6_Entry', rule, rule, mk, A0, layer='F')
    e_cands.append((rule, mk, A0))
EN_lb, EN_mk, EN_anc, EN_info = pick('L6_Entry', e_cands, field=5)
EN_anc = np.where(EN_mk, EN_anc, 0).astype(np.int64)
print('  Entry 主口径 %s，触发 %d 例' % (EN_lb, int(EN_mk.sum())))

# ══════════════════════ A–G 逐层归因 §19 §20 §27 ══════════════════════
print('\n[ATTR] A–G 逐层归因')
v1m = dec[KD]['V1_vr20']
GRP = [('A_全部首板', mass),
       ('B_第一次分歧', B_mk),
       ('C_分歧+缩量', C_mk),
       ('D_分歧+缩量+结构未破', S_mk),
       ('E_分歧+缩量+再启动', E_mk),
       ('F_分歧+缩量+结构未破+再启动', S_mk & (RD_main > 0)),
       ('G_F+再启动量能确认', S_mk & (RD_conf > 0))]
RD_D = np.where(S_mk & (RD_main > 0), RD_main, 0).astype(np.int64)
RD_G = np.where(S_mk & (RD_conf > 0), RD_conf, 0).astype(np.int64)
ANC_CHAIN = {'A_全部首板': A0, 'B_第一次分歧': np.where(FD > 0, FD, 0).astype(np.int64),
             'C_分歧+缩量': (np.where(FD > 0, FD, -1) + KD).clip(0).astype(np.int64),
             'D_分歧+缩量+结构未破': (np.where(FD > 0, FD, -1) + KD).clip(0).astype(np.int64),
             'E_分歧+缩量+再启动': np.where(RD_main > 0, RD_main, 0).astype(np.int64),
             'F_分歧+缩量+结构未破+再启动': RD_D,
             'G_F+再启动量能确认': RD_G}
for lb, mk in GRP:
    add_row('ATTR_条件化', lb, lb, mk, A0, mode='条件化(锚点T0)')
    add_row('ATTR_事件链', lb, lb, mk, ANC_CHAIN[lb], mode='事件链(各自锚点)')

# 稳健性网格：完整结构 G 在候选参数上的表现（§31）
print('\n[ROBUST] 完整结构参数网格')
grid = []
FAM = fdL if L1_lb.startswith('D1b') else fdC
for th in L.TH_ALL:
    fdg = FAM[th]
    for kd in (2, 3, 4, 5):
        v1g = L.decay_metrics(m, fdg, kd)['V1_vr20']
        Cg = (fdg > 0) & np.isfinite(v1g) & (v1g <= V_THR)
        Sg = Cg & SF[S_lb]
        for rule in ('R2', 'R6'):
            for vt in (1.2, 1.5):
                rdg = L.restart_day(m, fdg, rule, vt=vt, kmin=(np.where(fdg > 0, fdg, -1) + kd + 1))
                mg = Sg & (rdg > 0)
                add_row('ROBUST_完整结构网格', 'th%.0f_kd%d_%s_vt%.1f' % (th * 100, kd, rule, vt),
                        'th=%.0f%%|kd=%d|%s|vt=%.1f' % (th * 100, kd, rule, vt), mg,
                        np.where(rdg > 0, rdg, 0).astype(np.int64), mode='事件链(各自锚点)')
                grid.append(('th%.0f_kd%d_%s_vt%.1f' % (th * 100, kd, rule, vt), mg,
                             np.where(rdg > 0, rdg, 0).astype(np.int64)))

# ══════════════════════ 维度分层 §23 §24 §25 §26 ══════════════════════
print('\n[LAYER] Regime / 首板质量 / 板块 / 市值 / 换手 / 行业')
for dim, col in (('Regime', 'regime'), ('首板质量', 't0_quality'), ('板块', 'board'),
                 ('市值档', 'mv_grp'), ('换手档', 'to_grp')):
    vals = [v for v in pd.Series(base[col].values).dropna().unique()]
    for lv in sorted(map(str, vals)):
        sub = (base[col].astype(str).values == lv)
        for gname, gmk, ganc in (('B_分歧', B_mk, A0), ('C_分歧+缩量', C_mk, A0),
                                 ('G_完整结构', GRP[6][1], RD_G)):
            add_row('LAYER_%s' % dim, '%s|%s' % (gname, lv), lv, gmk & sub, ganc,
                    note='样本分层的组内表现')
ind = base['industry'].astype(str).values
for lv, cnt in pd.Series(ind).value_counts().items():
    if cnt < 100:
        continue
    sub = (ind == lv)
    add_row('LAYER_行业', 'G_完整结构|%s' % lv, lv, GRP[6][1] & sub, RD_G,
            note='行业样本 %d；N<100 的行业未列出' % cnt)

# ══════════════════════ 交互项 §22 ══════════════════════
print('\n[INTER] 交互项')
dd_fd = L.value_at(m['dd'], FD)
T0VR = base['t0_vr20'].values
TURN = base['turnover_t0'].values
MV = base['total_mv_t0'].values
Q0 = base['t0_quality'].astype(str).values


def inter(name, v1, edges1, v2, edges2, base_mk, anc):
    for i in range(len(edges1) - 1):
        for j in range(len(edges2) - 1):
            mk = (base_mk & np.isfinite(v1) & (v1 >= edges1[i]) & (v1 < edges1[i + 1])
                  & np.isfinite(v2) & (v2 >= edges2[j]) & (v2 < edges2[j + 1]))
            add_row('INTER_%s' % name, '%s[%s]x%s[%s]' %
                    (name, edges1[i], name, edges2[j]),
                    '%.2f~%.2f_x_%.2f~%.2f' % (edges1[i], edges1[i + 1], edges2[j], edges2[j + 1]),
                    mk, anc, note='交互格子')


inter('分歧深度x缩量', np.abs(dd_fd), [0, 0.03, 0.06, np.inf], v1m, [0, 0.8, 1.0, np.inf], B_mk, A0)
inter('分歧深度x换手', np.abs(dd_fd), [0, 0.03, 0.06, np.inf], TURN, [-np.inf, 8, 20, np.inf], B_mk, A0)
inter('首板VRx分歧VR', T0VR, [0, 1.5, 3.0, np.inf], v1m, [0, 0.8, 1.0, np.inf], B_mk, A0)
inter('市值x缩量', MV, [0, 5e5, 2e6, np.inf], v1m, [0, 0.8, 1.0, np.inf], B_mk, A0)
inter('首板质量x分歧深度', (Q0 == 'Strong').astype(float) * 2 + (Q0 == 'Normal').astype(float) * 1,
      [-0.5, 0.5, 1.5, 2.5], np.abs(dd_fd), [0, 0.03, 0.06, np.inf], B_mk, A0)

# ══════════════════════ 交易成本 §33 ══════════════════════
print('\n[COST] 交易成本与可交易性')
F_mk = GRP[5][1]; G_mk = GRP[6][1]
for slip in L.SLIP_ALL:
    c = L.cost_of(slip)
    for gname, gmk, ganc in (('F_分歧+缩量+结构未破+再启动', F_mk, RD_D),
                             ('G_F+量能确认', G_mk, RD_G)):
        for pn in ALLP:
            d = ev(gmk, ganc, pn, cost=c)
            ROWS.append({'section': 'COST_滑点', 'mode': '事件链(各自锚点)', 'def': gname,
                         'param': 'slip=%.1f%%' % (slip * 100), 'period': pn, 'N': d['n'],
                         'note': '双边成本 %.3f%%' % (c * 100),
                         **{'t%d_%s' % (N, k): (round(d['%s%d' % (v, N)] * 100, 3)
                                               if k in ('med', 'mean', 'ex') and np.isfinite(d['%s%d' % (v, N)])
                                               else round(d['%s%d' % (v, N)], 3) if np.isfinite(d['%s%d' % (v, N)]) else np.nan)
                            for N in HOR for v, k in (('m', 'med'), ('a', 'mean'), ('w', 'win'),
                                                      ('f', 'pf'), ('e', 'ex'), ('t', 'exT'))}})

# 可交易性：入场日涨停无法成交 / 成交额过低
amt = m['amt']; isl = m['isl']
ed = np.where(EN_mk, EN_anc, 0)
e_amt = amt[np.arange(n), np.clip(ed, 0, L.KMAX)]
e_isl = isl[np.arange(n), np.clip(ed, 0, L.KMAX)]
q10 = np.nanpercentile(e_amt[EN_mk], 10)
tradable = EN_mk & np.isfinite(e_isl) & (e_isl < 0.5) & np.isfinite(e_amt) & (e_amt >= q10)
for gname, gmk, ganc in (('Entry主口径_%s' % EN_lb, EN_mk, EN_anc),
                         ('G_F+量能确认', G_mk, RD_G)):
    add_row('TRADE_可交易性', gname + '|原始', 'all', gmk, ganc)
if EN_mk.sum() > 0:
    add_row('TRADE_可交易性', 'Entry主口径_净可交易(入场日未涨停且成交额>=P10)', 'filtered',
            tradable, EN_anc, note='P10 成交额阈值 %.0f 万元' % (q10 / 1e4))

# ══════════════════════ 输出 ══════════════════════
print('\n[OUT] 写文件')
exp = pd.DataFrame(ROWS)
p = os.path.join(HERE, 'three_stage_rebound_experiments_%s.csv' % DATE)
exp.to_csv(p, index=False, encoding='utf-8-sig')
print('  已写', p, exp.shape)

# 事件链回溯表
chain = pd.DataFrame({
    'event_id': base['event_id'], 'ts_code': base['ts_code'], 'name': base['name'],
    'industry': base['industry'], 't0_date': base['trade_date'], 'board': base['board'],
    'regime_t0': regime, 't0_quality': base['t0_quality'],
    'mv_grp': base['mv_grp'], 'to_grp': base['to_grp'],
    'fd_date': m['dt'][np.arange(n), np.clip(np.where(FD > 0, FD, 0), 0, L.KMAX)],
    'fd_offset': FD, 'dd_at_fd': L.value_at(m['dd'], FD), 'cp_at_fd': L.value_at(m['cp'], FD),
    'vr_at_fd': L.value_at(m['vr'], FD),
    'kd': KD, 'V1_vr20': v1m, 'V4_run': dec[KD]['V4_run'], 'V2_vov0': dec[KD]['V2_vov0'],
    'V3_vovfd': dec[KD]['V3_vovfd'], 'V6_dvol_uvol': dec[KD]['V6_dvol_uvol'],
    'ddmin': SF['ddmin'], 'S_rule': S_lb, 'S_hold': SF[S_lb],
    'restart_date': m['dt'][np.arange(n), np.clip(np.where(RD_main > 0, RD_main, 0), 0, L.KMAX)],
    'restart_offset': RD_main, 'restart_rule': R_lb,
    'restart_vr': m['vr'][np.arange(n), np.clip(np.where(RD_main > 0, RD_main, 0), 0, L.KMAX)],
    'entry_offset': EN_anc, 'entry_date': m['dt'][np.arange(n), np.clip(EN_anc, 0, L.KMAX)],
    'entry_rule': EN_lb, 'entry_amount': e_amt, 'entry_limit_up': e_isl,
    'tradable': tradable,
    'in_B': B_mk, 'in_C': C_mk, 'in_D': S_mk, 'in_E': E_mk,
    'in_F': F_mk, 'in_G': G_mk, 'clean': clean})
for N in HOR:
    chain['ret_from_T0_%d' % N] = L.fwd_ret(m, A0, N, COST) * 100
    chain['ret_from_entry_%d' % N] = L.fwd_ret(m, EN_anc, N, COST) * 100
p = os.path.join(HERE, 'three_stage_rebound_events_%s.csv' % DATE)
chain.to_csv(p, index=False, encoding='utf-8-sig')
print('  已写', p, chain.shape)

# 特征表 §21（分歧锚定）
feat = pd.DataFrame({
    'event_id': base['event_id'], 'ts_code': base['ts_code'], 't0_date': base['trade_date'],
    'industry': base['industry'], 'board': base['board'], 'regime_t0': regime,
    't0_quality': base['t0_quality'], 'fd_offset': FD})
fdk = np.clip(np.where(FD > 0, FD, 0), 0, L.KMAX)
ar = np.arange(n)
feat['ret_1'] = m['ret'][:, 1].astype(float) * 100
feat['ret_2'] = m['ret'][:, 2].astype(float) * 100
feat['ret_at_fd'] = L.value_at(m['ret'], FD) * 100
feat['dd_at_fd'] = L.value_at(m['dd'], FD) * 100
feat['ddl_at_fd'] = L.value_at(m['ddl'], FD) * 100
feat['dist_H0'] = (m['cl'][ar, fdk] / H0 - 1) * 100
feat['dist_C0'] = (m['cl'][ar, fdk] / C0 - 1) * 100
feat['dist_L0'] = (m['cl'][ar, fdk] / m['L0'].astype(np.float64) - 1) * 100
feat['vr_at_fd'] = L.value_at(m['vr'], FD) * 100
feat['vov0_at_fd'] = L.value_at(m['vov0'], FD) * 100
feat['vov5_at_fd'] = L.value_at(m['vov5'], FD) * 100
feat['V1_vr20'] = v1m * 100
for k in ('V2_vov0', 'V3_vovfd', 'V6_dvol_uvol'):
    feat[k] = dec[KD][k] * 100
feat['V4_run'] = dec[KD]['V4_run']
feat['to_rate_fd'] = m['to_rate'][ar, fdk]
feat['to_rate_t0'] = m['to_rate'][ar, 0]
feat['to_cum'] = np.nansum(np.where((np.arange(L.KMAX + 1)[None, :] >= 1)
                                    & (np.arange(L.KMAX + 1)[None, :] <= 10),
                                    m['to_rate'], 0.0), axis=1)
feat['body'] = base['body'] * 100
feat['upper_shadow'] = base['upper_shadow'] * 100
feat['lower_shadow'] = base['lower_shadow'] * 100
feat['t0_amplitude'] = base['amplitude'] * 100
feat['cp_at_fd'] = L.value_at(m['cp'], FD) * 100
feat['dma5'] = L.value_at(m['dma5'], FD) * 100
feat['dma10'] = L.value_at(m['dma10'], FD) * 100
feat['dma20'] = L.value_at(m['dma20'], FD) * 100
feat['dma60'] = L.value_at(m['dma60'], FD) * 100
feat['ma5slope'] = L.value_at(m['ma5slope'], FD) * 100
feat['rs_at_fd'] = L.value_at(m['rs'], FD) * 100
feat['idx_ret_at_fd'] = L.value_at(m['idxret'], FD) * 100
feat['t0_vr20'] = T0VR
feat['t0_ret20prev'] = base['t0_ret20prev'] * 100
feat['total_mv'] = MV
feat['ddmin'] = SF['ddmin'] * 100
for kk in s_keys:
    feat[kk] = SF[kk]
for N in HOR:
    feat['fwd_from_T0_%d' % N] = L.fwd_ret(m, A0, N, COST) * 100
    feat['fwd_from_fd_%d' % N] = L.fwd_ret(m, np.where(FD > 0, FD, 0).astype(np.int64), N, COST) * 100
p = os.path.join(HERE, 'three_stage_rebound_features_%s.csv' % DATE)
feat.to_csv(p, index=False, encoding='utf-8-sig')
print('  已写', p, feat.shape)

meta = {'date': DATE, 'n_events': int(n), 'n_clean': int(mass.sum()),
        'periods': {k: int(v.sum()) for k, v in PERIOD.items()},
        'selection': SELECT, 'params': {'fd_rule': L1_lb, 'fd_info': L1_info,
                                        'C': C_lb, 'C_info': C_info, 'V_THR': V_THR, 'KD': KD,
                                        'S': S_lb, 'S_info': S_info, 'R': R_lb, 'R_info': R_info,
                                        'VT': VT, 'VT_info': VT_info, 'E': EN_lb, 'E_info': EN_info},
        'groups': {lb: int((mk & mass).sum()) for lb, mk in GRP}}
with open(os.path.join(OUT, 'tr_core_meta.json'), 'w', encoding='utf-8') as f:
    json.dump(meta, f, ensure_ascii=False, indent=1, default=str)
print('  已写 out/tr_core_meta.json')
print('\n完成实验层')
