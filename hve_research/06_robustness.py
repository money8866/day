# -*- coding: utf-8 -*-
"""
HVE-Research V1  Step 6
Walk-Forward / 参数扰动 / 市场状态 / 板块与市值分层

参数化流水线 build() 可在不同 HVE 定义、聚类、形成窗、缩量阈值、突破窗口下重建事件集，
保证 Walk-Forward 与扰动检验不使用全样本最优参数。
"""
import os
import gc
import numpy as np
import pandas as pd
import hve_lib as L
from numpy.lib.stride_tricks import sliding_window_view

OUT = r'D:\mystock\hve_research\out'
DATA = L.DATA
OUT_H = [5, 10, 20]
W = 45

P = OC = M = POOL = None


def load():
    global P, OC, M, POOL
    if P is None:
        P = pd.read_parquet(os.path.join(DATA, 'panel.parquet'))
        OC = pd.read_parquet(os.path.join(DATA, 'outcomes.parquet'))
        M = L.add_mkt_fwd(pd.read_parquet(os.path.join(DATA, 'market.parquet')))
        POOL = pd.read_parquet(os.path.join(DATA, 'pool.parquet'))
    return P, OC, M, POOL


class ScanX:
    def __init__(self, ev, p, lo, hi, cols):
        N = len(p)
        cats = pd.Categorical(p['ts_code'].values)
        code_i = cats.codes
        ev_code = pd.Categorical(ev['ts_code'].values, categories=cats.categories).codes
        rows = ev['row'].values.astype(np.int64)
        off = np.arange(lo, hi + 1)
        idx = rows[:, None] + off[None, :]
        idx2 = np.clip(idx, 0, N - 1)
        same = (code_i[idx2] == ev_code[:, None]) & (idx < N)
        self.lo, self.hi, self.off, self.same = lo, hi, off, same
        self.g = {}
        for c in cols:
            v = p[c].values
            self.g[c] = np.where(same, v[idx2], np.nan)

    def col(self, c, a=None, b=None):
        i0 = 0 if a is None else a - self.lo
        i1 = len(self.off) if b is None else b - self.lo + 1
        return self.g[c][:, i0:i1]


def roll_prev_max(mat, w, lo):
    """mat: (n, L) 覆盖 offset lo..hi；返回 offset 1..W 上的前 w 日最高（不含当日）"""
    n, Lc = mat.shape
    Wm = Lc - w + 1
    out = np.full((n, W), np.nan)
    for o in range(1, W + 1):
        s = o - w - lo
        if s < 0 or s + w > Lc:
            continue
        out[:, o - 1] = np.nanmax(mat[:, s:s + w], axis=1)
    return out


