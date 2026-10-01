# -*- coding: utf-8 -*-
"""
HVE-Research V1  Step 4c
用「入场前已知」的缩量定义重算 HVT-BULL 增量链 / W7 / Entry 对比 / 同日配对 Null

修正点
------
原 contr = min(vrp[T+1..T+10]) <= th。对 Entry 在 T+d (d<10) 的突破/再扩张入场，
该条件使用入场后才发生的成交量，构成未来函数。
本脚本改为：Entry 在 T+d 时，缩量条件只用 T+1..T+d-1 的量能，且要求 d>=3。

对固定日期入场（HVE 当日 d=0 / 形成窗末 d=10）维持原口径，因为缩量信息在入场时已知。
"""
import os
import numpy as np
import pandas as pd
import hve_lib as L

DATA = L.DATA
OUT = r'D:\mystock\hve_research\out'
OUT_H = [3, 5, 10, 20]
COSTS = [0, 10, 20, 30, 50]
W = 45
RNG = np.random.default_rng(L.SEED)
NBOOT = 200


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
    """Entry 在 T+d：min(vrp[T+1..T+d-1])，要求 d >= min_gap+1"""
    n = len(d)
    out = np.full(n, np.nan)
    ok = np.isfinite(d) & (d >= min_gap + 1)
    for i in np.flatnonzero(ok):
        k = int(d[i]) - 1
        out[i] = np.nanmin(vrp[i, :k])
    return out


