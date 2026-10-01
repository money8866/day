# -*- coding: utf-8 -*-
"""
HVE-Research V1  Step 2
标志高量事件(HVE)定义研究 + 事件聚类 + 事件数据集

预注册候选（不因结果事后修改）：
  量能指标  vr5 / vr10 / vr20 / vr60 / amt_r20 / vol_pct250 / turn_pct250
  绝对阈值  1.5 2.0 2.5 3.0 4.0
  分位阈值  90% 95% 97.5% 99%
  价格条件  pct_chg>=3/5/7, clv>=0.6/0.7/0.8, close>ma20, close>ma60, close>hh20
  结构条件  ma20_slope5>0, close>ma60

选择流程（严格）：IS(起始~2023-06-30) 评估 -> OOS(2023-07~2026-09) 验证 -> 参数扰动
"""
import os
import numpy as np
import pandas as pd
import hve_lib as L

OUT = r'D:\mystock\hve_research\out'
os.makedirs(OUT, exist_ok=True)

IS_END = 20230630          # IS: 起始 ~ 2023-06-30

VOL_CANDS = {
    'vr5': [1.5, 2.0, 2.5, 3.0, 4.0],
    'vr10': [1.5, 2.0, 2.5, 3.0, 4.0],
    'vr20': [1.5, 2.0, 2.5, 3.0, 4.0],
    'vr60': [1.5, 2.0, 2.5, 3.0, 4.0],
    'amt_r20': [1.5, 2.0, 2.5, 3.0, 4.0],
    'vol_pct250': [0.90, 0.95, 0.975, 0.99],
    'turn_pct250': [0.90, 0.95, 0.975, 0.99],
}
PRICE_CANDS = {
    'pct3': ('pct_chg', 3.0), 'pct5': ('pct_chg', 5.0), 'pct7': ('pct_chg', 7.0),
    'clv60': ('clv', 0.60), 'clv70': ('clv', 0.70), 'clv80': ('clv', 0.80),
    'c_ma20': ('dist_ma20', 0.0), 'c_ma60': ('dist_ma60', 0.0), 'c_hh20': ('dist_hh20', 0.0),
}
STRUCT_CANDS = {'slope20': ('ma20_slope5', 0.0), 'c_ma60b': ('dist_ma60', 0.0)}

BASE_COLS = ['ts_code', 'name', 'industry', 'board', 'trade_date', 'td_idx', 'seq',
             'obs_days', 'is_st', 'is_delist', 'list_date', 'in_basic',
             'open', 'high', 'low', 'close', 'ret', 'pct_chg', 'vol', 'amount',
             'turnover_rate', 'total_mv', 'circ_mv',
             'ma5', 'ma10', 'ma20', 'ma60', 'vr5', 'vr10', 'vr20', 'vr60', 'amt_r20',
             'vma5', 'vma10', 'vma20', 'vma60', 'vol_pct250', 'amt_pct250', 'turn_pct250',
             'hh20', 'hh60', 'll20', 'ch20', 'ch60', 'clv', 'vol20', 'vol60',
             'ret5', 'ret20', 'ret60', 'ma20_slope5', 'ma20_slope10', 'ma60_slope10',
             'dist_ma20', 'dist_ma60', 'dist_hh20', 'dist_hh60',
             'up_limit', 'dn_limit', 'yizi']


