# -*- coding: utf-8 -*-
"""
H-IND-DIFFUSION-01  P2：行业日度状态与扩散特征（STEP 4-8 / §9-§20）

隔离声明：本模块自包含，不 import 任何既有研究模块。

产出（§57）
    industry_daily_state.parquet        行业 × 日 原始状态（Momentum / Breadth / ΔBreadth /
                                        Accel / Leader / Gap / Dispersion / RS / State / Quadrant）
    industry_diffusion_features.parquet 扩散特征的横截面分位（同日跨行业，仅 eligible 行业参与）
                                        + diffusion_score（等权 4 分量，见 DERIVED）
    data/panel/_market.npz              市场层控制变量（§24：Market Breadth / Market Momentum）

口径唯一化（§59）
    全部派生定义冻结在 hid_common.DERIVED（derived_hash）。本模块不做任何参数搜索。

PIT（§50 G1）
    全部特征只用决策日 k 及之前的收盘信息；前向收益由 P1 提供，本模块不触碰。
    行业归属为 PIT（P1 的 _code_ind_l1/_code_ind_l2），逐日生效。
"""
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd

from hid_common import (DATA, PREREG, DERIVED, mklog, pload, pload_day,
                        pload_meta, psave_meta_side, rows_by_code,
                        IND_FP, FEAT_FP, prereg_hash, derived_hash)

LOG = mklog('industry')

PAN = os.path.join(DATA, 'panel')
MKT_FP = os.path.join(PAN, '_market.npz')

MIN_N = int(PREREG['ind_n_min'])          # 20（§7 / §40 硬门槛）
ALT_N = tuple(PREREG['ind_n_alt'])        # (30, 50)
BR_W = tuple(PREREG['breadth_wins'])      # (1,3,5,10,20)
MOM_W = (5, 10, 20, 40, 60)               # 40 为 §37 grid_mom_win 所需（P1 未提供，本模块现算）
MOM_W_P1 = tuple(PREREG['ind_mom_wins'])  # (5,10,20,60)
ST_W = int(DERIVED['state_breadth_win'])  # 20
CHG_W = int(DERIVED['state_chg_win'])     # 5
ACC_S = int(PREREG['accel_short'])        # 3
ACC_L = tuple(PREREG['accel_long'])       # (5,10)
DISP_W = tuple(DERIVED['disp_wins'])      # (1,3,5,10)
LP = tuple(PREREG['leader_pcts'])         # (0.05,0.10,0.20)
LP_MAIN = float(DERIVED['leader_pct_main'])
RS_W = int(DERIVED['rs_win_main'])        # 20
RS_CHG = int(DERIVED['rs_chg_win'])       # 5
NARROW_THR = float(PREREG['narrow_gap_thr'])

NEED = ['valid', 'close', 'ret_1', 'ret_3', 'ret_5', 'ret_10', 'ret_20', 'ret_60',
        'cl_ma20', 'cl_ma60', 'circ_mv', 'total_mv', 'amount']


# ══════════════════════════════════════════════════════════════ 工具

def _shift(a, w):
    """按交易日索引平移（w>0 取 w 日前）。数组长度 = NCAL，缺失处 NaN。"""
    out = np.full_like(a, np.nan, dtype=np.float64)
    if w > 0:
        out[w:] = a[:-w]
    else:
        out[:] = a
    return out


def _safe_div(num, den):
    return np.where(np.asarray(den) > 0, np.asarray(num) / np.where(np.asarray(den) > 0, den, 1.0), np.nan)


def _share(sel, cond, denom):
    """给定 sel（分子分母共同的样本掩码）与 cond（布尔），返回条件占比。"""
    num = np.where(sel & cond, 1.0, 0.0).sum(0)
    return _safe_div(num, denom)


# ══════════════════════════════════════════════════════════════ 单行业特征

