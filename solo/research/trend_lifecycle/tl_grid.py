# -*- coding: utf-8 -*-
"""TL-01 §33 Parameter Perturbation / §34 Grid Smoothness

目的不是找最优参数（禁止），而是判断「是否存在连续稳定区域」。

扰动轴（§33 指定）：
  MA 窗口      : 18 / 20 / 22      -> c_ma{w}, ma{w}_ma60
  趋势窗口     : 18 / 20 / 25      -> ret_{w}, tv_alt{w}（w=20 时为 tv）
  RS 窗口      : 18 / 20 / 25      -> rs_{w}
  回撤阈值     : 5.0% / 7.5% / 10% -> cfg.dd_mild
  Age 分界     : 15 / 20 / 30      -> 「年轻 vs 年长」趋势年龄对比

评估口径：以扰动后的状态重算 SIG_TRANS（(state_prev,state)->E[fwd20]，仅 TRAIN 拟合），
再在 OOS 上做逐日横截面 IC。同时输出该配置下核心状态占比，避免「IC 变了是因为
状态分布塌缩」。

§34 平滑性判据（本脚本内固定，不因结果调整）：
  对 3x3 参数网格，记 9 个 OOS IC 为 g。
    sign_consistency = max(#g>0, #g<0) / 9
    roughness        = mean(|g_i - g_j|) over 相邻（上下左右）单元 / mean(|g|)
    isolated_peak    = (max(g) - max(邻域)) > std(g) 且 max(g) 是唯一 > mean(g)+std(g) 的单元
  判定：sign_consistency >= 0.78（即 7/9 同号）且 not isolated_peak -> SMOOTH；
        否则 PARAMETER_FRAGILE。

产出：tl01_parameter_grid.csv（逐配置 + 网格汇总）+ _tl_grid_run.txt
"""
import os
import time
import numpy as np
import pandas as pd

from tl_common import (HERE, PREREG, Log, panel_meta, pget, ic_stats, phase_of,
                       daily_ic)
from tl_states import assign_state, default_cfg

HZ = list(PREREG['horizons'])
PRIMARY = int(PREREG['primary_horizon'])
COLS = ['c_ma18', 'c_ma20', 'c_ma22', 'ma18_ma60', 'ma20_ma60', 'ma22_ma60',
        'ma60_slope', 'slope_20', 'pos_60', 'pos_120', 'ret_18', 'ret_20',
        'ret_25', 'rs_18', 'rs_20', 'rs_25', 'ts_raw', 'tv', 'tv_alt18',
        'tv_alt25', 'atr_pct_pct', 'v_trend', 'v_ma5_ma20', 'dd_min10', 'dd_60']


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


def derive_prev(S):
    """与 tl_build 同口径的 state_prev：同一股票相邻交易日、前一日状态（允许 S0=0）"""
    pv = np.full(S.shape, -1, dtype=np.int8)
    pv[1:] = S[:-1]
    pv[0] = -1
    pv[S < 0] = -1
    return pv


