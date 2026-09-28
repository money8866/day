# -*- coding: utf-8 -*-
"""TL-01 机制诊断 DX1（路线二 · 2A）

性质
  只读诊断。不重新拟合 SIG_TRANS、不修改任何预注册阈值、不产生任何新信号或交易授权。
  本脚本不进入 §52 判定，也不用于「救援」TL-01 的 CONDITIONAL 结论。

要回答的三个问题
  Q1  正 IC 是否由「规避单元」单独贡献，而非「选中单元」——
      即把样本限制到 e_train>0 的 (state_prev,state) 单元后，IC 是否归零。
  Q2  Model A 多数状态 avg_dur≈1 日的成因——
      是驱动条件（tv / v_trend / ATR 分位 / pos 分位 / dd 阈值）本身日频反复穿越阈值，
      还是状态在生命周期意义上真的快速演进。
  Q3  Target 错配——绝对 fwd_20 / 相对 rel_20 / T+1 可执行 exe_20 三种目标，
      以及 cont_* / fail_* 二值标签，是否给出不同的判别力结论。

输出
  tl01_dx1_decile.csv   信号十分位曲线（三目标 + 单元构成）
  tl01_dx1_cells.csv    121 个 (state_prev,state) 单元的 TRAIN/VALID/OOS/LIVE 期望
  tl01_dx1_state.csv    各状态占比 / P(stay) / 游程分布 / 主要出口
  tl01_dx1_flip.csv     驱动条件日频翻转率（总体 与 趋势态内）
  tl01_dx1_target.csv   目标 x 分期 的 IC / AUC / 顶部收益
  tl01_dx1_summary.json 关键结论机读汇总
  tl01_dx1_diagnostic.md 诊断备忘
"""
import os
import json
import time
import numpy as np
import pandas as pd

from tl_common import (HERE, PREREG, STATE_NAMES, Log, panel_meta, pget,
                       phase_of, rank_avg, ic_stats, daily_ic, MIN_XS)
from tl_alpha import (evaluate as alpha_evaluate, _fit_trans_map, _apply_trans)

PHASES = ['ALL', 'TRAIN', 'VALID', 'OOS', 'LIVE-LIKE']
NH = 20
RUN_BINS = np.array([1, 2, 5, 10, 20, 40, 10 ** 9])
RUN_LABELS = ['1', '2', '3-5', '6-10', '11-20', '21-40', '41+']


def auc_series(sig, lab, VAL, min_xs=MIN_XS):
    """逐日 AUC（Mann-Whitney 秩和口径，并列取平均秩）；lab 为 0/1 标签"""
    NCAL = sig.shape[0]
    out = np.full(NCAL, np.nan)
    for k in range(NCAL):
        v = VAL[k]
        if not v.any():
            continue
        m = v & np.isfinite(sig[k]) & np.isfinite(lab[k])
        n = int(m.sum())
        if n < min_xs:
            continue
        lb = lab[k][m].astype(np.float64)
        p = float((lb == 1).sum())
        nn = n - p
        if p <= 0 or nn <= 0:
            continue
        r = rank_avg(sig[k][m].astype(np.float64))
        out[k] = float((r[lb == 1].sum() - p * (p + 1) / 2.0) / (p * nn))
    return out


def pstat(series, PM, p, h=NH):
    return ic_stats(series[PM[p]], h)


