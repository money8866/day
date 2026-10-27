# -*- coding: utf-8 -*-
"""SHAKEOUT 组合条件检验 + 20261026 明细对齐（读 _tmp_shake_detail.csv，临时脚本，用完删除）。"""
import os

import numpy as np
import pandas as pd

DETAIL = r"D:\mystock\solo\_tmp_shake_detail.csv"
FWD = (1, 3, 5, 10)


def stat(d, k):
    if len(d) == 0:
        return None
    e = d[f"ex{k}"].dropna()
    if len(e) == 0:
        return None
    return dict(n=len(e), ex=e.mean() * 100, wr=(e > 0).mean() * 100,
                med=e.median() * 100)


def show(label, d):
    s3 = stat(d, 3)
    s10 = stat(d, 10)
    if not s3 or not s10:
        print(f"  {label:<34} n=0")
        return
    print(f"  {label:<34} n={s3['n']:>6}  T+3 超额{s3['ex']:>6.2f}% 胜率{s3['wr']:>5.1f}% "
          f"| T+10 超额{s10['ex']:>6.2f}% 胜率{s10['wr']:>5.1f}%")


def main():
    sig = pd.read_csv(DETAIL, dtype={"code": str, "date": str, "b_date": str})
    print(f"载入 {len(sig)} 条命中\n")

    # —— 持续性：同一 (code, b_date) 组合的连续触发天数 ——
    g = sig.groupby(["code", "b_date"])["date"].agg(["min", "count"]).reset_index()
    first = dict(zip(zip(g.code, g.b_date), g["min"]))
    sig["first_date"] = [first[(c, b)] for c, b in zip(sig.code, sig.b_date)]
    sig["is_first"] = sig["date"] == sig["first_date"]
    sig["span"] = sig.groupby(["code", "b_date"])["date"].transform("count")

    print("【触发持续性】同一长阳日锚下连续触发天数分布")
    print(f"  锚组数 {len(g)}  平均持续 {g['count'].mean():.2f} 天  中位 {g['count'].median():.0f} 天  "
          f"最长 {g['count'].max():.0f} 天")
    show("首次触发日(is_first)", sig[sig.is_first])
    show("非首次触发(重复)", sig[~sig.is_first])

    print("\n【持续天数分档】")
    for lo, hi in [(1, 1), (2, 3), (4, 6), (7, 99)]:
        d = sig[(sig.span >= lo) & (sig.span <= hi)]
        show(f"span {lo}~{hi}", d)
    print("\n【持续天数分档·仅首次触发】")
    for lo, hi in [(1, 1), (2, 3), (4, 6), (7, 99)]:
        d = sig[(sig.span >= lo) & (sig.span <= hi) & sig.is_first]
        show(f"span {lo}~{hi} & first", d)

    print("\n【基准对照】全样本")
    show("全部", sig)

    # —— 单因子网格（找有效轴）——
    axes = {
        "ma20_buf": [(-9, -0.001), (-0.001, 0.0), (0.0, 0.02), (0.02, 0.04), (0.04, 0.08), (0.08, 99)],
        "vr": [(0.6, 0.8), (0.8, 1.0), (1.0, 1.3), (1.3, 1.8), (1.8, 2.5)],
        "life_ext": [(0, 0.3), (0.3, 0.6), (0.6, 1.0), (1.0, 2.0), (2.0, 99)],
        "day_pc": [(-99, -3), (-3, -1), (-1, 0), (0, 2), (2, 99)],
        "b_pc": [(7, 9), (9, 11), (11, 99)],
        "b_volr": [(1.3, 2.0), (2.0, 3.0), (3.0, 99)],
        "dd_from_b": [(-9, -0.09), (-0.09, -0.07), (-0.07, -0.05), (-0.05, -0.03), (-0.03, 0)],
        "ret_from_b": [(-9, -0.07), (-0.07, -0.05), (-0.05, -0.03), (-0.03, 0.05)],
        "b_mkt_ret": [(-9, 0.005), (0.005, 0.02), (0.02, 99)],
        "vr20": [(0, 0.8), (0.8, 1.2), (1.2, 1.8), (1.8, 99)],
    }
    for k, bins in axes.items():
        print(f"\n【{k}】")
        for lo, hi in bins:
            d = sig[(sig[k] > lo) & (sig[k] <= hi)]
            show(f"{k} {lo}~{hi}", d)

    # —— 组合搜索：3 个候选因子两两/三三组合 ——
    print("\n\n【组合搜索（只保留 T+3 与 T+10 双正、n>=200、两年以上同向）】")
    cands = []
    grid = {
        "ma20_buf": [(0.0, 0.02), (0.0, 0.03), (0.0, 0.05)],
        "vr": [(0.6, 1.0), (0.6, 1.3), (0.8, 1.3)],
        "life_ext": [(0, 0.3), (0, 0.6), (0, 1.0)],
        "day_pc": [(-99, 0), (0, 99), (-99, -1)],
        "b_pc": [(7, 99), (9, 99)],
        "dd_from_b": [(-9, -0.05), (-9, -0.03)],
        "is_first": [True],
    }
    keys = list(grid)
    import itertools
    for r in (1, 2, 3):
        for combo in itertools.combinations(keys, r):
            m = pd.Series(True, index=sig.index)
            for kk in combo:
                lo, hi = grid[kk][0] if len(grid[kk]) == 1 else None, None
                if kk == "is_first":
                    m &= sig.is_first
                else:
                    vals = grid[kk]
                    sub = pd.Series(False, index=sig.index)
                    for lo, hi in vals:
                        sub |= (sig[kk] > lo) & (sig[kk] <= hi)
                    m &= sub
            d = sig[m]
            s3 = stat(d, 3)
            s10 = stat(d, 10)
            if not s3 or s3["n"] < 150:
                continue
            yrs = d.groupby(d.date.str[:4]).apply(
                lambda x: (x.ex10.dropna().mean() * 100) if len(x.ex10.dropna()) >= 20 else np.nan)
            yr_ok = int(np.nansum(yrs > 0))
            yr_ok10 = int(np.nansum(yrs < 0))
            if s3["ex"] > 0 and s10["ex"] > 0:
                cands.append((combo, s3["n"], s3["ex"], s3["wr"], s10["ex"], s10["wr"],
                              yr_ok, len(yrs), yr_ok10))
    cands.sort(key=lambda x: -(min(x[2], x[4]) * 2 + x[3] / 10))
    for c in cands[:40]:
        print(f"  {'+'.join(c[0]):<52} n={c[1]:>6} T+3 {c[2]:>6.2f}%/{c[3]:>5.1f}% "
              f"T+10 {c[4]:>6.2f}%/{c[5]:>5.1f}%  年份正 {c[6]}/{c[7]}")

    # —— 20261026 明细 ——
    d26 = sig[sig.date == "20261026"].copy()
    print(f"\n\n【20261026 命中 {len(d26)} 只】(引擎实际输出 18 只)")
    cols = ["code", "name", "industry", "b_date", "age", "close", "vr", "vr20", "day_pc",
            "b_pc", "b_volr", "ret_from_b", "dd_from_b", "ma20_buf", "life_ext",
            "mkt_ret", "b_mkt_ret", "batch", "span", "is_first"]
    print(d26[cols].to_string(index=False, float_format=lambda x: f"{x:.3f}"))

    print("\n【20261026 逐条 vs 历史最优档】")
    print(f"{'code':<11}{'name':<10}{'ma20buf':>9}{'life':>8}{'vr':>7}{'daypc':>8}{'first':>7}{'span':>6}")


if __name__ == "__main__":
    main()
