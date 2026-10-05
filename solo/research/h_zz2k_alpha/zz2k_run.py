# -*- coding: utf-8 -*-
"""H-ZZ2K-A1 主运行层 —— 中证2000增强 Alpha V1 全部计算与 CSV 产物.

口径唯一来源: H_ZZ2K_ALPHA_V1_SPEC.md (v1.1, 已冻结).
阈值全部取自 zz2k_common.PREREG / 模块常量, 本文件不含结果依赖开关.

用法
    python -u zz2k_run.py            # 全量 (B=2000, 全部 §13 扰动格点)
    python -u zz2k_run.py simple     # 冒烟: B=200, 仅基线扰动格点
    python -u zz2k_run.py force      # 忽略已有 CSV 检查点, 全量重算

退出码 (§16.5)
    SPEC 不一致 -> 2 ; future_column_scan 失败 -> 3 ; 正常 -> 0
"""
import os
import sys
import json
import time

import numpy as np
import pandas as pd

import zz2k_common as R

SEP = '\u2550' * 78
SUB = '\u2500' * 78

_ARG = [a.lower() for a in sys.argv[1:]]
SIMPLE = 'simple' in _ARG
FORCE = ('force' in _ARG) or ('--force' in sys.argv[1:])
B_NULL = 200 if SIMPLE else R.NULL_B
SEGS = ('ALL',) + ('PRE', 'IS', 'VALID', 'OOS')
SEG_ONLY = ('PRE', 'IS', 'VALID', 'OOS')
ROUNDS = 2 if SIMPLE else R.NULL_ROUNDS

_LOG = None


def lg(*a):
    if _LOG is not None:
        _LOG(*a)


# ================================================================== helpers
def new(store, **kw):
    store.append(kw)


def build_A35(p, g, bench, basic, ret, u, l1s, W_RM, W_VOL, W_VOL2, lg=None,
              cache=False):
    """A3 + A5 原始因子 (池相关). cache=True 时落盘 (仅基线窗口)."""
    fp = os.path.join(R.DATA, 'A35_%s_rm%d_v%d_%d_raw.npz'
                      % (p, W_RM, W_VOL, W_VOL2))
    if cache and os.path.exists(fp):
        z = np.load(fp, allow_pickle=True)
        F = {n: z[n] for n in z.files}
        if lg:
            lg('A35 %s loaded cache (%d factors)' % (p, len(F)))
        return F
    t0 = time.time()
    A3, A5, _, _ = R.build_A3_A5(g, bench, basic, ret, u, l1s, lg=lg,
                                 W_RM=W_RM, W_VOL=W_VOL, W_VOL2=W_VOL2)
    F = {}
    F.update(A3)
    F.update(A5)
    if cache:
        np.savez(fp, **F)
    if lg:
        lg('A35 %s built rm%d/v%d/%d %d factors (%.1fs)'
           % (p, W_RM, W_VOL, W_VOL2, len(F), time.time() - t0))
    return F


def mean_abs_corr(panels, u, min_n=30):
    """逐日横截面平均 |corr| (off-diagonal)."""
    names = list(panels)
    G = len(names)
    if G < 2:
        return np.nan, 0
    S, N = panels[names[0]].shape
    off = ~np.eye(G, dtype=bool)
    tot, days = 0.0, 0
    for t in range(N):
        m = u[:, t]
        for n in names:
            m = m & np.isfinite(panels[n][:, t])
        if int(m.sum()) < max(min_n, G * 5):
            continue
        Z = np.vstack([panels[n][m, t] for n in names]).astype('float64')
        sd = Z.std(axis=1, keepdims=True)
        if (sd <= 0).any():
            continue
        Z = (Z - Z.mean(axis=1, keepdims=True)) / sd
        C = np.corrcoef(Z)
        if not np.all(np.isfinite(C)):
            continue
        tot += float(np.abs(C[off]).mean())
        days += 1
    return (tot / days if days else np.nan), days


def nan_panel(shape):
    return np.full(shape, np.nan, dtype='float32')


def pool_orth(p, Fp, u, M0, cov, lg):
    """类内 Löwdin → C_k; 类间 cross_orth → C_k^perp; 附正交化诊断."""
    ORTH, C, gd = {}, {}, {}
    for k in R.GROUPS:
        fk = {n: Fp[n] for n in R.GROUP_FACTORS[k]}
        keep, drop = R.orth_input(fk, {n: cov[n] for n in fk}, lg=lg,
                                  tag='%s/%s' % (p, k))
        if not keep:
            C[k] = nan_panel(Fp[R.GROUP_FACTORS[k][0]].shape)
            gd[k] = {'keep': [], 'drop': drop, 'days': 0, 'trunc': 0,
                     'trunc_ratio': np.nan, 'mac_before': np.nan,
                     'mac_after': np.nan, 'gs_f': None, 'gs_r': None}
            continue
        std = R.standardize(keep, u)
        stats = {}
        lo = R.lowdin(std, u, lg=lg, stats=stats)
        mac_b, _ = mean_abs_corr(std, u)
        mac_a, _ = mean_abs_corr(lo, u)
        C[k] = R.group_composite(lo, u, lg=lg, tag='%s/C_%s' % (p, k))
        gd[k] = {'keep': sorted(keep), 'drop': drop,
                 'days': int(stats.get('days', 0)),
                 'trunc': int(stats.get('truncated_dirs', 0)),
                 'trunc_ratio': stats.get('trunc_ratio', np.nan),
                 'mac_before': mac_b, 'mac_after': mac_a,
                 'gs_f': R.group_composite(R.gs_orth(std, u, reverse=False), u),
                 'gs_r': R.group_composite(R.gs_orth(std, u, reverse=True), u)}
        ORTH.update(lo)
    Cperp = R.cross_orth(C, M0, u, lg=lg)
    return ORTH, C, Cperp, gd


