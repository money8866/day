# -*- coding: utf-8 -*-
"""
HVE-Research V1  Step 9
缩量条件的「无未来函数」重检验

问题：contr = min(vrp[T+1..T+10]) <= th 使用了事件后 10 日的成交量。
      当 Entry 发生在 T+d（d<10，如突破日 T+3）时，该条件含未来信息。
修正：对 Entry 在 T+d 的事件，缩量条件只用 T+1..T+d-1 的成交量（入场前已知）。
"""
import os
import numpy as np
import pandas as pd
import hve_lib as L

DATA = L.DATA
OUT = r'D:\mystock\hve_research\out'
OUT_H = [5, 10, 20]
W = 45


class ScanV:
    def __init__(self, ev, p, cols=('close', 'vol', 'vr20', 'pct_chg', 'td_idx'), W=45):
        N = len(p)
        cats = pd.Categorical(p['ts_code'].values)
        code_i = cats.codes
        ev_code = pd.Categorical(ev['ts_code'].values, categories=cats.categories).codes
        rows = ev['row'].values.astype(np.int64)
        off = np.arange(1, W + 1)
        idx = rows[:, None] + off[None, :]
        idx2 = np.clip(idx, 0, N - 1)
        same = (code_i[idx2] == ev_code[:, None]) & (idx < N)
        self.g = {}
        for c in cols:
            v = p[c].values
            self.g[c] = np.where(same, v[idx2], np.nan)


def main():
    p = pd.read_parquet(os.path.join(DATA, 'panel.parquet'),
                        columns=['ts_code', 'close', 'vol', 'vr20', 'pct_chg', 'td_idx'])
    ev = pd.read_parquet(os.path.join(DATA, 'events_w7.parquet'))
    S = ScanV(ev, p)
    vol = S.g['vol']
    base_vol = p['vol'].values[ev['row'].values].astype(np.float64)
    vrp = vol / np.where(base_vol[:, None] > 0, base_vol[:, None], np.nan)

    d_br = ev['d_br20'].values
    d_rx = ev['d_reexp'].values
    t = ev['trend'].values.astype(bool)
    s = ev['struct'].values.astype(bool)
    oos = ev['trade_date'].values > 20230630

    n = len(ev)
    ar = np.arange(n)

    def minvr_before(d, min_days=2):
        """Entry 在 T+d：只用 T+1..T+d-1 的量能（入场前已知）"""
        out = np.full(n, np.nan)
        ok = d >= (min_days + 1)
        idx = np.flatnonzero(ok)
        for i in idx:
            k = int(d[i]) - 1          # 覆盖 offset 1..d-1 -> 列 0..d-2
            out[i] = np.nanmin(vrp[i, :k])
        return out

    rows = []
    for tagname, d, tag, min_days in (('突破日', d_br, 'br_', 3),
                                      ('再扩张日', d_rx, 'rx_', 3)):
        mvb = minvr_before(d, min_days)
        valid = np.isfinite(mvb)
        for seg, sm in (('IS', ~oos), ('OOS', oos), ('ALL', np.ones(n, bool))):
            for th in (0.4, 0.5, 0.6, 0.8):
                for h in OUT_H:
                    for name, mm in (
                            (f'{tagname}_有缩量(<={th})', t & s & valid & (mvb <= th) & sm),
                            (f'{tagname}_无缩量(>{th})', t & s & valid & (mvb > th) & sm)):
                        if mm.sum() < 50:
                            continue
                        sub = ev[mm]
                        st = L.stat_full(sub, f'{tag}ret_{h}', f'{tag}mfe_{h}',
                                         f'{tag}mae_{h}', 0.0, f'{tag}exc_{h}', name)
                        rows.append(dict(kind=tagname, th=th, group=name, segment=seg,
                                         horizon=h, n=st['n'], mean=st['mean'],
                                         median=st['median'], pf=st['pf'], win=st['win'],
                                         exc=st.get('exc_mean', np.nan),
                                         mfe=st['mfe'], mae=st['mae']))
    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(OUT, '04b_contraction_clean.csv'), index=False,
               encoding='utf-8-sig')
    print('=== 缩量条件（无未来函数版本，OOS，h=10）===')
    x = res[(res['segment'] == 'OOS') & (res['horizon'] == 10)]
    print(x[['kind', 'th', 'group', 'n', 'mean', 'median', 'pf', 'win', 'exc']].round(4).to_string(index=False))

    # Welch 检验：有缩量 vs 无缩量
    print('\n=== Welch 检验（OOS）===')
    for tagname, d, tag in (('突破日', d_br, 'br_'), ('再扩张日', d_rx, 'rx_')):
        mvb = minvr_before(d, 3)
        valid = np.isfinite(mvb)
        for h in OUT_H:
            a = ev.loc[t & s & valid & (mvb <= 0.5) & oos, f'{tag}ret_{h}'].values
            b = ev.loc[t & s & valid & (mvb > 0.5) & oos, f'{tag}ret_{h}'].values
            tt, pv = L.welch_t(a, b)
            print(f'{tagname} h={h}: 有缩量 n={np.isfinite(a).sum()} mean={np.nanmean(a):.4f} '
                  f'pf={L.stat(a)["pf"]:.3f} | 无缩量 n={np.isfinite(b).sum()} '
                  f'mean={np.nanmean(b):.4f} pf={L.stat(b)["pf"]:.3f} | t={tt:.2f} p={pv:.5f}')


if __name__ == '__main__':
    main()
