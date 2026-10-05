# -*- coding: utf-8 -*-
"""
E09-V02 Step 3：匹配基准 + Null Model + Bootstrap CI + BH-FDR

产出：e09_v02_null_model.csv
"""
import os
import numpy as np
import pandas as pd
import sqlite3
import e09_lib as E

TAG = 'main'
NB = 2000          # Null bootstrap 次数（2000 × 数千事件，控制耗时）
NBCI = 5000        # 置信区间 bootstrap 次数


def build_pool():
    cols = ['ts_code', 'trade_date', 'td_idx', 'open', 'high', 'low', 'close',
            'pct_chg', 'vol', 'amount', 'ma20', 'vma20', 'vr20', 'board',
            'up_limit', 'is_st', 'obs_days', 'mom20']
    p = pd.read_parquet(E.PANEL, columns=['ts_code', 'trade_date', 'td_idx', 'close',
                                          'vol', 'amount', 'ma20', 'vma20', 'pct_chg',
                                          'is_st', 'obs_days'])
    p['_c'] = pd.factorize(p['ts_code'], sort=False)[0]
    E.add_roll(p, 'amount', 20, 'mean', 0, 20, 'amt20')
    g = p.groupby('_c', sort=False)
    mom20 = p['close'] / g['close'].shift(20) - 1.0
    p['mom20'] = mom20
    for h in (1, 3, 5, 10):
        p[f'r{h}'] = g['close'].shift(-h) / p['close'] - 1.0
    bad = ((p['vol'].values <= 0) | (p['close'].values <= 0) | ~np.isfinite(p['pct_chg'].values))
    uni = ((np.nan_to_num(p['is_st'].values) == 0) &
           (np.nan_to_num(p['obs_days'].values) >= E.MIN_OBS) &
           np.isfinite(p['ma20'].values) & np.isfinite(p['vma20'].values) &
           np.isfinite(mom20.values) & ~bad)
    pool = p[uni].reset_index(drop=True)
    pool['orig_row'] = np.flatnonzero(uni)
    pool['volr'] = pool['vol'] / pool['vma20']
    pool['year'] = (pool['trade_date'] // 10000).astype(int)
    # 市值分位（当日横截面，20日均成交额）
    sz = np.full(len(pool), np.nan)
    for td, idx in pool.groupby('td_idx').indices.items():
        v = pool['amt20'].values[idx]
        ok = np.isfinite(v)
        if ok.sum() < 50:
            continue
        q = np.nanpercentile(v, [33.3, 66.7])
        sz[idx] = np.where(~ok, np.nan, np.digitize(v, q))
    pool['size'] = sz
    return p, pool


def main():
    print('载入事件集 ...', flush=True)
    ev = pd.read_parquet(os.path.join(E.DATA, 'events_e09.parquet'))
    print('构建匹配池 ...', flush=True)
    p, pool = build_pool()
    print(f'  pool {len(pool):,} 行', flush=True)

    ix = pd.read_sql("select trade_date, close from index_daily_cache where ts_code='000001.SH' order by trade_date",
                     sqlite3.connect(r'D:\mystock\cache_daily\stock_data.db'))
    ix['ma20'] = ix['close'].rolling(20, min_periods=20).mean()
    ix['ma60'] = ix['close'].rolling(60, min_periods=60).mean()
    ix['regime'] = np.where((ix['close'] > ix['ma20']) & (ix['ma20'] > ix['ma60']), 'Bull',
                    np.where((ix['close'] < ix['ma20']) & (ix['ma20'] < ix['ma60']), 'Bear', 'Neutral'))
    rmap = dict(zip(ix['trade_date'].astype(np.int64), ix['regime']))
    pool['regime'] = pool['trade_date'].map(rmap).fillna('Neutral')

    # ---- 分层键 ----
    pool['mom_q'] = pd.qcut(pool['mom20'].rank(method='first'), 5, labels=False, duplicates='drop')
    pool['volr_q'] = pd.qcut(pool['volr'].rank(method='first'), 5, labels=False, duplicates='drop')
    pool['strata'] = (pool['year'].astype(str) + '|' + pool['regime'].astype(str) + '|' +
                      pool['mom_q'].fillna(-1).astype(int).astype(str) + '|' +
                      pool['volr_q'].fillna(-1).astype(int).astype(str) + '|' +
                      pool['size'].fillna(-1).astype(int).astype(str))
    pool = pool.sort_values('strata', kind='stable').reset_index(drop=True)
    sv = pool['strata'].values
    uq, st_ = np.unique(sv, return_index=True)
    en_ = np.concatenate([st_[1:], [len(sv)]])
    sidx = {c: i for i, c in enumerate(uq)}
    tdpool = pool['td_idx'].values
    tdu, stt_ = np.unique(tdpool, return_index=True)
    ent_ = np.concatenate([stt_[1:], [len(tdpool)]])
    rpool = {h: pool[f'r{h}'].values.astype(np.float64) for h in (1, 3, 5, 10)}
    print(f'  分层数 {len(uq):,}', flush=True)

    # ---- Null3：日期平移所需 ----
    cl = p['close'].values.astype(np.float64)
    code_i = p['_c'].values
    NP = len(p)
    RG = E.RNG

    def null3(entry_rows, h, nboot=NB):
        n = len(entry_rows)
        ec = code_i[entry_rows]
        base = cl[entry_rows]
        out = np.empty(nboot)
        for b in range(nboot):
            s = RG.integers(10, 61, size=n) * RG.choice([-1, 1], size=n)
            tgt = np.clip(entry_rows + s, 0, NP - 1)
            t2 = np.clip(tgt + h, 0, NP - 1)
            ok = (code_i[tgt] == ec) & (code_i[t2] == code_i[tgt])
            r = np.where(ok, cl[t2] / cl[tgt] - 1.0, np.nan)
            out[b] = np.nanmean(r)
        return out

    oos = (ev['segment'] == 'OOS').values

    def mask_of(v, L=3, contr=0.70, dd=0.05):
        c = ev[f'meanvr_{L}'].values <= contr
        s = ev[f'minlow_{L}'].values >= (1.0 - dd)
        return c, s, np.isfinite(ev[f'dB_{v}_{TAG}{L}'].values)

    # ---- 事件的分层键（与 pool 同口径）----
    ev['mom20e'] = ev['mom20'].values
    ev['volre'] = ev['vol_ma20r'].values
    ev['size_e'] = ev['size_grp'].values if 'size_grp' in ev.columns else np.nan
    qm = np.nanpercentile(pool['mom20'].values, [20, 40, 60, 80])
    qv = np.nanpercentile(pool['volr'].values, [20, 40, 60, 80])
    qs = np.nanpercentile(pool['amt20'].values, [33.3, 66.7])
    ev['mom_q'] = np.digitize(ev['mom20e'].values, qm)
    ev['volr_q'] = np.digitize(ev['volre'].values, qv)
    ev['size_q'] = np.digitize(ev['amt20i'].values, qs)
    ev['estrata'] = (ev['year'].astype(str) + '|' + ev['regime'].astype(str) + '|' +
                     ev['mom_q'].astype(str) + '|' + ev['volr_q'].astype(str) + '|' +
                     ev['size_q'].astype(str))

    rows = []
    STRATS = []
    c3, s3, b3 = mask_of('B')
    _, _, bA = mask_of('A')
    _, _, bC = mask_of('C')
    for v, bb in (('A', bA), ('B', b3), ('C', bC)):
        STRATS.append((f'E09_{v}_L3', oos & c3 & s3 & bb, 'B', v))
        STRATS.append((f'E09_{v}_L3', (~oos) & c3 & s3 & bb, 'B', v))
    STRATS.append(('S_contr_struct_L3', oos & c3 & s3, 'S', None))
    STRATS.append(('S_contr_struct_L3', (~oos) & c3 & s3, 'S', None))
    # Benchmark B：启动日但不具备完整 E09 结构
    notE = ~(c3 & s3 & b3)
    STRATS.append(('BMB_start_no_E09', oos & notE, 'S', None))
    STRATS.append(('BMB_start_no_E09', (~oos) & notE, 'S', None))

    for label, mk, kind, v in STRATS:
        if mk.sum() < 50:
            continue
        sub = ev[mk]
        seg = 'OOS' if (sub['segment'] == 'OOS').all() else ('IS' if (sub['segment'] == 'IS').all() else 'MIX')
        ekey = sub['estrata'].values
        ki = np.array([sidx.get(k, -1) for k in ekey])
        okm = ki >= 0
        sl = np.clip(ki, 0, len(st_) - 1)
        cnt = np.maximum(en_[sl] - st_[sl], 1)
        tds = sub['td_idx'].values
        slt = np.clip(np.searchsorted(tdu, tds), 0, len(stt_) - 1)
        cntt = np.maximum(ent_[slt] - stt_[slt], 1)
        # 入场行（panel 中的绝对行号）
        if kind == 'S':
            entry_rows = sub['row'].values.astype(np.int64)
        else:
            entry_rows = (sub['row'].values + np.nan_to_num(
                sub[f'dB_{v}_{TAG}3'].values, nan=0)).astype(np.int64)
        for h in (3, 5, 10):
            if kind == 'S':
                rc = f'S_ret_{h}'
            else:
                rc = f'B_ret_{h}_{v}_{TAG}3'
            real = sub[rc].values.astype(np.float64)
            real_m = float(np.nanmean(real))
            if not np.isfinite(real_m):
                continue
            # Null1：完全随机 股票×日期
            n1 = np.empty(NB)
            for b in range(NB):
                pick = RG.integers(0, len(pool), size=len(sub))
                n1[b] = np.nanmean(rpool[h][pick])
            # Null2：同日随机
            n2 = np.empty(NB)
            for b in range(NB):
                pick = stt_[slt] + (RG.random(len(sub)) * cntt).astype(np.int64)
                n2[b] = np.nanmean(rpool[h][np.clip(pick, 0, len(pool) - 1)])
            # Null2b：分层随机（年份×状态×动量×量比×市值）
            n3 = np.empty(NB)
            for b in range(NB):
                pick = st_[sl] + (RG.random(len(sub)) * cnt).astype(np.int64)
                n3[b] = np.nanmean(rpool[h][np.clip(pick, 0, len(pool) - 1)])
            # Null3：日期平移
            n4 = null3(entry_rows, h)
            c1, c2, c3m, c4 = map(float, (n1.mean(), n2.mean(), n3.mean(), n4.mean()))
            p1 = float((n1 >= real_m).mean())
            p2 = float((n2 >= real_m).mean())
            p3 = float((n3 >= real_m).mean())
            p4 = float((n4 >= real_m).mean())
            _, lo, hi = E.boot_ci(real, nboot=NBCI, cluster=sub['ts_code'].values)
            pc = E.cluster_boot_pvalue(real, nboot=1000, cluster=sub['ts_code'].values)
            rows.append(dict(strategy=label, segment=seg, horizon=h, n=int(np.isfinite(real).sum()),
                             mean=real_m, median=float(np.nanmedian(real)),
                             win=float(np.nanmean(real > 0)),
                             ci_lo=lo, ci_hi=hi, p_clusterboot=pc,
                             bm_random=c1, alpha_random=real_m - c1, p_random=p1,
                             bm_sameday=c2, alpha_sameday=real_m - c2, p_sameday=p2,
                             bm_matched=c3m, alpha_matched=real_m - c3m, p_matched=p3,
                             bm_dateshift=c4, alpha_dateshift=real_m - c4, p_dateshift=p4))
            print(f'  {label:22s} {seg} h={h:2d} n={int(np.isfinite(real).sum()):7d} '
                  f'mean={real_m:+.4f} α_matched={real_m - c3m:+.4f} p={p3:.3f}', flush=True)

    df = pd.DataFrame(rows)
    # BH-FDR（以 matched 对照为主检验）
    df['p_raw'] = df['p_matched']
    df['p_adj'] = E.bh_fdr(df['p_raw'].values)
    E.save(df, 'e09_v02_null_model.csv')
    print('\n=== 最终（按 matched α）===')
    print(df[['strategy', 'segment', 'horizon', 'n', 'mean', 'ci_lo', 'ci_hi',
              'bm_matched', 'alpha_matched', 'p_raw', 'p_adj',
              'bm_sameday', 'alpha_sameday', 'bm_dateshift', 'alpha_dateshift']].round(4).to_string(index=False))
    print('\nDONE', flush=True)


if __name__ == '__main__':
    main()
