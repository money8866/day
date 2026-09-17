# -*- coding: utf-8 -*-
"""专项回归：§二十 反追涨（Sample A/B）+ §十四 晚期伪启动。before / after 对照。"""
import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ACT = {"EARLY", "EMERGING", "CONFIRMING", "STRONG"}
# 上一轮审计 Sample A/B 的「强主题」口径已反解确认为 {EMERGING, CONFIRMING, STRONG}
# （用该口径 before nA=221 / nB=681，与审计报告完全一致），故对照必须用同一口径。
STRONG_ACT = {"EMERGING", "CONFIRMING", "STRONG"}
COLS = ["trade_date", "ts_code", "sector_id", "theme_phase", "theme_role", "ret_5", "ret_20",
        "membership_type", "membership_confidence",
        "distance_ma20", "volume_ratio_5", "extension_penalty", "structure_quality_precheck",
        "candidate_status", "candidate_score", "candidate_type", "theme_opportunity_score",
        "diffusion_pattern", "diffusion_stage"]


def load(p, extra=()):
    d = pd.read_csv(p, dtype={"trade_date": str}, low_memory=False,
                    usecols=lambda c: c in set(COLS) | set(extra))
    for c in ("ret_5", "ret_20", "distance_ma20", "volume_ratio_5", "extension_penalty",
              "structure_quality_precheck", "candidate_score", "theme_opportunity_score"):
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    return d


def sampleA(d):
    return d[(d["theme_phase"].isin(STRONG_ACT)) & (d["ret_5"] >= 0.30) & (d["distance_ma20"] >= 0.30)]


def sampleB(d):
    return d[(d["theme_phase"].isin(STRONG_ACT)) & (d["membership_type"] == "CORE")
             & d["ret_5"].between(0.02, 0.08) & d["distance_ma20"].between(0.0, 0.10)
             & d["volume_ratio_5"].between(1.0, 2.0)]


def rep(tag, s):
    if not len(s):
        print(f"  {tag:8s} n=0")
        return
    c = s["candidate_status"].eq("CANDIDATE").mean() if "candidate_status" in s else float("nan")
    print(f"  {tag:8s} n={len(s):5d} score_mean={s['candidate_score'].mean():7.2f} "
          f"median={s['candidate_score'].median():7.2f} CANDIDATE={c:6.1%} "
          f"ext_pen={s['extension_penalty'].mean():6.2f} struct={s['structure_quality_precheck'].mean():6.2f} "
          f"opp={s['theme_opportunity_score'].mean():6.2f}")


print("=" * 78)
print("Test A 反追涨（Sample A 极端动量 vs Sample B 健康）")
print("=" * 78)
for tag, path in [("BEFORE", ROOT / "_fix_baseline" / "daily_before.csv"),
                  ("AFTER ", ROOT / "data" / "sector_stock_candidate_daily.csv")]:
    d = load(path)
    print(f"[{tag}] 全样本 n={len(d)}  active相位 n={int(d['theme_phase'].isin(ACT).sum())}")
    rep("A", sampleA(d))
    rep("B", sampleB(d))
    a, b = sampleA(d), sampleB(d)
    print(f"  → 差(A-B)={a['candidate_score'].mean() - b['candidate_score'].mean():+.2f}；"
          f"A 中 CANDIDATE={int(a['candidate_status'].eq('CANDIDATE').sum())}；"
          f"ret_5>=25% 行 CANDIDATE={int(d[d['ret_5'] >= 0.25]['candidate_status'].eq('CANDIDATE').sum())}；"
          f"dist_ma20>=30% 行 CANDIDATE={int(d[d['distance_ma20'] >= 0.30]['candidate_status'].eq('CANDIDATE').sum())}")

print()
print("=" * 78)
print("Test B 晚期伪启动（theme_phase ∈ {EARLY, EMERGING}，主题日口径）")
print("=" * 78)
p = pd.read_csv(ROOT / "data" / "sector_seos_daily.csv", dtype={"trade_date": str}, low_memory=False)
print(f"主题面板：{len(p)} 行 / {p['trade_date'].nunique()} 日 / {p['sector_id'].nunique()} 主题")
e = p[p["theme_phase"].isin(["EARLY", "EMERGING"])].copy()
for c in ("ew_ret_5", "ew_ret_20", "top5_concentration", "core_breadth_delta_5"):
    if c in e.columns:
        e[c] = pd.to_numeric(e[c], errors="coerce")
print(f"EARLY/EMERGING 主题日 n={len(e)}（上一轮审计基线 n=352）")
c1 = e["ew_ret_5"] >= 0.15
c2 = e["ew_ret_20"] >= 0.25
c3 = e["top5_concentration"] >= 0.60
c4 = e["core_breadth_delta_5"] <= 0.0
print(f"  条件1 5D>=15%        : {int(c1.sum())}")
print(f"  条件2 20D>=25%       : {int(c2.sum())}")
print(f"  条件3 集中度>=0.60   : {int(c3.sum())}")
print(f"  条件4 核心广度未改善 : {int(c4.sum())}")
print(f"  ★ 四条件同时命中     : {int((c1 & c2 & c3 & c4).sum())}（要求 0）")
print(f"  ★ 三条件(1&2&3)同时命中: {int((c1 & c2 & c3).sum())}（要求 0）")
print(f"  top5_concentration>=0.60 样本数: {int(c3.sum())}")
print(f"  含 FAILED_EXPANSION / FAILED_DIFFUSION 占比: "
      f"{float(p[p['theme_phase'].isin(['EARLY','EMERGING'])]['theme_phase'].notna().mean()):.2f}")

# 新门槛对该样本的作用
d = load(ROOT / "data" / "sector_stock_candidate_daily.csv",
         extra=("calibrated_opportunity_base", "calibration_status"))
ee = d[d["theme_phase"].isin(["EARLY", "EMERGING"])]
cb = pd.to_numeric(ee["calibrated_opportunity_base"], errors="coerce")
print(f"\nEARLY/EMERGING 个股×主题行 n={len(ee)}；calibrated_base 非空 n={int(cb.notna().sum())}；"
      f"<55 的行数={int((cb < 55).sum())}（这些行无法通过 THEME_PULLBACK 主题机会门槛）")
print("calibration_status：" + str(ee["calibration_status"].value_counts().to_dict()))
