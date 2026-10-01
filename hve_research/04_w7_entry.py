# -*- coding: utf-8 -*-
"""
HVE-Research V1  Step 4
HVT-BULL 分层增量 / W7 二次突破 / Entry 对比

统一框架：
  事件 -> 扫描窗口 T+1..T+W -> 定位各类确认日(再扩张/突破/回踩) -> 以该确认日收盘为 Entry
  -> 用该 Entry 行自身的前瞻收益列评估结果（避免任何未来函数）

增量模型：
  M0 HVE
  M1 HVE + Trend
  M2 HVE + Trend + Structure
  M3 HVE + Trend + Structure + Volume Contraction   (Entry = T+F 形成窗末)
  M4 ... + Re-expansion                             (Entry = 再扩张日)
  M5 ... + Breakout                                 (Entry = 突破日)

W7 关键对照（缩量是否有增量）：
  A = HVE + Structure + Breakout
  B = HVE + Structure + Volume Contraction + Breakout
"""
import os
import numpy as np
import pandas as pd
import hve_lib as L

OUT = r'D:\mystock\hve_research\out'
DATA = L.DATA
IS_END = 20230630

W = 45                 # 扫描窗口
F = 10                 # 形成窗（收缩观察窗）
OUT_H = [3, 5, 10, 20]


class Scan:
    def __init__(self, ev, p):
        self.ev = ev
        self.N = len(p)
        codes_i = pd.Categorical(p['ts_code'].values).codes
        uni = pd.Categorical(p['ts_code'].values).categories
        self.ev_code = pd.Categorical(ev['ts_code'].values, categories=uni).codes
        rows = ev['row'].values.astype(np.int64)
        self.rows = rows
        off = np.arange(1, W + 1)
        idx = rows[:, None] + off[None, :]
        self.okm = idx < self.N
        idx2 = np.clip(idx, 0, self.N - 1)
        self.idx2 = idx2
        self.same = (codes_i[idx2] == self.ev_code[:, None]) & self.okm
        self.g = {}
        for c in ['close', 'high', 'low', 'vol', 'amount', 'ma20', 'ma60',
                  'hh20', 'hh60', 'pct_chg', 'vr20', 'clv', 'up_limit', 'td_idx', 'ret']:
            if c in p.columns:
                v = p[c].values
                self.g[c] = np.where(self.same, v[idx2], np.nan)

    def get(self, c):
        return self.g[c]


def first_day(cond, W=W):
    """返回条件首次成立的偏移(1..W)，否则 0"""
    any_ = cond.any(axis=1)
    d = np.where(any_, cond.argmax(axis=1) + 1, 0)
    return d


TD = None   # 全局 (n_events, W) 交易日索引矩阵


def outcomes_at(ev, oc, d, horizons=OUT_H, tag='', zero_ok=False):
    """以偏移 d 所在行为 Entry，取出该行的前瞻结果"""
    rows = ev['row'].values.astype(np.int64)
    N = len(oc)
    er = rows + d
    valid = ((d >= 0) if zero_ok else (d > 0)) & (er < N)
    er2 = np.clip(er, 0, N - 1)
    ar = np.arange(len(ev))
    res = {'e_row': np.where(valid, er2, -1), 'e_valid': valid.astype(np.int8)}
    for h in horizons:
        res[f'{tag}ret_{h}'] = np.where(valid, oc[f'ret_{h}'].values[er2], np.nan)
        res[f'{tag}mfe_{h}'] = np.where(valid, oc[f'mfe_{h}'].values[er2], np.nan)
        res[f'{tag}mae_{h}'] = np.where(valid, oc[f'mae_{h}'].values[er2], np.nan)
        res[f'{tag}open_ret_{h}'] = np.where(valid, oc[f'open_ret_{h}'].values[er2], np.nan)
    tdf = np.where(d > 0, np.nan_to_num(TD[ar, np.clip(d - 1, 0, W - 1)], nan=-1.0),
                   np.asarray(ev['td_idx'].values, dtype=np.float64))
    res[f'{tag}td_idx'] = np.where(valid, tdf, -1.0)
    return res


