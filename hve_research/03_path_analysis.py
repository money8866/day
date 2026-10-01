# -*- coding: utf-8 -*-
"""
HVE-Research V1  Step 3
Post-HVE 价格路径与状态分类

关键（避免循环论证）：
  状态使用「形成窗 T+1 .. T+f」内的数据判定（f=10 交易日），
  收益从形成窗终点 T+f 起算（T+f -> T+f+k），不使用形成窗内的收益评价状态。
"""
import os
import numpy as np
import pandas as pd
import hve_lib as L

OUT = r'D:\mystock\hve_research\out'
DATA = L.DATA
IS_END = 20230630

# 主事件定义（由 Step2 的 IS 评估确定，此处为默认值，可被 main 覆盖）
PRIMARY_METRIC = 'vr20'
PRIMARY_TH = 2.0
GAP = 5
MODE = 'first'

F = 10                      # 状态形成窗长度（交易日）
OUT_H = [3, 5, 10, 20]      # 从形成点起算的持有期


def load_ctx():
    p = pd.read_parquet(os.path.join(DATA, 'panel.parquet'),
                        columns=['ts_code', 'td_idx', 'close', 'high', 'low', 'vol',
                                 'ma20', 'ma60', 'hh20', 'ch20', 'pct_chg'])
    oc = pd.read_parquet(os.path.join(DATA, 'outcomes.parquet'))
    pool = pd.read_parquet(os.path.join(DATA, 'pool.parquet'))
    return p, oc, pool


def build_events(pool, metric, th, gap, mode):
    from importlib import import_module
    ev = import_module('02_hve_events').cluster_events(pool, metric, th, gap, mode)
    return ev.reset_index(drop=True)


def state_features(ev, p, F=F):
    """形成窗内的路径特征 + 形成点收益"""
    n = len(ev)
    pc_code = p['ts_code'].values
    code_cat = pd.Categorical(pc_code)
    codes_i = code_cat.codes                       # (N_panel,) int
    uni = code_cat.categories
    ev_code = pd.Categorical(ev['ts_code'].values, categories=uni).codes
    N = len(pc_code)
    rows = ev['row'].values.astype(np.int64)

    close = p['close'].values
    high = p['high'].values
    low = p['low'].values
    vol = p['vol'].values
    ma20 = p['ma20'].values
    ma60 = p['ma60'].values

    # ---- 形成窗序列 (n, F) ----
    off = np.arange(1, F + 1)
    idx = rows[:, None] + off[None, :]
    okm = (idx < N)
    idx2 = np.clip(idx, 0, N - 1)
    same = (codes_i[idx2] == ev_code[:, None]) & okm

    hv = np.where(same, vol[idx2], np.nan)          # 窗内成交量
    hc = np.where(same, close[idx2], np.nan)
    hh = np.where(same, high[idx2], np.nan)
    ll = np.where(same, low[idx2], np.nan)

    base_close = close[rows]
    base_vol = vol[rows]
    base_high = high[rows]

    # 量能演化
    vr_path = hv / base_vol[:, None]
    ev['volr_f'] = np.nanmean(vr_path, axis=1)                    # 形成窗均量 / HVE量
    ev['minvr_f'] = np.nanmin(vr_path, axis=1)                    # 窗内最低量 / HVE量
    ev['minvr_day'] = np.nanargmin(vr_path, axis=1) + 1
    ev['lastvr_f'] = vr_path[:, -1]
    ev['vr_t1'] = vr_path[:, 0]
    ev['vr_t3'] = vr_path[:, 2]

    # 价格路径（相对 HVE 收盘）
    ev['ret_f'] = hc[:, -1] / base_close - 1
    ev['maxret_f'] = np.nanmax(hc, axis=1) / base_close - 1
    ev['mfe_f'] = np.nanmax(hh, axis=1) / base_close - 1
    ev['mae_f'] = np.nanmin(ll, axis=1) / base_close - 1
    ev['up_days_f'] = np.nansum(hc > base_close[:, None], axis=1)
    # 是否创过 HVE 高点新高（窗口内）
    ev['above_hve_high'] = (np.nanmax(hh, axis=1) > base_high).astype(np.int8)
    # 第一次站上 HVE 高点的日子
    cond = (hh > base_high[:, None]) & same
    firsthit = np.where(cond.any(axis=1), cond.argmax(axis=1) + 1, np.nan)
    ev['first_new_high_day'] = firsthit

    # 形成点结构
    frow = np.clip(rows + F, 0, N - 1)
    same_f = codes_i[frow] == ev_code
    ev['f_row'] = np.where(same_f, frow, -1)
    ev['f_close'] = np.where(same_f, close[frow], np.nan)
    ev['f_ma20'] = np.where(same_f, ma20[frow], np.nan)
    ev['f_ma60'] = np.where(same_f, ma60[frow], np.nan)
    ev['f_dist_ma20'] = ev['f_close'] / ev['f_ma20'] - 1
    ev['f_dist_ma60'] = ev['f_close'] / ev['f_ma60'] - 1
    ev['f_valid'] = same_f.astype(np.int8)
    return ev


