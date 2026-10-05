# -*- coding: utf-8 -*-
"""
E09-V02 Step 1：构建启动日 S 事件集 + 整理窗 + 确认日 B + 全部持有期收益

数据源（复用项目现有 Tushare 本地缓存，未更换）
------------------------------------------------
* D:\\mystock\\cache_daily\\stock_data.db  → daily_cache / daily_basic_cache
* D:\\mystock\\hve_research\\data\\panel.parquet  ← 由上述缓存构建的无未来函数面板
  （已通过 Look-ahead 审计：ma20/vma20 为"含当日"，hh20/ll20 为"不含当日"，
    所有滚动量只用历史数据）

实际可用区间：由面板自动检测（缓存 daily_cache 仅 2021-01-04 起）。
"""
import os
import sqlite3
import numpy as np
import pandas as pd
import e09_lib as E

W = E.W


def load_panel():
    cols = ['ts_code', 'trade_date', 'td_idx', 'open', 'high', 'low', 'close',
            'pct_chg', 'vol', 'amount', 'ma20', 'ma60', 'vma20', 'vr20', 'amt_r20',
            'board', 'up_limit', 'yizi', 'is_st', 'is_delist', 'obs_days',
            'total_mv', 'turnover_rate', 'vol_pct250', 'name']
    p = pd.read_parquet(E.PANEL, columns=cols)
    p['_c'] = pd.factorize(p['ts_code'], sort=False)[0]
    return p


def add_features(p):
    n = len(p)
    close = p['close'].values.astype(np.float64)
    high = p['high'].values.astype(np.float64)
    low = p['low'].values.astype(np.float64)
    opn = p['open'].values.astype(np.float64)
    codes = p['_c'].values
    brk = np.flatnonzero(np.diff(codes)) + 1
    starts = np.concatenate([[0], brk])
    ends = np.concatenate([brk, [n]])

    mom = {k: np.full(n, np.nan) for k in (5, 10, 20)}
    trs = np.full(n, np.nan)
    rng = np.full(n, np.nan)
    body = np.full(n, np.nan)
    ush = np.full(n, np.nan)
    lsh = np.full(n, np.nan)
    cp = np.full(n, np.nan)
    for s, e in zip(starts, ends):
        c = close[s:e]; h = high[s:e]; l = low[s:e]; o = opn[s:e]
        m = e - s
        for k in (5, 10, 20):
            if m > k:
                mom[k][s + k:e] = c[k:] / c[:m - k] - 1.0
        pc = np.concatenate([[np.nan], c[:-1]])
        hl = h - l
        trs[s:e] = np.maximum(hl, np.maximum(np.abs(h - pc), np.abs(l - pc)))
        rng[s:e] = np.where(hl > 0, hl / np.where(c > 0, c, np.nan), np.nan)
        body[s:e] = np.where(hl > 0, (c - o) / hl, np.nan)
        hi_lo = np.maximum(c, o)
        lo_hi = np.minimum(c, o)
        ush[s:e] = np.where(hl > 0, (h - hi_lo) / hl, np.nan)
        lsh[s:e] = np.where(hl > 0, (lo_hi - l) / hl, np.nan)
        cp[s:e] = np.where(hl > 0, (c - l) / hl, np.nan)

    p['mom5'], p['mom10'], p['mom20'] = mom[5], mom[10], mom[20]
    p['tr'] = trs
    E.add_roll(p, 'tr', 14, 'mean', 0, 10, 'atr14')
    p['range_pct'] = rng
    p['body_pct'] = body
    p['upper_shadow_pct'] = ush
    p['lower_shadow_pct'] = lsh
    p['close_position'] = cp
    E.add_roll(p, 'amount', 20, 'mean', 0, 20, 'amt20i')
    p['ma20_dist'] = p['close'] / p['ma20'] - 1.0
    p['ma60_dist'] = p['close'] / p['ma60'] - 1.0
    p['vol_ma20r'] = p['vol'] / p['vma20']
    p['amt_r20i'] = p['amount'] / p['amt20i']
    # 启动日前的 1/3/5/10 日累计涨幅（动量画像）
    for k in (3, 5, 10):
        r = np.full(n, np.nan)
        for s, e in zip(starts, ends):
            c = close[s:e]; m = e - s
            if m > k:
                r[s + k:e] = c[k:] / c[:m - k] - 1.0
        p[f'ret{k}_prior'] = r
    return p


