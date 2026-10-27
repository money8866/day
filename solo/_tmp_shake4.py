# -*- coding: utf-8 -*-
"""SHAKEOUT 优化：逐轴单值全交叉网格 + 样本外验证（临时脚本，用完删除）。"""
import itertools

import numpy as np
import pandas as pd

DETAIL = r"D:\mystock\solo\_tmp_shake_detail.csv"

# 轴：每轴取一个具体档位，全交叉
AXES = {
    "ma20_buf": [("≤0.01", 0.0, 0.01), ("≤0.02", 0.0, 0.02), ("≤0.03", 0.0, 0.03),
                 ("≤0.05", 0.0, 0.05), ("全", -9, 99)],
    "vr20": [("≤0.9", 0.0, 0.9), ("≤1.0", 0.0, 1.0), ("≤1.2", 0.0, 1.2),
             ("≤1.6", 0.0, 1.6), ("全", -9, 99)],
    "vr": [("0.6~0.9", 0.6, 0.9), ("0.6~1.2", 0.6, 1.2), ("0.6~1.8", 0.6, 1.8),
           ("全", 0.6, 2.5)],
    "life_ext": [("≤0.3", 0.0, 0.3), ("≤0.8", 0.0, 0.8), ("≤1.5", 0.0, 1.5),
                 ("全", 0, 99)],
    "b_mkt_ret": [("≤0.5%", -9, 0.005), ("≤2%", -9, 0.02), ("全", -9, 99)],
    "day_pc": [("≤-1%", -99, -1), ("≤0", -99, 0), ("全", -99, 99)],
    "age": [("=1", 1, 1), ("≤3", 1, 3), ("≤6", 1, 6), ("全", 1, 10)],
    "dd_from_b": [("≤-7%", -9, -0.07), ("≤-5%", -9, -0.05), ("≤-3%", -9, -0.03),
                   ("全", -9, 0)],
    "first": [("首次", 1, 1), ("全", 0, 1)],
}
COL = {"ma20_buf": "ma20_buf", "vr20": "vr20", "vr": "vr", "life_ext": "life_ext",
       "b_mkt_ret": "b_mkt_ret", "day_pc": "day_pc", "age": "age",
       "dd_from_b": "dd_from_b", "first": "is_first"}


def ex(d, k):
    s = d[f"ex{k}"].dropna()
    return (len(s), s.mean() * 100, (s > 0).mean() * 100) if len(s) else (0, np.nan, np.nan)


