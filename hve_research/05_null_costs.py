# -*- coding: utf-8 -*-
"""
HVE-Research V1  Step 5
基准组 / Null Model / 尾部集中度 / 交易成本

基准:
  B1 随机股票-日期（全可投池）
  B2 随机正常量日 (vr20 in [0.8,1.2])
  B3 纯突破 (close > 20日新高，不要求HVE)
  B4 纯动量 (当日横截面 ret20 前10%)
  B6 HVE + 随机 Entry (事件后 T+1..T+20 随机日买入，同股)

Null Model:
  N_sameday   同交易日随机选股重抽样（保留当日市场条件）      -> 经验 p 值
  N_samedayvol 同交易日 + 波动率十分位配对随机重抽样          -> 经验 p 值
"""
import os
import numpy as np
import pandas as pd
import hve_lib as L

OUT = r'D:\mystock\hve_research\out'
DATA = L.DATA
IS_END = 20230630
OUT_H = [3, 5, 10, 20]
COSTS = [0, 10, 20, 30, 50]
RNG = np.random.default_rng(L.SEED)
NBOOT = 200
NEED = ['ret_{h}', 'mfe_{h}', 'mae_{h}', 'open_ret_{h}']


def eligible_frame():
    p = pd.read_parquet(os.path.join(DATA, 'panel.parquet'),
                        columns=['ts_code', 'td_idx', 'trade_date', 'vr20', 'vol20',
                                 'ret20', 'dist_hh20', 'close', 'board', 'total_mv',
                                 'amount', 'obs_days', 'is_st', 'is_delist', 'ma20'])
    m = ((p['board'].fillna('BJ') != 'BJ') & (p['is_st'].fillna(0) == 0) &
         (p['is_delist'].fillna(0) == 0) & (p['obs_days'] >= 60) &
         p['vr20'].notna() & p['ma20'].notna() & p['vol20'].notna()).values
    d = p[m][['ts_code', 'td_idx', 'trade_date', 'vr20', 'vol20', 'ret20',
              'dist_hh20', 'close', 'board', 'total_mv', 'amount']].copy()
    d['row'] = np.flatnonzero(m)
    cols = ['row'] + [c.format(h=h) for h in OUT_H for c in NEED]
    oc = pd.read_parquet(os.path.join(DATA, 'outcomes.parquet'), columns=cols)
    for c in cols[1:]:
        d[c] = oc[c].values[m]
    return d.sort_values('td_idx').reset_index(drop=True)


def group_bounds(keys):
    """keys 已排序 -> 每组的 (start,end)；返回按唯一 key 索引的起止"""
    uniq, starts = np.unique(keys, return_index=True)
    ends = np.concatenate([starts[1:], [len(keys)]])
    return uniq, starts, ends


