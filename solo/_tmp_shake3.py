# -*- coding: utf-8 -*-
"""SHAKEOUT 优化方案网格检验：找「减量但不损 alpha」的过滤条件（读 detail CSV，临时脚本）。"""
import itertools

import numpy as np
import pandas as pd

DETAIL = r"D:\mystock\solo\_tmp_shake_detail.csv"
FWD = (3, 10)


def st(d, k):
    e = d[f"ex{k}"].dropna()
    if len(e) == 0:
        return None
    return len(e), e.mean() * 100, (e > 0).mean() * 100


def line(label, d):
    s3, s10 = st(d, 3), st(d, 10)
    if not s3:
        print(f"  {label:<46} n=0")
        return None
    print(f"  {label:<46} n={s3[0]:>5} T+3 {s3[1]:>6.2f}%/{s3[2]:>5.1f}% "
          f"| T+10 {s10[1]:>6.2f}%/{s10[2]:>5.1f}%")
    return (s3[0], s3[1], s3[2], s10[1], s10[2])


def main():
    sig = pd.read_csv(DETAIL, dtype={"code": str, "date": str, "b_date": str})
    sig["year"] = sig.date.str[:4]
    g = sig.groupby(["code", "b_date"])["date"].agg(["min", "count"]).reset_index()
    first = dict(zip(zip(g.code, g.b_date), g["min"]))
    sig["is_first"] = sig["date"] == sig[["code", "b_date"]].apply(
        lambda r: first[(r["code"], r["b_date"])], axis=1)

    print(f"裸命中 {len(sig)}  覆盖 {sig.date.nunique()} 日  日均 {len(sig)/sig.date.nunique():.1f} 只\n")

    # ============ 1. 单因子全分档（含分年） ============
    axes = {
        "ma20_buf": [(-9, 0.01), (0.01, 0.02), (0.02, 0.03), (0.03, 0.05), (0.05, 0.08), (0.08, 99)],
        "vr": [(0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0), (1.0, 1.3), (1.3, 1.8), (1.8, 2.5)],
        "life_ext": [(0, 0.3), (0.3, 0.8), (0.8, 1.5), (1.5, 3.0), (3.0, 99)],
        "day_pc": [(-99, -5), (-5, -3), (-3, -1), (-1, 0), (0, 2), (2, 99)],
        "b_mkt_ret": [(-9, 0.005), (0.005, 0.02), (0.02, 99)],
        "age": [(1, 1), (2, 3), (4, 6), (7, 10)],
        "vr20": [(0, 0.9), (0.9, 1.2), (1.2, 1.6), (1.6, 99)],
    }
    for k, bins in axes.items():
        print(f"\n【{k}】(全样本 / 分年 T+10 超额)")
        for lo, hi in bins:
            d = sig[(sig[k] > lo) & (sig[k] <= hi)]
            s = st(d, 10)
            if not s:
                continue
            yrs = d.groupby("year").apply(lambda x: x.ex10.dropna().mean() * 100
                                          if len(x.ex10.dropna()) >= 30 else np.nan)
            ys = "  ".join(f"{y}:{v:+.2f}" for y, v in yrs.items())
            print(f"  {lo}~{hi:<8} n={s[0]:>5}  T+3 {s3str(d,3):>7}  T+10 {s[1]:>6.2f}%/{s[2]:>5.1f}%   {ys}")

    # ============ 2. 候选过滤组合排序 ============
    print("\n\n【组合搜索】要求：全样本 T+3>0 且 T+10>0，且 2024/2025/2026 至少两年 T+10>0，n>=150")
    grid = {
        "ma20": [(0.0, 0.01), (0.0, 0.02), (0.0, 0.03)],
        "vr": [(0.6, 0.9), (0.6, 1.0), (0.6, 1.2), (0.6, 2.5)],
        "life": [(0, 0.8), (0, 1.5), (0, 3.0), (0, 99)],
        "day": [(-99, 0), (-99, -1), (-99, 99)],
        "bmkt": [(-9, 0.005), (-9, 0.02), (-9, 99)],
        "age": [(1, 3), (1, 6), (1, 10)],
        "vr20": [(0, 0.9), (0, 1.0), (0, 1.2), (0, 99)],
        "dd": [(-9, -0.07), (-9, -0.05), (-9, 0)],
        "first": [(True, True)],
    }
    colmap = {"ma20": "ma20_buf", "vr": "vr", "life": "life_ext", "day": "day_pc",
              "bmkt": "b_mkt_ret", "age": "age", "vr20": "vr20", "dd": "dd_from_b"}
    res = []
    keys = list(grid)
    for r in (1, 2, 3, 4):
        for combo in itertools.combinations(keys, r):
            m = pd.Series(True, index=sig.index)
            for kk in combo:
                sub = pd.Series(False, index=sig.index)
                for lo, hi in grid[kk]:
                    if kk == "first":
                        sub |= sig.is_first
                    elif kk == "age":
                        sub |= (sig.age >= lo) & (sig.age <= hi)
                    else:
                        c = colmap[kk]
                        sub |= (sig[c] > lo) & (sig[c] <= hi)
                m &= sub
            d = sig[m]
            s3, s10 = st(d, 3), st(d, 10)
            if not s3 or s3[0] < 150:
                continue
            if s3[1] <= 0 or s10[1] <= 0:
                continue
            yrs = d.groupby("year").apply(lambda x: x.ex10.dropna().mean() * 100
                                          if len(x.ex10.dropna()) >= 30 else np.nan)
            yrs = yrs.dropna()
            npos = int((yrs > 0).sum())
            if npos < 2:
                continue
            res.append((combo, s3[0], s3[1], s3[2], s10[1], s10[2], npos, len(yrs),
                        yrs.to_dict(), d.date.nunique(), len(d) / d.date.nunique()))
    res.sort(key=lambda x: -(min(x[2], x[4]) * 3 + min(x[3], x[5]) / 5))
    for c in res[:35]:
        ys = " ".join(f"{k}:{v:+.2f}" for k, v in sorted(c[8].items()))
        print(f"  {'+'.join(c[0]):<44} n={c[1]:>5} T+3 {c[2]:>6.2f}%/{c[3]:>5.1f}% "
              f"T+10 {c[4]:>6.2f}%/{c[5]:>5.1f}% 日均{c[10]:>4.1f} [{ys}]")

    # ============ 3. 最优解在 20261026 的落地效果 ============
    print("\n\n【20261026 落地】")
    d26 = sig[sig.date == "20261026"].copy()
    print(f"裸命中 {len(d26)} 只")
    for combo in [("ma20",), ("ma20", "vr"), ("ma20", "vr", "bmkt"),
                  ("ma20", "vr", "bmkt", "life"), ("ma20", "vr", "bmkt", "life", "first")]:
        m = pd.Series(True, index=d26.index)
        for kk in combo:
            sub = pd.Series(False, index=d26.index)
            for lo, hi in grid[kk]:
                if kk == "first":
                    sub |= d26.is_first
                elif kk == "age":
                    sub |= (d26.age >= lo) & (d26.age <= hi)
                else:
                    c = colmap[kk]
                    sub |= (d26[c] > lo) & (d26[c] <= hi)
            m &= sub
        d = d26[m]
        print(f"  {'+'.join(combo):<40} 命中 {len(d):>2} 只: "
              f"{', '.join(d.code.tolist()) if len(d) else '（空）'}")

    # 引擎实际输出的 18 只 vs 裸 59
    print("\n【引擎 18 只 在裸命中中的落点】")
    eng = ["688608.SH", "002945.SZ", "300817.SZ", "300687.SZ", "688039.SH", "001298.SZ",
           "300276.SZ", "002475.SZ", "300353.SZ", "002988.SZ", "300561.SZ", "300421.SZ",
           "688775.SH", "002896.SZ", "688207.SH", "600698.SH", "688023.SH", "603893.SH"]
    sub = d26[d26.code.isin(eng)]
    print(sub[["code", "name", "b_date", "age", "ma20_buf", "vr", "life_ext", "day_pc",
               "b_mkt_ret"]].to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print(f"\n  引擎 18 只中 ma20_buf<=0.02 的有 {(sub.ma20_buf<=0.02).sum()} 只；"
          f"裸 59 只中 {(d26.ma20_buf<=0.02).sum()} 只")


def s3str(d, k):
    s = st(d, k)
    return f"{s[1]:+.2f}%/{s[2]:.0f}%" if s else "n/a"


if __name__ == "__main__":
    main()