def market_state():
    con = sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db')
    q = ("select trade_date, close from index_daily_cache "
         "where ts_code='000001.SH' order by trade_date")
    ix = pd.read_sql(q, con)
    con.close()
    ix['trade_date'] = ix['trade_date'].astype(np.int64)
    ix['ma20'] = ix['close'].rolling(20, min_periods=20).mean()
    ix['ma60'] = ix['close'].rolling(60, min_periods=60).mean()
    st = np.where((ix['close'] > ix['ma20']) & (ix['ma20'] > ix['ma60']), 'Bull',
         np.where((ix['close'] < ix['ma20']) & (ix['ma20'] < ix['ma60']), 'Bear', 'Neutral'))
    ix['regime'] = st
    ix = ix[['trade_date', 'close', 'ma20', 'ma60', 'regime']].rename(
        columns={'close': 'idx_close', 'ma20': 'idx_ma20', 'ma60': 'idx_ma60'})
    return ix


def build_matrices(p, rows):
    """返回 (n, W) 偏移矩阵字典：列 j-1 = 第 S+j 个交易日"""
    N = len(p)
    cats = pd.Categorical(p['ts_code'].values)
    code_i = cats.codes
    ev_code = code_i[rows]
    off = np.arange(1, W + 1)
    idx = np.clip(rows[:, None] + off[None, :], 0, N - 1)
    same = (code_i[idx] == ev_code[:, None])
    out = {}
    for name in ('close', 'high', 'low', 'open', 'vol', 'vma20', 'ma20', 'up_limit'):
        v = p[name].values.astype(np.float64)
        out[name] = np.where(same, v[idx], np.nan).astype(np.float32)
    v = p['td_idx'].values.astype(np.float64)
    out['td'] = np.where(same, v[idx], np.nan).astype(np.float32)
    return out


