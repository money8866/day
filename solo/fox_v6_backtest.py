# -*- coding: utf-8 -*-
"""猎狐 V6 全历史回放器：上升趋势中「缩量回踩不破位 → 尾盘重新走强」的趋势延续买点。

口径：
  1. 股池 = 全市场（沪深、剔 ST/退市/次新），与 fox_v4_backtest.py 一致。
  2. 形态 = fox_v6.build_features 的向量化判定；本脚本用「宽口径」扫描一次落盘，
     之后所有阈值/评分的网格标定都在 CSV 上离线重筛，无需重跑扫描。
  3. 收益 = 以信号日收盘为买入基准的前向收益（复权口径 close×adj_factor，免疫除权跳空）；
     同时给出相对上证指数(000001.SH)的超额。
  4. 止损=信号日最低，目标1/2 = 收盘×1.05 / ×1.10。

输出：report_daily/fox_v6_backtest_signals.csv + 控制台分档 + 参数网格标定
"""
import os
import sys
import time

sys.path.insert(0, r"D:\mystock\solo")
import numpy as np
import pandas as pd
from multiprocessing import Pool

from w7_second_wave_engine import CacheReader, market_index_series
import fox_v6

# 回放截止日动态取最新缓存日（由 main 里 reader.latest_date() 写入全局，经 worker initargs 传入）
LOAD_MIN_DATE = "20210101"
MIN_HIST = 60
WORKERS = 8
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "report_daily", "fox_v6_backtest_signals.csv")
HORIZONS = (1, 3, 5, 10, 20, 60)

# 宽口径扫描（只固定用户明确要求的涨幅下限 3%；其余放宽，靠离线重筛收敛）
LOOSE = {
    "signal_min": 3.0,
    "signal_max": 7.0,
    "gap_max": 4.0,
    "trend_win": 20,
    "trend_min": 15.0,
    "pullback_days": 20,
    "pullback_min": 3.0,
    "pullback_max": 25.0,
    "shrink_win": 5,
    "shrink_base": 20,
    "shrink_min": 0.0,
    "shrink_max": 1.2,
    "ma20_keep": 0.93,
    "close_pos_min": 0.5,
    "vol_win": 5,
    "volr_min": 1.0,
    "volr_max": 0.0,
    "vol20_base": 20,
    "vol20r_max": 0.0,
    "ma20_ratio_max": 0.0,
    "first_lookback": 10,
}

# 标定口径 V6.0（= 用户拍板 + fox_v6.DEFAULT_PARAMS 的其余默认硬条件）
SPEC = dict(LOOSE)
SPEC.update({"signal_max": 6.0, "gap_max": 1.5, "trend_min": 20.0, "trend_max": 25.0,
             "pullback_min": 5.0, "pullback_max": 15.0,
             "shrink_min": 0.4, "shrink_max": 0.9, "ma20_keep": 0.97,
             "close_pos_min": 0.6, "volr_min": 1.2, "volr_max": 3.0,
             "vol20r_max": 1.5, "ma20_ratio_max": 1.08})

_END = ""
_IDX = None