def state_label(ev):
    """状态判定：仅用形成窗内数据"""
    ret_f = ev['ret_f'].values
    mae_f = ev['mae_f'].values
    d20 = ev['f_dist_ma20'].values
    c20 = ev['f_dist_ma20'].values
    cond_fail = (mae_f <= -0.12) | ((c20 < -0.05) & (ret_f < -0.05))
    cond_bull = (ret_f >= 0.05) & (mae_f >= -0.05) & (c20 > 0)
    s = np.full(len(ev), 'DIGESTION', dtype=object)
    s[cond_bull & ~cond_fail] = 'BULL'
    s[cond_fail] = 'FAIL'
    ev['state'] = s
    return ev


def attach_fwd(ev, p, oc, horizons=OUT_H):
    """把形成点之后的前瞻结果接到事件上"""
    N = len(p)
    fr = ev['f_row'].values
    valid = fr >= 0
    idx = np.clip(fr, 0, N - 1)
    for h in horizons:
        ev[f'f_ret_{h}'] = np.where(valid, oc[f'ret_{h}'].values[idx], np.nan)
        ev[f'f_mfe_{h}'] = np.where(valid, oc[f'mfe_{h}'].values[idx], np.nan)
        ev[f'f_mae_{h}'] = np.where(valid, oc[f'mae_{h}'].values[idx], np.nan)
        ev[f'f_maxret_{h}'] = np.where(valid, oc[f'maxret_{h}'].values[idx], np.nan)
        ev[f'f_open_ret_{h}'] = np.where(valid, oc[f'open_ret_{h}'].values[idx], np.nan)
        ev[f'f_volr_{h}'] = np.where(valid, oc[f'volr_{h}'].values[idx], np.nan)
    # 市场同期收益（按形成点的 td_idx）
    return ev


def add_excess(ev, market):
    mf = market.set_index('td_idx')
    ftd = ev['f_td_idx'].values if 'f_td_idx' in ev.columns else None
    for h in OUT_H:
        mk = mf[f'mkt_fwd_{h}'].reindex(ftd).values
        ev[f'f_exc_{h}'] = ev[f'f_ret_{h}'].values - mk
    return ev


def summarize_states(ev):
    rows = []
    for seg, test in (('IS', lambda d: d['trade_date'] <= IS_END),
                      ('OOS', lambda d: d['trade_date'] > IS_END),
                      ('ALL', lambda d: d['trade_date'] > 0)):
        s0 = ev[test(ev)]
        for st in ['BULL', 'DIGESTION', 'FAIL', 'ALL']:
            s = s0 if st == 'ALL' else s0[s0['state'] == st]
            if len(s) < 30:
                continue
            for h in OUT_H:
                d = L.stat_full(s, f'f_ret_{h}', f'f_mfe_{h}', f'f_mae_{h}', 0.0, f'f_exc_{h}')
                rows.append(dict(segment=seg, state=st, horizon=h, n=d['n'],
                                 win=d['win'], mean=d['mean'], median=d['median'],
                                 pf=d['pf'], t=d['t'], exc=d.get('exc_mean', np.nan),
                                 exc_win=d.get('exc_win', np.nan),
                                 exc_t=d.get('exc_t', np.nan),
                                 mfe=d['mfe'], mae=d['mae']))
    return pd.DataFrame(rows)


def main():
    p, oc, pool = load_ctx()
    ev = build_events(pool, PRIMARY_METRIC, PRIMARY_TH, GAP, MODE)
    print('events', len(ev))
    ev = state_features(ev, p, F)
    # 形成点的交易日（用于市场超额）
    frow = ev['f_row'].values
    valid = frow >= 0
    Np = len(p)
    td = np.full(len(ev), -1, dtype=np.int32)
    td[valid] = p['td_idx'].values[np.clip(frow[valid], 0, Np - 1)]
    ev['f_td_idx'] = td
    ev = state_label(ev)
    ev = attach_fwd(ev, p, oc)
    m = pd.read_parquet(os.path.join(DATA, 'market.parquet'))
    m = L.add_mkt_fwd(m)
    # td_idx 映射
    ev = add_excess(ev, m)

    ev.to_parquet(os.path.join(DATA, 'events_path.parquet'), index=False)
    cols = ['ts_code', 'name', 'board', 'trade_date', 'td_idx', 'f_td_idx', 'state',
            'vr20', 'vol_pct250', 'pct_chg', 'clv', 'dist_ma20', 'dist_ma60', 'dist_hh20',
            'ret_f', 'mfe_f', 'mae_f', 'volr_f', 'minvr_f', 'lastvr_f',
            'f_dist_ma20', 'f_close', 'above_hve_high',
            'ret_1', 'ret_3', 'ret_5', 'ret_10', 'ret_20', 'ret_40', 'ret_60',
            'mfe_10', 'mae_10', 'exc_5', 'exc_10', 'exc_20'] + \
           [f'f_ret_{h}' for h in OUT_H] + [f'f_mfe_{h}' for h in OUT_H] + \
           [f'f_mae_{h}' for h in OUT_H] + [f'f_exc_{h}' for h in OUT_H]
    cols = [c for c in cols if c in ev.columns]
    ev[cols].to_csv(os.path.join(OUT, '02b_hve_event_paths.csv'),
                    index=False, encoding='utf-8-sig')

    st = summarize_states(ev)
    st.to_csv(os.path.join(OUT, '02_hve_path_analysis.csv'), index=False, encoding='utf-8-sig')
    print(st.to_string(index=False))
    print('\nstate counts:')
    print(ev['state'].value_counts().to_string())


if __name__ == '__main__':
    main()
