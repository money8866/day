# -*- coding: utf-8 -*-
"""
H-IND-DIFFUSION-01  P5：Null 模型 / 反事实 / 置换检验 / 多重检验
（STEP 13 / 21 / 22；§26 §27 §45 §46）

隔离声明
    本模块自包含，不 import 任何既有研究模块（TL-01 / H-TREND-PULLBACK / HVT / ARCHIVE）。
    所有口径在此文件冻结，看到数值后不得修改（§54 / §59）。

产出
    null_models.csv      N1-N5 Null 模型 + §27 反事实（观测值 vs 零假设分布）
    permutation.csv      §45 置换检验（n_perm；经验单侧 p / Z-score；多子样本）
    fdr_results.csv      §46 BH-FDR（族内 + 合并池；raw p / 调整后 q）
    data/panel/_meta_p5.json

方法说明（预先冻结）
    1. 置换（§45）：每个交易日内对「股票层扩散暴露」做随机重排（股票集合、日期、样本量
       完全不变），重算当日横截面 Rank IC 与 DIFF 组合收益，得到 n_perm 个零假设均值，
       与观测均值比较得到经验单侧 p 与 Z。同一批逐日置换同时用于 h=3/5/10（不重复抽样，
       不引入择优）；子样本（分期/Regime/年份）由同一批逐日置换值聚合，不增加抽样次数。
    2. N1（Random Industry-Date）：每日在 eligible 行业中随机取 Top3（等价于对行业层扩散
       分值随机重排后取 Top3），行业内仍按 ret_20 取 Top20%（保持动量内部结构）。
    3. N2（Random Within-Industry Stock）：行业内重排暴露 -> 行业内 IC 零分布；组合口径
       零分布由 §45 置换给出（跨截面随机重排对任一行业子集诱导的仍是对该子集的均匀随机
       重排，两者分布等价）。
    4. N3（Momentum-Matched）：与 DIFF 等量的对照组合，逐只从「同日 + 同行业 + 同 ret_20
       十分位」池随机抽取（池 < 5 只时放宽为同日同 ret_20 十分位），有放回。
    5. N4（Industry-Momentum Matched）：每日在 mom_ew_20 同五分位的 eligible 行业中，取
       diffusion_score 处于下半区者为对照，随机抽取后组成 IS 组合。
    6. N5（Breadth Shuffled）：对 4 个扩散原始分量按行业各自做随机循环位移
       （|shift| ∈ [5, NCAL-5]），重算横截面分位与 diffusion_score 后取 Top3。
    7. 反事实（§27）：按 §20 四象限比较「强动量 + 扩散(A)」与「强动量 + 未扩散(B)」。
    8. 组合口径统一为「行业 Top3 × 行业内 Top20%，等权，k 日信息选股、k+1 开盘进场」，
       与 P4 的 §43 口径一致。IS_* = 行业选择口径（行业内按 ret_20 取 Top20%）；
       DIFF = P4 实际组合口径（行业内按 exposure 取 Top20%）。
"""
import os
import time
import math
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore', category=RuntimeWarning,
                        message=r'.*(All-NaN|empty slice|Degrees of freedom|invalid value'
                                r'|divide by zero).*')

from hid_common import (DATA, PREREG, DERIVED, mklog, pload, pload_day, pload_meta,
                        rows_by_code, ic_stats, rank_avg, out_csv, psave_meta_side,
                        PHASES, prereg_hash, derived_hash, bh_fdr, code_grid, HERE)

LOG = mklog('null')
PAN = os.path.join(DATA, 'panel')

HZ = tuple(PREREG['primary_horizons'])        # (3, 5, 10)
N_PERM = int(PREREG['n_perm'])                # 1000
SEED = int(PREREG['seed'])
MIN_XS = 30                                   # 日横截面最小样本
MIN_IND = int(PREREG['ind_n_min'])            # 20
MIN_GRP = 5                                   # 行业内部最小成员数（组合构建用）
TOPK_IND = 3
TOP_FRAC = 0.20
PH_NAMES = ['ALL'] + list(PHASES)
REG_NAMES = ['BEAR', 'NORMAL', 'BULL']
YEARS = list(PREREG['year_report'])
SUBN = ['ALL', 'TRAIN', 'VALID', 'OOS', 'LIVE-LIKE']     # Null 模型报告子样本
RAWL1 = ['dchg_5', 'accel20', 'lb_020', 'rsb_chg_5']     # §11/§12/§14/§18 原始分量


# ══════════════════════════════════════════════════════════════ 统计工具

def p_two(t):
    """双侧正态 p"""
    if not np.isfinite(t):
        return np.nan
    return math.erfc(abs(float(t)) / math.sqrt(2.0))


def p_one_right(t):
    """单侧（H1: 统计量 > 0）正态 p"""
    if not np.isfinite(t):
        return np.nan
    return 0.5 * math.erfc(float(t) / math.sqrt(2.0))


