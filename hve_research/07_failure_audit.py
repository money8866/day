# -*- coding: utf-8 -*-
"""
HVE-Research V1  Step 7
失败分析 / 涨停不可成交 / 可实施 Entry 策略级对比 / Look-ahead 审计
"""
import os
import sqlite3
import numpy as np
import pandas as pd
import hve_lib as L

OUT = r'D:\mystock\hve_research\out'
DATA = L.DATA
DB = r'D:\mystock\cache_daily\stock_data.db'
OUT_H = [5, 10, 20]
RNG = np.random.default_rng(L.SEED + 7)


# ---------------- 失败分析 ----------------
def failure():
    ev = pd.read_parquet(os.path.join(DATA, 'events_w7.parquet'))
    t = ev['trend'].values.astype(bool)
    s = ev['struct'].values.astype(bool)
    c = ev['contr'].values.astype(bool)
    br = ev['d_br20'].values > 0
    mask = t & s & c & br
    sub = ev[mask].copy()
    sub['ret'] = sub['br_ret_10'].values
    ok = sub['ret'].notna()
    sub = sub[ok]
    q = sub['ret'].rank(pct=True)
    sub['bucket'] = np.where(q >= 0.9, 'TOP10', np.where(q <= 0.1, 'BOT10', 'MID'))
    feats = ['pct_chg', 'vr20', 'amt_pct250', 'ret20', 'ret60', 'dist_hh60',
             'dist_ma20', 'dist_ma60', 'minvr10', 'mae10', 'mfe10', 'struct_min',
             'd_br20', 'total_mv', 'turnover_rate', 'vol20', 'clv']
    rows = []
    for f in feats:
        if f not in sub.columns:
            continue
        for b in ['BOT10', 'MID', 'TOP10']:
            v = sub.loc[sub['bucket'] == b, f].values
            v = v[np.isfinite(v)]
            rows.append(dict(feature=f, bucket=b, n=len(v), mean=float(np.nanmean(v)),
                             median=float(np.nanmedian(v))))
    # 突破日特征（用 Scan 未保留，改为 d_br20 与 br_ 收益代理）
    rows.append(dict(feature='br_ret_10', bucket='ALL', n=len(sub),
                     mean=float(sub['ret'].mean()), median=float(sub['ret'].median())))
    # 涨停不可成交比例：突破次日开盘无法买入的代理 -> 事件日是否涨停
    if 'up_limit' in sub.columns:
        frac = float(np.nanmean(sub['up_limit'].values > 0))
        rows.append(dict(feature='HVE_day_limitup_frac', bucket='ALL', n=len(sub),
                         mean=frac, median=np.nan))
    fb = pd.DataFrame(rows)
    fb.to_csv(os.path.join(OUT, '10_failure_analysis.csv'), index=False, encoding='utf-8-sig')
    print('\n=== 失败分析（S4 W7 突破，T+10 收益十分位分组） ===')
    piv = fb.pivot_table(index='feature', columns='bucket', values='mean')
    print(piv.round(4).to_string())
    return fb


# ---------------- 涨停不可成交 / 可实施性 ----------------
def tradability():
    """突破日收盘买入的可实施性：涨停封板日无法以收盘价成交"""
    import importlib
    p = pd.read_parquet(os.path.join(DATA, 'panel.parquet'),
                        columns=['ts_code', 'close', 'high', 'up_limit', 'td_idx'])
    ev = pd.read_parquet(os.path.join(DATA, 'events_w7.parquet'))
    t = ev['trend'].values.astype(bool)
    s = ev['struct'].values.astype(bool)
    c = ev['contr'].values.astype(bool)
    br = ev['d_br20'].values > 0
    m = t & s & c & br
    rows = ev['row'].values.astype(np.int64) + np.clip(ev['d_br20'].values, 0, 45)
    rows = np.clip(rows, 0, len(p) - 1)
    is_lim = p['up_limit'].values[rows] > 0
    sub = ev[m]
    lim = is_lim[m]
    out = []
    for name, sel in (('ALL', np.ones(len(sub), bool)), ('non_limitup', ~lim),
                      ('limitup', lim)):
        for h in OUT_H:
            r = sub[f'br_ret_{h}'].values[sel]
            st = L.stat(r, 0.0)
            out.append(dict(group=name, horizon=h, n=st['n'], mean=st['mean'],
                            median=st['median'], pf=st['pf'], win=st['win'],
                            frac=float(sel.mean())))
    td = pd.DataFrame(out)
    td.to_csv(os.path.join(OUT, '10b_tradability.csv'), index=False, encoding='utf-8-sig')
    print('\n=== 突破日涨停可实施性 ===')
    print(td.round(4).to_string(index=False))
    return td


