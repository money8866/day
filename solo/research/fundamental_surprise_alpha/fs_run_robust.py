# -*- coding: utf-8 -*-
"""fs_run_robust: 稳健性检验
  §26 入场锚点 E1/E2/E3/E5（同一静态基本面特征在不同可交易锚点下的 IC）
  §28 OOS 硬门槛（TRAIN/VALID/OOS 三期方向一致性）
  §29 Walk Forward W1-W6
  §30 Regime (BULL/NORMAL/BEAR)
  §31 Counterfactual 匹配
  §32 参数稳定性网格（Momentum 窗口 / Fundamental 历史 / Price reaction 窗口 / Industry SW1-SW2）

依赖 fs_run_core 的 add_combos，保证组合特征口径与核心检验完全一致。
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fs_engine as E
from fs_common import DATA, OUTD, HORIZONS, Log
from fs_run_core import add_combos

log = Log('_fs_run_robust.txt')

WF = [('W1', '20180101', '20201231', '2021'),
      ('W2', '20190101', '20211231', '2022'),
      ('W3', '20200101', '20221231', '2023'),
      ('W4', '20210101', '20231231', '2024'),
      ('W5', '20220101', '20241231', '2025'),
      ('W6', '20230101', '20251231', '2026')]

VIEWS = ['E1', 'E2', 'E3', 'E5']

# 所有稳健性检验的探针集合：固定代表特征 ∪ 按 |T+5 raw IC| 排名靠前的候选
# （保证真正参与裁定的头部候选都有 §26/§32 结论，而不是只测一批无人关心的代表特征）
PROBE_BASE = ['S1_np', 'S2_np', 'S3_np', 'np_yoy', 'rev_yoy', 'dp_yoy']
PROBE_EXTRA = ['S1_rev', 'ocf_to_np', 'S4_np_vs_rev', 'S5_np_vs_ocf',
               'S6_g_cash', 'S6_g_margin']


def cand_probe(df, base=None, n=24):
    lst = list(base if base is not None else (PROBE_BASE + PROBE_EXTRA))
    icp = os.path.join(OUTD, 'ic_full.csv')
    if os.path.exists(icp):
        _ic = pd.read_csv(icp)
        _z = _ic[(_ic['anchor'] == 'E1') & (_ic['variant'] == 'raw')
                 & (_ic['horizon'] == 5) & (_ic['period'] == 'ALL')]
        _z = _z[np.isfinite(_z['ic_mean'])]
        _z = _z.reindex(_z['ic_mean'].abs().sort_values(ascending=False).index)
        lst = list(dict.fromkeys(lst + _z['feature'].head(n).tolist()))
    return [c for c in lst if c in df.columns]


def load_panel():
    df = pd.read_parquet(os.path.join(DATA, 'panel.parquet'))
    df = E.add_derived(df)
    df = E.add_ind_l2(df)
    df = E.add_hist_variants(df)
    df, asof = E.apply_asof_mask(df)
    log('  §5 as-of 掩码: %s' % asof)
    gr = df.groupby(['k', 'ind_l1'])['mom_20']
    r = gr.rank(method='average')
    n = gr.transform('size')
    df['ind_rs'] = np.where(n > 1, (r - 1) / (n - 1) - 0.5, np.nan)
    static = [c for c in (E.FUND_ALL + E.MOM_RAW + E.VALUE_RAW + E.SIZE_RAW + ['ind_rs'])
              if c in df.columns]
    Zsrc = static + [c for c in E.REACT_ALL if c in df.columns]
    Z0 = E.g_z(df, Zsrc)
    R0 = E.g_rank(df, Zsrc)
    df = add_combos(df, Z0, R0)
    return df


def cal_map():
    cal = pd.read_parquet(os.path.join(DATA, 'calendar.parquet'))
    return dict(zip(cal['idx'].astype(int), cal['trade_date'].astype(str)))


def index_regime():
    lv = pd.read_parquet(os.path.join(DATA, 'index_level.parquet')).sort_values('trade_date')
    s = pd.Series(lv['idx_level'].values, index=lv['trade_date'].astype(str).values)
    ma200 = s.rolling(200, min_periods=60).mean()
    r60 = s / s.shift(60) - 1.0
    reg = pd.Series('NORMAL', index=s.index)
    reg[(s > ma200) & (r60 > 0)] = 'BULL'
    reg[(s < ma200) & (r60 < 0)] = 'BEAR'
    return reg


def add_e5_view(df):
    """§26 E5：入场 = k1+4 收盘（ex_E5_T5/T20 已由面板预置）"""
    ok = df['entry_px_E5'].notna() & (df['entry_px_E5'] > 0)
    log('  E5 视角可交易比例 %.4f' % ok.mean())
    return df


def nframe(df, cols, ctrls, min_n, indc=None, anchor='E1', hs=(5, 20)):
    """中性化后补回 cohort 键与目标列"""
    R = E.neutralize(df, cols, ctrls, min_n=min_n, ind_col=indc)
    R['k'] = df['k'].values
    R['period'] = df['period'].values
    for h in hs:
        col = E.tgt(h, anchor)
        if col in df.columns:
            R[col] = df[col].values
    return R


def ic_of(df, cols, ycol):
    per = df.drop_duplicates('k').set_index('k')['period'].to_dict()
    icd = E.cohort_ic(df, cols, ycol)
    return {c: (E.ic_stats(*icd[c], per) if len(icd[c][0]) else None) for c in cols}


def main():
    log('=' * 64)
    log('0) 载入面板')
    df = load_panel()
    df = add_e5_view(df)
    kmap = cal_map()
    log('  面板 %d 行 / %d cohort' % (len(df), df['k'].nunique()))

    # ---------- §26 入场锚点 ----------
    log('=' * 64)
    log('1) §26 入场锚点（同一静态基本面特征 × 不同可交易锚点）')
    static_probe = cand_probe(df)
    log('    探针特征 %d 个' % len(static_probe))
    ar = []
    for a in VIEWS:
        for h in (5, 20):
            yc = E.tgt(h, a)
            if yc not in df.columns:
                continue
            st = ic_of(df, static_probe, yc)
            for c, s in st.items():
                if s is None:
                    continue
                ar.append(dict(anchor=a, horizon=h, feature=c,
                               ic_mean=s['ALL']['ic_mean'], icir=s['ALL']['icir'],
                               n_coh=s['ALL']['n_coh'],
                               ic_train=s['TRAIN']['ic_mean'],
                               ic_valid=s['VALID']['ic_mean'],
                               ic_oos=s['OOS']['ic_mean']))
        log('    %s done' % a)
    pd.DataFrame(ar).to_csv(os.path.join(OUTD, 'entry_anchor.csv'),
                            index=False, encoding='utf-8-sig')

    # ---------- §28 / §29 / §30 ----------
    log('=' * 64)
    log('2) §28 OOS 门槛 / §29 Walk Forward / §30 Regime')
    coh = pd.read_parquet(os.path.join(OUTD, 'cohort_ic.parquet'))
    coh['entry_date'] = coh['k'].map(kmap).astype(str)
    reg = index_regime()
    coh['regime'] = coh['entry_date'].map(reg).fillna('NORMAL')

    wf_rows, rg_rows = [], []
    for (a, c, h), v in coh.groupby(['anchor', 'feature', 'horizon'], sort=False):
        v = v[np.isfinite(v['ic'])]
        if len(v) < 30:
            continue
        for nm, s, e, oy in WF:
            tr = v[(v['entry_date'] >= s) & (v['entry_date'] <= e)]
            oo = v[v['entry_date'].str[:4] == oy]
            if len(tr) < 20 or len(oo) < 10:
                continue
            mt, mo = tr['ic'].mean(), oo['ic'].mean()
            wf_rows.append(dict(anchor=a, feature=c, horizon=h, window=nm,
                                n_train=len(tr), ic_train=mt,
                                n_oos=len(oo), ic_oos=mo,
                                same_sign=bool(np.sign(mt) == np.sign(mo))))
        for rn in ('BULL', 'NORMAL', 'BEAR'):
            vv = v[v['regime'] == rn]
            if len(vv) < 20:
                continue
            sd = vv['ic'].std(ddof=1)
            rg_rows.append(dict(anchor=a, feature=c, horizon=h, regime=rn,
                                n=len(vv), ic_mean=vv['ic'].mean(),
                                icir=vv['ic'].mean() / sd if sd > 1e-12 else np.nan,
                                pos=float((vv['ic'] > 0).mean())))
    pd.DataFrame(wf_rows).to_csv(os.path.join(OUTD, 'walkforward.csv'),
                                 index=False, encoding='utf-8-sig')
    pd.DataFrame(rg_rows).to_csv(os.path.join(OUTD, 'regime.csv'),
                                 index=False, encoding='utf-8-sig')
    log('  WF %d 行 / Regime %d 行' % (len(wf_rows), len(rg_rows)))

    # OOS 硬门槛表（TRAIN/VALID/OOS 三期）
    ic = pd.read_csv(os.path.join(OUTD, 'ic_full.csv'))
    oo = ic[(ic['variant'] == 'raw') & (ic['period'].isin(['TRAIN', 'VALID', 'OOS']))]
    oo = oo.pivot_table(index=['anchor', 'feature', 'horizon'], columns='period',
                        values='ic_mean').reset_index()
    oo.columns.name = None
    for col in ('TRAIN', 'VALID', 'OOS'):
        if col not in oo.columns:
            oo[col] = np.nan
    oo['same_sign_3'] = ((np.sign(oo['TRAIN']) == np.sign(oo['VALID']))
                         & (np.sign(oo['VALID']) == np.sign(oo['OOS'])))
    oo.to_csv(os.path.join(OUTD, 'oos.csv'), index=False, encoding='utf-8-sig')
    log('  OOS 表 %d 行（三期同向占比 %.4f）' % (
        len(oo), float(oo['same_sign_3'].mean())))

    # ---------- §31 Counterfactual ----------
    log('=' * 64)
    log('3) §31 Counterfactual 匹配（日期×行业×市值×流动性×动量×估值）')
    feats = [c for c in E.view_features(df, 'E1')
             if c in df.columns and df[c].notna().sum() > 10000]
    cf_rows = []
    for c in feats:
        for h in (5, 20):
            yc = E.tgt(h, 'E1')
            t = df[['k', 'ind_l1', yc, c, 'ln_mv', 'turnover_rate',
                    'mom_20', 'bp']].copy()
            t.columns = ['k', 'ind', 'y', 'x', 'mv', 'liq', 'mom', 'val']
            t = t[np.isfinite(t['x']) & np.isfinite(t['y'])]
            if len(t) < 500:
                continue
            for cc in ('mv', 'liq', 'mom', 'val'):
                t[cc + '_t'] = pd.qcut(t[cc].rank(method='first'), 3,
                                       labels=False, duplicates='drop')
            keys = ['k', 'ind', 'mv_t', 'liq_t', 'mom_t', 'val_t']
            med = t.groupby(keys)['x'].transform('median')
            t['hi'] = t['x'] > med
            mh = t[t['hi']].groupby(keys)['y'].mean()
            ml = t[~t['hi']].groupby(keys)['y'].mean()
            nh = t[t['hi']].groupby(keys)['y'].size()
            nl = t[~t['hi']].groupby(keys)['y'].size()
            d = (mh - ml).dropna()
            keep = (nh.reindex(d.index) >= 2) & (nl.reindex(d.index) >= 2)
            d = d[keep.values]
            if len(d) == 0:
                continue
            sd = d.std(ddof=1)
            cf_rows.append(dict(feature=c, horizon=h, n_cell=int(len(d)),
                                cf_alpha=float(d.mean()),
                                cf_t=float(d.mean() / (sd / np.sqrt(len(d))))
                                if len(d) > 1 and sd > 1e-12 else np.nan))
        log('    %s done' % c)
    pd.DataFrame(cf_rows).to_csv(os.path.join(OUTD, 'counterfactual.csv'),
                                 index=False, encoding='utf-8-sig')

    # ---------- §32 参数稳定性 ----------
    pg = run_param_grid(df)
    pd.DataFrame(pg).to_csv(os.path.join(OUTD, 'parameter_grid.csv'),
                            index=False, encoding='utf-8-sig')

    log('=' * 64)
    log('稳健性检验完成')
    log.save()
    print('DONE')


def run_param_grid(df):
    """§32 参数稳定性网格（Momentum 窗口 / Fundamental 历史 / Price reaction 窗口 / Industry SW1-SW2）"""
    log('=' * 64)
    log('4) §32 参数网格')
    pg = []

    # 探针集合 = 固定代表特征 ∪ 按 |T+5 raw IC| 排名靠前的候选（保证头部候选有 G14 结论）
    probe = cand_probe(df)
    log('    探针特征 %d 个' % len(probe))

    def add_pg(param, val, res, anchor='E1'):
        for h in (5, 20):
            yc = E.tgt(h, anchor)
            if yc not in df.columns:
                continue
            st = ic_of(res, probe, yc)
            for c, s in st.items():
                if s is None:
                    continue
                pg.append(dict(param=param, value=val, anchor=anchor, feature=c,
                               horizon=h, ic_resid=s['ALL']['ic_mean'],
                               n_coh=s['ALL']['n_coh']))

    # (a) Momentum 控制窗口 5 / 10 / 20 / 60
    for w in (5, 10, 20, 60):
        R = nframe(df, probe, ['mom_%d' % w], E.REQ['mom'])
        add_pg('momentum_window', w, R)
        log('    mom=%d done' % w)

    # (b) Fundamental 历史窗口 4 / 8 / 12 季度
    for hh in (4, 8, 12):
        cols = ['S1h%d_np' % hh, 'S1h%d_rev' % hh,
                'S2h%d_np' % hh, 'S2h%d_rev' % hh]
        cols = [c for c in cols if c in df.columns]
        for h in (5, 20):
            yc = E.tgt(h, 'E1')
            st = ic_of(df, cols, yc)
            for c, s in st.items():
                if s is None:
                    continue
                pg.append(dict(param='fund_history', value=hh, anchor='E1', feature=c,
                               horizon=h, ic_resid=s['ALL']['ic_mean'],
                               n_coh=s['ALL']['n_coh']))
        log('    hist=%d done' % hh)

    # (c) Price reaction 窗口 1 / 3 / 5 日（与入场锚点严格绑定）
    react_feats = {'E1': ['S1_np', 'S2_np', 'S3_np'],
                   'E3': ['S1_np', 'S2_np', 'S3_np',
                          E.combo_name('S1_np', 'Cum_R3')[0]],
                   'E5': ['S1_np', 'S2_np', 'S3_np',
                          E.combo_name('S1_np', 'Cum_R3')[0]]}
    for w, a in ((1, 'E1'), (3, 'E3'), (5, 'E5')):
        cols = [c for c in react_feats[a] if c in df.columns]
        for h in (5, 20):
            yc = E.tgt(h, a)
            if yc not in df.columns:
                continue
            st = ic_of(df, cols, yc)
            for c, s in st.items():
                if s is None:
                    continue
                pg.append(dict(param='price_reaction_window', value=w, anchor=a,
                               feature=c, horizon=h, ic_resid=s['ALL']['ic_mean'],
                               n_coh=s['ALL']['n_coh']))
        log('    react=%d(%s) done' % (w, a))

    # (d) 行业层级 SW1 vs SW2
    for lvl_name, colname in (('SW1', 'ind_l1'), ('SW2', 'ind_l2')):
        R = nframe(df, probe, [], 8, indc=colname)
        add_pg('industry_level', lvl_name, R)
        log('    ind=%s done' % lvl_name)
    return pg


if __name__ == '__main__':
    main()