def perm_p(obs, null):
    """经验单侧 p（null >= obs）、Z 与零分布统计量"""
    n = np.asarray(null, dtype=np.float64)
    n = n[np.isfinite(n)]
    if len(n) == 0 or not np.isfinite(obs):
        return np.nan, np.nan, np.nan, np.nan, 0
    p = (1.0 + float((n >= obs).sum())) / (len(n) + 1.0)
    mu, sd = float(n.mean()), float(n.std(ddof=1))
    z = (obs - mu) / sd if sd > 0 else np.nan
    return p, z, mu, sd, len(n)


def is_ret_vec(picks, R):
    """行业选择组合的逐日收益。picks (topk, NCAL) 行业码；R (n_ind, NCAL)"""
    n_ind, NCAL = R.shape
    cols = np.broadcast_to(np.arange(NCAL), picks.shape)
    pc = np.clip(picks, 0, n_ind - 1)
    V = R[pc, cols]
    V = np.where(np.asarray(picks) > 0, V, np.nan)
    with np.errstate(invalid='ignore'):
        return np.nanmean(V, axis=0)


def ind_top_all(g_ok, g_val, topk):
    """向量化 ind_top：一次处理全部日期。返回 (topk, NCAL) 行业码（-1 = 无）"""
    n_ind, NCAL = g_val.shape
    ok = np.isfinite(g_val) & (g_ok > 0.5)
    ok[0, :] = False
    V = np.where(ok, g_val, -np.inf)
    order = np.argsort(-V, axis=0, kind='stable')[:topk]
    valid = np.take_along_axis(ok, order, axis=0)
    return np.where(valid, order, -1).astype(np.int16)


def pct_rank_axis0(A, ok):
    """行业维（axis 0）百分位秩（0~1），仅 ok 且有限者参与；否则 NaN"""
    n_ind, NCAL = A.shape
    use = ok & np.isfinite(A)
    M = np.where(use, A, np.inf)
    order = np.argsort(M, axis=0, kind='stable')
    ranks = np.empty(order.shape, dtype=np.float64)
    base = np.broadcast_to(np.arange(n_ind, dtype=np.float64)[:, None], order.shape)
    np.put_along_axis(ranks, order, base, axis=0)
    cnt = use.sum(0).astype(np.float64)
    with np.errstate(invalid='ignore'):
        pct = ranks / np.maximum(cnt, 1.0)
    return np.where(use, pct, np.nan)


class Acc:
    """按子样本累积观测序列与置换零分布（同一批置换值可跨子样本复用）

    零分布按「逐位置（逐置换索引）有限值」累积：某日某位置为 NaN 时该位置当日不计数，
    不污染其余日期。全部 NaN 的日子（如面板末尾前向收益整体缺失）自然不计入任何位置。
    """

    def __init__(self, names, ncells, day_sub):
        self.names = names
        self.sum = {s: np.zeros(ncells, dtype=np.float64) for s in names}
        self.fin = {s: np.zeros(ncells, dtype=np.float64) for s in names}
        self.cnt = {s: 0 for s in names}
        self.osum = {s: 0.0 for s in names}
        self.ocnt = {s: 0 for s in names}
        self.day_sub = day_sub

    def add(self, k, null_vals, obs_val):
        v = np.asarray(null_vals, dtype=np.float64)
        fin = np.isfinite(v)
        vs = np.where(fin, v, 0.0)
        for s in self.day_sub[k]:
            self.sum[s] += vs
            self.fin[s] += fin
            self.cnt[s] += 1
            if np.isfinite(obs_val):
                self.osum[s] += obs_val
                self.ocnt[s] += 1

    def rows(self, model, stat, horizon, note=''):
        out = []
        for s in self.names:
            if self.cnt[s] == 0:
                continue
            obs = self.osum[s] / self.ocnt[s] if self.ocnt[s] else np.nan
            null = np.where(self.fin[s] > 0,
                            self.sum[s] / np.maximum(self.fin[s], 1.0), np.nan)
            p, z, mu, sd, n = perm_p(obs, null)
            out.append({'null_model': model, 'statistic': stat, 'horizon': horizon,
                        'subset': s, 'obs': obs, 'null_mean': mu, 'null_sd': sd,
                        'p_one_sided': p, 'z': z, 'n_perm': n,
                        'n_obs_days': self.ocnt[s], 'note': note})
        return out


# ══════════════════════════════════════════════════════════════ 主流程

