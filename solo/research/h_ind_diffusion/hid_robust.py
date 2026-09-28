# -*- coding: utf-8 -*-
"""
H-IND-DIFFUSION-01  P6：稳健性（STEP14-20 / §33-§43 / §49）

隔离声明：本模块自包含，不 import 任何既有研究模块（TL-01 / H-TREND-PULLBACK / HVT / ARCHIVE）；
          也不 import 本课题其他阶段模块（避免 mklog 截断其日志）。

产出（§57）
    oos_results.csv         §33 OOS / §30 判别：逐 phase 信号层（IC/AUC/Prec/Q5-Q1）+ 组合层（DIFF/B4/增量）
    walkforward.csv         §34 6 个滚动窗口（train 3y / valid 6M / oos 6M，无未来数据）
    year_results.csv        §35 2021-2026 逐年
    regime_results.csv      §36 BEAR/NORMAL/BULL
    parameter_surface.csv   §37/§38 参数扰动与参数面（4 轴，主面 4×3）
    cost_analysis.csv       §41 成本（0/10/20/30/50bp 往返）+ §42 信号密度
    tail_analysis.csv       §39 Leave-Top1%/5%/10%
    failure_analysis.csv    §49 F0-F8 失败归因
    data/panel/_meta_p6.json

预声明口径（**在跑数之前冻结**；P7 只按此读表，§54 禁止事后改口径救援）
    [O1] 主信号方向 = 原始方向（高扩散暴露 -> 高未来收益，§19/§21 的方向假设）。
         反向（低暴露 -> 高收益）仅作稳健性披露，**不参与任何 Gate 判定**（§59 禁止择优改向）。
    [O2] G9  对象：exposure(P3 主口径) 在 OOS 期的 ALL 口径 Rank IC 与 t_NW 均 > 0，且 Q5-Q1 > 0（nq=5）。
    [O3] G13 对象：exposure 在 OOS 期 |IC| >= ic_min(0.02) 且 AUC(gt_ind_median) >= auc_min(0.52)
         且 Prec@10 >= prec10_min(0.10) 且 nq=5 分位单调（原始方向）；另报方向无关上限 AUC' = 1-AUC。
    [O4] 组合口径：与 P4 完全一致（行业 Top3 × 入选行业成员池内 Top20%（§43），等权，k 日信息、
         k+1 开盘进场）。为验证可比性，P6 重建主参数格并与 P4 落盘序列逐日比对（见日志 CHECK）。
    [O5] 成本/尾部口径：重叠 cohort 组合——每日等权买入当日所选，持有 h=5 个交易日，
         第 t 日组合收益 = 在场 cohort（k ∈ [t-h, t-1]）的等权平均日收益；
         日收益用 open 到 open（O2O，r[k] = open[k+2]/open[k+1]-1），与 k+1 开盘进场一致。
         单边换手 turnover_d = 1 / (在场 cohort 数)（每日替换一个 cohort）；
         日成本 = turnover_d × bps / 1e4（**bps 为往返口径**，30bp 往返 <-> 单边 15bp；5 日一全换手 = 30bp）。
    [O6] §41 严格按 0/10/20/30/50 bp 全报；焦点 30bp。§42 同时报 信号日/信号数/暴露/平均持有/换手。
    [O7] §38 参数面：axis A breadth_win W∈{3,5,10,20}（score 变体 = mean(pct_dchg_W, pct_accel20,
         pct_lb_020, pct_rsb_chg_5)）；axis B mom_win M∈{10,20,40}（对照 B4'(M) 的行业动量窗口）；
         axis C ind_n∈{15,20,30}；axis D leader_pct∈{0.10,0.20}（score 变体用 pct_lb_0XX）。
         主面 = A×B（12 格）。评估量与 §44/P4 一致：exe_5 逐日等权均值 与 其相对 B4'(M) 的增量。
    [O8] G14：主面 12 格中增量 > 0 的比例 >= param_same_sign_frac(0.60)，否则 PARAMETER_FRAGILE。
    [O9] G15：Leave-Top5% 后 DIFF-B4 组合日均增量仍 > 0，否则 EXTREME_TAIL_DEPENDENT；
         尾部对象 = O5 口径的 DIFF-B4 日收益序列（剔除最大 1%/5%/10% 收益日）。
    [O10] G11：正年份比例 >= 0.60 且单一年份贡献占比 <= 0.50（贡献 = |年度均值| / Σ|年度均值|）。
    [O11] G10：6 窗口中 OOS 增量(DIFF-B4) 与 IC 同时为正的窗口比例 > 0.50。
    [O12] §49 失败 = P4 主口径 DIFF 选股样本 exe_5 <= 0；F0-F8 按固定优先级首个命中归类（详见 FAIL_ORDER）。
"""
import math
import os
import time
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore', category=RuntimeWarning,
                        message=r'.*(All-NaN|empty slice|Degrees of freedom|invalid value).*')

from hid_common import (DATA, PREREG, PHASES, mklog, pload, pload_day,
                        pload_meta, code_grid, rows_by_code,
                        ic_stats, daily_ic, auc_binary, rank_avg,
                        monotonicity, out_csv, psave_meta_side,
                        prereg_hash, derived_hash)

LOG = mklog('robust')
PAN = os.path.join(DATA, 'panel')
HERE = os.path.dirname(os.path.abspath(__file__))

HZ = tuple(PREREG['primary_horizons'])       # (3, 5, 10)
H_MAIN = 5                                   # §41/§43 主持有期
NQ = int(PREREG['n_quantiles'])              # 5
MIN_XS = 30
MIN_IND = int(PREREG['ind_n_min'])           # 20
TOPK_IND = 3
TOP_FRAC = 0.20
PH_NAMES = ['ALL'] + list(PHASES)
REG_NAMES = ['BEAR', 'NORMAL', 'BULL']
YEARS = list(PREREG['year_report'])          # (2021..2026)
COST_BPS = tuple(PREREG['cost_bps'])
BPS_FOCUS = int(PREREG['cost_focus_bps'])
TAIL_PCTS = tuple(PREREG['tail_pcts'])
YEAR_POS_FRAC = float(PREREG['year_pos_frac'])
YEAR_MAX_SHARE = float(PREREG['year_max_share'])
WF_POS_FRAC = float(PREREG['wf_pos_frac'])
PARAM_FRAC = float(PREREG['param_same_sign_frac'])
REG_FLIP_MAX = int(PREREG['regime_flip_max'])

# §34：6 个滚动窗口（train 3 年 / valid 6 个月 / oos 6 个月），无未来数据
WINDOWS = [
    ('W1', ('2018-01', '2020-12'), ('2021-01', '2021-06'), ('2021-07', '2021-12')),
    ('W2', ('2018-07', '2021-06'), ('2021-07', '2021-12'), ('2022-01', '2022-06')),
    ('W3', ('2019-01', '2021-12'), ('2022-01', '2022-06'), ('2022-07', '2022-12')),
    ('W4', ('2020-01', '2022-12'), ('2023-01', '2023-06'), ('2023-07', '2023-12')),
    ('W5', ('2021-01', '2023-12'), ('2024-01', '2024-06'), ('2024-07', '2024-12')),
    ('W6', ('2022-01', '2024-12'), ('2025-01', '2025-06'), ('2025-07', '2025-12')),
]

# §37 参数扰动邻域（只允许 PREREG 声明的取值）
GRID_B = tuple(PREREG['grid_breadth_win'])   # (3,5,10,20)
GRID_M = tuple(PREREG['grid_mom_win'])       # (10,20,40)
GRID_N = tuple(PREREG['grid_ind_n'])         # (15,20,30)
GRID_L = tuple(PREREG['grid_leader'])        # (0.10,0.20)

