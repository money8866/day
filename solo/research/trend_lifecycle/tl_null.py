# -*- coding: utf-8 -*-
"""TL-01 §35 Null Model / §36 Permutation Test / §37 Multiple Testing

为什么可以做到 1000 次置换而仍然很快：
  置换检验的对象是「状态转换 → 未来收益」这条链路，而信号 SIG_TRANS 在同一
  (state_prev, state) 单元内取值恒定。因此逐日横截面 Spearman IC 可以完全由
  单元的「成员数」与「成员 y 平均秩之和」两张表重建：

    avg_rank_c = start_c + (n_c - 1)/2 ,  start_c = 1 + Σ_{c': v_c' < v_c} n_c'
    Σ_i rank_x(i)·rank_y(i) = Σ_c avg_rank_c · S_c

  于是每一次置换只需对 121 个单元的值排序 + 两次矩阵点积，无需重排 860 万行。
  该推导给出的是**精确**的 Spearman IC（含并列的平均秩处理），不是近似。

零假设族（§35）：
  N1 随机股票   —— 逐日对信号做随机置换（等价于日内随机选股）
  N2 随机日期   —— 把信号面板整体平移 d 个交易日（破坏时间对齐）
  N3 随机状态   —— 随机重排 121 个单元的条件期望值（保持信号边缘分布）
  N4 Momentum   —— 现实基准 B1_MOM20（非随机，作为参照上限）
  N5 相对强度   —— 现实基准 B5_RS20

判定（§51）：若 Lifecycle 的 IC 落入零假设分布之内（|z| < null_z_pass），
则视为「Random Null 可复制」→ STOP 条件之一成立。

产出：
  tl01_null_model.csv    N1–N5 汇总
  tl01_permutation.csv   §36 1000 次置换的实证 p 值与 §37 BH-FDR 校正
  _tl_null_run.txt
"""
import os
import time
import numpy as np
import pandas as pd

from tl_common import (HERE, PREREG, Log, panel_meta, pget, ic_stats, phase_of,
                       daily_ic, rank_avg, bh_fdr)

HZ = list(PREREG['horizons'])
PRIMARY = 20
NC = 121
NPERM = int(PREREG['n_perm'])
SEED = int(PREREG['seed'])
ZS = float(PREREG['null_z_pass'])


def fit_trans_map(frm, to, y, valid, min_n=30):
    ps = np.zeros((11, 11))
    cn = np.zeros((11, 11))
    m = valid & (frm >= 0) & (to >= 0) & np.isfinite(y)
    np.add.at(ps, (frm[m], to[m]), y[m].astype(np.float64))
    np.add.at(cn, (frm[m], to[m]), 1.0)
    mp = np.where(cn >= min_n, ps / np.maximum(cn, 1.0), np.nan)
    s1 = np.zeros(11)
    c1 = np.zeros(11)
    m2 = valid & (to >= 0) & np.isfinite(y)
    np.add.at(s1, to[m2], y[m2].astype(np.float64))
    np.add.at(c1, to[m2], 1.0)
    stm = np.where(c1 > 0, s1 / np.maximum(c1, 1.0), np.nan)
    gm = float(np.mean(y[m2])) if m2.sum() else 0.0
    for a in range(11):
        for b in range(11):
            if not np.isfinite(mp[a, b]):
                mp[a, b] = stm[b] if np.isfinite(stm[b]) else gm
    return mp


