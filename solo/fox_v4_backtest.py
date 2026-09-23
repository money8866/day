# -*- coding: utf-8 -*-
"""猎狐 V4 全历史回放器：缩量回调后的第一根放量中阳线（3%~5%）+ 长下影。

口径：
  1. 股池 = 全市场（沪深、剔 ST/退市/次新），与 fox_t0_backtest.py 一致。
  2. 形态 = fox_v4.build_features 的向量化判定；本脚本用「宽口径」扫描一次
     （signal_min 固定 3%，其余阈值放宽），把全部特征列落盘，
     之后所有阈值/评分的网格标定都在 CSV 上离线重筛，无需重跑扫描。
  3. 收益 = 以信号日收盘为买入基准的前向收益（复权口径 close×adj_factor，免疫除权跳空）；
     同时给出相对上证指数(000001.SH)的超额。
  4. 止损/目标仅作参考：止损=信号日最低，目标1/2 = 收盘×1.05 / ×1.10。

输出：report_daily/fox_v4_backtest_signals.csv + 控制台分档 + 参数网格标定
"""
import os
import sys
import time

sys.path.insert(0, r"D:\mystock\solo")
import numpy as np
import pandas as pd
from multiprocessing import Pool

from w7_second_wave_engine import CacheReader, market_index_series
import fox_v4

DATE_END = "20260917"
LOAD_MIN_DATE = "20210101"
MIN_HIST = 60
WORKERS = 8
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "report_daily", "fox_v4_backtest_signals.csv")
HORIZONS = (1, 3, 5, 10, 20, 60)

# 宽口径扫描（只固定用户明确要求的涨幅下限 3%；其余全部放宽，靠离线重筛收敛）
LOOSE = {
    "signal_min": 3.0,
    "signal_max": 6.0,
    "gap_max": 6.0,
    "pb_min": 0.0,
    "pb_max": 30.0,
    "shrink_win": 5,
    "shrink_base": 20,
    "shrink_min": 0.0,
    "shrink_max": 1.5,
    "vol_win": 5,
    "volr_min": 1.0,
    "volr_max": 0.0,
    "ma20_ratio_max": 0.0,
    "first_lookback": 10,
}

# 标定口径 V4.1（= fox_v4.DEFAULT_PARAMS 的硬条件部分，由本回放的分档/网格确定）
SPEC = dict(LOOSE)
SPEC.update({"signal_max": 5.0, "gap_max": 1.5, "pb_min": 5.0, "pb_max": 20.0,
             "shrink_min": 0.4, "shrink_max": 1.0,
             "volr_min": 1.5, "volr_max": 3.0, "ma20_ratio_max": 1.05})

_IDX = None


def _init_worker(idx_dates, idx_vals):
    global _IDX
    _IDX = (idx_dates, idx_vals)


def _idx_ret_map():
    dates, vals = _IDX
    s = pd.Series(vals, index=pd.Index(dates.astype(str)))
    out = {}
    for h in HORIZONS:
        out[h] = (s.shift(-h) / s - 1.0).to_dict()
    return out


def scan_batch(codes_names):
    reader = CacheReader()
    reader.load_all(DATE_END, codes=[c for c, _ in codes_names], min_date=LOAD_MIN_DATE,
                    chunk=500, verbose=False)
    idxmap = _idx_ret_map()
    samples = []
    for code, name in codes_names:
        df = reader.bars(code, DATE_END)
        if df is None or len(df) < MIN_HIST:
            continue
        sig = fox_v4.scan_fox_v4(df, LOOSE)
        if sig is None or sig.empty:
            continue
        close = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
        high = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype=float)
        low = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype=float)
        if "adj_factor" in df.columns:
            adjv = (pd.to_numeric(df["adj_factor"], errors="coerce").ffill().bfill()
                    .fillna(1.0).to_numpy(dtype=float))
        else:
            adjv = np.ones(len(df))
        adjc, adjh, adjl = close * adjv, high * adjv, low * adjv
        dates = df["trade_date"].astype(str).to_numpy()
        n = len(df)
        for rec in sig.to_dict("records"):
            j = int(rec["idx"])
            base = adjc[j]
            if not np.isfinite(base) or base <= 0:
                continue
            rec["code"] = code
            rec["name"] = name
            d = str(dates[j])
            for h in HORIZONS:
                k = j + h
                rec[f"fwd{h}"] = (adjc[k] / base - 1.0) if k < n else np.nan
                ir = (idxmap.get(h) or {}).get(d, np.nan)
                rec[f"ex{h}"] = rec[f"fwd{h}"] - ir if np.isfinite(ir) else np.nan
            for h, tag in ((20, "20"), (60, "60")):
                k = min(j + h, n - 1)
                seg_h = adjh[j + 1:k + 1]
                seg_l = adjl[j + 1:k + 1]
                rec[f"mfe{tag}"] = (seg_h.max() / base - 1.0) if len(seg_h) else np.nan
                rec[f"mae{tag}"] = (seg_l.min() / base - 1.0) if len(seg_l) else np.nan
            samples.append(rec)
    reader.close()
    return samples