def _init_worker(end_db, idx_dates, idx_vals):
    global _END, _IDX
    _END = end_db
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
    reader.load_all(_END, codes=[c for c, _ in codes_names], min_date=LOAD_MIN_DATE,
                    chunk=500, verbose=False)
    idxmap = _idx_ret_map()
    samples = []
    for code, name in codes_names:
        df = reader.bars(code, _END)
        if df is None or len(df) < MIN_HIST:
            continue
        sig = fox_v6.scan_fox_v6(df, LOOSE)
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
                ir = idxmap[h].get(d, np.nan)
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
    """与 fox_v6.score_row 同口径的向量化评分（趋势20+回踩15+结构20+走强25+缩量10+位置10）"""
    p = fox_v6.merged(p)

    # 趋势强度
    r20 = pd.to_numeric(S.v6_ret20, errors="coerce")
    lo = float(p["trend_min"])
    hi = float(p.get("trend_max") or 0.0) or 25.0
    f_tr = np.where(r20 < lo, np.maximum(0.0, (r20 - 5.0) / max(lo - 5.0, 1e-6)),
                    np.where(r20 <= hi, 1.0, np.maximum(0.0, 1.0 - (r20 - hi) / 40.0)))

    # 回踩充分度：8% 最优
    f_pb = np.maximum(0.0, 1.0 - (pd.to_numeric(S.v6_pb, errors="coerce") - 8.0).abs() / 8.0)

    # 结构保持：low/MA20 ≥ 1.0 满分，ma20_keep→0
    lm = pd.to_numeric(S.v6_low_ma20, errors="coerce")
    keep = float(p["ma20_keep"])
    f_st = np.clip((lm - keep) / max(1.0 - keep, 1e-6), 0.0, 1.0)

    # 重新走强：收盘位置甜区 [0.7, 0.85]（0.7 以下未走强；0.85 以上追高退货）+ 量比温和（1.5~2.5 最优）
    cp = pd.to_numeric(S.v6_close_pos, errors="coerce")
    f_cp = np.where(cp < 0.7, np.clip(cp / 0.7, 0.0, 1.0),
                    np.where(cp <= 0.85, 1.0, np.maximum(0.0, 1.0 - (cp - 0.85) / 0.15)))
    vr = pd.to_numeric(S.v6_volr, errors="coerce")
    f_vr = np.where(vr < 1.5, np.maximum(0.0, (vr - 1.0) / 0.5),
                    np.where(vr <= 2.5, 1.0, np.maximum(0.0, 1.0 - (vr - 2.5) / 2.5)))

    # 缩量：甜区 [0.6, 0.8]
    sr = pd.to_numeric(S.v6_shrink, errors="coerce")
    sr_lo = max(float(p.get("shrink_min") or 0.0), 0.4)
    sr_max = float(p["shrink_max"])
    f_sr = np.where(sr < 0.6, np.maximum(0.0, (sr - sr_lo) / max(0.6 - sr_lo, 1e-6)),
                    np.where(sr <= 0.8, 1.0,
                             np.maximum(0.0, (sr_max - sr) / max(sr_max - 0.8, 1e-6))))

    # 位置：收盘/MA20 ≤1.02 满分
    mr = pd.to_numeric(S.v6_ma20_ratio, errors="coerce")
    f_pos = np.where(mr <= 0, 0.0,
                     np.where(mr <= 1.02, 1.0, np.maximum(0.0, 1.0 - (mr - 1.02) / 0.10)))

    z = pd.DataFrame({"a": f_tr, "b": f_pb, "c": f_st, "d": f_cp, "e": f_vr,
                      "f": f_sr, "g": f_pos}).fillna(0.0)
    return (z.a.clip(0, 1) * 20 + z.b.clip(0, 1) * 15 + z.c.clip(0, 1) * 20
            + (z.d.clip(0, 1) * 15 + z.e.clip(0, 1) * 10)
            + z.f.clip(0, 1) * 10 + z.g.clip(0, 1) * 10).round(1)


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
    q = q[q.v6_pct >= p["signal_min"]]
    q = q[q.v6_pct <= p["signal_max"]]
    q = q[q.v6_ret20 >= p["trend_min"]]
    if float(p.get("trend_max") or 0.0) > 0:
        q = q[q.v6_ret20 < p["trend_max"]]
    q = q[q.v6_pb >= p["pullback_min"]]
    q = q[q.v6_pb <= p["pullback_max"]]
    q = q[q.v6_shrink <= p["shrink_max"]]
    q = q[q.v6_low_ma20 >= p["ma20_keep"]]
    q = q[q.v6_close_pos >= p["close_pos_min"]]
    q = q[q.v6_volr >= p["volr_min"]]
    q = q[q.v6_gap <= p["gap_max"]]
    if float(p.get("shrink_min") or 0.0) > 0:
        q = q[q.v6_shrink >= p["shrink_min"]]
    if float(p.get("volr_max") or 0.0) > 0:
        q = q[q.v6_volr <= p["volr_max"]]
    if float(p.get("vol20r_max") or 0.0) > 0:
        q = q[q.v6_vol20r <= p["vol20r_max"]]
    if float(p.get("ma20_ratio_max") or 0.0) > 0:
        q = q[q.v6_ma20_ratio <= p["ma20_ratio_max"]]
    if p.get("first_lookback"):
        q = q[q.v6_prior_cnt <= 0]
    return q


