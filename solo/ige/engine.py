# -*- coding: utf-8 -*-
"""
IGE v1.2 编排层
================
把 data（宇宙/财务/价表/龙头）+ factors（L1~L3 打分表）串成最终结果：

  1. build_wide()：每股宽表（归属 + 财务 + 复利收益窗口 w20/w60/w120 + SLI_V2）
  2. 三级行业表 + 样本收缩混合 → ige_mix（基础行业弹性，生命周期调整前，不再二次分位）
  3. 生命周期分类 + 修正（最后 clip）→ IGE_ADJ（当前有效弹性）
  4. 行业类型/状态/加速确认/置信度档位标签 + 分行业解释文案
  5. 每股 SIA（60% 利润弹性差 + 40% 价格超额差）→ IndustryElasticity = 70%IGE_ADJ + 30%SIA
  6. v1.2：IGE_MOM / IGE_PERSISTENCE（三级混合→横截面分位）、IGE_EFFECTIVE、
     类型微调（STRUCTURAL/CYCLICAL_HIGH）、IGE_OPPORTUNITY_TYPE、T120_ROCKET_CORE、
     §13 分层排序（rank_tier），全部纯新增列，不改五因子与 IGE_ADJ。
  7. 输出：股票级全表 + 行业级摘要（ige/output/*.csv，utf-8-sig）

约定：所有评分 0~100；缺失一律 Fail-soft（降置信度，不人为填高分）。
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import numpy as np
import pandas as pd

from . import factors as F
from .config import (CONF_MULT, GRADE_BANDS, LIST_CUT_BACK, LOOKBACK_WINDOWS,
                     MOM_BANDS, OUTPUT_DIR, PERS_BANDS, PRICE_EXTRA_DAYS,
                     ROCKET_BLOCK_IGE, ROCKET_IGE_MIN, SNAPSHOT_DATE, W_IGE,
                     W_SIA, W_SIA_PRICE, W_SIA_PROFIT)
from .data import (build_universe, load_finance, load_prices, load_sli_panel,
                   trade_dates)

logger = logging.getLogger("ige.engine")

WINDOW_KEYS = list(LOOKBACK_WINDOWS)          # ["w20", "w60", "w120"]
WINDOW_SUFFIX = {k: k[1:] for k in WINDOW_KEYS}  # w20->20, w60->60, w120->120


# ── 小工具 ──────────────────────────────────────────

def _num(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _grade_of(v: float) -> str:
    if pd.isna(v):
        return "NA"
    for thr, name in GRADE_BANDS:
        if v >= thr:
            return name
    return GRADE_BANDS[-1][1]


def _clip01(s: pd.Series) -> pd.Series:
    return _num(s).clip(0.0, 100.0)


def _blend3(v3, v2, v1, w3, w2, w1) -> np.ndarray:
    """三级 IGE 混合（w 为标量或逐行数组）：缺失项权重自动归零并在可用项间重归一化。"""
    a3, a2, a1 = (np.asarray(x, dtype=float) for x in (v3, v2, v1))
    ww3, ww2, ww1 = (np.asarray(x, dtype=float) for x in (w3, w2, w1))
    m3, m2, m1 = ~np.isnan(a3), ~np.isnan(a2), ~np.isnan(a1)
    num = ww3 * np.where(m3, a3, 0.0) + ww2 * np.where(m2, a2, 0.0) + ww1 * np.where(m1, a1, 0.0)
    den = ww3 * m3 + ww2 * m2 + ww1 * m1
    out = np.where(den > 0, num / np.where(den > 0, den, np.nan), np.nan)
    return out


def _blend2(s1, s2, w1: float, w2: float) -> pd.Series:
    """两列加权（缺失项自动重归一化），返回 Series。"""
    a1, a2 = _num(s1), _num(s2)
    num = a1.fillna(0.0) * w1 + a2.fillna(0.0) * w2
    den = a1.notna().astype(float) * w1 + a2.notna().astype(float) * w2
    return num / den.replace(0.0, np.nan)


# ── 窗口收益 ─────────────────────────────────────────

def _window_returns(dates: list[str], piv: pd.DataFrame,
                    end_date: str) -> pd.DataFrame:
    """在累计收益指数 pivot 上取 [snapshot-N, snapshot] 累计收益率(%）。

    返回 DataFrame（index=ts_code，列 w20/w60/w120），并保证与快照日对齐。
    """
    try:
        last = dates.index(end_date)
    except ValueError:
        last = len(dates) - 1
        logger.warning("快照日 %s 无行情，改用最近交易日 %s", end_date, dates[last])
    cur = piv.iloc[last]
    out = {}
    for k in WINDOW_KEYS:
        n = LOOKBACK_WINDOWS[k]
        j = last - n
        if j < 0:
            out[k] = pd.Series(np.nan, index=piv.columns)
        else:
            prev = piv.iloc[j]
            out[k] = (cur / prev - 1.0) * 100.0
    ret = pd.DataFrame(out)
    ret.index.name = "ts_code"
    return ret


# ── 每股宽表 ─────────────────────────────────────────

def build_wide(end_date: str, cut_date: Optional[str] = None) -> pd.DataFrame:
    """组装每股宽表：universe + 财务(stock_metrics) + 窗口收益 + 每股利润弹性。

    wide 即 factors.build_level_table 的入参，另附加 name / 各级 code / el0。
    """
    uni = build_universe(end_date, cut_date=cut_date)
    if uni.empty:
        raise RuntimeError(f"{end_date} 宇宙为空")
    logger.info("宇宙：%d 只（快照 %s）", len(uni), end_date)

    # 财务每股指标
    fin = load_finance(end_date)
    fin = F.stock_metrics(fin)
    wide = uni.merge(fin, on="ts_code", how="left")
    logger.info("财务可覆盖：%d/%d", int(fin["ts_code"].nunique()), len(wide))

    # 价格窗口起点（快照往前 120+缓冲 个交易日）
    all_dates = trade_dates(end_date)
    if not all_dates:
        raise RuntimeError(f"{end_date} 无交易日历")
    n_look = max(LOOKBACK_WINDOWS.values()) + PRICE_EXTRA_DAYS
    start_date = all_dates[max(0, len(all_dates) - 1 - n_look)]
    dates, piv = load_prices(end_date, start_date)
    wr = _window_returns(dates, piv, end_date)
    wide = wide.merge(wr, on="ts_code", how="left")
    logger.info("价格窗口可覆盖：%d/%d", int(wr.notna().any(axis=1).sum()), len(wide))

    # 每股利润弹性（SIA_PROFIT 用，与行业计算同一口径）
    wide["el0"] = F._elasticity(wide[["rg_0", "pg_0"]].copy())
    return wide.reset_index(drop=True)


# ── 生命周期 / 置信度 ───────────────────────────────

def _lifecycle_of(l3t_row: pd.Series) -> tuple[str, float]:
    return F.classify_lifecycle(
        l3t_row.get("dg0"), l3t_row.get("dg1"),
        l3t_row.get("pg0"), l3t_row.get("pg1"), l3t_row.get("macc"))


def _confidence(w3: float, w2: float, w1: float, has_demand: bool,
                has_profit: bool, n_q0: int) -> float:
    """IGE 置信度 0~100：样本收缩越彻底(依赖 L2/L1)越低；核心指标缺失再扣减。"""
    conf = 50.0 + 50.0 * w3 + 25.0 * w2 + 10.0 * w1   # (1,0,0)→100 (0.7,0.3,0)→92.5 (0.4,0.4,0.2)→82
    if not has_demand:
        conf -= 8.0
    if not has_profit:
        conf -= 8.0
    if n_q0 <= 0:
        conf = min(conf, 30.0)
    return float(np.clip(conf, 0.0, 100.0))


# ── 主流程 ──────────────────────────────────────────

def run(end_date: Optional[str] = None) -> dict[str, pd.DataFrame]:
    """IGE v1.2 全流程。返回 {"stocks": 股票级全表, "industry": 行业级摘要}。"""
    end_date = end_date or SNAPSHOT_DATE
    if isinstance(end_date, str) and "-" in end_date:
        end_date = end_date.replace("-", "")
    wide = build_wide(end_date, cut_date=_cut_date(end_date))

    sli = load_sli_panel(end_date)
    if sli is not None:
        logger.info("SLI_V2 面板可用：%d 行", len(sli))

    # 基准 = 全宇宙等权（作为市场代理，替代沪深300/中证全指）
    bench = {f"b{k[1:]}": float(_num(wide[k]).mean())
             for k in WINDOW_KEYS if k in wide.columns}

    tables = F.build_all_levels(wide, sli, bench)
    l3t, l2t, l1t = tables["L3"], tables["L2"], tables["L1"]
    logger.info("L3 行业表 %d / L2 %d / L1 %d", len(l3t), len(l2t), len(l1t))
    if l3t.empty:
        raise RuntimeError("L3 行业表为空，无法继续")

    # ── 股票级骨架 ────────────────────────────────
    st = wide[["ts_code", "name", "l1_code", "l1_name",
               "l2_code", "l2_name", "l3_code", "l3_name",
               "el0", "w20", "w60", "w120"]].copy()

    # 行业级列（L3 打分为主；ige_2/ige_1 为父级 IGE）
    # v1.2：同时携带 三级 mom_raw/pers_raw（用于三级混合后横截面分位）
    l3_cols = ["n_total", "n_q0", "dg0", "dg1", "pg0", "pg1", "pa", "macc",
               "mgm", "el_lead", "el_broad", "mr20", "mr60", "mr120",
               "beta", "demand_score", "profit_elasticity_score",
               "acceleration_score", "supply_demand_score",
               "market_elasticity_score", "ige_raw", "ige",
               "mom_raw", "pers_raw"]
    l3_cols = [c for c in l3_cols if c in l3t.columns]
    st = st.merge(l3t[l3_cols].rename(columns={
        "n_total": "industry_sample_n", "n_q0": "industry_n_q0",
        "ige": "ige_3", "mom_raw": "ige_mom_3", "pers_raw": "ige_pers_3"}),
        left_on="l3_code", right_index=True, how="left")
    st = st.merge(l2t[["ige", "mom_raw", "pers_raw"]].rename(columns={
        "ige": "ige_2", "mom_raw": "ige_mom_2", "pers_raw": "ige_pers_2"}),
        left_on="l2_code", right_index=True, how="left")
    st = st.merge(l1t[["ige", "mom_raw", "pers_raw"]].rename(columns={
        "ige": "ige_1", "mom_raw": "ige_mom_1", "pers_raw": "ige_pers_1"}),
        left_on="l1_code", right_index=True, how="left")

    # ── 样本收缩混合 → 生命周期 → IGE_ADJ ───────────
    w = st["industry_sample_n"].apply(F.shrink_weights)
    st["_w3"] = [x[0] for x in w]
    st["_w2"] = [x[1] for x in w]
    st["_w1"] = [x[2] for x in w]
    st["ige_mix"] = _blend3(st["ige_3"], st["ige_2"], st["ige_1"],
                            st["_w3"], st["_w2"], st["_w1"])

    lc = l3t.apply(_lifecycle_of, axis=1)
    lc_df = pd.DataFrame(list(lc), columns=["lifecycle_label", "lifecycle_adj"],
                         index=l3t.index)
    st = st.merge(lc_df, left_on="l3_code", right_index=True, how="left")
    st["industry_lifecycle"] = st["lifecycle_label"].fillna("MATURE_GROWTH")
    st["lifecycle_adjustment"] = _num(st["lifecycle_adj"]).fillna(0.0)
    st["ige_adj"] = _clip01(st["ige_mix"] + st["lifecycle_adjustment"])

    # ── v1.1 类型 / 状态 / 加速确认 / 置信度档位 ─────────
    st["industry_elasticity_type"] = F.industry_type_of(st)
    st["acceleration_confirm"] = F.acceleration_confirm_of(st)
    st["industry_elasticity_state"] = F.industry_state_of(
        st["industry_lifecycle"], st["ige_mix"])
    st["industry_confidence"] = st["industry_sample_n"].apply(F.confidence_label)
    # F4 本期无真实产品价格数据源，标记缺失（Spec §8/§19），不人为填分
    st["price_data_missing"] = True

    # ── v1.2 动量 / 持续性：三级混合 → L3 行业横截面分位（0~100）──────
    # 只生成本层新变量 IGE_MOM / IGE_PERSISTENCE；不改五因子与 IGE_ADJ。
    st["ige_mom_raw"] = _blend3(st["ige_mom_3"], st["ige_mom_2"], st["ige_mom_1"],
                                st["_w3"], st["_w2"], st["_w1"])
    st["ige_pers_raw"] = _blend3(st["ige_pers_3"], st["ige_pers_2"],
                                 st["ige_pers_1"], st["_w3"], st["_w2"], st["_w1"])
    _l3u = (st[["l3_code", "ige_mom_raw", "ige_pers_raw"]]
            .drop_duplicates("l3_code").set_index("l3_code"))
    st["ige_mom"] = st["l3_code"].map(F.pct_rank(_l3u["ige_mom_raw"]))
    st["ige_persistence"] = st["l3_code"].map(F.pct_rank(_l3u["ige_pers_raw"]))
    st["ige_mom_band"] = F.band_of(st["ige_mom"], MOM_BANDS)
    st["ige_persistence_band"] = F.band_of(st["ige_persistence"], PERS_BANDS)
    # IGE_EFFECTIVE = IGE_ADJ × 置信度乘子（仅交易决策用，不改 IGE_ADJ 原值）
    _mult = st["industry_confidence"].map(CONF_MULT).fillna(CONF_MULT["LOW"])
    st["ige_effective"] = (_num(st["ige_adj"]) * _mult).clip(0.0, 100.0)

    # ── SIA（个股相对行业）──────────────────────────
    # SIA_PROFIT = 个股利润弹性 - 行业龙头利润弹性(IPE)
    st["sia_profit_raw"] = _num(st["el0"]) - _num(st["el_lead"])
    # SIA_PRICE = 个股 120D 收益 - 行业 120D 平均收益（w120 缺失回退 w60）
    price = st["w120"].where(st["w120"].notna(), st["w60"])
    st["sia_price_raw"] = _num(price) - _num(st["mr120"])
    st["sia_profit"] = F.pr_col(st["sia_profit_raw"])
    st["sia_price"] = F.pr_col(st["sia_price_raw"])
    st["sia"] = _blend2(st["sia_profit"], st["sia_price"], W_SIA_PROFIT, W_SIA_PRICE)

    # ── 个股最终 IndustryElasticity ────────────────
    final = (W_IGE * _num(st["ige_adj"]).fillna(0.0)
             + W_SIA * _num(st["sia"]).fillna(0.0))
    has_final = st["ige_adj"].notna()
    only_ige = st["ige_adj"].notna() & st["sia"].isna()    # SIA 缺 → 退化为 IGE_ADJ
    final = pd.Series(np.where(has_final & ~st["sia"].isna(), final,
                               np.where(only_ige, st["ige_adj"], np.nan)),
                      index=st.index)
    st["industry_elasticity"] = _clip01(final)

    # ── 置信度 / 等级 / ROCKET / 解释 ───────────────
    conf = st.apply(lambda r: _confidence(
        r["_w3"], r["_w2"], r["_w1"],
        pd.notna(r.get("dg0")), pd.notna(r.get("el_broad")), r["industry_n_q0"]),
        axis=1)
    st["ige_confidence"] = _clip01(conf)

    st["industry_elasticity_grade"] = st["industry_elasticity"].apply(_grade_of)
    st["rocket_eligible"] = (
        (st["industry_elasticity"] >= ROCKET_IGE_MIN)
        & st["industry_lifecycle"].isin(["EARLY_EXPANSION", "ACCELERATION"])
        & (_num(st["pa"]) > 0.0)
        & (st["industry_elasticity_state"] != "ELASTICITY_TRAP"))
    st["rocket_block"] = (st["industry_elasticity"] < ROCKET_BLOCK_IGE) \
        & st["industry_elasticity"].notna()
    # v1.1 解释按 类型+生命周期 定制（金融/周期/科技/医药不共用同一模板）
    st["explain_label"], st["explain_text"] = F.typed_explain(st)

    # ── v1.2 类型微调 / 机会类型 / 最严格门槛 / 分层排序（纯新增列）──
    st["structural_elasticity"] = F.structural_elasticity_of(st)
    st["cyclical_high_elasticity"] = F.cyclical_high_elasticity_of(st)
    st["cyclical_low_persistence"] = F.cyclical_low_persistence_of(st)
    st["ige_opportunity_type"] = F.opportunity_type_of(st)
    st["t120_rocket_core"] = F.rocket_core_of(st)     # 股票档：含个股 SIA≥75
    st["rank_tier"], st["rank_reason"] = F.rank_tier(st)   # §13 分层（股票档）

    # ── 输出列（21 必出字段 + v1.1 扩展 + 审计）──────
    st = st.rename(columns={"ts_code": "code", "l1_name": "sw_l1",
                            "l2_name": "sw_l2", "l3_name": "sw_l3",
                            "beta": "market_beta"})
    out_cols = [
        # 标识
        "code", "name",
        "l1_code", "sw_l1", "l2_code", "sw_l2", "l3_code", "sw_l3",
        # 行业样本
        "industry_sample_n", "industry_n_q0", "industry_confidence",
        # 五因子
        "demand_score", "profit_elasticity_score", "acceleration_score",
        "supply_demand_score", "market_elasticity_score",
        # IGE 体系（ige_mix 基础弹性 与 IGE_ADJ 有效弹性 同时输出）
        "ige_raw", "ige_3", "ige_2", "ige_1", "ige_mix", "ige_adj",
        "industry_lifecycle", "lifecycle_adjustment",
        # v1.1 类型 / 状态 / 标签
        "industry_elasticity_type", "industry_elasticity_state",
        "acceleration_confirm",
        # SIA 与个股弹性
        "sia_profit_raw", "sia_price_raw", "sia_profit", "sia_price", "sia",
        "industry_elasticity",
        # 置信度 / 等级 / ROCKET / 解释
        "ige_confidence", "industry_elasticity_grade",
        "rocket_eligible", "rocket_block",
        "explain_label", "explain_text",
        # v1.2 动量 / 持续性 / 有效弹性 / 机会类型 / 分层
        "ige_mom", "ige_mom_band",
        "ige_persistence", "ige_persistence_band",
        "ige_effective", "ige_opportunity_type",
        "structural_elasticity", "cyclical_high_elasticity",
        "cyclical_low_persistence", "t120_rocket_core",
        "rank_tier", "rank_reason",
        # 审计（F5 传导β / F4 数据缺失标记）
        "market_beta", "price_data_missing",
    ]
    keep = [c for c in out_cols if c in st.columns]
    st = st[keep].copy()
    for c in out_cols:
        if c not in st.columns:
            st[c] = np.nan
    st = st[[c for c in out_cols if c in st.columns]].reset_index(drop=True)

    # 行业级摘要（L3 唯一 · 纯行业维度，等级按 IGE_ADJ 界定；
    # 个股级 composite 见 ige_full 文件）
    ind_cols = ["l1_code", "sw_l1", "l2_code", "sw_l2", "l3_code", "sw_l3",
                "industry_sample_n", "industry_n_q0", "industry_confidence",
                "demand_score", "profit_elasticity_score", "acceleration_score",
                "supply_demand_score", "market_elasticity_score",
                "ige_raw", "ige_3", "ige_2", "ige_1", "ige_mix", "ige_adj",
                "industry_lifecycle", "lifecycle_adjustment",
                "industry_elasticity_type", "industry_elasticity_state",
                "acceleration_confirm",
                "ige_confidence", "market_beta",
                # v1.2
                "ige_mom", "ige_mom_band",
                "ige_persistence", "ige_persistence_band",
                "ige_effective", "ige_opportunity_type",
                "structural_elasticity", "cyclical_high_elasticity",
                "cyclical_low_persistence", "t120_rocket_core",
                "rank_tier", "rank_reason"]
    ind = st.drop_duplicates(subset=["l3_code"]).loc[:, ind_cols].copy()
    # 行业档 SIA 视为通过（无个股维度），重算最严格门槛与分层
    ind["t120_rocket_core"] = F.rocket_core_of(ind, sia_col=None)
    ind["rank_tier"], ind["rank_reason"] = F.rank_tier(ind, sia_col=None)
    # v1.2 分层排序（§13，替代 v1.1 的 IGE_ADJ 简单降序）：
    # 分层越小优先级越高；tier 0 = 不满足高 IGE 前提，统一垫底。
    # 层内按 IGE_ADJ → IGE_MOM 降序。
    ind["_tier_sort"] = ind["rank_tier"].replace(0, 6)
    ind = ind.sort_values(["_tier_sort", "ige_adj", "ige_mom"],
                          ascending=[True, False, False],
                          na_position="last").drop(columns="_tier_sort")
    ind = ind.reset_index(drop=True)
    ind["industry_elasticity_grade"] = ind["ige_adj"].apply(_grade_of)
    lab, txt = F.typed_explain(ind.rename(columns={"sw_l1": "l1_name"}))
    ind["explain_label"] = lab
    ind["explain_text"] = txt
    ind["price_data_missing"] = True
    # 纯行业 ROCKET 参照：生命周期 + 利润加速度 + IGE_ADJ（个股口径见 ige_full）
    ind["rocket_eligible"] = (
        (ind["ige_adj"] >= ROCKET_IGE_MIN)
        & ind["industry_lifecycle"].isin(["EARLY_EXPANSION", "ACCELERATION"])
        & (ind["ige_adj"].notna())
        & (ind["industry_elasticity_state"] != "ELASTICITY_TRAP"))
    ind["rocket_block"] = (ind["ige_adj"] < ROCKET_BLOCK_IGE) & ind["ige_adj"].notna()

    # 落盘
    _write_csv(st, f"ige_full_{end_date}.csv")
    _write_csv(ind, f"ige_industry_{end_date}.csv")

    logger.info("IGE 完成：股票 %d / L3 行业 %d", len(st), len(ind))
    return {"stocks": st, "industry": ind}


def _cut_date(end_date: str) -> str:
    """上市时长闸门：快照往前回推 LIST_CUT_BACK 个交易日的日历日。"""
    all_dates = trade_dates(end_date)
    if len(all_dates) <= LIST_CUT_BACK:
        return ""
    return all_dates[len(all_dates) - 1 - LIST_CUT_BACK]


def _write_csv(df: pd.DataFrame, name: str) -> str:
    path = os.path.join(OUTPUT_DIR, name)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    logger.info("已输出 %s（%d 行）", path, len(df))
    return path
