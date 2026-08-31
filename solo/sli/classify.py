# -*- coding: utf-8 -*-
"""
SLI 分类层
- 龙头类型：ABSOLUTE_LEADER / GROWTH_LEADER / PROFIT_LEADER / CHALLENGER / EMERGING
- 龙头差距指数：LeaderGap → DOMINANT / STRONG_LEADER / CLOSE_CONTEST / FRAGMENTED
- 龙头生命周期：T vs T-20/T-60/T-120 → EMERGING→ASCENDING→CONFIRMED→ACCELERATING→MATURE→DECLINING→REPLACED
- 龙头加速器：LEADER_ACCELERATION / NEXT_LEADER
- 四种特殊标签：LEADER_NO_TRADE / LEADER_EARNINGS_TURN / LEADER_BREAKOUT / NEXT_LEADER
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .config import CHALLENGER_THRESHOLD, CHALLENGER_W, CLS, CLS_V2, DOMINANCE_BANDS, DOMINANCE_W, LEADER_GAP_BANDS

logger = logging.getLogger("sli.classify")


# ══════════════════════════════════════════════════════
# 一、行业内部排名
# ══════════════════════════════════════════════════════

def industry_rank(panel: pd.DataFrame) -> pd.DataFrame:
    """行业内部按 SLI 排名（1 = 行业第一）。"""
    panel = panel.copy()
    panel["ind_rank"] = panel.groupby("l3_code")["sli"].rank(ascending=False, method="min")
    return panel


# ══════════════════════════════════════════════════════
# 二、龙头差距指数
# ══════════════════════════════════════════════════════

def leader_gap(panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """龙头差距指数。

    返回 (panel 加列, 行业汇总表)
    - 每行业：leader_gap = 第一名SLI − 第二名SLI；scale/profit/growth 差距同口径
    - gap_band ∈ DOMINANT / STRONG_LEADER / CLOSE_CONTEST / FRAGMENTED / SINGLE
    """
    panel = panel.copy()
    gap_cols = {"leader": "sli", "scale": "scale_score", "profit": "profit_score",
                "growth": "growth_score"}
    for tag, _ in gap_cols.items():
        panel[f"{tag}_gap"] = np.nan
    panel["gap_band"] = ""

    rows = []
    for l3, g in panel.groupby("l3_code"):
        top = g.nlargest(2, "sli")
        d = {"l3_code": l3}
        if len(top) == 1:
            d.update({"top1_ts_code": top.iloc[0]["ts_code"],
                      "top1_name": str(top.iloc[0].get("name", "")),
                      "top1_sli": float(top.iloc[0]["sli"]), "top2_sli": np.nan,
                      "leader_gap": np.nan, "gap_band": "SINGLE",
                      "scale_gap": np.nan, "profit_gap": np.nan, "growth_gap": np.nan})
            panel.loc[g.index, "gap_band"] = "SINGLE"
            rows.append(d)
            continue
        r1, r2 = top.iloc[0], top.iloc[1]
        d.update({"top1_ts_code": r1["ts_code"], "top1_name": str(r1.get("name", "")),
                  "top1_sli": float(r1["sli"]), "top2_sli": float(r2["sli"]),
                  "leader_gap": float(r1["sli"] - r2["sli"])})
        for tag, col in gap_cols.items():
            v1, v2 = r1.get(col), r2.get(col)
            d[f"{tag}_gap"] = (float(v1 - v2) if pd.notna(v1) and pd.notna(v2) else np.nan)
        band = "FRAGMENTED"
        for thr, name in LEADER_GAP_BANDS:
            if d["leader_gap"] >= thr:
                band = name
                break
        d["gap_band"] = band
        panel.loc[g.index, "leader_gap"] = d["leader_gap"]
        panel.loc[g.index, "gap_band"] = band
        for tag, _ in gap_cols.items():
            panel.loc[g.index, f"{tag}_gap"] = d[f"{tag}_gap"]
        rows.append(d)
    return panel, pd.DataFrame(rows)


# ══════════════════════════════════════════════════════
# 三、龙头生命周期
# ══════════════════════════════════════════════════════

def lifecycle(panels: dict[str, pd.DataFrame], col: str = "sli") -> pd.DataFrame:
    """龙头生命周期状态机。

    输入: {"T": p_T, "T20": p_T20, "T60": p_T60, "T120": p_T120}（各含 col 列）
    返回: ts_code, {col}_T..{col}_T120, rank_T..rank_T120, lifecycle
    """
    base = panels["T"][["ts_code", "l3_code", col]].copy()
    base["rank_T"] = base.groupby("l3_code")[col].rank(ascending=False, method="min")
    base[f"{col}_T"] = base[col]
    out = base[["ts_code", f"{col}_T", "rank_T"]].copy()

    for label in ("T20", "T60", "T120"):
        p = panels.get(label)
        if p is None or p.empty or col not in p.columns:
            out[f"{col}_{label}"] = np.nan
            out[f"rank_{label}"] = np.nan
            continue
        sub = p[["ts_code", col]].copy()
        sub["_l3"] = p["l3_code"]
        sub[f"rank_{label}"] = sub.groupby("_l3")[col].rank(ascending=False, method="min")
        out = out.merge(sub[["ts_code", col, f"rank_{label}"]].rename(
            columns={col: f"{col}_{label}"}), on="ts_code", how="left")

    out["lifecycle"] = out.apply(_lifecycle_state, axis=1, sli_col=f"{col}_")
    return out


def _lifecycle_state(r: pd.Series, sli_col: str = "sli_") -> str:
    sli = {k: r.get(f"{sli_col}{k}") for k in ("T120", "T60", "T20", "T")}
    rk = {k: r.get(f"rank_{k}") for k in ("T120", "T60", "T20", "T")}
    cur_sli, cur_rank = sli["T"], rk["T"]
    if pd.isna(cur_sli):
        return "UNKNOWN"

    was_top1 = pd.notna(rk["T120"]) and rk["T120"] == 1
    now_top2 = pd.notna(cur_rank) and cur_rank <= 2

    # REPLACED：曾是第一，如今掉出前2，或 SLI 较 T-120 崩塌
    if was_top1 and not now_top2:
        return "REPLACED"
    if pd.notna(sli["T120"]) and cur_sli < sli["T120"] - 15:
        return "REPLACED"

    # DECLINING：SLI 连续回落，或排名显著下滑且不在前2
    if pd.notna(sli["T20"]) and cur_sli < sli["T20"]:
        if pd.isna(sli["T60"]) or sli["T20"] <= sli["T60"]:
            return "DECLINING"
    if (pd.notna(rk["T20"]) and pd.notna(cur_rank) and cur_rank > rk["T20"] + 1
            and cur_rank > 2):
        return "DECLINING"

    # ACCELERATING：第一/前2 且 SLI 加速上行（二阶导>0 或 较 T-120 大涨）
    if now_top2:
        d_now = None
        if pd.notna(sli["T20"]) and pd.notna(sli["T60"]):
            d_now = cur_sli - sli["T20"]
            d_prev = sli["T20"] - sli["T60"]
            if d_prev > 0 and d_now > d_prev:
                return "ACCELERATING"
        if pd.notna(sli["T120"]) and cur_sli - sli["T120"] >= 8:
            return "ACCELERATING"
        if pd.notna(sli["T60"]) and cur_sli >= sli["T60"]:
            return "CONFIRMED"

    # EMERGING：现在进入前2，但 T-60 时不在前2，且 SLI 上行
    prev_top2 = pd.notna(rk["T60"]) and rk["T60"] <= 2
    if now_top2 and not prev_top2:
        if pd.isna(sli["T60"]) or cur_sli > sli["T60"]:
            return "EMERGING"

    # ASCENDING：排名改善或 SLI 明显上行
    if pd.notna(rk["T60"]) and pd.notna(cur_rank) and cur_rank < rk["T60"]:
        return "ASCENDING"
    if pd.notna(sli["T60"]) and cur_sli > sli["T60"] + 3:
        return "ASCENDING"

    # MATURE：长期第一，当前高位企稳（SLI 较 T-120 变化 <3）
    if now_top2 and pd.notna(sli["T120"]) and abs(cur_sli - sli["T120"]) < 3:
        return "MATURE"

    if now_top2:
        return "CONFIRMED"
    return "DECLINING"


# ══════════════════════════════════════════════════════
# 四、龙头类型分类
# ══════════════════════════════════════════════════════

TYPE_PRIORITY = ["ABSOLUTE_LEADER", "GROWTH_LEADER", "PROFIT_LEADER",
                 "CHALLENGER", "EMERGING"]


def classify_leader(panel: pd.DataFrame) -> pd.DataFrame:
    """龙头类型分类。返回加列：is_* 布尔列 + leader_type(主类型) + all_types。"""
    panel = panel.copy()
    a = CLS["absolute"]
    panel["is_ABSOLUTE_LEADER"] = (
        (panel["sli"] >= a["sli"])
        & (panel["scale_score"] >= a["scale"])
        & (panel["profit_score"] >= a["profit"])
        & (panel["purity"] >= a["purity"])
        & (panel["ind_rank"] <= a["rank_max"])
        & (~panel["low_sample"].fillna(False)))
    g = CLS["growth_leader"]
    panel["is_GROWTH_LEADER"] = (
        (panel["growth_score"] >= g["growth"]) & (panel["sli"] >= g["sli"]))
    p = CLS["profit_leader"]
    panel["is_PROFIT_LEADER"] = (
        (panel["profit_score"] >= p["profit"])
        & (panel.get("roe_adv", 0) > 0))
    c = CLS["challenger"]
    any_strong = (panel["growth_score"] >= c["growth"]) | \
                 (panel["profit_score"] >= c["profit"]) | \
                 (panel["market_score"] >= c["market"])
    panel["is_CHALLENGER"] = (
        (panel["sli"] >= c["sli"]) & (panel["ind_rank"] > 1) & any_strong)
    e = CLS["emerging"]
    panel["is_EMERGING"] = (
        (panel["sli"] >= e["sli"]) & (panel["growth_score"] >= e["growth"])
        & (panel["market_score"] >= e["market"]) & (panel["profit_score"] >= e["profit"]))

    def _types(r: pd.Series) -> tuple[str, str]:
        flags = [t for t in TYPE_PRIORITY if bool(r.get(f"is_{t}", False))]
        if flags:
            return flags[0], "/".join(flags)
        return "NONE", "NONE"

    tt = panel.apply(_types, axis=1, result_type="expand")
    panel["leader_type"] = tt[0]
    panel["all_types"] = tt[1]
    return panel


# ══════════════════════════════════════════════════════
# 五、龙头加速器
# ══════════════════════════════════════════════════════

def accelerator(panel: pd.DataFrame) -> pd.DataFrame:
    """龙头加速器：LEADER_ACCELERATION / NEXT_LEADER_STRONG。"""
    panel = panel.copy()
    ac = CLS["acceleration"]
    if "sli_T60" not in panel.columns:
        panel["sli_T60"] = np.nan
    panel["LEADER_ACCELERATION"] = (
        (panel["sli"] >= ac["sli"])
        & (panel["growth_score"] >= ac["growth"])
        & (panel["market_score"] >= ac["market"])
        & ((panel["sli_T"] - panel["sli_T60"]) >= ac["sli60_delta"]))
    # 行业景气（行业内 rs60 中位数 > 0）
    ind_med_rs60 = panel.groupby("l3_code")["rs60"].transform("median")
    panel["ind_med_rs60"] = ind_med_rs60
    # NEXT_LEADER 强化：同时 利润加速 + 行业景气 + 股价相对行业走强
    panel["NEXT_LEADER_STRONG"] = (
        panel["LEADER_ACCELERATION"]
        & (panel.get("accel_bonus", 0) > 0)
        & (ind_med_rs60 > 0)
        & (panel["rs60"] > 0))
    return panel


# ══════════════════════════════════════════════════════
# 六、特殊标签
# ══════════════════════════════════════════════════════

def special_tags(panel: pd.DataFrame) -> pd.DataFrame:
    """四种特殊标签。要求已并入 lifecycle 的 sli_T/sli_T60 列。"""
    panel = panel.copy()
    # 龙头但不适合买：SLI 高但趋势破坏（价<MA20<MA60）
    panel["LEADER_NO_TRADE"] = (
        (panel["sli"] >= 80)
        & (panel["close"] < panel["ma20"])
        & (panel["ma20"] < panel["ma60"]))
    # 龙头 + 基本面拐点：SLI≥85 且利润增速由负转正
    panel["LEADER_EARNINGS_TURN"] = (
        (panel["sli"] >= 85) & (panel.get("profit_turn", False) == True))  # noqa: E712
    # 龙头 + 趋势突破：SLI≥80、相对行业强、突破20日平台
    panel["LEADER_BREAKOUT"] = (
        (panel["sli"] >= 80)
        & (panel["rs60"] > 0)
        & (panel["close"] >= panel["high20"])
        & (panel["close"] > panel["ma20"]))
    # 下一代龙头：SLI 65~85、Growth≥85、Market≥80、SLI 持续上升
    nl = CLS["next_leader"]
    panel["NEXT_LEADER"] = (
        (panel["sli"] >= nl["sli_lo"]) & (panel["sli"] <= nl["sli_hi"])
        & (panel["growth_score"] >= nl["growth"])
        & (panel["market_score"] >= nl["market"])
        & (panel["profit_score"] >= nl["profit"])
        & (panel["sli_T"] > panel["sli_T60"]))
    return panel


# ══════════════════════════════════════════════════════
# 七、与短线交易系统对接（TradeAlpha）
# ══════════════════════════════════════════════════════

def trade_alpha(panel: pd.DataFrame, er20: dict[str, float] | None = None) -> pd.DataFrame:
    """TradeAlpha = 25%SLI + 20%ER20 + 15%行业强度 + 15%成长加速
                  + 10%相对强度 + 10%HVT + 5%趋势
    缺失分量按权重比例重归一化；仅 SLI≥80 且 ER20≥80 进入高优先级交易池。
    """
    panel = panel.copy()
    er20_s = pd.Series(0.0, index=panel.index)
    if er20:
        er20_s = panel["ts_code"].map(er20)
    panel["er20"] = er20_s.replace(0, np.nan)
    panel["industry_strength"] = panel.groupby("l3_code")["rs60"].transform("median").clip(lower=0) * 2.0
    panel["growth_accel"] = panel.get("accel_bonus", 0) * 10.0
    panel["hvt"] = panel["turnover_rate"].fillna(0).clip(0, 20) * 5.0  # 换手强度代理

    comps = {
        "sli": (panel["sli"], 0.25),
        "er20": (panel["er20"], 0.20),
        "industry": (panel["industry_strength"], 0.15),
        "growth_accel": (panel["growth_accel"], 0.15),
        "rs": (panel["rs60_score"], 0.10),
        "hvt": (panel["hvt"], 0.10),
        "trend": (panel["trend_score"], 0.05),
    }
    rows = []
    for _, r in panel.iterrows():
        total_w, acc = 0.0, 0.0
        for _, (col, w) in comps.items():
            v = r.get(col.name) if hasattr(col, "name") else None
            if v is None or pd.isna(v):
                continue
            total_w += w
            acc += w * float(v)
        rows.append(acc / total_w if total_w > 0 else np.nan)
    panel["trade_alpha"] = rows
    panel["high_priority_pool"] = (
        (panel["sli"] >= 80) & (panel["er20"] >= 80))
    return panel


# ══════════════════════════════════════════════════════
# SLI V2 分类层
# 龙头类型 / Dominance / ChallengerScore / NEXT_LEADER /
# SUPER_LEADER / EARNINGS_TURN（须先合并 lifecycle(col="sli_v2") 结果）
# ══════════════════════════════════════════════════════

TYPE_PRIORITY_V2 = ["ABSOLUTE_LEADER", "PRODUCT_LEADER", "GROWTH_LEADER",
                    "PROFIT_LEADER", "CHALLENGER", "EMERGING_LEADER"]


def classify_v2(panel: pd.DataFrame) -> pd.DataFrame:
    """V2 龙头类型分类。

    排名基准改为「细分赛道内部」（l3_code|subsector）：
    - ABSOLUTE_LEADER : SLI_V2≥85 + Industry≥80 + ProductPosition≥80 + Purity≥40 + 赛道前2
    - PRODUCT_LEADER  : ProductPosition≥90 且赛道第1（产品级绝对龙头）
    - GROWTH_LEADER   : Growth≥85 + SLI_V2≥75
    - PROFIT_LEADER   : ProfitQuality≥85
    - CHALLENGER      : SLI_V2≥75 + 非赛道第1 + (Growth/Profit/Market 任一项≥80)
    - EMERGING_LEADER : SLI_V2≥65 + Growth≥85 + Market≥80 + SLI60提升≥5

    返回加列：sub_rank/ind_rank_v2、is_*、leader_type_v2、all_types_v2
    """
    panel = panel.copy()
    grp_key = panel["l3_code"].astype(str) + "|" + panel.get("subsector", "").fillna("")
    panel["sub_rank"] = panel.groupby(grp_key)["sli_v2"].rank(ascending=False, method="min")
    panel["ind_rank_v2"] = panel.groupby("l3_code")["sli_v2"].rank(ascending=False, method="min")

    a = CLS_V2["absolute"]
    panel["is_ABSOLUTE_LEADER"] = (
        (panel["sli_v2"] >= a["sli"])
        & (panel["industry_score"] >= a["industry"])
        & (panel["product_position"] >= a["product"])
        & (panel["product_purity"] >= a["purity"])
        & (panel["sub_rank"] <= a["rank_max"])
        & (~panel["low_sample"].fillna(False)))
    pl = CLS_V2["product_leader"]
    panel["is_PRODUCT_LEADER"] = (
        (panel["product_position"] >= pl["product"])
        & (panel["sub_rank"] == 1))
    g = CLS_V2["growth_leader"]
    panel["is_GROWTH_LEADER"] = (
        (panel["growth_v2"] >= g["growth"]) & (panel["sli_v2"] >= g["sli"]))
    p = CLS_V2["profit_leader"]
    panel["is_PROFIT_LEADER"] = (
        (panel["profit_quality"] >= p["profit"])
        & (panel.get("roe_adv", 0) > 0))
    c = CLS_V2["challenger"]
    any_strong = ((panel["growth_v2"] >= c["growth"]) |
                  (panel["profit_quality"] >= c["profit"]) |
                  (panel["market_v2"] >= c["market"]))
    panel["is_CHALLENGER"] = (
        (panel["sli_v2"] >= c["sli"]) & (panel["sub_rank"] > 1) & any_strong)
    e = CLS_V2["emerging"]
    panel["is_EMERGING_LEADER"] = (
        (panel["sli_v2"] >= e["sli"]) & (panel["growth_v2"] >= e["growth"])
        & (panel["market_v2"] >= e["market"])
        & (panel["sli_v2_T"] - panel["sli_v2_T60"] >= e["sli60_delta"]))

    def _types(r: pd.Series) -> tuple[str, str]:
        flags = [t for t in TYPE_PRIORITY_V2 if bool(r.get(f"is_{t}", False))]
        if flags:
            return flags[0], "/".join(flags)
        return "NONE", "NONE"

    tt = panel.apply(_types, axis=1, result_type="expand")
    panel["leader_type_v2"] = tt[0]
    panel["all_types_v2"] = tt[1]
    return panel


def dominance_v2(panel: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """龙头统治力（按细分赛道）。

    Dominance = 40%与第二名SLI差距 + 30%产品规模差距 + 20%盈利能力差距
                + 10%市占率差距（无真实市占率数据 → 剔除并重归一化权重）
    分级：>20 DOMINANT / 10~20 STRONG_LEADER / 5~10 COMPETITIVE / <5 FRAGMENTED

    返回 (panel 加列, 每赛道汇总表)
    """
    panel = panel.copy()
    panel["dominance_score"] = np.nan
    panel["dominance"] = ""
    grp_key = panel["l3_code"].astype(str) + "|" + panel.get("subsector", "").fillna("")
    rows = []
    for key, g in panel.groupby(grp_key):
        top = g.nlargest(2, "sli_v2")
        if len(top) < 2:
            continue
        r1, r2 = top.iloc[0], top.iloc[1]
        comps = [
            ("sli", r1["sli_v2"] - r2["sli_v2"], DOMINANCE_W["sli"]),
            ("product", r1.get("product_position", np.nan) - r2.get("product_position", np.nan),
             DOMINANCE_W["product"]),
            ("profit", r1.get("profit_quality", np.nan) - r2.get("profit_quality", np.nan),
             DOMINANCE_W["profit"]),
        ]
        total_w, acc = 0.0, 0.0
        for name, v, w in comps:
            if pd.notna(v):
                total_w += w
                acc += w * max(0.0, float(v))
        score = acc / total_w if total_w > 0 else np.nan
        band = "FRAGMENTED"
        for thr, name in DOMINANCE_BANDS:
            if score >= thr:
                band = name
                break
        rows.append({"l3_code": r1["l3_code"], "l3_name": str(r1.get("l3_name", "")),
                     "subsector": str(r1.get("subsector", "")),
                     "leader": str(r1.get("name", "")), "runner_up": str(r2.get("name", "")),
                     "dominance_score": round(score, 2) if pd.notna(score) else np.nan,
                     "dominance": band})
        mask = (grp_key == key)
        panel.loc[mask, "dominance_score"] = score
        panel.loc[mask, "dominance"] = band
    return panel, pd.DataFrame(rows)


def _map_sli_growth(v) -> float:
    """SLI 60日提升 → 0~100 分。"""
    if v is None or pd.isna(v):
        return 50.0
    v = float(v)
    if v >= 5.0:
        return 100.0
    if v >= 2.5:
        return 80.0
    if v >= 0.0:
        return 55.0
    if v >= -2.5:
        return 30.0
    return 10.0


def _map_prod_growth(v) -> float:
    """核心产品收入增速 → 0~100 分。"""
    if v is None or pd.isna(v):
        return 50.0
    v = float(v)
    if v >= 30:
        return 100.0
    if v >= 20:
        return 80.0
    if v >= 10:
        return 60.0
    if v >= 0:
        return 45.0
    return 20.0


def challenger_score(panel: pd.DataFrame) -> pd.DataFrame:
    """龙头挑战指数。

    ChallengerScore = 30%SLI增长 + 25%Growth + 20%ProductPosition增长
                      + 15%Profit增长 + 10%RelativeStrength
    ≥80 → LEADER_CHALLENGER=TRUE
    """
    panel = panel.copy()
    if "sli_v2_T" in panel.columns and "sli_v2_T60" in panel.columns:
        panel["_sli_growth_v2"] = panel["sli_v2_T"] - panel["sli_v2_T60"]
    else:
        panel["_sli_growth_v2"] = np.nan
    if "prod_rev_growth" not in panel.columns:
        panel["prod_rev_growth"] = np.nan
    rs60 = panel.get("rs60_v2_score", panel.get("rs60_score", pd.Series(50.0, index=panel.index)))

    panel["challenger_score"] = (
        CHALLENGER_W["sli_growth"] * panel["_sli_growth_v2"].apply(_map_sli_growth)
        + CHALLENGER_W["growth"] * panel["growth_v2"].fillna(50.0)
        + CHALLENGER_W["product_growth"] * panel["prod_rev_growth"].apply(_map_prod_growth)
        + CHALLENGER_W["profit_growth"] * panel["profit_quality"].fillna(50.0)
        + CHALLENGER_W["rs"] * rs60.fillna(50.0))
    panel["LEADER_CHALLENGER"] = panel["challenger_score"] >= CHALLENGER_THRESHOLD
    return panel


def next_leader_v2(panel: pd.DataFrame) -> pd.DataFrame:
    """下一代龙头。

    SLI_V2≥65 + Growth≥85 + ProductPosition≥70 + Market≥75 + SLI60提升≥5，
    且「利润加速 / 产品收入增长 / 市场份额提升(产品增速>营收增速) / 产能扩张(无数据)
    」中至少满足 2 项 → NEXT_LEADER=TRUE
    """
    panel = panel.copy()
    nl = CLS_V2["next_leader"]
    rev_growth = pd.to_numeric(panel.get("or_yoy"), errors="coerce")
    prod_growth = pd.to_numeric(panel.get("prod_rev_growth"), errors="coerce")

    accel = (pd.to_numeric(panel.get("accel_bonus"), errors="coerce").fillna(0) > 0).astype(int)
    prod_ok = prod_growth.notna()
    # ② 产品收入增长：有产品数据用产品增速≥10%；缺失时以营收增速≥15%作低置信代理
    prod_incr = np.where(prod_ok, (prod_growth >= 10).astype(int),
                         (rev_growth >= 15).astype(int))
    # ③ 市场份额提升：产品增速 > 营收增速（仅产品数据可观测）
    share_up = (prod_ok & (prod_growth > rev_growth)).astype(int)
    confirm = accel + prod_incr + share_up
    panel["next_confirm_count"] = confirm

    sli_delta = np.nan
    if "sli_v2_T" in panel.columns and "sli_v2_T60" in panel.columns:
        sli_delta = panel["sli_v2_T"] - panel["sli_v2_T60"]
    panel["NEXT_LEADER"] = (
        (panel["sli_v2"] >= nl["sli"])
        & (panel["growth_v2"] >= nl["growth"])
        & (panel["product_position"] >= nl["product"])
        & (panel["market_v2"] >= nl["market"])
        & (sli_delta >= nl["sli60_delta"])
        & (confirm >= nl["confirm_min"]))
    return panel


def earnings_turn_v2(panel: pd.DataFrame) -> pd.DataFrame:
    """基本面拐点。

    EARNINGS_ACCELERATION : 近2期利润增速加速（g2>g1 且 g2>0）
    EARNINGS_TURN         : 利润增速由负转正
    LEADER_EARNINGS_TURN  : SLI_V2≥80 龙头 + 拐点
    """
    panel = panel.copy()
    g1 = pd.to_numeric(panel.get("g1"), errors="coerce")
    g2 = pd.to_numeric(panel.get("g2"), errors="coerce")
    panel["EARNINGS_ACCELERATION"] = g2.notna() & g1.notna() & (g2 > g1) & (g2 > 0)
    panel["EARNINGS_TURN"] = (panel.get("profit_turn", False) == True)  # noqa: E712
    panel["LEADER_EARNINGS_TURN"] = (
        (panel["sli_v2"] >= 80)
        & (panel["EARNINGS_TURN"] | panel["EARNINGS_ACCELERATION"]))
    return panel


def super_leader_v2(panel: pd.DataFrame) -> pd.DataFrame:
    """SUPER_LEADER：高龙头质量 × 高行业景气 × 利润加速。

    行业景气 = 三级行业内 RS60 中位数 > 0。
    """
    panel = panel.copy()
    sp = CLS_V2["super_leader"]
    ind_med_rs60 = panel.groupby("l3_code")["rs60"].transform("median")
    panel["industry_strength"] = ind_med_rs60
    if "EARNINGS_ACCELERATION" not in panel.columns:
        panel = earnings_turn_v2(panel)
    panel["SUPER_LEADER"] = (
        (panel["sli_v2"] >= sp["sli"])
        & (panel["growth_v2"] >= sp["growth"])
        & (ind_med_rs60 > sp["ind_rs60"])
        & (panel["EARNINGS_ACCELERATION"]
           | (pd.to_numeric(panel.get("accel_bonus"), errors="coerce").fillna(0) > 0)))
    return panel


def v2_pipeline(panel: pd.DataFrame, subsector_df: pd.DataFrame,
                prod_growth: pd.DataFrame | None = None) -> pd.DataFrame:
    """V2 分类总流水线（供 runner 一键调用，按顺序保证依赖）。

    依赖：panel 已含 build_panel_v2 的 sli_v2 与 lifecycle(col="sli_v2") 的 sli_v2_T/T60。
    依次执行：classify_v2 → dominance_v2 → challenger_score → next_leader_v2
             → earnings_turn_v2 → super_leader_v2
    """
    from .subsector import knowledge_check
    panel = classify_v2(panel)
    panel, _ = dominance_v2(panel)
    panel = challenger_score(panel)
    panel = next_leader_v2(panel)
    panel = earnings_turn_v2(panel)
    panel = super_leader_v2(panel)
    if subsector_df is not None and not subsector_df.empty:
        from .subsector import load_industry_knowledge
        panel = knowledge_check(panel, load_industry_knowledge())
    return panel
