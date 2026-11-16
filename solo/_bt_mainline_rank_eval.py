# -*- coding: utf-8 -*-
"""主线排序口径回放对比：excess20（旧） vs short_term（新）。

口径说明
--------
直接调用 etf_mainline.detect_mainlines 对每个交易日选 top1 主线，
用「未来 20 日相对等权行业 ETF 基准的超额」作事实标签
（沿用 _bt_etf_panel_eval.py / V10.3 调参的权威口径）。
前后半段分割验证，避免只看全样本过拟合。

用法：python _bt_mainline_rank_eval.py
产物：_bt_out/mainline_rank_eval_daily.csv（逐日明细，便于复查）
"""
import os
import sys
import copy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import etf_mainline as em

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "_bt_out")
os.makedirs(OUT, exist_ok=True)

base_cfg = em.load_config()
panel = em.load_etf_panel()
series = em.compute_industry_series(panel, base_cfg)
idx_all = sorted(panel["trade_date"].unique())
pos_of = {d: i for i, d in enumerate(idx_all)}

# 统一基准：无缺失的行业等权（与 _bt_etf_panel_eval.py 一致）
nav_mat = {ind: s.set_index("trade_date")["nav"].reindex(idx_all).values
           for ind, s in series.items()}
usable = [v for v in nav_mat.values() if not np.isnan(v).any()]
bench = np.mean(usable, axis=0)
print(f"行业 {len(series)} 个 / 基准取 {len(usable)} 个无缺失行业 / 交易日 {len(idx_all)} 天")

# 留出未来 20 日 + 至少 60 日历史（ma60_window）
last = len(idx_all) - 21
dates = [d for d in idx_all[60:last] if str(d) >= "20260101"]
print(f"评估区间 {dates[0]} ~ {dates[-1]}（{len(dates)} 个交易日）")

modes = ["excess20", "short_term"]
rows = []
for mode in modes:
    cfg = copy.deepcopy(base_cfg)
    cfg["relative_strength"]["rank_mode"] = mode
    for k, d in enumerate(dates):
        res = em.detect_mainlines(d, cfg, panel)
        inds = res.get("industries") or []
        ind = inds[0] if inds else None
        i = pos_of[d]
        fwd_ex = None
        if ind and i + 20 < len(bench):
            nv = nav_mat[ind]
            if not np.isnan(nv[i]) and not np.isnan(nv[i + 20]):
                fwd_ex = ((nv[i + 20] / nv[i] - 1) * 100
                          - (bench[i + 20] / bench[i] - 1) * 100)
        rows.append({"mode": mode, "d": str(d), "ind": ind, "fwd_ex": fwd_ex})
    print(f"  {mode} 完成 {len(dates)} 天")

df = pd.DataFrame(rows)
df.to_csv(os.path.join(OUT, "mainline_rank_eval_daily.csv"),
          index=False, encoding="utf-8-sig")


def report(name, sub):
    print(f"\n=== {name} ===")
    for mode in modes:
        s = sub[sub["mode"] == mode]
        v = s[s["fwd_ex"].notna()]
        covered = 100 * len(s[s["ind"].notna()]) / max(1, len(s))
        if not len(v):
            print(f"  {mode:12s} 无样本")
            continue
        hit = {t: 100 * float((v.fwd_ex > t).mean()) for t in (3, 5, 8, 10)}
        a = s["ind"].tolist()
        pairs = [(a[k], a[k + 1]) for k in range(len(a) - 1)
                 if a[k] and a[k + 1]]
        switch = 100 * sum(1 for x, y in pairs if x != y) / max(1, len(pairs))
        dist = s[s["ind"].notna()]["ind"].value_counts(normalize=True)
        top_share = 100 * float(dist.max()) if len(dist) else 0.0
        print(f"  {mode:12s} 有主线 {covered:5.1f}%  样本 {len(v):3d}  "
              f"均值 {v.fwd_ex.mean():+6.2f}  中位 {v.fwd_ex.median():+6.2f}  "
              f">3/5/8/10% {hit[3]:4.1f}/{hit[5]:4.1f}/{hit[8]:4.1f}/{hit[10]:4.1f}  "
              f"切换率 {switch:4.1f}%  单行业占比 {top_share:4.1f}%")


mid = len(dates) // 2
d1 = set(str(x) for x in dates[:mid])
d2 = set(str(x) for x in dates[mid:])
report("全样本", df)
report(f"训练段 {dates[0]}~{dates[mid - 1]}", df[df.d.isin(d1)])
report(f"验证段 {dates[mid]}~{dates[-1]}", df[df.d.isin(d2)])

print("\n=== 各口径 top1 行业分布（全样本，前 6）===")
for mode in modes:
    s = df[(df["mode"] == mode) & (df["ind"].notna())]["ind"].value_counts()
    print(f"  {mode}: " + ", ".join(f"{k} {v}天" for k, v in s.head(6).items()))