def main():
    t0 = time.time()
    log = Log('_tl_diag_dx1_run.txt')
    log('=' * 78)
    log('TL-01 DX1 机制诊断（只读，不产生信号、不进入 §52 判定）')

    meta = panel_meta()
    NCAL, NCODE = meta['ncal'], meta['ncode']
    dates = np.array(meta['dates'], dtype=object)

    ST = np.asarray(pget('state')).astype(np.int8)
    SP = np.asarray(pget('state_prev')).astype(np.int8)
    F20 = np.asarray(pget('fwd_20')).astype(np.float32)
    R20 = np.asarray(pget('rel_20')).astype(np.float32)
    # 注意：面板里 fwd_* 已减 1（收益率），exe_* 是价格比（未减 1），此处统一为收益率
    E20 = np.asarray(pget('exe_20')).astype(np.float32) - np.float32(1.0)
    VAL = ST >= 0
    log('  有效样本 %d / %d' % (int(VAL.sum()), VAL.size))

    # 交易日层面的年份/分期（面板 year 列是逐单元格的，分期需要按日）
    yr_d = np.zeros(NCAL, dtype=np.int32)
    for k in range(NCAL):
        d = dates[k]
        if d:
            yr_d[k] = int(d[:4])
    ph_d = phase_of(yr_d)
    PM = {p: (ph_d == p) for p in PHASES}
    PM['ALL'] = np.ones(NCAL, dtype=bool)
    for p in PHASES:
        log('  分期 %-9s 交易日 %d' % (p, int(PM[p].sum())))

    # ---------------- SIG_TRANS（TRAIN 期拟合，与 tl_alpha 完全同源） ----------------
    frm = SP[VAL]
    to = ST[VAL]
    y20_v = F20[VAL]
    trm = np.broadcast_to(PM['TRAIN'][:, None], VAL.shape)[VAL]
    mp = _fit_trans_map(frm, to, y20_v, trm)
    SIG = np.full(ST.shape, np.nan, dtype=np.float32)
    SIG[VAL] = _apply_trans(mp, frm, to)

    CID2 = np.maximum(SP, 0).astype(np.int32) * 11 + np.maximum(ST, 0)
    cellid_v = CID2[VAL]

    # ---------------- Q1a: 121 单元期望 + 分期实现 ----------------
    def cell_agg(v, mask):
        mm = mask & np.isfinite(v)
        c = np.bincount(cellid_v[mm], minlength=121)
        s = np.bincount(cellid_v[mm], weights=v[mm].astype(np.float64),
                        minlength=121)
        return c, np.where(c > 0, s / np.maximum(c, 1), np.nan)

    phv = {p: np.broadcast_to(PM[p][:, None], VAL.shape)[VAL] for p in PHASES}
    agg = {}
    for p in PHASES:
        for key, arr in (('fwd', F20), ('rel', R20), ('exe', E20)):
            agg[(p, key)] = cell_agg(arr[VAL], phv[p])
    rows = []
    for a in range(11):
        for b in range(11):
            cid = a * 11 + b
            rec = dict(from_state=a, from_name=STATE_NAMES[a],
                       to_state=b, to_name=STATE_NAMES[b],
                       e_train=float(mp[a, b]))
            for p in PHASES:
                c, m_ = agg[(p, 'fwd')]
                rec['n_' + p] = int(c[cid])
                rec['mean_' + p] = float(m_[cid])
            rec['rel20_oos'] = float(agg[('OOS', 'rel')][1][cid])
            rec['exe20_oos'] = float(agg[('OOS', 'exe')][1][cid])
            rows.append(rec)
    cells = pd.DataFrame(rows)

    gp = np.zeros(121, dtype=bool)
    gm = np.zeros(121, dtype=bool)
    for a in range(11):
        for b in range(11):
            if mp[a, b] > 1e-9:
                gp[a * 11 + b] = True
            elif mp[a, b] < -1e-9:
                gm[a * 11 + b] = True
    G0 = ~gp & ~gm
    VAL_GP = VAL & gp[CID2]
    VAL_GM = VAL & gm[CID2]
    VAL_G0 = VAL & G0[CID2]
    n_cell = int(len(mp.ravel()))
    log('  单元：正期望 %d / 负期望 %d / 零期望 %d（共 %d）'
        % (int(gp.sum()), int(gm.sum()), int(G0.sum()), n_cell))

    # 单元符号稳定性：TRAIN 期望符号 vs OOS 实现符号（n_oos>=30）
    sub = cells[cells['n_OOS'] >= 30].copy()
    sub = sub[np.abs(sub['e_train']) > 1e-9]
    sign_agr = float((np.sign(sub['e_train']) == np.sign(sub['mean_OOS'])).mean()) \
        if len(sub) else np.nan
    corr_cell = float(np.corrcoef(sub['e_train'], sub['mean_OOS'])[0, 1]) \
        if len(sub) >= 3 else np.nan
    log('  单元符号一致率(TRAIN vs OOS, n>=30) = %s，单元期望相关系数 = %s'
        % ('n/a' if not np.isfinite(sign_agr) else '%.3f' % sign_agr,
           'n/a' if not np.isfinite(corr_cell) else '%.3f' % corr_cell))

    # ---------------- Q1b: 单元限制下的 IC ----------------
    ic_full = daily_ic(SIG, F20, VAL)
    ic_gp = daily_ic(SIG, F20, VAL_GP)
    ic_gm = daily_ic(SIG, F20, VAL_GM)
    ic_g0 = daily_ic(SIG, F20, VAL_G0)
    ic_rows = []
    for nm, ser in (('FULL', ic_full), ('ONLY_Gplus', ic_gp),
                    ('ONLY_Gminus', ic_gm), ('ONLY_G0', ic_g0)):
        for p in PHASES:
            st = pstat(ser, PM, p)
            ic_rows.append(dict(scope=nm, phase=p, n_dates=st['n'],
                                mean_ic=st['mean_ic'], icir=st['icir'],
                                ic_t=st['ic_t'], pos_ratio=st['pos_ratio']))
    icdf = pd.DataFrame(ic_rows)
    log('  Q1 单元限制 IC20：')
    for nm in ('FULL', 'ONLY_Gplus', 'ONLY_Gminus'):
        r = icdf[(icdf['scope'] == nm) & (icdf['phase'] == 'OOS')].iloc[0]
        log('    %-12s OOS IC=%.4f  t=%.2f  n=%d'
            % (nm, r['mean_ic'], r['ic_t'], int(r['n_dates'])))

    # 分期实现均值
    grp_rows = []
    f20v = F20[VAL]
    r20v = R20[VAL]
    for p in PHASES:
        m = phv[p]
        rec = dict(phase=p)
        for nm, msk in (('Gplus', VAL_GP), ('Gminus', VAL_GM),
                        ('G0', VAL_G0), ('universe', VAL)):
            mv = msk[VAL]
            mm = m & mv & np.isfinite(f20v)
            rec['n_' + nm] = int(mm.sum())
            rec['fwd20_' + nm] = float(f20v[mm].mean()) if mm.sum() else np.nan
            mm2 = m & mv & np.isfinite(r20v)
            rec['rel20_' + nm] = float(r20v[mm2].mean()) if mm2.sum() else np.nan
        grp_rows.append(rec)
    grpdf = pd.DataFrame(grp_rows)
    log('  Q1 单元分组的 T+20 均值（OOS）：')
    _o = grpdf[grpdf['phase'] == 'OOS'].iloc[0]
    log('    G+ %s / G- %s / 全体 %s'
        % ('%.4f' % _o['fwd20_Gplus'], '%.4f' % _o['fwd20_Gminus'],
           '%.4f' % _o['fwd20_universe']))

    # ---------------- Q1c: 十分位曲线 ----------------
    DS = {p: dict(n=np.zeros(10), fwd=np.zeros(10), rel=np.zeros(10),
                  exe=np.zeros(10), gp=np.zeros(10), gm=np.zeros(10))
          for p in PHASES}
    day = {p: dict(d1=np.full(NCAL, np.nan), d10=np.full(NCAL, np.nan),
                   uni=np.full(NCAL, np.nan)) for p in PHASES}
    for k in range(NCAL):
        v = VAL[k]
        if not v.any():
            continue
        m = v & np.isfinite(SIG[k]) & np.isfinite(F20[k]) \
            & np.isfinite(R20[k]) & np.isfinite(E20[k])
        n = int(m.sum())
        if n < MIN_XS:
            continue
        r = rank_avg(SIG[k][m].astype(np.float64))
        b = np.minimum(9, (((r - 1.0) / n) * 10.0).astype(np.int64))
        ys = F20[k][m].astype(np.float64)
        y2 = R20[k][m].astype(np.float64)
        y3 = E20[k][m].astype(np.float64)
        gpm = gp[CID2[k][m]].astype(np.float64)
        gmm = gm[CID2[k][m]].astype(np.float64)
        nb = np.bincount(b, minlength=10)
        sf = np.bincount(b, weights=ys, minlength=10)
        sr = np.bincount(b, weights=y2, minlength=10)
        se = np.bincount(b, weights=y3, minlength=10)
        sg = np.bincount(b, weights=gpm, minlength=10)
        sm = np.bincount(b, weights=gmm, minlength=10)
        for p in PHASES:
            if not PM[p][k]:
                continue
            DS[p]['n'] += nb
            DS[p]['fwd'] += sf
            DS[p]['rel'] += sr
            DS[p]['exe'] += se
            DS[p]['gp'] += sg
            DS[p]['gm'] += sm
            if (b == 0).any():
                day[p]['d1'][k] = float(ys[b == 0].mean())
            if (b == 9).any():
                day[p]['d10'][k] = float(ys[b == 9].mean())
            day[p]['uni'][k] = float(ys.mean())
    dr = []
    for p in PHASES:
        d = DS[p]
        for i in range(10):
            nn = max(d['n'][i], 1.0)
            dr.append(dict(phase=p, decile=i + 1, n=int(d['n'][i]),
                           mean_fwd20=d['fwd'][i] / nn,
                           mean_rel20=d['rel'][i] / nn,
                           mean_exe20=d['exe'][i] / nn,
                           frac_Gplus=d['gp'][i] / nn,
                           frac_Gminus=d['gm'][i] / nn))
    dec = pd.DataFrame(dr)

    spr = []
    for p in PHASES:
        dd = day[p]
        for nm, a, bb in (('D10-D1', dd['d10'], dd['d1']),
                          ('D10-uni', dd['d10'], dd['uni']),
                          ('D1-uni', dd['d1'], dd['uni'])):
            st = ic_stats(np.asarray(a) - np.asarray(bb), NH)
            spr.append(dict(phase=p, spread=nm, n_dates=st['n'],
                            mean=st['mean_ic'], t=st['ic_t'],
                            pos_ratio=st['pos_ratio']))
    spdf = pd.DataFrame(spr)
    log('  Q1 十分位价差（T+20, exe=可执行）：')
    for p in ('TRAIN', 'OOS'):
        r = spdf[(spdf['phase'] == p) & (spdf['spread'] == 'D10-D1')].iloc[0]
        log('    %-9s D10-D1 = %+.4f (t=%.2f)' % (p, r['mean'], r['t']))

    # 剔除 D1 后的全体均值（「只避不选」反事实）
    avoid = {}
    for p in PHASES:
        d = DS[p]
        n_keep = d['n'][1:].sum()
        f_keep = d['fwd'][1:].sum()
        avoid[p] = dict(n_keep=int(n_keep),
                        mean_keep=float(f_keep / n_keep) if n_keep else np.nan,
                        mean_uni=float(d['fwd'].sum() / d['n'].sum())
                        if d['n'].sum() else np.nan)
    log('  剔除信号最低十分位后的 T+20 均值：')
    for p in ('TRAIN', 'OOS'):
        log('    %-9s 剔除后 %+.4f vs 全体 %+.4f'
            % (p, avoid[p]['mean_keep'], avoid[p]['mean_uni']))

    # ---------------- Q2: 状态粒度 / 抖动 ----------------
    prev_ok = np.zeros_like(VAL, dtype=bool)
    prev_ok[1:] = VAL[:-1]
    pair = VAL & prev_ok
    tr = np.bincount((np.maximum(SP, 0).astype(np.int16) * 11
                      + np.maximum(ST, 0).astype(np.int16))[pair],
                     minlength=121).reshape(11, 11)
    row_sum = tr.sum(1)
    p_stay = np.where(row_sum > 0, np.diag(tr) / np.maximum(row_sum, 1), np.nan)
    chg_rate = float((ST[pair] != SP[pair]).mean())

    # 游程（按股票列）
    hist = np.zeros((11, 7), dtype=np.int64)
    runs_tot = np.zeros(11, dtype=np.int64)
    reentry = np.zeros(11, dtype=np.int64)
    len_sum = np.zeros(11, dtype=np.float64)
    for j in range(NCODE):
        s = ST[:, j]
        ok = s >= 0
        if not ok.any():
            continue
        arr = s[ok]
        chg = np.flatnonzero(arr[1:] != arr[:-1]) + 1
        starts = np.concatenate(([0], chg))
        ends = np.concatenate((chg, [len(arr)]))
        lens = (ends - starts).astype(np.int64)
        sts = arr[starts].astype(np.int64)
        for i in range(len(sts)):
            sid = int(sts[i])
            L = int(lens[i])
            runs_tot[sid] += 1
            len_sum[sid] += L
            bi = int(np.searchsorted(RUN_BINS, L, side='left'))
            hist[sid, min(bi, 6)] += 1
            if i > 0 and lens[i - 1] <= 2:
                reentry[sid] += 1
    srows = []
    for s in range(11):
        ex = tr[s].copy()
        ex[s] = 0
        order = np.argsort(-ex)[:3]
        rt = max(int(runs_tot[s]), 1)
        srows.append(dict(
            state=s, name=STATE_NAMES[s],
            n_cells=int((ST == s).sum()),
            share=float((ST == s).sum()) / float(VAL.sum()),
            p_stay=float(p_stay[s]),
            mean_run=float(len_sum[s] / rt),
            n_runs=int(runs_tot[s]),
            run1_share=float(hist[s, 0] / rt),
            run2_share=float(hist[s, 1] / rt),
            run_le2_share=float((hist[s, 0] + hist[s, 1]) / rt),
            run_ge21_share=float((hist[s, 5] + hist[s, 6]) / rt),
            reentry_after_short_share=float(reentry[s] / rt),
            e1_state=int(order[0]), e1_p=float(ex[order[0]] / max(row_sum[s], 1)),
            e2_state=int(order[1]), e2_p=float(ex[order[1]] / max(row_sum[s], 1)),
            e3_state=int(order[2]), e3_p=float(ex[order[2]] / max(row_sum[s], 1)),
        ))
    sdf = pd.DataFrame(srows)
    log('  Q2 状态抖动：全样本日频状态改变率 = %.4f' % chg_rate)
    for s in (3, 4, 5, 8):
        r = sdf[sdf['state'] == s].iloc[0]
        log('    S%-2d P(stay)=%.3f  单日游程占比=%.3f  平均游程=%.2f 日'
            % (s, r['p_stay'], r['run1_share'] or 0.0, r['mean_run']))
    log('    S%-2d P(stay)=%.3f  单日游程占比=%.3f  平均游程=%.2f 日'
        % (7, sdf[sdf['state'] == 7].iloc[0]['p_stay'],
           sdf[sdf['state'] == 7].iloc[0]['run1_share'],
           sdf[sdf['state'] == 7].iloc[0]['mean_run']))

    # 驱动条件翻转率（逐列加载，避免同时驻留十余个 49MB 数组）
    TS = np.asarray(pget('ts_raw')).astype(np.float32)
    SPEC = [
        ('tv > 0', 'tv', 'gt', 0.0),
        ('v_trend > 0', 'v_trend', 'gt', 0.0),
        ('atr_pct_pct >= 0.60', 'atr_pct_pct', 'ge', 0.60),
        ('atr_pct_pct >= 0.70', 'atr_pct_pct', 'ge', 0.70),
        ('pos_60 >= 0.40', 'pos_60', 'ge', 0.40),
        ('pos_60 <= 0.85', 'pos_60', 'le', 0.85),
        ('pos_120 >= 0.90', 'pos_120', 'ge', 0.90),
        ('pos_120 >= 0.95', 'pos_120', 'ge', 0.95),
        ('c_ma20 > 0', 'c_ma20', 'gt', 0.0),
        ('ma20_ma60 > 0', 'ma20_ma60', 'gt', 0.0),
        ('slope_20 > 0', 'slope_20', 'gt', 0.0),
        ('rs_20 > 0', 'rs_20', 'gt', 0.0),
        ('v_ma5_ma20 < 1', 'v_ma5_ma20', 'lt', 1.0),
        ('dd_60 <= -0.05', 'dd_60', 'le', -0.05),
        ('dd_min10 <= -0.02', 'dd_min10', 'le', -0.02),
    ]
    trend_row = pair & np.isin(ST, np.array([3, 4, 5, 8], dtype=np.int8))
    n_pair = max(int(pair.sum()), 1)
    n_trend = max(int(trend_row.sum()), 1)
    frows = []
    for nm, col, op, thr in SPEC:
        a = np.asarray(pget(col)).astype(np.float32)
        if op == 'gt':
            cond = a > thr
        elif op == 'ge':
            cond = a >= thr
        elif op == 'le':
            cond = a <= thr
        else:
            cond = a < thr
        del a
        same = np.zeros_like(cond)
        same[1:] = cond[:-1]
        diff = cond != same
        frows.append(dict(feature=nm,
                          n_pairs=n_pair,
                          flip_rate=float((pair & diff).sum()) / n_pair,
                          flip_rate_trend=float((trend_row & diff).sum()) / n_trend))
        del cond, same, diff

    fdf = pd.DataFrame(frows)
    prev_ts = np.zeros_like(TS)
    prev_ts[1:] = TS[:-1]
    dts = np.abs(TS - prev_ts)
    m_stay = pair & (ST == SP)
    m_chg = pair & (ST != SP)
    dts_stay = float(np.nanmean(dts[m_stay]))
    dts_chg = float(np.nanmean(dts[m_chg]))
    log('  Q2 |dTS| 状态未变 %.4f vs 状态改变 %.4f（比值 %.2f）'
        % (dts_stay, dts_chg, dts_chg / (dts_stay + 1e-12)))

    # ---------------- Q3: Target 错配 ----------------
    trows = []
    tgt = [('fwd_20', F20), ('rel_20', R20), ('exe_20', E20)]
    res = {}
    for nm, arr in tgt:
        r = alpha_evaluate(SIG, {NH: arr}, (None, None), VAL, ph_d, NCAL,
                           want_mfe=False)[NH]
        res[nm] = r
        for p in PHASES:
            idx = PM[p]
            st = ic_stats(r['ic'][idx], NH)
            trows.append(dict(kind='IC', target=nm, phase=p, n_dates=st['n'],
                              mean_ic=st['mean_ic'], icir=st['icir'],
                              ic_t=st['ic_t'], auc=float(np.nanmean(r['auc'][idx])),
                              prec10=float(np.nanmean(r['prec10'][idx])),
                              top_mean=float(np.nanmean(r['top'][idx])),
                              uni_mean=float(np.nanmean(r['uni'][idx])),
                              bot_mean=float(np.nanmean(r['bot'][idx]))))
    # 秩不变性检验：rel_k = fwd_k - idxr[k]（当日全市场同一常数）
    # → 横截面秩完全相同，任何秩统计量（Spearman IC / AUC / 十分位）必然逐日相同
    n_same, n_tot = 0, 0
    for k in range(NCAL):
        v = VAL[k]
        if not v.any():
            continue
        m = v & np.isfinite(F20[k]) & np.isfinite(R20[k])
        if int(m.sum()) < MIN_XS:
            continue
        n_tot += 1
        if np.array_equal(rank_avg(F20[k][m].astype(np.float64)),
                          rank_avg(R20[k][m].astype(np.float64))):
            n_same += 1
    log('  Q3 秩不变性：fwd_20 与 rel_20 横截面秩一致的交易日 %d / %d'
        % (n_same, n_tot))

    logs = [('cont_nh20', 'cont_nh20'), ('cont_ma20', 'cont_ma20'),
            ('cont_rs20', 'cont_rs20'), ('fail_ma20', 'fail_ma20'),
            ('fail_ma60', 'fail_ma60'), ('fail_low', 'fail_low'),
            ('fail_rs', 'fail_rs')]
    for nm, col in logs:
        arr = np.asarray(pget(col)).astype(np.float32)
        a = auc_series(SIG, arr, VAL)
        for p in PHASES:
            m = PM[p][:, None] & VAL & np.isfinite(arr)
            trows.append(dict(kind='AUC_label', target=nm, phase=p,
                              n_dates=int(np.isfinite(a[PM[p]]).sum()),
                              mean_ic=np.nan, icir=np.nan, ic_t=np.nan,
                              auc=float(np.nanmean(a[PM[p]])),
                              prec10=np.nan,
                              top_mean=float(arr[m].mean()) if m.sum() else np.nan,
                              uni_mean=np.nan, bot_mean=np.nan))
    tdf = pd.DataFrame(trows)
    log('  Q3 Target 对比（OOS）：')
    for nm, _ in tgt:
        r = tdf[(tdf['kind'] == 'IC') & (tdf['target'] == nm)
                & (tdf['phase'] == 'OOS')].iloc[0]
        log('    %-8s IC=%+.4f  AUC=%.4f  Top=%.4f  全体=%.4f'
            % (nm, r['mean_ic'], r['auc'], r['top_mean'], r['uni_mean']))

    # ---------------- 落盘 ----------------
    dec.to_csv(os.path.join(HERE, 'tl01_dx1_decile.csv'),
               index=False, encoding='utf-8-sig')
    cells.to_csv(os.path.join(HERE, 'tl01_dx1_cells.csv'),
                 index=False, encoding='utf-8-sig')
    sdf.to_csv(os.path.join(HERE, 'tl01_dx1_state.csv'),
               index=False, encoding='utf-8-sig')
    fdf.to_csv(os.path.join(HERE, 'tl01_dx1_flip.csv'),
               index=False, encoding='utf-8-sig')
    tdf.to_csv(os.path.join(HERE, 'tl01_dx1_target.csv'),
               index=False, encoding='utf-8-sig')

    def g(df, **kw):
        m = pd.Series(True, index=df.index)
        for c, v in kw.items():
            m &= (df[c] == v)
        return df[m].iloc[0] if m.any() else None

    S = dict(
        generated='2026-09-26',
        nature='read-only mechanism diagnostic; no signal, no gate, no authorization',
        q1=dict(
            n_cells_plus=int(gp.sum()), n_cells_minus=int(gm.sum()),
            n_cells_zero=int(G0.sum()),
            sign_agreement_train_oos=sign_agr, cell_corr_train_oos=corr_cell,
            ic20={r['scope']: {p: float(g(icdf, scope=r['scope'], phase=p)['mean_ic'])
                               for p in PHASES}
                  for r in [dict(scope=s) for s in
                            ('FULL', 'ONLY_Gplus', 'ONLY_Gminus', 'ONLY_G0')]},
            t20_oos=dict(
                gplus=float(_o['fwd20_Gplus']), gminus=float(_o['fwd20_Gminus']),
                universe=float(_o['fwd20_universe']),
                gplus_rel=float(_o['rel20_Gplus']),
                gminus_rel=float(_o['rel20_Gminus']),
                universe_rel=float(_o['rel20_universe'])),
            decile={p: [float(x) for x in DS[p]['fwd'] / np.maximum(DS[p]['n'], 1)]
                    for p in PHASES},
            decile_rel={p: [float(x) for x in DS[p]['rel'] / np.maximum(DS[p]['n'], 1)]
                        for p in PHASES},
            decile_exe={p: [float(x) for x in DS[p]['exe'] / np.maximum(DS[p]['n'], 1)]
                        for p in PHASES},
            decile_gplus_share={p: [float(x) for x in DS[p]['gp'] / np.maximum(DS[p]['n'], 1)]
                                for p in PHASES},
            spread={p: {sp: dict(
                mean=float(spdf[(spdf['phase'] == p)
                                & (spdf['spread'] == sp)].iloc[0]['mean']),
                t=float(spdf[(spdf['phase'] == p)
                             & (spdf['spread'] == sp)].iloc[0]['t']))
                for sp in ('D10-D1', 'D10-uni', 'D1-uni')}
                for p in PHASES},
            avoid_d1=avoid,
        ),
        q2=dict(state_change_rate=chg_rate,
                states={int(r['state']): dict(
                    name=r['name'], share=float(r['share']), p_stay=float(r['p_stay']),
                    mean_run=float(r['mean_run']), run1_share=float(r['run1_share']),
                    run_le2_share=float(r['run_le2_share']),
                    run_ge21_share=float(r['run_ge21_share']),
                    reentry=float(r['reentry_after_short_share']),
                    top_exit=[[int(r['e1_state']), float(r['e1_p'])],
                              [int(r['e2_state']), float(r['e2_p'])],
                              [int(r['e3_state']), float(r['e3_p'])]])
                    for r in srows},
                flip_rates={r['feature']: dict(all=float(r['flip_rate']),
                                               trend=float(r['flip_rate_trend']))
                            for r in frows},
                dts_stay=dts_stay, dts_change=dts_chg),
        q3=dict(ic={nm: {p: float(g(tdf, kind='IC', target=nm, phase=p)['mean_ic'])
                         for p in PHASES} for nm, _ in tgt},
                auc={nm: {p: float(g(tdf, kind='IC', target=nm, phase=p)['auc'])
                          for p in PHASES} for nm, _ in tgt},
                auc_label={nm: {p: float(g(tdf, kind='AUC_label', target=nm,
                                           phase=p)['auc']) for p in PHASES}
                           for nm, _ in logs},
                rank_identity_days=[n_same, n_tot]),
    )
    with open(os.path.join(HERE, 'tl01_dx1_summary.json'), 'w',
              encoding='utf-8') as f:
        json.dump(S, f, ensure_ascii=False, indent=2)

    # ---------------- 备忘 ----------------
    H = []
    A = H.append
    A('# TL-01 DX1 机制诊断备忘')
    A('')
    A('性质：只读诊断。**不重新拟合、不改阈值、不产生信号、不进入 §52 判定、不改变 TL-01 的')
    A('CONDITIONAL 结论**，也不构成对 TL-01 的「救援」。')
    A('数据：`tl01_*` 面板（8.6M 有效样本）+ TRAIN 期冻结的 SIG_TRANS 映射。')
    A('')
    A('## Q1 正 IC 是不是「只避不选」')
    A('')
    A('单元切分：正期望单元 %d 个 / 负期望单元 %d 个 / 零期望单元 %d 个（共 121）。'
      % (int(gp.sum()), int(gm.sum()), int(G0.sum())))
    A('')
    A('| 样本范围 | ALL | TRAIN | VALID | OOS | LIVE-LIKE |')
    A('|---|---|---|---|---|---|')
    for nm in ('FULL', 'ONLY_Gplus', 'ONLY_Gminus', 'ONLY_G0'):
        vals = []
        for p in PHASES:
            r = g(icdf, scope=nm, phase=p)
            vals.append('n/a' if (r is None or not np.isfinite(r['mean_ic']))
                        else '%.4f (%d)' % (r['mean_ic'], int(r['n_dates'])))
        A('| %s | %s |' % (nm, ' | '.join(vals)))
    A('')
    A('T+20 均值（OOS）：G+ %+.4f / G- %+.4f / 全体 %+.4f；相对口径 G+ %+.4f / G- %+.4f / 全体 %+.4f。'
      % (_o['fwd20_Gplus'], _o['fwd20_Gminus'], _o['fwd20_universe'],
         _o['rel20_Gplus'], _o['rel20_Gminus'], _o['rel20_universe']))
    A('')
    A('单元符号一致率（TRAIN 期望 vs OOS 实现，n≥30）= %s；单元期望相关系数 = %s。'
      % ('n/a' if not np.isfinite(sign_agr) else '%.3f' % sign_agr,
         'n/a' if not np.isfinite(corr_cell) else '%.3f' % corr_cell))
    A('')
    A('十分位 T+20 均值：')
    A('')
    hdr = '| 目标 | ' + ' | '.join('D%d' % (i + 1) for i in range(10)) + ' |'
    A(hdr)
    A('|---|' + '---|' * 10)
    for key, lab in (('fwd', 'fwd_20'), ('rel', 'rel_20'), ('exe', 'exe_20')):
        vals = [('%.4f' % (DS['ALL'][key][i] / max(DS['ALL']['n'][i], 1)))
                for i in range(10)]
        A('| ALL %s | %s |' % (lab, ' | '.join(vals)))
    for key, lab in (('fwd', 'fwd_20'), ('exe', 'exe_20')):
        vals = [('%.4f' % (DS['OOS'][key][i] / max(DS['OOS']['n'][i], 1)))
                for i in range(10)]
        A('| OOS %s | %s |' % (lab, ' | '.join(vals)))
    A('')
    A('十分位价差（Newey-West t，滞后 19）：')
    A('')
    A('| 分期 | D10-D1 | t | D10-uni | t | D1-uni | t |')
    A('|---|---|---|---|---|---|---|')
    for p in PHASES:
        cellsv = []
        for sp in ('D10-D1', 'D10-uni', 'D1-uni'):
            r = spdf[(spdf['phase'] == p) & (spdf['spread'] == sp)].iloc[0]
            cellsv += ['%+.4f' % r['mean'], '%.2f' % r['t']]
        A('| %s | %s |' % (p, ' | '.join(cellsv)))
    A('')
    A('剔除信号最低十分位后的 T+20 均值（「只避不选」反事实）：')
    for p in PHASES:
        A('- %s：剔除后 %+.4f vs 全体 %+.4f'
          % (p, avoid[p]['mean_keep'], avoid[p]['mean_uni']))
    A('')
    A('## Q2 avg_dur≈1 日的成因')
    A('')
    A('全样本日频状态改变率 = %.4f；|ΔTS| 状态未变 %.4f vs 状态改变 %.4f（比值 %.2f）。'
      % (chg_rate, dts_stay, dts_chg, dts_chg / (dts_stay + 1e-12)))
    A('')
    A('| 状态 | 占比 | P(stay) | 平均游程(日) | 单日游程占比 | ≤2日游程占比 | 短期折返占比 | 主要出口 |')
    A('|---|---|---|---|---|---|---|---|')
    for r in srows:
        A('| %s | %.3f | %.3f | %.2f | %.3f | %.3f | %.3f | S%d(%.2f) S%d(%.2f) S%d(%.2f) |'
          % (r['name'], r['share'], r['p_stay'], r['mean_run'], r['run1_share'],
             r['run_le2_share'], r['reentry_after_short_share'],
             r['e1_state'], r['e1_p'], r['e2_state'], r['e2_p'],
             r['e3_state'], r['e3_p']))
    A('')
    A('驱动条件日频翻转率（相邻有效交易日对；右列为趋势态 S3/S4/S5/S8 内）：')
    A('')
    A('| 条件 | 全体翻转率 | 趋势态内翻转率 |')
    A('|---|---|---|')
    for r in frows:
        A('| %s | %.3f | %.3f |' % (r['feature'], r['flip_rate'], r['flip_rate_trend']))
    A('')
    A('## Q3 Target 错配')
    A('')
    A('| 目标 | 分期 | IC | ICIR | t | AUC | 顶部十分位 | 全体 |')
    A('|---|---|---|---|---|---|---|---|')
    for nm, _ in tgt:
        for p in PHASES:
            r = g(tdf, kind='IC', target=nm, phase=p)
            A('| %s | %s | %+.4f | %.3f | %.2f | %.4f | %+.4f | %+.4f |'
              % (nm, p, r['mean_ic'], r['icir'], r['ic_t'], r['auc'],
                 r['top_mean'], r['uni_mean']))
    A('')
    A('注 1：`rel_20 = fwd_20 − 当日全市场指数收益`（同一交易日对全体股票是同一常数），')
    A('因此两者的**横截面秩逐日完全相同**——实测 %d/%d 个交易日秩一致。'
      % (n_same, n_tot))
    A('任何秩统计量（Spearman IC / AUC / 十分位）在两者上必然恒等，故 rel_20 一列不构成独立证据；')
    A('相对口径只在绝对收益水平与择时层面才有区别。')
    A('')
    A('注 2：`exe_20` 为 T+1 开盘进场、T+20 收盘出场的可执行收益（已由价格比换算为收益率）；')
    A('它比 fwd_20 多承担一日的执行滞后。')
    A('')
    A('二值标签 AUC（>0.5 表示信号越高越易发生该事件）：')
    A('')
    A('| 标签 | ALL | TRAIN | VALID | OOS | LIVE-LIKE |')
    A('|---|---|---|---|---|---|')
    for nm, _ in logs:
        vals = ['%.4f' % float(g(tdf, kind='AUC_label', target=nm, phase=p)['auc'])
                for p in PHASES]
        A('| %s | %s |' % (nm, ' | '.join(vals)))
    A('')
    A('## 诊断结论（不含任何交易建议）')
    A('')
    A('见 tl01_dx1_summary.json 的机读字段；本备忘仅陈述结构事实，不作投资评级（§49）。')
    A('')
    with open(os.path.join(HERE, 'tl01_dx1_diagnostic.md'), 'w',
              encoding='utf-8') as f:
        f.write('\n'.join(H) + '\n')

    log('-' * 78)
    log('落盘：tl01_dx1_decile/cells/state/flip/target.csv, dx1_summary.json, dx1_diagnostic.md')
    log('完成 %.0fs' % (time.time() - t0))
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