def build(metric='vr20', th=2.0, gap=5, mode='first', F=10, contr_th=0.5,
          brk=20, W=45):
    import importlib
    P, OC, M, POOL = load()
    ev = importlib.import_module('02_hve_events').cluster_events(
        POOL, metric, th, gap, mode).reset_index(drop=True)
    lo = -(brk - 1)
    cols = ['close', 'high', 'low', 'vol', 'pct_chg', 'vr20', 'td_idx'] + \
           (['hh20'] if brk == 20 else [])
    SX = ScanX(ev, P, lo, W, cols)
    close = SX.col('close', 1, W)
    vol = SX.col('vol', 1, W)
    pct = SX.col('pct_chg', 1, W)
    vr = SX.col('vr20', 1, W)
    td = SX.col('td_idx', 1, W)
    if brk == 20:
        hh = SX.col('hh20', 1, W)          # 与 04 口径一致：前 20 日最高价(rmax_prev)
    else:
        hh = roll_prev_max(SX.g['high'], brk, lo)
    del cols

    base_vol = P['vol'].values[ev['row'].values].astype(np.float64)
    vrp = vol / np.where(base_vol[:, None] > 0, base_vol[:, None], np.nan)
    minvrF = np.nanmin(vrp[:, :F], axis=1)

    trend = (ev['dist_ma20'].values > 0) & (ev['ma20_slope5'].values > 0)
    struct = (ev['dist_ma60'].values > 0) & (ev['dist_hh60'].values > -0.15)
    contr = minvrF <= contr_th

    good = np.isfinite(close)
    br = good & (close > hh)
    rx = good & (vr >= 1.5) & (pct >= 3.0)
    d_br = np.where(br.any(axis=1), br.argmax(axis=1) + 1, 0)
    d_rx = np.where(rx.any(axis=1), rx.argmax(axis=1) + 1, 0)

    N = len(OC)
    rows = ev['row'].values.astype(np.int64)
    ar = np.arange(len(ev))
    mfi = M.set_index('td_idx')
    ntd = int(M['td_idx'].max()) + 2
    MKT = {h: np.asarray(mfi[f'mkt_fwd_{h}'].reindex(np.arange(ntd)).values, dtype=np.float64)
           for h in OUT_H}

    def outs(d, tag, zero_ok=False):
        er = rows + d
        valid = ((d >= 0) if zero_ok else (d > 0)) & (er < N)
        er2 = np.clip(er, 0, N - 1)
        res = {}
        for h in OUT_H:
            res[f'{tag}r{h}'] = np.where(valid, OC[f'ret_{h}'].values[er2], np.nan)
            res[f'{tag}m{h}'] = np.where(valid, OC[f'mfe_{h}'].values[er2], np.nan)
            res[f'{tag}a{h}'] = np.where(valid, OC[f'mae_{h}'].values[er2], np.nan)
        tix = np.where(d > 0, np.nan_to_num(td[ar, np.clip(d - 1, 0, W - 1)], nan=-1.0),
                       np.asarray(ev['td_idx'].values, dtype=np.float64))
        tix = np.where(valid, tix, -1.0).astype(np.int64)
        tix = np.where(tix < 0, -1, tix)
        for h in OUT_H:
            arr = MKT[h]
            mk = np.where(tix >= 0, arr[np.clip(tix, 0, len(arr) - 1)], np.nan)
            res[f'{tag}e{h}'] = res[f'{tag}r{h}'] - mk
        return res

    E = dict(row=rows, ts_code=ev['ts_code'].values,
             trade_date=ev['trade_date'].values, td_idx=ev['td_idx'].values,
             board=ev['board'].values if 'board' in ev.columns else np.array(['MAIN'] * len(ev)),
             total_mv=ev['total_mv'].values if 'total_mv' in ev.columns else np.full(len(ev), np.nan),
             pct_chg=ev['pct_chg'].values, vr20=ev['vr20'].values,
             ret20=ev['ret20'].values, ret60=ev['ret60'].values,
             dist_hh60=ev['dist_hh60'].values, amt_pct250=ev['amt_pct250'].values,
             trend=trend, struct=struct, contr=contr, minvrF=minvrF,
             d_br=d_br, d_rx=d_rx)
    E.update(outs(np.zeros(len(ev), dtype=np.int64), 'hve_', zero_ok=True))
    E.update(outs(np.full(len(ev), F, dtype=np.int64), 'f_'))
    E.update(outs(d_br, 'br_'))
    E.update(outs(d_rx, 'rx_'))
    del SX, close, vol, pct, vr, td, hh
    gc.collect()
    return E


def stats(E, mask, tag, h, cost_bp=0.0):
    r = E[f'{tag}r{h}'][mask] - cost_bp / 1e4
    st = L.stat(r, 0.0)
    e = E[f'{tag}e{h}'][mask]
    e = e[~np.isnan(e)]
    st['exc'] = float(e.mean()) if len(e) else np.nan
    st['exc_win'] = float((e > 0).mean()) if len(e) else np.nan
    st['mfe'] = float(np.nanmean(E[f'{tag}m{h}'][mask]))
    st['mae'] = float(np.nanmean(E[f'{tag}a{h}'][mask]))
    st['mkt'] = st['mean'] - st['exc'] if np.isfinite(st['exc']) else np.nan
    return st


def strategies(E):
    t, s, c = E['trend'], E['struct'], E['contr']
    br, rx = E['d_br'] > 0, E['d_rx'] > 0
    return {
        'S0_HVE_all': (np.ones(len(E['row']), bool), 'hve_'),
        'S1_HVE_trend': (t, 'hve_'),
        'S2_HVE_trend_struct': (t & s, 'hve_'),
        'S3_digest_T+F': (t & s & c, 'f_'),
        'S4_W7_breakout': (t & s & c & br, 'br_'),
        'S4b_breakout_noContr': (t & s & br, 'br_'),
        'S5_reexpansion': (t & s & c & rx, 'rx_'),
    }


