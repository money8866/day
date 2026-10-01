# -*- coding: utf-8 -*-
"""
HVE-Research V1 — Step 11b
代理口径交叉验证：config 的 HVT 定义以「换手率」为核心 (turn_pct250 / turn/20日均换手)，
而 Step 11 主口径用「成交量」代理（250日内流通股本近似不变 ⇒ 量分位 ≈ 换手分位）。
本脚本在 turnover_rate 可用的子样本（2023-01 起）上，直接对比两种口径。

检验内容
--------
1. 事件重合度（Jaccard / 相互命中率）
2. 两口径相同分层下的 OOS 结果（HVT only / +PriceStrength / +Trend / +GainStruct / +Breakout）
3. 市值门（total_mv>=30亿）开/关的敏感性
"""
import os
import numpy as np
import pandas as pd
import hve_lib as L
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location(
    'repl11', os.path.join(os.path.dirname(os.path.abspath(__file__)), '11_replicate_hvt.py'))
_m = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_m)
add_roll = _m.add_roll
cluster_events = _m.cluster_events
EvSet = _m.EvSet
summarize = _m.summarize

DATA = L.DATA
OUT = r'D:\mystock\hve_research\out'
W = 120
OUT_H = [3, 5, 10, 20]
IS_END = 20230630
AMT_MIN = 30000.0
MV_MIN = 300000.0
MIN_LISTED = 120


def run(mask_name, uni_mask, use_turn, p, oc, mktf):
    pct = (p['turn_pct250'] if use_turn else p['vol_pct250']).values * 100.0
    rat = (p['turn_r20p'] if use_turn else p['vr20']).values
    ar = p['amt_r20p'].values
    hvtA = uni_mask & (pct >= 99.0) & (rat >= 3.0) & (ar >= 2.0)
    hvtB = uni_mask & (pct >= 98.0) & (rat >= 2.0) & (ar >= 2.0)
    mask = hvtA | hvtB
    rows = np.flatnonzero(mask)
    rows = cluster_events(rows, p['td_idx'].values[rows], p['_c'].values[rows],
                          p['vol'].values, gap=10, mode='first')
    return rows


