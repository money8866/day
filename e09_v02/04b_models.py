# -*- coding: utf-8 -*-
"""E09-V02 Step 4b：仅跑 §29 增量模型 A/B/C/D（04 的前四步已产出）"""
import os
import sqlite3
import numpy as np
import pandas as pd
import e09_lib as E
import importlib.util as _ilu

_sp = _ilu.spec_from_file_location('m04', os.path.join(os.path.dirname(
    os.path.abspath(__file__)), '04_momentum_mechanism.py'))
_m04 = _ilu.module_from_spec(_sp)
_sp.loader.exec_module(_m04)
build_reg_sample = _m04.build_reg_sample

TAG = 'main'


def main():
    ev = pd.read_parquet(os.path.join(E.DATA, 'events_e09.parquet'))
    c3 = ev['meanvr_3'].values <= 0.70
    s3 = ev['minlow_3'].values >= 0.95

    print('构建回归样本 ...', flush=True)
    p, s = build_reg_sample()
    NP = len(p)
    flag = np.zeros(NP, dtype=np.int8)
    d = ev['dB_B_main3'].values
    okk = np.isfinite(d) & c3 & s3
    rr_ = np.clip(ev['row'].values[okk] + d[okk].astype(np.int64), 0, NP - 1)
    flag[rr_] = 1
    s['e09'] = flag[s['orig_row'].values]
    print(f'  回归样本 {len(s):,}  其中 E09 确认日 {int(s["e09"].sum()):,}', flush=True)

    ix = pd.read_sql("select trade_date, close from index_daily_cache where ts_code='000001.SH' order by trade_date",
                     sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db'))
    ix['ma20'] = ix['close'].rolling(20, min_periods=20).mean()
    ix['ma60'] = ix['close'].rolling(60, min_periods=60).mean()
    ix['regime'] = np.where((ix['close'] > ix['ma20']) & (ix['ma20'] > ix['ma60']), 'Bull',
                    np.where((ix['close'] < ix['ma20']) & (ix['ma20'] < ix['ma60']), 'Bear', 'Neutral'))
    s['regime'] = s['trade_date'].map(dict(zip(ix['trade_date'].astype(np.int64), ix['regime']))).fillna('Neutral')

    rows = []
    for H in (5, 10):
        ycol = f'fwd{H}'
        d2 = s[np.isfinite(s[ycol])].copy()
        isr = (d2['trade_date'] <= E.IS_END).values
        y = d2[ycol].values.astype(np.float64)
        grp = d2['ts_code'].values
        momX = d2[['mom5', 'mom10', 'mom20']].values.astype(np.float64)
        volX = d2[['volr', 'amtr']].values.astype(np.float64)
        e09X = d2['e09'].values.astype(np.float64).reshape(-1, 1)
        rg = pd.get_dummies(d2['regime'], prefix='rg').reindex(
            columns=['rg_Bull', 'rg_Bear', 'rg_Neutral'], fill_value=0).values.astype(float)

        def ztr(X):
            mu = X[isr].mean(axis=0)
            sd = X[isr].std(axis=0)
            sd = np.where(sd > 0, sd, 1.0)
            return (X - mu) / sd

        A = ztr(momX)
        B = np.column_stack([A, ztr(volX)])
        C = np.column_stack([B, e09X])
        D = np.column_stack([C, rg[:, :2]])
        names = {0: ['const', 'mom5', 'mom10', 'mom20'],
                 1: ['const', 'mom5', 'mom10', 'mom20', 'volr', 'amtr'],
                 2: ['const', 'mom5', 'mom10', 'mom20', 'volr', 'amtr', 'e09'],
                 3: ['const', 'mom5', 'mom10', 'mom20', 'volr', 'amtr', 'e09', 'rg_Bull', 'rg_Bear']}
        prev_oos = None
        for mi, X in enumerate([A, B, C, D]):
            nm = names[mi]
            fit = E.ols_cluster(y[isr], X[isr], grp[isr])
            beta = fit['beta']
            Xo = np.column_stack([np.ones((~isr).sum()), X[~isr]])
            yo = y[~isr]
            pred = Xo @ beta
            sse = ((yo - pred) ** 2).sum()
            sst = ((yo - yo.mean()) ** 2).sum()
            r2_oos = 1 - sse / sst if sst > 0 else np.nan
            for j, cname in enumerate(nm):
                rows.append(dict(horizon=H, model='ABCD'[mi], coef=cname,
                                 beta=fit['beta'][j], se=fit['se'][j], t=fit['t'][j],
                                 n_is=fit['n'], adj_r2_is=fit['adj_r2'],
                                 r2_oos=r2_oos,
                                 dr2_oos_vs_prev=(r2_oos - prev_oos) if prev_oos is not None else np.nan))
            print(f'  H={H} Model {"ABCD"[mi]}: adj_R2(IS)={fit["adj_r2"]:.6f} R2(OOS)={r2_oos:.6f} '
                  f'ΔR2(OOS)={(r2_oos - prev_oos) if prev_oos is not None else float("nan"):+.6f}', flush=True)
            prev_oos = r2_oos
    mdl = pd.DataFrame(rows)
    E.save(mdl, 'e09_v02_models.csv')
    print('\n=== E09 系数（关键 B→C）===')
    print(mdl[mdl.coef == 'e09'][['horizon', 'model', 'beta', 'se', 't', 'adj_r2_is', 'r2_oos', 'dr2_oos_vs_prev']].round(6).to_string(index=False))
    print('\n=== 各模型拟合度 ===')
    print(mdl[mdl.coef == 'const'][['horizon', 'model', 'adj_r2_is', 'r2_oos', 'dr2_oos_vs_prev']].round(6).to_string(index=False))
    print('\nDONE', flush=True)


if __name__ == '__main__':
    main()
