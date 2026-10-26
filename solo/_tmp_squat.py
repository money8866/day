# -*- coding: utf-8 -*-
"""「极致缩量十字星下蹲」（SQUAT）历史样本标定（临时脚本，用完删除）。

缘起：极米科技 688696.SH 20260929 —— T0=20260901（P100 天量 40748）后 19 根，
当日量 4305、量比 0.29（前20日均量 14800）、实体 31% 上下影 35%/33%、涨 -0.07%，
收盘 81.00 仍在 MA60 73.03 之上，次日 09/30 放量 +4.68%（量比 1.35）确认。

候选口径（初稿，待网格标定）：
  ① 前置：距今 ≤60 根内存在 P99 天量 T0；
  ② 极致缩量：vol / 前20日均量 ≤ VOLR；
  ③ 十字星：实体 ≤ BODY×振幅，上影 ≥ SHADOW×振幅，下影 ≥ SHADOW×振幅；
  ④ 窄幅：振幅 / 前收 ≤ AMP；
  ⑤ 短期已跌破 MA20（确属下蹲），但中期 MA60 未破（趋势仍存）；
  ⑥ 自 T0 后最高点回撤 ≥ DD。
"""
import argparse
import os
import sqlite3

import numpy as np
import pandas as pd

CACHE = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB = os.path.join(CACHE, "stock_data.db")
BASIC = os.path.join(CACHE, "stock_basic.csv")
FWD = (1, 3, 5, 10)


def load_bars(conn, since):
    q = ("SELECT ts_code, trade_date, open, high, low, close, pct_chg, vol FROM daily_cache "
         f"WHERE trade_date >= '{since}'")
    df = pd.read_sql_query(q, conn)
    df = df.dropna(subset=["close", "high", "low", "vol"])
    df = df.sort_values(["ts_code", "trade_date"], kind="mergesort")
    frames = {}
    for code, g in df.groupby("ts_code", sort=False):
        frames[code] = {
            "date": g.trade_date.astype(str).tolist(),
            "o": g.open.astype(float).to_numpy(), "h": g.high.astype(float).to_numpy(),
            "l": g.low.astype(float).to_numpy(), "c": g.close.astype(float).to_numpy(),
            "pc": g.pct_chg.astype(float).fillna(0.0).to_numpy(),
            "v": g.vol.astype(float).to_numpy(),
        }
    return frames


def rolling_mean(a, w):
    out = np.zeros(len(a))
    cs = np.cumsum(np.insert(a, 0, 0.0))
    for i in range(len(a)):
        if i >= w:
            out[i] = (cs[i] - cs[i - w]) / w
    return out


def rolling_max(a, w):
    out = np.zeros(len(a))
    for i in range(len(a)):
        if i >= 1:
            lo = max(0, i - w)
            out[i] = a[lo:i].max() if i > lo else 0.0
    return out


def prep(f):
    c, h, l, o, v = f["c"], f["h"], f["l"], f["o"], f["v"]
    n = len(c)
    f["vol20"] = rolling_mean(v, 20)
    f["ma20"] = rolling_mean(c, 20)
    f["ma60"] = rolling_mean(c, 60)
    # T0：前 250 根（不含当日）量的 P99
    p99 = np.zeros(n)
    for i in range(n):
        lo = max(0, i - 250)
        seg = v[lo:i]
        if len(seg) >= 120:
            p99[i] = np.quantile(seg, 0.99)
    f["t0"] = (p99 > 0) & (v >= p99)
    f["p99"] = p99
    return f