# §49 失败归因（固定优先级，首个命中即归类；全部为可计算代理，无新闻数据）
FAIL_ORDER = ['F4', 'F1', 'F2', 'F6', 'F3', 'F7', 'F8', 'F5', 'F0']
FAIL_NAME = {
    'F0': 'Other/Unclassified',
    'F1': 'Industry Momentum Reversal',
    'F2': 'Breadth Fake Expansion',
    'F3': 'Leader Collapse',
    'F4': 'Market Regime Reversal',
    'F5': 'Industry Rotation',
    'F6': 'Narrow Leadership',
    'F7': 'Stock-specific Shock',
    'F8': 'Liquidity Failure',
}


# ══════════════════════════════════════════════════════════════ 工具（自包含）

def f32(name):
    return np.asarray(pload(name), dtype=np.float32)


def p_two(t):
    """双侧正态 p（Newey-West t 的近似 p；供 §46 BH-FDR 使用）"""
    if t is None or not np.isfinite(t):
        return np.nan
    return float(math.erfc(abs(float(t)) / math.sqrt(2.0)))


def _spearman(a, b):
    ra = rank_avg(a)
    rb = rank_avg(b)
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    d = math.sqrt(float((ra * ra).sum()) * float((rb * rb).sum()))
    return float((ra * rb).sum() / d) if d > 0 else np.nan


def daily_ic_within(sig, tgt, VAL, ind2, RB, min_n=MIN_IND):
    """逐日「行业内 Rank IC 的行业等权平均」（§23 行业内部口径）。返回 (NCAL,)"""
    NCAL = sig.shape[1]
    out = np.full(NCAL, np.nan)
    for k in range(NCAL):
        icode = ind2[:, k]
        vals = []
        for c, rows in RB.items():
            m = VAL[rows, k] & (icode[rows] == c)
            if int(m.sum()) < min_n:
                continue
            s = np.asarray(sig[rows[m], k], dtype=np.float64)
            t = np.asarray(tgt[rows[m], k], dtype=np.float64)
            f = np.isfinite(s) & np.isfinite(t)
            if int(f.sum()) < min_n:
                continue
            r = _spearman(s[f], t[f])
            if np.isfinite(r):
                vals.append(r)
        if vals:
            out[k] = float(np.mean(vals))
    return out


def ind_median_map(x, VAL, ind2, RB, min_n=5):
    """逐 (日, 行业) 中位数映射回个股面板；返回 (NCODES, NCAL)"""
    out = np.full(x.shape, np.nan, dtype=np.float64)
    for c, rows in RB.items():
        sub = (ind2[rows, :] == c) & VAL[rows, :] & np.isfinite(x[rows, :])
        n = sub.sum(0)
        with np.errstate(invalid='ignore'):
            med = np.nanmedian(np.where(sub, x[rows, :], np.nan), axis=0)
        keep = np.isfinite(med) & (n >= min_n)
        out[rows, :] = np.where(sub & keep[None, :], med[None, :], np.nan)
    return out


def daily_prec_rec(sig, lab, VAL, frac=0.10, min_n=MIN_XS):
    """逐日 Precision@top-frac / Recall@top-frac（信号降序）"""
    NCAL = sig.shape[1]
    prec = np.full(NCAL, np.nan)
    rec = np.full(NCAL, np.nan)
    for k in range(NCAL):
        s = np.asarray(sig[VAL[:, k], k], dtype=np.float64)
        y = np.asarray(lab[VAL[:, k], k], dtype=np.float64)
        f = np.isfinite(s) & np.isfinite(y)
        n = int(f.sum())
        if n < min_n:
            continue
        ss, yy = s[f], y[f]
        ntop = max(1, int(round(frac * n)))
        idx = np.argsort(-ss, kind='stable')[:ntop]
        prec[k] = float(yy[idx].mean())
        tot = float(yy.sum())
        rec[k] = float(yy[idx].sum() / tot) if tot > 0 else np.nan
    return prec, rec


def quantile_scan(sig, tgt, VAL, ind2, RB, nq, scopes):
    """分位扫描（ALL / WITHIN_IND 一次遍历）。返回 {scope: (S, C)}"""
    NCAL = sig.shape[1]
    Su, Cn = {}, {}
    for sc in scopes:
        Su[sc] = np.zeros((NCAL, nq))
        Cn[sc] = np.zeros((NCAL, nq), dtype=np.int64)
    for k in range(NCAL):
        m = VAL[:, k] & np.isfinite(sig[:, k]) & np.isfinite(tgt[:, k])
        if int(m.sum()) < MIN_XS:
            continue
        idx = np.nonzero(m)[0]
        s = np.asarray(sig[idx, k], dtype=np.float64)
        t = np.asarray(tgt[idx, k], dtype=np.float64)
        if 'ALL' in scopes:
            n = len(s)
            r = rank_avg(s)
            b = np.minimum(nq - 1, ((r - 1.0) / n * nq).astype(np.int64))
            for q in range(nq):
                sel = (b == q)
                if sel.any():
                    Su['ALL'][k, q] = t[sel].sum()
                    Cn['ALL'][k, q] = int(sel.sum())
        if 'WITHIN_IND' in scopes:
            icode = ind2[idx, k]
            for c, _rows in RB.items():
                pos = np.nonzero(icode == c)[0]
                if len(pos) < MIN_IND:
                    continue
                nn = len(pos)
                rr = rank_avg(s[pos])
                b = np.minimum(nq - 1, ((rr - 1.0) / nn * nq).astype(np.int64))
                for q in range(nq):
                    sel = (b == q)
                    if sel.any():
                        Su['WITHIN_IND'][k, q] += t[pos][sel].sum()
                        Cn['WITHIN_IND'][k, q] += int(sel.sum())
    res = {}
    for sc in scopes:
        res[sc] = (np.where(Cn[sc] > 0, Su[sc] / np.maximum(Cn[sc], 1), np.nan), Cn[sc])
    return res


def ind_top(g_ok, g_val, topk):
    """逐日按行业层指标降序取 Top-K 行业代码（行 0 = UNKNOWN 自动排除）"""
    n_ind, NCAL = g_val.shape
    out = np.full((topk, NCAL), -1, dtype=np.int16)
    for k in range(NCAL):
        ok = (np.isfinite(g_ok[:, k]) & (g_ok[:, k] > 0.5)
              & np.isfinite(g_val[:, k]))
        ok[0] = False
        if not ok.any():
            continue
        idx = np.nonzero(ok)[0]
        order = idx[np.argsort(-g_val[idx, k], kind='stable')]
        out[:min(topk, len(order)), k] = order[:min(topk, len(order))]
    return out


def member(top, ind2, k):
    codes = top[:, k]
    codes = codes[codes > 0]
    if len(codes) == 0:
        return np.zeros(ind2.shape[0], dtype=bool)
    return np.isin(ind2[:, k], codes)


def top_frac(sig_col, base, frac):
    m = np.zeros(len(sig_col), dtype=bool)
    idx = np.nonzero(np.asarray(base, dtype=bool) & np.isfinite(sig_col))[0]
    if len(idx) == 0:
        return m
    ntop = max(1, int(np.ceil(frac * len(idx))))
    m[idx[np.argsort(-sig_col[idx], kind='stable')[:ntop]]] = True
    return m


def build_sel(TOP, rank, base, ind2):
    """§43 组合：行业 Top3 ∩ 入选行业成员池内按 rank 取 Top20%"""
    sel = np.zeros(base.shape, dtype=np.int8)
    for k in range(base.shape[1]):
        b = base[:, k]
        if not b.any():
            continue
        md = b & member(TOP, ind2, k)
        if md.any():
            sel[:, k] = (md & top_frac(rank[:, k], md, TOP_FRAC)).astype(np.int8)
    return sel


def port_sel_mean(sel, R):
    """逐日所选等权收益均值（P4 口径）"""
    NCAL = sel.shape[1]
    out = np.full(NCAL, np.nan)
    for k in range(NCAL):
        m = sel[:, k] > 0
        if not m.any():
            continue
        v = np.asarray(R[m, k], dtype=np.float64)
        v = v[np.isfinite(v)]
        if len(v):
            out[k] = float(v.mean())
    return out


