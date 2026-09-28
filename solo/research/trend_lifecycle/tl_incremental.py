# -*- coding: utf-8 -*-
"""TL-01 §25 反事实 / §26 增量 Alpha（Momentum / Size / Value / Volatility / Industry 控制）

回答两个问题：
  §25  同样的股票与市场环境下，若不用生命周期状态、只用普通 Momentum，结论是否相同？
       做法：把 SIG_TRANS（TRAIN 拟合的状态转换条件期望）与 B1 Momentum20 / B2 Momentum60 /
             B5 RS20 / B6 POS60 放在同一口径下比较（逐日横截面 IC），并做双重排序
             （先按 momentum 分位、再按生命周期信号分位），检验生命周期是否在 momentum 内部仍有区分度。
  §26  Lifecycle 信号在控制 Momentum + Size + Value + Volatility + Industry 之后是否仍有增量信息？
       做法：逐日横截面 OLS
             fwd20 ~ 1 + SIG_TRANS + MOM20 + logMV + PB + ATR% + Industry dummies
       收集 SIG_TRANS 的每日系数 → Fama-MacBeth 均值与 t 值；并比较控制前 / 控制后的 IC。

纪律：控制变量只用决策日 t 及之前可观测的估值/行情字段；SIG_TRANS 映射只在 TRAIN 拟合。
产出：out/frag_incremental.json（报告素材）+ _tl_incremental_run.txt
"""
import os
import json
import time
import numpy as np

from tl_common import (OUTD, PREREG, Log, panel_meta, pget, ic_stats, phase_of,
                       daily_ic, rank_avg)

PRIMARY_H = 20
MIN_XS = 100


def fit_trans_map(frm, to, y, valid, min_n=30):
    ps = np.zeros((11, 11))
    cn = np.zeros((11, 11))
    m = valid & (frm >= 0) & (to >= 0) & np.isfinite(y)
    np.add.at(ps, (frm[m], to[m]), y[m].astype(np.float64))
    np.add.at(cn, (frm[m], to[m]), 1.0)
    mp = np.where(cn >= min_n, ps / np.maximum(cn, 1.0), np.nan)
    st_s = np.zeros(11)
    st_c = np.zeros(11)
    m2 = valid & (to >= 0) & np.isfinite(y)
    np.add.at(st_s, to[m2], y[m2].astype(np.float64))
    np.add.at(st_c, to[m2], 1.0)
    stm = np.where(st_c > 0, st_s / np.maximum(st_c, 1.0), np.nan)
    gm = float(np.mean(y[m2])) if m2.sum() else 0.0
    for a in range(11):
        for b in range(11):
            if not np.isfinite(mp[a, b]):
                mp[a, b] = stm[b] if np.isfinite(stm[b]) else gm
    return mp


def zrow(v):
    v = np.asarray(v, dtype=np.float64)
    v = np.clip(v, np.percentile(v, 1), np.percentile(v, 99))
    sd = v.std()
    return (v - v.mean()) / sd if sd > 1e-12 else np.zeros_like(v)