# ---------------- 策略级 Entry 对比（无未来函数） ----------------
def entry_strategy_level():
    """
    P_EARLY  事件后 T+10 固定入场（trend+struct+contr），不知道后续是否突破
    P_WAIT   等 45 日窗口内首次突破日收盘入场；未突破则该事件不交易（收益记 0）
    P_HVE    事件日收盘即入场（不做路径筛选）
    统一以「事件」为单位比较，避免 per-trade 样本不可比
    """
    ev = pd.read_parquet(os.path.join(DATA, 'events_w7.parquet'))
    t = ev['trend'].values.astype(bool)
    s = ev['struct'].values.astype(bool)
    c = ev['contr'].values.astype(bool)
    br = ev['d_br20'].values > 0
    base = t & s & c
    rows = []
    for seg, sm in (('IS', ev['trade_date'].values <= 20230630),
                    ('OOS', ev['trade_date'].values > 20230630),
                    ('ALL', np.ones(len(ev), bool))):
        for h in OUT_H:
            for name, arr in (
                ('P_HVE_day', np.where(sm, ev[f'hve_ret_{h}'].values, np.nan)),
                ('P_EARLY_T+10_all', np.where(sm & base, ev[f'f_ret_{h}'].values, np.nan)),
                ('P_EARLY_T+10_brkOnly', np.where(sm & base & br, ev[f'f_ret_{h}'].values, np.nan)),
                ('P_WAIT_breakout_else0', np.where(sm & base,
                                                   np.where(br, np.nan_to_num(ev[f'br_ret_{h}'].values), 0.0),
                                                   np.nan)),
                ('P_WAIT_breakout_only', np.where(sm & base & br, ev[f'br_ret_{h}'].values, np.nan)),
            ):
                st = L.stat(arr, 0.0)
                rows.append(dict(entry=name, segment=seg, horizon=h, n=st['n'],
                                 mean=st['mean'], median=st['median'], pf=st['pf'],
                                 win=st['win'], t=st['t']))
    es = pd.DataFrame(rows)
    es.to_csv(os.path.join(OUT, '05b_entry_strategy_level.csv'), index=False,
              encoding='utf-8-sig')
    print('\n=== 策略级 Entry 对比（无未来函数，事件为单位） ===')
    print(es[es['horizon'] == 10].round(4).to_string(index=False))
    return es


# ---------------- Look-ahead 审计 ----------------
def audit_recompute(n=400):
    """从 panel 原始 OHLCV 重算关键特征，只用 <= 事件行的数据"""
    p = pd.read_parquet(os.path.join(DATA, 'panel.parquet'))
    ev = pd.read_parquet(os.path.join(DATA, 'events_w7.parquet'))
    codes, _ = pd.factorize(p['ts_code'], sort=False)
    chg = np.flatnonzero(np.diff(codes)) + 1
    starts = np.concatenate([[0], chg]); ends = np.concatenate([chg, [len(p)]])
    blk = np.zeros(len(p), dtype=np.int64)
    for i, (a, b) in enumerate(zip(starts, ends)):
        blk[a:b] = i
    rows = ev['row'].values.astype(np.int64)
    sel = RNG.choice(len(rows), size=min(n, len(rows)), replace=False)
    close = p['close'].values; high = p['high'].values; vol = p['vol'].values
    ma20v = p['ma20'].values; hh20v = p['hh20'].values; vr20v = p['vr20'].values
    dist60v = p['dist_hh60'].values; slopev = p['ma20_slope5'].values
    vp250 = p['vol_pct250'].values
    res = []
    for i in sel:
        r = rows[i]
        a = starts[blk[r]]
        if r - a < 65:
            continue
        d = {}
        # 注意：panel 的 ma20 为 rmean_incl（含当日），审计口径须一致
        d['ma20'] = (ma20v[r], np.nanmean(close[r - 19:r + 1]))
        d['hh20'] = (hh20v[r], np.nanmax(high[r - 20:r]))
        d['vr20'] = (vr20v[r], vol[r] / np.nanmean(vol[r - 20:r]))
        d['dist_hh60'] = (dist60v[r], close[r] / np.nanmax(high[r - 60:r]) - 1)
        d['ma20_slope5'] = (slopev[r], np.nanmean(close[r - 19:r + 1]) / np.nanmean(close[r - 24:r - 4]) - 1)
        win = vol[max(a, r - 249):r + 1]
        d['vol_pct250'] = (vp250[r], float(np.mean(win <= vol[r])))
        for k, (a1, b1) in d.items():
            res.append(dict(feature=k, panel=a1, recomputed=b1,
                            diff=abs(a1 - b1) if np.isfinite(a1) and np.isfinite(b1) else np.nan))
    ar = pd.DataFrame(res)
    s = ar.groupby('feature')['diff'].agg(['count', 'max', 'mean'])
    print('\n=== Look-ahead 审计 A：特征重算一致性（panel vs 仅用历史数据重算） ===')
    print(s.round(8).to_string())
    return ar, s


