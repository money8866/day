# -*- coding: utf-8 -*-
"""hep_stability: H-EARN-POST §35–§40 稳定性与泛化检验

覆盖预注册条款
  §35 时间稳定性：按 Year / Quarter / Month 分解（2026 特别检查 8/9 月集中度）
  §36 历史 OOS：TRAIN 2018–2022 / VALID 2023–2024 / OOS 2025 / LIVE-LIKE 2026
  §37 历史年份单独报告：逐年 Sample / IC / Top-Bottom / Net Return / AUC
  §38 Walk Forward：W1–W6（方向只用各自训练窗确定后冻结，测试年独立）
  §39 Regime 稳定性：BULL / NORMAL / BEAR（project-wide 统一口径，取 E 日 regime）
  §40 参数稳定性：hist_window 4/8/12、Entry 20/25/30、FSC 分位 0.2/0.3/0.4/0.5、
                  Price Reaction LOW/MID/HIGH —— 邻域须同向，禁止单点最优

隔离声明（§1）：只读 data/hep_events.parquet 与市场级 Tushare cache，
复用本课题自有 hep_engine / hep_portfolio 原语，不触碰任何生产模块。

输出：out/h_earn_post_oos.csv / _walkforward.csv / _regime.csv / _parameter_grid.csv
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hep_common import (OUTD, PREREG, ENTRIES, HORIZONS, Log, zs, ols_resid, spearman_ic)
import hep_engine as E
import hep_portfolio as P

log = Log('_hep_stability.txt')
PRIMARY_SIG = PREREG['primary_signal']          # SIG_RESID
PRIMARY_T = PREREG['primary_horizon']           # 20
PHASES = list(PREREG['phases'])
WFS = list(PREREG['walkforward'])
COST_BPS = 30.0
DIR_COL = {                                      # 未冻结方向的原始信号列
    'SIG_FSC': 'fsc_z', 'SIG_RESID': 'sig_resid',
    'SIG_QUADA': 'sig_quada', 'SIG_INTER': 'sig_inter',
}
PORT_KIND = {'SIG_FSC': 'FSC_T10', 'SIG_RESID': 'RESID_T10',
             'SIG_QUADA': 'QUADA', 'SIG_INTER': 'INTER_T10'}


# ------------------------------------------------------------------ 市场 / Regime
def market():
    mkt = P.load_market()
    lv = pd.Series(mkt['lvl'])
    ma200 = lv.rolling(200, min_periods=60).mean()
    r60 = lv / lv.shift(60) - 1.0
    reg = pd.Series('NORMAL', index=lv.index)
    reg[(lv > ma200) & (r60 > 0)] = 'BULL'
    reg[(lv < ma200) & (r60 < 0)] = 'BEAR'
    mkt['reg'] = reg.values
    return mkt


def sig_meta(de, mkt):
    """每个 sig_k（=E 日）的 rep_year / year / quarter / month / regime（as-of E，无未来函数）。"""
    g = de.groupby('sig_k', sort=True)
    sk = np.array(sorted(g.groups.keys()), dtype=np.int64)
    td = pd.Series(mkt['td'][sk], index=sk).astype(str)
    yr = td.str.slice(0, 4)
    mo = td.str.slice(4, 6)
    return pd.DataFrame({
        'rep_year': g['rep_year'].first().reindex(sk).values,
        'year': yr.values, 'month': (yr + '-' + mo).values,
        'quarter': (yr + 'Q' + ((mo.astype(int) - 1) // 3 + 1).astype(str)).values,
        'regime': mkt['reg'][sk],
    }, index=pd.Index(sk, name='sig_k'))


# ------------------------------------------------------------------ 组合净收益（分域）
def port_scope(de, kind, T, mkt, cost_bps=COST_BPS):
    """域内 §27 固定持有组合。返回年化（长窗有意义）与**累计**（短窗/年度口径）。"""
    si = de['si'].to_numpy().astype(np.int64)
    bk = de['buy_k'].to_numpy().astype(np.int64)
    m = P.sel_mask(de, kind)
    if m.sum() == 0:
        return None
    sim = P.simulate(si[m], bk[m], T, mkt)
    if sim is None:
        return None
    ser = P.daily_series(sim, mkt, k0=sim['k_first'], k1=sim['k_last'])
    met = P.metrics(ser, cost_bps, mkt)
    c = cost_bps / 1e4
    rabs = np.asarray(ser['rabs']) - c * np.asarray(ser['trade'])
    rex = np.asarray(ser['rex']) - c * np.asarray(ser['trade'])
    met['cum_net'] = float(np.prod(1.0 + rabs) - 1.0)
    met['cum_excess'] = float(np.prod(1.0 + rex) - 1.0)
    return met


# ------------------------------------------------------------------ §35/§36/§37
def scope_table(de, meta, sigs):
    """逐 (entry, signal, horizon) 复用 IC / 分位序列，再按 scope 掩码聚合。"""
    rows = []
    kdex = meta.index.values
    repy = meta['rep_year'].values
    scopes = []
    for ph, y0, y1 in PHASES:
        scopes.append(('PHASE', ph, (repy >= y0) & (repy <= y1)))
    for y in sorted(pd.unique(meta['year'])):
        scopes.append(('YEAR', y, (meta['year'] == y).values))
    for q in sorted(pd.unique(meta['quarter'])):
        scopes.append(('QUARTER', q, (meta['quarter'] == q).values))
    for mo in sorted(pd.unique(meta['month'])):
        scopes.append(('MONTH', mo, (meta['month'] == mo).values))
    ksz = de.groupby('sig_k', sort=True)['sig_k'].size().reindex(kdex).fillna(0).values
    for sig in sigs:
        for T in HORIZONS:
            ret = 'ex_%d' % T
            ics = E.ic_series(de, sig, ret)
            q = E.quintile_means(de, sig, ret)
            tb = (q['t20'] - q['b20']) if not q.empty else pd.Series(dtype=float)
            for stype, sval, msk in scopes:
                kk = kdex[msk]
                if len(kk) == 0:
                    continue
                st = E.ic_row(ics.reindex(kk).dropna(), T)
                r = dict(signal=sig, horizon='T+%d' % T, scope_type=stype, scope=sval,
                         n_day=st['n'], n_ev=int(ksz[msk].sum()),
                         mean_ic=st['mean_ic'], med_ic=st['med_ic'], ic_std=st['ic_std'],
                         icir=st['icir'], ic_t=st['ic_t'], pos_ratio=st['pos_ratio'],
                         tb20_gross=np.nan, tb20_net=np.nan, auc=np.nan, prec10=np.nan,
                         port_cum_net30=np.nan, port_cum_excess_net30=np.nan,
                         port_ann_net30=np.nan, port_excess_net30=np.nan)
                if st['n'] >= 5 and len(tb):
                    v = tb.reindex(kk).dropna().mean()
                    r['tb20_gross'] = float(v)
                    r['tb20_net'] = float(v - 4 * COST_BPS / 1e4)
                if stype in ('PHASE', 'YEAR'):
                    ab = E.auc_block(de[de['sig_k'].isin(kk)], sig, ret)
                    r['auc'] = ab['auc']
                    r['prec10'] = ab['prec10']
                    pm = port_scope(de[de['sig_k'].isin(kk)], PORT_KIND[sig], T, mkt=P_MKT)
                    if pm is not None:
                        r['port_cum_net30'] = pm['cum_net']
                        r['port_cum_excess_net30'] = pm['cum_excess']
                        r['port_ann_net30'] = pm['ann']
                        r['port_excess_net30'] = pm['excess_ann']
                r['scope'] = sval
                rows.append(r)
    return pd.DataFrame(rows)


def run_scopes(d, mkt, sigs):
    global P_MKT
    P_MKT = mkt
    log('=' * 78)
    log('1) §35/§36/§37 时间稳定性与历史 OOS（Year / Quarter / Month / Phase）')
    rows = []
    for e in ENTRIES:
        de = d[d['E'] == e].copy()
        meta = sig_meta(de, mkt)
        t = scope_table(de, meta, sigs)
        t.insert(0, 'entry', 'E%d' % e)
        rows.append(t)
    df = pd.concat(rows, ignore_index=True)
    cols = ['entry', 'signal', 'horizon', 'scope_type', 'scope', 'n_day', 'n_ev',
            'mean_ic', 'med_ic', 'ic_std', 'icir', 'ic_t', 'pos_ratio',
            'tb20_gross', 'tb20_net', 'auc', 'prec10',
            'port_cum_net30', 'port_cum_excess_net30',
            'port_ann_net30', 'port_excess_net30']
    df = df[cols]
    df.to_csv(os.path.join(OUTD, 'h_earn_post_oos.csv'), index=False, encoding='utf-8-sig')
    p = df[(df['signal'] == PRIMARY_SIG) & (df['horizon'] == 'T+%d' % PRIMARY_T)]
    log('  §36 分期（%s / T+%d；NetReturn = 该域 §27 组合 30bp 后累计净额/累计超额）:'
        % (PRIMARY_SIG, PRIMARY_T))
    log(E.fmt_tbl(p[p['scope_type'] == 'PHASE'].set_index(['entry', 'scope'])[
        ['n_day', 'n_ev', 'mean_ic', 'icir', 'ic_t', 'tb20_gross', 'auc',
         'port_cum_net30', 'port_cum_excess_net30']]))
    log('  §37 逐年（%s / T+%d）:' % (PRIMARY_SIG, PRIMARY_T))
    log(E.fmt_tbl(p[p['scope_type'] == 'YEAR'].set_index(['entry', 'scope'])[
        ['n_day', 'n_ev', 'mean_ic', 'ic_t', 'tb20_gross', 'tb20_net', 'auc',
         'port_cum_net30', 'port_cum_excess_net30']]))
    log('  §37 逐年 × 四信号 mean_ic（E20 / T+%d）:' % PRIMARY_T)
    q = df[(df['horizon'] == 'T+%d' % PRIMARY_T) & (df['scope_type'] == 'YEAR')]
    log(E.fmt_tbl(q.pivot_table(index='scope', columns=['signal', 'entry'],
                                values='mean_ic')))
    log('  §35 2026 LIVE-LIKE 月度（%s / T+%d）:' % (PRIMARY_SIG, PRIMARY_T))
    r26 = df[(df['scope_type'] == 'MONTH') & (df['scope'].str.startswith('2026'))
             & (df['signal'] == PRIMARY_SIG) & (df['horizon'] == 'T+%d' % PRIMARY_T)]
    log(E.fmt_tbl(r26.set_index(['entry', 'scope'])[
        ['n_day', 'n_ev', 'mean_ic', 'ic_t', 'tb20_gross', 'auc']]))
    log('  §35 全部月份 mean_ic（E20 / %s / T+%d）：后段逐月' % (PRIMARY_SIG, PRIMARY_T))
    rmo = df[(df['scope_type'] == 'MONTH') & (df['signal'] == PRIMARY_SIG)
             & (df['horizon'] == 'T+%d' % PRIMARY_T) & (df['entry'] == 'E20')]
    log(E.fmt_tbl(rmo.set_index('scope')[['n_day', 'mean_ic']].tail(20)))
    log('  §35 季度 mean_ic（%s / T+%d，列=Entry）:' % (PRIMARY_SIG, PRIMARY_T))
    rq = df[(df['scope_type'] == 'QUARTER') & (df['signal'] == PRIMARY_SIG)
            & (df['horizon'] == 'T+%d' % PRIMARY_T)]
    log(E.fmt_tbl(rq.pivot_table(index='scope', columns='entry', values='mean_ic')))
    log('  已存 out/h_earn_post_oos.csv  %d 行' % len(df))
    return df


# ------------------------------------------------------------------ §38 Walk Forward
def wf_dir(tr, base, T=PRIMARY_T):
    ic = spearman_ic(pd.to_numeric(tr[base], errors='coerce').values,
                     pd.to_numeric(tr['ex_%d' % T], errors='coerce').values)
    return 1.0 if (np.isfinite(ic) and ic >= 0) else -1.0


def run_walkforward(d, mkt, sigs):
    log('=' * 78)
    log('2) §38 Walk Forward（方向只用各自训练窗确定后冻结；测试年独立）')
    rows = []
    for wn, y0, y1, ty in WFS:
        tr = d[d['rep_year'].between(y0, y1)]
        te_all = d[d['rep_year'] == ty]
        dirs = {s: (1.0 if s == 'SIG_QUADA' else wf_dir(tr, DIR_COL[s])) for s in sigs}
        for e in ENTRIES:
            te = te_all[te_all['E'] == e].copy()
            if len(te) == 0:
                continue
            for s in sigs:
                te['_sig'] = pd.to_numeric(te[DIR_COL[s]], errors='coerce') * dirs[s]
                tric = spearman_ic(
                    pd.to_numeric(tr[DIR_COL[s]], errors='coerce').values * dirs[s],
                    pd.to_numeric(tr['ex_%d' % PRIMARY_T], errors='coerce').values)
                for T in HORIZONS:
                    ret = 'ex_%d' % T
                    st = E.ic_row(E.ic_series(te, '_sig', ret), T)
                    q = E.quintile_means(te, '_sig', ret)
                    tb = float((q['t20'] - q['b20']).mean()) if not q.empty else np.nan
                    ab = E.auc_block(te, '_sig', ret)
                    pm = None
                    if s == PRIMARY_SIG:
                        pm = port_scope(te.assign(sig_resid_d=te['_sig']),
                                        PORT_KIND[s], T, mkt)
                    rows.append(dict(
                        window=wn, train='%d-%d' % (y0, y1), test_year=ty,
                        entry='E%d' % e, signal=s, horizon='T+%d' % T,
                        dir=int(dirs[s]), n_train=len(tr), n_test=len(te),
                        n_day=st['n'], train_ic=tric, test_ic=st['mean_ic'],
                        icir=st['icir'], ic_t=st['ic_t'], pos_ratio=st['pos_ratio'],
                        tb20_gross=tb,
                        tb20_net=(tb - 4 * COST_BPS / 1e4) if np.isfinite(tb) else np.nan,
                        auc=ab['auc'], prec10=ab['prec10'],
                        port_cum_net30=None if pm is None else pm['cum_net'],
                        port_cum_excess_net30=None if pm is None else pm['cum_excess'],
                        port_excess_net30=None if pm is None else pm['excess_ann']))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUTD, 'h_earn_post_walkforward.csv'), index=False,
              encoding='utf-8-sig')
    p = df[(df['signal'] == PRIMARY_SIG) & (df['horizon'] == 'T+%d' % PRIMARY_T)]
    log('  主信号 %s / T+%d：train_ic vs test_ic vs AUC' % (PRIMARY_SIG, PRIMARY_T))
    log(E.fmt_tbl(p.pivot_table(index=['window', 'test_year'], columns='entry',
                                values=['train_ic', 'test_ic', 'auc'])))
    log('  全部信号 test_ic（T+%d；跨 Entry 均值）:' % PRIMARY_T)
    log(E.fmt_tbl(df[df['horizon'] == 'T+%d' % PRIMARY_T].pivot_table(
        index='window', columns='signal', values='test_ic', aggfunc='mean')))
    log('  主信号 test_ic 的 Horizon 剖面（每个窗口一个测试年，E20/E25/E30 均值）:')
    z = df[df['signal'] == PRIMARY_SIG].pivot_table(
        index='window', columns='horizon', values='test_ic', aggfunc='mean')
    log(E.fmt_tbl(z))
    log('  已存 out/h_earn_post_walkforward.csv  %d 行' % len(df))
    return df


# ------------------------------------------------------------------ §39 Regime
def run_regime(d, mkt, sigs):
    log('=' * 78)
    log('3) §39 Regime 稳定性（BULL / NORMAL / BEAR，取 E 日 regime）')
    rows = []
    for e in ENTRIES:
        de = d[d['E'] == e].copy()
        meta = sig_meta(de, mkt)
        for rg in ('BULL', 'NORMAL', 'BEAR'):
            kk = meta.index.values[(meta['regime'] == rg).values]
            if len(kk) == 0:
                continue
            sub = de[de['sig_k'].isin(kk)]
            for s in sigs:
                for T in HORIZONS:
                    ret = 'ex_%d' % T
                    ics = E.ic_series(sub, s, ret)
                    st = E.ic_row(ics, T)
                    q = E.quintile_means(sub, s, ret)
                    tb = float((q['t20'] - q['b20']).mean()) if not q.empty else np.nan
                    ab = E.auc_block(sub, s, ret)
                    pm = port_scope(sub, PORT_KIND[s], T, mkt) if T == PRIMARY_T else None
                    rows.append(dict(
                        entry='E%d' % e, signal=s, regime=rg, horizon='T+%d' % T,
                        n_day=st['n'], n_ev=len(sub), mean_ic=st['mean_ic'],
                        med_ic=st['med_ic'], ic_std=st['ic_std'], icir=st['icir'],
                        ic_t=st['ic_t'], pos_ratio=st['pos_ratio'],
                        tb20_gross=tb,
                        tb20_net=(tb - 4 * COST_BPS / 1e4) if np.isfinite(tb) else np.nan,
                        auc=ab['auc'], prec10=ab['prec10'],
                        port_cum_net30=None if pm is None else pm['cum_net'],
                        port_cum_excess_net30=None if pm is None else pm['cum_excess'],
                        port_ann_net30=None if pm is None else pm['ann'],
                        port_excess_net30=None if pm is None else pm['excess_ann']))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUTD, 'h_earn_post_regime.csv'), index=False,
              encoding='utf-8-sig')
    p = df[df['horizon'] == 'T+%d' % PRIMARY_T]
    log('  T+%d 各 Regime mean_ic（行=Regime，列=信号×Entry）:' % PRIMARY_T)
    log(E.fmt_tbl(p.pivot_table(index='regime', columns=['signal', 'entry'],
                                values='mean_ic')))
    log('  主信号 %s 的 IC / TB / AUC（T+%d）:' % (PRIMARY_SIG, PRIMARY_T))
    q = p[p['signal'] == PRIMARY_SIG]
    log(E.fmt_tbl(q.set_index(['regime', 'entry'])[
        ['n_day', 'mean_ic', 'ic_t', 'tb20_gross', 'auc',
         'port_cum_excess_net30']]))
    log('  各 Horizon × Regime mean_ic（主信号，跨 Entry 均值）:')
    log(E.fmt_tbl(df[df['signal'] == PRIMARY_SIG].pivot_table(
        index='horizon', columns='regime', values='mean_ic', aggfunc='mean')))
    flip, tot = 0, 0
    for (e, s), g in df.groupby(['entry', 'signal']):
        vals = g[g['horizon'] == 'T+%d' % PRIMARY_T]['mean_ic'].values
        sg = set(np.sign(v) for v in vals if np.isfinite(v))
        tot += 1
        flip += int(len(sg) > 1)
    log('  方向翻转组合数 = %d / %d  -> %s'
        % (flip, tot, 'REGIME_UNSTABLE' if flip > 0.5 * tot else 'REGIME_STABLE(偏)'))
    log('  已存 out/h_earn_post_regime.csv  %d 行' % len(df))
    return df


# ------------------------------------------------------------------ §40 参数网格
def fsc_window(d, w, train=(2018, 2022)):
    """用 hist_window=w 重算 FSC（S1 的 np/rev 换成 S1_*_w）与 SIG_RESID；
    方向只用 TRAIN 期确定后冻结。"""
    fv = list(PREREG['fund_vars'])
    ren = [('S1_np_%d' % w) if v == 'np_s1' else
           ('S1_rev_%d' % w) if v == 'rev_s1' else v for v in fv]
    z = pd.DataFrame(index=d.index)
    for v, c in zip(fv, ren):
        z[v] = pd.to_numeric(d[c], errors='coerce')
    for idx in d.groupby('sig_k', sort=False).groups.values():
        sub = z.loc[idx]
        for v in fv:
            z.loc[idx, v] = zs(sub[v]).values
    cnt = z.notna().sum(axis=1)
    fsc = pd.Series(np.where(cnt >= 4, z.sum(axis=1) / cnt, np.nan), index=d.index)
    resid = pd.Series(np.nan, index=d.index)
    pa = pd.to_numeric(d['pa_z'], errors='coerce')
    for idx in d.groupby('sig_k', sort=False).groups.values():
        resid.iloc[idx] = ols_resid(fsc.loc[idx].values, pa.loc[idx].values)
    m = d['rep_year'].between(train[0], train[1]).values
    y = d['ex_%d' % PRIMARY_T].values
    df_ = 1.0 if spearman_ic(fsc.values[m], y[m]) >= 0 else -1.0
    dr_ = 1.0 if spearman_ic(resid.values[m], y[m]) >= 0 else -1.0
    return fsc * df_, resid * dr_, (int(df_), int(dr_))


def run_param_grid(d, mkt):
    log('=' * 78)
    log('4) §40 参数稳定性（预注册邻域；邻域须同向，禁止单点最优）')
    rows = []
    T = PRIMARY_T
    ret = 'ex_%d' % T

    def emit(axis, val, sig_name, e, sub, col):
        st = E.ic_row(E.ic_series(sub, col, ret), T)
        q = E.quintile_means(sub, col, ret)
        ab = E.auc_block(sub, col, ret)
        tb = float((q['t20'] - q['b20']).mean()) if not q.empty else np.nan
        rows.append(dict(axis=axis, param_value=str(val), signal=sig_name,
                         entry='E%d' % e, horizon='T+%d' % T,
                         n_day=st['n'], n_ev=len(sub), mean_ic=st['mean_ic'],
                         icir=st['icir'], ic_t=st['ic_t'], pos_ratio=st['pos_ratio'],
                         tb20_gross=tb, auc=ab['auc']))

    # A. hist_window 4/8/12（重建 FSC / SIG_RESID，方向 TRAIN 冻结）
    for w in PREREG['hist_windows']:
        fsc, resid, dd_ = fsc_window(d, w)
        dd = d.copy()
        dd['_fsc_w'] = fsc
        dd['_res_w'] = resid
        for e in ENTRIES:
            sub = dd[dd['E'] == e]
            emit('HIST_WIN', w, 'SIG_FSC', e, sub, '_fsc_w')
            emit('HIST_WIN', w, 'SIG_RESID', e, sub, '_res_w')
        log('  HIST_WIN=%d 方向(TRAIN 冻结) fsc=%+d resid=%+d' % (w, dd_[0], dd_[1]))

    # B. Entry 20/25/30（四个预注册信号）
    for e in ENTRIES:
        sub = d[d['E'] == e]
        for s in E.SIG_COLS:
            emit('ENTRY', e, s, e, sub, E.SIG_COLS[s])

    # C. FSC 分位阈值 0.2/0.3/0.4/0.5（§13 象限上界；PA 固定 MID=0.6）
    fr = pd.to_numeric(d['fsc_rank'], errors='coerce')
    pr = pd.to_numeric(d['pa_rank'], errors='coerce')
    for c in PREREG['pct_cuts']:
        dd = d.copy()
        dd['_qa_c'] = np.where(fr.notna() & pr.notna(),
                               ((fr >= 1.0 - c) & (pr <= 0.6)).astype(float), np.nan)
        for e in ENTRIES:
            emit('FSC_PCT', c, 'SIG_QUADA', e, dd[dd['E'] == e], '_qa_c')

    # D. Price Reaction 上界 LOW/MID/HIGH（§14 不预设越低越好）
    for nm, ub in (('LOW', 0.4), ('MID', 0.6), ('HIGH', 0.8)):
        dd = d.copy()
        dd['_qa_r'] = np.where(fr.notna() & pr.notna(),
                               ((fr >= 0.7) & (pr <= ub)).astype(float), np.nan)
        for e in ENTRIES:
            emit('REACT_BIN', nm, 'SIG_QUADA', e, dd[dd['E'] == e], '_qa_r')

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUTD, 'h_earn_post_parameter_grid.csv'), index=False,
              encoding='utf-8-sig')
    for axis in ('HIST_WIN', 'ENTRY', 'FSC_PCT', 'REACT_BIN'):
        p = df[df['axis'] == axis]
        if p.empty:
            continue
        log('  [%s] mean_ic 邻域（T+%d）:' % (axis, T))
        log(E.fmt_tbl(p.pivot_table(index='param_value', columns=['signal', 'entry'],
                                    values='mean_ic')))
    vd = []
    for (axis, sig, e), p in df.groupby(['axis', 'signal', 'entry']):
        v = p['mean_ic'].values
        sg = set(np.sign(x) for x in v if np.isfinite(x))
        vd.append(dict(axis=axis, signal=sig, entry=e, n_pts=len(v),
                       mean_ic_min=float(np.nanmin(v)), mean_ic_max=float(np.nanmax(v)),
                       same_sign=bool(len(sg) <= 1),
                       ic_span=float(np.nanmax(v) - np.nanmin(v))))
    vd = pd.DataFrame(vd)
    log('  邻域同向组合 = %d / %d' % (int(vd['same_sign'].sum()), len(vd)))
    log(E.fmt_tbl(vd.set_index(['axis', 'signal', 'entry'])[
        ['n_pts', 'mean_ic_min', 'mean_ic_max', 'ic_span', 'same_sign']]))
    log('  已存 out/h_earn_post_parameter_grid.csv  %d 行' % len(df))
    return df


P_MKT = None


def main():
    log('=' * 78)
    log('H-EARN-POST §35–§40 稳定性与泛化检验（预注册 %s）' % PREREG['hypothesis_id'])
    d = E.load()
    log('载入 hep_events.parquet %s' % (d.shape,))
    d = E.add_features(d)
    dirs = E.freeze_dir(d, E.BASE_COLS, (2018, 2022), horizon=PRIMARY_T)
    E.apply_dir(d, E.BASE_COLS, dirs)
    log('基线方向冻结(TRAIN 2018–2022, T+%d): %s'
        % (PRIMARY_T, {k: int(v) for k, v in dirs.items()}))
    mkt = market()
    log('市场面板 %d 天；全样本 Regime 分布 %s'
        % (mkt['NCAL'], pd.Series(mkt['reg']).value_counts().to_dict()))
    if 'regime' in d.columns:
        log('D0 日 Regime 事件分布: %s'
            % d.groupby('sig_k').first()['regime'].value_counts().to_dict())
    sigs = list(E.SIG_COLS)
    run_scopes(d, mkt, sigs)
    run_walkforward(d, mkt, sigs)
    run_regime(d, mkt, sigs)
    run_param_grid(d, mkt)
    log('=' * 78)
    log('DONE')
    log.save()


if __name__ == '__main__':
    main()