def scan(f, p):
    """返回该股票全部命中 (index) 列表；confirm=True 时返回「次日放量阳线确认」那根"""
    o, h, l, c, v = f["o"], f["h"], f["l"], f["c"], f["v"]
    vol20, ma20, ma60, t0 = f["vol20"], f["ma20"], f["ma60"], f["t0"]
    n = len(c)
    hits = []
    t0_idx = None
    for i in range(61, n - 1):
        # 维护最近 T0（≤60 根）
        if t0[i]:
            t0_idx = i
        if t0_idx is None or i - t0_idx > p["age"] or i == t0_idx:
            continue
        if i - t0_idx < p.get("age_min", 0):
            continue
        if vol20[i] <= 0 or ma20[i] <= 0 or ma60[i] <= 0:
            continue
        volr = v[i] / vol20[i]
        if volr > p["volr"]:
            continue
        # 极致缩量：相对 T0 天量衰减到位
        if p.get("t0_ratio") and v[t0_idx] > 0 and v[t0_idx] / v[i] < p["t0_ratio"]:
            continue
        # 极致缩量：接近近 60 日地量
        if p.get("low60"):
            lo = v[max(0, i - 60):i + 1].min()
            if lo > 0 and v[i] > lo * p["low60"]:
                continue
        span = h[i] - l[i]
        if span <= 0:
            continue
        prev_c = c[i - 1]
        if prev_c <= 0:
            continue
        amp = span / prev_c
        if amp > p["amp"]:
            continue
        body = abs(c[i] - o[i]) / span
        if body > p["body"]:
            continue
        up_sh = (h[i] - max(o[i], c[i])) / span
        dn_sh = (min(o[i], c[i]) - l[i]) / span
        if up_sh < p["shadow"] or dn_sh < p["shadow"]:
            continue
        # 下蹲：短期已跌破 MA20，但中期 MA60 未破
        if p.get("require_below_ma20", True):
            if c[i] >= ma20[i]:
                continue
        if c[i] < ma60[i]:
            continue
        # 自 T0 后最高点回撤
        pk = h[t0_idx:i + 1].max()
        if pk <= 0 or (pk - c[i]) / pk < p["dd"]:
            continue
        # 前期要有过一波拉升（T0 前 20 日累计涨幅）
        if p.get("min_rise"):
            base_c = c[max(0, t0_idx - 20)]
            if base_c <= 0 or c[t0_idx] / base_c - 1.0 < p["min_rise"]:
                continue
        if p.get("confirm"):
            j = i + 1
            if j >= n:
                continue
            if vol20[j] <= 0 or f["pc"][j] < p["c_pct"] or v[j] / vol20[j] < p["c_volr"]:
                continue
            hits.append(j)
        else:
            hits.append(i)
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20241201")
    ap.add_argument("--from", dest="frm", default="20250101")
    ap.add_argument("--volr", type=float, default=0.35)
    ap.add_argument("--amp", type=float, default=0.04)
    ap.add_argument("--body", type=float, default=0.35)
    ap.add_argument("--shadow", type=float, default=0.15)
    ap.add_argument("--dd", type=float, default=0.08)
    ap.add_argument("--age", type=int, default=60)
    ap.add_argument("--t0-ratio", dest="t0_ratio", type=float, default=0.0)
    ap.add_argument("--low60", type=float, default=0.0)
    ap.add_argument("--min-rise", dest="min_rise", type=float, default=0.0)
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--c-pct", dest="c_pct", type=float, default=2.0)
    ap.add_argument("--c-volr", dest="c_volr", type=float, default=1.2)
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--grid2", action="store_true")
    ap.add_argument("--grid3", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    basic = pd.read_csv(BASIC, dtype={"ts_code": str, "list_date": str})
    name_map = dict(zip(basic.ts_code, basic.name))
    frames = load_bars(conn, args.since)
    conn.close()
    print(f"载入 {len(frames)} 只股票", flush=True)
    for f in frames.values():
        prep(f)

    base_cfg = dict(volr=args.volr, amp=args.amp, body=args.body, shadow=args.shadow,
                    dd=args.dd, age=args.age, t0_ratio=args.t0_ratio, low60=args.low60,
                    min_rise=args.min_rise, confirm=args.confirm, c_pct=args.c_pct,
                    c_volr=args.c_volr, age_min=0)
    configs = [base_cfg]
    if args.grid3:
        configs = []
        for lbl, over in [
            ("t0ratio9", dict(t0_ratio=9.0)),
            ("low60=1.2", dict(low60=1.2)),
            ("minrise20", dict(min_rise=0.20)),
            ("low60=1.2+minrise20", dict(low60=1.2, min_rise=0.20)),
            ("t0ratio8+low60=1.2", dict(t0_ratio=8.0, low60=1.2)),
            ("t0ratio8+minrise20", dict(t0_ratio=8.0, min_rise=0.20)),
        ]:
            c = dict(base_cfg)
            c.update(dd=0.08, t0_ratio=0.0)
            c.update(over)
            c["_lbl"] = lbl
            configs.append(c)
    elif args.grid2:
        configs = []
        for lbl, dd, t0r, amin in [
            ("基准 dd8", 0.08, 0.0, 0),
            ("dd8+age15", 0.08, 0.0, 15),
            ("dd8+t0ratio8", 0.08, 8.0, 0),
            ("dd8+t0ratio8+age15", 0.08, 8.0, 15),
            ("dd10", 0.10, 0.0, 0),
            ("dd10+t0ratio8", 0.10, 8.0, 0),
            ("dd10+age10", 0.10, 0.0, 10),
            ("dd12+t0ratio8", 0.12, 8.0, 0),
        ]:
            c = dict(base_cfg)
            c.update(dd=dd, t0_ratio=t0r, age_min=amin, _lbl=lbl)
            configs.append(c)
    elif args.grid:
        configs = []
        for volr in (0.30, 0.35, 0.45):
            for dd in (0.05, 0.08, 0.12):
                for t0r in (0.0, 8.0, 12.0):
                    for cf in (False, True):
                        c = dict(base_cfg)
                        c.update(volr=volr, dd=dd, t0_ratio=t0r, confirm=cf)
                        configs.append(c)

    # 全市场等权基准：每日所有股票的前向收益均值
    base = {k: {} for k in FWD}
    for code, f in frames.items():
        c = f["c"]
        dts = f["date"]
        for i in range(len(c) - 10):
            for k in FWD:
                if i + k < len(c) and c[i] > 0:
                    base[k].setdefault(dts[i], []).append(c[i + k] / c[i] - 1.0)
    base_m = {k: {d: float(np.mean(v)) for d, v in base[k].items()} for k in FWD}

    for cfg in configs:
        sig = []
        for code, f in frames.items():
            hits = scan(f, cfg)
            c, dts = f["c"], f["date"]
            for i in hits:
                if dts[i] < args.frm:
                    continue
                row = []
                for k in FWD:
                    row.append(c[i + k] / c[i] - 1.0 if i + k < len(c) and c[i] > 0 else np.nan)
                sig.append((dts[i], code, name_map.get(code, code), row))
        print("\n" + "=" * 96)
        print(f"参数 [{cfg.get('_lbl', '')}] {cfg}")
        if not sig:
            print("  无信号")
            continue
        print(f"  信号数 = {len(sig)}  覆盖交易日 = {len(set(s[0] for s in sig))}")
        print(f"  {'周期':<6}{'样本':>7}{'均值%':>9}{'超额%':>9}{'胜率%':>8}{'中位%':>9}")
        for ki, k in enumerate(FWD):
            rs, ex = [], []
            for s in sig:
                r = s[3][ki]
                if np.isnan(r):
                    continue
                b = base_m[k].get(s[0])
                if b is None:
                    continue
                rs.append(r)
                ex.append(r - b)
            if not rs:
                print(f"  T+{k:<4}{0:>7}")
                continue
            print(f"  T+{k:<4}{len(rs):>7}{np.mean(rs)*100:>9.2f}{np.mean(ex)*100:>9.2f}"
                  f"{np.mean([1 if x > 0 else 0 for x in ex])*100:>8.1f}{np.median(rs)*100:>9.2f}")
        # 分年拆分（T+10 超额）
        for yr in sorted(set(s[0][:4] for s in sig)):
            sub = [s for s in sig if s[0][:4] == yr]
            ex = []
            for s in sub:
                r = s[3][3]
                if np.isnan(r):
                    continue
                b = base_m[10].get(s[0])
                if b is None:
                    continue
                ex.append(r - b)
            if ex:
                print(f"    {yr}: n={len(ex)} T+10超额={np.mean(ex)*100:+.2f}% "
                      f"胜率={np.mean([1 if x > 0 else 0 for x in ex])*100:.0f}%")


if __name__ == "__main__":
    main()