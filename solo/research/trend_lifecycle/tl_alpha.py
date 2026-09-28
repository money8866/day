# -*- coding: utf-8 -*-
"""TL-01 §25–§32 预测力与稳定性：IC / AUC / 年度 / Regime / OOS / Walk Forward

产出（§53）：
  tl01_ic.csv           §27 横截面 IC（Spearman/Pearson/ICIR，分信号 x 横断面 x 分期）
  tl01_auc.csv          §28 Winner/Loser 判别力（AUC / Precision@10% / Precision@20% / EffectSize）
  tl01_year.csv         §29 时间稳定性（Year / Quarter / Month；含 Positive 比例）
  tl01_regime.csv       §30 Regime 稳定性（BULL / NORMAL / BEAR 使用独立市场定义）
  tl01_oos.csv          §31 OOS 分阶段汇总（TRAIN / VALID / OOS / LIVE-LIKE）
  tl01_walkforward.csv  §32 Walk Forward（6 窗口，训练期独立拟合 SIG_TRANS）

纪律：
  - 所有信号只使用决策日 t 及之前的可观测信息（构建期已保证；状态/转换不含未来）。
  - SIG_TRANS 的 (state_prev,state) -> E[fwd_20] 映射只在「训练期」拟合；
    OOS / LIVE-LIKE 行不参与拟合、不参与任何选择（§31）。
  - 本模块只报告统计量，不做参数择优、不做投资评级（§49）。
"""
import os
import time
import numpy as np
import pandas as pd

from tl_common import (HERE, PREREG, STATE_NAMES, Log, panel_meta, pget,
                       phase_of, ic_stats, auc_score, pearson_r)

HZ = PREREG['horizons']
PH = PREREG['phases']
PRIMARY = 20
MIN_XS = 50                       # 单日横截面最少股票数
SIGNALS = ['SIG_TS', 'SIG_TV', 'SIG_TA', 'SIG_REACC', 'SIG_AGE',
           'SIG_TRANS', 'SIG_TRANS_CORE',
           'B1_MOM20', 'B2_MOM60', 'B3_MA20', 'B4_MA60', 'B5_RS20', 'B6_POS60']
SRC = {'SIG_TS': ('ts_raw', 1.0), 'SIG_TV': ('tv', 1.0), 'SIG_TA': ('ta', 1.0),
       'SIG_REACC': ('reacc_raw', 1.0), 'SIG_AGE': ('trend_age', -1.0),
       'B1_MOM20': ('ret_20', 1.0), 'B2_MOM60': ('ret_60', 1.0),
       'B3_MA20': ('c_ma20', 1.0), 'B4_MA60': ('c_ma60', 1.0),
       'B5_RS20': ('rs_20', 1.0), 'B6_POS60': ('pos_60', 1.0)}


def _rank(x):
    """并列取平均秩（正确 IC 必需：SIG_TRANS 取值离散，并列极多）"""
    u, inv, cnt = np.unique(x, return_inverse=True, return_counts=True)
    csum = np.cumsum(cnt)
    start = csum - cnt
    avg = (start + csum - 1) / 2.0
    return avg[inv]


def _fit_trans_map(frm, to, y, valid, min_n=30):
    """在给定子样本上拟合 (state_prev, state) -> E[fwd20]；样本不足回退到 state 均值"""
    ps = np.zeros((11, 11))
    cn = np.zeros((11, 11))
    m = valid & (frm >= 0) & (to >= 0) & np.isfinite(y)
    np.add.at(ps, (frm[m], to[m]), y[m].astype(np.float64))
    np.add.at(cn, (frm[m], to[m]), 1.0)
    mp = np.where(cn >= min_n, ps / np.maximum(cn, 1.0), np.nan)
    st_sum = np.zeros(11)
    st_cnt = np.zeros(11)
    m2 = valid & (to >= 0) & np.isfinite(y)
    np.add.at(st_sum, to[m2], y[m2].astype(np.float64))
    np.add.at(st_cnt, to[m2], 1.0)
    stm = np.where(st_cnt > 0, st_sum / np.maximum(st_cnt, 1.0), np.nan)
    gm = float(np.nanmean(y[m2])) if m2.sum() else 0.0
    for a in range(11):
        for b in range(11):
            if not np.isfinite(mp[a, b]):
                mp[a, b] = stm[b] if np.isfinite(stm[b]) else gm
    return mp


def _apply_trans(mp, frm, to):
    s = np.full(frm.shape, np.nan, dtype=np.float32)
    m = (frm >= 0) & (to >= 0)
    s[m] = mp[frm[m], to[m]]
    return s