def main():
    t0 = time.time()
    log = Log('_tl_grid_run.txt')
    log('=' * 78)
    log('TL-01 §33 参数扰动 / §34 参数网格平滑性')

    meta = panel_meta()
    NCAL, NCODE = meta['ncal'], meta['ncode']
    ph_d = phase_of(np.array([int(d[:4]) if d else 0
                              for d in meta['dates']], dtype=np.int32))
    S0 = np.asarray(pget('state'))
    SP0 = np.asarray(pget('state_prev'))
    VAL = S0 >= 0
    Y = np.asarray(pget('fwd_%d' % PRIMARY))
    AGE = np.asarray(pget('trend_age'))
    F = {c: np.asarray(pget(c)) for c in COLS}
    F['up_recent20'] = F['ret_20']          # 与 tl_build 同源定义（见 tl_build L696）
    train_day = np.broadcast_to((ph_d == 'TRAIN')[:, None], S0.shape)
    oos_days = np.flatnonzero(ph_d == 'OOS')
    log('  面板 %d x %d  有效 %d  特征列 %d  载入 %.0fs'
        % (NCAL, NCODE, int(VAL.sum()), len(F), time.time() - t0))

    def run_cfg(tag, cfg):
        t1 = time.time()
        # assign_state 是 1 维实现（tl_build 按股票段调用）；这里展平后调用再还原
        Ff = {k: v.reshape(-1) for k, v in F.items()}
        S = assign_state(Ff, cfg).reshape(S0.shape)
        S[~VAL] = -1
        pv = derive_prev(S)      # 与 tl_build 同口径：同股票相邻日的前一日状态（允许 S0=0）
        m = (pv >= 0) & (S >= 0)
        mp = fit_trans_map(pv[m], S[m], Y[m], train_day[m])
        sig = np.full(S.shape, np.nan, dtype=np.float32)
        sig[m] = mp[pv[m], S[m]]
        ic = daily_ic(sig, Y, VAL, days=np.flatnonzero(ph_d == 'TRAIN'))
        ic_oos = daily_ic(sig, Y, VAL, days=oos_days)
        rtr = ic_stats(ic, PRIMARY)
        roos = ic_stats(ic_oos, PRIMARY)
        dist = {s: float((S == s).sum()) / max(int((S >= 0).sum()), 1)
                for s in (1, 2, 3, 4, 7, 8)}
        return dict(tag=tag, sig=sig, S=S, mp=mp, dist=dist,
                    train_ic=rtr['mean_ic'], train_icir=rtr['icir'],
                    oos_ic=roos['mean_ic'], oos_icir=roos['icir'],
                    oos_n=roos['n'], sec=round(time.time() - t1, 1))

    base = run_cfg('baseline', default_cfg())
    mism = int((base['S'] != S0).sum())
    log('  baseline 自检：重算状态与交付状态不一致 %d / %d -> %s'
        % (mism, S0.size, 'PASS' if mism == 0 else 'FAIL'))
    mprev = int((derive_prev(base['S']) != SP0).sum())
    log('  baseline 自检：重算 state_prev 与交付 state_prev 不一致 %d / %d -> %s'
        % (mprev, SP0.size, 'PASS' if mprev == 0 else 'FAIL'))
    log('  baseline  TRAIN_IC=%+.4f  OOS_IC=%+.4f (ICIR %.3f, n=%d)'
        % (base['train_ic'], base['oos_ic'], base['oos_icir'], base['oos_n']))

    rows = []

    def rec(r, axis, value, grid, a2, v2):
        rows.append(dict(kind='config', grid=grid, axis=axis, value=value,
                         param_a=a2, param_b=v2, tag=r['tag'],
                         train_ic=round(r['train_ic'], 5),
                         train_icir=round(r['train_icir'], 4),
                         oos_ic=round(r['oos_ic'], 5),
                         oos_icir=round(r['oos_icir'], 4),
                         oos_days=r['oos_n'], sec=r['sec'],
                         share_S3=round(r['dist'][3], 5), share_S4=round(r['dist'][4], 5),
                         share_S7=round(r['dist'][7], 5), share_S8=round(r['dist'][8], 5)))

    rec(base, 'baseline', 20, 'baseline', 20, 20)

    # ---------------- 一维扰动 ----------------
    for axis, key, vals in (('ma_win', 'ma_win', PREREG['grid_ma']),
                            ('trend_win', 'trend_win', PREREG['grid_trend_win']),
                            ('rs_win', 'rs_win', PREREG['grid_rs_win']),
                            ('dd_mild', 'dd_mild', PREREG['grid_dd'])):
        for v in vals:
            r = run_cfg('%s=%s' % (axis, v), default_cfg(**{key: v}))
            rec(r, axis, v, 'ofat', v, '')
            log('    %-14s %-6s TRAIN %+.4f  OOS %+.4f  (%.0fs)'
                % (axis, v, r['train_ic'], r['oos_ic'], r['sec']))

    # ---------------- 二维网格（§34） ----------------
    def grid2(name, axa, keya, vala, axb, keyb, valb):
        cells = np.full((len(vala), len(valb)), np.nan)
        for i, va in enumerate(vala):
            for j, vb in enumerate(valb):
                r = run_cfg('%s=%s,%s=%s' % (keya, va, keyb, vb),
                            default_cfg(**{keya: va, keyb: vb}))
                rec(r, name, '', name, va, vb)
                cells[i, j] = r['oos_ic']
        g = cells.ravel()
        g = g[np.isfinite(g)]
        mean_abs = float(np.mean(np.abs(g))) if len(g) else np.nan
        nb = []
        for i in range(cells.shape[0]):
            for j in range(cells.shape[1]):
                for di, dj in ((1, 0), (0, 1)):
                    if i + di < cells.shape[0] and j + dj < cells.shape[1]:
                        nb.append(abs(cells[i, j] - cells[i + di, j + dj]))
        rough = float(np.mean(nb) / mean_abs) if mean_abs else np.nan
        sign_cons = float(max((g > 0).sum(), (g < 0).sum()) / len(g))
        imax = np.unravel_index(np.nanargmax(cells), cells.shape)
        nbv = []
        for di, dj in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            a, b = imax[0] + di, imax[1] + dj
            if 0 <= a < cells.shape[0] and 0 <= b < cells.shape[1]:
                nbv.append(cells[a, b])
        iso = bool((cells[imax] - max(nbv)) > np.std(g)
                   and (cells > g.mean() + g.std()).sum() == 1)
        smooth = bool(sign_cons >= 0.78 and not iso)
        log('  §34 网格 %s：OOS IC 范围 [%+.4f, %+.4f]  同号比例 %.2f  '
            '粗糙度 %.2f  孤立高点=%s -> %s'
            % (name, np.nanmin(cells), np.nanmax(cells), sign_cons, rough, iso,
               'SMOOTH' if smooth else 'PARAMETER_FRAGILE'))
        for i, va in enumerate(vala):
            log('    ' + '  '.join('%s' % ('%+.3f' % cells[i, j])
                                   for j in range(len(valb))))
        rows.append(dict(kind='grid_summary', grid=name, axis='', value='',
                         param_a='', param_b='',
                         train_ic=np.nan, train_icir=np.nan,
                         oos_ic=round(float(np.nanmax(cells)), 5),
                         oos_icir=round(rough, 4), oos_days=len(g),
                         sec=np.nan, share_S3=round(sign_cons, 4),
                         share_S4=1.0 if iso else 0.0,
                         share_S7=round(float(np.nanmin(cells)), 5),
                         share_S8=1.0 if smooth else 0.0))
        return cells

    grid2('ma_x_trend', 'ma_win', 'ma_win', PREREG['grid_ma'],
          'trend_win', 'trend_win', PREREG['grid_trend_win'])
    grid2('rs_x_dd', 'rs_win', 'rs_win', PREREG['grid_rs_win'],
          'dd_mild', 'dd_mild', PREREG['grid_dd'])

    # ---------------- Age 分界扰动（§33 Age 15/20/30） ----------------
    log('  §33 Age 分界扰动（趋势年龄 年轻 vs 年长，T+%d 平均收益）' % PRIMARY)
    trend = (S0 >= 1) & (S0 <= 8) & (AGE > 0) & np.isfinite(Y)
    for t in PREREG['grid_age']:
        young = trend & (AGE <= t)
        old = trend & (AGE > t)
        my = float(np.nanmean(Y[young]))
        mo = float(np.nanmean(Y[old]))
        ic = daily_ic(np.where(trend, -AGE, np.nan).astype(np.float32), Y, VAL,
                      days=np.flatnonzero(ph_d == 'OOS'))
        r = ic_stats(ic, PRIMARY)
        rows.append(dict(kind='age_cut', grid='age', axis='age_cut', value=t,
                         param_a='', param_b='', tag='AGE<=%d vs >%d' % (t, t),
                         train_ic=np.nan, train_icir=round(r['icir'], 4),
                         oos_ic=round(my - mo, 5), oos_icir=round(r['mean_ic'], 5),
                         oos_days=int(min(young.sum(), old.sum())), sec=np.nan,
                         share_S3=round(my, 5), share_S4=round(mo, 5),
                         share_S7=float(young.sum()), share_S8=float(old.sum())))
        log('    Age 分界 %-3s  E[fwd20|young]=%+.3f%%  E[fwd20|old]=%+.3f%%  '
            '差=%+.3f%%  OOS IC(年龄)=-AGE vs fwd20 %+.4f (ICIR %.3f)'
            % (t, my * 100, mo * 100, (my - mo) * 100, r['mean_ic'], r['icir']))

    pd.DataFrame(rows).to_csv(os.path.join(HERE, 'tl01_parameter_grid.csv'),
                              index=False, encoding='utf-8-sig')
    log('  已写 tl01_parameter_grid.csv')
    log('完成  %.0fs' % (time.time() - t0))
    log.save()
    print('DONE')


if __name__ == '__main__':
    main()
