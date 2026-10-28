# -*- coding: utf-8 -*-
"""W7 两类统一信号 —— 轻量标定（复用已缓存的原语，不重扫全市场）。

输入：_tmp_w7_feats.npy（_tmp_w7_grid.py 产出的判定原语缓存，含 T0 锚/回踩段/当日 K 线等）
输出：阈值网格下的 T+1/3/5/10 均值、超额（个股−全市场等权）、胜率、中位数、分年/分市场环境。

前向收益与全市场等权基准从 daily_cache 现算（向量化），不重跑极端量能扫描。
用法：python _tmp_w7_grid2.py --mode grid
"""
import argparse
import os
import sqlite3
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import w7_second_wave_engine as w7

CACHE = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB = os.path.join(CACHE, "stock_data.db")
HERE = os.path.dirname(os.path.abspath(__file__))
FWD = (1, 3, 5, 10)


def load_feats():
    p = os.path.join(HERE, "_tmp_w7_feats.npy")
    a = np.load(p, allow_pickle=True)
    return list(a)


def attach_fwd_and_bench(feats):
    """补前向收益 + 全市场等权基准（向量化，一次算完）。"""
    conn = sqlite3.connect(DB)
    codes = sorted({f["code"] for f in feats})
    ph = ",".join("?" * len(codes))
    q = (f"SELECT ts_code, trade_date, close FROM daily_cache WHERE ts_code IN ({ph})"
         " ORDER BY ts_code, trade_date")
    df = pd.read_sql_query(q, conn, params=codes)
    # 全市场等权基准（全表，一次）
    allq = "SELECT trade_date, close, vol FROM daily_cache WHERE vol > 0 ORDER BY ts_code, trade_date"
    alldf = pd.read_sql_query(allq, conn)
    conn.close()
    alldf = alldf.dropna(subset=["close"])
    dates = alldf.trade_date.astype(str).to_numpy()
    closes = alldf.close.to_numpy(dtype=float)
    bench = {}
    for k in FWD:
        fut = np.full(len(closes), np.nan)
        if len(closes) > k:
            fut[:len(closes) - k] = closes[k:] / closes[:len(closes) - k] - 1.0
        tmp = pd.DataFrame({"d": dates, "r": fut}).dropna()
        bench[k] = tmp.groupby("d")["r"].mean().to_dict()

    # 逐股映射前向收益
    fmap = {}
    for code, g in df.groupby("ts_code", sort=False):
        g = g.reset_index(drop=True)
        c = g.close.to_numpy(dtype=float)
        d = g.trade_date.astype(str).to_numpy()
        n = len(c)
        fw = np.full((n, len(FWD)), np.nan)
        for ki, k in enumerate(FWD):
            if n > k:
                fw[:n - k, ki] = c[k:] / c[:n - k] - 1.0
        fmap[code] = (d, fw)
    for f in feats:
        code = f["code"]
        if code not in fmap:
            f["fwd"] = [np.nan] * len(FWD)
            continue
        d, fw = fmap[code]
        i = np.searchsorted(d, f["date"])
        if i >= len(d) or d[i] != f["date"]:
            f["fwd"] = [np.nan] * len(FWD)
        else:
            f["fwd"] = list(fw[i])
    return bench