def main():
    t_all = time.time()
    LOG.sep('=')
    LOG('H-IND-DIFFUSION-01  P5 Null/置换/多重检验（STEP13/21/22）  hash=%s derived=%s  %s'
        % (prereg_hash(), derived_hash(), time.strftime('%Y-%m-%d %H:%M:%S')))
    LOG.sep()

    meta = pload_meta()
    NCODES, NCAL = int(meta['NCODES']), int(meta['NCAL'])
    n_l1 = int(meta['n_l1'])
    n_ind = n_l1 + 1
    day = pload_day()
    phc = np.asarray(day['phase']).astype(np.int8)
    regc = np.asarray(day['regime']).astype(np.int8)
    yr = np.asarray(day['year']).astype(np.int32)

    SUBS = {'ALL': np.ones(NCAL, dtype=bool)}
    for i, nm in enumerate(PHASES):
        SUBS[nm] = (phc == i + 1)
    for i, nm in enumerate(REG_NAMES):
        SUBS[nm] = (regc == i)
    for y in YEARS:
        SUBS['Y%d' % y] = (yr == y)
    LOG('  面板 %d 股 × %d 日；行业 %d（L1）' % (NCODES, NCAL, n_l1))
    LOG('  子样本：%s' % {k: int(v.sum()) for k, v in SUBS.items()})

    rng = np.random.default_rng(SEED)

    ind2 = np.asarray(pload('_code_ind_l1'), dtype=np.int32)
    RB = rows_by_code(ind2, NCODES)
    SCOPE = (np.asarray(pload('expo_expo_scope')) == 1)
    expo = np.asarray(pload('expo_expo_ind'), dtype=np.float32)
    ret20 = np.asarray(pload('ret_20'), dtype=np.float32)
    EXE = {h: np.asarray(pload('exe_%d' % h), dtype=np.float32) for h in HZ}

    fin_fwd = np.ones((NCODES, NCAL), dtype=bool)
    for h in HZ:
        fin_fwd &= np.isfinite(EXE[h])
    CAND = SCOPE & np.isfinite(ret20) & np.isfinite(expo)
    BASE = CAND & fin_fwd
    LOG('  CAND = %d 股票日；BASE（CAND ∩ 三 horizon 前向可得）= %d（%.4f）'
        % (int(CAND.sum()), int(BASE.sum()), BASE.sum() / max(1, int(CAND.sum()))))

    # ──────────────────────────────────────────── P2 行业层网格
    st = pd.read_parquet(os.path.join(HERE, 'industry_daily_state.parquet'))
    ft = pd.read_parquet(os.path.join(HERE, 'industry_diffusion_features.parquet'))
    st = st[st['ind_level'] == 'L1']
    ft = ft[ft['ind_level'] == 'L1']
    g_el = code_grid(st, 'eligible', n_l1, NCAL)
    g_n = code_grid(st, 'n_used', n_l1, NCAL)
    g_mom = code_grid(st, 'mom_ew_20', n_l1, NCAL)
    g_diff = code_grid(ft, 'diffusion_score', n_l1, NCAL)
    g_raw = {c: code_grid(st, c, n_l1, NCAL) for c in RAWL1}
    ELB = (g_el > 0.5)
    OKN = ELB & (g_n >= MIN_IND)

    QC = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
    g_q = np.full((n_ind, NCAL), np.nan)
    qv = st['quadrant'].map(QC)
    okq = qv.notna().values
    g_q[st['ind_code'].values[okq], st['k'].values[okq]] = qv.values[okq]

    TOPD = ind_top_all(OKN, g_diff, TOPK_IND)               # 扩散选行业
    TOPM = ind_top_all(OKN, g_mom, TOPK_IND)                # 动量选行业（对照）
    LOG('  行业网格就绪：eligible 行业日 %d，OKN（N>=%d）%d，扩散 Top3 有效日 %d'
        % (int(ELB.sum()), MIN_IND, int(OKN.sum()), int((TOPD[0] > 0).sum())))

    # ──────────────────────────────────────────── 逐 (日, 行业) 收益表
    LOG.sep('─')
    LOG('[STEP13-A] 预计算逐 (日, 行业) 收益表：R_all / R_mom（行业内 Top%.0f%% by ret_20）'
        % (TOP_FRAC * 100))
    t0 = time.time()
    R_all = {h: np.full((n_ind, NCAL), np.nan) for h in HZ}
    R_mom = {h: np.full((n_ind, NCAL), np.nan) for h in HZ}
    for c, rows in RB.items():
        sub = CAND[rows, :] & (ind2[rows, :] == c)
        if not sub.any():
            continue
        V = {h: EXE[h][rows, :] for h in HZ}
        R20 = ret20[rows, :]
        for k in range(NCAL):
            pos = np.nonzero(sub[:, k])[0]
            nn = len(pos)
            if nn == 0:
                continue
            ntop = max(1, int(np.ceil(TOP_FRAC * nn)))
            take = pos[np.argsort(-R20[pos, k], kind='stable')[:ntop]] if nn > 1 else pos
            for h in HZ:
                v = np.asarray(V[h][pos, k], dtype=np.float64)
                f = np.isfinite(v)
                if f.any():
                    R_all[h][c, k] = v[f].mean()
                vt = np.asarray(V[h][take, k], dtype=np.float64)
                ft2 = np.isfinite(vt)
                if ft2.any():
                    R_mom[h][c, k] = vt[ft2].mean()
        del V, R20, sub
    LOG('  [DONE] 收益表（%.1fs）' % (time.time() - t0))

    IS_DIFF = {h: is_ret_vec(TOPD, R_mom[h]) for h in HZ}
    IS_MOM = {h: is_ret_vec(TOPM, R_mom[h]) for h in HZ}
    for h in HZ:
        stt = ic_stats(IS_DIFF[h][SUBS['ALL']], lag=h)
        stm = ic_stats(IS_MOM[h][SUBS['ALL']], lag=h)
        LOG('  IS_DIFF h=%-2d mean=%+.5f t_NW=%+.2f | IS_MOM mean=%+.5f t_NW=%+.2f'
            % (h, stt['mean'], stt['t_nw'], stm['mean'], stm['t_nw']))

    strat = np.load(os.path.join(PAN, '_strat_p4.npz'))
    for h in HZ:
        v = np.asarray(strat['DIFF_h%d' % h], dtype=np.float64)
        stt = ic_stats(v[SUBS['ALL']], lag=h)
        LOG('  DIFF(P4) h=%-2d mean=%+.5f t_NW=%+.2f hold=%.1f'
            % (h, stt['mean'], stt['t_nw'], float(np.nanmean(strat['hold_DIFF']))))

    day_sub = {k: [s for s in SUBN if SUBS[s][k]] for k in range(NCAL)}
    day_sub_all = {k: [s for s in SUBS if SUBS[s][k]] for k in range(NCAL)}

    null_rows = []
    perm_rows = []

    # ════════════════════════════════════════════ §45 置换（日内跨截面重排暴露）
    LOG.sep('─')
    LOG('[STEP21] §45 置换检验：日内跨截面重排股票层暴露（n_perm=%d）' % N_PERM)
    t0 = time.time()
    acc_ic = {h: Acc(list(SUBS), N_PERM, day_sub_all) for h in HZ}
    acc_pf = {h: Acc(list(SUBS), N_PERM, day_sub_all) for h in HZ}
    obs_ic = {h: np.full(NCAL, np.nan) for h in HZ}
    obs_pf = {h: np.full(NCAL, np.nan) for h in HZ}
    n_day_perm = 0
    for k in range(NCAL):
        m = BASE[:, k]
        n = int(m.sum())
        if n < MIN_XS:
            continue
        idx = np.nonzero(m)[0]
        n_day_perm += 1
        a = rank_avg(np.asarray(expo[idx, k], dtype=np.float64))
        a = a - a.mean()
        na = math.sqrt(float((a * a).sum()))
        posmap = np.full(NCODES, -1, dtype=np.int64)
        posmap[idx] = np.arange(n)
        members = []
        for c in TOPD[:, k]:
            if c <= 0:
                continue
            rr = RB[int(c)]
            sel = BASE[rr, k] & (ind2[rr, k] == c)
            gr = rr[sel]
            if len(gr) == 0:
                continue
            eo = np.argsort(-np.asarray(expo[gr, k], dtype=np.float64), kind='stable')
            ntop = max(1, int(np.ceil(TOP_FRAC * len(gr))))
            eot = np.sort(eo[:ntop])
            pv = posmap[gr]
            okp = pv >= 0
            members.append((pv[okp], gr[okp], eot))
            # 注意：gr 与 pv 已按 gr 原序对齐；eot 为其上的下标
        if not members:
            continue
        Rm = rng.random((N_PERM, n), dtype=np.float32)
        perm = np.argsort(Rm, axis=1)
        del Rm
        ap = a[perm]
        for h in HZ:
            b = rank_avg(np.asarray(EXE[h][idx, k], dtype=np.float64))
            b = b - b.mean()
            den = na * math.sqrt(float((b * b).sum()))
            if den <= 0:
                continue
            ic_obs = float((a @ b) / den)
            acc_ic[h].add(k, (ap @ b) / den, ic_obs)
            obs_ic[h][k] = ic_obs
            s_sum = np.zeros(N_PERM)
            s_cnt = np.zeros(N_PERM, dtype=np.int64)
            for (pv, gr, eot) in members:
                ktop = len(eot)
                pp = perm[:, pv]
                if ktop < pp.shape[1]:
                    tk = np.argpartition(-pp, ktop - 1, axis=1)[:, :ktop]
                else:
                    tk = np.broadcast_to(np.arange(pp.shape[1]), pp.shape)
                vv = np.asarray(EXE[h][gr[tk], k], dtype=np.float64)
                mm = np.nanmean(vv, axis=1)
                okv = np.isfinite(mm)
                s_sum[okv] += mm[okv]
                s_cnt[okv] += 1
            pf_perm = np.where(s_cnt > 0, s_sum / np.maximum(s_cnt, 1), np.nan)
            sel_all = np.concatenate([gr[eot] for (_, gr, eot) in members])
            vo = np.asarray(EXE[h][sel_all, k], dtype=np.float64)
            vo = vo[np.isfinite(vo)]
            pf_obs = float(vo.mean()) if len(vo) else np.nan
            obs_pf[h][k] = pf_obs
            acc_pf[h].add(k, pf_perm, pf_obs)
        del ap, perm
    LOG('  有效置换日 %d（%.1fs）' % (n_day_perm, time.time() - t0))

    for h in HZ:
        perm_rows += acc_ic[h].rows('PERM_cross_sectional', 'IC_exposure', h,
                                    '§45 日内跨截面随机重排暴露')
        perm_rows += acc_pf[h].rows('PERM_cross_sectional', 'DIFF_portfolio', h,
                                    '§45 重排暴露后 DIFF 组合（行业内 Top20%）')
    for h in HZ:
        stt = ic_stats(obs_ic[h][SUBS['ALL']], lag=h)
        pts = ic_stats(obs_pf[h][SUBS['ALL']], lag=h)
        LOG('  观测 IC_exposure h=%-2d mean=%+.4f t=%+.2f | DIFF_portfolio mean=%+.5f'
            % (h, stt['mean'], stt['t_nw'], pts['mean']))
    # 组合口径置换 = N2 的组合读数
    for r in [x for x in perm_rows if x['statistic'] == 'DIFF_portfolio' and x['subset'] in SUBN]:
        null_rows.append(dict(r, null_model='N2_WithinIndustryStockPortfolio',
                              note='§45 重排暴露后 DIFF 组合（对成员行业子集等价于均匀随机 20%）'))

    # ════════════════════════════════════════════ N1 随机行业-日期
    LOG.sep('─')
    LOG('[STEP13] N1 Random Industry-Date：每日随机取 Top3 eligible 行业（动量内部）')
    t0 = time.time()
    acc = {h: Acc(SUBN, N_PERM, day_sub) for h in HZ}
    for k in range(NCAL):
        ok = OKN[:, k].copy()
        ok[0] = False
        cand = np.nonzero(ok)[0]
        if len(cand) < TOPK_IND:
            continue
        pick = cand[np.argsort(rng.random((N_PERM, len(cand))), axis=1)[:, :TOPK_IND]]
        for h in HZ:
            V = R_mom[h][pick, np.full((N_PERM, TOPK_IND), k, dtype=np.int64)]
            with np.errstate(invalid='ignore'):
                acc[h].add(k, np.nanmean(V, axis=1), IS_DIFF[h][k])
    for h in HZ:
        null_rows += acc[h].rows('N1_RandomIndustryDate', 'IS_DIFF_return', h,
                                 '随机 3 行业 + 行业内 Top20% by ret_20；观测=扩散选行业')
    LOG('  [DONE]（%.1fs）' % (time.time() - t0))

    # ════════════════════════════════════════════ N4 行业动量匹配
    LOG.sep('─')
    LOG('[STEP13] N4 Industry-Momentum Matched：同 mom 五分位内 diffusion 下半区为对照')
    t0 = time.time()
    acc = {h: Acc(SUBN, N_PERM, day_sub) for h in HZ}
    for k in range(NCAL):
        ok = OKN[:, k].copy()
        ok[0] = False
        cand = np.nonzero(ok)[0]
        if len(cand) < 6:
            continue
        q = np.minimum(4, np.floor(rank_avg(g_mom[cand, k]) / len(cand) * 5).astype(np.int64))
        half = np.nanmedian(g_diff[cand, k])
        picks = [int(c) for c in TOPD[:, k] if c > 0]
        pools = []
        for c in picks:
            w = np.nonzero(cand == c)[0]
            if len(w) == 0:
                continue
            qq = q[w[0]]
            pool = cand[(q == qq) & (cand != c)]
            if len(pool) and np.isfinite(half):
                low = pool[g_diff[pool, k] <= half]
                if len(low):
                    pool = low
            if len(pool) == 0:
                pool = cand[cand != c]
            if len(pool):
                pools.append(pool)
        if not pools:
            continue
        for h in HZ:
            vals = np.zeros(N_PERM)
            for pl in pools:
                vals += np.asarray(R_mom[h][pl[rng.integers(0, len(pl), N_PERM)], k],
                                   dtype=np.float64)
            vals /= len(pools)
            acc[h].add(k, vals, IS_DIFF[h][k])
    for h in HZ:
        null_rows += acc[h].rows('N4_IndustryMomentumMatched', 'IS_DIFF_return', h,
                                 '行业动量同五分位 + diffusion 下半区随机对照')
    LOG('  [DONE]（%.1fs）' % (time.time() - t0))

    # ════════════════════════════════════════════ N5 扩散分量时间重排
    LOG.sep('─')
    LOG('[STEP13] N5 Breadth Shuffled：4 个扩散分量按行业随机循环位移后重算 Top3')
    t0 = time.time()
    ar = np.arange(NCAL, dtype=np.int64)
    sc0 = np.zeros((n_ind, NCAL))
    for c in RAWL1:
        sc0 += pct_rank_axis0(g_raw[c], ELB)
    sc0 /= len(RAWL1)
    d = np.abs(sc0 - g_diff)
    LOG('    [CHECK] 分量复算 score vs P2 diffusion_score：max|diff|=%.3e（NaN 位置不一致 %d）'
        % (np.nanmax(d), int((np.isnan(sc0) != np.isnan(g_diff)).sum())))
    IS_perm5 = {h: np.full((N_PERM, NCAL), np.nan) for h in HZ}
    for p in range(N_PERM):
        sgn = rng.choice([-1, 1], size=n_ind)
        sh = sgn * rng.integers(5, NCAL - 4, size=n_ind)
        sh[0] = 0
        idx_sh = (ar[None, :] + sh[:, None]) % NCAL
        sc = np.zeros((n_ind, NCAL))
        for c in RAWL1:
            sc += pct_rank_axis0(np.take_along_axis(g_raw[c], idx_sh, axis=1), ELB)
        sc /= len(RAWL1)
        picks = ind_top_all(ELB, sc, TOPK_IND)
        for h in HZ:
            IS_perm5[h][p, :] = is_ret_vec(picks, R_mom[h])
    for h in HZ:
        for s in SUBN:
            mm = SUBS[s]
            if not mm.any():
                continue
            obs = float(np.nanmean(IS_DIFF[h][mm]))
            null = np.nanmean(IS_perm5[h][:, mm], axis=1)
            p_, z, mu, sd, n = perm_p(obs, null)
            null_rows.append({'null_model': 'N5_BreadthShuffled', 'statistic': 'IS_DIFF_return',
                              'horizon': h, 'subset': s, 'obs': obs, 'null_mean': mu,
                              'null_sd': sd, 'p_one_sided': p_, 'z': z, 'n_perm': n,
                              'n_obs_days': int(mm.sum()),
                              'note': '4 分量按行业随机循环位移；观测=未位移扩散选行业'})
        stt = ic_stats(IS_DIFF[h][SUBS['ALL']], lag=h)
        LOG('    h=%-2d IS_DIFF=%+.5f | N5 null=%+.5f p=%.3f'
            % (h, stt['mean'], float(np.nanmean(IS_perm5[h][:, SUBS['ALL']])),
               [x['p_one_sided'] for x in null_rows if x['null_model'] == 'N5_BreadthShuffled'
                and x['horizon'] == h and x['subset'] == 'ALL'][0]))
    LOG('  [DONE]（%.1fs）' % (time.time() - t0))

    # ════════════════════════════════════════════ N3 股票动量匹配
    LOG.sep('─')
    LOG('[STEP13] N3 Momentum-Matched：同日+同行业+同 ret_20 十分位 随机对照')
    t0 = time.time()
    acc = {h: Acc(SUBN, N_PERM, day_sub) for h in HZ}
    selN = np.asarray(strat['sel_DIFF'])
    for k in range(NCAL):
        m = BASE[:, k]
        if int(m.sum()) < MIN_XS:
            continue
        idx = np.nonzero(m)[0]
        n = len(idx)
        dec = np.minimum(9, np.floor(rank_avg(np.asarray(ret20[idx, k], dtype=np.float64))
                                      / n * 10).astype(np.int64))
        sel = (selN[:, k] > 0) & m
        if not sel.any():
            continue
        sidx = np.nonzero(sel)[0]
        pools = []
        for i in sidx:
            j = int(np.searchsorted(idx, i))
            c = int(ind2[i, k])
            pool = idx[(dec == dec[j]) & (ind2[idx, k] == c) & (idx != i)]
            if len(pool) < MIN_GRP:
                pool = idx[(dec == dec[j]) & (idx != i)]
            if len(pool) < MIN_GRP:
                pool = idx[(ind2[idx, k] == c) & (idx != i)]
            if len(pool) == 0:
                pool = idx[idx != i]
            pools.append(pool)
        for h in HZ:
            vals = np.zeros(N_PERM)
            for pl in pools:
                vals += np.asarray(EXE[h][pl[rng.integers(0, len(pl), N_PERM)], k],
                                   dtype=np.float64)
            vals /= len(pools)
            vo = np.asarray(EXE[h][sidx, k], dtype=np.float64)
            vo = vo[np.isfinite(vo)]
            acc[h].add(k, vals, float(vo.mean()) if len(vo) else np.nan)
    for h in HZ:
        null_rows += acc[h].rows('N3_MomentumMatched', 'DIFF_return', h,
                                 '观测=DIFF 组合；零分布=动量匹配随机组合（有放回）')
    LOG('  [DONE]（%.1fs）' % (time.time() - t0))

    # ════════════════════════════════════════════ N2 行业内重排 -> 行业内 IC
    LOG.sep('─')
    LOG('[STEP13] N2 Random Within-Industry Stock：行业内重排暴露 -> 行业内 IC 零分布')
    t0 = time.time()
    HP = 5
    accw = Acc(list(SUBS), N_PERM, day_sub_all)
    for k in range(NCAL):
        m = BASE[:, k]
        if int(m.sum()) < MIN_XS:
            continue
        idx = np.nonzero(m)[0]
        y = np.asarray(EXE[HP][idx, k], dtype=np.float64)
        x = np.asarray(expo[idx, k], dtype=np.float64)
        acc_null = np.zeros(N_PERM)
        acc_obs = 0.0
        nind = 0
        for c in range(1, n_ind):
            pp = np.nonzero(ind2[idx, k] == c)[0]
            if len(pp) < MIN_IND:
                continue
            a = rank_avg(x[pp]); a = a - a.mean()
            b = rank_avg(y[pp]); b = b - b.mean()
            den = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
            if den <= 0:
                continue
            acc_obs += float((a @ b) / den)
            nind += 1
            permw = np.argsort(rng.random((N_PERM, len(pp)), dtype=np.float32), axis=1)
            acc_null += (a[permw] @ b) / den
            del permw
        if nind == 0:
            continue
        accw.add(k, acc_null / nind, acc_obs / nind)
    null_rows += accw.rows('N2_RandomWithinIndustryStock', 'within_industry_IC', HP,
                           '行业内暴露重排；观测=行业内 IC 均值（§23 行业内口径）')
    LOG('  [DONE]（%.1fs）' % (time.time() - t0))

    # ════════════════════════════════════════════ §27 反事实：四象限
    LOG.sep('─')
    LOG('[STEP13] §27 反事实：强动量+扩散(A) vs 强动量+未扩散(B)')
    t0 = time.time()
    for h in HZ:
        STQ = {q: np.full(NCAL, np.nan) for q in QC}
        for k in range(NCAL):
            vals = {q: [] for q in QC}
            for c in TOPD[:, k]:
                if c <= 0:
                    continue
                qq = g_q[int(c), k]
                v = R_mom[h][int(c), k]
                if np.isfinite(qq) and np.isfinite(v):
                    vals['ABCD'[int(qq)]].append(v)
            for q in QC:
                if vals[q]:
                    STQ[q][k] = float(np.mean(vals[q]))
        for q in QC:
            stt = ic_stats(STQ[q][SUBS['ALL']], lag=h)
            null_rows.append({'null_model': 'CF27_Quadrant_%s' % q,
                              'statistic': 'selected_industry_return', 'horizon': h,
                              'subset': 'ALL', 'obs': stt['mean'], 'null_mean': np.nan,
                              'null_sd': np.nan, 'p_one_sided': p_one_right(stt['t_nw']),
                              'z': stt['t_nw'], 'n_perm': 0, 'n_obs_days': stt['n'],
                              'note': 'A=Mom↑Breadth↑ B=Mom↑Breadth↓ C=Mom↓Breadth↑ D=Mom↓Breadth↓'})
        ma = np.isfinite(STQ['A']) & np.isfinite(STQ['B'])
        stt = ic_stats((STQ['A'] - STQ['B'])[ma], lag=h)
        null_rows.append({'null_model': 'CF27_A_minus_B',
                          'statistic': 'diff_selected_industry_return', 'horizon': h,
                          'subset': 'ALL', 'obs': stt['mean'], 'null_mean': np.nan,
                          'null_sd': stt['std'], 'p_one_sided': p_one_right(stt['t_nw']),
                          'z': stt['t_nw'], 'n_perm': 0, 'n_obs_days': stt['n'],
                          'note': '§27 强动量+扩散 − 强动量+未扩散'})
        LOG('    h=%-2d A=%+.5f B=%+.5f C=%+.5f D=%+.5f | A-B=%+.5f t_NW=%+.2f'
            % (h, np.nanmean(STQ['A']), np.nanmean(STQ['B']), np.nanmean(STQ['C']),
               np.nanmean(STQ['D']), stt['mean'], stt['t_nw']))
    LOG('  [DONE]（%.1fs）' % (time.time() - t0))

    # ════════════════════════════════════════════ 落盘
    out_csv('null_models.csv', pd.DataFrame(null_rows))
    out_csv('permutation.csv', pd.DataFrame(perm_rows))
    LOG.sep('─')
    LOG('[SAVE] null_models.csv rows=%d；permutation.csv rows=%d'
        % (len(null_rows), len(perm_rows)))
    for nm in ('N1_RandomIndustryDate', 'N4_IndustryMomentumMatched', 'N5_BreadthShuffled',
               'N3_MomentumMatched', 'N2_RandomWithinIndustryStock'):
        r = [x for x in null_rows if x['null_model'] == nm and x['horizon'] == 5
             and x['subset'] == 'ALL']
        if r:
            LOG('    %-32s h5 ALL obs=%+.5f null=%+.5f p=%.3f z=%+.2f'
                % (nm, r[0]['obs'], r[0]['null_mean'], r[0]['p_one_sided'], r[0]['z']))
    for r in [x for x in perm_rows if x['statistic'] == 'IC_exposure'
              and x['subset'] in ('ALL', 'OOS')]:
        LOG('    [PERM] IC_exposure %-5s h=%-2d obs=%+.4f null=%+.4f p=%.4f z=%+.2f'
            % (r['subset'], r['horizon'], r['obs'], r['null_mean'], r['p_one_sided'], r['z']))

    # ════════════════════════════════════════════ §46 BH-FDR
    collect_fdr()

    psave_meta_side('_meta_p5.json', {
        'p5_built_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'p5_prereg_hash': prereg_hash(), 'p5_derived_hash': derived_hash(),
        'p5_n_perm': N_PERM, 'p5_seed': SEED,
        'p5_n_base': int(BASE.sum()), 'p5_n_cand': int(CAND.sum()),
        'p5_perm_days': n_day_perm,
        'p5_rows': {'null': len(null_rows), 'perm': len(perm_rows)},
        'p5_elapsed_sec': round(time.time() - t_all, 1),
    })
    LOG.sep('=')
    LOG('DONE  P5  （总耗时 %.1fs）' % (time.time() - t_all))


