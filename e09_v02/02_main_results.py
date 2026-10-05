# -*- coding: utf-8 -*-
"""
E09-V02 Step 2：主结果

产出
----
* e09_v02_oos.csv        OOS 主结果（E09-A/B/C × L，T+1/3/5/10，毛/净）
* e09_v02_main.csv       IS+OOS 全量分层结果
* e09_v02_entry.csv      Close entry vs Next-day Open entry + 跳空
* e09_v02_cluster.csv    事件簇污染（Version A / B，top1/top5 贡献）
* e09_v02_compare.csv    §16 三个核心比较
"""
import os
import numpy as np
import pandas as pd
import e09_lib as E

TAG = 'main'


def P(v, L, h, kind='ret'):
    """B-entry 列名"""
    return f'B_{kind}_{h}_{v}_{TAG}{L}'


def add(rows, ev, mask, label, seg, horizons, kind='ret', v=None, L=None,
        cost=0.0, mfe=True, entry=None):
    """kind: 'S' 或 'B'；entry: None=close, 'O'=next open"""
    if mask.sum() < 30:
        return
    sub = ev[mask]
    for h in horizons:
        if kind == 'S':
            rc, mc, ac, xc, xmc = (f'S_ret_{h}', f'S_mfe_{h}', f'S_mae_{h}',
                                   f'S_exc_{h}', f'S_excmed_{h}')
        else:
            suf = 'O' if entry == 'O' else ''
            rc = f'B_ret{suf}_{h}_{v}_{TAG}{L}'
            mc = f'B_mfe_{h}_{v}_{TAG}{L}'
            ac = f'B_mae_{h}_{v}_{TAG}{L}'
            xc = f'B_exc_{h}_{v}_{TAG}{L}'
            xmc = f'B_excmed_{h}_{v}_{TAG}{L}'
        r = sub[rc].values.astype(np.float64)
        r = r - cost / 1e4
        st = E.stat(r, 0.0, sub[xc].values, label)
        if st.get('n', 0) < 30:
            continue
        _, lo, hi = E.boot_ci(r, cluster=sub['ts_code'].values)
        p = E.cluster_boot_pvalue(r, nboot=1000, cluster=sub['ts_code'].values)
        d = dict(strategy=label, segment=seg, horizon=h, cost_bp=cost, n=st['n'],
                 mean=st['mean'], median=st['median'], win=st['win'], pf=st['pf'],
                 t=st['t'], exc=st.get('exc', np.nan), exc_t=st.get('exc_t', np.nan),
                 ci_lo=lo, ci_hi=hi, p_clustboot=p,
                 excmed=float(np.nanmean(sub[xmc].values)))
        if mfe and kind == 'S':
            d['mfe'] = float(np.nanmean(sub[mc].values))
            d['mae'] = float(np.nanmean(sub[ac].values))
        elif mfe and entry != 'O':
            d['mfe'] = float(np.nanmean(sub[mc].values))
            d['mae'] = float(np.nanmean(sub[ac].values))
        else:
            d['mfe'] = np.nan
            d['mae'] = np.nan
        rows.append(d)


