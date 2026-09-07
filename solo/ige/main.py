# -*- coding: utf-8 -*-
"""
IGE v1.2 CLI
用法：
    python -X utf8 -m ige.main --date 20260901
    python -X utf8 -m ige.main            # 使用 config.SNAPSHOT_DATE
    python -X utf8 -m ige.main --top 15   # 展示行业 Top N
"""
from __future__ import annotations

import argparse
import logging
import sys

import numpy as np
import pandas as pd

from .config import SNAPSHOT_DATE
from .engine import run


def _num(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _setup_log() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S", stream=sys.stdout)


def _summary(ind: pd.DataFrame, st: pd.DataFrame, top: int) -> None:
    print()
    print("═" * 78)
    print(f"IGE v1.2 行业级摘要　L3 行业 {len(ind)} 个"
          "（分层 rank_tier→IGE_ADJ→IGE_MOM，不再简单 IGE_ADJ 降序）")
    print("═" * 78)
    grade_cols = pd.Series(ind["industry_elasticity_grade"])
    g = grade_cols.value_counts()
    for name in ("EXTREME_ELASTICITY", "HIGH_ELASTICITY", "MEDIUM_HIGH_ELASTICITY",
                 "NEUTRAL_ELASTICITY", "LOW_ELASTICITY", "VERY_LOW_ELASTICITY"):
        if name in g.index:
            print(f"  {name:<26} {int(g[name]):>5} 家")
    rc = int(ind["rocket_eligible"].sum())
    blk = int(ind["rocket_block"].sum())
    trap = int((ind["industry_elasticity_state"] == "ELASTICITY_TRAP").sum())
    core = int(ind["t120_rocket_core"].sum())
    print(f"  ROCKET_ELIGIBLE(行业参照)  {rc:>5} 家")
    print(f"  ROCKET_BLOCK(行业IGE<60)   {blk:>5} 家")
    print(f"  ELASTICITY_TRAP(禁止进火箭) {trap:>4} 家")
    print(f"  T120_ROCKET_CORE(最严格门槛){core:>4} 家")
    src = int(st["rocket_eligible"].sum())
    sblk = int(st["rocket_block"].sum())
    scor = int(st["t120_rocket_core"].sum())
    print(f"  个股 rocket_eligible      {src:>5} 只 / rocket_block {sblk} 只"
          f" / rocket_core {scor} 只")
    ac = int(st["acceleration_confirm"].sum())
    print(f"  ACCELERATION_CONFIRM       {ac:>5} 只")
    vt = st["industry_elasticity_type"].value_counts()
    print("  弹性类型：", "  ".join(f"{k}{int(v)}" for k, v in vt.items()))

    # v1.2 分层分布（行业档）
    tier_g = ind["rank_tier"].value_counts().sort_index()
    tier_txt = "  ".join(f"T{t}{int(tier_g[t])}家"
                         for t in range(0, 6) if t in tier_g.index)
    print(f"  分层分布(行业档)：{tier_txt}")

    # 封顶核查：igemix 与 IGE_ADJ 各自到顶的行业数（§13）
    def _n100(s: pd.Series, thr: float = 99.5) -> int:
        return int((_num(s) >= thr).sum())

    print()
    print("─ 封顶核查：")
    print(f"  ige_mix ≥90/≥99.5：{_n100(ind['ige_mix'], 90):>4} / {_n100(ind['ige_mix']):>4} 家"
          f"　IGE_ADJ ≥90/≥99.5：{_n100(ind['ige_adj'], 90):>4} / {_n100(ind['ige_adj']):>4} 家")
    print(f"  ige_mix 分位 p10/p50/p90 = "
          f"{_num(ind['ige_mix']).quantile(.1):.1f} / "
          f"{_num(ind['ige_mix']).quantile(.5):.1f} / "
          f"{_num(ind['ige_mix']).quantile(.9):.1f}")

    # v1.2 新变量核查（边界/陷阱强制/有效弹性单调性）
    print()
    print("─ v1.2 强制检查：")
    for col, lo, hi in (("ige_adj", 0, 100), ("ige_mom", 0, 100),
                        ("ige_persistence", 0, 100), ("ige_effective", 0, 100)):
        v = _num(ind[col])
        bad = int(((v < lo) | (v > hi)).sum())
        okc = int(v.notna().sum())
        print(f"  {col:<18} 有效 {okc:>4}  越界 {bad:>3}")
    viol = int((_num(ind["ige_effective"]) > _num(ind["ige_adj"]) + 1e-9).sum())
    print(f"  IGE_EFFECTIVE 超过 IGE_ADJ（应≤0）：{viol:>3}")
    trap_core = int((st["t120_rocket_core"].fillna(False)
                     & (st["industry_elasticity_state"] == "ELASTICITY_TRAP")).sum())
    print(f"  ELASTICITY_TRAP 却进 T120_ROCKET_CORE（应=0）：{trap_core:>3}")
    nmis = int(((st["ige_opportunity_type"] == "STRUCTURAL_ELASTICITY")
                & ~st["industry_elasticity_type"].isin(
                    ["TECHNOLOGY", "GROWTH"])).sum())
    print(f"  非TECH/GROWTH 标为 STRUCTURAL_ELASTICITY（应=0）：{nmis:>3}")
    ot = st["ige_opportunity_type"].value_counts()
    print("  机会类型：", "  ".join(f"{k}{int(v)}" for k, v in ot.items()))

    print()
    print("─" * 78)
    print("Top 行业（分层 1~3 · 优先 MOM 加速 / 高持续性；含 v1.2 新列）")
    print("─" * 78)
    cols = ["sw_l1", "sw_l2", "sw_l3", "ige_adj", "ige_mom", "ige_persistence",
            "ige_effective", "ige_opportunity_type",
            "industry_lifecycle", "industry_elasticity_state",
            "industry_sample_n", "rank_tier"]
    cols = [c for c in cols if c in ind.columns]
    show = ind.head(top)[cols].copy()
    for c in ("ige_adj", "ige_mom", "ige_persistence", "ige_effective"):
        show[c] = show[c].round(1)
    print(show.to_string(index=False))


def main() -> None:
    ap = argparse.ArgumentParser(description="IGE v1.2 行业增长弹性因子")
    ap.add_argument("--date", default=SNAPSHOT_DATE, help="快照日 YYYYMMDD")
    ap.add_argument("--top", type=int, default=15, help="摘要展示行数")
    args = ap.parse_args()
    _setup_log()

    res = run(args.date)
    st, ind = res["stocks"], res["industry"]
    if ind.empty:
        print("无可用行业结果。")
        return
    _summary(ind, st, args.top)

    # 覆盖率核对
    n_ok = int(st["industry_elasticity"].notna().sum())
    print()
    print(f"─ 覆盖率：股票 {len(st)} 只，其中 IndustryElasticity 有效 {n_ok} "
          f"（{n_ok / max(1, len(st)) * 100:.1f}%）")
    print(f"─ 输出：d:\\mystock\\solo\\ige\\output\\ige_full_{args.date}.csv 与 "
          f"ige_industry_{args.date}.csv")


if __name__ == "__main__":
    main()