def audit_raw_db(n=30):
    """从 SQLite 原始表抽查：panel 的 close/vol 与数据库一致，且事件行可用历史数据重算"""
    p = pd.read_parquet(os.path.join(DATA, 'panel.parquet'),
                        columns=['ts_code', 'trade_date', 'close_raw', 'vol', 'vr20'])
    # panel 的 ma20 建立在「后复权」价格上；与原始库对比须用同一口径 -> 用 close_raw 重算
    ev = pd.read_parquet(os.path.join(DATA, 'events_w7.parquet'))
    sel = RNG.choice(len(ev), size=min(n, len(ev)), replace=False)
    con = sqlite3.connect(DB)
    diffs = []
    for i in sel:
        code = ev['ts_code'].values[i]
        dt = int(ev['trade_date'].values[i])
        q = f"SELECT trade_date, close, vol FROM daily_cache WHERE ts_code='{code}' AND trade_date<={dt} ORDER BY trade_date DESC LIMIT 25"
        df = pd.read_sql(q, con)
        if len(df) < 25:
            continue
        close = df['close'].values[::-1].astype(float)
        vol = df['vol'].values[::-1].astype(float)
        ma20_raw = np.nanmean(close[-20:])            # 含当日，未复权口径
        vr20 = vol[-1] / np.nanmean(vol[-21:-1])
        row = int(ev['row'].values[i])
        diffs.append(dict(ts_code=code, trade_date=dt,
                          panel_close=p['close_raw'].values[row], db_close=close[-1],
                          panel_ma20raw=np.nanmean(p['close_raw'].values[row - 19:row + 1]),
                          db_ma20=ma20_raw,
                          panel_vr20=p['vr20'].values[row], db_vr20=vr20))
    con.close()
    dd = pd.DataFrame(diffs)
    if len(dd):
        dd['d_close'] = (dd['panel_close'] - dd['db_close']).abs()
        dd['d_ma20raw'] = (dd['panel_ma20raw'] / dd['db_ma20'] - 1).abs()
        dd['d_vr20'] = (dd['panel_vr20'] / dd['db_vr20'] - 1).abs()
        print('\n=== Look-ahead 审计 B：原始数据库抽查 ===')
        print(dd[['d_close', 'd_ma20raw', 'd_vr20']].describe().round(6).to_string())
        dd.to_csv(os.path.join(OUT, '10c_audit_rawdb.csv'), index=False, encoding='utf-8-sig')
    return dd


def audit_latent_entry():
    """审计 E_lat（突破前潜伏）是否含未来函数：d_br 在入场时尚未可知"""
    ev = pd.read_parquet(os.path.join(DATA, 'events_w7.parquet'))
    t = ev['trend'].values.astype(bool)
    s = ev['struct'].values.astype(bool)
    c = ev['contr'].values.astype(bool)
    br = ev['d_br20'].values > 0
    m = t & s & c & br
    print('\n=== Look-ahead 审计 C：突破前潜伏 Entry ===')
    for k in (1, 3, 5):
        sub = ev[m]
        r = sub[f'lat{k}_ret_10'].values
        st = L.stat(r, 0.0)
        print(f'  E_lat{k}（入场日 = 突破日 - {k}） n={st["n"]} mean={st["mean"]:.4f} '
              f'pf={st["pf"]:.2f} win={st["win"]:.3f}  -> 入场时 d_br 未知，含未来函数，不可交易')
    return None


if __name__ == '__main__':
    failure()
    tradability()
    entry_strategy_level()
    audit_recompute(400)
    audit_raw_db(30)
    audit_latent_entry()