def main():
    t0 = time.time()
    log = Log('_tl_incremental_run.txt')
    log('=' * 78)
    log('TL-01 §25 反事实 / §26 增量 Alpha（控制 Momentum/Size/Value/Vol/Industry）')

    meta = panel_meta()
    NCAL, NCODE = meta['ncal'], meta['ncode']
    dates = meta['dates']
    ph_d = phase_of(np.array([int(d[:4]) if d else 0 for d in dates], dtype=np.int32))

    ST = np.asarray(pget('state'))
    SP = np.asarray(pget('state_prev'))
    Y = np.asarray(pget('fwd_%d' % PRIMARY_H))
    VAL = ST >= 0
    MOM20 = np.asarray(pget('ret_20'))
    MOM60 = np.asarray(pget('ret_60'))
    RS20 = np.asarray(pget('rs_20'))
    POS60 = np.asarray(pget('pos_60'))
    MV = np.asarray(pget('bm_total_mv'))
    PB = np.asarray(pget('bm_pb'))
    ATR = np.asarray(pget('atr_pct_pct'))
    L1 = np.asarray(pget('ind_l1'))

    train_day = np.broadcast_to((ph_d == 'TRAIN')[:, None], ST.shape)
    mp = fit_trans_map(SP[VAL], ST[VAL], Y[VAL], train_day[VAL])
    SIGT = np.full(ST.shape, np.nan, dtype=np.float32)
    m = (SP >= 0) & (ST >= 0)
    SIGT[m] = mp[SP[m], ST[m]]
    log('  SIG_TRANS 映射（TRAIN 拟合）就绪  %.0fs' % (time.time() - t0))

    # ---------------- 行业虚拟变量 ----------------
    cats = np.array(sorted(set(int(x) for x in np.unique(L1) if x >= 0)))
    NG = len(cats)
    L1C = L1.copy()
    log('  行业类别（一级，缺失 -> 剔除该行） = %d' % NG)

    # ---------------- 逐日横截面回归 ----------------
    def day_beta(k):
        v = VAL[k]
        if not v.any():
            return None
        s = SIGT[k]
        mm = v & np.isfinite(s) & np.isfinite(Y[k])
        n = int(mm.sum())
        if n < MIN_XS:
            return None
        ys = Y[k][mm].astype(np.float64)
        x_sig = s[mm].astype(np.float64)
        m20 = MOM20[k][mm].astype(np.float64)
        m60 = MOM60[k][mm].astype(np.float64)
        lmv = np.log(np.maximum(MV[k][mm].astype(np.float64), 1e3))
        pb = PB[k][mm].astype(np.float64)
        atr = ATR[k][mm].astype(np.float64)
        l1 = L1C[k][mm]
        okc = (np.isfinite(m20) & np.isfinite(m60) & np.isfinite(lmv)
               & np.isfinite(pb) & np.isfinite(atr) & (l1 >= 0))
        if okc.sum() < MIN_XS:
            return None
        ys = ys[okc]
        x_sig, m20, m60 = x_sig[okc], m20[okc], m60[okc]
        lmv, pb, atr, l1 = lmv[okc], pb[okc], atr[okc], l1[okc]
        l1 = np.searchsorted(cats, l1)
        D = np.zeros((len(ys), max(NG - 1, 1)))
        jj = l1 - 1
        good = jj >= 0
        D[np.flatnonzero(good), jj[good]] = 1.0
        # 统一方向：Size/Value/Vol 取标准化后入模
        C = np.column_stack([zrow(m20), zrow(m60), zrow(lmv), zrow(pb), zrow(atr), D])
        # 缩减模型：仅 SIG_TRANS
        A0 = np.column_stack([np.ones(len(ys)), zrow(x_sig)])
        b0, *_ = np.linalg.lstsq(A0, ys, rcond=None)
        # 全模型：SIG_TRANS + controls
        A1 = np.column_stack([A0, C])
        b1, *_ = np.linalg.lstsq(A1, ys, rcond=None)
        # SIG_TRANS 对 controls 的横截面残差 -> 增量 IC 用
        r_sig = A0[:, 1] - np.column_stack([np.ones(len(ys)), C]).dot(
            np.linalg.lstsq(np.column_stack([np.ones(len(ys)), C]),
                            A0[:, 1], rcond=None)[0])
        r_mom = zrow(m20) - np.column_stack([np.ones(len(ys)), zrow(x_sig)]).dot(
            np.linalg.lstsq(np.column_stack([np.ones(len(ys)), zrow(x_sig)]),
                            zrow(m20), rcond=None)[0])
        return b0[1], b1[1], r_sig, r_mom, okc, 1.0

    NB = np.full(NCAL, np.nan)      # beta 无控制
    NBt = np.full(NCAL, np.nan)     # beta 有控制
    RS = np.full((NCAL, NCODE), np.nan, dtype=np.float32)
    RM = np.full((NCAL, NCODE), np.nan, dtype=np.float32)
    for k in range(NCAL):
        r = day_beta(k)
        if r is None:
            continue
        NB[k] = r[0]
        NBt[k] = r[1]
        mm = VAL[k] & np.isfinite(SIGT[k]) & np.isfinite(Y[k])
        idx = np.flatnonzero(mm)
        okc = r[4]
        RS[k, idx[okc]] = r[2]
        RM[k, idx[okc]] = r[3]
        if k % 400 == 0:
            log('    逐日回归 %d/%d  %.0fs' % (k, NCAL, time.time() - t0))
    log('  逐日横截面回归完成  %.0fs' % (time.time() - t0))

    def fm(beta, pts):
        out = {}
        for pn, _a, _b in PREREG['phases']:
            idx = pts == pn
            v = beta[idx]
            v = v[np.isfinite(v)]
            if len(v) < 5:
                out[pn] = dict(n=int(len(v)), mean=np.nan, t=np.nan)
                continue
            t = float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v))))
            out[pn] = dict(n=int(len(v)), mean=round(float(v.mean()), 6),
                           t=round(t, 3))
        return out

    ic_raw = daily_ic(SIGT, Y, VAL)
    ic_m20 = daily_ic(MOM20, Y, VAL)
    ic_m60 = daily_ic(MOM60, Y, VAL)
    ic_rs = daily_ic(RS20, Y, VAL)
    ic_pos = daily_ic(POS60, Y, VAL)
    ic_sres = daily_ic(RS, Y, VAL)
    ic_mres = daily_ic(RM, Y, VAL)

    def agg_ic(x):
        return {pn: dict(n=int(ic_stats(x[ph_d == pn], PRIMARY_H)['n']),
                         ic=round(ic_stats(x[ph_d == pn], PRIMARY_H)['mean_ic'], 5),
                         icir=round(ic_stats(x[ph_d == pn], PRIMARY_H)['icir'], 4))
                for pn, _a, _b in PREREG['phases']}

    # ---------------- §25 双重排序 ----------------
    def double_sort(pn):
        idx = ph_d == pn
        s = SIGT[idx]
        mo = MOM20[idx]
        y = Y[idx]
        v = VAL[idx]
        m = v & np.isfinite(s) & np.isfinite(mo) & np.isfinite(y)
        s, mo, y = s[m], mo[m], y[m]
        qm = np.clip((rank_avg(mo) - 1) / (len(mo) / 5.0), 0, 4).astype(int)
        qs = np.clip((rank_avg(s) - 1) / (len(s) / 5.0), 0, 4).astype(int)
        cell = np.full((5, 5), np.nan)
        for i in range(5):
            for j in range(5):
                mm = (qm == i) & (qs == j)
                if mm.sum() >= 100:
                    cell[i, j] = float(y[mm].mean())
        return np.round(cell, 5).tolist(), int(m.sum())

    frag = dict(
        horizon=PRIMARY_H,
        fama_macbeth=dict(reduced_only_sigtrans=fm(NB, ph_d),
                          full_with_controls=fm(NBt, ph_d),
                          controls=['MOM20', 'MOM60', 'logMV', 'PB', 'ATR%',
                                    'Industry dummies']),
        ic=dict(SIG_TRANS=agg_ic(ic_raw), B1_MOM20=agg_ic(ic_m20),
                B2_MOM60=agg_ic(ic_m60), B5_RS20=agg_ic(ic_rs),
                B6_POS60=agg_ic(ic_pos),
                SIG_TRANS_resid_on_controls=agg_ic(ic_sres),
                MOM20_resid_on_SIGTRANS=agg_ic(ic_mres)),
        double_sort={pn: dict(cell=double_sort(pn)[0], n=double_sort(pn)[1])
                     for pn, _a, _b in PREREG['phases']},
        note='SIG_TRANS map fit on TRAIN only; controls are point-in-time fields')
    with open(os.path.join(OUTD, 'frag_incremental.json'), 'w', encoding='utf-8') as f:
        json.dump(frag, f, ensure_ascii=False, indent=1)

    log('-' * 78)
    log('  §25 逐日横截面 IC（T+20，分期）')
    for nm, x in (('SIG_TRANS', ic_raw), ('B1_MOM20', ic_m20), ('B2_MOM60', ic_m60),
                  ('B5_RS20', ic_rs), ('B6_POS60', ic_pos),
                  ('SIG_TRANS|controls', ic_sres), ('MOM20|SIG_TRANS', ic_mres)):
        a = agg_ic(x)
        log('    %-20s TRAIN %+.4f (icir %.3f)  OOS %+.4f (icir %.3f)'
            % (nm, a['TRAIN']['ic'], a['TRAIN']['icir'],
               a['OOS']['ic'], a['OOS']['icir']))
    log('  §26 Fama-MacBeth 系数（fwd20 ~ SIG_TRANS [+controls]）')
    for nm, d in (('无控制', frag['fama_macbeth']['reduced_only_sigtrans']),
                  ('含控制', frag['fama_macbeth']['full_with_controls'])):
        log('    %s：' % nm + '  '.join(
            '%s beta=%+.5f t=%s' % (pn, d[pn]['mean'], d[pn]['t'])
            for pn, _a, _b in PREREG['phases']))
    log('  §25 双重排序（momentum 五分位 x SIG_TRANS 五分位，OOS 平均 fwd20）')
    for row in frag['double_sort']['OOS']['cell']:
        log('    ' + '  '.join('%+.3f%%' % (x * 100) if x == x else '   n/a'
                               for x in row))
    log('  已写 out/frag_incremental.json')
    log('完成  %.0fs' % (time.time() - t0))
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