def industry_features(rows, code, indm, P, NCAL, with_disp, with_leader):
    """返回长度 NCAL 的 1D 数组字典（原始状态）。

    indm : (NCODES, NCAL) int16 行业编码矩阵（PIT）
    """
    L = {}
    m = len(rows)
    sub = (np.asarray(indm[rows, :]) == code)                       # (m, NCAL)
    val = (np.asarray(P['valid'][rows, :]) == 1)
    base = sub & val

    def M(col):
        return np.asarray(P[col][rows, :], dtype=np.float32)

    # 现算 ret_40（§37 grid_mom_win=40；P1 未提供该窗口）
    cl = M('close')
    with np.errstate(invalid='ignore', divide='ignore'):
        r40 = cl / _shift(cl, 40) - 1.0
    r40 = np.where(np.isfinite(r40), r40, np.nan)

    R = {}
    for w in (1, 3, 5, 10, 20, 60):
        R[w] = M('ret_%d' % w)
    R[40] = r40

    L['n_valid'] = base.sum(0).astype(np.float64)
    L['n_used'] = (base & np.isfinite(R[20])).sum(0).astype(np.float64)

    # ---- §9 Industry Momentum（EW / VW / MED）----
    # 注：RS 参照（§18）需要 1/3/5/10/20 全部窗口的行业 EW，故此处对并集一次算完，
    # 仅 MOM_W 的窗口落成 mom_* 列。
    wv = M('circ_mv')
    wfall = M('total_mv')
    wv = np.where(np.isfinite(wv) & (wv > 0), wv, wfall)
    ewmap = {}
    for w in sorted(set(MOM_W) | set(BR_W)):
        r = R[w]
        sel = base & np.isfinite(r)
        n = sel.sum(0).astype(np.float64)
        ew = _safe_div(np.where(sel, r, 0.0).sum(0), n)
        ewmap[w] = ew
        if w not in MOM_W:
            continue
        selw = sel & np.isfinite(wv) & (wv > 0)
        ww = np.where(selw, wv, 0.0)
        vw = _safe_div((ww * np.where(selw, r, 0.0)).sum(0), ww.sum(0))
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            med = np.nanmedian(np.where(sel, r, np.nan), axis=0)
        L['mom_ew_%d' % w] = ew
        L['mom_vw_%d' % w] = vw
        L['mom_med_%d' % w] = np.asarray(med, dtype=np.float64)

    # ---- §10 / §13 Breadth（UP 层，窗口 1/3/5/10/20）----
    for w in BR_W:
        r = R[w]
        sel = base & np.isfinite(r)
        L['up_%d' % w] = _share(sel, r > 0, sel.sum(0).astype(np.float64))

    # ---- §13 强股 Breadth：AboveMA20 / AboveMA60 ----
    for tag, col in (('br_ma20', 'cl_ma20'), ('br_ma60', 'cl_ma60')):
        a = M(col)
        sel = base & np.isfinite(a)
        L[tag] = _share(sel, a > 0, sel.sum(0).astype(np.float64))

    # ---- §18 Relative Strength Breadth（个股 - 行业 EW）----
    for w in (1, 3, 5, 10, 20):
        r = R[w]
        sel = base & np.isfinite(r)
        ew = ewmap[w]
        ex = r - np.broadcast_to(ew, r.shape)
        selx = sel & np.isfinite(ex)
        L['rsb_%d' % w] = _share(selx, ex > 0, selx.sum(0).astype(np.float64))

    # ---- §11 Breadth Change（对 Breadth20）----
    b20 = L['up_%d' % ST_W]
    for w in PREREG['breadth_chg_wins']:
        L['dchg_%d' % w] = b20 - _shift(b20, w)
    L['dchg_20'] = b20 - _shift(b20, 20)          # §37 grid_breadth_win=20 所需
    # ---- §12 Breadth Acceleration（预注册固定公式）----
    L['accel20'] = (b20 - _shift(b20, ACC_S)) - (_shift(b20, ACC_L[0]) - _shift(b20, ACC_L[1]))

    # ---- §18 RS_BreadthChange ----
    rsb20 = L['rsb_%d' % RS_W]
    L['rsb_chg_%d' % RS_CHG] = rsb20 - _shift(rsb20, RS_CHG)

    # ---- §17 Cross-sectional Dispersion ----
    if with_disp:
        for w in DISP_W:
            r = R[w]
            sel = base & np.isfinite(r)
            z = np.where(sel, r, np.nan)
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                sd = np.nanstd(z, axis=0)
                p = np.nanpercentile(z, [DERIVED['disp_band'][0], DERIVED['disp_band'][1]],
                                     axis=0)
            L['disp_sd_%d' % w] = np.asarray(sd, dtype=np.float64)
            L['disp_rng_%d' % w] = np.asarray(p[1] - p[0], dtype=np.float64)

    # ---- §14 / §15 / §16 Leader Breadth / Concentration / Diffusion Gap ----
    if with_leader:
        r20, r5 = R[20], R[5]
        selr = base & np.isfinite(r20)
        sel5 = base & np.isfinite(r5)
        nr = selr.sum(0).astype(np.float64)
        # 取负后升序排序 ⇒ 秩 0 = ret_20 最高（行业动量最强）。
        # 非成员/缺失置 +inf，排到尾部，不影响有限样本的秩。
        X = np.where(selr, -r20, np.inf)
        order = np.argsort(X, axis=0, kind='stable')                 # (m, NCAL)
        rk = np.empty(order.shape, dtype=np.int64)
        vals = np.broadcast_to(np.arange(m, dtype=np.int64)[:, None], order.shape)
        np.put_along_axis(rk, order, vals, axis=0)                   # 秩 0 = 动量最强

        # 龙头集合 = 秩 < ceil(p * n)
        for pct in LP:
            ktop = np.maximum(np.ceil(pct * nr), 1.0)
            lead = selr & (rk < np.broadcast_to(ktop, rk.shape)) & sel5
            bot = selr & (rk >= np.broadcast_to(ktop, rk.shape)) & sel5
            L['lb_%03d' % round(pct * 100)] = _share(lead, r5 > 0, lead.sum(0).astype(np.float64))
            if abs(pct - LP_MAIN) < 1e-9:
                L['bb_%03d' % round(pct * 100)] = _share(bot, r5 > 0,
                                                         bot.sum(0).astype(np.float64))

        # §15 集中度：正贡献中 Top-p 的占比（p = 5% / 10%）
        posden = np.where(selr & (r20 > 0), r20, 0.0).sum(0)
        for pct in (0.05, 0.10):
            ktop = np.maximum(np.ceil(pct * nr), 1.0)
            sel_top = selr & (rk < np.broadcast_to(ktop, rk.shape)) & (r20 > 0)
            L['contrib_top%dpct' % round(pct * 100)] = _safe_div(
                np.where(sel_top, r20, 0.0).sum(0), posden)
        L['leader_conc'] = L['contrib_top5pct']

        # §16 DiffusionGap
        up5 = L['up_5']
        L['diff_gap'] = L['lb_%03d' % round(LP_MAIN * 100)] - up5
        L['diff_gap2'] = L['lb_%03d' % round(LP_MAIN * 100)] - L['bb_%03d' % round(LP_MAIN * 100)]

    return L


