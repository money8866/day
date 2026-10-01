"""W7-不破中位（MIDLINE_HOLD）历史样本核对 / 参数校准。

口径与 w7_second_wave_engine.midline_hold 一致，但为向量化快速版：
按交易日遍历全池（日线窄表 + 当日 circ_mv），命中信号日即记录，统计 T+1/3/5/10 收盘收益。

用法：
  python _w7_midline.py                      # 默认参数跑默认区间
  python _w7_midline.py --grid               # 参数网格
  python _w7_midline.py --start 20250101 --volratio 0.6 --pct 9
"""
import argparse
import os
import sqlite3

import numpy as np
import pandas as pd

CACHE = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB = os.path.join(CACHE, "stock_data.db")
BASIC = os.path.join(CACHE, "stock_basic.csv")

MIN_CIRC_MV = 500_000.0  # 与引擎一致：流通市值 ≥50 亿元（Tushare 单位万元）
MIN_BARS = 250           # 上市满一年
FWD = (1, 3, 5, 10)

DEFAULTS = dict(pct=7.0, volr_day=1.3, close_pos=67.0, age=10,
                volratio=0.70, max_ext=1.10, low_tol=0.98, mid="hl")


def load_bars(conn, since="20230101", stride=1):
    """全池原始日线（本分支只用 OHLC/pct_chg/vol），按代码分组为 list-of-list。

    性能注意：库 22GB，`ORDER BY ts_code, trade_date` 会走索引随机读导致分钟级阻塞；
    改为**顺序全表扫描**（不排序）后在 pandas 内排序，快一个量级。
    stride>1 时按排序后的代码序列抽稀，用于快速校准。
    """
    q = ("SELECT ts_code, trade_date, open, high, low, close, pct_chg, vol FROM daily_cache "
         f"WHERE trade_date >= '{since}'")
    df = pd.read_sql_query(q, conn)
    df = df.dropna(subset=["close", "high", "low", "vol"])
    df = df.sort_values(["ts_code", "trade_date"], kind="mergesort")
    if stride > 1:
        keep = set(sorted(df.ts_code.unique())[::stride])
        df = df[df.ts_code.isin(keep)]
    frames = {}
    for code, g in df.groupby("ts_code", sort=False):
        frames[code] = {
            "date": g.trade_date.astype(str).tolist(),
            "o": g.open.astype(float).tolist(), "h": g.high.astype(float).tolist(),
            "l": g.low.astype(float).tolist(), "c": g.close.astype(float).tolist(),
            "pc": g.pct_chg.astype(float).fillna(0.0).tolist(),
            "v": g.vol.astype(float).tolist(),
        }
    return frames


def prep(f, p):
    """按参数预计算：长阳日布尔、前20日均量、MA20、中位、60日新高标记"""
    o, h, l, c, pc, v = f["o"], f["h"], f["l"], f["c"], f["pc"], f["v"]
    n = len(c)
    vol20 = [0.0] * n
    run = 0.0
    for i in range(n):
        vol20[i] = (run / 20.0) if i >= 20 else 0.0
        run += v[i]
        if i >= 20:
            run -= v[i - 20]
    ma20 = [0.0] * n
    run = 0.0
    for i in range(n):
        run += c[i]
        if i >= 20:
            run -= c[i - 20]
        ma20[i] = (run / 20.0) if i >= 19 else 0.0
    mid = [(h[i] + l[i]) / 2.0 if p["mid"] == "hl" else (o[i] + c[i]) / 2.0 for i in range(n)]
    span = [h[i] - l[i] for i in range(n)]
    yang = [bool(c[i] > o[i] and pc[i] >= p["pct"] and vol20[i] > 0
                 and v[i] >= vol20[i] * p["volr_day"]
                 and (span[i] > 0 and (c[i] - l[i]) / span[i] * 100.0 >= p["close_pos"]))
            for i in range(n)]
    nh60 = [bool(i >= 60 and c[i] >= max(h[i - 60:i])) for i in range(n)]
    f["vol20"], f["mid"], f["yang"], f["ma20"], f["nh60"] = vol20, mid, yang, ma20, nh60
    return f