def _pct(x):
    return f"{x * 100:+.2f}%" if x == x else "-"


def _score_vec(S, p):
    """与 fox_v4.score_row 同口径的向量化评分（放量30+下影25+回调20+缩量15+位置10）"""
    p = fox_v4.merged(p)
    v = pd.to_numeric(S.fox_volr, errors="coerce")
    v_lo = float(p["volr_min"])
    v_hi = min(float(p["volr_max"]) or 2.0, 2.0)
    v_cap = float(p["volr_max"]) or 5.0
    f_vol = np.where(v < v_lo, np.maximum(0.0, (v - 1.0) / max(v_lo - 1.0, 1e-6)),
                     np.where(v <= v_hi, 1.0,
                              np.maximum(0.0, 1.0 - (v - v_hi) / max(v_cap - v_hi, 1e-6))))
    # 下影：单调递增，1.5% 起满分
    sh = pd.to_numeric(S.fox_shadow, errors="coerce")
    f_sh = np.minimum(1.0, np.maximum(0.0, sh) / 1.5)
    # 回调：6.5% 最优
    f_pb = np.maximum(0.0, 1.0 - (pd.to_numeric(S.fox_pb, errors="coerce") - 6.5).abs() / 6.5)
    # 缩量：甜区 [0.6, 0.8]
    sr = pd.to_numeric(S.fox_shrink, errors="coerce")
    sr_lo = max(float(p.get("shrink_min") or 0.0), 0.4)
    sr_max = float(p["shrink_max"])
    f_sr = np.where(sr < 0.6, np.maximum(0.0, (sr - sr_lo) / max(0.6 - sr_lo, 1e-6)),
                    np.where(sr <= 0.8, 1.0,
                             np.maximum(0.0, (sr_max - sr) / max(sr_max - 0.8, 1e-6))))
    # 位置：收盘 ≤0.98×MA20 满分，越高越差
    mr = pd.to_numeric(S.fox_ma20_ratio, errors="coerce")
    f_pos = np.where(mr <= 0, 0.0,
                     np.where(mr <= 0.98, 1.0, np.maximum(0.0, 1.0 - (mr - 0.98) / 0.12)))
    z = pd.DataFrame({"a": f_vol, "b": f_sh, "c": f_pb, "d": f_sr, "e": f_pos}).fillna(0.0)
    return (z.a.clip(0, 1) * 30 + z.b.clip(0, 1) * 25 + z.c.clip(0, 1) * 20
            + z.d.clip(0, 1) * 15 + z.e.clip(0, 1) * 10).round(1)


def block(sub, label, keys=None):
    keys = keys or [f"fwd{h}" for h in HORIZONS]
    if sub is None or sub.empty:
        print(f"\n### {label}  样本=0")
        return
    print(f"\n### {label}  样本={len(sub)}")
    line = []
    for k in keys:
        d = pd.to_numeric(sub[k], errors="coerce").dropna()
        if d.empty:
            line.append(f"{k}=无")
            continue
        line.append(f"{k}: {_pct(d.mean())}/{(d > 0).mean() * 100:.0f}%")
    print("  " + "  ".join(line))
    for k in ("mfe20", "mae20"):
        if k in sub:
            d = pd.to_numeric(sub[k], errors="coerce").dropna()
            if len(d):
                print(f"  {k}: 均值={_pct(d.mean())} 中位={_pct(d.median())}")