# ---------------- Walk-Forward ----------------
def walk_forward():
    P, OC, M, POOL = load()
    E = None
    ev = pd.read_parquet(os.path.join(DATA, 'events_w7.parquet'))
    t = ev['trend'].values.astype(bool)
    s = ev['struct'].values.astype(bool)
    c = ev['contr'].values.astype(bool)
    br = ev['d_br20'].values > 0
    rx = ev['d_reexp'].values > 0
    V = {
        'S0_HVE_all': (np.ones(len(ev), bool), 'hve_'),
        'S1_HVE_trend': (t, 'hve_'),
        'S2_HVE_trend_struct': (t & s, 'hve_'),
        'S3_digest_T+10': (t & s & c, 'f_'),
        'S4_W7_breakout': (t & s & c & br, 'br_'),
        'S4b_breakout_noContr': (t & s & br, 'br_'),
        'S5_reexpansion': (t & s & c & rx, 'rx_'),
        'W7_contr0.4': (t & s & (ev['minvr10'].values <= 0.4) & br, 'br_'),
        'W7_contr0.6': (t & s & (ev['minvr10'].values <= 0.6) & br, 'br_'),
        'W7_contr0.8': (t & s & (ev['minvr10'].values <= 0.8) & br, 'br_'),
        'W7_BR60': (t & s & c & (ev['d_br60'].values > 0), 'br60_'),
        'W7_BR_hvehigh': (t & s & c & (ev['d_brhve'].values > 0), 'brh_'),
        'W7_BR_local': (t & s & c & (ev['d_brloc'].values > 0), 'brl_'),
    }
    dt = ev['trade_date'].values
    FOLDS = [
        ('F1', dt <= 20231231, (dt >= 20240101) & (dt <= 20241231), (dt >= 20250101) & (dt <= 20251231)),
        ('F2', dt <= 20241231, (dt >= 20250101) & (dt <= 20251231), (dt >= 20260101)),
        ('F3', dt <= 20221231, (dt >= 20230101) & (dt <= 20231231), (dt >= 20240101) & (dt <= 20241231)),
    ]
    rows = []
    for fname, tr, va, te in FOLDS:
        best, best_m = None, -np.inf
        for name, (mask, tag) in V.items():
            for h in (10, 20):
                m = mask & tr
                if m.sum() < 50:
                    continue
                st = L.stat_full(ev[m], f'{tag}ret_{h}', f'{tag}mfe_{h}', f'{tag}mae_{h}',
                                 0.0, f'{tag}exc_{h}', name)
                rows.append(dict(fold=fname, phase='train', variant=name, horizon=h,
                                 n=st['n'], mean=st['mean'], median=st['median'],
                                 pf=st['pf'], win=st['win'], exc=st.get('exc_mean', np.nan),
                                 mfe=st['mfe'], mae=st['mae'], selected=False))
                if h == 10 and np.isfinite(st['mean']) and st['mean'] > best_m:
                    best_m, best = st['mean'], name
        # 选中变体 -> val / test
        for phase, m in (('val', va), ('test', te)):
            for name, (mask, tag) in V.items():
                if name != best:
                    continue
                for h in OUT_H:
                    mm = mask & m
                    if mm.sum() < 50:
                        continue
                    st = L.stat_full(ev[mm], f'{tag}ret_{h}', f'{tag}mfe_{h}', f'{tag}mae_{h}',
                                     0.0, f'{tag}exc_{h}', name)
                    rows.append(dict(fold=fname, phase=phase, variant=name, horizon=h,
                                     n=st['n'], mean=st['mean'], median=st['median'],
                                     pf=st['pf'], win=st['win'], exc=st.get('exc_mean', np.nan),
                                     mfe=st['mfe'], mae=st['mae'], selected=True))
                # 同时给出全部变体在 test 上的表现（选择不稳定性证据）
                if phase == 'test':
                    for n2, (m2, t2) in V.items():
                        if n2 == best:
                            continue
                        mm = m2 & m
                        if mm.sum() < 50:
                            continue
                        st = L.stat_full(ev[mm], f'{t2}ret_10', f'{t2}mfe_10', f'{t2}mae_10',
                                         0.0, f'{t2}exc_10', n2)
                        rows.append(dict(fold=fname, phase='test_all', variant=n2, horizon=10,
                                         n=st['n'], mean=st['mean'], median=st['median'],
                                         pf=st['pf'], win=st['win'], exc=st.get('exc_mean', np.nan),
                                         mfe=st['mfe'], mae=st['mae'], selected=False))
    wf = pd.DataFrame(rows)
    wf.to_csv(os.path.join(OUT, '07_walk_forward.csv'), index=False, encoding='utf-8-sig')
    print('\n=== Walk-Forward：train 选出的变体在 val/test 的表现 (h=10) ===')
    print(wf[(wf['phase'].isin(['val', 'test'])) & (wf['horizon'] == 10)][
        ['fold', 'phase', 'variant', 'n', 'mean', 'median', 'pf', 'win', 'exc', 'mae']].round(4).to_string(index=False))
    return wf


