# -*- coding: utf-8 -*-
"""
HVE-Research V1  Step 10
清洁版 W7(S4) 的最终确认：波动率配对 Null / 尾部集中度 / 涨停可实施性 / 分年稳定性
"""
import os
import numpy as np
import pandas as pd
import hve_lib as L

DATA = L.DATA
OUT = r'D:\mystock\hve_research\out'
OUT_H = [3, 5, 10, 20]
W = 45
RNG = np.random.default_rng(L.SEED + 11)
NBOOT = 400


def build_vrp(ev, p):
    N = len(p)
    cats = pd.Categorical(p['ts_code'].values)
    code_i = cats.codes
    ev_code = pd.Categorical(ev['ts_code'].values, categories=cats.categories).codes
    rows = ev['row'].values.astype(np.int64)
    off = np.arange(1, W + 1)
    idx2 = np.clip(rows[:, None] + off[None, :], 0, N - 1)
    same = (code_i[idx2] == ev_code[:, None]) & ((rows[:, None] + off[None, :]) < N)
    vol = np.where(same, p['vol'].values[idx2], np.nan)
    base = p['vol'].values[rows].astype(np.float64)
    return vol / np.where(base[:, None] > 0, base[:, None], np.nan)


def minvr_before(vrp, d, min_gap=2):
    out = np.full(len(d), np.nan)
    ok = np.isfinite(d) & (d >= min_gap + 1)
    for i in np.flatnonzero(ok):
        out[i] = np.nanmin(vrp[i, :int(d[i]) - 1])
    return out


