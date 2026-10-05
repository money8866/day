# -*- coding: utf-8 -*-
"""
E09-V02 Step 4：
  §29 增量模型 Model A/B/C/D（OLS + 按个股聚类稳健标准误）
  §17 Momentum 匹配与分层
  §30 机制分析 G1-G4
  §31 量能衰减形态
  §32 量缩 + 价格波动收缩

产出：
  e09_v02_momentum_compare.csv / e09_v02_mechanism.csv
  e09_v02_volume_decay.csv / e09_v02_price_contraction.csv / e09_v02_models.csv
"""
import os
import sqlite3
import numpy as np
import pandas as pd
import e09_lib as E

TAG = 'main'


# ---------------- 回归样本 ----------------
def build_reg_sample():
    p = pd.read_parquet(E.PANEL, columns=['ts_code', 'trade_date', 'td_idx', 'close',
                                          'vol', 'amount', 'ma20', 'vma20', 'pct_chg',
                                          'is_st', 'obs_days'])
    p['_c'] = pd.factorize(p['ts_code'], sort=False)[0]
    E.add_roll(p, 'amount', 20, 'mean', 0, 20, 'amt20')
    g = p.groupby('_c', sort=False)
    for k in (5, 10, 20):
        p[f'mom{k}'] = p['close'] / g['close'].shift(k) - 1.0
    for h in (5, 10):
        p[f'fwd{h}'] = g['close'].shift(-h) / p['close'] - 1.0
    p['volr'] = p['vol'] / p['vma20']
    p['amtr'] = p['amount'] / p['amt20']
    bad = ((p['vol'].values <= 0) | (p['close'].values <= 0) |
           ~np.isfinite(p['pct_chg'].values))
    ok = ((np.nan_to_num(p['is_st'].values) == 0) &
          (np.nan_to_num(p['obs_days'].values) >= 60) &
          np.isfinite(p['mom20'].values) & np.isfinite(p[f'fwd5'].values) & ~bad)
    s = p[ok].reset_index(drop=True)
    s['orig_row'] = np.flatnonzero(ok)
    s['year'] = (s['trade_date'] // 10000).astype(int)
    return p, s


def main():
    ev = pd.read_parquet(os.path.join(E.DATA, 'events_e09.parquet'))
    oos = (ev['segment'] == 'OOS').values

    def msk(L=3, contr=0.70, dd=0.05):
        c = ev[f'meanvr_{L}'].values <= contr
        s = ev[f'minlow_{L}'].values >= (1.0 - dd)
        b = {v: np.isfinite(ev[f'dB_{v}_{TAG}{L}'].values) for v in ('A', 'B', 'C')}
        return c, s, b

    c3, s3, b3 = msk()

    # ================= §17 Momentum 分层 =================
    print('[1] Momentum 分层 ...', flush=True)
    rows = []
    for mv in ('mom5', 'mom10', 'mom20'):
        v = ev[mv].values.astype(np.float64)
        qs = np.nanpercentile(v, [33.3, 66.7])
        grp = np.digitize(v, qs)
        for gi, gl in enumerate(['Low', 'Mid', 'High']):
            for sname, sm in (('IS', ~oos), ('OOS', oos)):
                m = sm & (grp == gi)
                m_e = sm & (grp == gi) & c3 & s3 & b3['B']
                a = E.stat(ev.loc[m, 'S_ret_5'].values, 0.0)
                b = E.stat(ev.loc[m_e, f'B_ret_5_B_{TAG}3'].values, 0.0)
                if b.get('n', 0) < 30:
                    continue
                rows.append(dict(momentum=mv, group=gl, segment=sname,
                                 n_start=a['n'], mean_start=a['mean'], win_start=a['win'],
                                 n_e09=b['n'], mean_e09=b['mean'], win_e09=b['win'],
                                 median_e09=b['median'], pf_e09=b['pf'],
                                 diff=b['mean'] - a['mean']))
    mom = pd.DataFrame(rows)
    E.save(mom, 'e09_v02_momentum_compare.csv')
    print(mom[mom.segment == 'OOS'].round(4).to_string(index=False))

    # ================= §30 机制 G1-G4 =================
    print('[2] 机制 G1-G4 ...', flush=True)
    mrows = []
    notc = ~c3
    nots = ~s3
    for sname, sm in (('IS', ~oos), ('OOS', oos), ('ALL', np.ones(len(ev), bool))):
        for bver in ('A', 'B', 'C'):
            bb = b3[bver]
            G = {
                'G1 缩量+结构保持': c3 & s3,
                'G2 缩量+结构破坏': c3 & nots,
                'G3 非缩量+结构保持': notc & s3,
                'G4 非缩量+结构破坏': notc & nots,
            }
            for gl, gm in G.items():
                mm = sm & gm & bb
                for h in (3, 5, 10):
                    st = E.stat(ev.loc[mm, f'B_ret_{h}_{bver}_{TAG}3'].values, 0.0)
                    if st.get('n', 0) < 30:
                        continue
                    mrows.append(dict(breakout=f'B{bver}', group=gl, segment=sname, horizon=h,
                                      n=st['n'], mean=st['mean'], median=st['median'],
                                      win=st['win'], pf=st['pf'], t=st['t']))
    mech = pd.DataFrame(mrows)
    E.save(mech, 'e09_v02_mechanism.csv')
    print('\n=== G1-G4（OOS, T+5）===')
    print(mech[(mech.segment == 'OOS') & (mech.horizon == 5)].round(4).to_string(index=False))

    # ================= §31 量能衰减 =================
    print('[3] 量能衰减 ...', flush=True)
    vrows = []
    sl = ev['vol_decay_slope_3'].values.astype(np.float64)
    sub_ok = c3 & s3 & b3['B'] & np.isfinite(sl)
    qs = np.nanpercentile(sl[sub_ok], [33.3, 66.7])
    grp = np.digitize(sl, qs)
    for sname, sm in (('IS', ~oos), ('OOS', oos)):
        for gi, gl in enumerate(['快速衰减(斜率最小)', '平缓衰减', '无明显衰减(斜率最大)']):
            mm = sm & sub_ok & (grp == gi)
            for h in (3, 5, 10):
                st = E.stat(ev.loc[mm, f'B_ret_{h}_B_{TAG}3'].values, 0.0)
                if st.get('n', 0) < 30:
                    continue
                vrows.append(dict(group=gl, segment=sname, horizon=h, n=st['n'],
                                  mean=st['mean'], median=st['median'], win=st['win'],
                                  pf=st['pf'], slope=float(np.nanmean(sl[mm]))))
    vd = pd.DataFrame(vrows)
    E.save(vd, 'e09_v02_volume_decay.csv')
    print('\n=== 量能衰减（OOS, T+5）===')
    print(vd[(vd.segment == 'OOS') & (vd.horizon == 5)].round(4).to_string(index=False))

    # ================= §32 量缩 + 价格波动收缩 =================
    print('[4] 价格收缩 ...', flush=True)
    prows = []
    rr = ev['range_ratio_3'].values.astype(np.float64)
    m0 = c3 & s3 & b3['B'] & np.isfinite(rr)
    qs = np.nanpercentile(rr[m0], [33.3, 66.7])
    g2 = np.digitize(rr, qs)
    for sname, sm in (('IS', ~oos), ('OOS', oos)):
        for gi, gl in enumerate(['波动明显收缩', '中性', '波动放大']):
            for pc, pm in (('量缩', c3), ('非量缩', ~c3)):
                mm = sm & pm & s3 & b3['B'] & (g2 == gi)
                for h in (5,):
                    st = E.stat(ev.loc[mm, f'B_ret_{h}_B_{TAG}3'].values, 0.0)
                    if st.get('n', 0) < 30:
                        continue
                    prows.append(dict(volume=pc, price_contraction=gl, segment=sname,
                                      horizon=h, n=st['n'], mean=st['mean'],
                                      median=st['median'], win=st['win'], pf=st['pf']))
    pc = pd.DataFrame(prows)
    E.save(pc, 'e09_v02_price_contraction.csv')
    print('\n=== 量缩 × 波动收缩（OOS, T+5）===')
    print(pc[pc.segment == 'OOS'].round(4).to_string(index=False))

    # ================= §29 增量模型 =================
    print('[5] 增量模型 A→B→C→D ...', flush=True)
    p, s = build_reg_sample()
    NP = len(p)
    flag = np.zeros(NP, dtype=np.int8)
    for v in ('B',):
        d = ev[f'dB_{v}_{TAG}3'].values
        okk = np.isfinite(d) & c3 & s3
        rr_ = ev['row'].values[okk] + d[okk].astype(np.int64)
        rr_ = np.clip(rr_, 0, NP - 1)
        flag[rr_] = 1
    s['e09'] = flag[s['orig_row'].values]

    # 市场状态
    ix = pd.read_sql("select trade_date, close from index_daily_cache where ts_code='000001.SH' order by trade_date",
                     sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db'))
    ix['ma20'] = ix['close'].rolling(20, min_periods=20).mean()
    ix['ma60'] = ix['close'].rolling(60, min_periods=60).mean()
    ix['regime'] = np.where((ix['close'] > ix['ma20']) & (ix['ma20'] > ix['ma60']), 'Bull',
                    np.where((ix['close'] < ix['ma20']) & (ix['ma20'] < ix['ma60']), 'Bear', 'Neutral'))
    s['regime'] = s['trade_date'].map(dict(zip(ix['trade_date'].astype(np.int64), ix['regime']))).fillna('Neutral')

    mrows2 = []
    for H in (5, 10):
        ycol = f'fwd{H}'
        d = s[np.isfinite(s[ycol])].copy()
        isr = (d['trade_date'] <= E.IS_END).values
        y = d[ycol].values
        grp = d['ts_code'].values
        momX = np.column_stack([d['mom5'], d['mom10'], d['mom20']])
        volX = np.column_stack([d['volr'], d['amtr']])
        e09X = d['e09'].values.reshape(-1, 1)
        rg = pd.get_dummies(d['regime'], prefix='rg').reindex(
            columns=['rg_Bull', 'rg_Bear', 'rg_Neutral'], fill_value=0).values.astype(float)
        # 标准化（用 IS 的均值/标准差，避免信息泄漏）
        def ztr(X, m):
            mu = X[m].mean(axis=0); sd = X[m].std(axis=0)
            sd = np.where(sd > 0, sd, 1.0)
            return (X - mu) / sd
        A = ztr(momX, isr)
        B = np.column_stack([A, ztr(volX, isr)])
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
            # OOS R²：用 IS 系数预测 OOS
            beta = fit['beta']
            Xo = np.column_stack([np.ones((~isr).sum()), X[~isr]]) if X.shape[1] + 1 == len(beta) else None
            yo = y[~isr]
            pred = Xo @ beta
            sse = ((yo - pred) ** 2).sum()
            sst = ((yo - yo.mean()) ** 2).sum()
            r2_oos = 1 - sse / sst if sst > 0 else np.nan
            for j, cname in enumerate(nm):
                mrows2.append(dict(horizon=H, model='ABCD'[mi], coef=cname,
                                   beta=fit['beta'][j], se=fit['se'][j], t=fit['t'][j],
                                   n_is=fit['n'], adj_r2_is=fit['adj_r2'],
                                   r2_oos=r2_oos,
                                   dr2_oos_vs_prev=(r2_oos - prev_oos) if prev_oos is not None else np.nan))
            prev_oos = r2_oos
            print(f'  H={H} Model {"ABCD"[mi]}: adj_R2(IS)={fit["adj_r2"]:.5f} R2(OOS)={r2_oos:.5f}', flush=True)
    mdl = pd.DataFrame(mrows2)
    E.save(mdl, 'e09_v02_models.csv')
    print('\n=== 增量模型（E09 系数）===')
    print(mdl[mdl.coef == 'e09'].round(6).to_string(index=False))
    print('\nDONE', flush=True)


if __name__ == '__main__':
    main()