def signal_at(f, p, i):
    """等价于引擎 midline_hold(df, i)：返回命中的长阳日 index 或 -1"""
    if i < 21:
        return -1
    c, l, v, h = f["c"], f["l"], f["v"], f["h"]
    yang, mid, ma20 = f["yang"], f["mid"], f["ma20"]
    if p.get("ma20_up") and not (i >= 5 and ma20[i] > 0 and ma20[i] >= ma20[i - 5]):
        return -1
    for j in range(max(20, i - p["age"]), i):
        if not yang[j]:
            continue
        if p.get("nh60") and not f["nh60"][j]:
            continue
        if p.get("volr20") and (f["vol20"][i] <= 0 or v[i] > f["vol20"][i] * p["volr20"]):
            continue
        m = mid[j]
        if m <= 0:
            continue
        if c[i] > h[j]:
            continue
        if c[i] > c[j] * p["max_ext"]:
            continue
        seg_c = c[j + 1:i + 1]
        if min(seg_c) < m:
            continue
        seg_l = l[j + 1:i + 1]
        if min(seg_l) < m * p["low_tol"]:
            continue
        if p.get("pull_hold") and min(seg_l) < c[j] * p["pull_hold"]:
            continue
        if v[i] > v[j] * p["volratio"]:
            continue
        if min(seg_c) > c[j] * 0.995:
            continue
        return j
    return -1


def run(conn, frames, start, end, p, basic, verbose=False):
    rows, base = [], {k: [] for k in FWD}
    dates = sorted({d for f in frames.values() for d in f["date"] if start <= d <= end})
    for date in dates:
        mv = pd.read_sql_query("SELECT ts_code, circ_mv FROM daily_basic_cache WHERE trade_date=?",
                               conn, params=(date,))
        mv_map = dict(zip(mv.ts_code, mv.circ_mv))
        for code, f in frames.items():
            if not code.endswith((".SZ", ".SH")) or code.startswith(("8", "43", "83", "87", "92")):
                continue
            if float(mv_map.get(code) or 0.0) < MIN_CIRC_MV:
                continue
            nm = basic.get(code, "")
            if "ST" in nm.upper() or "退" in nm:
                continue
            try:
                i = f["idx"][date]
            except KeyError:
                continue
            if i < MIN_BARS:
                continue
            c = f["c"]
            fw = {}
            for k in FWD:
                fw[k] = (c[i + k] / c[i] - 1.0) if i + k < len(c) else None
                if fw[k] is not None:
                    base[k].append(fw[k])
            j = signal_at(f, p, i)
            if j < 0:
                continue
            rows.append({"date": date, "code": code, "name": nm, "close": c[i],
                         "yang_date": f["date"][j], "yang_close": c[j], "mid": f["mid"][j],
                         "vol_ratio_day": f["v"][i] / f["v"][j] if f["v"][j] else 0.0,
                         **{f"fwd{k}": fw[k] for k in FWD}})
        if verbose and len(rows) and len(rows) % 200 == 0:
            print(f"  ...{date} 累计信号 {len(rows)}", flush=True)
    sig = pd.DataFrame(rows)
    bn = {k: (float(np.mean(v)) if v else float("nan")) for k, v in base.items()}
    return sig, bn


