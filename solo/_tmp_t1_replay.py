import os
import sqlite3
import sys
import time
from multiprocessing import Pool

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _daily_double_scan as S
from w7_second_wave_engine import CacheReader


def _init_pool(sub, name):
    S._SUB.clear()
    S._SUB.update(sub)
    S._NAME.clear()
    S._NAME.update(name)


def _replay_worker(job):
    chunk, days, latest = job
    reader = CacheReader()
    reader.load_all(latest, codes=chunk, min_date="20230101")
    out = []
    for code in chunk:
        df = reader.frames.get(code)
        if df is None or df.empty:
            continue
        arr = df.trade_date.astype(str).to_numpy()
        closes = pd.to_numeric(df.close, errors="coerce").to_numpy(float)
        highs = pd.to_numeric(df.high, errors="coerce").to_numpy(float)
        n = len(arr)
        for d in days:
            k = int(np.searchsorted(arr, d, side="right"))
            if k == 0 or arr[k - 1] != d:
                continue
            rec = S._last_event_rec(code, df.iloc[:k], d)
            if rec:
                c0 = closes[k - 1]
                if k < n and np.isfinite(c0) and c0 > 0:
                    ha = highs[k:]
                    ha = ha[np.isfinite(ha)]
                    rec["fwd_max"] = round(float(ha.max() / c0 - 1) * 100, 1) if len(ha) else np.nan
                    rec["fwd_now"] = round(float(closes[-1] / c0 - 1) * 100, 1) if np.isfinite(closes[-1]) else np.nan
                    rec["fwd_days"] = n - k
                else:
                    rec["fwd_max"] = rec["fwd_now"] = rec["fwd_days"] = np.nan
                out.append(rec)
    reader.close()
    return out


def main():
    probe = CacheReader()
    latest = str(probe.conn.execute("SELECT MAX(trade_date) FROM daily_cache").fetchone()[0])
    days = [str(r[0]) for r in probe.conn.execute(
        "SELECT DISTINCT trade_date FROM daily_cache WHERE trade_date > 20260611 AND trade_date <= ? ORDER BY trade_date",
        (latest,)).fetchall()]
    probe.close()
    print(f"回放窗口: {days[0]} ~ {days[-1]} 共{len(days)}个交易日 (事件库缺口回放)", flush=True)

    panel = S.get_subsector_top5()
    sub = panel[["ts_code", "subsector", "name"]].drop_duplicates("ts_code")
    sub["ts_code"] = sub["ts_code"].astype(str).str.strip()
    submap = dict(zip(sub.ts_code, sub.subsector))
    namemap = dict(zip(sub.ts_code, sub.name))
    try:
        con = sqlite3.connect(r"D:\mystock\cache_daily\stock_data.db")
        for (t,) in con.execute("select name from sqlite_master where type='table'").fetchall():
            cols = [c[1].lower() for c in con.execute(f"pragma table_info({t})").fetchall()]
            cc = next((c for c in cols if c in ("code", "ts_code", "stock_code", "symbol")), None)
            nc = next((c for c in cols if c in ("name", "stock_name", "sec_name")), None)
            if cc and nc:
                for a, b in con.execute(f"select {cc}, {nc} from {t}").fetchall():
                    s = str(a).strip()
                    if not b:
                        continue
                    k = s.zfill(6) if s.isdigit() else s
                    namemap[k] = b
                    if len(k) == 6 and k.isdigit():
                        namemap[k + (".SH" if k.startswith("6") else ".SZ")] = b
                break
        con.close()
    except Exception as e:
        print("name lookup failed:", e)
    codes = sorted(submap.keys())
    print(f"股票池={len(codes)}", flush=True)

    nw = 8
    chunks = [list(c) for c in np.array_split(np.asarray(codes, dtype=object), nw) if len(c)]
    t0 = time.time()
    with Pool(nw, initializer=_init_pool, initargs=(submap, namemap)) as pool:
        batches = pool.map(_replay_worker, [(c, days, latest) for c in chunks])
    recs = [r for b in batches for r in b]
    print(f"事件总数={len(recs)} 耗时={time.time()-t0:.0f}s", flush=True)

    df = pd.DataFrame(recs)
    df["tier"] = df.apply(S.tier, axis=1)
    df["t1_old"] = ((df.mv_yi <= 30) & (df.turn_today >= 8) & (df.rel_hi250 < -0.10)
                    & (df.ma60_x < 0.10) & (df.ma20_x < 0.15))

    d0 = df[df.date == latest]
    print(f"[校验] {latest}: 事件={len(d0)} TIER1={int((d0.tier=='TIER1_核心').sum())} TIER2={int((d0.tier=='TIER2_基础').sum())}  ←应与今日扫描 26/0/9 一致")
    print()

    df["ym"] = df.date.astype(str).str[:6]
    print("=== 窗口内月度信号数 ===")
    pv = df.groupby("ym").agg(天量事件=("code", "size"), TIER1新=("tier", lambda s: int((s == "TIER1_核心").sum())),
                              TIER2=("tier", lambda s: int((s == "TIER2_基础").sum())), TIER1旧口径=("t1_old", "sum"))
    print(pv.to_string())
    print()

    t1 = df[df.tier == "TIER1_核心"].sort_values("date")
    print(f"=== 新口径 TIER1 信号明细（共{len(t1)}个）===")
    if len(t1):
        print(f"{'日期':<10}{'代码':<10}{'名称':<8}{'行业':<10}{'流通亿':>6}{'换手%':>7}{'回撤%':>7}{'MA60乖%':>8}{'当日%':>7}{'簇首':>4}{'至今最大涨幅%':>12}{'现价涨幅%':>10}")
        for _, r in t1.iterrows():
            fm = "-" if pd.isna(r.fwd_max) or r.fwd_days == 0 else f"{r.fwd_max:+.1f}"
            fn = "-" if pd.isna(r.fwd_now) or r.fwd_days == 0 else f"{r.fwd_now:+.1f}"
            print(f"{r.date:<10}{r.code:<10}{r['name']:<8}{r.subsector:<10}{r.mv_yi:>6.1f}{r.turn_today:>7.1f}"
                  f"{r.rel_hi250*100:>7.0f}{r.ma60_x*100:>8.1f}{r.pct:>7.1f}{'是' if r.first_of_cluster else '-':>4}{fm:>12}{fn:>10}")
    print()

    t1o = df[df.t1_old & (df.tier != "TIER1_核心")].sort_values("date")
    print(f"=== 旧口径TIER1中新口径剔除的（回撤在-25%~-10%带内，共{len(t1o)}个）===")
    for _, r in t1o.iterrows():
        print(f"  {r.date} {r.code} {r['name']:<6} 回撤{r.rel_hi250*100:.0f}% 换手{r.turn_today:.0f}% 至今最大涨幅"
              f"{'-' if pd.isna(r.fwd_max) or r.fwd_days==0 else f'{r.fwd_max:+.0f}%'}")

    t2 = df[df.tier == "TIER2_基础"]
    if len(t2):
        print()
        print("=== TIER2 月度分布 ===")
        print(t2.groupby(t2.date.astype(str).str[:6]).size().to_string())


if __name__ == "__main__":
    main()