def main():
    sig = pd.read_csv(DETAIL, dtype={"code": str, "date": str, "b_date": str})
    sig["year"] = sig.date.str[:4]
    g = sig.groupby(["code", "b_date"])["date"].agg(["min", "count"]).reset_index()
    first = dict(zip(zip(g.code, g.b_date), g["min"]))
    sig["is_first"] = sig["date"] == sig[["code", "b_date"]].apply(
        lambda r: first[(r["code"], r["b_date"])], axis=1)
    print(f"裸命中 {len(sig)}，日均 {len(sig)/sig.date.nunique():.1f} 只\n")

    # 各轴掩码
    masks = {}
    for ax, opts in AXES.items():
        col = COL[ax]
        for label, lo, hi in opts:
            if ax == "first":
                m = sig.is_first if label == "首次" else pd.Series(True, index=sig.index)
            else:
                m = (sig[col] > lo) & (sig[col] <= hi)
            masks[(ax, label)] = m

    keys = list(AXES)
    rows = []
    for combo in itertools.product(*[AXES[k] for k in keys]):
        m = pd.Series(True, index=sig.index)
        for (ax, label), mm in zip(zip(keys, combo), [masks[(k, c[0])] for k, c in zip(keys, combo)]):
            m &= mm
        d = sig[m]
        n3, e3, w3 = ex(d, 3)
        if n3 < 120:
            continue
        n10, e10, w10 = ex(d, 10)
        if not np.isfinite(e3) or not np.isfinite(e10):
            continue
        yr = {}
        for y, x in d.groupby("year"):
            if len(x.ex10.dropna()) >= 25:
                yr[y] = x.ex10.dropna().mean() * 100
        pos = sum(1 for v in yr.values() if v > 0)
        rows.append(dict(combo="|".join(c[0] for c in combo), n=n3, e3=e3, w3=w3,
                         e10=e10, w10=w10, yr=yr, pos=pos, nyr=len(yr),
                         days=d.date.nunique(), perday=n3 / max(1, d.date.nunique()),
                         dd=d))

    # —— 筛选：T+3 与 T+10 均正、至少 2/3 年 T+10 为正、n≥120 ——
    ok = [r for r in rows if r["e3"] > 0 and r["e10"] > 0 and r["pos"] >= 2 and r["nyr"] >= 3]
    ok.sort(key=lambda r: -(min(r["e3"], r["e10"]) * 2 + min(r["w3"], r["w10"]) / 5))
    print(f"合格组合 {len(ok)} 个（n>=120、T+3/T+10 双正、>=2/3 年 T+10 为正），列前 30：\n")
    for r in ok[:30]:
        ys = " ".join(f"{y}:{v:+.2f}" for y, v in sorted(r["yr"].items()))
        print(f"  {r['combo']:<58} n={r['n']:>5} 日均{r['perday']:>4.1f} "
              f"T+3 {r['e3']:>6.2f}%/{r['w3']:>5.1f}% T+10 {r['e10']:>6.2f}%/{r['w10']:>5.1f}% [{ys}]")

    # —— 样本外：2024 选参 → 2025/2026 检验 ——
    print("\n\n【样本外：以 2024 为训练年选最优参数，在 2025+2026 上检验】")
    tr = []
    for r in rows:
        d24 = r["dd"][r["dd"].year == "2024"]
        n24, e24, w24 = ex(d24, 10)
        if n24 < 40:
            continue
        tr.append((r["e10"], r, e24, w24))
    tr.sort(key=lambda x: -(x[0] * 0.5 + x[2] * 0.5))
    print(f"{'组合':<58}{'24训练':>9}{'25检验':>9}{'26检验':>9}{'全T+3':>9}{'全T+10':>9}")
    for sc, r, e24, w24 in tr[:25]:
        d25 = r["dd"][r["dd"].year == "2025"]
        d26_ = r["dd"][r["dd"].year == "2026"]
        _, e25, w25 = ex(d25, 10)
        _, e26, w26 = ex(d26_, 10)
        print(f"  {r['combo']:<56}{e24:>8.2f}%{e25:>8.2f}%{e26:>8.2f}%"
              f"{r['e3']:>8.2f}%{r['e10']:>8.2f}%")

    # —— 引擎 18 只：各轴落点 ——
    print("\n\n【引擎 18 只（20261026）各轴落点】")
    d26 = sig[sig.date == "20261026"]
    eng = ["688608.SH", "002945.SZ", "300817.SZ", "300687.SZ", "688039.SH", "001298.SZ",
           "300276.SZ", "002475.SZ", "300353.SZ", "002988.SZ", "300561.SZ", "300421.SZ",
           "688775.SH", "002896.SZ", "688207.SH", "600698.SH", "688023.SH", "603893.SH"]
    e18 = d26[d26.code.isin(eng)]
    print(f"  引擎 18 只：ma20_buf {e18.ma20_buf.min():.3f}~{e18.ma20_buf.max():.3f}  "
          f"vr20 {e18.vr20.min():.2f}~{e18.vr20.max():.2f}  "
          f"vr {e18.vr.min():.2f}~{e18.vr.max():.2f}  "
          f"life {e18.life_ext.min():.2f}~{e18.life_ext.max():.2f}")
    for ax, opts in AXES.items():
        col = COL[ax]
        line = []
        for label, lo, hi in opts:
            if ax == "first":
                m = e18.is_first if label == "首次" else pd.Series(True, index=e18.index)
            else:
                m = (e18[col] > lo) & (e18[col] <= hi)
            line.append(f"{label}:{int(m.sum())}")
        print(f"    {ax:<11} " + "  ".join(line))

    # —— 全轴同时收紧的落地数 ——
    print("\n\n【20261026 落地：逐步收紧后的剩余数量】")
    cand = [
        ("原口径(裸命中)", []),
        ("ma20≤0.02", [("ma20_buf", "≤0.02")]),
        ("ma20≤0.02 & vr20≤1.2", [("ma20_buf", "≤0.02"), ("vr20", "≤1.2")]),
        ("ma20≤0.02 & b_mkt≤0.5%", [("ma20_buf", "≤0.02"), ("b_mkt_ret", "≤0.5%")]),
        ("ma20≤0.02 & vr≤0.9 & b_mkt≤0.5%", [("ma20_buf", "≤0.02"), ("vr", "0.6~0.9"),
                                          ("b_mkt_ret", "≤0.5%")]),
        ("ma20≤0.02 & vr≤1.2 & b_mkt≤2% & day≤0", [("ma20_buf", "≤0.02"), ("vr", "0.6~1.2"),
                                                ("b_mkt_ret", "≤2%"), ("day_pc", "≤0")]),
        ("ma20≤0.05 & vr≤1.2 & life≤0.8 & b_mkt≤2%", [("ma20_buf", "≤0.05"), ("vr", "0.6~1.2"),
                                                ("life_ext", "≤0.8"), ("b_mkt_ret", "≤2%")]),
    ]
    for cname, parts in cand:
        m = pd.Series(True, index=d26.index)
        for ax, lbl in parts:
            m &= masks[(ax, lbl)]
        d = d26[m]
        print(f"  {cname:<46} {len(d):>2} 只  "
              f"{', '.join(d.code.tolist()) if len(d) else '（空）'}")


if __name__ == "__main__":
    main()
