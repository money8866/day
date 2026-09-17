# -*- coding: utf-8 -*-
"""CANDIDATE 6541 -> 5499 的逐行归因分解（before CANDIDATE 中降级行的原因分类）。"""
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
KEY = ["trade_date", "ts_code", "sector_id"]
WANT = KEY + ["candidate_status", "candidate_score", "theme_phase", "membership_type",
              "fundamental_quality", "calibrated_opportunity_base", "cross_theme_pollution_flag",
              "is_context_theme", "candidate_theme_count", "cross_theme_score_penalty",
              "calibration_status", "candidate_type"]


def L(p):
    return pd.read_csv(p, dtype={"trade_date": str}, low_memory=False,
                       usecols=lambda c: c in set(WANT))


b = L(ROOT / "_fix_baseline" / "daily_before.csv")
a = L(ROOT / "data" / "sector_stock_candidate_daily.csv")
cfg = json.loads((ROOT / "config" / "sector_stock_candidate_config.json").read_text(encoding="utf-8"))
print("status_thresholds =", json.dumps(cfg["status_thresholds"], ensure_ascii=False))
print("selection_limits  =", json.dumps(cfg["selection_limits"], ensure_ascii=False))
print("pullback gate     =", cfg["pullback_rules"]["theme_opportunity_gate"][
    "min_calibrated_opportunity_base"], " fundamental_min =", cfg["pullback_rules"]["fundamental_min"])
print("fundamental ramp  =", json.dumps(cfg["fundamental_quality"], ensure_ascii=False)[:400])

print("\nBEFORE status:", b["candidate_status"].value_counts().to_dict())
print("AFTER  status:", a["candidate_status"].value_counts().to_dict())

bb = b[b["candidate_status"] == "CANDIDATE"][
    ["trade_date", "ts_code", "sector_id", "candidate_score", "theme_phase",
     "membership_type", "fundamental_quality"]].rename(
    columns={"candidate_score": "score_b", "theme_phase": "phase_b",
             "membership_type": "mtype_b", "fundamental_quality": "fq_b"})
aa = a[["trade_date", "ts_code", "sector_id", "candidate_status", "candidate_score",
        "calibrated_opportunity_base", "cross_theme_pollution_flag", "is_context_theme",
        "candidate_theme_count", "cross_theme_score_penalty", "calibration_status",
        "candidate_type"]].rename(columns={"candidate_score": "score_a",
                                           "candidate_status": "status_a"})
m = bb.merge(aa, on=KEY, how="left")
lost = m[m["status_a"] != "CANDIDATE"].copy()
keep = m[m["status_a"] == "CANDIDATE"]
print(f"\nBEFORE CANDIDATE 行 n={len(m)}；AFTER 仍 CANDIDATE n={len(keep)}；"
      f"AFTER 降级 n={len(lost)}")
print(f"  降级后状态分布: {lost['status_a'].value_counts().to_dict()}")

fq = pd.to_numeric(lost["fq_b"], errors="coerce")
cb = pd.to_numeric(lost["calibrated_opportunity_base"], errors="coerce")
pen = pd.to_numeric(lost["cross_theme_score_penalty"], errors="coerce").fillna(0.0)
r = pd.Series("Z_OTHER_SCORE_DRIFT", index=lost.index, dtype=object)
r = r.mask(pen > 0, "E_MULTI_THEME_CROWDING_PENALTY")
r = r.mask(fq < 45.0, "D_FUNDAMENTAL_MIN_40_TO_45")
r = r.mask(cb < 55.0, "C_OPPORTUNITY_GATE_LT55")
r = r.mask(lost["cross_theme_pollution_flag"].eq("HIGH"), "B_HIGH_POLLUTION_CAP")
r = r.mask(lost["is_context_theme"].fillna(False).astype(bool), "A_CONTEXT_THEME_CAP")
lost["reason"] = r
print("\n### 归因（互斥，按 A>B>C>D>E>Z 优先级）")
vc = lost["reason"].value_counts()
for k, v in vc.items():
    print(f"  {k}: {int(v)} ({v/len(lost):.1%})")

print("\n### 各原因的 before score 均值 / after score 均值 / after 状态")
g = lost.groupby("reason").agg(
    n=("reason", "size"), score_b=("score_b", "mean"), score_a=("score_a", "mean"))
print(g.to_string(float_format=lambda x: f"{x:.3f}"))

print("\n### 专项：AFTER CANDIDATE 的准入画像（用于确认未靠放宽门槛扩量）")
c = a[a["candidate_status"] == "CANDIDATE"]
print(f"  n={len(c)}")
print(f"  calibrated_base min={pd.to_numeric(c['calibrated_opportunity_base'],errors='coerce').min():.4f} "
      f"mean={pd.to_numeric(c['calibrated_opportunity_base'],errors='coerce').mean():.4f}")
print(f"  fundamental_quality min={pd.to_numeric(c['fundamental_quality'],errors='coerce').min():.4f}")
print(f"  candidate_theme_count max={int(pd.to_numeric(c['candidate_theme_count'],errors='coerce').max())}")
print(f"  is_context_theme=True n={int(c['is_context_theme'].fillna(False).astype(bool).sum())}")
print(f"  pollution_flag: {c['cross_theme_pollution_flag'].value_counts().to_dict()}")
print(f"  calibration_status: {c['calibration_status'].value_counts().to_dict()}")