def apply_params(S, p):
    """在落盘样本上离线重筛（列均已存盘，无需重跑扫描）"""
    q = S
    q = q[(q.fox_pct >= p["signal_min"]) & (q.fox_pct <= p["signal_max"])]
    q = q[(q.fox_pb >= p["pb_min"]) & (q.fox_pb <= p["pb_max"])]
    q = q[q.fox_shrink <= p["shrink_max"]]
    q = q[q.fox_volr >= p["volr_min"]]
    q = q[q.fox_gap <= p["gap_max"]]
    if float(p.get("shrink_min") or 0.0) > 0:
        q = q[q.fox_shrink >= p["shrink_min"]]
    if float(p.get("volr_max") or 0.0) > 0:
        q = q[q.fox_volr <= p["volr_max"]]
    if float(p.get("ma20_ratio_max") or 0.0) > 0:
        q = q[q.fox_ma20_ratio <= p["ma20_ratio_max"]]
    if p.get("first_lookback"):
        q = q[q.fox_prior_cnt <= 0]
    return q


def grid_search(S):
    print("\n" + "═" * 110)
    print("参数网格标定（每行 = 一组阈值；指标为信号日收盘买入的前向均值/胜率）")
    print("═" * 110)
    rows = []
    for pb_min in (3.0, 5.0):
        for pb_max in (15.0, 20.0):
            for shrink_max in (0.8, 1.0):
                for volr_min in (1.3, 1.5, 2.0):
                    for volr_max in (0.0, 3.0, 5.0):
                        for gap_max in (1.5, 3.0):
                            p = dict(SPEC)
                            p.update({"pb_min": pb_min, "pb_max": pb_max,
                                      "shrink_max": shrink_max, "volr_min": volr_min,
                                      "volr_max": volr_max, "gap_max": gap_max,
                                      "signal_max": 5.0})
                            q = apply_params(S, p)
                            r = {"pb_min": pb_min, "pb_max": pb_max, "shrink": shrink_max,
                                 "volr": volr_min, "vmax": volr_max, "gap": gap_max,
                                 "n": len(q)}
                            for h in (1, 5, 10, 20, 60):
                                d = pd.to_numeric(q.get(f"fwd{h}"), errors="coerce").dropna()
                                r[f"f{h}"] = d.mean() if len(d) else np.nan
                                r[f"w{h}"] = (d > 0).mean() if len(d) else np.nan
                            rows.append(r)
    G = pd.DataFrame(rows)
    if G.empty:
        print("无组合")
        return G
    G = G.sort_values("f20", ascending=False)
    print(f"{'pb':>10} {'缩量':>5} {'放量':>9} {'跳空':>5} {'n':>6} "
          f"{'f1':>7} {'f5':>8} {'f10':>8} {'f20':>8} {'f60':>8} {'胜20':>6}")
    for _, r in G.head(20).iterrows():
        print(f"{r.pb_min:>4.0f}~{r.pb_max:<5.0f} {r.shrink:>5.2f} "
              f"{r.volr:>4.1f}~{(r.vmax if r.vmax else 99):<4.0f} {r.gap:>5.1f} "
              f"{int(r.n):>6d} {_pct(r.f1):>7} {_pct(r.f5):>8} {_pct(r.f10):>8} "
              f"{_pct(r.f20):>8} {_pct(r.f60):>8} {r.w20 * 100:>5.0f}%")
    print("\n—— 尾部 5 组（对照）——")
    for _, r in G.tail(5).iterrows():
        print(f"{r.pb_min:>4.0f}~{r.pb_max:<5.0f} {r.shrink:>5.2f} "
              f"{r.volr:>4.1f}~{(r.vmax if r.vmax else 99):<4.0f} {r.gap:>5.1f} "
              f"{int(r.n):>6d} {_pct(r.f1):>7} {_pct(r.f5):>8} {_pct(r.f10):>8} "
              f"{_pct(r.f20):>8} {_pct(r.f60):>8} {r.w20 * 100:>5.0f}%")
    return G


