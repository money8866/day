# -*- coding: utf-8 -*-
"""
SLI 评分层
七个子评分（Scale/Profit/Growth/Purity/Moat/Market/Trend）+ SLI 总分，
缺失指标自动重归一化权重；纯度置信度折扣。
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from .config import (
    ACCEL_BONUS_MILD,
    ACCEL_BONUS_STRONG,
    GROWTH_W,
    LOW_SAMPLE_N,
    MARKET_W,
    MOAT_W,
    PRODUCT_CONF_DISCOUNT,
    PROFIT_Q_W,
    PROFIT_W,
    PURITY_CONF_DISCOUNT,
    SCALE_W,
    SLI_WEIGHTS,
    SLI_V2_WEIGHTS,
    SUSTAINED_MOAT_BONUS,
    SUSTAINED_PROFIT_BONUS,
)
from .features import industry_median, pct_rank_industry

logger = logging.getLogger("sli.scoring")


# ── 分段线性映射 ──────────────────────────────────────

def _pl(v: float, pts: list[tuple[float, float]]) -> float:
    """分段线性映射，超出范围取端点值，NaN 返回 NaN。"""
    if v is None or pd.isna(v):
        return float("nan")
    pts = sorted(pts)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    if v <= xs[0]:
        return float(ys[0])
    if v >= xs[-1]:
        return float(ys[-1])
    for i in range(len(xs) - 1):
        if xs[i] <= v <= xs[i + 1]:
            span = xs[i + 1] - xs[i]
            if span == 0:
                return float(ys[i])
            return float(ys[i] + (v - xs[i]) / span * (ys[i + 1] - ys[i]))
    return float(ys[-1])


_YOY_PTS = [(-40, 0), (-20, 20), (-10, 35), (0, 50), (10, 65), (20, 80), (30, 90), (60, 100)]
_MARGIN_PTS = [(-10, 5), (-5, 20), (-2, 40), (0, 55), (2, 75), (5, 92), (10, 100)]
_RS_PTS = [(-30, 5), (-20, 20), (-10, 40), (0, 55), (10, 70), (20, 85), (30, 100)]
_ADV_PTS = [(-15, 10), (-8, 30), (-3, 50), (0, 60), (3, 75), (8, 90), (15, 100)]
_CFQ_ADV_PTS = [(-60, 10), (-20, 35), (0, 55), (20, 75), (60, 95), (150, 100)]
_PURITY_PTS = [(0, 20), (20, 40), (30, 60), (40, 75), (60, 90), (80, 100)]


def _accel_bonus(g1: float, g2: float, g3: float) -> float:
    """增速加速度加分：3期连续加速=强；2期加速=中。"""
    if pd.isna(g2):
        return 0.0
    if pd.notna(g3) and g1 < g2 < g3:
        return ACCEL_BONUS_STRONG
    if g1 < g2:
        return ACCEL_BONUS_MILD
    return 0.0


def _profit_turn(g1: float, g2: float, g3: float) -> bool:
    """利润拐点：最新期增速由负转正。"""
    last = g3 if pd.notna(g3) else g2
    prev = g2 if pd.notna(g3) else g1
    return (pd.notna(last) and last > 0) and (pd.notna(prev) and prev <= 0)


# ── 缺失分量权重重归一化 ──────────────────────────────

def _renorm_wsum(panel: pd.DataFrame, cols: list[str], weights: list[float]) -> pd.Series:
    """按分量加权求和；某分量缺失时剔除该分量并对其余权重重归一化。

    返回与 panel.index 对齐的 Series（NaN 仅当全部分量缺失）。
    """
    arr = panel[cols].to_numpy(dtype=float)
    valid = ~np.isnan(arr)
    w = np.asarray(weights, dtype=float)
    # 每行有效权重和
    wsum = valid @ w
    # 归一化权重（行级）
    wn = np.divide(w[None, :] * valid, wsum[:, None],
                   out=np.zeros_like(arr), where=wsum[:, None] > 0)
    score = np.where(wsum > 0, np.nansum(wn * arr, axis=1), np.nan)
    return pd.Series(score, index=panel.index)


# ── 子评分 ────────────────────────────────────────────

def scale_score(panel: pd.DataFrame) -> pd.Series:
    """ScaleScore = 40%营收排名 + 30%市值排名 + 20%毛利排名 + 10%总资产排名。"""
    cols = ["rev_rank", "mv_rank", "gp_rank", "asset_rank"]
    ws = [SCALE_W["revenue"], SCALE_W["mv"], SCALE_W["gross_profit"], SCALE_W["asset"]]
    return _renorm_wsum(panel, cols, ws)


def profit_score(panel: pd.DataFrame) -> pd.Series:
    """ProfitScore = 30%ROE + 25%毛利率 + 20%净利率 + 15%ROIC + 10%现金流质量。"""
    cols = ["roe_rank", "gm_rank", "nm_rank", "roic_rank", "cfq_rank"]
    ws = [PROFIT_W["roe"], PROFIT_W["gross_margin"], PROFIT_W["net_margin"],
          PROFIT_W["roic"], PROFIT_W["cashflow"]]
    s = _renorm_wsum(panel, cols, ws)
    # 连续3年盈利领先奖励
    s = s + panel["sustained_profit_years"].clip(upper=3) * SUSTAINED_PROFIT_BONUS
    return s


def growth_score(panel: pd.DataFrame) -> pd.Series:
    """GrowthScore = 35%营收增速 + 35%利润增速 + 15%ROE变化 + 15%毛利变化 + 加速加分。"""
    cols = ["rev_growth_score", "profit_growth_score",
            "roe_growth_score", "margin_expansion_score"]
    ws = [GROWTH_W["rev_growth"], GROWTH_W["profit_growth"],
          GROWTH_W["roe_growth"], GROWTH_W["margin_expansion"]]
    s = _renorm_wsum(panel, cols, ws)
    s = s + panel["accel_bonus"].fillna(0.0)
    return s


def purity_score(panel: pd.DataFrame) -> pd.Series:
    """PurityScore：>=80%→100, 60~80→90, 40~60→75, 30~40→60, 20~30→40, <20→20。"""
    return panel["purity"].apply(lambda v: _pl(v, _PURITY_PTS) if pd.notna(v) else float("nan"))


def moat_score(panel: pd.DataFrame) -> pd.Series:
    """MoatScore = 30%毛利率优势 + 25%ROE优势 + 20%净利率优势 + 15%现金流质量 + 10%研发强度。"""
    cols = ["gm_adv_score", "roe_adv_score", "nm_adv_score", "cfq_adv_score", "rd_score"]
    ws = [MOAT_W["gm_adv"], MOAT_W["roe_adv"], MOAT_W["nm_adv"],
          MOAT_W["cfq_adv"], MOAT_W["rd"]]
    s = _renorm_wsum(panel, cols, ws)
    # 连续3年盈利领先行业：护城河奖励
    s = s + panel["sustained_moat_years"].clip(upper=3).apply(
        lambda y: SUSTAINED_MOAT_BONUS if y >= 3 else 0.0)
    return s


def market_score(panel: pd.DataFrame) -> pd.Series:
    """MarketScore = 35%RS20 + 35%RS60 + 20%RS120 + 10%流动性排名。"""
    cols = ["rs20_score", "rs60_score", "rs120_score", "liq_rank"]
    ws = [MARKET_W["rs20"], MARKET_W["rs60"], MARKET_W["rs120"], MARKET_W["liq"]]
    return _renorm_wsum(panel, cols, ws)


def trend_score(panel: pd.DataFrame) -> pd.Series:
    """TrendScore = 30%MA20>MA60 + 30%MA60>MA120 + 20%MA20斜率 + 20%MA60斜率。"""
    t = pd.Series(0.0, index=panel.index)
    t += np.where(panel["ma20"] > panel["ma60"], 30.0, 0.0)
    t += np.where(panel["ma60"] > panel["ma120"], 30.0, 0.0)
    t += panel["ma20_slope"].apply(lambda v: 20.0 if pd.notna(v) and v > 0 else 0.0)
    t += panel["ma60_slope"].apply(lambda v: 20.0 if pd.notna(v) and v > 0 else 0.0)
    return pd.Series(t, index=panel.index, dtype=float)


# ── 面板构建 ──────────────────────────────────────────

def build_panel(uni: pd.DataFrame, price: pd.DataFrame, dbasic: pd.DataFrame,
                snapshot: pd.DataFrame, accel: pd.DataFrame, purity: pd.DataFrame,
                annual_moat: pd.DataFrame) -> pd.DataFrame:
    """合并全部特征，计算 7 个子评分与 SLI。

    返回完整面板（含原始值 + 排名 + 子评分 + SLI + 覆盖率）。
    """
    uni = uni[["ts_code", "l3_code", "l3_name", "l2_name", "l1_name",
               "name", "list_date", "is_st"]].drop_duplicates("ts_code")
    p = price.reset_index().rename(columns={"index": "ts_code"})
    if "ts_code" not in p.columns:
        p = price.copy()
        p = p.reset_index().rename(columns={p.index.name or "index": "ts_code"})

    panel = uni.merge(p, on="ts_code", how="left")
    panel = panel.merge(dbasic[["ts_code", "total_mv", "circ_mv", "pe_ttm", "pb", "turnover_rate"]],
                        on="ts_code", how="left")
    panel = panel.merge(snapshot, on="ts_code", how="left")
    panel = panel.merge(accel, on="ts_code", how="left")
    panel = panel.merge(purity[["ts_code", "purity", "purity_confidence", "purity_top1"]],
                        on="ts_code", how="left")

    # ── 衍生字段 ──
    panel["gross_profit"] = (pd.to_numeric(panel.get("revenue"), errors="coerce") -
                             pd.to_numeric(panel.get("total_cogs"), errors="coerce")).where(
        pd.to_numeric(panel.get("total_cogs"), errors="coerce").notna(),
        pd.to_numeric(panel.get("revenue"), errors="coerce") -
        pd.to_numeric(panel.get("operate_cost"), errors="coerce"))
    panel["rd_intensity"] = (pd.to_numeric(panel.get("rd_exp"), errors="coerce") /
                             pd.to_numeric(panel.get("revenue"), errors="coerce").replace(0, np.nan))

    # 行情数值化
    for c in ("ret20", "ret60", "ret120", "ma20", "ma60", "ma120",
              "ma20_slope", "ma60_slope", "amount20", "amount60",
              "high20", "vol20", "vol_today", "close"):
        panel[c] = pd.to_numeric(panel.get(c), errors="coerce")
    for c in ("total_mv", "circ_mv", "pe_ttm", "pb", "turnover_rate"):
        panel[c] = pd.to_numeric(panel.get(c), errors="coerce")
    for c in ("roe", "roic", "grossprofit_margin", "netprofit_margin",
              "or_yoy", "netprofit_yoy", "ocf_to_profit", "revenue",
              "total_assets", "q_profit_yoy", "dt_netprofit_yoy", "roe_dt"):
        panel[c] = pd.to_numeric(panel.get(c), errors="coerce")

    # ── 行业内相对收益 RS ──
    for span in (20, 60, 120):
        ind_med = industry_median(panel, "l3_code", f"ret{span}")
        panel[f"rs{span}"] = panel[f"ret{span}"] - ind_med
        panel[f"rs{span}_score"] = panel[f"rs{span}"].apply(lambda v: _pl(v, _RS_PTS))

    # ── 行业内 percentile 排名 ──
    panel["rev_rank"] = pct_rank_industry(panel, "l3_code", "revenue")
    panel["mv_rank"] = pct_rank_industry(panel, "l3_code", "total_mv")
    panel["gp_rank"] = pct_rank_industry(panel, "l3_code", "gross_profit")
    panel["asset_rank"] = pct_rank_industry(panel, "l3_code", "total_assets")
    panel["roe_rank"] = pct_rank_industry(panel, "l3_code", "roe")
    panel["gm_rank"] = pct_rank_industry(panel, "l3_code", "grossprofit_margin")
    panel["nm_rank"] = pct_rank_industry(panel, "l3_code", "netprofit_margin")
    panel["roic_rank"] = pct_rank_industry(panel, "l3_code", "roic")
    panel["cfq_rank"] = pct_rank_industry(panel, "l3_code", "ocf_to_profit")
    panel["liq_rank"] = pct_rank_industry(panel, "l3_code", "amount20")
    panel["rd_score"] = pct_rank_industry(panel, "l3_code", "rd_intensity")

    # ── 盈利优势（相对行业中位数） ──
    for col in ("grossprofit_margin", "roe", "netprofit_margin", "ocf_to_profit"):
        panel[f"{col}_adv"] = panel[col] - industry_median(panel, "l3_code", col)
    panel["gm_adv_score"] = panel["grossprofit_margin_adv"].apply(lambda v: _pl(v, _ADV_PTS))
    panel["roe_adv_score"] = panel["roe_adv"].apply(lambda v: _pl(v, _ADV_PTS))
    panel["nm_adv_score"] = panel["netprofit_margin_adv"].apply(lambda v: _pl(v, _ADV_PTS))
    panel["cfq_adv_score"] = panel["ocf_to_profit_adv"].apply(lambda v: _pl(v, _CFQ_ADV_PTS))

    # ── 成长子项 ──
    panel["rev_growth_score"] = panel["or_yoy"].apply(lambda v: _pl(v, _YOY_PTS))
    panel["profit_growth_score"] = panel["netprofit_yoy"].apply(lambda v: _pl(v, _YOY_PTS))
    panel["roe_growth"] = panel["roe"] - panel.get("roe_prev", pd.Series(np.nan, index=panel.index))
    panel["margin_expansion"] = (panel["grossprofit_margin"] -
                                 panel.get("gm_prev", pd.Series(np.nan, index=panel.index)))
    panel["roe_growth_score"] = panel["roe_growth"].apply(lambda v: _pl(v, _MARGIN_PTS))
    panel["margin_expansion_score"] = panel["margin_expansion"].apply(lambda v: _pl(v, _MARGIN_PTS))
    panel["accel_bonus"] = panel.apply(
        lambda r: _accel_bonus(r.get("g1", np.nan), r.get("g2", np.nan), r.get("g3", np.nan)),
        axis=1)
    panel["profit_turn"] = panel.apply(
        lambda r: _profit_turn(r.get("g1", np.nan), r.get("g2", np.nan), r.get("g3", np.nan)),
        axis=1)

    # 护城河/盈利持续领先年数
    if annual_moat is not None and not annual_moat.empty:
        panel = panel.merge(annual_moat, on="ts_code", how="left")
    for c in ("sustained_moat_years", "sustained_profit_years"):
        if c not in panel.columns:
            panel[c] = 0.0
        panel[c] = pd.to_numeric(panel[c], errors="coerce").fillna(0.0)

    # ── 七个子评分（截断到 0~100） ──
    panel["scale_score"] = scale_score(panel)
    panel["profit_score"] = profit_score(panel)
    panel["growth_score"] = growth_score(panel)
    panel["purity_score"] = purity_score(panel)
    panel["moat_score"] = moat_score(panel)
    panel["market_score"] = market_score(panel)
    panel["trend_score"] = trend_score(panel)
    for c in ("scale_score", "profit_score", "growth_score", "purity_score",
              "moat_score", "market_score", "trend_score"):
        panel[c] = panel[c].clip(0, 100)

    # ── SLI（缺失指标权重重归一化 + 纯度置信度折扣） ──
    panel["sli"] = _sli(panel)

    # 行业样本量
    cnt = panel.groupby("l3_code")["ts_code"].transform("count")
    panel["industry_count"] = cnt
    panel["low_sample"] = cnt < LOW_SAMPLE_N

    # 覆盖率（有效子评分数量）
    sub_cols = ["scale_score", "profit_score", "growth_score", "purity_score",
                "moat_score", "market_score", "trend_score"]
    panel["coverage"] = panel[sub_cols].notna().sum(axis=1) / len(sub_cols)

    return panel


def _sli(panel: pd.DataFrame) -> pd.Series:
    """SLI = Σ(w_i * s_i)，缺失分项按比例重归一化权重；纯度权重按置信度打折。"""
    sub_cols = ["scale_score", "profit_score", "growth_score", "purity_score",
                "moat_score", "market_score", "trend_score"]
    base_w = dict(SLI_WEIGHTS)
    values = panel[sub_cols]

    def _row(r):
        ws = {}
        for k, v in base_w.items():
            w = v
            if k == "purity":
                conf = r.get("purity_confidence", "LOW")
                w = v * PURITY_CONF_DISCOUNT.get(conf, PURITY_CONF_DISCOUNT["LOW"])
            ws[k] = w
        total = 0.0
        acc = 0.0
        for k, col in zip(base_w.keys(), sub_cols):
            s = r.get(col)
            if s is None or pd.isna(s):
                continue
            total += ws[k]
            acc += ws[k] * float(s)
        if total <= 0:
            return float("nan")
        return acc / total

    return panel.apply(_row, axis=1)


# ══════════════════════════════════════════════════════
# SLI V2 —— 八维评分（25/20/15/15/10/5/5/5）
# SLI_V2 = 25% Industry + 20% Product + 15% ProfitQ + 15% Growth
#        + 10% ProductPurity + 5% Moat + 5% MarketStrength + 5% Trend
# 需先经 subsector.py 的 product_position() 将产品层并入 panel。
# ══════════════════════════════════════════════════════

def industry_score(panel: pd.DataFrame) -> pd.Series:
    """IndustryPosition = 40%营收排名 + 30%市值排名 + 20%毛利排名 + 10%总资产排名。
    groupby(l3_code)（申万三级行业内部排名）。"""
    cols = ["rev_rank", "mv_rank", "gp_rank", "asset_rank"]
    ws = [0.40, 0.30, 0.20, 0.10]
    return _renorm_wsum(panel, cols, ws)


def profit_quality_score(panel: pd.DataFrame) -> pd.Series:
    """ProfitQuality = 30%ROE + 25%ROIC + 20%毛利率 + 15%净利率 + 10%现金流质量。
    附加 ProfitAdvantage（相对行业中位数的盈利优势，±5 封顶）。"""
    cols = ["roe_rank", "roic_rank", "gm_rank", "nm_rank", "cfq_rank"]
    ws = [PROFIT_Q_W["roe"], PROFIT_Q_W["roic"], PROFIT_Q_W["gm"],
          PROFIT_Q_W["nm"], PROFIT_Q_W["cfq"]]
    s = _renorm_wsum(panel, cols, ws)
    adv_cols = [c for c in ("roe_adv", "roic_adv", "grossprofit_margin_adv")
                if c in panel.columns]
    if adv_cols:
        adv = panel[adv_cols].mean(axis=1)
        bonus = adv.apply(lambda v: 0.0 if pd.isna(v)
                          else float(np.clip(v, -12.5, 12.5)) * 0.4)
        s = s + bonus
    return s


def growth_score_v2(panel: pd.DataFrame) -> pd.Series:
    """Growth = 35%营收增速 + 35%利润增速 + 15%ROE变化 + 15%毛利变化 + 加速加分(≤10)。
    与 V1 growth_score 权重一致；V2 中加速加分封顶 10。"""
    cols = ["rev_growth_score", "profit_growth_score",
            "roe_growth_score", "margin_expansion_score"]
    ws = [GROWTH_W["rev_growth"], GROWTH_W["profit_growth"],
          GROWTH_W["roe_growth"], GROWTH_W["margin_expansion"]]
    s = _renorm_wsum(panel, cols, ws)
    s = s + panel["accel_bonus"].fillna(0.0).clip(0, 10.0)
    return s


def product_purity_score(panel: pd.DataFrame) -> pd.Series:
    """ProductPurity = 核心产品收入 / 公司总收入 的分段映射。
    数据缺失（LOW_CONFIDENCE）时返回 NaN，由 SLI_V2 合成层自动降权。"""
    if "product_purity" not in panel.columns:
        return pd.Series(float("nan"), index=panel.index)
    return panel["product_purity"].apply(
        lambda v: _pl(v, _PURITY_PTS) if pd.notna(v) else float("nan"))


def market_strength_v2(panel: pd.DataFrame) -> pd.Series:
    """MarketStrength = 35%RS20 + 35%RS60 + 20%RS120 + 10%流动性排名。
    相对收益改为「个股 - 细分赛道中位数」（V2 重点：比的是赛道内部强弱，
    而非整个三级行业，避免不同细分赛道混在一起比较）。"""
    p = panel.copy()
    sub_grp = p["l3_code"].astype(str) + "|" + p.get("subsector", "").fillna("")
    has_sub = "subsector" in p.columns and p["subsector"].notna().any()
    for span in (20, 60, 120):
        col = f"ret{span}"
        if col not in p.columns:
            continue
        if has_sub:
            sub_med = p.groupby(sub_grp)[col].transform("median")
        else:
            sub_med = industry_median(p, "l3_code", col)
        p[f"rs{span}_v2"] = p[col] - sub_med
        p[f"rs{span}_v2_score"] = p[f"rs{span}_v2"].apply(lambda v: _pl(v, _RS_PTS))

    cols = [c for c in ("rs20_v2_score", "rs60_v2_score", "rs120_v2_score")
            if c in p.columns]
    ws = [MARKET_W["rs20"], MARKET_W["rs60"], MARKET_W["rs120"]][:len(cols)]
    if cols and "liq_rank" in p.columns:
        cols.append("liq_rank")
        ws = ws + [MARKET_W["liq"]]
    if not cols:
        return pd.Series(float("nan"), index=p.index)
    return _renorm_wsum(p, cols, ws)


def build_panel_v2(panel: pd.DataFrame, subsector_df: pd.DataFrame,
                   prod_growth: pd.DataFrame | None = None) -> pd.DataFrame:
    """在 V1 面板之上构建 V2 八维评分与 SLI_V2。

    需要 subsector_df（subsector_match 结果）与可选 prod_growth
    （product_rev_growth 结果，用于 Challenger/NEXT_LEADER）。

    返回 panel 加列：subsector/chain/product_purity/product_confidence、
    product_rev_growth、industry_score/product_position/profit_quality/growth_v2/
    purity_v2/moat_v2/market_v2/trend_v2、sli_v2、coverage_v2、low_sample_sub。
    """
    from .subsector import primary_subsector, product_leadership, product_position
    panel = panel.copy()

    # ── 产品层并入 ──
    prim = primary_subsector(subsector_df)
    if prim is None or prim.empty:
        panel["subsector"] = ""
        panel["chain"] = ""
        panel["product_purity"] = np.nan
        panel["product_confidence"] = "LOW"
    else:
        panel = panel.merge(prim, on="ts_code", how="left")
        panel["subsector"] = panel["subsector"].fillna("")
        panel["chain"] = panel["chain"].fillna("")
    if "product_position" not in panel.columns:
        panel = product_position(panel, subsector_df)
    if "product_leadership" not in panel.columns:
        panel = product_leadership(panel)

    # 细分赛道低样本标记
    panel["sub_count"] = panel.groupby("l3_code")["subsector"].transform(
        lambda s: s.map(s.value_counts())) if "subsector" in panel.columns else 0
    panel["low_sample_sub"] = (panel["sub_count"] < LOW_SAMPLE_N) & (
        panel["sub_count"] > 0)

    # 产品收入成长代理（Challenger / NEXT_LEADER 用）
    if prod_growth is not None and not prod_growth.empty:
        panel = panel.merge(prod_growth, on="ts_code", how="left")
    if "prod_rev_growth" not in panel.columns:
        panel["prod_rev_growth"] = np.nan

    # ── 八维评分 ──
    panel["industry_score"] = industry_score(panel)
    panel["profit_quality"] = profit_quality_score(panel)
    panel["growth_v2"] = growth_score_v2(panel)
    panel["purity_v2"] = product_purity_score(panel)
    panel["moat_v2"] = moat_score(panel)
    panel["market_v2"] = market_strength_v2(panel)
    panel["trend_v2"] = trend_score(panel)
    # product_position 已在产品层计算；缺失时用 industry_score 兜底并记低置信
    if "product_position" not in panel.columns:
        panel["product_position"] = panel["industry_score"]

    for c in ("industry_score", "product_position", "profit_quality", "growth_v2",
              "purity_v2", "moat_v2", "market_v2", "trend_v2"):
        panel[c] = pd.to_numeric(panel[c], errors="coerce").clip(0, 100)

    # ── SLI_V2 合成（缺失分项重归一化 + 产品纯度置信度折扣） ──
    panel["sli_v2"] = _sli_v2(panel)

    v2_cols = ["industry_score", "product_position", "profit_quality", "growth_v2",
               "purity_v2", "moat_v2", "market_v2", "trend_v2"]
    panel["coverage_v2"] = panel[v2_cols].notna().sum(axis=1) / len(v2_cols)
    return panel


def _sli_v2(panel: pd.DataFrame) -> pd.Series:
    """SLI_V2 = Σ(w_i * s_i)。产品纯度置信度 LOW 时权重折半。"""
    sub_cols = ["industry_score", "product_position", "profit_quality", "growth_v2",
                "purity_v2", "moat_v2", "market_v2", "trend_v2"]
    base_w = dict(SLI_V2_WEIGHTS)
    # 权重顺序与 sub_cols 对应：industry/product/profit/growth/purity/moat/market/trend

    def _row(r):
        ws = {}
        for k, v in base_w.items():
            w = v
            if k == "purity":
                conf = r.get("product_confidence", "LOW")
                w = v * PRODUCT_CONF_DISCOUNT.get(conf, PRODUCT_CONF_DISCOUNT["LOW"])
            ws[k] = w
        total = 0.0
        acc = 0.0
        for k, col in zip(base_w.keys(), sub_cols):
            s = r.get(col)
            if s is None or pd.isna(s):
                continue
            total += ws[k]
            acc += ws[k] * float(s)
        if total <= 0:
            return float("nan")
        return acc / total

    return panel.apply(_row, axis=1)