def build_pool():
    p, m, cal = L.load_panel()
    oc_path = os.path.join(L.DATA, 'outcomes.parquet')
    oc = pd.read_parquet(oc_path) if os.path.exists(oc_path) else L.compute_outcomes(p)
    m = L.add_mkt_fwd(m)

    keep = [c for c in BASE_COLS if c in p.columns]
    d = p[keep].copy()
    d['row'] = np.arange(len(d), dtype=np.int64)
    d = d.merge(oc, on='row', how='left')
    mf = m[['td_idx'] + [f'mkt_fwd_{h}' for h in L.HORIZONS] +
           [f'mktmed_fwd_{h}' for h in L.HORIZONS]]
    d = d.merge(mf, on='td_idx', how='left')
    for h in L.HORIZONS:
        d[f'exc_{h}'] = d[f'ret_{h}'] - d[f'mkt_fwd_{h}']
        d[f'excmed_{h}'] = d[f'ret_{h}'] - d[f'mktmed_fwd_{h}']

    d['in_basic'] = (~d['name'].isna()).astype(np.int8)
    elig = (
        (d['board'].fillna('BJ') != 'BJ') &
        (d['is_st'].fillna(0) == 0) & (d['is_delist'].fillna(0) == 0) &
        (d['obs_days'] >= 60) &
        d['vr20'].notna() & d['ma20'].notna() & d['ma60'].notna() &
        d['hh20'].notna() & d['vol_pct250'].notna() & d['clv'].notna() &
        d['ret_1'].notna()
    )
    d = d[elig].copy()
    print('eligible rows', len(d), flush=True)

    loose = (
        (d['vr5'] >= 1.5) | (d['vr10'] >= 1.5) | (d['vr20'] >= 1.5) | (d['vr60'] >= 1.5) |
        (d['amt_r20'] >= 1.5) | (d['vol_pct250'] >= 0.90) | (d['turn_pct250'] >= 0.90)
    )
    pool = d[loose].copy()
    print('pool rows', len(pool), flush=True)
    pool.to_parquet(os.path.join(L.DATA, 'pool.parquet'), index=False)
    return pool


class Pool:
    """numpy 视图，便于高速筛选统计"""

    def __init__(self, pool):
        self.df = pool
        self.date = pool['trade_date'].values
        self.is_mask = self.date <= IS_END
        self.oos_mask = ~self.is_mask
        self.ret = {h: pool[f'ret_{h}'].values for h in L.HORIZONS}
        self.exc = {h: pool[f'exc_{h}'].values for h in L.HORIZONS}
        self.excmed = {h: pool[f'excmed_{h}'].values for h in L.HORIZONS}
        self.mfe = {h: pool[f'mfe_{h}'].values for h in L.HORIZONS}
        self.mae = {h: pool[f'mae_{h}'].values for h in L.HORIZONS}
        self.maxret = {h: pool[f'maxret_{h}'].values for h in L.HORIZONS}
        self.cols = {c: pool[c].values for c in pool.columns}

    def stats(self, mask, h, segmask=None):
        m = mask if segmask is None else (mask & segmask)
        r = self.ret[h][m]
        st = L.stat(r, 0.0, self.exc[h][m])
        st['mfe'] = float(np.nanmean(self.mfe[h][m])) if m.any() else np.nan
        st['mae'] = float(np.nanmean(self.mae[h][m])) if m.any() else np.nan
        if m.any():
            em = self.excmed[h][m]
            em = em[~np.isnan(em)]
            st['excmed'] = float(em.mean()) if len(em) else np.nan
            st['excmed_win'] = float((em > 0).mean()) if len(em) else np.nan
        return st


def eval_grid(P, horizons=(5, 10, 20)):
    rows = []
    for metric, ths in VOL_CANDS.items():
        v = P.cols[metric]
        for th in ths:
            with np.errstate(invalid='ignore'):
                m0 = np.nan_to_num(v, nan=-1e9) >= th
            for seg, sm in (('IS', P.is_mask), ('OOS', P.oos_mask), ('ALL', None)):
                mm = m0 if sm is None else (m0 & sm)
                if mm.sum() < 200:
                    continue
                for h in horizons:
                    st = P.stats(m0, h, sm)
                    rows.append(dict(cand=f'{metric}>={th}', segment=seg, horizon=h,
                                     n=st['n'], win=st['win'], mean=st['mean'],
                                     median=st['median'], pf=st['pf'], t=st['t'],
                                     exc=st.get('exc_mean', np.nan),
                                     exc_win=st.get('exc_win', np.nan),
                                     exc_t=st.get('exc_t', np.nan),
                                     excmed=st.get('excmed', np.nan),
                                     excmed_win=st.get('excmed_win', np.nan),
                                     mfe=st['mfe'], mae=st['mae']))
    g = pd.DataFrame(rows)
    g.to_csv(os.path.join(OUT, 'cand_grid_vol.csv'), index=False, encoding='utf-8-sig')
    return g