def main():
    print('载入事件集 ...', flush=True)
    ev = pd.read_parquet(os.path.join(E.DATA, 'events_e09.parquet'))
    print(f'  {len(ev):,} 行', flush=True)
    oos = (ev['segment'] == 'OOS').values
    segs = {'IS': ~oos, 'OOS': oos, 'ALL': np.ones(len(ev), bool)}

    def msk(L, contr=0.70, dd=0.05):
        c = ev[f'meanvr_{L}'].values <= contr
        s = ev[f'minlow_{L}'].values >= (1.0 - dd)
        b = {v: np.isfinite(ev[f'dB_{v}_{TAG}{L}'].values) for v in ('A', 'B', 'C')}
        return c, s, b

    # ================= 主结果 =================
    print('主结果 ...', flush=True)
    rows = []
    for sname, sm in segs.items():
        add(rows, ev, sm, 'S_start_all', sname, E.HOR_S, kind='S')
        for Lx in E.L_GRID:
            cL, sL, bL = msk(Lx)
            add(rows, ev, sm & cL & sL, f'S_contr_struct_L{Lx}', sname, E.HOR_S, kind='S')
            for v in ('A', 'B', 'C'):
                add(rows, ev, sm & cL & sL & bL[v], f'E09_{v}_L{Lx}', sname,
                    E.HOR_B, kind='B', v=v, L=Lx)
    main_df = pd.DataFrame(rows)
    E.save(main_df, 'e09_v02_main.csv')

    # ================= OOS + 成本 =================
    print('成本扫描 ...', flush=True)
    orows = []
    for Lx in E.L_GRID:
        cL, sL, bL = msk(Lx)
        for v in ('A', 'B', 'C'):
            m = oos & cL & sL & bL[v]
            for c in E.COSTS:
                add(orows, ev, m, f'E09_{v}_L{Lx}', 'OOS', E.HOR_B, kind='B', v=v, L=Lx, cost=c)
        m = oos & cL & sL
        for c in E.COSTS:
            add(orows, ev, m, f'S_contr_struct_L{Lx}', 'OOS', E.HOR_S, kind='S', cost=c)
    oos_df = pd.DataFrame(orows)
    E.save(oos_df, 'e09_v02_oos.csv')

    # ================= 入场敏感性 =================
    print('入场敏感性 ...', flush=True)
    erows = []
    for Lx in E.L_GRID:
        cL, sL, bL = msk(Lx)
        for v in ('A', 'B', 'C'):
            for sname, sm in segs.items():
                mm = sm & cL & sL & bL[v]
                if mm.sum() < 30:
                    continue
                sub = ev[mm]
                for h in E.HOR_B:
                    for kind_lbl, ent in (('Close', None), ('NextOpen', 'O')):
                        add(erows, ev, mm, f'E09-{v}', sname, [h], kind='B', v=v, L=Lx, entry=ent)
                        erows[-1]['entry'] = kind_lbl
                        erows[-1]['L'] = Lx
                g = sub[f'B_gap_{v}_{TAG}{Lx}'].values.astype(np.float64)
                lu = np.nan_to_num(sub[f'B_limitup_{v}_{TAG}{Lx}'].values.astype(np.float64))
                r5 = sub[P(v, Lx, 5)].values.astype(np.float64)
                nolu = ~lu.astype(bool)
                erows.append(dict(strategy=f'E09-{v}', L=Lx, segment=sname, horizon=0,
                                  entry='gap/limitup', cost_bp=0, n=int(np.isfinite(g).sum()),
                                  mean=float(np.nanmean(g)), median=float(np.nanmedian(g)),
                                  win=float(np.nanmean(g > 0)), pf=np.nan, t=np.nan,
                                  exc=np.nan, exc_t=np.nan, ci_lo=np.nan, ci_hi=np.nan,
                                  p_clustboot=np.nan, excmed=np.nan,
                                  mfe=float(np.nanmean(lu)),   # 突破日涨停占比
                                  mae=float(np.nanmean(r5[nolu])) if nolu.sum() > 30 else np.nan))
    ent = pd.DataFrame(erows)
    E.save(ent, 'e09_v02_entry.csv')

    # ================= 事件簇 =================
    print('事件簇 ...', flush=True)
    crows = []
    for Lx in E.L_GRID:
        cL, sL, bL = msk(Lx)
        for v in ('A', 'B', 'C'):
            for sname, sm in segs.items():
                base = sm & cL & sL & bL[v]
                for vname, mm in (('VersionA_all', base),
                                  ('VersionB_gap10', base & (ev['gap_prev'].values > 10))):
                    if mm.sum() < 30:
                        continue
                    sub = ev[mm]
                    for h in E.HOR_B:
                        r = sub[P(v, Lx, h)].values.astype(np.float64)
                        st = E.stat(r, 0.0)
                        if st.get('n', 0) < 30:
                            continue
                        g = sub.groupby('ts_code')[P(v, Lx, h)].agg(['sum', 'count'])
                        tot = g['sum'].sum()
                        cc = E.concentration(r)
                        crows.append(dict(version=f'E09-{v}', L=Lx, segment=sname, horizon=h,
                                          variant=vname, n=st['n'],
                                          unique_stocks=int(sub['ts_code'].nunique()),
                                          events_per_stock=st['n'] / max(sub['ts_code'].nunique(), 1),
                                          top1_contrib=(g['sum'].nlargest(1).sum() / tot) if tot else np.nan,
                                          top5_contrib=(g['sum'].nlargest(5).sum() / tot) if tot else np.nan,
                                          top5pct_contrib=cc.get('top5_contrib', np.nan),
                                          mean_ex_top5=cc.get('mean_ex_top5', np.nan),
                                          mean=st['mean'], median=st['median'],
                                          win=st['win'], pf=st['pf']))
    cl = pd.DataFrame(crows)
    E.save(cl, 'e09_v02_cluster.csv')

    # ================= §16 三个核心比较 =================
    print('三个核心比较 ...', flush=True)
    prows = []
    c3, s3, b3 = msk(3)
    for sname, sm in segs.items():
        for h in E.HOR_S:
            a = E.stat(ev.loc[sm, f'S_ret_{h}'].values, 0.0)
            b = E.stat(ev.loc[sm & c3 & s3, f'S_ret_{h}'].values, 0.0)
            t, pv = E.welch_t(ev.loc[sm & c3 & s3, f'S_ret_{h}'].values,
                              ev.loc[sm, f'S_ret_{h}'].values) if hasattr(E, 'welch_t') else (np.nan, np.nan)
            prows.append(dict(compare='C1 启动 vs 启动+缩量整理(S入场)', segment=sname, horizon=h,
                              n_a=a['n'], mean_a=a['mean'], win_a=a['win'],
                              n_b=b['n'], mean_b=b['mean'], win_b=b['win'],
                              diff=b['mean'] - a['mean'], t=t, p=pv))
        for v in ('A', 'B', 'C'):
            msub = sm & c3 & s3 & b3[v]
            for h in E.HOR_B:
                a = E.stat(ev.loc[msub, f'S_ret_{h}'].values, 0.0)
                b = E.stat(ev.loc[msub, P(v, 3, h)].values, 0.0)
                prows.append(dict(compare=f'C2 S入场 vs B入场 (E09-{v})', segment=sname, horizon=h,
                                  n_a=a['n'], mean_a=a['mean'], win_a=a['win'],
                                  n_b=b['n'], mean_b=b['mean'], win_b=b['win'],
                                  diff=b['mean'] - a['mean'], t=np.nan, p=np.nan))
        for h in E.HOR_B:
            a = E.stat(ev.loc[sm & c3 & s3 & b3['A'], P('A', 3, h)].values, 0.0)
            b = E.stat(ev.loc[sm & c3 & s3 & b3['B'], P('B', 3, h)].values, 0.0)
            c = E.stat(ev.loc[sm & c3 & s3 & b3['C'], P('C', 3, h)].values, 0.0)
            prows.append(dict(compare='C3 突破 vs 放量突破 vs 强放量突破', segment=sname, horizon=h,
                              n_a=a['n'], mean_a=a['mean'], win_a=a['win'],
                              n_b=b['n'], mean_b=b['mean'], win_b=b['win'],
                              diff=c['mean'] - a['mean'], t=np.nan, p=np.nan))
    cmpdf = pd.DataFrame(prows)
    E.save(cmpdf, 'e09_v02_compare.csv')

    print('\n=== OOS T+5（30bp 净）===')
    o = oos_df[(oos_df.horizon == 5) & (oos_df.cost_bp == 30)]
    print(o[['strategy', 'n', 'mean', 'median', 'win', 'pf', 'exc', 'ci_lo', 'ci_hi', 'p_clustboot']].round(4).to_string(index=False))
    print('\n=== OOS T+3（30bp 净）===')
    o = oos_df[(oos_df.horizon == 3) & (oos_df.cost_bp == 30)]
    print(o[['strategy', 'n', 'mean', 'median', 'win', 'pf', 'exc', 'ci_lo', 'ci_hi', 'p_clustboot']].round(4).to_string(index=False))
    print('\n=== OOS T+5（0bp 毛）===')
    o = oos_df[(oos_df.horizon == 5) & (oos_df.cost_bp == 0)]
    print(o[['strategy', 'n', 'mean', 'median', 'win', 'pf', 'exc', 'excmed', 'p_clustboot']].round(4).to_string(index=False))
    print('\n=== 事件簇（OOS, T+5, E09-B L3）===')
    print(cl[(cl.segment == 'OOS') & (cl.horizon == 5) & (cl.version == 'E09-B')][
        ['L', 'variant', 'n', 'unique_stocks', 'events_per_stock', 'top1_contrib',
         'top5_contrib', 'top5pct_contrib', 'mean_ex_top5', 'mean', 'pf']].round(4).to_string(index=False))
    print('\n=== 入场敏感性（OOS, T+5, E09-B L3）===')
    print(ent[(ent.segment == 'OOS') & (ent.horizon == 5) & (ent.strategy == 'E09-B') & (ent.L == 3)][
        ['entry', 'n', 'mean', 'median', 'win', 'pf', 'mfe', 'mae']].round(4).to_string(index=False))
    print('\nDONE', flush=True)


if __name__ == '__main__':
    main()
