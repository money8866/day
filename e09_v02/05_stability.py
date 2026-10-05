# -*- coding: utf-8 -*-
"""
E09-V02 Step 5：参数稳定性 / 分年 / 市场状态 / 市值分组

产出：e09_v02_parameter_stability.csv / e09_v02_yearly.csv
      e09_v02_regime.csv / e09_v02_size.csv
"""
import os
import numpy as np
import pandas as pd
import e09_lib as E

TAG = 'main'


def main():
    ev = pd.read_parquet(os.path.join(E.DATA, 'events_e09.parquet'))
    grid = pd.read_parquet(os.path.join(E.DATA, 'events_e09_grid.parquet'))
    oos = (ev['segment'] == 'OOS').values
    print(f'主事件集 {len(ev):,}  网格事件集 {len(grid):,}', flush=True)

    def msk(df, L=3, contr=0.70, dd=0.05):
        c = df[f'meanvr_{L}'].values <= contr
        s = df[f'minlow_{L}'].values >= (1.0 - dd)
        b = {v: np.isfinite(df[f'dB_{v}_{TAG}{L}'].values) for v in ('A', 'B', 'C')}
        return c, s, b

    # ================= 分年 / 状态 / 市值 =================
    print('[1] 分组 ...', flush=True)
    c3, s3, b3 = msk(ev)
    dims = {'year': 'year', 'regime': 'regime', 'size': 'size_lbl'}
    outs = {}
    for fname, dim in dims.items():
        rows = []
        vals = ev[dim].astype(str).values
        for uv in pd.unique(vals):
            if uv in ('nan', 'None'):
                continue
            for sname, sm in (('IS', ~oos), ('OOS', oos), ('ALL', np.ones(len(ev), bool))):
                m = sm & (vals == uv)
                for v in ('A', 'B', 'C'):
                    mm = m & c3 & s3 & b3[v]
                    for h in (3, 5, 10):
                        st = E.stat(ev.loc[mm, f'B_ret_{h}_{v}_{TAG}3'].values, 0.0)
                        if st.get('n', 0) < 30:
                            continue
                        cc = E.concentration(ev.loc[mm, f'B_ret_{h}_{v}_{TAG}3'].values)
                        rows.append(dict(group=uv, version=f'E09-{v}', segment=sname, horizon=h,
                                         n=st['n'], mean=st['mean'], median=st['median'],
                                         win=st['win'], pf=st['pf'], t=st['t'],
                                         mean_ex_top5=cc.get('mean_ex_top5', np.nan),
                                         top5_contrib=cc.get('top5_contrib', np.nan)))
        df = pd.DataFrame(rows)
        name = {'year': 'e09_v02_yearly.csv', 'regime': 'e09_v02_regime.csv',
                'size': 'e09_v02_size.csv'}[fname]
        E.save(df, name)
        outs[fname] = df
        print(f'\n--- {dim}（OOS, T+5）---')
        print(df[(df.segment == 'OOS') & (df.horizon == 5)][
            ['group', 'version', 'n', 'mean', 'median', 'win', 'pf', 'mean_ex_top5']].round(4).to_string(index=False))

    # ================= 参数稳定性 =================
    print('\n[2] 参数稳定性网格 ...', flush=True)
    g = grid
    goos = (g['segment'] == 'OOS').values
    rows = []
    for ret in E.RET_GRID:
        for vr in E.VRB_GRID:
            base = (g['pct_chg'].values >= ret) & (g['vol_ma20r'].values >= vr)
            for Lx in [2, 3, 4, 5]:
                cL = g[f'meanvr_{Lx}'].values
                sL = g[f'minlow_{Lx}'].values
                for contr in E.CONTR_GRID:
                    for dd in E.DD_GRID:
                        mc = cL <= contr
                        ms = sL >= (1.0 - dd)
                        for v in ('A', 'B', 'C'):
                            bb = np.isfinite(g[f'dB_{v}_{Lx}'].values)
                            for sname, sm in (('IS', ~goos), ('OOS', goos)):
                                mm = sm & base & mc & ms & bb
                                if mm.sum() < 200:
                                    continue
                                st = E.stat(g.loc[mm, f'B_ret_5_{v}_{Lx}'].values, 0.0)
                                st30 = E.stat(g.loc[mm, f'B_ret_5_{v}_{Lx}'].values, 30.0)
                                if st.get('n', 0) < 200:
                                    continue
                                rows.append(dict(ret=ret, vr=vr, L=Lx, contr=contr, dd=dd,
                                                 version=f'E09-{v}', segment=sname,
                                                 n=st['n'], mean=st['mean'], median=st['median'],
                                                 win=st['win'], pf=st['pf'],
                                                 mean_net30=st30['mean'], pf_net30=st30['pf']))
    ps = pd.DataFrame(rows)
    E.save(ps, 'e09_v02_parameter_stability.csv')
    print(f'  网格行数 {len(ps):,}')

    print('\n=== 参数稳定性（OOS，E09-B，T+5 净30bp）：单参数扰动 ===')
    for key, label in (('ret', '启动涨幅'), ('vr', '启动量比'), ('L', '整理长度'),
                       ('contr', '缩量阈值'), ('dd', '回撤阈值')):
        sub = ps[(ps.segment == 'OOS') & (ps.version == 'E09-B')]
        base = dict(ret=3.0, vr=1.5, L=3, contr=0.70, dd=0.05)
        oth = {k: v for k, v in base.items() if k != key}
        q = sub.copy()
        for k, v in oth.items():
            q = q[q[k] == v]
        print(f'\n  -- {label} --')
        print(q[[key, 'n', 'mean', 'mean_net30', 'pf_net30', 'win']].sort_values(key).round(4).to_string(index=False))

    print('\nDONE', flush=True)


if __name__ == '__main__':
    main()
