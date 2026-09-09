# -*- coding: utf-8 -*-
"""网格结果深挖：尾部风险+配对止损分析+分年度+执行现实性 → v2 冻结依据（只读分析，不改规则）"""
import os
import numpy as np
import pandas as pd

SOLO = os.path.dirname(os.path.abspath(__file__))
G = pd.read_csv(os.path.join(SOLO, "report_daily/double_rule_grid.csv"),
                dtype={"code": str, "date": str})
G["year"] = G["year"].astype(str)
f = G[G.filled == 1].copy()
t1 = f[f.tier == "T1"]
t2 = f[f.tier == "T2"]

CAND = [("E0_v1", "S0_v1"), ("E1_next", "S_none"), ("E1_next", "S20"),
        ("E1_next", "S30"), ("E1_next", "S35"), ("E0_v1", "S20"), ("E3_pb5", "S_none")]


def stats(d):
    net = d.net
    p5 = net.quantile(.05) * 100
    cvar = net[net <= net.quantile(.05)].mean() * 100
    return (len(d), net.median() * 100, net.mean() * 100, p5, net.min() * 100, cvar,
            (net > 0).mean() * 100, (net <= -.20).mean() * 100, (net <= -.30).mean() * 100,
            d.hold.median())


HEAD = ["| 规则 | 笔数 | 中位% | 均值% | p5% | 最差% | CVaR5% | 胜率% | ≤-20% | ≤-30% | 持有中位 |",
        "|---|---|---|---|---|---|---|---|---|---|---|"]

L = ["# 网格深挖：v2 冻结依据（样本内，只读分析）", ""]

for tier, name in [(t1, "一、TIER1 候选规则全指标"), (t2, "二、TIER2 同表（对照）")]:
    L += [f"## {name}", ""] + HEAD
    for e, s in CAND:
        d = tier[(tier.entry == e) & (tier.stop == s)]
        n, med, mean, p5, mn, cvar, win, l20, l30, hold = stats(d)
        L.append(f"| {e}+{s} | {n} | {med:.1f} | {mean:.1f} | {p5:.1f} | {mn:.1f} | {cvar:.1f} "
                 f"| {win:.0f} | {l20:.1f} | {l30:.1f} | {hold:.0f} |")
    L.append("")

L += ["## 三、配对分析（TIER1, E1_next：-20%止损 vs 无止损，同事件对照）", ""]
p = t1[t1.entry == "E1_next"].pivot_table(index=["code", "date"], columns="stop",
                                          values="net", aggfunc="first").dropna(subset=["S20", "S_none"])
a, b = p["S20"], p["S_none"]
stopped = a <= -0.19
rec = stopped & (b > 0)
saved = stopped & (b <= 0)
L += [f"- 配对事件 {len(p)}",
      f"- S20 被止损 {int(stopped.sum())} 笔（占 {stopped.mean()*100:.0f}%）",
      f"  - 假止损（无止损口径最终盈利，砍在恢复前）{int(rec.sum())} 笔，占被止损的 {rec.sum()/max(stopped.sum(),1)*100:.0f}%；这些笔无止损口径中位 {b[rec].median()*100:.1f}% / 均值 {b[rec].mean()*100:.1f}%",
      f"  - 真保护（无止损口径最终亏损）{int(saved.sum())} 笔，无止损口径均值 {b[saved].mean()*100:.1f}%（最差 {b[saved].min()*100:.1f}%），止损封顶在约-20%",
      f"- 净效应：S20 均值 {a.mean()*100:.1f}% vs S_none {b.mean()*100:.1f}%（无止损多 {(b.mean()-a.mean())*100:.1f}pp）；中位 {a.median()*100:.1f}% vs {b.median()*100:.1f}%"]

L += ["", "## 四、分年度一致性（TIER1：2024 与 2025 各自 中位% / 胜率% / 笔数）", "",
      "| 规则 | 2024中位 | 2024胜率 | 2024笔数 | 2025中位 | 2025胜率 | 2025笔数 |",
      "|---|---|---|---|---|---|---|"]
for e, s in CAND:
    d = t1[(t1.entry == e) & (t1.stop == s)]
    row = [f"{e}+{s}"]
    for y in ["2024", "2025"]:
        dy = d[d.year == y]
        row += [f"{dy.net.median()*100:.1f}", f"{(dy.net > 0).mean()*100:.0f}", str(len(dy))]
    L.append("| " + " | ".join(row) + " |")

L += ["", "## 五、止损执行方式（TIER1：stop_gap=开盘跳空穿越按开盘价成交，滑点更大）", "",
      "| 规则 | stop_hit% | stop_gap% | gap笔均额外损失pp |", "|---|---|---|---|"]
for e, s in [("E0_v1", "S0_v1"), ("E1_next", "S20"), ("E1_next", "S30"), ("E1_next", "S35")]:
    d = t1[(t1.entry == e) & (t1.stop == s)]
    gap = d[d.exit_reason == "stop_gap"]
    extra = (gap.net.mean() * 100 + float(s[1:]) if s.startswith("S") and s[1:].isdigit() else gap.net.mean() * 100) if len(gap) else 0.0
    L.append(f"| {e}+{s} | {(d.exit_reason == 'stop_hit').mean()*100:.0f} | {(d.exit_reason == 'stop_gap').mean()*100:.0f} | {extra:.1f} |")

L += ["", "## 六、成本压力（假设成交成本比回测口径多1%）", "",
      "| 规则 | 中位%-1pp | 均值%-1pp | 仍为正? |", "|---|---|---|---|"]
for e, s in CAND:
    d = t1[(t1.entry == e) & (t1.stop == s)]
    L.append(f"| {e}+{s} | {(d.net - .01).median()*100:.1f} | {(d.net - .01).mean()*100:.1f} | {'是' if (d.net - .01).median() > 0 else '否'} |")

out = os.path.join(SOLO, "report_daily/double_rule_grid_deep.md")
with open(out, "w", encoding="utf-8") as fh:
    fh.write("\n".join(L))
print("\n".join(L))