def main():
    cols = ['ts_code', 'trade_date', 'td_idx', 'open', 'high', 'low', 'close',
            'pct_chg', 'vol', 'amount', 'turnover_rate', 'total_mv', 'ma20', 'ma60',
            'vr20', 'vol_pct250', 'turn_pct250', 'vol20', 'ret20', 'ret60',
            'ma20_slope10', 'board', 'is_st', 'is_delist', 'obs_days']
    p = pd.read_parquet(os.path.join(DATA, 'panel.parquet'), columns=cols)
    p['_c'] = pd.factorize(p['ts_code'], sort=False)[0]
    add_roll(p, 'amount', 20, 'mean', 1, 20, 'amt20p')
    p['amt_r20p'] = p['amount'] / p['amt20p']
    add_roll(p, 'high', 120, 'max', 1, 60, 'hh120')
    add_roll(p, 'vol', 20, 'mean', 0, 20, 'vma20i')
    p['vr20i'] = p['vol'] / p['vma20i']
    add_roll(p, 'turnover_rate', 20, 'mean', 1, 20, 'turn20p')
    p['turn_r20p'] = p['turnover_rate'] / p['turn20p']
    hl = (p['high'].values - p['low'].values)
    hl = np.where(hl <= 0, np.nan, hl)
    p['cp'] = (p['close'].values - p['low'].values) / hl
    p['body'] = (p['close'].values - p['open'].values) / hl
    p['dist_hh120'] = (p['hh120'].values / p['close'].values - 1.0) * 100.0
    p['row'] = np.arange(len(p), dtype=np.int64)

    MKT = pd.read_parquet(os.path.join(DATA, 'market.parquet'), columns=['td_idx', 'ew_ret', 'med_ret'])
    MKT = L.add_mkt_fwd(MKT, OUT_H)
    mktf = {h: MKT[f'mkt_fwd_{h}'].values.astype(np.float64) for h in OUT_H}
    oc = pd.read_parquet(os.path.join(DATA, 'outcomes.parquet'),
                         columns=[f'{k}_{h}' for h in OUT_H for k in ('ret', 'mfe', 'mae')])

    mvv = p['total_mv'].values
    base = ((p['board'].values != 'BJ') &
            (np.nan_to_num(p['is_st'].values) == 0) &
            (np.nan_to_num(p['is_delist'].values) == 0) &
            (np.nan_to_num(p['obs_days'].values) >= MIN_LISTED) &
            (np.nan_to_num(p['amt20p'].values) >= AMT_MIN) &
            np.isfinite(p['vr20'].values) & np.isfinite(p['amt_r20p'].values) &
            np.isfinite(p['hh120'].values) & np.isfinite(p['ma20'].values))
    has_mv = np.isfinite(mvv)
    uni_mv = base & np.where(has_mv, mvv >= MV_MIN, True)
    # 换手率口径要求 turnover 可用
    has_turn = np.isfinite(p['turn_pct250'].values) & np.isfinite(p['turn_r20p'].values)
    print(f'换手率可用行 {int(has_turn.sum()):,} / {len(p):,}', flush=True)

    res = []
    sets = {}
    for uname, um in (('U_withMV', uni_mv & has_turn), ('U_noMV', base & has_turn)):
        for tname, ut in (('turnover', True), ('vol_proxy', False)):
            rows = run(f'{uname}_{tname}', um, ut, p, oc, mktf)
            sets[(uname, tname)] = rows
            print(f'{uname:9s} {tname:10s} 事件数 {len(rows):,}', flush=True)

    # 重合度
    ov = []
    for uname in ('U_withMV', 'U_noMV'):
        a = sets[(uname, 'turnover')]
        b = sets[(uname, 'vol_proxy')]
        sa, sb = set(a.tolist()), set(b.tolist())
        inter = len(sa & sb)
        ov.append(dict(universe=uname, n_turnover=len(sa), n_vol=len(sb),
                       inter=inter,
                       jaccard=inter / max(len(sa | sb), 1),
                       hit_turn_in_vol=inter / max(len(sa), 1),
                       hit_vol_in_turn=inter / max(len(sb), 1)))
    ov = pd.DataFrame(ov)
    print('\n=== 事件重合度 ===')
    print(ov.round(4).to_string(index=False))

    keep_cols = ['ts_code', 'trade_date', 'td_idx', 'row', 'close', 'high', 'low', 'open',
                 'vol', 'amount', 'pct_chg', 'cp', 'body', 'ma20', 'ma60', 'ma20_slope10',
                 'ret20', 'ret60', 'dist_hh120', 'vr20', 'vol_pct250', 'turn_pct250',
                 'turn_r20p', 'total_mv', 'vol20', 'board']
    for (uname, tname), rows in sets.items():
        if len(rows) < 200:
            continue
        ev = p.iloc[rows][keep_cols].reset_index(drop=True)
        E = EvSet(p, rows, tname, oc, mktf)
        close_m = E.mat(p['close'].values, 'close')
        low_m = E.mat(p['low'].values, 'low')
        vol_m = E.mat(p['vol'].values, 'vol')
        vr20i_m = E.mat(p['vr20i'].values, 'vr20i')
        cp_m = E.mat(p['cp'].values, 'cp')
        del E._cache['vr20i'], E._cache['cp']
        n = len(ev)
        c0 = ev['close'].values
        h0 = ev['high'].values
        ps = ((ev['pct_chg'].values >= 3.0) & (ev['cp'].values >= 0.70) & (ev['body'].values >= 0.30))
        tr = ((c0 > ev['ma20'].values) & (ev['ma20_slope10'].values > 0) & (c0 > ev['ma60'].values))
        r20 = ev['ret20'].values * 100.0
        r60 = ev['ret60'].values * 100.0
        dh = ev['dist_hh120'].values
        gs = ((r20 >= 10.0) & (r20 <= 40.0) & (r60 >= 10.0) & (r60 <= 80.0) &
              (dh >= 5.0) & (dh <= 30.0))
        v5 = np.nanmean(vol_m[:, :5], axis=1)
        l5 = np.nanmin(low_m[:, :5], axis=1)
        c5 = close_m[:, 4]
        chip = ((v5 <= ev['vol'].values * 0.60) & (l5 >= c0 * 0.93) & (c5 >= c0 * 0.95))
        cond = (close_m > (h0[:, None] * 1.01)) & (vr20i_m >= 1.3) & (cp_m >= 0.75)
        anyb = cond.any(axis=1)
        d_br = np.where(anyb, np.argmax(cond, axis=1) + 1.0, np.nan)
        vrp = vol_m / np.where(ev['vol'].values[:, None] > 0, ev['vol'].values[:, None], np.nan)
        mv_br = np.full(n, np.nan)
        for i in np.flatnonzero(np.isfinite(d_br)):
            k = int(d_br[i]) - 1
            if k >= 1:
                mv_br[i] = np.nanmin(vrp[i, :k])
        for tag, d in (('hve_', np.zeros(n)), ('chip_', np.full(n, 5.0)), ('br_', d_br)):
            for k, v in E.outcomes(d, tag).items():
                ev[k] = v
        oos = ev['trade_date'].values > IS_END
        rl = []
        for sname, sm in (('IS', ~oos), ('OOS', oos), ('ALL', np.ones(n, bool))):
            for label, mask, tag in (
                ('L0_HVT_only', np.ones(n, bool), 'hve_'),
                ('L1_+PriceStrength', ps, 'hve_'),
                ('L2_+Trend', ps & tr, 'hve_'),
                ('L3_+GainStruct', ps & tr & gs, 'hve_'),
                ('L4_+ChipLock(T+5)', ps & tr & gs & chip, 'chip_'),
                ('L5_+Breakout', ps & tr & gs & np.isfinite(d_br), 'br_'),
                ('L5b_Brk+Contr<=0.6', ps & tr & gs & np.isfinite(d_br) & (mv_br <= 0.6), 'br_'),
            ):
                summarize(ev, sm & mask, tag, label, sname, rl)
        df = pd.DataFrame(rl)
        df['universe'] = uname
        df['hvt_metric'] = tname
        res.append(df)

    res = pd.concat(res, ignore_index=True)
    ov['_k'] = 1
    res.to_csv(os.path.join(OUT, '12g_hvt_turnover_vs_vol.csv'), index=False, encoding='utf-8-sig')
    ov.to_csv(os.path.join(OUT, '12h_hvt_def_overlap.csv'), index=False, encoding='utf-8-sig')
    print('\n=== 换手率 vs 成交量代理（OOS, T+10, 0bp）===')
    print(res[(res.segment == 'OOS') & (res.horizon == 10)].pivot_table(
        index='model', columns=['universe', 'hvt_metric'],
        values=['n', 'mean', 'pf', 'exc']).round(4).to_string())
    print('\n=== 全样本（IS+OOS, T+10）===')
    print(res[(res.segment == 'ALL') & (res.horizon == 10)].pivot_table(
        index='model', columns=['universe', 'hvt_metric'],
        values=['n', 'mean', 'pf']).round(4).to_string())
    print('\nDONE', flush=True)


if __name__ == '__main__':
    main()