def build_composites(C, u):
    eq, _ = R.composite_from_rank(C, u, {k: 1.0 / len(C) for k in C})
    pr, _ = R.composite_from_rank(C, u, R.COMP_PRIOR)
    return eq, pr


def null_rounds(Rf, Rl, u, kind, B, rounds, seed):
    """§12 把 B 次抽样拆成 rounds 轮 (每轮 B/rounds), 报中位 p 与最小 resolution."""
    per = max(1, int(B) // max(1, rounds))
    obs, ps, res = [], [], []
    for i in range(rounds):
        d = R.null_test(Rf, Rl, u, kind=kind, B=per, seed=seed + 101 * i)
        obs.append(d['obs'])
        ps.append(d['p'])
        res.append(d['resolution'])
    pv = np.array([p for p in ps if np.isfinite(p)], dtype='float64')
    rv = np.array([r for r in res if np.isfinite(r)], dtype='float64')
    return {'obs': float(np.nanmean(obs)) if obs else np.nan,
            'p': float(np.median(pv)) if pv.size else np.nan,
            'p_min': float(pv.min()) if pv.size else np.nan,
            'b': per * rounds, 'rounds': rounds, 'b_round': per,
            'resolution': float(rv.min()) if rv.size else np.nan}


# ================================================================== per pool
def run_pool(p, FR, A35, g, basic, bench, ret, lab, l1s, uni, elig, dates,
             mem, out, lg):
    """单口径全部计算; 结果 append 到 out 的各列表."""
    u = uni[p]
    Fp = dict(FR)
    Fp.update(A35[p])
    cov = R.factor_coverage(Fp, u, dates=dates)
    M0 = R.build_M0(g, basic, bench, ret, l1s, u, lg=lg)
    ORTH, C, Cperp, gd = pool_orth(p, Fp, u, M0, cov, lg)
    comp_eq, comp_prior = build_composites(C, u)

    # -------- label rank 面板 (IC 用) ----------------------------------
    LR = {}
    for lb in R.LABELS:
        for h in R.HORIZONS:
            LR[(lb, h)] = R.xs_rank_pct(lab[p][lb][h], u)

    # -------- 序列登记 --------------------------------------------------
    SER = {}                                   # name -> (kind, group, raw_panel, orth_panel)
    for n in R.ALL_FACTORS:
        SER[n] = ('factor', n.split('_')[0], Fp[n], ORTH.get(n))
    for k in R.GROUPS:
        SER['C_%s' % k] = ('group', k, None, C[k])
        SER['Cperp_%s' % k] = ('perp', k, None, Cperp[k])
    SER['COMP_EQ'] = ('composite', 'EQ', None, comp_eq)
    SER['COMP_PRIOR'] = ('composite', 'PRIOR', None, comp_prior)

    # -------- §7 IC 全表 (R-ALL / R-DEDUP × 5 段) ----------------------
    ICARR = {}                                 # name -> 日频 IC (R-ALL, h5, y_ex_zz)
    SIC = {}                                   # name -> 判决用标量
    for name, (kind, grp, praw, porth) in SER.items():
        pans = [('RAW', praw)] if praw is not None else []
        pans.append(('ORTH', porth))
        SIC[name] = {}
        for pon, pan in pans:
            if pan is None:
                # 门禁剔除: 仍逐行登记 (SPEC §16.4 含 0 覆盖因子)
                for h in R.HORIZONS:
                    for lb in R.LABELS:
                        for ep in ('R_ALL', 'R_DEDUP'):
                            for s in SEGS:
                                new(out['ic'], pool=p, series=name, kind=kind,
                                    group=grp, panel=pon, horizon=h, label=lb,
                                    episode=ep, seg=s, seg_days=0,
                                    IC_mean=np.nan, IC_std=np.nan, ICIR=np.nan,
                                    t_NW=np.nan, pos_ratio=np.nan,
                                    n_mean=np.nan, n_days=0,
                                    cov=cov.get(name, {}).get('cov', np.nan),
                                    coverage_flag=R.coverage_flag(cov.get(name, {})))
                continue
            Rf = R.xs_rank_pct(pan, u)
            Rd = R.dedup_rank(pan, u, gap=5, q=5)[1]
            for h in R.HORIZONS:
                for lb in R.LABELS:
                    Rl = LR[(lb, h)]
                    ic, cnt = R.daily_ic(Rf, Rl, min_n=R.IC_MIN_N)
                    dic, dcnt, _ = R.dedup_ic_rank(Rd, Rl, min_n=5)
                    if (pon == 'ORTH' and h == R.PRIMARY_H
                            and lb == 'y_ex_zz'):
                        ICARR[name] = ic
                    for ep, (eic, ecnt) in (('R_ALL', (ic, cnt)),
                                            ('R_DEDUP', (dic, dcnt))):
                        r = R.ic_row(eic, ecnt)
                        segs = {'ALL': r}
                        for s in SEG_ONLY:
                            segs[s] = R.seg_ic(eic, ecnt, dates, p, s)
                        if pon == 'ORTH' and ep == 'R_ALL':
                            for s in ('ALL', 'IS', 'VALID', 'OOS'):
                                SIC[name]['%s_%s' % (s, 'ic')] = segs[s]['IC_mean']
                                SIC[name]['%s_%s' % (s, 't')] = segs[s]['t_NW']
                        if pon == 'ORTH' and ep == 'R_DEDUP':
                            for s in ('IS', 'ALL'):
                                SIC[name]['dedup_%s_ic' % s] = segs[s]['IC_mean']
                        for s in SEGS:
                            sr = segs[s]
                            new(out['ic'], pool=p, series=name, kind=kind,
                                group=grp, panel=pon, horizon=h, label=lb,
                                episode=ep, seg=s,
                                seg_days=int(sr.get('seg_days',
                                                     sr['n_days'])),
                                IC_mean=sr['IC_mean'], IC_std=sr['IC_std'],
                                ICIR=sr['ICIR'], t_NW=sr['t_NW'],
                                pos_ratio=sr['pos_ratio'], n_mean=sr['n_mean'],
                                n_days=sr['n_days'],
                                cov=cov.get(name, {}).get('cov', np.nan),
                                coverage_flag=R.coverage_flag(cov.get(name, {})))
        lg('  ic %-16s done (pool=%s)' % (name, p))

    # -------- §9 分层全表 ----------------------------------------------
    QSTAT = {}
    for name, (kind, grp, praw, porth) in SER.items():
        pan = porth if porth is not None else praw
        if pan is None or not np.isfinite(pan).any():
            continue
        for h in R.HORIZONS:
            for lb in R.LABELS:
                Y = lab[p][lb][h]
                for q in R.Q_GROUPS:
                    rows, sp = R.quantile_stats(pan, Y, u, horizon=h, q=q)
                    mon = sp['monotonicity']
                    for r in rows:
                        new(out['q'], pool=p, series=name, kind=kind, group=grp,
                            horizon=h, label=lb, q=q, q_group=r['q_group'],
                            n_obs=r['n_obs'], n_mean=r['n_mean'],
                            gross=r['gross'], net0=r['net0'], net15=r['net15'],
                            net30=r['net30'], net50=r['net50'],
                            win30=r['win30'], median=r['median'],
                            max_drawdown=r['max_drawdown'],
                            profit_factor=r['profit_factor'],
                            turnover=r['turnover'], n_periods=r['n_periods'],
                            monotonicity=mon,
                            gross_spread=sp['gross_spread'],
                            spread_net0=sp['spread_net0'],
                            spread_net15=sp['spread_net15'],
                            spread_net30=sp['spread_net30'],
                            spread_net50=sp['spread_net50'])
                    QSTAT[(name, h, lb, q)] = sp
        lg('  quantile %-16s done (pool=%s)' % (name, p))

    # -------- §8.4 正交化诊断 ------------------------------------------
    for name, (kind, grp, praw, porth) in SER.items():
        if praw is None:
            continue
        g0 = gd[grp] if grp in gd else {}
        ic_raw = SIC.get(name, {}).get('IS_ic', np.nan)  # placeholder
        new(out['orthf'], pool=p, level='factor', group=grp, series=name,
            kind=kind, keep_in_orth=int(porth is not None),
            mac_before=g0.get('mac_before', np.nan),
            mac_after=g0.get('mac_after', np.nan),
            trunc_ratio=g0.get('trunc_ratio', np.nan),
            days=g0.get('days', 0),
            drop=','.join(g0.get('drop', []) or []),
            cov=cov.get(name, {}).get('cov', np.nan),
            coverage_flag=R.coverage_flag(cov.get(name, {})))
    for k in R.GROUPS:
        g0 = gd[k]
        icc_f, icc_r = np.nan, np.nan
        if g0.get('gs_f') is not None:
            icf, _ = R.daily_ic(R.xs_rank_pct(g0['gs_f'], u),
                                LR[('y_ex_zz', R.PRIMARY_H)], min_n=R.IC_MIN_N)
            icr, _ = R.daily_ic(R.xs_rank_pct(g0['gs_r'], u),
                                LR[('y_ex_zz', R.PRIMARY_H)], min_n=R.IC_MIN_N)
            icc_f = R.nw_t(icf)[1]
            icc_r = R.nw_t(icr)[1]
        sign_f = np.sign(SIC.get('C_%s' % k, {}).get('IS_ic', np.nan))
        sign_r = np.sign(icc_r) if np.isfinite(icc_r) else np.nan
        osen = int(np.isfinite(icc_f) and np.isfinite(icc_r)
                   and np.sign(icc_f) != sign_r)
        new(out['orthg'], pool=p, group=k, name='C_%s' % k,
            kind='group', days=g0.get('days', 0),
            trunc_ratio=g0.get('trunc_ratio', np.nan),
            mac_before=g0.get('mac_before', np.nan),
            mac_after=g0.get('mac_after', np.nan),
            gs_fwd_t=icc_f, gs_rev_t=icc_r, order_sensitive=osen,
            IC_C_IS=SIC.get('C_%s' % k, {}).get('IS_ic', np.nan),
            IC_Cperp_IS=SIC.get('Cperp_%s' % k, {}).get('IS_ic', np.nan),
            keep=len(g0.get('keep', [])), drop=len(g0.get('drop', [])),
            drop_list=','.join(g0.get('drop', []) or []))

    # -------- §11 增量回归 ---------------------------------------------
    Rc = {k: R.xs_rank_pct(C[k], u) for k in R.GROUPS}
    Rp = {k: R.xs_rank_pct(Cperp[k], u) for k in R.GROUPS}
    Ro = {n: R.xs_rank_pct(ORTH[n], u) for n in ORTH}
    INC = {}
    for h in (5, 10):
        Yr = LR[('y_ex_zz', h)]
        inc, cq, cp = R.incremental(Yr, u, M0, C, Cperp, Rc, Rp, Ro)
        INC[h] = inc
        for mname, mv in inc.items():
            res = mv['res']
            Kx = len(mv['extra'])
            for j in range(Kx):
                new(out['inc'], pool=p, horizon=h, model=mname,
                    term=mv['extra'][j], dR2=mv['dR2'], n=mv['n'],
                    r2_mean=res['r2_mean'], beta=res.get('beta%d' % j, np.nan),
                    t_NW=res.get('t%d' % j, np.nan),
                    pos_ratio=res.get('pos%d' % j, np.nan))
            if Kx == 0:
                new(out['inc'], pool=p, horizon=h, model=mname, term='(base)',
                    dR2=0.0, n=mv['n'], r2_mean=res['r2_mean'], beta=np.nan,
                    t_NW=np.nan, pos_ratio=np.nan)
        lg('  incremental h=%d done (pool=%s)' % (h, p))

    ic_c_is = {k: SIC.get('C_%s' % k, {}).get('IS_ic', np.nan) for k in R.GROUPS}
    ic_cp_is = {k: SIC.get('Cperp_%s' % k, {}).get('IS_ic', np.nan)
                for k in R.GROUPS}
    IND = R.independence_flags(INC[R.PRIMARY_H], ic_c_is, ic_cp_is)

    # -------- §11.4 单因子独立增量 + 自动筛选 --------------------------
    srows = R.single_factor_incremental(LR[('y_ex_zz', R.PRIMARY_H)], u, M0,
                                        Rc, Ro, t_min=R.IC_T_MIN)
    ssel = {}
    for r in srows:
        ssel[r['factor']] = {'t_NW': r['t_NW'], 'dR2': r['dR2'],
                             'significant': r['significant']}
        new(out['ind'], pool=p, factor=r['factor'],
            group=r['factor'].split('_')[0], beta=r['beta'], t_NW=r['t_NW'],
            pos_ratio=r['pos_ratio'], dR2=r['dR2'], n_days=r['n_days'],
            n_extra=r['n_extra'], significant=r['significant'],
            selected=r['significant'],
            ic_is=SIC.get(r['factor'], {}).get('IS_ic', np.nan),
            t_is=SIC.get(r['factor'], {}).get('IS_t', np.nan),
            dedup_ic=SIC.get(r['factor'], {}).get('dedup_ALL_ic', np.nan))
    n_ind = int(sum(v['significant'] for v in ssel.values()))
    lg('  single-factor incremental done: selected=%d/38 (pool=%s)'
       % (n_ind, p))

    # -------- §10.2 Walk-Forward ---------------------------------------
    WF = {}
    for name, (kind, grp, praw, porth) in SER.items():
        pan = porth if porth is not None else praw
        if pan is None or not np.isfinite(pan).any():
            continue
        rows = R.walk_forward(pan, lab[p]['y_ex_zz'][R.PRIMARY_H], u, dates, p)
        ok, frac, n = R.wf_verdict(rows)
        WF[name] = {'ok': ok, 'frac': frac, 'n': n}
        for r in rows:
            new(out['wf'], pool=p, series=name, kind=kind, group=grp, **r)
        lg('  wf %-16s ok=%s frac=%.2f (pool=%s)' % (name, ok, frac, p))

    # -------- §10.3 Regime ---------------------------------------------
    reg = R.regime_labels(g, bench, lg=lg)
    RSAME = {}
    for name, ic in ICARR.items():
        kind, grp = SER[name][0], SER[name][1]
        for ax in ('trend', 'style', 'vol', 'earn'):
            d = R.regime_ic(ic, reg[ax])
            for st, v in d.items():
                new(out['reg'], pool=p, series=name, kind=kind, group=grp,
                    axis=ax, state=st, IC_mean=v['IC_mean'], t_NW=v['t_NW'],
                    n_days=v['n_days'])
            if ax == 'trend':
                RSAME[name] = R.regime_same_sign(d)
    lg('  regime done (pool=%s)' % p)

    # -------- §12 Null --------------------------------------------------
    ic38 = np.column_stack([ICARR[n] for n in ORTH if n in ICARR])
    for k in R.GROUPS:
        if 'C_%s' % k not in ICARR:
            continue
        Rf, Rl = Rc[k], LR[('y_ex_zz', R.PRIMARY_H)]
        for kind in ('N1', 'N2'):
            d = null_rounds(Rf, Rl, u, kind, B_NULL, ROUNDS, R.SEED)
            new(out['null'], pool=p, target='C_%s' % k, kind=kind, B=d['b'],
                rounds=rounds_col(d), b_round=d['b_round'], obs=d['obs'],
                p=d['p'], p_min=d['p_min'], resolution=d['resolution'])
        obs3 = float(np.nanmean(ICARR['C_%s' % k]))
        d3 = R.null_n3(ic38, obs3, B=B_NULL, seed=R.SEED)
        new(out['null'], pool=p, target='C_%s' % k, kind='N3', B=d3['b'],
            rounds=1, b_round=d3['b'], obs=d3['obs'], p=d3['p'],
            p_min=d3['p'], resolution=d3['resolution'])
        lg('  null C_%s done (pool=%s)' % (k, p))

    # 合成序列的 N1 主 null (§14 g9 对 COMP_EQ / COMP_PRIOR 自身取值)
    for nm, cpan in (('COMP_EQ', comp_eq), ('COMP_PRIOR', comp_prior)):
        if nm not in ICARR:
            continue
        d = null_rounds(R.xs_rank_pct(cpan, u), LR[('y_ex_zz', R.PRIMARY_H)],
                        u, 'N1', B_NULL, ROUNDS, R.SEED)
        new(out['null'], pool=p, target=nm, kind='N1', B=d['b'],
            rounds=rounds_col(d), b_round=d['b_round'], obs=d['obs'],
            p=d['p'], p_min=d['p_min'], resolution=d['resolution'])
        lg('  null %s done (pool=%s)' % (nm, p))

    # -------- §16.4 样本漏斗 -------------------------------------------
    frows = R.funnel(g, u, elig, Fp, C, Cperp, n_ind, p, mem=mem, lg=lg)
    for r in frows:
        new(out['funnel'], **r)

    return {'SER': SER, 'C': C, 'Cperp': Cperp, 'ORTH': ORTH, 'gd': gd,
            'SIC': SIC, 'QSTAT': QSTAT, 'INC': INC, 'IND': IND, 'WF': WF,
            'RSAME': RSAME, 'n_ind': n_ind, 'cov': cov, 'M0': M0,
            'ICARR': ICARR, 'SEL': ssel}


def rounds_col(d):
    return d.get('rounds', 1)


# ================================================================== perturb
def run_perturb(FR, A35, g, basic, bench, ret, lab, l1s, uni, dates, out, lg):
    """§13 star 扰动: 每个格点内部独立重算正交化与合成."""
    if SIMPLE:
        pts = []
    else:
        pts = R.star_grid()
    base = dict(R.PERT_BASE)
    cfgs = [dict(base, axis='base')] + pts

    def ic_of(pan, pool, h):
        if pan is None:
            return np.nan, np.nan, 0
        u = uni[pool]
        Rf = R.xs_rank_pct(pan, u)
        Rl = R.xs_rank_pct(lab[pool]['y_ex_zz'][h], u)
        ic, cnt = R.daily_ic(Rf, Rl, min_n=R.IC_MIN_N)
        mu, t, _ = R.nw_t(ic)
        return mu, t, int(np.isfinite(ic).sum())

    store = {}
    for cfg in cfgs:
        pool = cfg['pool']
        if uni.get(pool) is None:
            continue
        key = R.pert_cfg_key(cfg)
        Fp = dict(FR)
        if (cfg['roll_window'] == 250 and cfg['vol_window'] == 60):
            Fp.update(A35[pool])
        else:
            Fp.update(build_A35(pool, g, bench, basic, ret, uni[pool], l1s,
                                cfg['roll_window'], cfg['vol_window'], 120,
                                lg=None, cache=False))
        u = uni[pool]
        cov = R.factor_coverage(Fp, u, dates=dates)
        M0 = R.build_M0(g, basic, bench, ret, l1s, u)
        ORTH, C, Cperp, gd = pool_orth(pool, Fp, u, M0, cov, lg=None)
        comp_eq, _ = R.composite_from_rank(
            C, u, {k: 1.0 / len(C) for k in C})
        comp_prior, _ = R.composite_from_rank(C, u, R.COMP_PRIOR)
        vals = {}
        for k in R.GROUPS:
            vals['C_%s' % k] = ic_of(C[k], pool, cfg['h'])
            vals['Cperp_%s' % k] = ic_of(Cperp[k], pool, cfg['h'])
        vals['COMP_EQ'] = ic_of(comp_eq, pool, cfg['h'])
        vals['COMP_PRIOR'] = ic_of(comp_prior, pool, cfg['h'])
        for n in R.ALL_FACTORS:
            vals[n] = ic_of(ORTH.get(n), pool, cfg['h'])
        store[key] = {'pool': pool, 'axis': cfg['axis'], 'h': cfg['h'],
                      'q': cfg['q_groups'], 'roll': cfg['roll_window'],
                      'vol': cfg['vol_window'], 'comp': cfg['composite'],
                      'vals': vals}
        lg('  pert %s done' % key)
        del ORTH, C, Cperp, Fp

    bkey = R.pert_cfg_key(base)
    if bkey not in store:
        return {}
    bvals = store[bkey]['vals']
    PERT = {}
    for name, (bic, _, _) in bvals.items():
        ics = []
        for key, s in store.items():
            if key == bkey:
                continue
            ics.append(s['vals'].get(name, (np.nan, np.nan, 0))[0])
        ratio, ok, tot = R.pert_pos_ratio(bic, ics)
        vd = R.pert_verdict(ratio)
        PERT[name] = {'base_ic': bic, 'pos_ratio': ratio, 'verdict': vd,
                      'n_ok': ok, 'n_tot': tot}
        for key, s in store.items():
            v = s['vals'].get(name, (np.nan, np.nan, 0))
            new(out['pert'], pool=s['pool'], series=name, axis=s['axis'],
                cfg_key=key, horizon=s['h'], q_groups=s['q'],
                roll_window=s['roll'], vol_window=s['vol'],
                composite=s['comp'], IC_mean=v[0], t_NW=v[1], n_days=v[2],
                is_base=int(key == bkey))
        new(out['pertsum'], series=name, base_ic=bic, pos_ratio=ratio,
            n_ok=ok, n_tot=tot, verdict=vd)
    return PERT


# ================================================================== verdicts
# §11.3 预注册权重 (声明在先, 禁止优化)
_WEIGHT_DESC = {
    'COMP_EQ': 'A1=0.20,A2=0.20,A3=0.20,A4=0.20,A5=0.20',
    'COMP_PRIOR': 'A1=0.10,A2=0.30,A3=0.25,A4=0.20,A5=0.15',
}


def group_verdicts(PBD, PERT, out, lg):
    """§14 12 位判决 (C_k / Cperp_k / COMP_EQ / COMP_PRIOR).

    同时落盘 SPEC §16.2 要求的 `zz2k_group_composite.csv`: 五组 `C_k` /
    `C_k^perp` / `COMP_EQ` / `COMP_PRIOR` 的 IC 明细与合成权重明细。
    """
    V = {}
    for p, B in PBD.items():
        SIC, QSTAT, WF, RSAME = B['SIC'], B['QSTAT'], B['WF'], B['RSAME']
        gd = B['gd']
        for name in ['C_%s' % k for k in R.GROUPS] + \
                    ['Cperp_%s' % k for k in R.GROUPS] + \
                    ['COMP_EQ', 'COMP_PRIOR']:
            s = SIC.get(name, {})
            is_ic, is_t = s.get('IS_ic', np.nan), s.get('IS_t', np.nan)
            sp = QSTAT.get((name, R.PRIMARY_H, 'y_ex_zz', 5), {})
            pt = PERT.get(name, {})
            k = (name[6:] if name.startswith('Cperp_')
                 else (name[2:] if name.startswith('C_') else None))
            bits = R.verdict_bits(
                is_ic=is_ic, is_t=is_t,
                sign_ok=bool(np.isfinite(is_ic) and is_ic > 0),
                valid_ic=s.get('VALID_ic', np.nan),
                valid_t=s.get('VALID_t', np.nan),
                oos_ic=s.get('OOS_ic', np.nan),
                mono=sp.get('monotonicity', np.nan),
                net30=sp.get('spread_net30', np.nan),
                pert_stable=(pt.get('verdict') == 'stable'),
                regime_same=bool(RSAME.get(name, False)),
                null_p=null_p_for(name, PBD[p]['NULL'], p),
                dedup_ic=s.get('dedup_ALL_ic', np.nan),
                cperp_t=(SIC.get('Cperp_%s' % k, {}).get('IS_t', np.nan)
                         if k else np.nan),
                wf_ok=bool(WF.get(name, {}).get('ok', False)))
            n_true = int(sum(bool(x) for x in bits.values()))
            vd = R.map_verdict(n_true,
                               pert_fragile=(pt.get('verdict') == 'FRAGILE'))
            notes = []
            if np.isfinite(is_ic) and is_ic < 0:
                notes.append('SIGN_CONFLICT')
            if (np.isfinite(is_ic) and np.isfinite(s.get('VALID_ic', np.nan))
                    and np.sign(is_ic) != np.sign(s.get('VALID_ic', np.nan))):
                notes.append('UNSTABLE')
            if pd.notna(s.get('dedup_ALL_ic', np.nan)) and np.isfinite(is_ic) \
                    and np.sign(s['dedup_ALL_ic']) != np.sign(is_ic):
                notes.append('EPISODE_SENSITIVE')
            if k:
                kk = len(gd[k]['keep']) if k in gd else 0
                wdesc, nfac = 'intra-equal (1/%d)' % kk, kk
                kd = ('perp' if name.startswith('Cperp_') else 'group')
            else:
                wdesc = _WEIGHT_DESC[name]
                nfac = len(B['C'])
                kd = 'composite'
            row = {
                'pool': p, 'series': name, 'kind': kd,
                'weight': wdesc, 'n_factors': nfac,
                'IS_ic': is_ic, 'IS_t': is_t,
                'VALID_ic': s.get('VALID_ic', np.nan),
                'VALID_t': s.get('VALID_t', np.nan),
                'OOS_ic': s.get('OOS_ic', np.nan),
                'OOS_t': s.get('OOS_t', np.nan),
                'ALL_ic': s.get('ALL_ic', np.nan),
                'dedup_ALL_ic': s.get('dedup_ALL_ic', np.nan),
                'monotonicity': sp.get('monotonicity', np.nan),
                'net30_spread': sp.get('spread_net30', np.nan),
                'pert_verdict': pt.get('verdict', 'NA'),
                'n_true': n_true, 'verdict': vd, 'notes': '|'.join(notes),
                **{kk2: int(vv) for kk2, vv in bits.items()}}
            V[(p, name)] = {'bits': bits, 'n_true': n_true, 'verdict': vd,
                            'notes': notes, 'row': row}
            new(out['comp'], **row)
    lg('group_verdicts done: %d 行 (含 C_k / C_k^perp / COMP_*)' % len(V))
    return V


def null_p_for(name, nullrows, p):
    """C_k / COMP_* 用自身 N1 p; C_k^perp 用其所属组的 N1 p (报告中披露)."""
    tgt = name
    if name.startswith('Cperp_'):
        tgt = 'C_' + name[6:]
    for r in nullrows:
        if r['kind'] == 'N1' and r['target'] == tgt:
            return r['p']
    return np.nan


# ================================================================== main
def main():
    global _LOG
    _LOG = R.Log('zz2k_run')
    lg(SEP)
    lg('H-ZZ2K-A1 运行  SIMPLE=%s FORCE=%s B_NULL=%d ROUNDS=%d'
       % (SIMPLE, FORCE, B_NULL, ROUNDS))
    lg(SEP)

    if not R.spec_check(lg=lg):
        lg('EXIT 2 (SPEC 不一致)')
        sys.exit(2)

    g = R.load_grid(lg=lg)
    dates = g['dates']
    basic = R.load_basic(g, lg=lg)
    bench = R.load_bench(g, lg=lg)

    ok, detail = R.future_scan(g, basic, bench, lg=lg)
    fs = {}
    for k, v in detail.items():
        fs[k] = {'ok': bool(v[0]),
                 'evidence': (v[1] if isinstance(v[1], (int, float))
                              else str(v[1]))}
    R.save_json(fs, 'H_ZZ2K_ALPHA_V1_FUTURE_SCAN.json', lg=lg)
    if not ok:
        lg('EXIT 3 (future_column_scan 失败)')
        sys.exit(3)

    ret = R.ret_panel(g)
    uni, diag = R.build_universe(g, basic, lg=lg)
    elig = diag['elig']
    # §16.4 漏斗 stage2: U-ZZ2K = PIT 成分; U-PROXY = 非宽基成分 (代理池原始域)
    mb = diag['mem_bench']
    MEM = {'U-ZZ2K': diag['mem_zz'],
           'U-PROXY': (None if mb is None else ~mb)}
    pools = [p for p in R.POOLS if uni.get(p) is not None]
    for p in R.POOLS:
        if uni.get(p) is None:
            lg('MEMBER_DATA_MISSING -> %s 口径停用' % p)
    if not pools:
        lg('EXIT 3 (无可用股票池)')
        sys.exit(3)

    lab = R.build_labels(g, bench, uni, lg=lg)
    l1s, l1_map = R.industry_l1(g, lg=lg)

    lg(SEP)
    lg('因子层: A1/A2/A4 (共享) + A3/A5 (逐口径)')
    FR = {}
    for k in ('A1', 'A2', 'A4'):
        FR.update(R.build_group(k, g, basic, bench, ret, uni[pools[0]], l1s,
                                lg=lg))
    A35 = {}
    for p in pools:
        A35[p] = build_A35(p, g, bench, basic, ret, uni[p], l1s, 250, 60, 120,
                           lg=lg, cache=True)
    lg('原始因子就绪: %d (A1/A2/A4=%d, A3/A5=%d)'
       % (len(FR) + len(A35[pools[0]]), len(FR), len(A35[pools[0]])))

    out = {k: [] for k in ('ic', 'q', 'orthf', 'orthg', 'inc', 'ind', 'wf',
                           'reg', 'null', 'funnel', 'pert', 'pertsum',
                           'comp')}
    PBD = {}
    for p in pools:
        lg(SEP)
        lg('口径 %s 全流程' % p)
        PBD[p] = run_pool(p, FR, A35, g, basic, bench, ret, lab, l1s, uni, elig,
                          dates, MEM[p], out, lg)
        for rr in out['null']:
            if rr['pool'] == p:
                PBD[p].setdefault('NULL', []).append(rr)

    lg(SEP)
    lg('§13 参数扰动 (star 设计)')
    PERT = run_perturb(FR, A35, g, basic, bench, ret, lab, l1s, uni, dates,
                       out, lg)

    lg(SEP)
    lg('§14 判决')
    V = group_verdicts(PBD, PERT, out, lg)

    # -------- 落盘 -----------------------------------------------------
    lg(SEP)
    lg('保存 CSV')
    # SPEC §16.2 必产 12 个 CSV (orthf/orthg 合并为 zz2k_orthogonal.csv 单表)
    names = {
        'ic': 'zz2k_factor_ic.csv', 'q': 'zz2k_factor_quantile.csv',
        'comp': 'zz2k_group_composite.csv',
        'inc': 'zz2k_incremental.csv', 'ind': 'zz2k_independent_factors.csv',
        'wf': 'zz2k_walkforward.csv', 'reg': 'zz2k_regime.csv',
        'null': 'zz2k_null.csv', 'funnel': 'zz2k_funnel.csv'}
    for k, fn in names.items():
        if out[k]:
            R.save_csv(pd.DataFrame(out[k]), fn, lg=lg)
        else:
            lg('EMPTY %s' % fn)

    # 补充产物 (§13 扰动, 供报告引用)
    for k, fn in (('pert', 'zz2k_perturb.csv'),
                  ('pertsum', 'zz2k_perturb_summary.csv')):
        if out[k]:
            R.save_csv(pd.DataFrame(out[k]), fn, lg=lg)

    # §16.2 正交化诊断 (类内/类间): 因子级 + 组级合并为一份表
    of = pd.DataFrame(out['orthf'])
    og = pd.DataFrame(out['orthg'])
    if len(of) or len(og):
        merged = pd.concat([of, og], ignore_index=True, sort=False)
        R.save_csv(merged, 'zz2k_orthogonal.csv', lg=lg)

    # §10.4 成本表 (由分层表 q=5 派生, 不重算)
    qdf = pd.DataFrame(out['q'])
    if len(qdf):
        cst = qdf[qdf['q'] == 5].copy()
        keep = ['pool', 'series', 'kind', 'group', 'horizon', 'label',
                'gross', 'net0', 'net15', 'net30', 'net50', 'gross_spread',
                'spread_net0', 'spread_net15', 'spread_net30', 'spread_net50',
                'monotonicity']
        R.save_csv(cst[keep], 'zz2k_cost.csv', lg=lg)

    # §10.1 OOS 表 (由 IC 全表派生)
    icd = pd.DataFrame(out['ic'])
    if len(icd):
        oo = icd[(icd['panel'] == 'ORTH') & (icd['episode'] == 'R_ALL')
                 & (icd['seg'].isin(list(SEG_ONLY)))]
        piv = oo.pivot_table(index=['pool', 'series', 'kind', 'group',
                                    'horizon', 'label'],
                             columns='seg', values=['IC_mean', 't_NW', 'n_days'],
                             aggfunc='first').reset_index()
        piv.columns = ['_'.join([str(x) for x in c if x != ''])
                       if isinstance(c, tuple) else c for c in piv.columns]
        if 'IC_mean_IS' in piv.columns:
            piv['sign_same_IS_VALID'] = (
                np.sign(piv['IC_mean_IS']) == np.sign(piv.get('IC_mean_VALID')))
            piv['sign_same_IS_OOS'] = (
                np.sign(piv['IC_mean_IS']) == np.sign(piv.get('IC_mean_OOS')))
            piv['UNSTABLE'] = ~piv['sign_same_IS_VALID']
            piv['OOS_SHORT'] = 1
        R.save_csv(piv, 'zz2k_oos.csv', lg=lg)

    results = {
        'research_id': 'H-ZZ2K-A1', 'version': '1.1',
        'run_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'simple': bool(SIMPLE), 'B_null': int(B_NULL), 'rounds': int(ROUNDS),
        'pools': pools,
        'group_composite': [v['row'] for v in V.values()],
        'pert': {k: {'base_ic': v['base_ic'], 'pos_ratio': v['pos_ratio'],
                     'verdict': v['verdict']} for k, v in PERT.items()},
        'independent_selected': {
            p: {k: v for k, v in PBD[p]['SEL'].items() if v['significant']}
            for p in pools},
        'n_independent': {p: PBD[p]['n_ind'] for p in pools},
        'trading_authorization': 'NO',
    }
    R.save_json(results, 'H_ZZ2K_ALPHA_V1_RESULTS.json', lg=lg)
    lg(SEP)
    lg('done')
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