# ---------------- 参数扰动 ----------------
def perturbation():
    BASE = dict(metric='vr20', th=2.0, gap=5, mode='first', F=10, contr_th=0.5, brk=20)
    GRID = [
        ('baseline', {}),
        ('th_1.6', dict(th=1.6)), ('th_2.4', dict(th=2.4)),
        ('gap_4', dict(gap=4)), ('gap_6', dict(gap=6)),
        ('F_8', dict(F=8)), ('F_12', dict(F=12)),
        ('contr_0.4', dict(contr_th=0.4)), ('contr_0.6', dict(contr_th=0.6)),
        ('brk_16', dict(brk=16)), ('brk_24', dict(brk=24)),
        ('mode_maxvol', dict(mode='maxvol')),
        ('metric_vr10', dict(metric='vr10')), ('metric_amt_r20', dict(metric='amt_r20', th=2.0)),
    ]
    rows = []
    for name, upd in GRID:
        kw = dict(BASE); kw.update(upd)
        try:
            E = build(**kw)
        except Exception as ex:
            print('FAIL', name, ex)
            continue
        V = strategies(E)
        dt = E['trade_date']
        for seg, sm in (('IS', dt <= 20230630), ('OOS', dt > 20230630), ('ALL', dt > 0)):
            for sname, (mask, tag) in V.items():
                mm = mask & sm
                if mm.sum() < 50:
                    continue
                for h in OUT_H:
                    st = stats(E, mm, tag, h)
                    rows.append(dict(perturb=name, param=str(upd), strategy=sname,
                                     segment=seg, horizon=h, n=st['n'], mean=st['mean'],
                                     median=st['median'], pf=st['pf'], win=st['win'],
                                     exc=st['exc'], mfe=st['mfe'], mae=st['mae']))
        print('done', name, flush=True)
        del E
        gc.collect()
    pr = pd.DataFrame(rows)
    pr.to_csv(os.path.join(OUT, '08_parameter_robustness.csv'), index=False, encoding='utf-8-sig')
    print('\n=== 参数扰动（OOS, h=10, S4_W7_breakout） ===')
    x = pr[(pr['segment'] == 'OOS') & (pr['horizon'] == 10) & (pr['strategy'] == 'S4_W7_breakout')]
    print(x[['perturb', 'n', 'mean', 'median', 'pf', 'win', 'exc', 'mae']].round(4).to_string(index=False))
    return pr


