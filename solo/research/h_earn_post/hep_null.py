# -*- coding: utf-8 -*-
"""hep_null: H-EARN-POST §32/§33 Null Model 与 §34 Counterfactual Matching

隔离声明（§1）：本模块只读 data/hep_events.parquet 与市场级 Tushare cache
（calendar / price_panel / index_panel），不 import 亦不调用 ARCHIVE / HVT / DLG /
F120 / Theme Quant / te_buy_pool / 突破策略 / 天量策略 / 首板策略 的任何代码或结果。

§32 Null Model
  N1 Random Stock  —— 同一决策日(cohort)内随机选择股票。等价于在 cohort 内把
                      ex_T 的可得值随机重排（Spearman IC 对配对标签对称）。
  N2 Random Date   —— 同一只股票、把 Entry 平移到 ±NULL_SHIFT 交易日内的随机日期（δ≠0），
                      收益与超额收益按平移后的窗口重算（只用 Tushare 行情 cache）。
  N3 Momentum      —— 只用 Ret_5 / Ret_20 / Ret_60          => 引擎 B1_MOM
  N4 Fundamental   —— 只用 Revenue Growth / Profit Growth / ROE => 引擎 B3_GROW
  N5 Post-Earnings —— 只用 Post20 Relative Return           => 引擎 B4_REACT
  N3/N4/N5 为确定性参照基线（无随机分布），故不参与 seed 分布，仅作对照行。

§33 判定（预注册门槛 z ≥ PREREG['null_z_pass']，单侧）
  若 Event Alpha ≈ Random Stock -> FAIL — NO EVENT ALPHA
  若 Event Alpha ≈ Random Date  -> FAIL — TIME EFFECT
  若 H-EARN-POST < Momentum     -> NO_INCREMENTAL_ALPHA

§34 Counterfactual Matching
  在 Industry / MarketCap / Liquidity / Momentum / Value 上做同 cohort 最近邻匹配
  （Industry 受限优先），比较 High Fundamental Surprise 与 Matched Control。

输出：out/h_earn_post_null_model.csv, out/h_earn_post_counterfactual.csv
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hep_common import (OUTD, FS_DATA, PREREG, ENTRIES, HORIZONS, MIN_XS, Log)
import hep_engine as E

log = Log('_hep_null.txt')

SEED = PREREG['seed']
NSEED = PREREG['n_seed']
NULL_SHIFT = PREREG['null_shift_days']
CUT_HI = PREREG['null_pct_cut_hi']
CUT_LO = PREREG['null_pct_cut_lo']
Z_PASS = PREREG['null_z_pass']
COST_BPS = 30.0

# 需要做零假设分布的候选信号（§13/§14 预注册四信号）
CAND = list(E.SIG_COLS)
# 确定性参照基线（§32 N3/N4/N5）
REF = {'N3': 'B1_MOM', 'N4': 'B3_GROW', 'N5': 'B4_REACT'}
FACTORS = CAND + list(REF.values())
MATCH_VARS = ['CTRL_SIZE', 'CTRL_LIQ', 'CTRL_MOM', 'CTRL_VAL']   # §34 匹配维度


# ------------------------------------------------------------------ 市场面板（N2 用）
def load_market():
    cal = pd.read_parquet(os.path.join(FS_DATA, 'calendar.parquet'))
    td = cal['trade_date'].astype(str).values
    NCAL = len(td)
    kmap = pd.Series(np.arange(NCAL), index=td)
    px = pd.read_parquet(os.path.join(FS_DATA, 'price_panel.parquet'),
                         columns=['ts_code', 'trade_date', 'qfq_open', 'qfq_close'])
    px['trade_date'] = px['trade_date'].astype(str)
    codes = np.sort(px['ts_code'].astype(str).unique())
    cmap = pd.Series(np.arange(len(codes)), index=codes)
    si = px['ts_code'].map(cmap).values
    kk = px['trade_date'].map(kmap).values
    ok = pd.notna(si) & pd.notna(kk)
    si, kk = si[ok].astype(np.int64), kk[ok].astype(np.int64)
    Oq = np.full((len(codes), NCAL), np.nan, dtype=np.float32)
    Cq = np.full((len(codes), NCAL), np.nan, dtype=np.float32)
    Oq[si, kk] = pd.to_numeric(px['qfq_open'], errors='coerce').values[ok]
    Cq[si, kk] = pd.to_numeric(px['qfq_close'], errors='coerce').values[ok]
    del px
    idx = pd.read_parquet(os.path.join(FS_DATA, 'index_panel.parquet'))
    idx = idx[idx['ts_code'] == '000300.SH'].copy()
    idx['k'] = idx['trade_date'].astype(str).map(kmap)
    idx = idx[idx['k'].notna()].sort_values('k')
    lvl = np.full(NCAL, np.nan)
    lvl[idx['k'].values.astype(int)] = pd.to_numeric(idx['close'], errors='coerce').values
    lvl = pd.Series(lvl).ffill().bfill().values
    return dict(cmap=cmap, Oq=Oq, Cq=Cq, lvl=lvl, NCAL=NCAL, n_codes=len(codes))


# ------------------------------------------------------------------ 零假设收益矩阵
def null_n1(de, ret, groups):
    """N1 Random Stock：cohort 内随机重排 ex_T 的**可得值**（NaN 位置保持不变）。

    返回 (NSEED, n_rows) 矩阵。等价于「同一决策日随机选择股票」。
    """
    r = pd.to_numeric(de[ret], errors='coerce').astype('float64').to_numpy()
    rng = np.random.default_rng(SEED + 104729)
    R = np.empty((NSEED, len(r)), dtype=float)
    for i in range(NSEED):
        rr = r.copy()
        for pos in groups:
            v = r[pos]
            m = np.isfinite(v)
            if int(m.sum()) > 1:
                rr[pos[m]] = rng.permutation(v[m])
        R[i] = rr
    return R


def null_n2(de, T, mkt):
    """N2 Random Date：同股票、Entry 平移 δ ∈ ±[1..NULL_SHIFT] 个交易日（δ≠0）。

    平移后窗口的个股收益与沪深300 收益同时重算，超额收益口径与主分析一致。
    """
    si = de['si'].to_numpy().astype(np.int64)
    bk = de['buy_k'].to_numpy().astype(np.int64)
    NCAL = mkt['NCAL']
    deltas = np.concatenate([-np.arange(1, NULL_SHIFT + 1), np.arange(1, NULL_SHIFT + 1)])
    rng = np.random.default_rng(SEED + 7919)
    R = np.full((NSEED, len(de)), np.nan, dtype=float)
    for i in range(NSEED):
        dk = rng.choice(deltas, size=len(de), replace=True)
        b2, e2 = bk + dk, bk + dk + T
        m = (si >= 0) & (b2 >= 1) & (e2 <= NCAL - 1)
        ix = np.flatnonzero(m)
        if len(ix):
            with np.errstate(invalid='ignore', divide='ignore'):
                rr = mkt['Cq'][si[ix], e2[ix]] / mkt['Oq'][si[ix], b2[ix]] - 1.0
                ir = mkt['lvl'][e2[ix]] / mkt['lvl'][b2[ix]] - 1.0
            R[i, ix] = rr - ir
    return R


# ------------------------------------------------------------------ 指标
def _metrics(d, sig, ret):
    ics = E.ic_series(d, sig, ret)
    mic = float(ics.mean()) if len(ics) else np.nan
    q = E.quintile_means(d, sig, ret)
    if q.empty:
        return dict(mean_ic=mic, tb20=np.nan, top20=np.nan)
    return dict(mean_ic=mic,
                tb20=float((q['t20'] - q['b20']).mean()),
                top20=float(q['t20'].mean()))


def null_rows(de, sig, ret, Rnull, entry, hz, nid):
    """对单个 (entry, horizon, factor) 计算 actual 与零假设分布统计。"""
    k = de['sig_k'].to_numpy()
    base = pd.DataFrame({'sig_k': k,
                         '_s': pd.to_numeric(de[sig], errors='coerce')
                                 .astype('float64').to_numpy()})
    act = _metrics(base.assign(_r=pd.to_numeric(de[ret], errors='coerce')
                               .astype('float64').to_numpy()), '_s', '_r')
    acc = {m: [] for m in act}
    for i in range(Rnull.shape[0]):
        mm = _metrics(base.assign(_r=Rnull[i]), '_s', '_r')
        for m in acc:
            acc[m].append(mm[m])
    out = []
    for mname, aval in act.items():
        arr = np.asarray(acc[mname], dtype=float)
        arr = arr[np.isfinite(arr)]
        if len(arr) == 0:
            continue
        mu, sd = float(arr.mean()), float(arr.std(ddof=1))
        z = (aval - mu) / sd if sd > 1e-12 else np.nan
        out.append(dict(null_id=nid, kind='RANDOM', entry=entry, factor=sig,
                        horizon=hz, metric=mname, actual=aval, null_mean=mu,
                        null_std=sd, null_p95=float(np.percentile(arr, 95)),
                        delta=aval - mu,
                        z=float(z) if np.isfinite(z) else np.nan,
                        p_ge=float((arr >= aval).mean()), seeds=int(len(arr))))
    return out


# ------------------------------------------------------------------ §34 反事实匹配
VARIANTS = ('UNMATCHED', 'MATCHED_COHORT', 'MATCHED_IND')


def counterfactual(d):
    rows = []
    for e in ENTRIES:
        de = d[d['E'] == e].reset_index(drop=True)
        # 匹配变量在 cohort 内标准化（原始 CTRL_* 已是 cohort 内 z 合成，此处再统一量纲）
        M = pd.DataFrame(index=de.index)
        for c in MATCH_VARS:
            v = pd.to_numeric(de[c], errors='coerce')
            g = v.groupby(de['sig_k'])
            M['m_' + c] = (v - g.transform('mean')) / g.transform('std').replace(0, np.nan)
        mcols = ['m_' + c for c in MATCH_VARS]
        Xall = M[mcols].to_numpy(float)
        need = M[mcols].notna().all(axis=1).to_numpy()
        fscv = de.groupby('sig_k')['fsc_z'].rank(pct=True).to_numpy(float)
        indv = de['ind_tx'].astype(str).to_numpy()
        groups = list(de.groupby('sig_k', sort=False).groups.items())

        for T in HORIZONS:
            rv = pd.to_numeric(de['ex_%d' % T], errors='coerce').to_numpy(float)
            acc = {v: dict(nt=0, st=0.0, ps=0.0, pn=0, pairs=[], fb=0) for v in VARIANTS}
            for _, idx in groups:
                ok = need[idx] & np.isfinite(rv[idx])
                tt = idx[ok & (fscv[idx] >= CUT_HI)]
                cc = idx[ok & (fscv[idx] <= CUT_LO)]
                if len(tt) == 0 or len(cc) == 0:
                    continue
                rt, rc = rv[tt], rv[cc]
                for v in VARIANTS:
                    a = acc[v]
                    a['nt'] += len(rt)
                    a['st'] += float(rt.sum())
                    a['ps'] += float(rc.sum())
                    a['pn'] += len(rc)
                Xt, Xc = Xall[tt], Xall[cc]
                D = ((Xt[:, None, :] - Xc[None, :, :]) ** 2).sum(axis=2)
                j_co = D.argmin(axis=1)
                same = indv[tt][:, None] == indv[cc][None, :]
                Dm = np.where(same, D, np.inf)
                j_in = Dm.argmin(axis=1)
                fb = ~np.isfinite(Dm[np.arange(len(tt)), j_in])
                acc['MATCHED_IND']['fb'] += int(fb.sum())
                j_in = np.where(fb, j_co, j_in)
                acc['MATCHED_COHORT']['pairs'].append(rt - rc[j_co])
                acc['MATCHED_IND']['pairs'].append(rt - rc[j_in])

            for v in VARIANTS:
                a = acc[v]
                if a['nt'] == 0 or a['pn'] == 0:
                    continue
                treated, pool = a['st'] / a['nt'], a['ps'] / a['pn']
                rec = dict(entry='E%d' % e, horizon='T+%d' % T, variant=v,
                           match_vars='|'.join(MATCH_VARS),
                           n_treated=a['nt'], n_control=a['pn'],
                           fallback_rate=(a['fb'] / float(a['nt'])) if v == 'MATCHED_IND' else 0.0,
                           treated_mean=treated, control_mean=pool,
                           raw_diff=treated - pool, matched_diff=np.nan,
                           matched_std=np.nan, t_stat=np.nan, net_30bp=np.nan)
                if v != 'UNMATCHED':
                    dd = np.concatenate(a['pairs']) if a['pairs'] else np.array([])
                    md = float(dd.mean()) if len(dd) else np.nan
                    sd_ = float(dd.std(ddof=1)) if len(dd) > 1 else np.nan
                    ts = (md / (sd_ / np.sqrt(len(dd)))
                          if (np.isfinite(sd_) and sd_ > 1e-12 and len(dd) > 1) else np.nan)
                    rec.update(matched_diff=md, matched_std=sd_,
                               t_stat=float(ts) if np.isfinite(ts) else np.nan,
                               net_30bp=(md - 4 * COST_BPS / 1e4) if np.isfinite(md) else np.nan)
                rows.append(rec)
        log('  E%d 反事实匹配完成，累计 %d 行' % (e, len(rows)))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ 主流程
def main():
    log('=' * 78)
    log('H-EARN-POST §32/§33/§34 Null Model 与 Counterfactual（预注册 %s）'
        % PREREG['hypothesis_id'])
    d = E.load()
    d = E.add_features(d)
    dirs = E.freeze_dir(d, E.BASE_COLS, (2018, 2022), horizon=PREREG['primary_horizon'])
    E.apply_dir(d, E.BASE_COLS, dirs)
    log('载入 %s；参照基线方向(TRAIN 冻结) %s' % ((d.shape,), {k: int(v) for k, v in dirs.items()}))

    # ---------------- N1 / N2 ----------------
    mkt = load_market()
    log('行情面板就绪：%d 只 × %d 交易日；N2 平移窗口 ±%d 日' % (
        mkt['n_codes'], mkt['NCAL'], NULL_SHIFT))
    rows = []
    for e in ENTRIES:
        de = d[d['E'] == e].reset_index(drop=True)
        grp = E.cohorts(de)
        log('-' * 78)
        log('E%d：%d 行 / %d 个 cohort' % (e, len(de), len(grp)))
        for T in HORIZONS:
            ret = 'ex_%d' % T
            R1 = null_n1(de, ret, grp)
            R2 = null_n2(de, T, mkt)
            for sig in CAND:
                rows.extend(null_rows(de, sig, ret, R1, 'E%d' % e, 'T+%d' % T, 'N1'))
                rows.extend(null_rows(de, sig, ret, R2, 'E%d' % e, 'T+%d' % T, 'N2'))
            log('    T+%d 完成（N1/N2，%d 个候选信号 × %d seeds），累计 %d 行'
                % (T, len(CAND), NSEED, len(rows)))

        # N3/N4/N5 确定性参照（无 seed 分布）
        for T in HORIZONS:
            ret = 'ex_%d' % T
            for nid, fac in REF.items():
                base = pd.DataFrame({'sig_k': de['sig_k'].to_numpy(),
                                     '_s': pd.to_numeric(de[fac], errors='coerce')
                                             .astype('float64').to_numpy()})
                mm = _metrics(base.assign(_r=pd.to_numeric(de[ret], errors='coerce')
                                          .astype('float64').to_numpy()), '_s', '_r')
                for mname, aval in mm.items():
                    rows.append(dict(null_id=nid, kind='BASELINE_REF', entry='E%d' % e,
                                     factor=fac, horizon='T+%d' % T, metric=mname,
                                     actual=aval, null_mean=np.nan, null_std=np.nan,
                                     null_p95=np.nan, delta=np.nan, z=np.nan,
                                     p_ge=np.nan, seeds=0))
        log('    E%d 的 N3/N4/N5 参照完成，累计 %d 行' % (e, len(rows)))

    ndf = pd.DataFrame(rows)
    cols = ['null_id', 'kind', 'entry', 'factor', 'horizon', 'metric', 'actual',
            'null_mean', 'null_std', 'null_p95', 'delta', 'z', 'p_ge', 'seeds']
    ndf = ndf[cols]
    ndf.to_csv(os.path.join(OUTD, 'h_earn_post_null_model.csv'),
               index=False, encoding='utf-8-sig')
    log('已存 out/h_earn_post_null_model.csv  %d 行' % len(ndf))

    # ---------------- §34 Counterfactual ----------------
    log('=' * 78)
    log('§34 Counterfactual Matching（High FSC 分位 ≥%.1f vs Control 分位 ≤%.1f）'
        % (CUT_HI, CUT_LO))
    cdf = counterfactual(d)
    cdf.to_csv(os.path.join(OUTD, 'h_earn_post_counterfactual.csv'),
               index=False, encoding='utf-8-sig')
    log('已存 out/h_earn_post_counterfactual.csv  %d 行' % len(cdf))

    # ---------------- 汇总打印 ----------------
    log('=' * 78)
    log('1) §32/§33 Null Model —— mean_ic（Primary = E20 / T+20）')
    pri = ndf[(ndf['horizon'] == 'T+20') & (ndf['metric'] == 'mean_ic')
              & (ndf['entry'] == 'E20')]
    act = pri.pivot_table(index='factor', columns='entry', values='actual', aggfunc='first')
    nn1 = pri[(pri['null_id'] == 'N1') & (pri['entry'] == 'E20')].set_index('factor')
    nn2 = pri[(pri['null_id'] == 'N2') & (pri['entry'] == 'E20')].set_index('factor')
    tab = act.copy()
    tab['N1_mean'] = nn1['null_mean']
    tab['N1_std'] = nn1['null_std']
    tab['N1_z'] = nn1['z']
    tab['N1_p'] = nn1['p_ge']
    tab['N2_mean'] = nn2['null_mean']
    tab['N2_std'] = nn2['null_std']
    tab['N2_z'] = nn2['z']
    tab['N2_p'] = nn2['p_ge']
    log(E.fmt_tbl(tab, 4))

    log('=' * 78)
    log('2) §32/§33 Null Model —— Top-Bottom 20%（Primary = E20 / T+20）')
    pr2 = ndf[(ndf['horizon'] == 'T+20') & (ndf['metric'] == 'tb20') & (ndf['entry'] == 'E20')]
    a2 = pr2.pivot_table(index='factor', columns='entry', values='actual', aggfunc='first')
    m1 = pr2[pr2['null_id'] == 'N1'].set_index('factor')
    m2 = pr2[pr2['null_id'] == 'N2'].set_index('factor')
    t2 = a2.copy()
    t2['N1_mean'] = m1['null_mean']
    t2['N1_z'] = m1['z']
    t2['N1_p'] = m1['p_ge']
    t2['N2_mean'] = m2['null_mean']
    t2['N2_z'] = m2['z']
    t2['N2_p'] = m2['p_ge']
    log(E.fmt_tbl(t2, 4))

    log('=' * 78)
    log('3) N1 零假设方差自检（mean_ic 的零假设 std 应随 cohort 样本量收缩）')
    chk = ndf[(ndf['null_id'] == 'N1') & (ndf['metric'] == 'mean_ic')
              & (ndf['horizon'] == 'T+20')].pivot_table(index='entry', columns='factor',
                                                        values='null_std')
    log(E.fmt_tbl(chk, 4))

    log('=' * 78)
    log('4) §33 关键判定（Primary = E20 / T+20 / mean_ic，门槛 z ≥ %.1f）' % Z_PASS)
    for fac in ('SIG_FSC', 'SIG_RESID'):
        r1 = pri[(pri['factor'] == fac) & (pri['null_id'] == 'N1') & (pri['entry'] == 'E20')]
        r2 = pri[(pri['factor'] == fac) & (pri['null_id'] == 'N2') & (pri['entry'] == 'E20')]
        if r1.empty or r2.empty:
            continue
        a = float(r1['actual'].iloc[0])
        mom = pri[(pri['factor'] == 'B1_MOM') & (pri['entry'] == 'E20')]
        mom_ic = float(mom['actual'].iloc[0]) if len(mom) else np.nan
        log('  %-9s actual=%.4f | N1 z=%.2f p=%.3f (%s) | N2 z=%.2f p=%.3f (%s) | Mom=%.4f'
            % (fac, a, r1['z'].iloc[0], r1['p_ge'].iloc[0],
               'PASS' if r1['z'].iloc[0] >= Z_PASS else 'FAIL-NO_EVENT_ALPHA',
               r2['z'].iloc[0], r2['p_ge'].iloc[0],
               'PASS' if r2['z'].iloc[0] >= Z_PASS else 'FAIL-TIME_EFFECT',
               mom_ic))
        log('    vs Momentum: %s'
            % ('NO_INCREMENTAL_ALPHA (低于 B1_MOM)' if (np.isfinite(mom_ic) and a < mom_ic)
               else '高于 B1_MOM'))

    log('=' * 78)
    log('5) §34 Counterfactual —— matched_diff（High FSC vs Matched Control，全部 Entry/Horizon）')
    cm = cdf[cdf['variant'] != 'UNMATCHED']
    piv = cm.pivot_table(index=['entry', 'horizon'], columns='variant',
                         values='matched_diff')
    log(E.fmt_tbl(piv, 4))
    log('  净额（matched_diff - 4×30bp）：')
    piv2 = cm.pivot_table(index=['entry', 'horizon'], columns='variant', values='net_30bp')
    log(E.fmt_tbl(piv2, 4))
    log('  匹配后相对未匹配的衰减（UNMATCHED raw_diff vs MATCHED_IND matched_diff，T+20）：')
    p20 = cdf[cdf['horizon'] == 'T+20'].pivot_table(index='entry', columns='variant',
                                                    values=['raw_diff', 'matched_diff'])
    log(E.fmt_tbl(p20, 4))
    log('  匹配 fallback（同行业内无对照，退化为 cohort 内匹配）比例（T+20）：')
    fb = cdf[(cdf['horizon'] == 'T+20') & (cdf['variant'] == 'MATCHED_IND')]
    log(E.fmt_tbl(fb.set_index('entry')[['n_treated', 'n_control', 'fallback_rate', 't_stat']], 4))
    log('=' * 78)
    log('DONE')
    log.save()


if __name__ == '__main__':
    main()