def main():
    p = pd.read_parquet(os.path.join(DATA, 'panel.parquet'),
                        columns=['ts_code', 'vol', 'vr20', 'vol20', 'td_idx',
                                 'trade_date', 'obs_days', 'is_st', 'is_delist',
                                 'ma20', 'up_limit', 'close', 'high'])
    ev = pd.read_parquet(os.path.join(DATA, 'events_w7.parquet'))
    vrp = build_vrp(ev, p)
    d_br = ev['d_br20'].values
    mv = minvr_before(vrp, d_br)
    t = ev['trend'].values.astype(bool)
    s = ev['struct'].values.astype(bool)
    m4 = t & s & np.isfinite(mv) & (mv <= 0.5) & (d_br > 0)
    oos = ev['trade_date'].values > 20230630

    # ---------- 可投池 ----------
    m = ((p['is_st'].fillna(0) == 0) & (p['is_delist'].fillna(0) == 0) &
         (p['obs_days'] >= 60) & p['vr20'].notna() & p['vol20'].notna()).values
    d = p[m][['ts_code', 'td_idx', 'trade_date', 'vr20', 'vol20']].copy()
    d['row'] = np.flatnonzero(m)
    oc = pd.read_parquet(os.path.join(DATA, 'outcomes.parquet'),
                         columns=['row'] + [f'ret_{h}' for h in OUT_H])
    for h in OUT_H:
        d[f'ret_{h}'] = oc[f'ret_{h}'].values[m]
    d = d.sort_values('td_idx').reset_index(drop=True)
    tdv = d['td_idx'].values
    uq, s_td = np.unique(tdv, return_index=True)
    e_td = np.concatenate([s_td[1:], [len(tdv)]])
    vq = np.digitize(d['vol20'].values, np.nanpercentile(d['vol20'].values,
                                                         np.arange(10, 100, 10)))
    key2 = tdv * 20 + vq
    o2 = np.argsort(key2, kind='stable')
    u2, s2 = np.unique(key2[o2], return_index=True)
    e2 = np.concatenate([s2[1:], [len(o2)]])
    retc = {h: d[f'ret_{h}'].values for h in OUT_H}

    rows = []
    for seg, sm in (('IS', ~oos), ('OOS', oos), ('ALL', np.ones(len(ev), bool))):
        mm = m4 & sm
        if mm.sum() < 50:
            continue
        sub = ev[mm]
        tds = sub['td_idx'].values
        sl = np.clip(np.searchsorted(uq, tds), 0, len(s_td) - 1)
        cnt = np.maximum(e_td[sl] - s_td[sl], 1)
        # 事件自身波动十分位
        pos = np.searchsorted(np.sort(d['row'].values), sub['row'].values)
        pos = np.clip(pos, 0, len(d) - 1)
        order = np.argsort(d['row'].values)
        vq_ev = vq[order[pos]]
        k2 = tds * 20 + vq_ev
        sl2 = np.clip(np.searchsorted(u2, k2), 0, len(s2) - 1)
        cnt2 = np.maximum(e2[sl2] - s2[sl2], 1)
        for h in OUT_H:
            real = sub[f'br_ret_{h}'].values
            real_m = np.nanmean(real)
            b1 = np.empty(NBOOT); b2 = np.empty(NBOOT)
            for b in range(NBOOT):
                pk = s_td[sl] + (RNG.random(len(tds)) * cnt).astype(np.int64)
                b1[b] = np.nanmean(retc[h][np.clip(pk, 0, len(d) - 1)])
                pk2 = s2[sl2] + (RNG.random(len(tds)) * cnt2).astype(np.int64)
                b2[b] = np.nanmean(retc[h][np.clip(o2[pk2], 0, len(d) - 1)])
            c = L.concentration(real)
            rows.append(dict(segment=seg, horizon=h, n=int(np.sum(~np.isnan(real))),
                             mean=real_m, median=float(np.nanmedian(real)),
                             win=float(np.nanmean(real > 0)),
                             ctrl_sameday=float(b1.mean()), p_sameday=float((b1 >= real_m).mean()),
                             ctrl_volmatched=float(b2.mean()),
                             p_volmatched=float((b2 >= real_m).mean()),
                             alpha_volmatched=real_m - float(b2.mean()),
                             mean_ex_top5=c.get('mean_ex_top5', np.nan),
                             pf_ex_top5=c.get('pf_ex_top5', np.nan),
                             top5_contrib=c.get('top5_contrib', np.nan),
                             mean_ex_top10=c.get('mean_ex_top10', np.nan)))
    res = pd.DataFrame(rows)
    res.to_csv(os.path.join(OUT, '06d_null_clean_volmatched.csv'), index=False,
               encoding='utf-8-sig')
    print('=== 清洁版 W7(S4)：同日配对 / 同日+波动配对 / 尾部集中度 ===')
    print(res.round(4).to_string(index=False))

    # ---------- 涨停可实施性 ----------
    print('\n=== 清洁版 W7(S4) 突破日涨停可实施性 ===')
    rowsb = []
    for seg, sm in (('IS', ~oos), ('OOS', oos)):
        mm = m4 & sm
        er = np.clip(ev['row'].values[mm] + np.clip(d_br[mm], 0, W), 0, len(p) - 1)
        lim = p['up_limit'].values[er] > 0
        for nm, sel in (('ALL', np.ones(len(er), bool)), ('non_limitup', ~lim),
                        ('limitup', lim)):
            for h in OUT_H:
                r = ev.loc[mm, f'br_ret_{h}'].values[sel]
                st = L.stat(r, 0.0)
                rowsb.append(dict(segment=seg, group=nm, horizon=h, n=st['n'],
                                  mean=st['mean'], median=st['median'], pf=st['pf'],
                                  win=st['win'], frac=float(sel.mean())))
    tb = pd.DataFrame(rowsb)
    tb.to_csv(os.path.join(OUT, '10d_tradability_clean.csv'), index=False,
              encoding='utf-8-sig')
    print(tb[tb['segment'] == 'OOS'].round(4).to_string(index=False))

    # ---------- 分年稳定性 ----------
    print('\n=== 清洁版 W7(S4) 分年（vs 同日配对）===')
    rowsy = []
    evy = ev[m4].copy()
    evy['year'] = evy['trade_date'].values // 10000
    for y, sub in evy.groupby('year'):
        if len(sub) < 100:
            continue
        tds = sub['td_idx'].values
        sl = np.clip(np.searchsorted(uq, tds), 0, len(s_td) - 1)
        cnt = np.maximum(e_td[sl] - s_td[sl], 1)
        for h in (5, 10):
            real = sub[f'br_ret_{h}'].values
            rm = np.nanmean(real)
            bb = np.empty(200)
            for b in range(200):
                pk = s_td[sl] + (RNG.random(len(tds)) * cnt).astype(np.int64)
                bb[b] = np.nanmean(retc[h][np.clip(pk, 0, len(d) - 1)])
            rowsy.append(dict(year=int(y), horizon=h, n=int(np.sum(~np.isnan(real))),
                              mean=rm, win=float(np.nanmean(real > 0)),
                              ctrl=float(bb.mean()), alpha=rm - float(bb.mean()),
                              p=float((bb >= rm).mean())))
    ry = pd.DataFrame(rowsy)
    ry.to_csv(os.path.join(OUT, '09b_regime_clean_yearly.csv'), index=False,
              encoding='utf-8-sig')
    print(ry.round(4).to_string(index=False))


if __name__ == '__main__':
    main()