# ---------------- 市场状态 / 分层 ----------------
def regime():
    P, OC, M, POOL = load()
    ev = pd.read_parquet(os.path.join(DATA, 'events_w7.parquet'))
    m = M.sort_values('td_idx').reset_index(drop=True)
    c300 = m['close_000300'].ffill().values
    lr = np.log(np.where(c300 > 0, c300, np.nan))
    n = len(m)
    r60 = np.full(n, np.nan); r20 = np.full(n, np.nan)
    r60[60:] = c300[60:] / c300[:-60] - 1
    r20[20:] = c300[20:] / c300[:-20] - 1
    ma60 = pd.Series(c300).rolling(60).mean().values
    amt = m['tot_amount'].ffill().values
    xs = m['xsec_std'].ffill().values
    amt_q = pd.Series(amt).rank(pct=True).values
    xs_q = pd.Series(xs).rank(pct=True).values
    reg = pd.DataFrame(dict(td_idx=m['td_idx'].values))
    reg['trend_regime'] = np.where(c300 > ma60, 'UP', 'DOWN')
    reg['mom60'] = r60
    reg['turnover_regime'] = np.where(amt_q >= 0.7, 'HIGH_TURNOVER',
                                      np.where(amt_q <= 0.3, 'LOW_TURNOVER', 'MID'))
    reg['vol_regime'] = np.where(xs_q >= 0.7, 'HIGH_VOL',
                                 np.where(xs_q <= 0.3, 'LOW_VOL', 'MID'))
    reg['year'] = (m['trade_date'].values // 10000).astype(int)

    t = ev['trend'].values.astype(bool)
    s = ev['struct'].values.astype(bool)
    c = ev['contr'].values.astype(bool)
    br = ev['d_br20'].values > 0
    V = {
        'S0_HVE_all': (np.ones(len(ev), bool), 'hve_'),
        'S2_HVE_trend_struct': (t & s, 'hve_'),
        'S4_W7_breakout': (t & s & c & br, 'br_'),
        'S4b_breakout_noContr': (t & s & br, 'br_'),
        'S5_reexpansion': (t & s & c & (ev['d_reexp'].values > 0), 'rx_'),
    }
    evv = ev.merge(reg, on='td_idx', how='left')
    # 市值分位（事件日横截面）
    evv['mv_q'] = evv.groupby('td_idx')['total_mv'].rank(pct=True)
    evv['size'] = np.where(evv['mv_q'] >= 0.8, 'LARGE',
                           np.where(evv['mv_q'] <= 0.2, 'SMALL', 'MID'))
    evv['period'] = np.where(evv['trade_date'] <= 20211231, '2019-2021',
                             np.where(evv['trade_date'] <= 20231231, '2022-2023',
                                      np.where(evv['trade_date'] <= 20241231, '2024',
                                               np.where(evv['trade_date'] <= 20251231, '2025', '2026'))))
    rows = []
    grp_cache = {dim: evv.groupby(dim).indices for dim in
                 ['trend_regime', 'turnover_regime', 'vol_regime', 'year',
                  'period', 'board', 'size']}
    for dim, groups in grp_cache.items():
        for grp, pos in groups.items():
            pos = np.asarray(pos, dtype=np.int64)
            for sname, (mask, tag) in V.items():
                sel = pos[mask[pos]]
                if len(sel) < 30:
                    continue
                sub2 = evv.iloc[sel]
                for h in OUT_H:
                    st = L.stat_full(sub2, f'{tag}ret_{h}', f'{tag}mfe_{h}', f'{tag}mae_{h}',
                                     0.0, f'{tag}exc_{h}', sname)
                    if st['n'] < 30:
                        continue
                    rows.append(dict(dim=dim, group=str(grp), strategy=sname, horizon=h,
                                     n=st['n'], mean=st['mean'], median=st['median'],
                                     pf=st['pf'], win=st['win'], exc=st.get('exc_mean', np.nan),
                                     mfe=st['mfe'], mae=st['mae']))
    rg = pd.DataFrame(rows)
    rg.to_csv(os.path.join(OUT, '09_regime_analysis.csv'), index=False, encoding='utf-8-sig')
    print('\n=== 市场状态 / 时间稳定性（S4_W7_breakout, h=10） ===')
    x = rg[(rg['strategy'] == 'S4_W7_breakout') & (rg['horizon'] == 10) &
           (rg['dim'].isin(['trend_regime', 'turnover_regime', 'vol_regime', 'period', 'board', 'size']))]
    print(x[['dim', 'group', 'n', 'mean', 'median', 'pf', 'win', 'exc', 'mae']].round(4).to_string(index=False))
    return rg


if __name__ == '__main__':
    import sys
    which = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if which in ('all', 'wf'):
        walk_forward()
    if which in ('all', 'pt'):
        perturbation()
    if which in ('all', 'rg'):
        regime()