def main():
    print('[1/5] 载入面板 ...', flush=True)
    p = load_panel()
    dmin, dmax = int(p['trade_date'].min()), int(p['trade_date'].max())
    print(f'      实际数据范围 {dmin} ~ {dmax}', flush=True)
    print(f'      总行数 {len(p):,}  股票数 {p["ts_code"].nunique():,}', flush=True)

    print('[2/5] 计算特征 ...', flush=True)
    p = add_features(p)

    # ---------- 股票池 ----------
    bad = ((p['vol'].values <= 0) | (p['close'].values <= 0) |
           (p['high'].values < p['low'].values) | ~np.isfinite(p['pct_chg'].values))
    uni = ((np.nan_to_num(p['is_st'].values) == 0) &
           (np.nan_to_num(p['obs_days'].values) >= E.MIN_OBS) &
           np.isfinite(p['ma20'].values) & np.isfinite(p['vma20'].values) &
           np.isfinite(p['mom20'].values) & ~bad)
    print(f'      可投池 {int(uni.sum()):,} 行（剔除 ST / 错误行情 / 历史不足）', flush=True)
    print(f'      含退市标记股票 {int(p.loc[uni, "is_delist"].fillna(0).sum() and p["ts_code"][p["is_delist"].fillna(0)==1].nunique())} 只', flush=True)

    # ---------- 启动日 S ----------
    S = uni & (p['pct_chg'].values >= E.P_RET) & \
        (p['vol_ma20r'].values >= E.P_VR) & (p['close'].values > p['ma20'].values)
    rows = np.flatnonzero(S)
    print(f'[3/5] 启动日 S = {len(rows):,}', flush=True)

    M = build_matrices(p, rows)
    n = len(rows)
    close_m = M['close'].astype(np.float64)
    high_m = M['high'].astype(np.float64)
    low_m = M['low'].astype(np.float64)
    open_m = M['open'].astype(np.float64)
    vol_m = M['vol'].astype(np.float64)
    vma_m = M['vma20'].astype(np.float64)
    ma20_m = M['ma20'].astype(np.float64)
    td_m = M['td'].astype(np.float64)

    ev = p.iloc[rows][['ts_code', 'trade_date', 'td_idx', 'open', 'high', 'low', 'close',
                       'pct_chg', 'vol', 'amount', 'ma20', 'ma60', 'vma20', 'vr20',
                       'amt_r20', 'amt_r20i', 'vol_ma20r', 'board', 'up_limit', 'yizi',
                       'obs_days', 'mom5', 'mom10', 'mom20', 'atr14', 'range_pct',
                       'body_pct', 'upper_shadow_pct', 'lower_shadow_pct',
                       'close_position', 'ma20_dist', 'ma60_dist', 'amt20i',
                       'ret3_prior', 'ret5_prior', 'ret10_prior', 'total_mv',
                       'turnover_rate', 'vol_pct250', 'is_delist']].reset_index(drop=True)
    ev['row'] = rows
    c0 = ev['close'].values.astype(np.float64)
    h0 = ev['high'].values.astype(np.float64)
    l0 = ev['low'].values.astype(np.float64)
    o0 = ev['open'].values.astype(np.float64)
    v0 = ev['vol'].values.astype(np.float64)
    mid0 = (o0 + c0) / 2.0

    # ---------- 各 L 的整理窗统计 ----------
    print('[4/5] 整理窗与确认日 ...', flush=True)
    for Lx in E.L_GRID:
        sl = slice(0, Lx)
        ev[f'meanvr_{Lx}'] = np.nanmean(vol_m[:, sl], axis=1) / v0
        ev[f'minlow_{Lx}'] = np.nanmin(low_m[:, sl], axis=1) / c0
        ev[f'maxdd_close_{Lx}'] = ev[f'minlow_{Lx}'] - 1.0
        ev[f'minhigh_{Lx}'] = np.nanmin(high_m[:, sl], axis=1) / c0
        ev[f'maxdd_high_{Lx}'] = np.nanmin(high_m[:, sl], axis=1) / h0 - 1.0
        # 结构破坏标签（§10，仅作标签）
        ev[f'brk_ma20_{Lx}'] = np.nanmean((ma20_m[:, sl] > close_m[:, sl]).astype(np.float64), axis=1)
        ev[f'brk_Slow_{Lx}'] = (np.nanmin(low_m[:, sl], axis=1) < l0).astype(np.float64)
        ev[f'brk_Smid_{Lx}'] = (np.nanmin(close_m[:, sl], axis=1) < mid0).astype(np.float64)
        ev[f'brk_Sclose_{Lx}'] = (np.nanmin(close_m[:, sl], axis=1) < c0).astype(np.float64)
        # 量能衰减（§31）
        for j in range(1, 5):
            ev[f'vr{j}_of_S'] = np.where(j <= W, vol_m[:, j - 1] / v0, np.nan)
        xs = np.arange(1, Lx + 1, dtype=np.float64)
        yy = vol_m[:, sl] / v0[:, None]
        ok = np.isfinite(yy).all(axis=1)
        slope = np.full(n, np.nan)
        if ok.any():
            yv = yy[ok]
            xm = xs.mean()
            ym = yv.mean(axis=1)
            den = ((xs - xm) ** 2).sum()
            slope[ok] = ((yv - ym[:, None]) * (xs - xm)[None, :]).sum(axis=1) / den
        ev[f'vol_decay_slope_{Lx}'] = slope
        # 价格收缩（§32）
        ev[f'range_mean_{Lx}'] = np.nanmean(
            (high_m[:, sl] - low_m[:, sl]) / close_m[:, sl], axis=1)
        ev[f'range_ratio_{Lx}'] = ev[f'range_mean_{Lx}'] / np.where(
            ev['range_pct'].values > 0, ev['range_pct'].values, np.nan)

    # ---------- 确认日 B ----------
    for tag, lo_off in (('main', 'L+1'), ('lit', '2')):
        for Lx in E.L_GRID:
            if tag == 'main':
                cols = np.arange(Lx, 10)          # 偏移 L+1 .. 10
            else:
                cols = np.arange(1, 10)           # 偏移 2 .. 10
            cw = close_m[:, cols]
            b1 = cw > h0[:, None]
            vrb = vol_m[:, cols] / vma_m[:, cols]
            for ver, thr in E.BRK_VOL.items():
                cond = b1 if thr <= 0 else (b1 & (vrb >= thr))
                anyb = cond.any(axis=1)
                db = np.where(anyb, cols[0] + np.argmax(cond, axis=1) + 1, np.nan)
                ev[f'dB_{ver}_{tag}{Lx}'] = db
                ev[f'brvol_{ver}_{tag}{Lx}'] = np.where(
                    anyb, vrb[np.arange(n), np.argmax(cond, axis=1)], np.nan)

    # ---------- 收益 ----------
    print('[5/5] 计算收益 ...', flush=True)
    HS = sorted(set(E.HOR_S + E.HOR_B))
    mkt = pd.read_parquet(E.MKTPATH, columns=['td_idx', 'ew_ret', 'med_ret'])
    mkt = E.L.add_mkt_fwd(mkt, HS)
    mktf = {h: mkt[f'mkt_fwd_{h}'].values.astype(np.float64) for h in HS}
    mktmed = {h: mkt[f'mktmed_fwd_{h}'].values.astype(np.float64) for h in HS}
    td0 = ev['td_idx'].values

    def exc(td_at, h, med=False):
        src = mktmed[h] if med else mktf[h]
        t = np.clip(np.nan_to_num(td_at, nan=0).astype(np.int64), 0, len(src) - 1)
        return src[t]

    # S-entry
    for h in E.HOR_S:
        if h > W:
            continue
        r = close_m[:, h - 1] / c0 - 1.0
        ev[f'S_ret_{h}'] = r
        ev[f'S_exc_{h}'] = r - exc(td0, h)
        ev[f'S_excmed_{h}'] = r - exc(td0, h, med=True)
        ev[f'S_mfe_{h}'] = np.nanmax(high_m[:, :h], axis=1) / c0 - 1.0
        ev[f'S_mae_{h}'] = np.nanmin(low_m[:, :h], axis=1) / c0 - 1.0

    # B-entry（三种版本 × 两种窗口口径 × 三个 L）
    for ver in E.BRK_VOL:
        for tag in ('main', 'lit'):
            for Lx in E.L_GRID:
                db = ev[f'dB_{ver}_{tag}{Lx}'].values
                ok = np.isfinite(db)
                di = np.where(ok, db.astype(np.int64) - 1, 0)
                ar = np.arange(n)
                cb = np.where(ok, close_m[ar, np.clip(di, 0, W - 1)], np.nan)
                for h in E.HOR_B:
                    j = np.clip(di + h, 0, W - 1)
                    ev[f'B_ret_{h}_{ver}_{tag}{Lx}'] = np.where(ok, close_m[ar, j] / cb - 1.0, np.nan)
                    lo = di
                    hi = np.clip(di + h - 1, 0, W - 1)
                    mf = np.full(n, np.nan); ma = np.full(n, np.nan)
                    for i in np.flatnonzero(ok):
                        a, b = int(lo[i]), int(hi[i])
                        if b >= a:
                            mf[i] = np.nanmax(high_m[i, a:b + 1]) / cb[i] - 1.0
                            ma[i] = np.nanmin(low_m[i, a:b + 1]) / cb[i] - 1.0
                    ev[f'B_mfe_{h}_{ver}_{tag}{Lx}'] = mf
                    ev[f'B_mae_{h}_{ver}_{tag}{Lx}'] = ma
                    td_b = np.where(ok, td_m[ar, np.clip(di, 0, W - 1)], np.nan)
                    ev[f'B_exc_{h}_{ver}_{tag}{Lx}'] = ev[f'B_ret_{h}_{ver}_{tag}{Lx}'].values - exc(td_b, h)
                    ev[f'B_excmed_{h}_{ver}_{tag}{Lx}'] = ev[f'B_ret_{h}_{ver}_{tag}{Lx}'].values - exc(td_b, h, med=True)
                # 次日开盘入场 + 跳空
                ob = np.where(ok, open_m[ar, np.clip(di + 1, 0, W - 1)], np.nan)
                ev[f'B_gap_{ver}_{tag}{Lx}'] = ob / cb - 1.0
                for h in E.HOR_B:
                    j = np.clip(di + h, 0, W - 1)
                    ev[f'B_retO_{h}_{ver}_{tag}{Lx}'] = np.where(ok, close_m[ar, j] / ob - 1.0, np.nan)
                # 突破日是否涨停
                ev[f'B_limitup_{ver}_{tag}{Lx}'] = np.where(
                    ok, np.nan_to_num(M['up_limit'].astype(np.float64)[ar, np.clip(di, 0, W - 1)]), np.nan)

    # ---------- 市场状态 / 市值 / 年份 ----------
    ix = market_state()
    ev = ev.merge(ix, on='trade_date', how='left')
    ev['year'] = (ev['trade_date'] // 10000).astype(int)
    ev['segment'] = np.where(ev['trade_date'] > E.IS_END, 'OOS', 'IS')
    # 市值分组：主口径用 20 日均成交额的当日横截面分位（全样本可用）
    ev['amt20i'] = ev['amt20i'].values
    sz = np.full(n, np.nan)
    for td, g in ev.groupby('td_idx', sort=False):
        v = g['amt20i'].values.astype(np.float64)
        ok = np.isfinite(v)
        if ok.sum() < 50:
            continue
        q1, q2 = np.nanpercentile(v, [33.3, 66.7])
        sz[g.index.values] = np.where(~ok, np.nan, np.where(v <= q1, 0, np.where(v <= q2, 1, 2)))
    ev['size_grp'] = sz
    ev['size_lbl'] = pd.Categorical.from_codes(
        np.where(np.isnan(sz), -1, sz.astype(int)), categories=['Small', 'Mid', 'Large'])
    # 真实市值分组（仅 2023+ 可用，作为校验）
    mvq = np.full(n, np.nan)
    hav = np.isfinite(ev['total_mv'].values.astype(np.float64))
    if hav.sum() > 100:
        qs = np.nanpercentile(ev['total_mv'].values[hav], [33.3, 66.7])
        mvq[hav] = np.digitize(ev['total_mv'].values[hav], qs)
        ev['size_mv'] = mvq
    ev.to_parquet(os.path.join(E.DATA, 'events_e09.parquet'), index=False)

    # ---------- 事件簇（§21）----------
    ev = ev.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
    gap = np.full(len(ev), 999)
    prev = ev.groupby('ts_code')['td_idx'].shift(1)
    gap = (ev['td_idx'] - prev).fillna(999).values
    ev['gap_prev'] = gap
    ev['cluster_id'] = (gap > 10).cumsum()
    ev.to_parquet(os.path.join(E.DATA, 'events_e09.parquet'), index=False)

    print(f'\n事件集完成：{len(ev):,} 行 × {ev.shape[1]} 列')
    print(f'  IS {int((ev.segment=="IS").sum()):,}  OOS {int((ev.segment=="OOS").sum()):,}')
    print('  确认日命中率（main 口径，L=3）：')
    for ver in E.BRK_VOL:
        d = ev[f'dB_{ver}_main3'].values
        print(f'    E09-{ver}: {np.isfinite(d).mean():.3f}  中位 dB={np.nanmedian(d):.0f}')

    # ---------- 数据画像 ----------
    prof = dict(
        data_range=[dmin, dmax],
        panel_rows=int(len(p)),
        universe_rows=int(uni.sum()),
        total_stocks=int(p['ts_code'].nunique()),
        universe_stocks=int(p.loc[uni, 'ts_code'].nunique()),
        delisted_stocks=int(p.loc[p['is_delist'].fillna(0) == 1, 'ts_code'].nunique()),
        board_counts={k: int(v) for k, v in p.loc[uni, 'board'].value_counts().items()},
        n_events=int(len(ev)),
        n_is=int((ev.segment == 'IS').sum()),
        n_oos=int((ev.segment == 'OOS').sum()),
    )
    import json
    with open(os.path.join(E.OUT, '_profile.json'), 'w', encoding='utf-8') as f:
        json.dump(prof, f, ensure_ascii=False, indent=2)
    print('\nPROFILE:', prof, flush=True)


if __name__ == '__main__':
    main()