def summarize(df, ret_pre, mfe_pre, mae_pre, exc_pre=None, label='', segment='ALL'):
    rows = []
    for h in OUT_H:
        d = L.stat_full(df, f'{ret_pre}ret_{h}', f'{mfe_pre}mfe_{h}', f'{mae_pre}mae_{h}', 0.0,
                        f'{exc_pre}exc_{h}' if exc_pre else None, label)
        rows.append(dict(model=label, segment=segment, horizon=h, n=d['n'],
                         win=d['win'], mean=d['mean'], median=d['median'],
                         pf=d['pf'], t=d['t'], exc=d.get('exc_mean', np.nan),
                         exc_win=d.get('exc_win', np.nan), exc_t=d.get('exc_t', np.nan),
                         mfe=d['mfe'], mae=d['mae']))
    return rows


def main():
    p = pd.read_parquet(os.path.join(DATA, 'panel.parquet'))
    oc = pd.read_parquet(os.path.join(DATA, 'outcomes.parquet'))
    pool = pd.read_parquet(os.path.join(DATA, 'pool.parquet'))
    m = L.add_mkt_fwd(pd.read_parquet(os.path.join(DATA, 'market.parquet')))
    import importlib
    ev = importlib.import_module('02_hve_events').cluster_events(
        pool, 'vr20', 2.0, 5, 'first').reset_index(drop=True)
    print('events', len(ev))

    S = Scan(ev, p)
    global TD
    TD = S.get('td_idx')
    # 市场前瞻收益按 td_idx 建表
    mfi = m.set_index('td_idx')
    MKT = {h: np.asarray(mfi[f'mkt_fwd_{h}'].reindex(
        np.arange(int(m['td_idx'].max()) + 2)).values, dtype=float)
        for h in OUT_H}
    MKT[-1] = np.zeros(1)

    close = S.get('close'); high = S.get('high'); low = S.get('low')
    vol = S.get('vol'); ma20 = S.get('ma20'); ma60 = S.get('ma60')
    hh20 = S.get('hh20'); hh60 = S.get('hh60'); pct = S.get('pct_chg'); vr = S.get('vr20')
    base_close = p['close'].values[ev['row'].values]
    base_vol = p['vol'].values[ev['row'].values]
    base_high = p['high'].values[ev['row'].values]

    # ---------- 路径特征 ----------
    vrp = vol / base_vol[:, None]
    ev['minvr10'] = np.nanmin(vrp[:, :F], axis=1)
    ev['volr10'] = np.nanmean(vrp[:, :F], axis=1)
    ev['mae10'] = np.nanmin(low[:, :F], axis=1) / base_close - 1
    ev['mfe10'] = np.nanmax(high[:, :F], axis=1) / base_close - 1
    struct_ok = np.nanmin(close[:, :F] / ma20[:, :F] - 1, axis=1)     # 消化期最低相对MA20
    ev['struct_min'] = struct_ok

    # ---------- 确认日 ----------
    # 再扩张: 量能重新放大(>=1.5*前20日均量) 且 当日涨幅>=3%
    reexp = (vr >= 1.5) & (pct >= 3.0)
    d_reexp = first_day(reexp)
    # 突破: 收盘创20日新高 / 60日新高 / 突破HVE高点 / 突破HVE后局部高点
    cummax_h = np.maximum.accumulate(np.nan_to_num(high, nan=-np.inf), axis=1)
    prev_max = np.concatenate([np.full((len(ev), 1), -np.inf), cummax_h[:, :-1]], axis=1)
    br20 = close > hh20
    br60 = close > hh60
    br_hve = close > base_high[:, None]
    br_loc = (close > prev_max) & (prev_max > 0)
    d_br20 = first_day(br20)
    d_br60 = first_day(br60)
    d_br_hve = first_day(br_hve)
    d_br_loc = first_day(br_loc)
    ev['d_reexp'] = d_reexp
    ev['d_br20'] = d_br20
    ev['d_br60'] = d_br60
    ev['d_brhve'] = d_br_hve
    ev['d_brloc'] = d_br_loc

    # ---------- 基础条件 ----------
    trend = (ev['dist_ma20'].values > 0) & (ev['ma20_slope5'].values > 0)
    struct = (ev['dist_ma60'].values > 0) & (ev['dist_hh60'].values > -0.15)
    contr = ev['minvr10'].values <= 0.5
    ev['trend'] = trend.astype(np.int8)
    ev['struct'] = struct.astype(np.int8)
    ev['contr'] = contr.astype(np.int8)

    def mk_out(d, tag, zero_ok=False):
        r = outcomes_at(ev, oc, d, tag=tag, zero_ok=zero_ok)
        for k, v in r.items():
            ev[k] = v
        # 超额
        tix = ev[f'{tag}td_idx'].values.astype(np.int64)
        tix = np.where(tix < 0, -1, tix)
        for h in OUT_H:
            arr = MKT[h]
            mk = np.where((tix >= 0) & (tix < len(arr)), arr[np.clip(tix, 0, len(arr) - 1)], np.nan)
            ev[f'{tag}exc_{h}'] = ev[f'{tag}ret_{h}'].values - mk

    mk_out(np.full(len(ev), F), 'f_')        # 形成窗末 T+10
    mk_out(d_reexp, 'rx_')
    mk_out(d_br20, 'br_')
    mk_out(d_br60, 'br60_')
    mk_out(d_br_hve, 'brh_')
    mk_out(d_br_loc, 'brl_')
    # HVE 当日
    mk_out(np.zeros(len(ev), dtype=np.int64), 'hve_', zero_ok=True)
    # 全体 HVE 在 T+1 / T+3 / T+5 入场的对照（不做事后筛选）
    for k in (1, 3, 5):
        mk_out(np.full(len(ev), k, dtype=np.int64), f't{k}_')

    # ---------- 回踩 Entry（突破后） ----------
    brd = d_br20
    okb = brd > 0
    rows = ev['row'].values
    # 突破日收盘价
    br_close = np.where(okb, close[np.arange(len(ev)), np.clip(brd - 1, 0, W - 1)], np.nan)
    after = np.arange(1, W + 1)[None, :] > np.clip(brd, 1, W)[:, None]
    pull_cond = (low <= br_close[:, None] * 0.98) & (close > ma20) & after & S.same
    d_pull = np.where(okb, first_day(pull_cond), 0)
    ev['d_pull'] = d_pull
    mk_out(d_pull, 'pb_')
    # 突破后一日
    mk_out(np.where(okb, np.clip(brd + 1, 0, W), 0), 'brp1_')
    # 突破前潜伏：-1 / -3 / -5
    for k in (1, 3, 5):
        dk = np.where(okb & (brd - k >= 1), brd - k, 0)
        ev[f'd_lat{k}'] = dk
        mk_out(dk, f'lat{k}_')

    ev.to_parquet(os.path.join(DATA, 'events_w7.parquet'), index=False)

    # ---------- 统计 ----------
    segs = {'IS': ev['trade_date'] <= IS_END, 'OOS': ev['trade_date'] > IS_END,
            'ALL': ev['trade_date'] > 0}
    out_rows = []

    def add(label, mask, tag, seg_name='ALL'):
        sub = ev[mask]
        if len(sub) < 50:
            return
        out_rows.extend(summarize(sub, tag, tag, tag, tag, label, seg_name))

    for sname, sm in segs.items():
        s0 = ev[sm]
        # HVT-BULL 分层
        add('M0_HVE', sm, 'hve_', sname)
        add('M1_+Trend', sm & trend, 'hve_', sname)
        add('M2_+Struct', sm & trend & struct, 'hve_', sname)
        add('M3_+Contraction', sm & trend & struct & contr, 'f_', sname)
        # M4: 缩量后再扩张
        m4 = sm & trend & struct & contr & (d_reexp > 0)
        add('M4_+Reexpansion', m4, 'rx_', sname)
        m5 = sm & trend & struct & contr & (d_br20 > 0)
        add('M5_+Breakout', m5, 'br_', sname)
        # 无缩量对照
        add('M4b_Reexp_noContr', sm & trend & struct & (d_reexp > 0), 'rx_', sname)
        add('M5b_Brk_noContr', sm & trend & struct & (d_br20 > 0), 'br_', sname)

    inc = pd.DataFrame(out_rows)
    inc.to_csv(os.path.join(OUT, '03_hvt_bull_analysis.csv'), index=False, encoding='utf-8-sig')
    print('\n=== 增量模型 (T+10) ===')
    print(inc[inc['horizon'] == 10].pivot_table(
        index='model', columns='segment',
        values=['n', 'mean', 'exc', 'win', 'pf', 'mfe', 'mae']).round(4).to_string())

    # ---------- W7 定义对照 ----------
    w7_rows = []
    for sname, sm in segs.items():
        s0 = ev[sm]
        variants = {
            'W7_full(contr<=0.5)': (sm & trend & struct & contr & (d_br20 > 0), 'br_'),
            'W7_contr<=0.6': (sm & trend & struct & (ev['minvr10'].values <= 0.6) & (d_br20 > 0), 'br_'),
            'W7_contr<=0.4': (sm & trend & struct & (ev['minvr10'].values <= 0.4) & (d_br20 > 0), 'br_'),
            'W7_contr<=0.8': (sm & trend & struct & (ev['minvr10'].values <= 0.8) & (d_br20 > 0), 'br_'),
            'noContr(A)': (sm & trend & struct & (d_br20 > 0), 'br_'),
            'contrOnly': (sm & contr & (d_br20 > 0), 'br_'),
            'BR60': (sm & trend & struct & contr & (d_br60 > 0), 'br60_'),
            'BR_HVEhigh': (sm & trend & struct & contr & (d_br_hve > 0), 'brh_'),
            'BR_local': (sm & trend & struct & contr & (d_br_loc > 0), 'brl_'),
        }
        for name, (mask, tag) in variants.items():
            for h in OUT_H:
                sub = ev[mask]
                if len(sub) < 50:
                    continue
                d = L.stat_full(sub, f'{tag}ret_{h}',
                                f'{tag}mfe_{h}', f'{tag}mae_{h}', 0.0, f'{tag}exc_{h}', name)
                w7_rows.append(dict(variant=name, segment=sname, horizon=h, n=d['n'],
                                    win=d['win'], mean=d['mean'], median=d['median'],
                                    pf=d['pf'], t=d['t'], exc=d.get('exc_mean', np.nan),
                                    exc_t=d.get('exc_t', np.nan), mfe=d['mfe'], mae=d['mae']))
    w7 = pd.DataFrame(w7_rows)
    w7.to_csv(os.path.join(OUT, '04_w7_second_breakout_analysis.csv'),
              index=False, encoding='utf-8-sig')
    print('\n=== W7 变体 (T+10) ===')
    print(w7[w7['horizon'] == 10].pivot_table(
        index='variant', columns='segment',
        values=['n', 'mean', 'exc', 'win', 'pf', 'mae']).round(4).to_string())

    # ---------- Entry 对比（同一批突破事件） ----------
    ent_rows = []
    brset = (d_br20 > 0) & trend & struct & contr
    for sname, sm in segs.items():
        base = sm & brset
        ents = [('E0_breakout_day', d_br20, 'br_'),
                ('E+1_after', ev['d_br20'].values + 1, 'brp1_'),
                ('E_lat1', ev['d_lat1'].values, 'lat1_'),
                ('E_lat3', ev['d_lat3'].values, 'lat3_'),
                ('E_lat5', ev['d_lat5'].values, 'lat5_'),
                ('E_pullback', ev['d_pull'].values, 'pb_')]
        for name, dd, tag in ents:
            for h in OUT_H:
                sub = ev[base]
                if len(sub) < 50:
                    continue
                d = L.stat_full(sub, f'{tag}ret_{h}', f'{tag}mfe_{h}', f'{tag}mae_{h}',
                                0.0, f'{tag}exc_{h}', name)
                ent_rows.append(dict(entry=name, segment=sname, horizon=h, n=d['n'],
                                     win=d['win'], mean=d['mean'], median=d['median'],
                                     pf=d['pf'], t=d['t'], exc=d.get('exc_mean', np.nan),
                                     mfe=d['mfe'], mae=d['mae']))
        # 全体 HVE 事件（不做突破筛选）在 T+1 / T+3 / T+5 入场的对照
        for k in (1, 3, 5):
            for h in OUT_H:
                sub = ev[sm]
                d = L.stat_full(sub, f't{k}_ret_{h}',
                                f't{k}_mfe_{h}', f't{k}_mae_{h}', 0.0, f't{k}_exc_{h}',
                                f'ALL_HVE_T+{k}')
                ent_rows.append(dict(entry=f'ALL_HVE_T+{k}', segment=sname, horizon=h,
                                     n=d['n'], win=d['win'], mean=d['mean'],
                                     median=d['median'], pf=d['pf'], t=d['t'],
                                     exc=d.get('exc_mean', np.nan), mfe=d['mfe'], mae=d['mae']))
    ent = pd.DataFrame(ent_rows)
    ent.to_csv(os.path.join(OUT, '05_entry_comparison.csv'), index=False, encoding='utf-8-sig')
    print('\n=== Entry 对比 (T+10) ===')
    print(ent[(ent['horizon'] == 10)].pivot_table(
        index='entry', columns='segment',
        values=['n', 'mean', 'exc', 'win', 'pf', 'mfe', 'mae']).round(4).to_string())


if __name__ == '__main__':
    main()