# ══════════════════════════════════════════════════════════════ 单层级主循环

def build_level(level, indm, NCODES, NCAL, P):
    rows_by_code_ = rows_by_code(indm, NCODES)
    codes_used = sorted(rows_by_code_)
    with_disp = (level == 'L1')
    with_leader = True
    n_ind = len(codes_used)
    LOG('  %s：出现过的行业 %d 个；逐行业计算（Dispersion %s）'
        % (level, n_ind, 'on' if with_disp else 'off（L2 仅稳健性抽检）'))

    # 收集为 (n_ind, NCAL)
    keep = None
    K = {}
    t0 = time.time()
    for j, c in enumerate(codes_used):
        L = industry_features(rows_by_code_[c], c, indm, P, NCAL,
                              with_disp=with_disp, with_leader=with_leader)
        if keep is None:
            keep = list(L.keys())
        for k in keep:
            K.setdefault(k, np.full((n_ind, NCAL), np.nan, dtype=np.float64))[j, :] = L[k]
        if (j + 1) % 40 == 0:
            LOG('    ... %d / %d（%.1fs）' % (j + 1, n_ind, time.time() - t0))
    LOG('    行业特征完成 %d 个（%.1fs）' % (n_ind, time.time() - t0))

    n_used = K['n_used']
    elig = (n_used >= MIN_N)                              # §7 硬门槛
    # §16 NARROW_LEADERSHIP（预注册规则）
    narrow = np.zeros((n_ind, NCAL), dtype=np.float64)
    if 'diff_gap' in K:
        g = K['diff_gap']
        d5 = K['dchg_%d' % CHG_W]
        f = np.isfinite(g) & np.isfinite(d5)
        narrow[f] = ((g[f] >= NARROW_THR) & (d5[f] <= 0)).astype(np.float64)

    # ---- §19 扩散状态（同日跨行业中位数切分，仅 eligible 行业参与）----
    b = np.where(elig, K['up_%d' % ST_W], np.nan)
    d = np.where(elig, K['dchg_%d' % CHG_W], np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        mb = np.nanmedian(b, axis=0)
        md = np.nanmedian(d, axis=0)
        mq = np.nanmedian(np.where(elig, K['mom_ew_%d' % DERIVED['quad_mom_win']], np.nan), axis=0)
    hi = b > np.broadcast_to(mb, b.shape)
    rs_ = d > np.broadcast_to(md, d.shape)
    state = np.full((n_ind, NCAL), -1, dtype=np.int8)
    ok = np.isfinite(b) & np.isfinite(d)
    state[ok & (~hi) & rs_] = 1        # D1 Low  -> Rising
    state[ok & hi & rs_] = 2           # D2 High -> Rising
    state[ok & hi & (~rs_)] = 3        # D3 High -> Falling
    state[ok & (~hi) & (~rs_)] = 4     # D4 Low  -> Falling

    # ---- §20 四象限 ----
    qm = np.where(elig, K['mom_ew_%d' % DERIVED['quad_mom_win']], np.nan)
    mhi = qm > np.broadcast_to(mq, qm.shape)
    quad = np.full((n_ind, NCAL), -1, dtype=np.int8)
    okq = np.isfinite(qm) & np.isfinite(d)
    quad[okq & mhi & rs_] = 1          # A 动量↑ 广度↑
    quad[okq & mhi & (~rs_)] = 2       # B 动量↑ 广度↓
    quad[okq & (~mhi) & rs_] = 3       # C 动量↓ 广度↑
    quad[okq & (~mhi) & (~rs_)] = 4    # D 动量↓ 广度↓

    return codes_used, K, elig, narrow, state, quad


# ══════════════════════════════════════════════════════════════ main

def main():
    argv = sys.argv[1:]
    only = 'L1'
    if '--level' in argv:
        only = argv[argv.index('--level') + 1]

    LOG.sep('=')
    LOG('H-IND-DIFFUSION-01  P2 行业日度状态与扩散特征  hash=%s derived=%s  %s'
        % (prereg_hash(), derived_hash(), time.strftime('%Y-%m-%d %H:%M:%S')))
    LOG.sep()

    meta = pload_meta()
    NCODES, NCAL = int(meta['NCODES']), int(meta['NCAL'])
    day = pload_day()
    td = np.asarray(day['dates']).astype(str)
    year = np.asarray(day['year'])
    assert len(td) == NCAL

    P = {c: pload(c) for c in NEED}

    levels = []
    if only in ('L1', 'BOTH'):
        levels.append(('L1', np.asarray(pload('_code_ind_l1'))))
    if only in ('L2', 'BOTH'):
        levels.append(('L2', np.asarray(pload('_code_ind_l2'))))

    frames = []
    state_dist = {}
    for level, indm in levels:
        indm = np.asarray(indm)
        LOG.sep()
        LOG('[STEP4-8] %s 行业状态 / Breadth / ΔBreadth / Accel / Leader / Gap / Dispersion / RS'
            % level)
        codes_used, K, elig, narrow, state, quad = build_level(level, indm, NCODES, NCAL, P)
        names = meta['l1_names'] if level == 'L1' else meta['l2_names']
        n_ind = len(codes_used)

        # ---- 组装长表 ----
        kk = np.tile(np.arange(NCAL, dtype=np.int64), n_ind)
        ii = np.repeat(np.arange(n_ind, dtype=np.int64), NCAL)
        d = {'ind_level': level,
             'ind_code': np.repeat(np.asarray(codes_used), NCAL),
             'ind_name': np.repeat(np.asarray([names[c - 1] for c in codes_used]), NCAL),
             'k': kk, 'trade_date': td[kk]}
        for key, arr in K.items():
            d[key] = arr[ii, kk]
        d['eligible'] = elig[ii, kk].astype(np.int8)
        d['low_sample'] = (K['n_used'][ii, kk] < MIN_N).astype(np.int8)
        for an in ALT_N:
            d['low_sample%d' % an] = (K['n_used'][ii, kk] < an).astype(np.int8)
        d['narrow_leadership'] = narrow[ii, kk].astype(np.int8)
        d['state'] = state[ii, kk]
        d['quadrant'] = quad[ii, kk]
        df = pd.DataFrame(d)
        df['state'] = df['state'].map({-1: '', 1: 'D1', 2: 'D2', 3: 'D3', 4: 'D4'})
        df['quadrant'] = df['quadrant'].map({-1: '', 1: 'A', 2: 'B', 3: 'C', 4: 'D'})
        frames.append(df)

        # ---- 日志 ----
        nel = int(elig.sum())
        LOG('  eligible(行业日, N>=%d) %d / %d (%.2f%%)；LOW_SAMPLE 行业日 %d'
            % (MIN_N, nel, n_ind * NCAL, 100.0 * nel / (n_ind * NCAL),
               int((~elig).sum())))
        s = pd.Series(state[ii, kk]).map({-1: 'NA', 1: 'D1', 2: 'D2', 3: 'D3', 4: 'D4'})
        state_dist[level] = s.value_counts().to_dict()
        LOG('  §19 状态分布：%s' % dict(state_dist[level]))
        q = pd.Series(quad[ii, kk]).map({-1: 'NA', 1: 'A', 2: 'B', 3: 'C', 4: 'D'})
        LOG('  §20 四象限分布：%s' % dict(q.value_counts().to_dict()))
        LOG('  §16 NARROW_LEADERSHIP 行业日 %d（占 eligible 的 %.2f%%）'
            % (int(narrow[elig].sum()), 100.0 * narrow[elig].sum() / max(nel, 1)))

    # ══ 落盘 1：industry_daily_state.parquet
    st = pd.concat(frames, ignore_index=True)
    st.to_parquet(IND_FP, index=False)
    LOG.sep()
    LOG('[SAVE] %s  shape=%s  cols=%d' % (os.path.basename(IND_FP), st.shape, st.shape[1]))

    # ══ 落盘 2：industry_diffusion_features.parquet（横截面分位 + 复合分）
    pct_cols = ['up_20', 'dchg_3', 'dchg_5', 'dchg_10', 'dchg_20', 'accel20',
                'lb_010', 'lb_020', 'rsb_chg_5', 'diff_gap', 'diff_gap2',
                'mom_ew_10', 'mom_ew_20', 'mom_ew_40', 'disp_sd_5', 'disp_rng_5',
                'contrib_top5pct']
    parts = []
    for level in st['ind_level'].unique():
        sub = st[st['ind_level'] == level].copy()
        ok = sub['eligible'] == 1
        for c in pct_cols:
            sub['pct_' + c] = np.nan
            if c not in sub.columns:
                continue
            sub.loc[ok, 'pct_' + c] = sub.loc[ok].groupby('trade_date')[c].rank(pct=True)
        parts.append(sub)
    fdf = pd.concat(parts, ignore_index=True)
    comp = [c for c in DERIVED['diffusion_score_components'] if c in fdf.columns]
    fdf['diffusion_score'] = fdf[comp].mean(axis=1, skipna=False)
    cols = (['ind_level', 'ind_code', 'ind_name', 'k', 'trade_date', 'eligible',
             'n_used', 'n_valid'] + ['pct_' + c for c in pct_cols]
            + ['diffusion_score'])
    fdf = fdf[cols]
    fdf.to_parquet(FEAT_FP, index=False)
    LOG('[SAVE] %s  shape=%s  cols=%d（分量 %s，等权）'
        % (os.path.basename(FEAT_FP), fdf.shape, fdf.shape[1], comp))

    # ══ 落盘 3：市场层控制变量（§24 用；本模块顺带产出，避免下游重复全市场扫描）
    v = np.asarray(P['valid']) == 1
    nv = v.sum(0).astype(np.float64)
    mkt = {'mkt_n_valid': nv}
    for w in (5, 20):
        r = np.asarray(P['ret_%d' % w], dtype=np.float32)
        sel = v & np.isfinite(r)
        mkt['mkt_breadth_%d' % w] = _share(sel, r > 0, sel.sum(0).astype(np.float64))
        mkt['mkt_ret_%d' % w] = _safe_div(np.where(sel, r, 0.0).sum(0),
                                          sel.sum(0).astype(np.float64))
    a = np.asarray(P['cl_ma20'], dtype=np.float32)
    sel = v & np.isfinite(a)
    mkt['mkt_breadth_ma20'] = _share(sel, a > 0, sel.sum(0).astype(np.float64))
    mkt['mkt_hs300'] = np.asarray(day['hs300'], dtype=np.float64)
    mkt['mkt_regime'] = np.asarray(day['regime'])
    np.savez(MKT_FP, **mkt)
    LOG('[SAVE] %s（市场层控制：n / breadth5 / breadth20 / MA20广度 / EW收益 / HS300 / regime）'
        % os.path.basename(MKT_FP))

    psave_meta_side('_meta_p2.json', {
        'p2_built_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'p2_prereg_hash': prereg_hash(), 'p2_derived_hash': derived_hash(),
        'p2_rows_state': int(len(st)), 'p2_rows_feat': int(len(fdf)),
        'p2_state_dist': state_dist,
        'p2_min_n': MIN_N, 'p2_narrow_thr': NARROW_THR,
        'p2_feat_components': comp,
    })
    LOG.sep('=')
    LOG('DONE  P2')


if __name__ == '__main__':
    main()
