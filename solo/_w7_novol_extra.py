# -*- coding: utf-8 -*-
"""W7 配对回测的补充分析（区分「改造本身的影响」与「新增信号的质量」）。

关键问题
--------
回测主表把 new 的全量（2304 条）与 old 的全量（228 条）并列，容易误读：
new 的均值被 2076 条「仅 new」的低质量信号稀释。
真正的因果对比应是：
  ① 共有信号（228 条）：同一批 (日期,代码)，两口径都命中 → 改造本身对它们的影响
  ② 仅 new 信号（2076 条）：改造新增的部分 → 这批信号本身的质量（决定是否值得放开）

附加维度
--------
  ③ 市场环境分层（BEAR/RANGE/RECOVERY/BULL）
  ④ 量能分层：按 pull_vol_ratio / break_volr / volr 分档，看量能是否真有单调区分度
  ⑤ 失效位触达率与「触碰失效位后 T+10」的表现
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

SOLO = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(SOLO, "_w7_novol_out")


def stats(g, label=""):
    """统计一组信号的收益/超额/胜率。

    注意：eval_signals.csv 里的 retN / mktN 已经是**百分数**（如 1.23 表示 +1.23%），
    所以这里直接取均值，不再乘 100。
    """
    out = {"label": label, "n": int(len(g))}
    for h in (1, 3, 5, 10):
        r = g["ret%d" % h] if "ret%d" % h in g.columns else pd.Series(dtype=float)
        m = g["mkt%d" % h] if "mkt%d" % h in g.columns else pd.Series(dtype=float)
        if len(r) and r.notna().any():
            out["ret%d" % h] = round(float(r.mean()), 3)
            out["ex%d" % h] = round(float((r - m).mean()), 3)
            out["win%d" % h] = round(float((r.dropna() > 0).mean() * 100), 1)
    if "stop_hit_date" in g.columns:
        out["stop_rate"] = round(float(g["stop_hit_date"].notna().mean() * 100), 1)
    return out


def show(rows, title):
    print("\n%s" % title)
    hdr = "%-34s %5s %8s %8s %9s %8s %8s %9s %8s" % (
        "分组", "n", "T+1", "T+3", "T+3超额", "T+5", "T+10", "T+10超额", "T+3胜率")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print("%-34s %5d %8s %8s %9s %8s %8s %9s %8s" % (
            r["label"], r["n"],
            _f(r.get("ret1")), _f(r.get("ret3")), _f(r.get("ex3")),
            _f(r.get("ret5")), _f(r.get("ret10")), _f(r.get("ex10")), _f(r.get("win3"))))
    print("   （收益/超额单位：%%）")


def _f(v):
    return "—" if v is None else "%+.2f" % v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default=OUT_DIR)
    ap.add_argument("--json", default=os.path.join(OUT_DIR, "eval_extra.json"))
    args = ap.parse_args()
    p_sig = os.path.join(args.outdir, "eval_signals.csv")
    df = pd.read_csv(p_sig)
    key = ["date", "code"]
    o = df[df.caliber == "old"].set_index(key)
    n = df[df.caliber == "new"].set_index(key)
    both = o.index.intersection(n.index)
    only_new = n.index.difference(o.index)
    only_old = o.index.difference(n.index)

    extra = {}
    print("=" * 100)
    print("W7 配对回测补充分析")
    print("=" * 100)
    print("old=%d  new=%d  共有=%d  仅new=%d  仅old=%d"
          % (len(o), len(n), len(both), len(only_new), len(only_old)))

    # ── ① 共有 vs 增量 ──────────────────────────────────────
    print("\n" + "=" * 100)
    print("① 因果拆分：共有信号（改造本身的影响） vs 仅 new 信号（新增部分的质量）")
    print("=" * 100)
    rows = []
    og = o.loc[both].reset_index()
    ng = n.loc[both].reset_index()
    rows.append(stats(og, "共有信号 · old 口径"))
    rows.append(stats(ng, "共有信号 · new 口径"))
    ong = n.loc[only_new].reset_index() if len(only_new) else pd.DataFrame()
    rows.append(stats(ong, "仅 new 信号"))
    if len(only_old):
        rows.append(stats(o.loc[only_old].reset_index(), "仅 old 信号"))
    show(rows, "")
    extra["causal_split"] = rows

    # 共有信号的 state 改变明细
    if len(both):
        chg = og.merge(ng, on=key, suffixes=("_old", "_new"))
        sc = chg[chg["state_old"] != chg["state_new"]]
        print("\n  共有信号中状态改变 %d 条：" % len(sc))
        for _, r in sc.iterrows():
            print("    %s %s  %s → %s" % (r["date"], r["code"], r["state_old"], r["state_new"]))
        extra["state_changed"] = [
            {"date": r["date"], "code": r["code"], "old": r["state_old"], "new": r["state_new"]}
            for _, r in sc.iterrows()]

    # ── ③ 市场环境分层（new 全量） ─────────────────────────
    print("\n" + "=" * 100)
    print("③ 市场环境分层")
    print("=" * 100)
    rows = []
    ng_all = df[df.caliber == "new"]
    for rg, g in ng_all.groupby("regime"):
        r = stats(g, "new · %s" % rg)
        r["n_old_same"] = int((df[(df.caliber == "old") & (df.regime == rg)]).shape[0])
        rows.append(r)
    show(rows, "")
    extra["regime"] = rows

    # ── ④ 量能分层：检验量能是否真有单调区分度 ───────────────
    print("\n" + "=" * 100)
    print("④ 量能单调性检验（在 new 全量信号上分档，看超额是否单调）")
    print("=" * 100)
    rows = []
    # 类型A：按止跌日量比 volr 分档
    a = ng_all[ng_all.state == "T0_PULLBACK_HOLD"].copy()
    if len(a):
        a["volr_bin"] = pd.cut(a.volr, [-0.01, 0.4, 0.55, 0.8, 1.2, 99])
        for b, g in a.groupby("volr_bin", observed=True):
            rows.append(stats(g, "类型A · 止跌日量比 %s" % b))
    # 类型A：按回踩段均量/T0量 分档
    if len(a):
        a["pvr_bin"] = pd.cut(a.pull_vol_ratio, [-0.01, 0.3, 0.5, 0.7, 1.0, 99])
        for b, g in a.groupby("pvr_bin", observed=True):
            rows.append(stats(g, "类型A · 回踩量比 %s" % b))
    # 类型B：按突破日量比分档
    b = ng_all[ng_all.state == "T0_REBREAK"].copy()
    if len(b):
        b["bvr_bin"] = pd.cut(b.break_volr, [-0.01, 1.2, 1.8, 2.2, 3.0, 99])
        for bn, g in b.groupby("bvr_bin", observed=True):
            rows.append(stats(g, "类型B · 突破量比 %s" % bn))
    show(rows, "")
    extra["volume_buckets"] = rows

    # ── ⑤ 失效位触达 ────────────────────────────────────────
    print("\n" + "=" * 100)
    print("⑤ 失效位触达（10 日内）")
    print("=" * 100)
    rows = []
    rows.append(stats(og, "共有信号 · old"))
    rows.append(stats(ng, "共有信号 · new"))
    rows.append(stats(ong, "仅 new 信号"))
    show(rows, "")
    extra["stop"] = rows

    # ── ⑥ 增量信号若加闸：能否救回期望？ ────────────────────
    print("\n" + "=" * 100)
    print("⑥ 反事实：给「仅 new」加不同闸门后的表现（找可用的替代约束）")
    print("=" * 100)
    rows = []
    if len(ong):
        cand = ong.copy()
        for name, mask in (
            ("全部仅 new", np.ones(len(cand), dtype=bool)),
            ("量比≤0.55（真缩量止跌）", cand.volr <= 0.55),
            ("量比≤0.60 且 回踩量比≤0.50", (cand.volr <= 0.60) & (cand.pull_vol_ratio <= 0.50)),
            ("回踩量比≤0.50", cand.pull_vol_ratio <= 0.50),
            ("突破量比≥2.20（类型B 放量确认）",
             ((cand.state == "T0_REBREAK") & (cand.break_volr >= 2.20)) |
             (cand.state == "T0_PULLBACK_HOLD")),
            ("市场非 BEAR", cand.regime != "BEAR"),
            ("市场非 BEAR 且 量比≤0.55", (cand.regime != "BEAR") & (cand.volr <= 0.55)),
            ("base_score≥55", cand.base_score >= 55),
            ("base_score≥65", cand.base_score >= 65),
            ("base_score≥75", cand.base_score >= 75),
        ):
            g = cand[mask]
            if len(g) == 0:
                continue
            rows.append(stats(g, name))
    show(rows, "")
    extra["counterfactual"] = rows

    # ── ⑦ old 口径自身的量能单调性（对照） ───────────────────
    print("\n" + "=" * 100)
    print("⑦ 对照：old 口径 228 条信号的量比分布与表现")
    print("=" * 100)
    rows = []
    og_all = df[df.caliber == "old"].copy()
    if len(og_all):
        rows.append(stats(og_all, "old 全量"))
        for stt, g in og_all.groupby("state"):
            rows.append(stats(g, "old · %s" % stt))
        oa = og_all[og_all.state == "T0_PULLBACK_HOLD"].copy()
        if len(oa):
            oa["volr_bin"] = pd.cut(oa.volr, [-0.01, 0.4, 0.55, 0.8, 1.2, 99])
            for bn, g in oa.groupby("volr_bin", observed=True):
                rows.append(stats(g, "old 类型A · 量比 %s" % bn))
    show(rows, "")
    extra["old_volume"] = rows

    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(extra, fh, ensure_ascii=False, indent=2)
    print("\n[extra] 输出：%s" % args.json)


if __name__ == "__main__":
    main()