def main():
    t0 = time.time()
    reader = CacheReader()
    universe = reader.universe(DATE_END)
    stock_list = []
    for r in universe.to_dict("records"):
        name = str(r.get("name") or r["ts_code"])
        if "ST" in name.upper() or "退" in name:
            continue
        basic = reader.basic.loc[r["ts_code"]] if r["ts_code"] in reader.basic.index else {}
        list_date = str(basic.get("list_date", "")) if hasattr(basic, "get") else ""
        if list_date and list_date.isdigit() and int(list_date) > int(DATE_END) - 365:
            continue
        stock_list.append((str(r["ts_code"]), name))
    mdates, mvals = market_index_series(reader.conn, DATE_END, LOAD_MIN_DATE)
    reader.close()
    print(f"[foxv4] 股池={len(stock_list)} workers={WORKERS} 扫描口径={LOOSE}", flush=True)
    batches = [b for b in (stock_list[i::WORKERS] for i in range(WORKERS)) if b]
    with Pool(WORKERS, initializer=_init_worker, initargs=(mdates, mvals)) as pool:
        results = pool.map_async(scan_batch, batches).get()
    samples = [s for batch in results for s in batch]
    print(f"[foxv4] 回放完成 宽口径样本={len(samples)} 耗时={time.time() - t0:.0f}s", flush=True)
    S = pd.DataFrame(samples)
    if S.empty:
        print("无任何形态样本")
        return
    S = S.sort_values(["signal_date", "code"]).reset_index(drop=True)
    # 评分按「用户口径」重算（扫描期用的是宽口径 shrink_max，仅影响缩量子项 15 分）
    S["score"] = _score_vec(S, SPEC)
    S["tier"] = S["score"].map(lambda v: fox_v4.tier_of(v, SPEC))
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    S.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    print(f"[foxv4] 明细已保存 {CSV_PATH}")

    print("\n" + "═" * 110)
    print("一、标定口径 V4.1：3%~5% 中阳 · 回调5~20% · 缩量0.4~1.0 · 放量1.5~3.0× · 跳空≤1.5% · 收盘/MA20≤1.05 · 第一根")
    print("═" * 110)
    Q = apply_params(S, SPEC)
    block(Q, "口径内全样本")
    Q = Q.copy()
    Q["year"] = Q.signal_date.str[:4]
    for y, g in Q.groupby("year"):
        block(g, f"年度={y}")

    print("\n" + "═" * 110)
    print("二、放宽/收紧单一条件看边际（基准=口径内样本）")
    print("═" * 110)
    base = dict(SPEC)
    for label, patch in (
        ("涨幅上限放到 6%", {"signal_max": 6.0}),
        ("回调下限回到 3%", {"pb_min": 3.0}),
        ("回调上限收到 15%", {"pb_max": 15.0}),
        ("缩量上限收到 0.8", {"shrink_max": 0.8}),
        ("去掉「过缩<0.4」约束", {"shrink_min": 0.0}),
        ("放量下限降到 1.2", {"volr_min": 1.2}),
        ("去掉「爆量>3×」约束", {"volr_max": 0.0}),
        ("跳空上限放到 3%", {"gap_max": 3.0}),
        ("去掉「收盘/MA20≤1.05」", {"ma20_ratio_max": 0.0}),
        ("去掉「第一根」约束", {"first_lookback": 0}),
    ):
        p = dict(base)
        p.update(patch)
        block(apply_params(S, p), f"仅改：{label}")

    print("\n" + "═" * 110)
    print("三、特征分档（口径内样本）")
    print("═" * 110)
    for lo, hi in ((1.5, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 99)):
        block(Q[(Q.fox_volr >= lo) & (Q.fox_volr < hi)], f"放量倍数∈[{lo},{hi})")
    for lo, hi in ((0, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)):
        block(Q[(Q.fox_shrink >= lo) & (Q.fox_shrink < hi)], f"缩量比∈[{lo},{hi})")
    for lo, hi in ((5, 8), (8, 12), (12, 16), (16, 20)):
        block(Q[(Q.fox_pb >= lo) & (Q.fox_pb <= hi)], f"回调幅度∈[{lo},{hi}]%")
    for lo, hi in ((0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 99)):
        block(Q[(Q.fox_shadow >= lo) & (Q.fox_shadow < hi)], f"下影线∈[{lo},{hi})%")
    for lo, hi in ((0, 0.5), (0.5, 1.0), (1.0, 1.5)):
        block(Q[(Q.fox_gap >= lo) & (Q.fox_gap < hi)], f"开盘跳空∈[{lo},{hi})%")
    for lo, hi in ((0, 0.95), (0.95, 1.0), (1.0, 1.03), (1.03, 1.05)):
        block(Q[(Q.fox_ma20_ratio >= lo) & (Q.fox_ma20_ratio < hi)],
              f"收盘/MA20∈[{lo},{hi})")
    for lo, hi in ((0, 3.5), (3.5, 4.0), (4.0, 4.5), (4.5, 5.01)):
        block(Q[(Q.fox_pct >= lo) & (Q.fox_pct < hi)], f"涨幅∈[{lo},{hi})%")
    for lo, hi in ((0, 45), (45, 55), (55, 65), (65, 75), (75, 101)):
        block(Q[(Q.score >= lo) & (Q.score < hi)], f"评分∈[{lo},{hi})")
    for t, g in Q.groupby("tier"):
        block(g, f"分档={t}")

    grid_search(S)
    print(f"\n[foxv4] 总耗时={time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