def main():
    t0 = time.time()
    log = Log('_tl_null_run.txt')
    log('=' * 78)
    log('TL-01 §35 Null Model / §36 Permutation Test（%d 次）' % NPERM)

    meta = panel_meta()
    NCAL, NCODE = meta['ncal'], meta['ncode']
    ph_d = phase_of(np.array([int(d[:4]) if d else 0
                              for d in meta['dates']], dtype=np.int32))
    oos = ph_d == 'OOS'
    ST = np.asarray(pget('state'))
    SP = np.asarray(pget('state_prev'))
    VAL = ST >= 0
    cell = np.where((SP >= 0) & (ST >= 0), SP * 11 + ST, -1).astype(np.int16)
    use = VAL & (cell >= 0)
    kd = np.broadcast_to(np.arange(NCAL)[:, None], cell.shape)
    kused = kd[use]
    cused = cell[use]

    train_day = np.broadcast_to((ph_d == 'TRAIN')[:, None], ST.shape)
    mp = fit_trans_map(SP[VAL], ST[VAL], np.asarray(pget('fwd_%d' % PRIMARY))[VAL],
                       train_day[VAL])

    # ---------------- 逐日单元级聚合（各 horizon） ----------------
    tab = {}
    sqtab = {}
    for h in HZ:
        Y = np.asarray(pget('fwd_%d' % h))
        ryflat = np.full(NCAL * NCODE, np.nan)
        s2 = np.full(NCAL, np.nan)
        for k in range(NCAL):
            v = VAL[k]
            if not v.any():
                continue
            y = Y[k]
            m = v & np.isfinite(y) & (cell[k] >= 0)
            if m.sum() < 50:
                continue
            idx = np.flatnonzero(m)
            r = rank_avg(y[m])
            ryflat[k * NCODE + idx] = r
            s2[k] = float((r ** 2).sum())
        ry = ryflat.reshape(NCAL, NCODE)
        nd = np.zeros((NCAL, NC), dtype=np.float64)
        Sd = np.zeros((NCAL, NC), dtype=np.float64)
        ryv = ry[use]
        good = np.isfinite(ryv)
        np.add.at(nd, (kused[good], cused[good]), 1.0)
        np.add.at(Sd, (kused[good], cused[good]), ryv[good])
        Nk = nd.sum(1)
        Mrk = np.divide(Sd.sum(1), Nk, out=np.full(NCAL, np.nan), where=Nk > 0)
        tab[h] = dict(nd=nd, Sd=Sd, N=Nk, mry=Mrk)
        sqtab[h] = s2
        log('  单元聚合 T+%d 完成  有效日 %d  %.0fs'
            % (h, int((Nk > 0).sum()), time.time() - t0))
        del Y, ryflat, ry, ryv

    def ic_series(vals, h, days):
        T = tab[h]
        ndc = T['nd'][days]
        Sdc = T['Sd'][days]
        N = T['N'][days]
        mry = T['mry'][days]
        order = np.argsort(vals)
        nds = ndc[:, order]
        Sds = Sdc[:, order]
        cum = np.cumsum(nds, axis=1)
        start = cum - nds + 1.0
        avg = start + (nds - 1.0) / 2.0
        numer = (avg * Sds).sum(1)
        mr = (N + 1.0) / 2.0
        varx = (nds * (avg - mr[:, None]) ** 2).sum(1) / N
        very = sqtab[h][days] / N - mry ** 2
        cov = numer / N - mr * mry
        with np.errstate(invalid='ignore', divide='ignore'):
            return cov / np.sqrt(varx * very)

    # ---------------- 真实信号（同口径） ----------------
    vals_real = mp.reshape(-1).copy()
    if len(np.unique(vals_real)) < NC:      # 极小概率并列 -> 加微小抖动
        vals_real = vals_real + np.arange(NC) * 1e-12
    real_ic = {}
    for h in HZ:
        s = ic_series(vals_real, h, np.flatnonzero(tab[h]['N'] > 0))
        days = np.flatnonzero(tab[h]['N'] > 0)
        rec = ic_stats(s, h)
        real_ic[h] = dict(mean=rec['mean_ic'], n=rec['n'], icir=rec['icir'],
                          t=rec['ic_t'], s=s, days=days)
        log('  真实 SIG_TRANS T+%d  全样本 IC=%+.4f (ICIR %.3f, t=%.2f, n=%d)'
            % (h, rec['mean_ic'], rec['icir'], rec['ic_t'], rec['n']))

    # ---------------- §36 置换检验（N3） ----------------
    rng = np.random.default_rng(SEED)
    perm_stat = {h: np.zeros(NPERM) for h in HZ}
    oos_days = {h: np.flatnonzero((tab[h]['N'] > 0) & oos) for h in HZ}
    for i in range(NPERM):
        v = rng.permutation(vals_real)
        for h in HZ:
            s = ic_series(v, h, oos_days[h])
            perm_stat[h][i] = float(np.nanmean(s))
    log('  N3 随机状态置换 %d 次完成  %.0fs' % (NPERM, time.time() - t0))

    rows_p = []
    for h in HZ:
        real_oos = float(np.nanmean(ic_series(vals_real, h, oos_days[h])))
        nd = perm_stat[h]
        nd = nd[np.isfinite(nd)]
        p = float((np.sum(np.abs(nd) >= abs(real_oos)) + 1.0) / (len(nd) + 1.0))
        z = float((real_oos - nd.mean()) / (nd.std(ddof=1) + 1e-12))
        rows_p.append(dict(horizon=h, scope='OOS', real_ic=round(real_oos, 5),
                           null_mean=round(float(nd.mean()), 5),
                           null_sd=round(float(nd.std(ddof=1)), 5),
                           z=round(z, 3), p_emp=p, n_perm=len(nd),
                           n_oos_days=len(oos_days[h])))
        log('    T+%-3d OOS real=%+.4f  置换均值=%+.4f sd=%.4f  z=%+.2f  p=%.4f'
            % (h, real_oos, nd.mean(), nd.std(ddof=1), z, p))
    dfp = pd.DataFrame(rows_p)
    adj, rej = bh_fdr(dfp['p_emp'].values)
    dfp['p_adj_bh'] = adj.values
    dfp['reject_fdr05'] = rej.values
    dfp.to_csv(os.path.join(HERE, 'tl01_permutation.csv'), index=False,
               encoding='utf-8-sig')
    log('  已写 tl01_permutation.csv')

    # ---------------- §35 Null Model 族 ----------------
    rows_n = []

    def add(name, desc, mean_ic, sd_ic=None, n=None, z=None, p=None):
        rows_n.append(dict(null=name, description=desc, mean_ic=mean_ic,
                           sd_ic=sd_ic, n=n, z_vs_real=z, p=p))

    # N3（置换）作为随机状态零假设
    for h in HZ:
        r = [x for x in rows_p if x['horizon'] == h][0]
        add('N3_RandomState_T%d' % h, '随机重排 121 单元条件期望（OOS）',
            r['null_mean'], r['null_sd'], r['n_perm'], r['z'], r['p_emp'])

    # N1 随机股票：日内随机置换（经验，仅 OOS 日）
    rng2 = np.random.default_rng(SEED + 1)
    N1R = 100
    for h in HZ:
        Y = np.asarray(pget('fwd_%d' % h))
        stats = []
        for rep in range(N1R):
            ics = []
            for k in oos_days[h]:
                v = VAL[k]
                y = Y[k]
                m = v & np.isfinite(y) & (cell[k] >= 0)
                if m.sum() < 50:
                    continue
                xs = rng2.random(int(m.sum()))
                ys = y[m]
                rx = rank_avg(xs)
                ry = rank_avg(ys)
                rx = (rx - rx.mean()) / (rx.std() + 1e-12)
                ry = (ry - ry.mean()) / (ry.std() + 1e-12)
                ics.append(float(np.dot(rx, ry) / len(rx)))
            stats.append(np.mean(ics) if ics else np.nan)
        stats = np.array(stats)
        stats = stats[np.isfinite(stats)]
        real_oos = float(np.nanmean(ic_series(vals_real, h, oos_days[h])))
        z = float((real_oos - stats.mean()) / (stats.std(ddof=1) + 1e-12))
        add('N1_RandomStock_T%d' % h, '日内随机选股（%d 次，OOS）' % N1R,
            round(float(stats.mean()), 5), round(float(stats.std(ddof=1)), 5),
            len(stats), round(z, 3), np.nan)
        del Y

    # N2 随机日期：信号面板整体平移 d 日
    sig = np.full(ST.shape, np.nan, dtype=np.float32)
    sig[use] = mp[SP[use], ST[use]]
    for h in HZ:
        Y = np.asarray(pget('fwd_%d' % h))
        ics = []
        for d in range(1, 41):
            sh = np.full(ST.shape, np.nan, dtype=np.float32)
            sh[d:] = sig[:-d]
            s = daily_ic(sh, Y, VAL, days=oos_days[h])
            v = s[oos_days[h]]
            ics.append(float(np.nanmean(v)) if np.isfinite(v).any() else np.nan)
        ics = np.array(ics)
        ics = ics[np.isfinite(ics)]
        real_oos = float(np.nanmean(ic_series(vals_real, h, oos_days[h])))
        z = float((real_oos - ics.mean()) / (ics.std(ddof=1) + 1e-12))
        add('N2_RandomDate_T%d' % h, '信号整体平移 1..40 日（OOS）',
            round(float(ics.mean()), 5), round(float(ics.std(ddof=1)), 5),
            len(ics), round(z, 3), np.nan)
        del Y

    # N4 / N5 现实基准
    for tag, col in (('N4_Momentum20', 'ret_20'), ('N5_RelStrength20', 'rs_20')):
        X = np.asarray(pget(col))
        s = daily_ic(X, np.asarray(pget('fwd_%d' % PRIMARY)), VAL,
                     days=oos_days[PRIMARY])
        v = s[oos_days[PRIMARY]]
        r = ic_stats(v, PRIMARY)
        add('%s_T%d' % (tag, PRIMARY), '现实基准（非随机，参照）',
            round(r['mean_ic'], 5), round(r['ic_std'], 5), int(r['n']),
            np.nan, np.nan)
        del X

    dfn = pd.DataFrame(rows_n)
    dfn.to_csv(os.path.join(HERE, 'tl01_null_model.csv'), index=False,
               encoding='utf-8-sig')
    log('  已写 tl01_null_model.csv')

    # ---------------- 判定 ----------------
    verdict = []
    for h in HZ:
        r = [x for x in rows_p if x['horizon'] == h][0]
        verdict.append('T+%d %s(z=%.2f)' % (h, 'PASS' if abs(r['z']) >= ZS else 'FAIL',
                                            r['z']))
    log('-' * 78)
    log('  §51 Null 判定（|z| >= %.1f 视为通过）：%s'
        % (ZS, '  '.join(verdict)))
    log('完成  %.0fs' % (time.time() - t0))
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