# ══════════════════════════════════════════════════════════════ §46 多重检验

def collect_fdr(extra_note=''):
    """汇总当前已产出的全部统计量 -> BH-FDR（族内 + 合并池）。

    P7 在 P6 产出 parameter_surface / tail / cost 等文件后可再次调用本函数补全族。
    """
    rows = []

    def _add(family, test, stat, p, direction, src):
        if np.isfinite(p):
            rows.append({'family': family, 'test': test, 'statistic': stat,
                         'raw_p': float(p), 'direction': direction, 'source': src})

    fp = os.path.join(HERE, 'ic_results.csv')
    if os.path.exists(fp):
        d = pd.read_csv(fp)
        for _, r in d[(d['phase'] == 'ALL') & (d['horizon'] == 5)].iterrows():
            _add('feature', '%s|h5|ALL' % r['signal'], 'mean_ic', p_two(r['t_nw']),
                 'two-sided', 'ic_results.csv')
        d2 = d[d['signal'].isin(['exposure', 'expo_rs', 'ind_diffusion_score'])
               & d['phase'].isin(['ALL', 'OOS'])]
        for _, r in d2.iterrows():
            _add('horizon', '%s|h%d|%s' % (r['signal'], r['horizon'], r['phase']),
                 'mean_ic', p_two(r['t_nw']), 'two-sided', 'ic_results.csv')
    fp = os.path.join(HERE, 'auc_results.csv')
    if os.path.exists(fp):
        d = pd.read_csv(fp)
        d = d[(d['signal'].isin(['exposure', 'expo_rs', 'ind_diffusion_score']))
              & (d['label'] == 'gt_ind_median') & d['phase'].isin(['ALL', 'OOS'])]
        for _, r in d.iterrows():
            _add('discrimination', '%s|h%d|%s' % (r['signal'], r['horizon'], r['phase']),
                 'auc', p_two(r['t_nw_vs_50']), 'two-sided', 'auc_results.csv')
    fp = os.path.join(HERE, 'model_ladder.csv')
    if os.path.exists(fp):
        d = pd.read_csv(fp)
        d = d[(((d['model'] == 'M2') & (d['term'] == 'ind_breadth'))
               | ((d['model'] == 'M3') & (d['term'] == 'diffusion')))
              & d['phase'].isin(['ALL', 'OOS'])]
        for _, r in d.iterrows():
            _add('model', '%s.%s|%s|h%d|%s' % (r['model'], r['term'], r['sample'],
                                               r['horizon'], r['phase']),
                 'beta', p_one_right(r['t_nw']), 'one-sided(+)', 'model_ladder.csv')
    fp = os.path.join(HERE, 'null_models.csv')
    if os.path.exists(fp):
        d = pd.read_csv(fp)
        for _, r in d[d['null_model'].str.startswith('N')].iterrows():
            _add('null', '%s|%s|h%d|%s' % (r['null_model'], r['statistic'],
                                           r['horizon'], r['subset']),
                 'obs_vs_null', r['p_one_sided'], 'one-sided(+)', 'null_models.csv')
    fp = os.path.join(HERE, 'permutation.csv')
    if os.path.exists(fp):
        d = pd.read_csv(fp)
        for _, r in d.iterrows():
            _add('permutation', '%s|h%d|%s' % (r['statistic'], r['horizon'], r['subset']),
                 'obs_vs_perm', r['p_one_sided'], 'one-sided(+)', 'permutation.csv')
    fp = os.path.join(HERE, 'regime_results.csv')
    if os.path.exists(fp):
        d = pd.read_csv(fp)
        for _, r in d.iterrows():
            if 't_nw' in d.columns:
                _add('regime', '%s|h%s|%s' % (r.get('signal'), r.get('horizon'),
                                              r.get('regime')), 'mean',
                     p_two(r['t_nw']), 'two-sided', 'regime_results.csv')
    fp = os.path.join(HERE, 'parameter_surface.csv')
    if os.path.exists(fp):
        d = pd.read_csv(fp)
        for _, r in d.iterrows():
            if 't_nw' in d.columns:
                _add('window', '%s|h%s|%s' % (r.get('signal'), r.get('horizon'),
                                              r.get('param')), 'mean',
                     p_two(r['t_nw']), 'two-sided', 'parameter_surface.csv')

    if not rows:
        return None
    df = pd.DataFrame(rows)
    df['q_bh_within'] = np.nan
    df['reject_within'] = False
    for fam, g in df.groupby('family'):
        rej, q = bh_fdr(g['raw_p'].values, alpha=float(PREREG['fdr_alpha']))
        df.loc[g.index, 'q_bh_within'] = q
        df.loc[g.index, 'reject_within'] = rej
    rej, q = bh_fdr(df['raw_p'].values, alpha=float(PREREG['fdr_alpha']))
    df['q_bh_pooled'] = q
    df['reject_pooled'] = rej
    df['note'] = extra_note
    df = df.sort_values(['family', 'raw_p']).reset_index(drop=True)
    out_csv('fdr_results.csv', df)
    LOG.sep('─')
    LOG('[STEP22] §46 BH-FDR（alpha=%.2f）：%d 项检验；族内显著 %d，合并池显著 %d'
        % (PREREG['fdr_alpha'], len(df), int(df['reject_within'].sum()),
           int(df['reject_pooled'].sum())))
    for fam, g in df.groupby('family'):
        LOG('    %-14s n=%-4d raw_p<.05: %-4d 族内 BH 显著: %d'
            % (fam, len(g), int((g['raw_p'] < 0.05).sum()), int(g['reject_within'].sum())))
    return df


if __name__ == '__main__':
    main()