def summarize(tag, sig, base):
    if sig.empty:
        print(f"{tag}: 无信号")
        return
    out = [f"{tag}: n={len(sig)}  区间={sig.date.min()}~{sig.date.max()}"]
    for k in FWD:
        col = sig[f"fwd{k}"].dropna()
        if col.empty:
            continue
        out.append(f"  T+{k:<2d} 均值={col.mean() * 100:+6.2f}%  中位={col.median() * 100:+6.2f}%  "
                   f"胜率={float((col > 0).mean()) * 100:5.1f}%  | 全池基线={base[k] * 100:+5.2f}%  "
                   f"超额={(col.mean() - base[k]) * 100:+6.2f}%")
    print("\n".join(out), flush=True)
    return sig


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20240102")
    ap.add_argument("--end", default="20260930")
    ap.add_argument("--since", default="20230101", help="日线载入起点（需早于 start，保证 MA20/60日新高有前置窗口）")
    ap.add_argument("--pct", type=float)
    ap.add_argument("--volr-day", type=float)
    ap.add_argument("--volratio", type=float)
    ap.add_argument("--age", type=int)
    ap.add_argument("--max-ext", type=float)
    ap.add_argument("--low-tol", type=float)
    ap.add_argument("--close-pos", type=float)
    ap.add_argument("--mid", choices=["hl", "body"])
    ap.add_argument("--ma20-up", type=int, choices=[0, 1])
    ap.add_argument("--nh60", type=int, choices=[0, 1], help="长阳日须创 60 日新高（平台突破属性）")
    ap.add_argument("--pull-hold", type=float, help="回踩期最低 ≥ 长阳收盘×该系数（回踩要浅）")
    ap.add_argument("--volr20", type=float, help="选股日 量/前20日均量 上限（用于比对 V5.2 的 volr≤0.66 阈值）")
    ap.add_argument("--stride", type=int, default=1, help="代码抽稀步长（>1 提速，用于快速校准）")
    ap.add_argument("--grid", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    basic = {}
    if os.path.exists(BASIC):
        b = pd.read_csv(BASIC, dtype=str)
        basic = dict(zip(b.ts_code, b.name.fillna("")))
    print("加载日线...", flush=True)
    frames = load_bars(conn, since=args.since, stride=args.stride)
    print(f"  代码数={len(frames)}", flush=True)
    sig_index = {}
    for code, f in frames.items():
        f["idx"] = {d: i for i, d in enumerate(f["date"])}
        sig_index[code] = f

    def make(**kw):
        p = dict(DEFAULTS)
        p.update({k: v for k, v in kw.items() if v is not None})
        p.update({k: v for k, v in
                  dict(pct=args.pct, volr_day=args.volr_day, volratio=args.volratio, age=args.age,
                       max_ext=args.max_ext, low_tol=args.low_tol, close_pos=args.close_pos,
                       ma20_up=args.ma20_up, nh60=args.nh60, pull_hold=args.pull_hold,
                       volr20=args.volr20,
                       **{"mid": args.mid}).items() if v is not None})
        return p

    if args.grid:
        # 聚焦「结构语境」条件：MA20 走平/向上、长阳须平台突破、回踩要浅、更短新鲜度
        grid = [
            {},
            dict(ma20_up=1),
            dict(nh60=1),
            dict(ma20_up=1, nh60=1),
            dict(max_ext=1.03),
            dict(max_ext=1.03, ma20_up=1),
            dict(ma20_up=1, age=5),
            dict(ma20_up=1, pull_hold=0.95),
            dict(ma20_up=1, volratio=0.6),
            dict(ma20_up=1, pct=9.0),
            dict(ma20_up=1, nh60=1, max_ext=1.03),
            dict(ma20_up=1, nh60=1, volratio=0.6),
        ]
        if args.stride == 1:
            grid = [{}]
    else:
        grid = [{}]

    for kw in grid:
        p = make(**kw)
        for f in frames.values():
            prep(f, p)
        tag = ("pct=%g volr_day=%g pos=%g age=%d volratio=%g ext=%g tol=%g mid=%s "
               "ma20up=%s nh60=%s pull=%s volr20=%s"
               % (p["pct"], p["volr_day"], p["close_pos"], p["age"], p["volratio"],
                  p["max_ext"], p["low_tol"], p["mid"],
                  p.get("ma20_up"), p.get("nh60"), p.get("pull_hold"), p.get("volr20")))
        sig, base = run(conn, frames, args.start, args.end, p, basic)
        summarize(tag, sig, base)
        if not args.grid and not sig.empty:
            print("\n最近命中样本（末 12 条）：")
            show = sig.sort_values("date").tail(12)
            print(show[["date", "code", "name", "close", "yang_date", "yang_close", "mid",
                        "vol_ratio_day", "fwd3", "fwd5", "fwd10"]].to_string(index=False))
        print("", flush=True)


if __name__ == "__main__":
    main()