# ------------------------------------------------------------------
# 权重网格：一次收集每日横截面，离线比较多组权重
# （ex1/ex5/ex20/ex60 与权重无关，无需反复调用判定函数）
# 纪律：训练段选参、验证段检验；两段都不劣于旧口径才考虑启用。
# ------------------------------------------------------------------
print("\n=== 权重网格（训练段选参 / 验证段检验）===")

collect = []
cfg = copy.deepcopy(base_cfg)
cfg["relative_strength"]["rank_mode"] = "excess20"   # 只需 details 的 ex 值
for d in dates:
    det = em.detect_mainlines(d, cfg, panel)["details"]
    collect.append((str(d), pd.DataFrame([
        {"ind": ind, "ex1": x.get("excess1"), "ex5": x.get("excess5"),
         "ex20": x.get("excess20"), "ex60": x.get("excess60"),
         "passed": bool(x.get("passed")), "days_strong": x.get("days_strong")}
        for ind, x in det.items()])))

fwd_map = {}
for d in dates:
    i = pos_of[d]
    if i + 20 >= len(bench):
        continue
    bf = (bench[i + 20] / bench[i] - 1) * 100
    for ind, nv in nav_mat.items():
        if not np.isnan(nv[i]) and not np.isnan(nv[i + 20]):
            fwd_map[(str(d), ind)] = (nv[i + 20] / nv[i] - 1) * 100 - bf

GRID = [
    ("旧口径 ex20 only   ", (0.00, 0.00, 1.00, 0.00)),
    ("当前 35/35/20/10   ", (0.35, 0.35, 0.20, 0.10)),
    ("均衡 25/25/25/25   ", (0.25, 0.25, 0.25, 0.25)),
    ("偏长 10/20/40/30   ", (0.10, 0.20, 0.40, 0.30)),
    ("偏长 15/25/35/25   ", (0.15, 0.25, 0.35, 0.25)),
    ("5+20 为主 10/35/45/10", (0.10, 0.35, 0.45, 0.10)),
    ("20+60 双周期       ", (0.00, 0.00, 0.60, 0.40)),
    ("偏短 50/30/15/05   ", (0.50, 0.30, 0.15, 0.05)),
]

top_n = int(base_cfg["relative_strength"]["rank_top_n"])


def grid_eval(weights):
    out = []
    for d, day in collect:
        raw = day[["ex1", "ex5", "ex20", "ex60"]].astype(float).fillna(-999)
        score = raw.rank(pct=True).values.dot(np.array(weights))
        tmp = day.assign(score=score).sort_values(
            "score", ascending=False).reset_index(drop=True)
        cand = tmp.head(top_n)
        cand = cand[cand.passed]
        # min_days_strong 门禁：仅在有值时生效（excess20 模式下非前 8 行业无值）
        cand = cand[cand.days_strong.isna() | (cand.days_strong >= 2)]
        ind = cand.iloc[0]["ind"] if len(cand) else None
        out.append((d, ind, fwd_map.get((d, ind)) if ind else None))
    return pd.DataFrame(out, columns=["d", "ind", "fwd_ex"])


def summarize(g):
    a = g[g.d.isin(d1)].fwd_ex.dropna()
    b = g[g.d.isin(d2)].fwd_ex.dropna()
    inds = g["ind"].tolist()
    pairs = [(inds[k], inds[k + 1]) for k in range(len(inds) - 1)
             if inds[k] and inds[k + 1]]
    sw = 100 * sum(1 for x, y in pairs if x != y) / max(1, len(pairs))
    return (100 * float((a > 5).mean()), 100 * float((b > 5).mean()),
            100 * float((b > 8).mean()), float(b.mean()), sw)


print(f"{'组合':<24}{'训练>5%':>9}{'验证>5%':>9}{'验证>8%':>9}{'验证均值':>10}{'切换率':>8}")
base_tr, base_va, base_va8, base_mean, base_sw = summarize(
    df[df["mode"] == "excess20"].copy())
print(f"{'真实函数 excess20':<24}{base_tr:>9.1f}{base_va:>9.1f}{base_va8:>9.1f}"
      f"{base_mean:>10.2f}{base_sw:>8.1f}")
grid_res = []
for name, w in GRID:
    g = grid_eval(w)
    tr, va, va8, mean, sw = summarize(g)
    grid_res.append((name, w, tr, va, va8, mean, sw))
    print(f"{name:<24}{tr:>9.1f}{va:>9.1f}{va8:>9.1f}{mean:>10.2f}{sw:>8.1f}")

print("\n=== 判定（训练段不劣 且 验证段 >5% 与 >8% 均不劣于旧口径）===")
ok = [r for r in grid_res
      if r[2] >= base_tr and r[3] >= base_va and r[4] >= base_va8]
if ok:
    for r in ok:
        print(f"  通过: {r[0].strip()}  权重={r[1]}")
else:
    print("  无组合同时满足。按分割验证纪律，rank_mode 保持 excess20（旧口径），")
    print("  short_term 仅作为可切换开关保留，不作为默认。")