def main():
    ev = pd.read_parquet(os.path.join(DATA, 'events_w7.parquet'))
    d = eligible_frame()
    print('events', len(ev), 'eligible', len(d), flush=True)

    # ---------- 同日 / 同日+波动十分位 抽样器 ----------
    dtv = d['trade_date'].values
    tdv = d['td_idx'].values
    vol20 = d['vol20'].values
    vq = np.digitize(vol20, np.nanpercentile(vol20, np.arange(10, 100, 10)))
    _, s_td, e_td = group_bounds(tdv)                      # 按 td_idx 排序后的组界
    key2 = tdv * 20 + vq
    o2 = np.argsort(key2, kind='stable')
    u2, s2, e2 = group_bounds(key2[o2])

    def sample_same(tds, rng):
        pos = np.searchsorted(u2 if False else np.unique(tdv), tds)  # placeholder
        return pos

    td_uniq = np.unique(tdv)
    # td_idx -> 组号（td_uniq 已升序，且 td_idx 连续性不保证，统一用 searchsorted）
    def td_slot(t):
        return np.searchsorted(td_uniq, t)

    starts_td, ends_td = s_td, e_td
    key2_uniq = u2

    ret_cols = {h: d[f'ret_{h}'].values for h in OUT_H}
    mfe_cols = {h: d[f'mfe_{h}'].values for h in OUT_H}
    mae_cols = {h: d[f'mae_{h}'].values for h in OUT_H}

    # ---------- 事件 -> 可投池 row 的映射（用于 B6 随机 Entry） ----------
    drows = d['row'].values
    order = np.argsort(drows)
    srows = drows[order]
    dcode = pd.Categorical(d['ts_code']).codes[order]
    evcode = pd.Categorical(ev['ts_code'], categories=pd.Categorical(d['ts_code']).categories).codes

    trend = ev['trend'].values.astype(bool)
    struct = ev['struct'].values.astype(bool)
    contr = ev['contr'].values.astype(bool)
    br = ev['d_br20'].values > 0
    rx = ev['d_reexp'].values > 0

    STRATS = {
        'S0_HVE_all': (np.ones(len(ev), bool), 'hve_'),
        'S1_HVE_trend': (trend, 'hve_'),
        'S2_HVE_trend_struct': (trend & struct, 'hve_'),
        'S3_digest_T+10': (trend & struct & contr, 'f_'),
        'S4_W7_breakout': (trend & struct & contr & br, 'br_'),
        'S4b_breakout_noContr': (trend & struct & br, 'br_'),
        'S5_reexpansion': (trend & struct & contr & rx, 'rx_'),
    }
    segs = {'IS': ev['trade_date'].values <= IS_END,
            'OOS': ev['trade_date'].values > IS_END,
            'ALL': np.ones(len(ev), bool)}

    rows = []
    # ---------- 策略 × 成本 ----------
    for sname, (mask, tag) in STRATS.items():
        for seg, sm in segs.items():
            mm = mask & sm
            if mm.sum() < 50:
                continue
            sub = ev[mm]
            for h in OUT_H:
                r = sub[f'{tag}ret_{h}'].values
                mkt = sub[f'{tag}exc_{h}'].values
                mkt_m = np.nanmean(r - mkt) if mkt.size else np.nan
                mfe = float(np.nanmean(sub[f'{tag}mfe_{h}'].values))
                mae = float(np.nanmean(sub[f'{tag}mae_{h}'].values))
                for c in COSTS:
                    st = L.stat(r, costs_bp=c)
                    rows.append(dict(kind='strategy', name=sname, segment=seg, horizon=h,
                                     cost_bp=c, n=st['n'], win=st['win'], mean=st['mean'],
                                     median=st['median'], pf=st['pf'], t=st['t'],
                                     mfe=mfe, mae=mae,
                                     net_excess=st['mean'] - mkt_m if np.isfinite(mkt_m) else np.nan,
                                     mkt_mean=mkt_m, pval=np.nan))

    # ---------- 基准 ----------
    vr = d['vr20'].values
    dh = d['dist_hh20'].values
    r20 = d['ret20'].values
    mom_pct = d.groupby('td_idx')['ret20'].rank(pct=True).values
    BENCH = {
        'B1_random_stockday': np.ones(len(d), bool),
        'B2_normal_volume': (vr >= 0.8) & (vr <= 1.2),
        'B3_pure_breakout': dh > 0,
        'B4_momentum_top10': mom_pct >= 0.90,
    }
    for bname, bm in BENCH.items():
        for seg, test in (('IS', dtv <= IS_END), ('OOS', dtv > IS_END), ('ALL', dtv > 0)):
            mm = bm & test
            if mm.sum() < 50:
                continue
            for h in OUT_H:
                r = ret_cols[h][mm]
                for c in COSTS:
                    st = L.stat(r, costs_bp=c)
                    rows.append(dict(kind='benchmark', name=bname, segment=seg, horizon=h,
                                     cost_bp=c, n=st['n'], win=st['win'], mean=st['mean'],
                                     median=st['median'], pf=st['pf'], t=st['t'],
                                     mfe=float(np.nanmean(mfe_cols[h][mm])),
                                     mae=float(np.nanmean(mae_cols[h][mm])),
                                     net_excess=np.nan, mkt_mean=np.nan, pval=np.nan))

    # ---------- B6: HVE + 随机 Entry（同股，T+1..T+20） ----------
    for seg, sm in segs.items():
        idx = np.flatnonzero(sm)
        if len(idx) < 50:
            continue
        for rep in range(3):
            off = RNG.integers(1, 21, size=len(idx))
            tgt = ev['row'].values[idx] + off
            pos = np.searchsorted(srows, tgt)
            posc = np.clip(pos, 0, len(srows) - 1)
            ok = (srows[posc] == tgt) & (dcode[posc] == evcode[idx])
            for h in OUT_H:
                rr = np.where(ok, ret_cols[h][order[posc]], np.nan)
                st = L.stat(rr, 0.0)
                rows.append(dict(kind='benchmark', name=f'B6_HVE_random_entry_r{rep}',
                                 segment=seg, horizon=h, cost_bp=0, n=st['n'],
                                 win=st['win'], mean=st['mean'], median=st['median'],
                                 pf=st['pf'], t=st['t'], mfe=np.nan, mae=np.nan,
                                 net_excess=np.nan, mkt_mean=np.nan, pval=np.nan))

    # ---------- Null: 同日配对随机 / 同日+波动配对 ----------
    n = len(d)
    vq_d = d['_vq'].values if '_vq' in d.columns else vq
    for sname, (mask, tag) in STRATS.items():
        for seg, sm in segs.items():
            mm = mask & sm
            if mm.sum() < 50:
                continue
            sub = ev[mm]
            tds = sub['td_idx'].values
            vqs = vq[np.searchsorted(srows, sub['row'].values)] if False else None
            # 事件自身的波动十分位（用可投池同一 row）
            pos_ev = np.searchsorted(srows, sub['row'].values)
            pos_ev = np.clip(pos_ev, 0, len(srows) - 1)
            vq_ev = np.where(srows[pos_ev] == sub['row'].values, vq_d[order[pos_ev]], 5)
            for h in OUT_H:
                real = sub[f'{tag}ret_{h}'].values
                real_m = np.nanmean(real)
                if not np.isfinite(real_m):
                    continue
                # --- 同日 ---
                sl = np.clip(td_slot(tds), 0, len(starts_td) - 1)
                cnt = np.maximum(ends_td[sl] - starts_td[sl], 1)
                boot = np.empty(NBOOT)
                for b in range(NBOOT):
                    pick = starts_td[sl] + (RNG.random(len(tds)) * cnt).astype(np.int64)
                    boot[b] = np.nanmean(ret_cols[h][np.clip(pick, 0, n - 1)])
                ctrl = float(boot.mean())
                pval = float((boot >= real_m).mean())
                rows.append(dict(kind='null_sameday', name=sname, segment=seg, horizon=h,
                                 cost_bp=0, n=int(np.sum(~np.isnan(real))),
                                 win=float(np.nanmean(real > 0)), mean=real_m,
                                 median=float(np.nanmedian(real)), pf=np.nan, t=np.nan,
                                 mfe=np.nan, mae=np.nan, net_excess=np.nan,
                                 mkt_mean=ctrl, pval=pval))
                # --- 同日 + 波动十分位 ---
                kk = tds * 20 + vq_ev
                sl2 = np.clip(np.searchsorted(key2_uniq, kk), 0, len(s2) - 1)
                hit = (key2_uniq[sl2] == kk)
                cnt2 = np.maximum(e2[sl2] - s2[sl2], 1)
                boot2 = np.empty(NBOOT)
                for b in range(NBOOT):
                    pick = s2[sl2] + (RNG.random(len(tds)) * cnt2).astype(np.int64)
                    boot2[b] = np.nanmean(ret_cols[h][np.clip(o2[pick], 0, n - 1)])
                ctrl2 = float(boot2.mean())
                pval2 = float((boot2 >= real_m).mean())
                rows.append(dict(kind='null_sameday_volmatched', name=sname, segment=seg,
                                 horizon=h, cost_bp=0, n=int(np.sum(~np.isnan(real))),
                                 win=float(np.nanmean(real > 0)), mean=real_m,
                                 median=float(np.nanmedian(real)), pf=np.nan, t=np.nan,
                                 mfe=np.nan, mae=np.nan, net_excess=np.nan,
                                 mkt_mean=ctrl2, pval=pval2))

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, '06_null_model.csv'), index=False, encoding='utf-8-sig')
    print('\n=== 策略 T+10 成本扫描 (OOS) ===')
    x = df[(df['horizon'] == 10) & (df['segment'] == 'OOS') & (df['kind'] == 'strategy')]
    print(x[['name', 'cost_bp', 'n', 'mean', 'median', 'pf', 'win', 'net_excess']].round(4).to_string(index=False))
    print('\n=== 策略 vs 同日随机 (T+10, OOS, 0bp) ===')
    y = df[(df['horizon'] == 10) & (df['segment'] == 'OOS') & (df['cost_bp'] == 0) &
           (df['kind'].isin(['strategy', 'null_sameday', 'null_sameday_volmatched',
                             'benchmark']))]
    print(y[['kind', 'name', 'n', 'mean', 'win', 'pf', 'mkt_mean', 'pval']].round(4).to_string(index=False))

    # ---------- 尾部集中度 ----------
    conc_rows = []
    for sname, (mask, tag) in STRATS.items():
        for seg, sm in segs.items():
            mm = mask & sm
            if mm.sum() < 50:
                continue
            sub = ev[mm]
            for h in OUT_H:
                c = L.concentration(sub[f'{tag}ret_{h}'].values)
                c.update(dict(name=sname, segment=seg, horizon=h))
                conc_rows.append(c)
    conc = pd.DataFrame(conc_rows)
    conc.to_csv(os.path.join(OUT, '06b_concentration.csv'), index=False, encoding='utf-8-sig')
    print('\n=== 尾部集中度 (T+10, OOS) ===')
    print(conc[(conc['horizon'] == 10) & (conc['segment'] == 'OOS')].round(4).to_string(index=False))


if __name__ == '__main__':
    main()