def eval_type_a(r, p):
    if r["age"] < p["a_min_age"] or r["age"] > p["a_max_age"]:
        return False
    if r["cur_low_idx"] < 0:
        return False
    t0o, t0c, t0v = r["t0_open"], r["t0_close"], r["t0_vol"]
    if r["deep_low"] > t0c * (1 - p["a_pullback_min"]):
        return False
    if r["deep_low"] > t0o * (1 + p["a_reach"]):
        return False
    if r["deep_low"] < t0o * (1 - p["a_open_tol"]):
        return False
    if r["pull_vol_mean"] > t0v * p["a_shrink"]:
        return False
    if r["h"] - r["l"] <= 0:
        return False
    stall = False
    if r["body_ratio"] <= 0.35 and r["up_sh"] >= 0.15 and r["dn_sh"] >= 0.15:
        stall = True
    elif r["dn_sh"] >= p["a_dn_sh"]:
        stall = True
    elif r["c"] > r["o"] and r["pct"] >= p["a_pct"]:
        stall = True
    if not stall:
        return False
    if r["pull_bars"] > p["a_pull_max_bars"]:
        return False
    if r["c"] > r["cur_low"] * (1 + p["a_close_off_low"]):
        return False
    if r["ma60"] > 0 and r["c"] < r["ma60"] * p["a_ma60_k"]:
        return False
    if r["c"] > t0c * p["a_max_ext"]:
        return False
    if r["vol20_now"] > 0 and r["vol"] > r["vol20_now"] * p["a_stall_volr"]:
        return False
    if p["a_require_amt"] and 0 < r["amount"] < p["a_min_amount"]:
        return False
    return True


def eval_type_b(r, p):
    if r["age"] < p["b_min_age"] or r["age"] > p["b_max_age"]:
        return False
    if r["pre_low_idx"] < 0:
        return False
    t0o, t0c, t0v = r["t0_open"], r["t0_close"], r["t0_vol"]
    if r["pre_low"] > t0c * (1 - p["b_pullback_min"]):
        return False
    if r["pre_low"] < t0o * (1 - p["b_open_tol"]):
        return False
    if r["l"] < t0o * (1 - p["b_open_tol"]):
        return False
    if p["b_require_reach"]:
        if r["deep_low"] > t0o * (1 + p["b_reach"]):
            return False
        if r["deep_low"] < t0o * (1 - p["b_open_tol"]):
            return False
    if r["pre_vol_mean"] > t0v * p["b_shrink"]:
        return False
    if r["c"] <= t0c * p["b_break_buf"]:
        return False
    if r["vol20_now"] <= 0 or r["vol"] < r["vol20_now"] * p["b_break_volr"]:
        return False
    if r["h"] - r["l"] <= 0:
        return False
    cp = (r["c"] - r["l"]) / (r["h"] - r["l"]) * 100.0
    if cp < p["b_close_pos"]:
        return False
    if (r["h"] - r["c"]) / (r["h"] - r["l"]) > p["b_shadow"]:
        return False
    if r["ma60"] > 0 and r["c"] < r["ma60"] * p["b_ma60_k"]:
        return False
    if r["c"] > t0c * p["b_max_ext"]:
        return False
    if p["b_require_amt"] and 0 < r["amount"] < p["b_min_amount"]:
        return False
    return True


BASE = {
    "a_min_age": 3, "a_max_age": 40, "a_pullback_min": 0.04, "a_reach": 0.05,
    "a_open_tol": 0.02, "a_shrink": 0.80, "a_dn_sh": 0.40, "a_pct": 1.5,
    "a_pull_max_bars": 5, "a_close_off_low": 0.05, "a_ma60_k": 0.97,
    "a_max_ext": 1.08, "a_stall_volr": 1.20,
    "a_require_amt": True, "a_min_amount": 50000.0,
    "b_min_age": 3, "b_max_age": 40, "b_pullback_min": 0.04, "b_open_tol": 0.02,
    "b_shrink": 1.00, "b_break_buf": 1.0, "b_break_volr": 1.20, "b_close_pos": 70.0,
    "b_shadow": 0.35, "b_ma60_k": 0.97, "b_max_ext": 1.15,
    "b_require_amt": True, "b_min_amount": 50000.0,
    "b_require_reach": False, "b_reach": 0.05,
}


def run(feats, bench, p):
    ka, kb = [], []
    for r in feats:
        if eval_type_a(r, p):
            ka.append(r)
        elif eval_type_b(r, p):
            kb.append(r)
    return ka, kb