def grid_search(S):
    print("\n" + "═" * 110)
    print("参数网格标定（每行 = 一组阈值；指标为信号日收盘买入的前向均值/胜率）")
    print("═" * 110)
    rows = []
    for trend_min in (15.0, 20.0, 25.0):
        for pullback_min in (3.0, 5.0):
            for pullback_max in (12.0, 15.0, 20.0):
                for signal_max in (6.0, 7.0):
                    for volr_min in (1.0, 1.2, 1.5):
                        for ma20_ratio_max in (1.05, 1.08, 0.0):
                            p = dict(SPEC)
                            p.update({"trend_min": trend_min, "pullback_min": pullback_min,
                                      "pullback_max": pullback_max, "signal_max": signal_max,
                                      "volr_min": volr_min, "ma20_ratio_max": ma20_ratio_max})
                            q = apply_params(S, p)
                            r = {"trend": trend_min, "pb_min": pullback_min, "pb_max": pullback_max,
                                 "sig_max": signal_max, "volr": volr_min,
                                 "mr_max": ma20_ratio_max, "n": len(q)}
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
    print(f"{'趋势':>5} {'回撤':>9} {'涨上限':>6} {'放量':>5} {'收盘/MA20':>9} {'n':>6} "
          f"{'f1':>7} {'f5':>8} {'f10':>8} {'f20':>8} {'f60':>8} {'胜20':>6}")
    for _, r in G.head(20).iterrows():
        print(f"{r.trend:>5.0f} {r.pb_min:>4.0f}~{r.pb_max:<5.0f} {r.sig_max:>6.1f} "
              f"{r.volr:>5.1f} {(r.mr_max if r.mr_max else '无'):>9} "
              f"{int(r.n):>6d} {_pct(r.f1):>7} {_pct(r.f5):>8} {_pct(r.f10):>8} "
              f"{_pct(r.f20):>8} {_pct(r.f60):>8} {r.w20 * 100:>5.0f}%")
    print("\n—— 尾部 5 组（对照）——")
    for _, r in G.tail(5).iterrows():
        print(f"{r.trend:>5.0f} {r.pb_min:>4.0f}~{r.pb_max:<5.0f} {r.sig_max:>6.1f} "
              f"{r.volr:>5.1f} {(r.mr_max if r.mr_max else '无'):>9} "
              f"{int(r.n):>6d} {_pct(r.f1):>7} {_pct(r.f5):>8} {_pct(r.f10):>8} "
              f"{_pct(r.f20):>8} {_pct(r.f60):>8} {r.w20 * 100:>5.0f}%")
    return G