def o2o_mat(O, h):
    """open 到 open h 日收益：open[k+1+h]/open[k+1]-1（k+1 开盘进场）"""
    NCAL = O.shape[1]
    R = np.full(O.shape, np.nan, dtype=np.float64)
    if 1 + h < NCAL:
        num = O[:, 1 + h:]
        den = O[:, 1:NCAL - h]
        with np.errstate(invalid='ignore', divide='ignore'):
            v = num / den - 1.0
        R[:, :NCAL - 1 - h] = np.where((den > 0) & np.isfinite(v), v, np.nan)
    return R


def cohort_daily(sel, R1, h):
    """重叠 cohort 组合日收益 + 每日在场 cohort 数（O5 口径）"""
    NCAL = sel.shape[1]
    CM = np.full((NCAL, h), np.nan)
    for k in range(NCAL):
        m = sel[:, k] > 0
        if not m.any():
            continue
        for j in range(h):
            t = k + j
            if t >= NCAL:
                break
            v = np.asarray(R1[m, t], dtype=np.float64)
            v = v[np.isfinite(v)]
            if len(v):
                CM[k, j] = v.mean()
    P = np.full(NCAL, np.nan)
    HACT = np.zeros(NCAL, dtype=np.int64)
    for t in range(NCAL):
        vals = []
        for j in range(1, h + 1):
            k = t - j
            if k < 0:
                continue
            v = CM[k, j - 1]
            if np.isfinite(v):
                vals.append(v)
        if vals:
            P[t] = float(np.mean(vals))
            HACT[t] = len(vals)
    turn = np.where(HACT > 0, 1.0 / np.maximum(HACT, 1), np.nan)
    return P, HACT, turn


def turnover_rebal(sel):
    """诊断用：日频完全重选口径的单边换手 0.5*Σ|w-w_prev|"""
    NCAL = sel.shape[1]
    N = sel.shape[0]
    out = np.full(NCAL, np.nan)
    prev = np.zeros(N)
    for k in range(NCAL):
        m = sel[:, k] > 0
        if m.any():
            w = np.zeros(N); w[m] = 1.0 / m.sum()
        else:
            w = np.zeros(N)
        out[k] = 0.5 * float(np.abs(w - prev).sum())
        prev = w
    return out


def cs_pct(X, VAL):
    """逐日横截面百分位（0~1）"""
    out = np.full(X.shape, np.nan, dtype=np.float64)
    for k in range(X.shape[1]):
        m = VAL[:, k] & np.isfinite(X[:, k])
        n = int(m.sum())
        if n < 5:
            continue
        out[m, k] = rank_avg(np.asarray(X[m, k], dtype=np.float64)) / n
    return out


def ym_mask(day, a, b):
    y = np.asarray(day['year']).astype(np.int64)
    mo = np.asarray(day['month']).astype(np.int64)
    ka = int(a[:4]) * 12 + int(a[5:7])
    kb = int(b[:4]) * 12 + int(b[5:7])
    kk = y * 12 + mo
    return (kk >= ka) & (kk <= kb)


def tail_leave(x, mask, pcts):
    """Leave-Top p% 后均值 / t（按日收益值大小剔除）"""
    v = np.asarray(x, dtype=np.float64)[mask]
    v = v[np.isfinite(v)]
    out = {}
    if len(v) == 0:
        return out
    st = ic_stats(v, lag=19)
    out['base'] = (st['mean'], st['t_nw'], st['n'])
    for p in pcts:
        ncut = int(np.ceil(p * len(v)))
        if ncut <= 0:
            out[p] = (st['mean'], st['t_nw'], len(v))
            continue
        keep = np.sort(v)[:len(v) - ncut]
        s = ic_stats(keep, lag=19)
        out[p] = (s['mean'], s['t_nw'], len(keep))
    return out


# ══════════════════════════════════════════════════════════════ 主流程