def eval_layers(P, base_metric='vr20', base_th=2.0, horizons=(5, 10, 20)):
    v = P.cols[base_metric]
    base = np.nan_to_num(v, nan=-1e9) >= base_th
    layers = {'A_vol': []}
    for name, cond in PRICE_CANDS.items():
        layers['B_' + name] = [cond]
    for name, cond in PRICE_CANDS.items():
        layers['B+C_' + name + '_slope'] = [cond, STRUCT_CANDS['slope20']]
    rows = []
    for lname, conds in layers.items():
        m0 = base.copy()
        for col, th in conds:
            m0 &= np.nan_to_num(P.cols[col], nan=-1e9) >= th
        for seg, sm in (('IS', P.is_mask), ('OOS', P.oos_mask)):
            mm = m0 & sm
            if mm.sum() < 200:
                continue
            for h in horizons:
                st = P.stats(m0, h, sm)
                rows.append(dict(layer=lname, segment=seg, horizon=h, n=st['n'],
                                 win=st['win'], mean=st['mean'], median=st['median'],
                                     pf=st['pf'], t=st['t'], exc=st.get('exc_mean', np.nan),
                                     exc_t=st.get('exc_t', np.nan),
                                     excmed=st.get('excmed', np.nan),
                                     mfe=st['mfe'], mae=st['mae']))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, 'cand_layers.csv'), index=False, encoding='utf-8-sig')
    return df


def cluster_events(pool, metric, th, gap, mode='first'):
    sub = pool[pool[metric] >= th].copy()
    sub = sub.sort_values(['ts_code', 'seq'])
    g = sub.groupby('ts_code', sort=False)
    gap_prev = g['seq'].diff()
    new_cl = gap_prev.isna() | (gap_prev > gap)
    sub['cluster_id'] = new_cl.groupby(sub['ts_code']).cumsum()
    size = sub.groupby(['ts_code', 'cluster_id']).size().rename('cluster_size')
    if mode == 'first':
        idx = sub.groupby(['ts_code', 'cluster_id'])['seq'].idxmin()
    elif mode == 'maxvol':
        idx = sub.groupby(['ts_code', 'cluster_id'])['vol'].idxmax()
    else:
        idx = sub.groupby(['ts_code', 'cluster_id'])['amount'].idxmax()
    ev = sub.loc[idx].merge(size.reset_index(), on=['ts_code', 'cluster_id'], how='left')
    return ev


def eval_clustering(P, pool, metric='vr20', th=2.0, horizons=(5, 10, 20)):
    rows = []
    for gap in (0, 3, 5, 10):
        for mode in ('first', 'maxvol', 'maxamt'):
            ev = cluster_events(pool, metric, th, gap, mode)
            for seg, test in (('IS', lambda x: x['trade_date'] <= IS_END),
                              ('OOS', lambda x: x['trade_date'] > IS_END)):
                s = ev[test(ev)]
                for h in horizons:
                    st = L.stat_full(s, f'ret_{h}', f'mfe_{h}', f'mae_{h}', 0.0, f'exc_{h}')
                    rows.append(dict(gap=gap, mode=mode, segment=seg, horizon=h, n=st['n'],
                                     win=st['win'], mean=st['mean'], median=st['median'],
                                     pf=st['pf'], t=st['t'], exc=st.get('exc_mean', np.nan),
                                     mfe=st['mfe'], mae=st['mae']))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, 'cand_cluster.csv'), index=False, encoding='utf-8-sig')
    return df


def show(g, h=10, cols=('n', 'mean', 'exc', 'win', 'pf', 'exc_t')):
    x = g[g['horizon'] == h]
    key = 'cand' if 'cand' in x.columns else ('layer' if 'layer' in x.columns else None)
    if key is None:
        return x
    p = x.pivot_table(index=key, columns='segment', values=list(cols))
    return p.round(4)


def main():
    pool = build_pool()
    P = Pool(pool)
    print('--- volume candidate grid (T+10) ---')
    g = eval_grid(P)
    print(show(g, 10).to_string())
    print()
    print('--- layers (base vr20>=2.0, T+10) ---')
    pl = eval_layers(P)
    print(show(pl, 10).to_string())
    print()
    print('--- clustering (T+10, vr20>=2.0) ---')
    cl = eval_clustering(P, pool)
    print(cl[cl['horizon'] == 10].to_string(index=False))


if __name__ == '__main__':
    main()