def main():
    p = pd.read_parquet(os.path.join(DATA, 'panel.parquet'),
                        columns=['ts_code', 'vol', 'vr20', 'pct_chg', 'td_idx', 'close'])
    ev = pd.read_parquet(os.path.join(DATA, 'events_w7.parquet'))
    n = len(ev)
    vrp = build_vrp(ev, p)
    evc = ev
    t = evc['trend'].values.astype(bool)
    s = evc['struct'].values.astype(bool)
    oos = evc['trade_date'].values > 20230630
    segs = {'IS': ~oos, 'OOS': oos, 'ALL': np.ones(n, bool)}

    d_br = evc['d_br20'].values
    d_rx = evc['d_reexp'].values
    mv_br = minvr_before(vrp, d_br)
    mv_rx = minvr_before(vrp, d_rx)
    mv_f = np.nanmin(vrp[:, :10], axis=1)          # 形成窗末 T+10 入场：T+1..T+10 已知
    evc['minvr_pre_br'] = mv_br
    evc['minvr_pre_rx'] = mv_rx

    # ---------- 增量模型 ----------
    rows = []
    def add(label, mask, tag, seg):
        sub = evc[mask]
        if len(sub) < 50:
            return
        for h in OUT_H:
            st = L.stat_full(sub, f'{tag}ret_{h}', f'{tag}mfe_{h}', f'{tag}mae_{h}',
                             0.0, f'{tag}exc_{h}', label)
            rows.append(dict(model=label, segment=seg, horizon=h, n=st['n'],
                             win=st['win'], mean=st['mean'], median=st['median'],
                             pf=st['pf'], t=st['t'], exc=st.get('exc_mean', np.nan),
                             mfe=st['mfe'], mae=st['mae']))

    for sname, sm in segs.items():
        add('M0_HVE', sm, 'hve_', sname)
        add('M1_+Trend', sm & t, 'hve_', sname)
        add('M2_+Struct', sm & t & s, 'hve_', sname)
        add('M3_+Contraction(entry T+10)', sm & t & s & (mv_f <= 0.5), 'f_', sname)
        add('M4_+Reexpansion', sm & t & s & np.isfinite(mv_rx) & (mv_rx <= 0.5) & (d_rx > 0), 'rx_', sname)
        add('M5_+Breakout', sm & t & s & np.isfinite(mv_br) & (mv_br <= 0.5) & (d_br > 0), 'br_', sname)
        add('M4b_Reexp_noContr', sm & t & s & (d_rx > 0), 'rx_', sname)
        add('M5b_Brk_noContr', sm & t & s & (d_br > 0), 'br_', sname)
    inc = pd.DataFrame(rows)
    inc.to_csv(os.path.join(OUT, '03b_hvt_bull_clean.csv'), index=False, encoding='utf-8-sig')
    print('=== 增量模型（无未来函数缩量，T+10）===')
    print(inc[inc['horizon'] == 10].pivot_table(index='model', columns='segment',
                                                values=['n', 'mean', 'exc', 'pf', 'win', 'mae']).round(4).to_string())

    # ---------- W7 变体 ----------
    w7 = []
    for sname, sm in segs.items():
        V = {
            'W7_contr<=0.4': (sm & t & s & np.isfinite(mv_br) & (mv_br <= 0.4), 'br_'),
            'W7_contr<=0.5': (sm & t & s & np.isfinite(mv_br) & (mv_br <= 0.5), 'br_'),
            'W7_contr<=0.6': (sm & t & s & np.isfinite(mv_br) & (mv_br <= 0.6), 'br_'),
            'W7_contr<=0.8': (sm & t & s & np.isfinite(mv_br) & (mv_br <= 0.8), 'br_'),
            'noContr(A)': (sm & t & s & (d_br > 0), 'br_'),
            'BR60_contr<=0.5': (sm & t & s & np.isfinite(minvr_before(vrp, evc['d_br60'].values)) &
                                (minvr_before(vrp, evc['d_br60'].values) <= 0.5) &
                                (evc['d_br60'].values > 0), 'br60_'),
            'BR_HVEhigh_contr<=0.5': (sm & t & s & np.isfinite(minvr_before(vrp, evc['d_brhve'].values)) &
                                      (minvr_before(vrp, evc['d_brhve'].values) <= 0.5) &
                                      (evc['d_brhve'].values > 0), 'brh_'),
            'BR_local_contr<=0.5': (sm & t & s & np.isfinite(minvr_before(vrp, evc['d_brloc'].values)) &
                                    (minvr_before(vrp, evc['d_brloc'].values) <= 0.5) &
                                    (evc['d_brloc'].values > 0), 'brl_'),
        }
        for name, (mask, tag) in V.items():
            for h in OUT_H:
                sub = evc[mask]
                if len(sub) < 50:
                    continue
                st = L.stat_full(sub, f'{tag}ret_{h}', f'{tag}mfe_{h}', f'{tag}mae_{h}',
                                 0.0, f'{tag}exc_{h}', name)
                w7.append(dict(variant=name, segment=sname, horizon=h, n=st['n'],
                               win=st['win'], mean=st['mean'], median=st['median'],
                               pf=st['pf'], t=st['t'], exc=st.get('exc_mean', np.nan),
                               mfe=st['mfe'], mae=st['mae']))
    w7 = pd.DataFrame(w7)
    w7.to_csv(os.path.join(OUT, '04b_w7_clean.csv'), index=False, encoding='utf-8-sig')
    print('\n=== W7 变体（无未来函数缩量，T+10）===')
    print(w7[w7['horizon'] == 10].pivot_table(index='variant', columns='segment',
                                              values=['n', 'mean', 'exc', 'pf', 'win', 'mae']).round(4).to_string())

    # ---------- 策略级 Entry 对比 ----------
    ent = []
    for sname, sm in segs.items():
        okb = t & s & np.isfinite(mv_br) & (mv_br <= 0.5)
        for h in OUT_H:
            for name, arr in (
                ('P_HVE_day(无缩量信息)', np.where(sm, evc[f'hve_ret_{h}'].values, np.nan)),
                ('P_EARLY_T+10(缩量组)', np.where(sm & t & s & (mv_f <= 0.5), evc[f'f_ret_{h}'].values, np.nan)),
                ('P_WAIT_breakout_else0', np.where(sm & okb,
                                                   np.where(d_br > 0, np.nan_to_num(evc[f'br_ret_{h}'].values), 0.0), np.nan)),
                ('P_WAIT_breakout_only', np.where(sm & okb & (d_br > 0), evc[f'br_ret_{h}'].values, np.nan)),
                ('P_BREAKOUT_noContr', np.where(sm & t & s & (d_br > 0), evc[f'br_ret_{h}'].values, np.nan)),
            ):
                st = L.stat(arr, 0.0)
                ent.append(dict(entry=name, segment=sname, horizon=h, n=st['n'],
                                mean=st['mean'], median=st['median'], pf=st['pf'],
                                win=st['win'], t=st['t']))
    ent = pd.DataFrame(ent)
    ent.to_csv(os.path.join(OUT, '05c_entry_strategy_clean.csv'), index=False, encoding='utf-8-sig')
    print('\n=== 策略级 Entry 对比（T+10）===')
    print(ent[ent['horizon'] == 10].round(4).to_string(index=False))

    # ---------- 同日配对 Null（clean S4）----------
    d = pd.read_parquet(os.path.join(DATA, 'panel.parquet'),
                        columns=['ts_code', 'td_idx', 'trade_date', 'vr20', 'vol20',
                                 'obs_days', 'is_st', 'is_delist', 'ma20', 'ret20', 'dist_hh20'])
    m = ((d['is_st'].fillna(0) == 0) & (d['is_delist'].fillna(0) == 0) &
         (d['obs_days'] >= 60) & d['vr20'].notna() & d['vol20'].notna()).values
    d = d[m].reset_index(drop=True)
    d['row'] = np.flatnonzero(m)
    oc = pd.read_parquet(os.path.join(DATA, 'outcomes.parquet'),
                         columns=['row'] + [f'ret_{h}' for h in OUT_H])
    for h in OUT_H:
        d[f'ret_{h}'] = oc[f'ret_{h}'].values[m]
    d = d.sort_values('td_idx').reset_index(drop=True)
    tdv = d['td_idx'].values
    uniq, st_ = np.unique(tdv, return_index=True)
    en_ = np.concatenate([st_[1:], [len(tdv)]])
    vol20 = d['vol20'].values
    vq = np.digitize(vol20, np.nanpercentile(vol20, np.arange(10, 100, 10)))
    key2 = tdv * 20 + vq
    o2 = np.argsort(key2, kind='stable')
    u2, s2 = np.unique(key2[o2], return_index=True)
    e2 = np.concatenate([s2[1:], [len(o2)]])
    retc = {h: d[f'ret_{h}'].values for h in OUT_H}

    null_rows = []
    STRATS = {
        'M0_HVE_all': (np.ones(n, bool), 'hve_'),
        'M2_HVE_trend_struct': (t & s, 'hve_'),
        'M3_digest_T+10': (t & s & (mv_f <= 0.5), 'f_'),
        'S4clean_W7_breakout': (t & s & np.isfinite(mv_br) & (mv_br <= 0.5) & (d_br > 0), 'br_'),
        'S5clean_reexp': (t & s & np.isfinite(mv_rx) & (mv_rx <= 0.5) & (d_rx > 0), 'rx_'),
    }
    for sname, (mask, tag) in STRATS.items():
        for seg, sm in (('IS', ~oos), ('OOS', oos)):
            mm = mask & sm
            if mm.sum() < 50:
                continue
            sub = evc[mm]
            tds = sub['td_idx'].values
            sl = np.clip(np.searchsorted(uniq, tds), 0, len(st_) - 1)
            cnt = np.maximum(en_[sl] - st_[sl], 1)
            for h in OUT_H:
                real = sub[f'{tag}ret_{h}'].values
                real_m = np.nanmean(real)
                if not np.isfinite(real_m):
                    continue
                boot = np.empty(NBOOT)
                for b in range(NBOOT):
                    pick = st_[sl] + (RNG.random(len(tds)) * cnt).astype(np.int64)
                    boot[b] = np.nanmean(retc[h][np.clip(pick, 0, len(d) - 1)])
                pval = float((boot >= real_m).mean())
                null_rows.append(dict(strategy=sname, segment=seg, horizon=h,
                                      n=int(np.sum(~np.isnan(real))), mean=real_m,
                                      win=float(np.nanmean(real > 0)),
                                      ctrl_sameday=float(boot.mean()), pval=pval))
                # 成本
                for c in COSTS:
                    stx = L.stat(real, costs_bp=c)
                    null_rows.append(dict(strategy=sname, segment=seg, horizon=h,
                                          n=stx['n'], mean=stx['mean'], win=stx['win'],
                                          ctrl_sameday=np.nan, pval=np.nan,
                                          cost_bp=c))
    nr = pd.DataFrame(null_rows)
    nr.to_csv(os.path.join(OUT, '06c_null_clean.csv'), index=False, encoding='utf-8-sig')
    print('\n=== 同日配对 Null（OOS, h=10）===')
    print(nr[(nr['segment'] == 'OOS') & (nr['horizon'] == 10) & nr['cost_bp'].isna()].round(4).to_string(index=False))
    print('\n=== 成本扫描（OOS, h=10, clean S4）===')
    print(nr[(nr['segment'] == 'OOS') & (nr['horizon'] == 10) & nr['cost_bp'].notna()][
        ['strategy', 'cost_bp', 'n', 'mean', 'win']].round(4).to_string(index=False))


if __name__ == '__main__':
    main()