def main():
    t0_all = time.time()
    LOG.sep('=')
    LOG('H-IND-DIFFUSION-01  P6 稳健性（STEP14-20 / §33-§43 / §49）  hash=%s derived=%s  %s'
        % (prereg_hash(), derived_hash(), time.strftime('%Y-%m-%d %H:%M:%S')))
    LOG.sep()

    meta = pload_meta()
    NCODES, NCAL = int(meta['NCODES']), int(meta['NCAL'])
    n_l1 = int(meta['n_l1'])
    day = pload_day()
    td = np.asarray(day['dates']).astype(str)
    year = np.asarray(day['year']).astype(np.int64)
    phc = np.asarray(day['phase']).astype(np.int8)
    regc = np.asarray(day['regime']).astype(np.int8)
    PHM = {'ALL': np.ones(NCAL, dtype=bool)}
    for i, nm in enumerate(PHASES):
        PHM[nm] = (phc == i + 1)
    REGM = {nm: (regc == i) for i, nm in enumerate(REG_NAMES)}
    YRM = {y: (year == y) for y in YEARS}
    LOG('  面板 %d 股 × %d 日；%s ~ %s' % (NCODES, NCAL, td[0], td[-1]))
    LOG('  分期 %s' % {k: int(v.sum()) for k, v in PHM.items() if k != 'ALL'})
    LOG('  Regime %s' % {k: int(v.sum()) for k, v in REGM.items()})

    ind_l1 = np.asarray(pload('_code_ind_l1'))
    RB = rows_by_code(ind_l1, NCODES)
    SCOPE = (np.asarray(pload('expo_expo_scope')) == 1)
    EXPO = np.asarray(pload('expo_expo_ind'), dtype=np.float32)
    RET20 = np.asarray(pload('ret_20'), dtype=np.float32)
    TRAD = (np.asarray(pload('tradable_next')) == 1)
    CAND = SCOPE & np.isfinite(RET20) & np.isfinite(EXPO)
    EXE = {h: np.asarray(pload('exe_%d' % h), dtype=np.float32) for h in HZ}
    O = np.asarray(pload('open'), dtype=np.float64)
    R1 = o2o_mat(O, 1)

    # P4 组合序列（唯一权威：G20 / OOS / 年份 / regime 的组合层口径与 P4 一致）
    z = np.load(os.path.join(PAN, '_strat_p4.npz'))
    S4 = {(nm, h): np.asarray(z['%s_h%d' % (nm, h)], dtype=np.float64)
          for nm in ('B1', 'B2', 'B3', 'B4', 'DIFF') for h in HZ}
    SEL4 = {nm: np.asarray(z['sel_%s' % nm]) for nm in ('B1', 'B2', 'B3', 'B4', 'DIFF')}
    DIF4 = {h: np.asarray(z['DIFF-B4_h%d' % h], dtype=np.float64) for h in HZ}
    LOG('  [LOAD] _strat_p4.npz；CAND=%d（占 valid %.4f）'
        % (int(CAND.sum()), CAND.sum() / max(1, int((np.asarray(pload('valid')) == 1).sum()))))

    # ---- §3 可成交性诊断（G3，不作过滤，保持与 P4 同一策略） ----
    selD_p4 = SEL4['DIFF'] > 0
    trad_rate = float(TRAD[selD_p4].mean()) if selD_p4.any() else np.nan
    LOG('  [G3 诊断] DIFF 入选样本 T+1 可成交率 = %.4f（阈值 %.2f）'
        % (trad_rate, float(PREREG['tradable_min'])))

    # ══════════════════════════════════════════════════ 信号层日序列（一次算全）
    LOG.sep('─')
    LOG('[信号层] 逐日 IC / 行业内 IC / AUC / Prec@10 / 分位（exposure，原始方向）')
    t0 = time.time()
    IC_ALL, IC_WIN, AUC_M, PREC = {}, {}, {}, {}
    QS = {}
    for h in HZ:
        IC_ALL[h] = daily_ic(EXPO, EXE[h], SCOPE, min_xs=MIN_XS)
        IC_WIN[h] = daily_ic_within(EXPO, EXE[h], SCOPE, ind_l1, RB, min_n=MIN_IND)
        MED = ind_median_map(np.asarray(EXE[h], dtype=np.float64), SCOPE, ind_l1, RB)
        with np.errstate(invalid='ignore'):
            lab = np.where(np.isfinite(EXE[h]) & np.isfinite(MED),
                           (np.asarray(EXE[h], dtype=np.float64) > MED).astype(np.float64), np.nan)
        AUC_M[h] = auc_binary(EXPO, lab, SCOPE, min_n=MIN_XS)
        PREC[h], _rec = daily_prec_rec(EXPO, lab, SCOPE, frac=0.10)
        QS[h] = quantile_scan(EXPO, EXE[h], SCOPE, ind_l1, RB, NQ, ['ALL', 'WITHIN_IND'])
        LOG('  h=%-2d IC_ALL=%+.4f IC_WIN=%+.4f AUC=%+.4f Prec@10=%.4f（%.0fs）'
            % (h, np.nanmean(IC_ALL[h][PHM['ALL']]), np.nanmean(IC_WIN[h][PHM['ALL']]),
               np.nanmean(AUC_M[h][PHM['ALL']]), np.nanmean(PREC[h][PHM['ALL']]),
               time.time() - t0))

    def q5q1_series(h, scope):
        S, C = QS[h][scope]
        with np.errstate(invalid='ignore'):
            v = S[:, NQ - 1] - S[:, 0]
        return v

    Q5Q1 = {(h, sc): q5q1_series(h, sc) for h in HZ for sc in ('ALL', 'WITHIN_IND')}

    def sig_metrics(h, scope, mask):
        """某一子样本下的信号层指标（含分位均值与单调性，单调性按子样本内均值序列计算）"""
        ic = IC_ALL[h] if scope == 'ALL' else IC_WIN[h]
        icm = ic[mask]
        st_ic = ic_stats(icm, lag=19)
        au = ic_stats(AUC_M[h][mask], lag=19)
        pr = PREC[h][mask]
        qv = Q5Q1[(h, scope)][mask]
        S, C = QS[h][scope]
        with np.errstate(invalid='ignore'):
            qmean = np.nanmean(S[mask], axis=0)
        rho, slope = monotonicity(qmean)
        return {'ic_mean': st_ic['mean'], 'ic_t_nw': st_ic['t_nw'], 'ic_pos': st_ic['pos_ratio'],
                'ic_n': st_ic['n'], 'auc': au['mean'], 'auc_minus_05': au['mean'] - 0.5,
                'prec10': float(np.nanmean(pr)) if mask.any() else np.nan,
                'q1': float(qmean[0]), 'q5': float(qmean[NQ - 1]),
                'q5_q1': float(np.nanmean(qv)) if mask.any() else np.nan,
                'mono_rho': rho, 'mono_slope': slope}

    def port_metrics(x, mask, lag=H_MAIN):
        st = ic_stats(np.asarray(x, dtype=np.float64)[mask], lag=lag)
        return {'mean_ret': st['mean'], 't_nw': st['t_nw'], 'pos_ratio': st['pos_ratio'],
                'n_days': st['n'], 'mean_ann': st['mean'] * 243 if np.isfinite(st['mean']) else np.nan}

    # ══════════════════════════════════════════════════ §33 OOS
    LOG.sep('─')
    LOG('[STEP14] §33 OOS：TRAIN/VALID/OOS/LIVE-LIKE 分层（特征与阈值均预注册，无拟合）')
    oos_rows = []
    for h in HZ:
        for sc in ('ALL', 'WITHIN_IND'):
            for ph in PH_NAMES:
                m = PHM[ph]
                if not m.any():
                    continue
                sm = sig_metrics(h, sc, m)
                oos_rows.append({'object': 'signal', 'scope': sc, 'horizon': h, 'phase': ph,
                                 'n_days': sm['ic_n'], 'ic_mean': sm['ic_mean'],
                                 'ic_t_nw': sm['ic_t_nw'], 'ic_pos_ratio': sm['ic_pos'],
                                 'auc': sm['auc'], 'auc_minus_05': sm['auc_minus_05'],
                                 'prec10': sm['prec10'], 'q1': sm['q1'], 'q5': sm['q5'],
                                 'q5_q1': sm['q5_q1'], 'mono_rho': sm['mono_rho'],
                                 'mono_slope': sm['mono_slope'],
                                 'mean_ret': np.nan, 't_nw': np.nan, 'pos_ratio': np.nan,
                                 'p_value': p_two(sm['ic_t_nw'])})
    for nm, x in (('B1', S4), ('B2', S4), ('B3', S4), ('B4', S4), ('DIFF', S4)):
        for h in HZ:
            for ph in PH_NAMES:
                m = PHM[ph]
                pm = port_metrics(x[(nm, h)][m], np.ones(int(m.sum()), dtype=bool), lag=h)
                oos_rows.append({'object': nm, 'scope': 'PORT', 'horizon': h, 'phase': ph,
                                 'n_days': pm['n_days'], 'ic_mean': np.nan, 'ic_t_nw': np.nan,
                                 'ic_pos_ratio': np.nan, 'auc': np.nan, 'auc_minus_05': np.nan,
                                 'prec10': np.nan, 'q1': np.nan, 'q5': np.nan, 'q5_q1': np.nan,
                                 'mono_rho': np.nan, 'mono_slope': np.nan,
                                 'mean_ret': pm['mean_ret'], 't_nw': pm['t_nw'],
                                 'pos_ratio': pm['pos_ratio'], 'p_value': p_two(pm['t_nw'])})
    for h in HZ:
        for ph in PH_NAMES:
            m = PHM[ph]
            pm = port_metrics(DIF4[h][m], np.ones(int(m.sum()), dtype=bool), lag=h)
            oos_rows.append({'object': 'DIFF-B4', 'scope': 'PORT', 'horizon': h, 'phase': ph,
                             'n_days': pm['n_days'], 'ic_mean': np.nan, 'ic_t_nw': np.nan,
                             'ic_pos_ratio': np.nan, 'auc': np.nan, 'auc_minus_05': np.nan,
                             'prec10': np.nan, 'q1': np.nan, 'q5': np.nan, 'q5_q1': np.nan,
                             'mono_rho': np.nan, 'mono_slope': np.nan,
                             'mean_ret': pm['mean_ret'], 't_nw': pm['t_nw'],
                             'pos_ratio': pm['pos_ratio'], 'p_value': p_two(pm['t_nw'])})
    out_csv('oos_results.csv', pd.DataFrame(oos_rows))
    LOG('  [SAVE] oos_results.csv rows=%d' % len(oos_rows))
    for ph in ('TRAIN', 'VALID', 'OOS', 'LIVE-LIKE'):
        r = [x for x in oos_rows if x['object'] == 'signal' and x['scope'] == 'ALL'
             and x['horizon'] == H_MAIN and x['phase'] == ph]
        d = [x for x in oos_rows if x['object'] == 'DIFF-B4' and x['horizon'] == H_MAIN
             and x['phase'] == ph]
        if r and d:
            LOG('    %-10s ICh%d=%+.4f t=%+.2f AUC=%.4f Q5-Q1=%+.4f | DIFF-B4 h5=%+.5f t=%+.2f'
                % (ph, H_MAIN, r[0]['ic_mean'], r[0]['ic_t_nw'], r[0]['auc'], r[0]['q5_q1'],
                   d[0]['mean_ret'], d[0]['t_nw']))

    # ══════════════════════════════════════════════════ §34 Walk Forward
    LOG.sep('─')
    LOG('[STEP15] §34 Walk Forward：%d 窗口（train 3y / valid 6M / oos 6M，滚动半年）' % len(WINDOWS))
    wf_rows = []
    for name, tr, va, oo in WINDOWS:
        mtr = ym_mask(day, *tr)
        mva = ym_mask(day, *va)
        moo = ym_mask(day, *oo)
        for h in HZ:
            row = {'window': name, 'horizon': h,
                   'train_start': tr[0], 'train_end': tr[1],
                   'valid': '%s~%s' % va, 'oos': '%s~%s' % oo,
                   'n_train': int(mtr.sum()), 'n_valid': int(mva.sum()), 'n_oos': int(moo.sum())}
            sm = sig_metrics(h, 'ALL', mva)
            so = sig_metrics(h, 'ALL', moo)
            row.update({'ic_train': float(np.nanmean(IC_ALL[h][mtr])) if mtr.any() else np.nan,
                        'q5q1_train': float(np.nanmean(Q5Q1[(h, 'ALL')][mtr])) if mtr.any() else np.nan,
                        'ic_valid': sm['ic_mean'], 'ic_valid_t': sm['ic_t_nw'],
                        'q5q1_valid': sm['q5_q1'], 'auc_valid': sm['auc'],
                        'ic_oos': so['ic_mean'], 'ic_oos_t': so['ic_t_nw'],
                        'q5q1_oos': so['q5_q1'], 'auc_oos': so['auc'], 'prec10_oos': so['prec10'],
                        'mono_oos': so['mono_rho']})
            pm = port_metrics(DIF4[h][moo], np.ones(int(moo.sum()), dtype=bool), lag=h) if moo.any() else \
                {'mean_ret': np.nan, 't_nw': np.nan, 'pos_ratio': np.nan, 'n_days': 0}
            pv = port_metrics(DIF4[h][mva], np.ones(int(mva.sum()), dtype=bool), lag=h) if mva.any() else \
                {'mean_ret': np.nan, 't_nw': np.nan, 'pos_ratio': np.nan, 'n_days': 0}
            row.update({'diffb4_oos_mean': pm['mean_ret'], 'diffb4_oos_t': pm['t_nw'],
                        'diffb4_valid_mean': pv['mean_ret'], 'diffb4_valid_t': pv['t_nw'],
                        'p_value': p_two(pm['t_nw'])})
            wf_rows.append(row)
    out_csv('walkforward.csv', pd.DataFrame(wf_rows))
    wf_h5 = [r for r in wf_rows if r['horizon'] == H_MAIN]
    pos_w = sum(1 for r in wf_h5 if (r['diffb4_oos_mean'] or 0) > 0 and (r['ic_oos'] or 0) > 0)
    LOG('  [SAVE] walkforward.csv rows=%d' % len(wf_rows))
    LOG('  h5 窗口明细：')
    for r in wf_h5:
        LOG('    %s oos=%s ICoos=%+.4f t=%+.2f Q5-Q1=%+.4f AUC=%.4f | DIFF-B4=%+.5f t=%+.2f'
            % (r['window'], r['oos'], r['ic_oos'], r['ic_oos_t'], r['q5q1_oos'],
               r['auc_oos'], r['diffb4_oos_mean'], r['diffb4_oos_t']))
    LOG('  [G10] h5 OOS 双正（IC>0 且 增量>0）窗口 %d/%d = %.2f（阈值 > %.2f）'
        % (pos_w, len(wf_h5), pos_w / max(len(wf_h5), 1), WF_POS_FRAC))

    # ══════════════════════════════════════════════════ §35 Cross-Year
    LOG.sep('─')
    LOG('[STEP16] §35 Cross-Year：%s' % list(YEARS))
    yr_rows = []
    for y in YEARS:
        m = YRM[y]
        for h in (H_MAIN,):
            sm = sig_metrics(h, 'ALL', m)
            pmD = port_metrics(S4[('DIFF', h)][m], np.ones(int(m.sum()), dtype=bool), lag=h)
            pmX = port_metrics(DIF4[h][m], np.ones(int(m.sum()), dtype=bool), lag=h)
            pmB = port_metrics(S4[('B4', h)][m], np.ones(int(m.sum()), dtype=bool), lag=h)
            yr_rows.append({'year': y, 'horizon': h, 'n_days': sm['ic_n'],
                            'ic_mean': sm['ic_mean'], 'ic_t_nw': sm['ic_t_nw'],
                            'auc': sm['auc'], 'prec10': sm['prec10'], 'q5_q1': sm['q5_q1'],
                            'mono_rho': sm['mono_rho'],
                            'DIFF_mean': pmD['mean_ret'], 'DIFF_t': pmD['t_nw'],
                            'B4_mean': pmB['mean_ret'], 'B4_t': pmB['t_nw'],
                            'DIFFB4_mean': pmX['mean_ret'], 'DIFFB4_t': pmX['t_nw'],
                            'p_value': p_two(pmX['t_nw'])})
    out_csv('year_results.csv', pd.DataFrame(yr_rows))
    pos_y = sum(1 for r in yr_rows if (r['DIFFB4_mean'] or 0) > 0)
    share = _max_share([abs(r['DIFFB4_mean']) if np.isfinite(r['DIFFB4_mean']) else 0.0
                        for r in yr_rows])
    LOG('  [SAVE] year_results.csv rows=%d' % len(yr_rows))
    for r in yr_rows:
        LOG('    %d ICh5=%+.4f t=%+.2f AUC=%.4f | DIFF-B4=%+.5f t=%+.2f'
            % (r['year'], r['ic_mean'], r['ic_t_nw'], r['auc'], r['DIFFB4_mean'], r['DIFFB4_t']))
    LOG('  [G11] 增量正年份 %d/%d = %.2f（阈值 %.2f）；最大单年贡献占比 %.2f（上限 %.2f）'
        % (pos_y, len(yr_rows), pos_y / max(len(yr_rows), 1), YEAR_POS_FRAC, share, YEAR_MAX_SHARE))

    # ══════════════════════════════════════════════════ §36 Regime
    LOG.sep('─')
    LOG('[STEP17] §36 Regime：%s' % REG_NAMES)
    rg_rows = []
    for nm in REG_NAMES:
        m = REGM[nm]
        for h in (H_MAIN,):
            sm = sig_metrics(h, 'ALL', m)
            pmX = port_metrics(DIF4[h][m], np.ones(int(m.sum()), dtype=bool), lag=h)
            rg_rows.append({'regime': nm, 'horizon': h, 'n_days': sm['ic_n'],
                            'ic_mean': sm['ic_mean'], 'ic_t_nw': sm['ic_t_nw'],
                            'auc': sm['auc'], 'prec10': sm['prec10'],
                            'q5_q1': sm['q5_q1'], 'mono_rho': sm['mono_rho'],
                            'DIFFB4_mean': pmX['mean_ret'], 'DIFFB4_t': pmX['t_nw'],
                            'p_value': p_two(pmX['t_nw'])})
    out_csv('regime_results.csv', pd.DataFrame(rg_rows))
    LOG('  [SAVE] regime_results.csv rows=%d' % len(rg_rows))
    for r in rg_rows:
        LOG('    %-6s ICh5=%+.4f t=%+.2f AUC=%.4f Q5-Q1=%+.4f | DIFF-B4=%+.5f t=%+.2f'
            % (r['regime'], r['ic_mean'], r['ic_t_nw'], r['auc'], r['q5_q1'],
               r['DIFFB4_mean'], r['DIFFB4_t']))
    flips = _count_sign_flips([r['DIFFB4_mean'] for r in rg_rows])
    LOG('  [G12] 三 regime 增量符号翻转次数 = %d（上限 %d）' % (flips, REG_FLIP_MAX))

    # ══════════════════════════════════════════════════ §37/§38 参数面
    LOG.sep('─')
    LOG('[STEP18] §37/§38 参数扰动与参数面（%d 格主面 %dx%d + ind_n + leader）'
        % (len(GRID_B) * len(GRID_M), len(GRID_B), len(GRID_M)))
    t0 = time.time()
    st = pd.read_parquet(os.path.join(HERE, 'industry_daily_state.parquet'))
    ft = pd.read_parquet(os.path.join(HERE, 'industry_diffusion_features.parquet'))
    st = st[st['ind_level'] == 'L1'].copy()
    ft = ft[ft['ind_level'] == 'L1'].copy()
    g_el = code_grid(st, 'eligible', n_l1, NCAL)
    g_n = code_grid(st, 'n_used', n_l1, NCAL)
    g_momM = {M: code_grid(st, 'mom_ew_%d' % M, n_l1, NCAL) for M in GRID_M}

    def score_variant(W, lp):
        comp = ['pct_dchg_%d' % W, 'pct_accel20', 'pct_lb_%03d' % int(round(lp * 100)),
                'pct_rsb_chg_5']
        for c in comp:
            if c not in ft.columns:
                raise KeyError('特征列缺失：%s' % c)
        return code_grid(ft.assign(_s=ft[comp].mean(axis=1, skipna=False)), '_s', n_l1, NCAL)

    surf_rows = []

    def eval_cell(axis, value, score_g, M, ok_n, tag):
        g_ok = g_el > 0.5
        if ok_n:
            g_ok = g_ok & (g_n >= ok_n)
        TOP_D = ind_top(g_ok, score_g, TOPK_IND)
        TOP_M = ind_top(g_ok, g_momM[M], TOPK_IND)
        selD = build_sel(TOP_D, EXPO, CAND, ind_l1)
        selB = build_sel(TOP_M, RET20, CAND, ind_l1)
        sD = port_sel_mean(selD, EXE[H_MAIN])
        sB = port_sel_mean(selB, EXE[H_MAIN])
        d = sD - sB
        m = PHM['ALL']
        stD = ic_stats(sD[m], lag=H_MAIN)
        stB = ic_stats(sB[m], lag=H_MAIN)
        stX = ic_stats(d[m], lag=H_MAIN)
        return {'axis': axis, 'tag': tag, 'param_value': value, 'mom_win': M, 'ind_n_min': ok_n,
                'n_days': stX['n'], 'n_sel_days': int((selD.sum(0) > 0).sum()),
                'DIFF_mean': stD['mean'], 'DIFF_t': stD['t_nw'],
                'B4p_mean': stB['mean'], 'B4p_t': stB['t_nw'],
                'inc_mean': stX['mean'], 'inc_t': stX['t_nw'],
                'inc_ann': stX['mean'] * 243 if np.isfinite(stX['mean']) else np.nan,
                'inc_positive': int(np.isfinite(stX['mean']) and stX['mean'] > 0),
                'p_value': p_two(stX['t_nw'])}

    chk_done = False
    chk_maxdiff = np.nan
    chk_ndays = 0
    for W in GRID_B:
        sg = score_variant(W, 0.20)
        for M in GRID_M:
            r = eval_cell('breadth_win', W, sg, M, MIN_IND,
                          'W%d_M%d' % (W, M))
            # 与 P4 的 CHECK（主参数格 W=5, M=20 必须逐日一致）
            if W == 5 and M == 20:
                sD = port_sel_mean(build_sel(ind_top(g_el > 0.5, sg, TOPK_IND), EXPO, CAND, ind_l1),
                                   EXE[H_MAIN])
                ref = S4[('DIFF', H_MAIN)]
                ok = np.isfinite(sD) & np.isfinite(ref)
                chk_maxdiff = float(np.nanmax(np.abs(sD[ok] - ref[ok]))) if ok.any() else np.nan
                chk_ndays = int(ok.sum())
                LOG('  [CHECK] 主参数格(W5,M20) 与 P4 DIFF_h5 逐日最大偏差 = %.3e（应 ~0，重合 %d 日）'
                    % (chk_maxdiff, chk_ndays))
                chk_done = True
            surf_rows.append(r)
    for M in GRID_M:
        sg = score_variant(5, 0.20)
        surf_rows.append(eval_cell('mom_win', M, sg, M, MIN_IND, 'M%d' % M))
    for nn in GRID_N:
        sg = score_variant(5, 0.20)
        surf_rows.append(eval_cell('ind_n', nn, sg, 20, nn, 'N%d' % nn))
    for lp in GRID_L:
        sg = score_variant(5, lp)
        surf_rows.append(eval_cell('leader_pct', lp, sg, 20, MIN_IND,
                                   'L%02d' % int(round(lp * 100))))
    out_csv('parameter_surface.csv', pd.DataFrame(surf_rows))
    LOG('  [SAVE] parameter_surface.csv rows=%d（%.1fs）' % (len(surf_rows), time.time() - t0))
    main_cells = [r for r in surf_rows if r['axis'] == 'breadth_win']
    LOG('  主面（行=breadth_win，列=mom_win）增量日均收益：')
    hdr = '        ' + ''.join('%14s' % ('M%d' % M) for M in GRID_M)
    LOG(hdr)
    for W in GRID_B:
        line = '  W%-3d ' % W
        for M in GRID_M:
            r = [x for x in main_cells if x['param_value'] == W and x['mom_win'] == M]
            line += '%14s' % ('%+.5f' % r[0]['inc_mean'] if r else 'NA')
        LOG(line)
    LOG('  主面增量 t 值：')
    LOG(hdr)
    for W in GRID_B:
        line = '  W%-3d ' % W
        for M in GRID_M:
            r = [x for x in main_cells if x['param_value'] == W and x['mom_win'] == M]
            line += '%14s' % ('%+.2f' % r[0]['inc_t'] if r else 'NA')
        LOG(line)
    for ax in ('ind_n', 'leader_pct'):
        for r in [x for x in surf_rows if x['axis'] == ax]:
            LOG('  [%s=%-5s] DIFF=%+.5f t=%+.2f | 增量=%+.5f t=%+.2f'
                % (ax, str(r['param_value']), r['DIFF_mean'], r['DIFF_t'],
                   r['inc_mean'], r['inc_t']))
    pos_cells = sum(1 for r in main_cells if r['inc_positive'] == 1)
    LOG('  [G14] 主面 %d 格中增量为正 %d 格 = %.2f（阈值 %.2f）'
        % (len(main_cells), pos_cells, pos_cells / max(len(main_cells), 1), PARAM_FRAC))

    # ══════════════════════════════════════════════════ §39 尾部
    LOG.sep('─')
    LOG('[STEP19] §39 尾部：Leave-Top%s' % (tuple(int(100 * p) for p in TAIL_PCTS),))
    PD, HD, TD = cohort_daily(SEL4['DIFF'], R1, H_MAIN)
    PB, HB, TB = cohort_daily(SEL4['B4'], R1, H_MAIN)
    PX = PD - PB
    tail_rows = []
    for nm, x in (('DIFF', PD), ('B4', PB), ('DIFF-B4', PX)):
        t = tail_leave(x, PHM['ALL'], TAIL_PCTS)
        base = t.get('base', (np.nan, np.nan, 0))
        for p in (None,) + TAIL_PCTS:
            if p is None:
                mu, tv, n = base
                tail_rows.append({'basis': nm, 'leave_pct': 0.0, 'mean_ret': mu, 't_nw': tv,
                                  'n_days': n, 'delta_vs_base': 0.0,
                                  'p_value': p_two(tv)})
            else:
                mu, tv, n = t[p]
                tail_rows.append({'basis': nm, 'leave_pct': float(p), 'mean_ret': mu, 't_nw': tv,
                                  'n_days': n, 'delta_vs_base': mu - base[0],
                                  'p_value': p_two(tv)})
    # 个股样本层（DIFF 选股样本的 exe_5 贡献分布）——诊断
    _I, _K = np.nonzero(SEL4['DIFF'] > 0)
    _V = np.asarray(EXE[H_MAIN], dtype=np.float64)[_I, _K]
    _V = _V[np.isfinite(_V)]
    v = np.sort(_V)
    for p in (0.0,) + TAIL_PCTS:
        ncut = int(np.ceil(p * len(v)))
        keep = v[:len(v) - ncut] if ncut > 0 else v
        s = ic_stats(keep, lag=H_MAIN)
        tail_rows.append({'basis': 'DIFF_selected_stockday_h5', 'leave_pct': float(p),
                          'mean_ret': s['mean'], 't_nw': s['t_nw'], 'n_days': len(keep),
                          'delta_vs_base': np.nan, 'p_value': p_two(s['t_nw'])})
    out_csv('tail_analysis.csv', pd.DataFrame(tail_rows))
    LOG('  [SAVE] tail_analysis.csv rows=%d' % len(tail_rows))
    for r in tail_rows:
        if r['basis'] in ('DIFF-B4', 'DIFF_selected_stockday_h5'):
            LOG('    %-26s leave=%4.0f%% mean=%+.5f t=%+.2f n=%d'
                % (r['basis'], 100 * r['leave_pct'], r['mean_ret'], r['t_nw'], r['n_days']))
    g15 = [r for r in tail_rows if r['basis'] == 'DIFF-B4' and abs(r['leave_pct'] - 0.05) < 1e-9]
    LOG('  [G15] Leave-Top5%% 后 DIFF-B4 增量 = %+.5f（t=%+.2f）-> %s'
        % (g15[0]['mean_ret'], g15[0]['t_nw'],
           'PASS' if g15[0]['mean_ret'] > 0 else 'EXTREME_TAIL_DEPENDENT'))

    # ══════════════════════════════════════════════════ §41/§42 成本与密度
    LOG.sep('─')
    LOG('[STEP20] §41 成本（%s bp 往返，焦点 %d） + §42 信号密度' % (list(COST_BPS), BPS_FOCUS))
    cost_rows = []
    for nm, x, tn in (('DIFF', PD, TD), ('B4', PB, TB), ('DIFF-B4', PX, TD)):
        m = PHM['ALL'] & np.isfinite(x)
        gross = ic_stats(x[m], lag=H_MAIN)
        tv = float(np.nanmean(tn[m]))
        for bps in COST_BPS:
            net = x - tn * bps / 1e4
            sn = ic_stats(net[m], lag=H_MAIN)
            cost_rows.append({'row_type': 'cost', 'strategy': nm, 'bps': int(bps),
                              'n_days': sn['n'], 'turnover_daily': tv,
                              'avg_holding_days': (1.0 / tv) if tv > 0 else np.nan,
                              'gross_mean': gross['mean'], 'gross_t': gross['t_nw'],
                              'cost_daily': tv * bps / 1e4,
                              'net_mean': sn['mean'], 'net_ann': sn['mean'] * 243,
                              'net_t': sn['t_nw'], 'net_pos_ratio': sn['pos_ratio'],
                              'p_value': p_two(sn['t_nw'])})
    for nm, sel in (('DIFF', SEL4['DIFF']), ('B4', SEL4['B4'])):
        m = sel > 0
        tr_reb = turnover_rebal(sel)
        n_sel_days = int((m.sum(0) > 0).sum())
        cost_rows.append({'row_type': 'density', 'strategy': nm, 'bps': -1,
                          'n_days': n_sel_days, 'signal_days': n_sel_days,
                          'signal_count': int(m.sum()),
                          'exposure_avg': float(m.sum() / max(n_sel_days, 1)),
                          'turnover_daily': float(np.nanmean(turnover_rebal(sel))),
                          'avg_holding_days': H_MAIN,
                          'gross_mean': np.nan, 'gross_t': np.nan, 'cost_daily': np.nan,
                          'net_mean': np.nan, 'net_ann': np.nan, 'net_t': np.nan,
                          'net_pos_ratio': np.nan, 'p_value': np.nan})
    out_csv('cost_analysis.csv', pd.DataFrame(cost_rows))
    LOG('  [SAVE] cost_analysis.csv rows=%d' % len(cost_rows))
    for r in cost_rows:
        if r['row_type'] == 'cost' and r['strategy'] == 'DIFF-B4':
            LOG('    DIFF-B4 %2dbp gross=%+.5f cost/day=%.5f net=%+.5f net_ann=%+.2f%% t=%+.2f'
                % (r['bps'], r['gross_mean'], r['cost_daily'], r['net_mean'],
                   100 * r['net_ann'], r['net_t']))
    for r in cost_rows:
        if r['row_type'] == 'density':
            LOG('    [密度] %-5s signal_days=%d signal_count=%d 暴露=%.1f 持有=%.1f 日频重选换手=%.3f'
                % (r['strategy'], r['signal_days'], r['signal_count'],
                   r['exposure_avg'], r['avg_holding_days'], r['turnover_daily']))
    net30 = [r for r in cost_rows if r['row_type'] == 'cost' and r['strategy'] == 'DIFF-B4'
             and r['bps'] == BPS_FOCUS][0]
    LOG('  [G16] %dbp 往返后 DIFF-B4 净增量 = %+.5f（t=%+.2f）-> %s'
        % (BPS_FOCUS, net30['net_mean'], net30['net_t'],
           'PASS' if net30['net_mean'] > 0 else 'COST_FAIL'))
    LOG('  [G19] 经济意义门槛 = %.4f（单笔/日）-> %s'
        % (float(PREREG['econ_min_net']),
           'PASS' if net30['net_mean'] >= float(PREREG['econ_min_net']) else 'FAIL'))

    # ══════════════════════════════════════════════════ §49 失败归因
    LOG.sep('─')
    LOG('[STEP20c] §49 失败归因（失败 = DIFF 选股样本 exe_%d <= 0；优先级 %s）'
        % (H_MAIN, '/'.join(FAIL_ORDER)))
    t0 = time.time()
    g_mom = code_grid(st, 'mom_ew_20', n_l1, NCAL)
    g_chg = code_grid(st, 'dchg_5', n_l1, NCAL)
    g_lb = code_grid(st, 'lb_020', n_l1, NCAL)
    g_nw = code_grid(st, 'narrow_leadership', n_l1, NCAL)
    pct_mom = code_grid(ft, 'pct_mom_ew_20', n_l1, NCAL)
    AMT = np.asarray(pload('amt_ma20'), dtype=np.float64)
    AMT_PCT = cs_pct(AMT, SCOPE)
    R1f = np.asarray(pload('ret_1'), dtype=np.float32)

    I, K = np.nonzero(SEL4['DIFF'] > 0)
    keep = np.isfinite(np.asarray(EXE[H_MAIN], dtype=np.float64)[I, K])
    I, K = I[keep], K[keep]
    N = len(I)
    K5 = np.minimum(K + H_MAIN, NCAL - 1)
    indI = ind_l1[I, K]
    y = np.asarray(EXE[H_MAIN], dtype=np.float64)[I, K]
    fail = y <= 0
    mom_k = g_mom[np.clip(indI, 0, n_l1), K]
    mom_5 = g_mom[np.clip(indI, 0, n_l1), K5]
    chg_k = g_chg[np.clip(indI, 0, n_l1), K]
    chg_5 = g_chg[np.clip(indI, 0, n_l1), K5]
    lb_k = g_lb[np.clip(indI, 0, n_l1), K]
    lb_5 = g_lb[np.clip(indI, 0, n_l1), K5]
    nrw = g_nw[np.clip(indI, 0, n_l1), K]
    pm_k = pct_mom[np.clip(indI, 0, n_l1), K]
    pm_5 = pct_mom[np.clip(indI, 0, n_l1), K5]
    rg_k = regc[K]
    rg_5 = regc[K5]
    r1_next = np.asarray(R1f, dtype=np.float64)[I, np.minimum(K + 1, NCAL - 1)]
    amt_p = AMT_PCT[I, K]
    with np.errstate(invalid='ignore'):
        cond = {
            'F4': (rg_k != 0) & (rg_5 == 0),
            'F1': (mom_k >= 0) & (mom_5 < 0),
            'F2': (chg_k > 0) & (chg_5 <= 0),
            'F6': (nrw == 1),
            'F3': (lb_k >= 0.5) & ((lb_5 - lb_k) <= -0.25),
            'F7': (r1_next <= -0.09) & (mom_5 >= 0),
            'F8': (amt_p <= 0.20),
            'F5': (pm_k >= 0.5) & (pm_5 < 0.5),
        }
    label = np.array(['F0'] * N, dtype=object)
    assigned = np.zeros(N, dtype=bool)
    for f in FAIL_ORDER:
        if f == 'F0':
            continue
        c = np.nan_to_num(cond[f], nan=False).astype(bool) if cond[f].dtype != bool else cond[f]
        hit = c & (~assigned)
        label[hit] = f
        assigned |= hit
    f_rows = []
    for f in FAIL_ORDER:
        sel_f = (label == f)
        nf = int((sel_f & fail).sum())
        na = int(sel_f.sum())
        ys = y[sel_f]
        f_rows.append({'code': f, 'name': FAIL_NAME[f],
                       'n_failed': nf, 'pct_of_failures': nf / max(int(fail.sum()), 1),
                       'n_all': na, 'pct_of_all': na / max(N, 1),
                       'fail_rate_in_bucket': (nf / na) if na > 0 else np.nan,
                       'mean_exe5_bucket': float(np.nanmean(ys)) if na > 0 else np.nan})
    f_rows.append({'code': 'TOTAL', 'name': 'ALL selected samples', 'n_failed': int(fail.sum()),
                   'pct_of_failures': 1.0, 'n_all': N, 'pct_of_all': 1.0,
                   'fail_rate_in_bucket': float(fail.mean()),
                   'mean_exe5_bucket': float(np.nanmean(y))})
    out_csv('failure_analysis.csv', pd.DataFrame(f_rows))
    LOG('  [SAVE] failure_analysis.csv rows=%d（样本 %d，失败 %d，%.1f%%；%.0fs）'
        % (len(f_rows), N, int(fail.sum()), 100 * fail.mean(), time.time() - t0))
    for r in f_rows:
        LOG('    %-5s %-28s 失败 %6d（占失败 %.1f%%，桶内失败率 %.3f）'
            % (r['code'], r['name'], r['n_failed'], 100 * r['pct_of_failures'],
               r['fail_rate_in_bucket']))

    # ══════════════════════════════════════════════════ 元信息
    oos_h5 = {(o, p): next((x for x in oos_rows if x['object'] == o and x['horizon'] == H_MAIN
                            and x['phase'] == p), None)
              for o in ('signal', 'DIFF', 'B4', 'DIFF-B4') for p in PH_NAMES}
    sig_h5 = (next(x for x in oos_rows if x['object'] == 'signal' and x['scope'] == 'ALL'
                   and x['horizon'] == H_MAIN and x['phase'] == 'OOS'))
    g3 = {'tradable_rate': trad_rate, 'threshold': float(PREREG['tradable_min']),
          'pass': bool(trad_rate >= float(PREREG['tradable_min']))}
    psave_meta_side('_meta_p6.json', {
        'p6_built_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'p6_prereg_hash': prereg_hash(), 'p6_derived_hash': derived_hash(),
        'p6_declared_rule': {
            'O1': 'Gate 判定只用原始方向（高暴露->高收益）；反向仅披露',
            'O2_G9': 'OOS: exposure ALL 口径 Rank IC>0 且 t_NW>0 且 Q5-Q1>0',
            'O3_G13': 'OOS: |IC|>=%.2f 且 AUC>=%.2f 且 Prec@10>=%.2f 且 nq=5 单调（原始方向）'
                      % (PREREG['ic_min'], PREREG['auc_min'], PREREG['prec10_min']),
            'O4': '组合口径与 P4 一致（行业Top3 × 成员池Top20%，exe_H）',
            'O5': '成本/尾部用重叠 cohort（h=5）、O2O 日收益、turnover=1/在场cohort数、'
                  '日成本=turnover*bps/1e4（bps 往返）',
            'O7': '参数面 axis A breadth_win / B mom_win / C ind_n / D leader_pct（[O7] 全文）',
            'O8_G14': '主面 12 格增量同号比例 >= %.2f' % PARAM_FRAC,
            'O9_G15': 'Leave-Top5%% 后 DIFF-B4 日增量 > 0',
            'O10_G11': '正年份 >= %.2f 且最大单年贡献 <= %.2f' % (YEAR_POS_FRAC, YEAR_MAX_SHARE),
            'O11_G10': 'OOS 与 IC 双正窗口比例 > %.2f' % WF_POS_FRAC,
            'O12_F49': '失败 = DIFF 选股样本 exe_5<=0；F0-F8 固定优先级归类（数据代理）',
        },
        'p6_G3_tradability': g3,
        'p6_check_main_cell': {'maxdiff_vs_p4': chk_maxdiff, 'overlap_days': chk_ndays,
                               'done': bool(chk_done)},
        'p6_G9_OOS_signal_h5': None if sig_h5 is None else {
            'ic_mean': sig_h5['ic_mean'], 'ic_t_nw': sig_h5['ic_t_nw'],
            'q5_q1': sig_h5['q5_q1'], 'auc': sig_h5['auc'], 'prec10': sig_h5['prec10'],
            'mono_rho': sig_h5['mono_rho']},
        'p6_G13_OOS_discrimination': None if sig_h5 is None else {
            'abs_ic': abs(sig_h5['ic_mean']), 'ic_min': PREREG['ic_min'],
            'auc': sig_h5['auc'], 'auc_reversed': 1.0 - sig_h5['auc'] if np.isfinite(sig_h5['auc']) else np.nan,
            'auc_min': PREREG['auc_min'], 'prec10': sig_h5['prec10'],
            'prec10_min': PREREG['prec10_min'], 'mono_rho': sig_h5['mono_rho']},
        'p6_G10_WF': {'windows': len(wf_h5), 'double_positive': pos_w,
                      'ratio': pos_w / max(len(wf_h5), 1), 'threshold': WF_POS_FRAC},
        'p6_G11_year': {'n_years': len(yr_rows), 'positive': pos_y,
                        'ratio': pos_y / max(len(yr_rows), 1), 'max_share': share,
                        'ratio_min': YEAR_POS_FRAC, 'share_max': YEAR_MAX_SHARE},
        'p6_G12_regime': {'sign_flips': flips, 'max_flip': REG_FLIP_MAX,
                          'detail': [(r['regime'], r['DIFFB4_mean'], r['DIFFB4_t']) for r in rg_rows]},
        'p6_G14_param': {'n_cells': len(main_cells), 'positive': pos_cells,
                         'ratio': pos_cells / max(len(main_cells), 1), 'threshold': PARAM_FRAC},
        'p6_G15_tail': {'leave_top5_diffb4': [g15[0]['mean_ret'], g15[0]['t_nw']]},
        'p6_G16_cost30': {'strategy': 'DIFF-B4', 'bps': BPS_FOCUS,
                          'turnover_daily': net30['turnover_daily'],
                          'gross_mean': net30['gross_mean'], 'net_mean': net30['net_mean'],
                          'net_ann': net30['net_ann'], 'net_t': net30['net_t']},
        'p6_G19_econ': {'threshold': float(PREREG['econ_min_net']),
                        'net_mean_30bp': net30['net_mean'],
                        'pass': bool(net30['net_mean'] >= float(PREREG['econ_min_net']))},
        'p6_rows': {'oos': len(oos_rows), 'wf': len(wf_rows), 'year': len(yr_rows),
                    'regime': len(rg_rows), 'surface': len(surf_rows),
                    'tail': len(tail_rows), 'cost': len(cost_rows),
                    'failure_samples': N},
        'p6_elapsed_sec': round(time.time() - t0_all, 1),
        'p6_note': 'FDR（§46）在 P7 统一汇总（含 P4/P5/P6 全部检验的 p 值）',
    })
    LOG('  [SAVE] _meta_p6.json')
    LOG.sep('=')
    LOG('DONE  P6（总耗时 %.1fs）' % (time.time() - t0_all))


def _max_share(vals):
    v = np.asarray([x if np.isfinite(x) else 0.0 for x in vals], dtype=np.float64)
    s = np.abs(v).sum()
    return float(np.abs(v).max() / s) if s > 0 else np.nan


def _count_sign_flips(vals):
    v = [x for x in vals if np.isfinite(x)]
    return sum(1 for i in range(1, len(v)) if (v[i] > 0) != (v[i - 1] > 0))


if __name__ == '__main__':
    main()
