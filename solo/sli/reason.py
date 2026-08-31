# -*- coding: utf-8 -*-
"""
SLI 可解释输出：LEADER_REASON
为每个 Top 级龙头自动生成「为什么它是龙头」的文本，禁止黑盒分数。
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger("sli.reason")


def _fmt(v, nd=1) -> str:
    """数值格式化为字符串，NaN/None 返回 '—'。"""
    try:
        v = float(v)
        if np.isnan(v) or np.isinf(v):
            return "—"
        return f"{v:.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def _ord(r, col, rank_col) -> str:
    v = r.get(rank_col)
    if pd.isna(v):
        return "—"
    return f"第{int(v)}"


def _row_reason(r: pd.Series) -> str:
    ind_n = r.get("industry_count")
    ind_n = f"（行业共{int(ind_n)}家）" if pd.notna(ind_n) else ""

    # 规模
    scale = (f"行业营收排名{_ord(r, 'revenue', 'rev_ord')}{ind_n}，"
             f"市值排名{_ord(r, 'total_mv', 'mv_ord')}，"
             f"毛利排名{_ord(r, 'gross_profit', 'gp_ord')}，"
             f"总资产排名{_ord(r, 'total_assets', 'asset_ord')}")

    # 盈利
    roe = r.get("roe")
    roe_adv = r.get("roe_adv")
    gm_adv = r.get("grossprofit_margin_adv")
    nm_adv = r.get("netprofit_margin_adv")
    profit = (f"ROE={_fmt(roe)}%，高于行业中位数{_fmt(roe_adv)}pct；"
              f"毛利率高于行业中位数{_fmt(gm_adv)}pct；"
              f"净利率高于行业中位数{_fmt(nm_adv)}pct；ROIC={_fmt(r.get('roic'))}%")

    # 成长
    g1, g2, g3 = r.get("g1"), r.get("g2"), r.get("g3")
    if pd.notna(g1) and pd.notna(g2) and pd.notna(g3) and g1 < g2 < g3:
        accel_txt = "近3期利润增速连续加速"
    elif pd.notna(g1) and pd.notna(g2) and g1 < g2:
        accel_txt = "近2期利润增速加速"
    else:
        accel_txt = "增速趋稳/减速"
    growth = (f"营收同比{_fmt(r.get('or_yoy'))}%，"
              f"净利润同比{_fmt(r.get('netprofit_yoy'))}%（{accel_txt}）")

    # 纯度
    purity = (f"主营业务占比{_fmt(r.get('purity'), 0)}%"
              f"（置信度 {r.get('purity_confidence', 'LOW')}）")

    # 竞争
    m_years = r.get("sustained_moat_years", 0)
    p_years = r.get("sustained_profit_years", 0)
    m_years = int(m_years) if pd.notna(m_years) else 0
    p_years = int(p_years) if pd.notna(p_years) else 0
    if m_years >= 3:
        comp = f"连续{m_years}年毛利率/ROE/净利率全面领先行业中位数"
    elif p_years >= 3:
        comp = f"连续{p_years}年ROE领先行业中位数"
    else:
        comp = "盈利领先持续性不足3年"

    # 市场
    market = (f"20日相对行业超额{_fmt(r.get('rs20'))}%，"
              f"60日相对行业超额{_fmt(r.get('rs60'))}%，"
              f"120日相对行业超额{_fmt(r.get('rs120'))}%")

    # 结论
    ltype = r.get("leader_type", "NONE")
    concl_map = {
        "ABSOLUTE_LEADER": "绝对龙头：规模/盈利/成长/纯度/壁垒/市场全面占优",
        "GROWTH_LEADER": "成长龙头：成长能力行业领先",
        "PROFIT_LEADER": "盈利龙头：盈利能力行业领先",
        "CHALLENGER": "龙头挑战者：具备冲击当前龙头的条件",
        "EMERGING": "潜在新龙头：高成长+强市场认可，规模快速提升",
        "NONE": "暂未达到龙头分类阈值",
    }
    concl = concl_map.get(ltype, ltype)

    return (f"规模：{scale}。"
            f"盈利：{profit}。"
            f"成长：{growth}。"
            f"纯度：{purity}。"
            f"竞争：{comp}。"
            f"市场：{market}。"
            f"结论：{concl}。")


def build_reasons(panel: pd.DataFrame, top_rank: int = 3) -> pd.DataFrame:
    """为每个行业前 top_rank 名生成 LEADER_REASON。

    返回：ts_code, name, l3_name, ind_rank, leader_type, sli, reason
    """
    p = panel.copy()
    for col, rank_col in (("revenue", "rev_ord"), ("total_mv", "mv_ord"),
                          ("gross_profit", "gp_ord"), ("total_assets", "asset_ord")):
        p[rank_col] = p.groupby("l3_code")[col].rank(ascending=False, method="min")
    p["reason"] = p.apply(_row_reason, axis=1)
    cols = ["ts_code", "name", "l3_name", "ind_rank", "leader_type",
            "sli", "reason"]
    return p.loc[p["ind_rank"] <= top_rank, [c for c in cols if c in p.columns]]


# ══════════════════════════════════════════════════════
# SLI V2 —— 产品层 LEADER_REASON
# ══════════════════════════════════════════════════════

def _row_reason_v2(r: pd.Series) -> str:
    chain = r.get("chain", "") or ""
    sub = r.get("subsector", "") or ""
    core = r.get("subsector", "") or "—"   # 细分赛道名即产品级名称（如 微型钻针/氯化法钛白粉）

    # 产品地位（细分赛道内 ordinal 排名）
    prod_rank = r.get("product_rank")
    prod_n = r.get("product_count")
    if pd.notna(prod_rank):
        prod_txt = f"细分赛道排名第{int(prod_rank)}"
        if pd.notna(prod_n):
            prod_txt += f"（赛道共{int(prod_n)}家）"
    else:
        prod_txt = "细分赛道排名—"
    rev_ord = r.get("prod_rev_ord")
    rev_txt = f"产品收入排名第{int(rev_ord)}" if pd.notna(rev_ord) else "产品收入排名—"

    # 盈利（相对行业中位数）
    profit = (f"ROE={_fmt(r.get('roe'))}%，高于行业中位数{_fmt(r.get('roe_adv'))}pct；"
              f"毛利率高于行业中位数{_fmt(r.get('grossprofit_margin_adv'))}pct；"
              f"ROIC={_fmt(r.get('roic'))}%")

    # 成长（收入/扣非利润/核心产品收入）
    growth = (f"收入同比{_fmt(r.get('or_yoy'))}%，扣非利润同比{_fmt(r.get('netprofit_yoy'))}%，"
              f"核心产品收入同比{_fmt(r.get('prod_rev_growth'))}%")

    # 主营纯度（产品级）
    purity = (f"核心产品占主营收入{_fmt(r.get('product_purity'), 0)}%"
              f"（置信度 {r.get('product_confidence', 'LOW')}）")

    # 相对强度（对细分赛道中位数）
    rs60 = r.get("rs60_v2") if "rs60_v2" in r.index else r.get("rs60")
    market = f"过去60日跑赢细分赛道{_fmt(rs60)}%"

    # 生命周期
    lc = r.get("lifecycle", "—")

    # 结论
    ltype = r.get("leader_type_v2", "NONE")
    concl_map = {
        "ABSOLUTE_LEADER": "绝对龙头：产业地位/产品地位/盈利/成长/纯度/市场全面占优",
        "PRODUCT_LEADER": "产品龙头：产品级收入与利润在细分赛道内绝对第一",
        "GROWTH_LEADER": "成长龙头：成长能力细分赛道领先",
        "PROFIT_LEADER": "盈利龙头：盈利能力细分赛道领先",
        "CHALLENGER": "龙头挑战者：正在快速追赶当前赛道龙头",
        "EMERGING_LEADER": "潜在新龙头：高成长+强市场认可，SLI快速上行",
        "NONE": "暂未达到 V2 龙头分类阈值",
    }
    concl = concl_map.get(ltype, ltype)

    return (f"产业链：{chain}。"
            f"细分赛道：{sub}。"
            f"核心产品：{core}。"
            f"产品地位：{prod_txt}，{rev_txt}。"
            f"盈利：{profit}。"
            f"成长：{growth}。"
            f"主营纯度：{purity}。"
            f"相对强度：{market}。"
            f"生命周期：{lc}。"
            f"最终判断：{concl}。")


def build_reasons_v2(panel: pd.DataFrame, top_rank: int = 5) -> pd.DataFrame:
    """为每个细分赛道前 top_rank 名生成 V2 LEADER_REASON。

    返回：ts_code, name, l3_name, subsector, sub_rank, leader_type_v2,
          sli_v2, reason
    """
    p = panel.copy()
    grp_key = p["l3_code"].astype(str) + "|" + p.get("subsector", "").fillna("")
    p["prod_rev_ord"] = p.groupby(grp_key)["matched_revenue"].rank(
        ascending=False, method="min")
    p["reason"] = p.apply(_row_reason_v2, axis=1)
    cols = ["ts_code", "name", "l3_name", "subsector", "sub_rank",
            "leader_type_v2", "sli_v2", "reason"]
    if "sub_rank" in p.columns:
        return p.loc[p["sub_rank"] <= top_rank, [c for c in cols if c in p.columns]]
    return p[[c for c in cols if c in p.columns]]