def evaluate(sig, TGT, MFEMA, VAL, ph_d, NCAL, want_mfe=True):
    """逐日横截面：IC / AUC / Precision@k / EffectSize / 顶部十分位收益"""
    out = {}
    for h in TGT.keys():
        t = TGT[h]
        ic = np.full(NCAL, np.nan)
        icp = np.full(NCAL, np.nan)
        au = np.full(NCAL, np.nan)
        p10 = np.full(NCAL, np.nan)
        p20 = np.full(NCAL, np.nan)
        eff = np.full(NCAL, np.nan)
        top = np.full(NCAL, np.nan)
        bot = np.full(NCAL, np.nan)
        uni = np.full(NCAL, np.nan)
        for k in range(NCAL):
            v = VAL[k]
            if not v.any():
                continue
            s = sig[k]
            y = t[k]
            m = v & np.isfinite(s) & np.isfinite(y)
            n = int(m.sum())
            if n < MIN_XS:
                continue
            xs = s[m].astype(np.float64)
            ys = y[m].astype(np.float64)
            if np.std(xs) < 1e-12 or np.std(ys) < 1e-12:
                continue
            rx_raw = _rank(xs)
            ry = _rank(ys)
            rx = (rx_raw - rx_raw.mean()) / (rx_raw.std() + 1e-12)
            ry = (ry - ry.mean()) / (ry.std() + 1e-12)
            ic[k] = float(np.dot(rx, ry) / n)
            icp[k] = pearson_r(xs, ys)
            hi = np.percentile(ys, 90)
            lab = (ys >= hi).astype(np.float64)
            p, nn = lab.sum(), n - lab.sum()
            if p > 0 and nn > 0:
                # AUC（Mann-Whitney U，秩和口径；秩为 0..n-1）
                au[k] = float((rx_raw[lab == 1].sum() - p * (p + 1) / 2.0) / (p * nn))
                idxs = np.argsort(-xs)
                c10 = max(1, int(round(0.10 * n)))
                c20 = max(1, int(round(0.20 * n)))
                p10[k] = float(lab[idxs[:c10]].mean())
                p20[k] = float(lab[idxs[:c20]].mean())
                b10 = idxs[-c10:]
                eff[k] = float((ys[idxs[:c10]].mean() - ys[b10].mean())
                               / (ys.std() + 1e-12))
                top[k] = float(ys[idxs[:c10]].mean())
                bot[k] = float(ys[b10].mean())
                uni[k] = float(ys.mean())
        rec = dict(ic=ic, icp=icp, auc=au, prec10=p10, prec20=p20, eff=eff,
                   top=top, bot=bot, uni=uni)
        if want_mfe and h == PRIMARY:
            tm = np.full(NCAL, np.nan)
            am = np.full(NCAL, np.nan)
            for k in range(NCAL):
                v = VAL[k]
                if not v.any():
                    continue
                s = sig[k]
                y = TGT[h][k]
                m = v & np.isfinite(s) & np.isfinite(y)
                n = int(m.sum())
                if n < MIN_XS:
                    continue
                xs = s[m]
                idxs = np.argsort(-xs)[:max(1, int(round(0.10 * n)))]
                fm = MFEMA[0][k][m][idxs]
                am_ = MFEMA[1][k][m][idxs]
                tm[k] = float(np.nanmean(fm))
                am[k] = float(np.nanmean(am_))
            rec['top_mfe20'] = tm
            rec['top_mae20'] = am
        out[h] = rec
    return out