def main():
    t0 = time.time()
    reader = CacheReader()
    end_db = reader.latest_date()
    if not end_db:
        print("缓存库无数据, 退出")
        return
    universe = reader.universe(end_db)
    stock_list = []
    for r in universe.to_dict("records"):
        name = str(r.get("name") or r["ts_code"])
        if "ST" in name.upper() or "退" in name:
            continue
        basic = reader.basic.loc[r["ts_code"]] if r["ts_code"] in reader.basic.index else {}
        list_date = str(basic.get("list_date", "")) if hasattr(basic, "get") else ""
        if list_date and list_date.isdigit() and int(list_date) > int(end_db) - 365:
            continue
        stock_list.append((str(r["ts_code"]), name))
    mdates, mvals = market_index_series(reader.conn, end_db, LOAD_MIN_DATE)
    reader.close()
    print(f"[foxv6] 股池={len(stock_list)} workers={WORKERS} 截止={end_db} 扫描口径={LOOSE}", flush=True)
    batches = [b for b in (stock_list[i::WORKERS] for i in range(WORKERS)) if b]
    with Pool(WORKERS, initializer=_init_worker, initargs=(end_db, mdates, mvals)) as pool:
        results = pool.map_async(scan_batch, batches).get()
    samples = [s for batch in results for s in batch]
    print(f"[foxv6] 回放完成 宽口径样本={len(samples)} 耗时={time.time() - t0:.0f}s", flush=True)
    S = pd.DataFrame(samples)
    if S.empty:
        print("无任何形态样本")
        return
    S = S.sort_values(["signal_date", "code"]).reset_index(drop=True)
    S["score"] = _score_vec(S, SPEC)
    S["tier"] = S["score"].map(lambda v: fox_v6.tier_of(v, SPEC))
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    S.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    print(f"[foxv6] 明细已保存 {CSV_PATH}")

    print("\n" + "═" * 110)
    print("一、标定口径 V6.1：前20日20%~25% · 回撤5~15% · 缩量0.4~0.9 · 低点≥0.97×MA20 · 收盘位≥0.6 · 3%~6%中阳 · 放量1.2~3.0× · 收盘/MA20≤1.08 · 第一根")
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
        ("趋势下限放宽到 15%", {"trend_min": 15.0}),
        ("趋势下限收紧到 25%", {"trend_min": 25.0}),
        ("回撤下限回到 3%", {"pullback_min": 3.0}),
        ("回撤上限收到 12%", {"pullback_max": 12.0}),
        ("回撤上限放到 20%", {"pullback_max": 20.0}),
        ("缩量上限收到 0.8", {"shrink_max": 0.8}),
        ("结构保持放宽到 0.95", {"ma20_keep": 0.95}),
        ("收盘位下限放到 0.5", {"close_pos_min": 0.5}),
        ("放量下限降到 1.0", {"volr_min": 1.0}),
        ("去掉「收盘/MA20≤1.08」", {"ma20_ratio_max": 0.0}),
        ("去掉「第一根」约束", {"first_lookback": 0}),
    ):
        p = dict(base)
        p.update(patch)
        block(apply_params(S, p), f"仅改：{label}")

    print("\n" + "═" * 110)
    print("三、特征分档（口径内样本）")
    print("═" * 110)
    for lo, hi in ((15, 20), (20, 25), (25, 35), (35, 60), (60, 999)):
        block(Q[(Q.v6_ret20 >= lo) & (Q.v6_ret20 < hi)], f"前20日涨幅∈[{lo},{hi})%")
    for lo, hi in ((5, 8), (8, 12), (12, 15)):
        block(Q[(Q.v6_pb >= lo) & (Q.v6_pb <= hi)], f"回撤∈[{lo},{hi}]%")
    for lo, hi in ((0.93, 0.97), (0.97, 1.0), (1.0, 1.1)):
        block(Q[(Q.v6_low_ma20 >= lo) & (Q.v6_low_ma20 < hi)], f"低点/MA20∈[{lo},{hi})")
    for lo, hi in ((0.5, 0.6), (0.6, 0.7), (0.7, 0.85), (0.85, 1.01)):
        block(Q[(Q.v6_close_pos >= lo) & (Q.v6_close_pos < hi)], f"收盘位∈[{lo},{hi})")
    for lo, hi in ((0.4, 0.6), (0.6, 0.8), (0.8, 0.9)):
        block(Q[(Q.v6_shrink >= lo) & (Q.v6_shrink < hi)], f"缩量比∈[{lo},{hi})")
    for lo, hi in ((1.2, 1.5), (1.5, 2.0), (2.0, 3.0)):
        block(Q[(Q.v6_volr >= lo) & (Q.v6_volr < hi)], f"量比∈[{lo},{hi})")
    for lo, hi in ((0, 1.0), (1.0, 1.02), (1.02, 1.05), (1.05, 1.08)):
        block(Q[(Q.v6_ma20_ratio >= lo) & (Q.v6_ma20_ratio < hi)], f"收盘/MA20∈[{lo},{hi})")
    for lo, hi in ((3.0, 4.0), (4.0, 5.0), (5.0, 6.01)):
        block(Q[(Q.v6_pct >= lo) & (Q.v6_pct < hi)], f"涨幅∈[{lo},{hi})%")
    for lo, hi in ((0, 45), (45, 55), (55, 65), (65, 75), (75, 101)):
        block(Q[(Q.score >= lo) & (Q.score < hi)], f"评分∈[{lo},{hi})")
    for t, g in Q.groupby("tier"):
        block(g, f"分档={t}")

    grid_search(S)
    print(f"\n[foxv6] 总耗时={time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()