def line(rows, bench, tag):
    if not rows:
        return f"    {tag}: 无信号"
    out = f"    {tag} n={len(rows):<6}日{len(set(r['date'] for r in rows)):<4}"
    for ki, k in enumerate(FWD):
        vals = np.array([r["fwd"][ki] for r in rows], dtype=float)
        vals = vals[np.isfinite(vals)]
        if len(vals) == 0:
            continue
        ex = np.array([r["fwd"][ki] - bench[k][r["date"]] for r in rows
                       if r["date"] in bench[k] and np.isfinite(r["fwd"][ki])], dtype=float)
        out += f" T+{k}:{vals.mean() * 100:+.2f}%/{(ex.mean() * 100 if len(ex) else 0):+.2f}%超/{(vals > 0).mean() * 100:.0f}%胜"
    return out


def show(feats, bench, p, label):
    ka, kb = run(feats, bench, p)
    print(f"  {label}")
    print(line(ka, bench, "A 缩量回踩止跌"))
    print(line(kb, bench, "B 回踩后突破T0收盘"))
    # 分年
    for tag, rows in (("A", ka), ("B", kb)):
        if not rows:
            continue
        yr = defaultdict(list)
        for r in rows:
            yr[r["date"][:4]].append(r)
        for y in sorted(yr):
            print("      " + line(yr[y], bench, f"{tag}{y}").strip())
    # 分市场环境
    for tag, rows in (("A", ka), ("B", kb)):
        if not rows:
            continue
        rg = defaultdict(list)
        for r in rows:
            rg[r["regime"]].append(r)
        for g in sorted(rg):
            print("      " + line(rg[g], bench, f"{tag}/{g}").strip())
    return ka, kb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="grid", choices=["grid", "base"])
    args = ap.parse_args()
    print("[标定] 载入原语缓存...", flush=True)
    feats = load_feats()
    print(f"[标定] {len(feats)} 行原语", flush=True)
    print("[标定] 计算前向收益与等权基准...", flush=True)
    bench = attach_fwd_and_bench(feats)
    print("[标定] 完成", flush=True)

    print("\n" + "=" * 104)
    print("【基准配置】")
    show(feats, bench, BASE, "基准")

    if args.mode == "grid":
        grids = [
            ("A.pullback_min", "a_pullback_min", [0.02, 0.04, 0.06, 0.08]),
            ("A.reach", "a_reach", [0.02, 0.05, 0.08, 0.15]),
            ("A.shrink", "a_shrink", [0.5, 0.8, 1.0, 1.5]),
            ("A.max_age", "a_max_age", [15, 25, 40, 60]),
            ("A.ma60_k", "a_ma60_k", [0.0, 0.95, 0.97, 1.0]),
            ("A.max_ext", "a_max_ext", [1.02, 1.05, 1.08, 1.15]),
            ("A.stall_volr", "a_stall_volr", [0.7, 1.0, 1.2, 2.0]),
            ("A.pull_max_bars", "a_pull_max_bars", [1, 3, 5, 10]),
            ("A.dn_sh", "a_dn_sh", [0.25, 0.40, 0.55]),
            ("B.pullback_min", "b_pullback_min", [0.02, 0.04, 0.06, 0.10]),
            ("B.break_volr", "b_break_volr", [1.0, 1.2, 1.4, 1.8]),
            ("B.close_pos", "b_close_pos", [40, 55, 70, 85]),
            ("B.shadow", "b_shadow", [0.15, 0.35, 0.6]),
            ("B.max_age", "b_max_age", [15, 25, 40, 60]),
            ("B.max_ext", "b_max_ext", [1.02, 1.06, 1.10, 1.25]),
            ("B.shrink", "b_shrink", [0.4, 0.7, 1.0, 1.4]),
            ("B.ma60_k", "b_ma60_k", [0.0, 0.95, 0.97, 1.0]),
            ("B.require_reach", "b_require_reach", [False, True]),
            ("B.break_buf", "b_break_buf", [1.0, 1.005, 1.02]),
        ]
        for gname, gkey, vals in grids:
            print(f"\n【单轴网格】{gname}")
            for v in vals:
                p = dict(BASE)
                p[gkey] = v
                ka, kb = run(feats, bench, p)
                print(f"  {gname}={v}")
                print(line(ka, bench, "A"))
                print(line(kb, bench, "B"))


if __name__ == "__main__":
    main()