def main():
    t0 = time.time()
    log = Log('_tl_alpha_run.txt')
    log('=' * 78)
    log('TL-01 §25–§32 预测力与稳定性')
    meta = panel_meta()
    NCAL, NCODE = meta['ncal'], meta['ncode']
    dates = np.array(meta['dates'], dtype=object)

    ST = np.asarray(pget('state'))
    SP = np.asarray(pget('state_prev'))
    YR = np.asarray(pget('year'))
    RG = np.asarray(pget('regime'))
    VAL = ST >= 0
    log('  有效样本 %d / %d' % (int(VAL.sum()), VAL.size))

    TGT = {h: np.asarray(pget('fwd_%d' % h)) for h in HZ}
    MFEMA = (np.asarray(pget('mfe_20')), np.asarray(pget('mae_20')))

    # 逐日分期 / regime / 年月
    ph_d = np.empty(NCAL, dtype=object)
    yr_d = np.empty(NCAL, dtype=np.int32)
    qt_d = np.empty(NCAL, dtype=object)
    rg_d = np.empty(NCAL, dtype=np.int16)
    for k in range(NCAL):
        d = dates[k]
        if d:                       # 前 119 日早于最小历史长度，面板无任何行
            yr_d[k] = int(d[:4])
            qt_d[k] = '%sQ%d' % (d[:4], (int(d[4:6]) - 1) // 3 + 1)
        else:
            yr_d[k] = 0
            qt_d[k] = ''
        r = RG[k]
        r = r[np.isfinite(r)]
        rg_d[k] = int(np.bincount(r.astype(np.int64) + 1).argmax() - 1) if len(r) else -1
    ph_d = phase_of(yr_d)
    log('  分期天数：' + '  '.join('%s=%d' % (nm, int((ph_d == nm).sum()))
                                   for nm, _a, _b in PH))
    log('  Regime 天数：BEAR=%d NORMAL=%d BULL=%d'
        % (int((rg_d == 0).sum()), int((rg_d == 1).sum()), int((rg_d == 2).sum())))

    frm_all = SP[VAL]
    to_all = ST[VAL]
    y20_all = TGT[PRIMARY][VAL]
    trm = np.broadcast_to((ph_d == 'TRAIN')[:, None], VAL.shape)[VAL]
    mp_train = _fit_trans_map(frm_all, to_all, y20_all, trm)
    log('  SIG_TRANS 映射（仅 TRAIN 2018-2022 拟合）：')
    for a, b in PREREG['report_transitions']:
        log('    S%d->S%d  E[fwd20]=%.4f' % (a, b, mp_train[a, b]))

    # ---------------- 逐信号评估 ----------------
    res = {}
    for nm in SIGNALS:
        t1 = time.time()
        if nm == 'SIG_TRANS':
            sig = np.full(VAL.shape, np.nan, dtype=np.float32)
            sig[VAL] = _apply_trans(mp_train, frm_all, to_all)
        elif nm == 'SIG_TRANS_CORE':
            core = np.zeros(VAL.shape, dtype=np.float32)
            sc = np.zeros(ST.shape, dtype=np.float32)
            hit = np.zeros((11, 11), dtype=bool)
            for a, b in PREREG['core_transitions']:
                hit[a, b] = True
            m = (SP >= 0) & (ST >= 0)
            hh = np.zeros(ST.shape, dtype=bool)
            hh[m] = hit[SP[m], ST[m]]
            sc[hh] = 1.0
            sc[~VAL] = np.nan
            sig = sc
        else:
            src, sg = SRC[nm]
            a = np.asarray(pget(src)).astype(np.float32)
            sig = a * np.float32(sg)
            sig[~VAL] = np.nan
            del a
        res[nm] = evaluate(sig, TGT, MFEMA, VAL, ph_d, NCAL)
        log('    %-14s 完成  %.1fs   IC20=%.4f  ICIR=%.3f  AUC20=%.4f'
            % (nm, time.time() - t1,
               np.nanmean(res[nm][PRIMARY]['ic']),
               ic_stats(res[nm][PRIMARY]['ic'], PRIMARY)['icir'],
               np.nanmean(res[nm][PRIMARY]['auc'])))
        del sig

    # ---------------- §27 IC ----------------
    log('-' * 78)
    log('1) §27 横截面 IC / ICIR（分期）')
    rows = []
    scopes = [('ALL', np.ones(NCAL, dtype=bool))]
    for nm2, _a, _b in PH:
        scopes.append((nm2, ph_d == nm2))
    scopes += [('BEAR', rg_d == 0), ('NORMAL', rg_d == 1), ('BULL', rg_d == 2)]
    for nm in SIGNALS:
        for h in HZ:
            base = res[nm][h]
            for sc, idx in scopes:
                st = ic_stats(base['ic'][idx], h)
                stp = ic_stats(base['icp'][idx], h)
                rows.append(dict(signal=nm, horizon=h, scope=sc, n_dates=st['n'],
                                 mean_ic=st['mean_ic'], med_ic=st['med_ic'],
                                 ic_std=st['ic_std'], icir=st['icir'],
                                 ic_t=st['ic_t'], pos_ratio=st['pos_ratio'],
                                 p_raw=st['p_raw'], pearson=stp['mean_ic']))
    df_ic = pd.DataFrame(rows)
    adj, rej = None, None
    try:
        from tl_common import bh_fdr
        pv = df_ic['p_raw'].values
        a2, r2 = bh_fdr(pv)
        df_ic['p_bh'] = a2.values
        df_ic['reject_bh'] = r2.values.astype(int)
    except Exception:
        df_ic['p_bh'] = np.nan
        df_ic['reject_bh'] = 0
    df_ic.to_csv(os.path.join(HERE, 'tl01_ic.csv'), index=False, encoding='utf-8-sig')
    log('  已写 tl01_ic.csv（%d 行）' % len(df_ic))

    # ---------------- §28 AUC ----------------
    log('-' * 78)
    log('2) §28 Winner/Loser 判别力（AUC / Precision / EffectSize）')
    rows = []
    for nm in SIGNALS:
        for h in HZ:
            for sc, idx in scopes:
                b = res[nm][h]
                g = lambda c: float(np.nanmean(b[c][idx])) if idx.any() else np.nan
                nn = int(np.isfinite(b['auc'][idx]).sum())
                rows.append(dict(signal=nm, horizon=h, scope=sc, n_dates=nn,
                                 auc=g('auc'), prec10=g('prec10'), prec20=g('prec20'),
                                 effect_size=g('eff'), top_mean=g('top'),
                                 bot_mean=g('bot'), universe_mean=g('uni'),
                                 top_minus_bot=(g('top') - g('bot'))))
    pd.DataFrame(rows).to_csv(os.path.join(HERE, 'tl01_auc.csv'),
                              index=False, encoding='utf-8-sig')
    log('  已写 tl01_auc.csv')

    # ---------------- §29 Year / Quarter / Month ----------------
    log('-' * 78)
    log('3) §29 时间稳定性（Year / Quarter / Month）')
    rows = []
    years = sorted(set(y for y in yr_d.tolist() if y > 0))
    for nm in SIGNALS:
        for h in HZ:
            b = res[nm][h]
            for y in years:
                idx = yr_d == y
                st = ic_stats(b['ic'][idx], h)
                rows.append(dict(signal=nm, horizon=h, period='Y%d' % y, kind='year',
                                 n_dates=st['n'], mean_ic=st['mean_ic'],
                                 icir=st['icir'], pos_ratio=st['pos_ratio'],
                                 top_mean=float(np.nanmean(b['top'][idx])),
                                 spread=float(np.nanmean((b['top'] - b['bot'])[idx]))))
            for qt in sorted(set(q for q in qt_d.tolist() if q)):
                idx = qt_d == qt
                rows.append(dict(signal=nm, horizon=h, period=qt, kind='quarter',
                                 n_dates=int(idx.sum()),
                                 mean_ic=float(np.nanmean(b['ic'][idx])),
                                 icir=np.nan, pos_ratio=np.nan,
                                 top_mean=float(np.nanmean(b['top'][idx])),
                                 spread=float(np.nanmean((b['top'] - b['bot'])[idx]))))
    months = np.array([d[:6] for d in dates.tolist()], dtype=object)
    qts = np.array([str(x) for x in qt_d.tolist()], dtype=object)
    for nm in SIGNALS:
        for h in HZ:
            b = res[nm][h]
            mrec = {}
            for key, arr in (('month', months), ('quarter', qts)):
                vals = []
                for u in sorted(set(arr.tolist())):
                    idx = arr == u
                    v = np.nanmean(b['ic'][idx])
                    if np.isfinite(v):
                        vals.append(v)
                mrec[key] = (float(np.mean(np.array(vals) > 0)) if vals else np.nan,
                             len(vals))
            yv = []
            for y in years:
                v = np.nanmean(b['ic'][yr_d == y])
                if np.isfinite(v):
                    yv.append(v)
            rows.append(dict(signal=nm, horizon=h, period='SUMMARY', kind='positive_ratio',
                             n_dates=NCAL,
                             mean_ic=float(np.mean(np.array(yv) > 0)) if yv else np.nan,
                             icir=np.nan, pos_ratio=mrec['month'][0],
                             top_mean=mrec['quarter'][0], spread=np.nan))
    df_y = pd.DataFrame(rows)
    df_y['pos_years'] = df_y['mean_ic'].where(df_y['kind'] == 'positive_ratio')
    df_y['pos_months'] = df_y['pos_ratio'].where(df_y['kind'] == 'positive_ratio')
    df_y['pos_quarters'] = df_y['top_mean'].where(df_y['kind'] == 'positive_ratio')
    df_y.to_csv(os.path.join(HERE, 'tl01_year.csv'), index=False, encoding='utf-8-sig')
    log('  已写 tl01_year.csv')

    # ---------------- §30 Regime ----------------
    log('-' * 78)
    log('4) §30 Regime 稳定性（独立市场定义：PROXY 200MA + 60日动量）')
    rows = []
    for nm in SIGNALS:
        for h in HZ:
            b = res[nm][h]
            for rc, rn in ((0, 'BEAR'), (1, 'NORMAL'), (2, 'BULL')):
                idx = rg_d == rc
                st = ic_stats(b['ic'][idx], h)
                tw = b['top'][idx]
                rec = dict(signal=nm, horizon=h, regime=rn, n_dates=int(idx.sum()),
                           mean_ic=st['mean_ic'], icir=st['icir'], ic_t=st['ic_t'],
                           top_mean=float(np.nanmean(tw)),
                           uni_mean=float(np.nanmean(b['uni'][idx])),
                           winrate=float(np.nanmean(tw > 0)),
                           spread=float(np.nanmean((b['top'] - b['bot'])[idx])))
                if h == PRIMARY and 'top_mfe20' in b:
                    rec['mfe20'] = float(np.nanmean(b['top_mfe20'][idx]))
                    rec['mae20'] = float(np.nanmean(b['top_mae20'][idx]))
                rows.append(rec)
    pd.DataFrame(rows).to_csv(os.path.join(HERE, 'tl01_regime.csv'),
                              index=False, encoding='utf-8-sig')
    log('  已写 tl01_regime.csv')

    # ---------------- §31 OOS ----------------
    log('-' * 78)
    log('5) §31 OOS 分阶段汇总')
    rows = []
    for nm in SIGNALS:
        for h in HZ:
            b = res[nm][h]
            for pn, _a, _b in PH:
                idx = ph_d == pn
                st = ic_stats(b['ic'][idx], h)
                rows.append(dict(signal=nm, horizon=h, phase=pn, n_dates=int(idx.sum()),
                                 mean_ic=st['mean_ic'], icir=st['icir'], ic_t=st['ic_t'],
                                 auc=float(np.nanmean(b['auc'][idx])),
                                 top_mean=float(np.nanmean(b['top'][idx])),
                                 uni_mean=float(np.nanmean(b['uni'][idx])),
                                 excess=float(np.nanmean((b['top'] - b['uni'])[idx])),
                                 winrate=float(np.nanmean(b['top'][idx] > 0)),
                                 spread=float(np.nanmean((b['top'] - b['bot'])[idx]))))
    pd.DataFrame(rows).to_csv(os.path.join(HERE, 'tl01_oos.csv'),
                              index=False, encoding='utf-8-sig')
    log('  已写 tl01_oos.csv')

    # ---------------- §32 Walk Forward ----------------
    log('-' * 78)
    log('6) §32 Walk Forward（训练期独立拟合 SIG_TRANS，测试年评估）')
    rows = []
    for wname, ta, tb, ty in PREREG['walkforward']:
        tridx = (yr_d >= ta) & (yr_d <= tb)
        teidx = yr_d == ty
        mp = _fit_trans_map(frm_all, to_all, y20_all,
                            np.broadcast_to(tridx[:, None], VAL.shape)[VAL])
        sig = np.full(ST.shape, np.nan, dtype=np.float32)
        sig[VAL] = _apply_trans(mp, frm_all, to_all)
        rr = evaluate(sig, {PRIMARY: TGT[PRIMARY]}, MFEMA, VAL, ph_d, NCAL,
                      want_mfe=True)[PRIMARY]
        for sc, idx in (('train', tridx), ('test', teidx)):
            st = ic_stats(rr['ic'][idx], PRIMARY)
            rows.append(dict(window=wname, train='%d-%d' % (ta, tb), test=ty,
                             scope=sc, factor='SIG_TRANS', n_dates=int(idx.sum()),
                             mean_ic=st['mean_ic'], icir=st['icir'], ic_t=st['ic_t'],
                             auc=float(np.nanmean(rr['auc'][idx])),
                             top_mean=float(np.nanmean(rr['top'][idx])),
                             uni_mean=float(np.nanmean(rr['uni'][idx])),
                             spread=float(np.nanmean((rr['top'] - rr['bot'])[idx])),
                             top_mfe20=float(np.nanmean(rr['top_mfe20'][idx])),
                             top_mae20=float(np.nanmean(rr['top_mae20'][idx]))))
        st = ic_stats(rr['ic'][teidx], PRIMARY)
        log('    %s  train %d-%d  test %d  test_IC=%.4f  ICIR=%.3f  AUC=%.4f'
            % (wname, ta, tb, ty, st['mean_ic'], st['icir'],
               np.nanmean(rr['auc'][teidx])))
        del sig
    pd.DataFrame(rows).to_csv(os.path.join(HERE, 'tl01_walkforward.csv'),
                              index=False, encoding='utf-8-sig')
    log('  已写 tl01_walkforward.csv')

    log('-' * 78)
    log('完成  %.0fs' % (time.time() - t0))
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
