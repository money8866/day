# -*- coding: utf-8 -*-
"""
SLI V2 产品层（Subsector/Product）
- 加载 细分赛道映射 / 产业链 / 产品别名 / 人工知识库（config/*.json）
- 从 fina_mainbz 提取产品级收入/利润（bz_item → 规范化产品名）
- 细分赛道归属（公司按产品关键词落入 SUBSECTOR，最长关键词优先，避免跨赛道污染）
- ProductPosition / ProductPurity / ProductLeadershipScore
- 人工知识与量化结果冲突检测（KNOWLEDGE_CONFLICT）

核心原则：
- 禁止伪造市场份额 → 用行业内排名构造 ProductLeadershipScore
- 市值大 ≠ 产品龙头 → ProductPosition 在 SUBSECTOR 内部按产品收入/利润排名
- 综合公司误判 → ProductPurity 用「匹配产品收入 / 公司总主营收入」
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any, Optional

import numpy as np
import pandas as pd

from .config import CONFIG_DIR, PRODUCT_CONF_DISCOUNT, PRODUCT_POS_W
from .features import AGG_ROWS

logger = logging.getLogger("sli.subsector")


# ══════════════════════════════════════════════════════
# 一、配置 JSON 加载（带缓存）
# ══════════════════════════════════════════════════════

@lru_cache(maxsize=8)
def _load_json(name: str) -> dict[str, Any]:
    path = os.path.join(CONFIG_DIR, name)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("产业知识文件缺失: %s", path)
        return {}
    except json.JSONDecodeError as exc:
        logger.warning("产业知识文件解析失败 %s: %s", path, exc)
        return {}


def load_subsector_map() -> dict[str, Any]:
    return _load_json("subsector_map.json")


def load_industry_chain() -> dict[str, Any]:
    return _load_json("industry_chain.json")


def load_product_alias() -> dict[str, str]:
    return _load_json("product_alias.json")


def load_industry_knowledge() -> dict[str, Any]:
    return _load_json("industry_knowledge.json")


# ══════════════════════════════════════════════════════
# 二、产品名称标准化
# ══════════════════════════════════════════════════════

def canonicalize(item: str, alias: dict[str, str]) -> str:
    """产品名标准化：优先全名匹配，其次前缀匹配（最长前缀），未命中原样返回。"""
    if not isinstance(item, str) or not item:
        return ""
    exact = alias.get(item)
    if exact:
        return exact
    best, best_len = item, 0
    for k, v in alias.items():
        if len(k) > best_len and item.startswith(k):
            best, best_len = v, len(k)
    return best


# ══════════════════════════════════════════════════════
# 三、产品级聚合（每股最新报告期）
# ══════════════════════════════════════════════════════

def product_agg(mainbz: pd.DataFrame, alias: dict[str, str],
                max_periods: int = 2) -> pd.DataFrame:
    """每股产品级聚合（取最近 max_periods 期主营构成，按规范化产品名求和）。

    返回列：ts_code, product, prod_revenue, prod_profit, total_bz, prod_rev_share
    prod_rev_share = 该产品收入 / 该公司全部主营产品收入（防未来函数：同期内口径一致）
    """
    if mainbz is None or mainbz.empty:
        return pd.DataFrame()
    mb = mainbz.copy()
    mb["bz_item"] = mb["bz_item"].fillna("").astype(str)
    for c in ("bz_sales", "bz_profit"):
        mb[c] = pd.to_numeric(mb.get(c), errors="coerce")
    mb = mb[mb["bz_sales"].notna() & (mb["bz_sales"] > 0)]
    mb = mb[mb["bz_item"].apply(
        lambda x: isinstance(x, str) and x not in AGG_ROWS and x != "")]
    if mb.empty:
        return pd.DataFrame()
    mb["_end"] = mb["end_date"].fillna("").astype(str)
    mb = mb.sort_values(["ts_code", "_end"]).groupby("ts_code").tail(max_periods * 40)
    mb["product"] = mb["bz_item"].apply(lambda x: canonicalize(x, alias))
    mb = mb[mb["product"] != ""]

    rows = []
    for code, g in mb.groupby("ts_code"):
        total = float(g["bz_sales"].sum())
        if total <= 0:
            continue
        for prod, pg in g.groupby("product"):
            rows.append({
                "ts_code": code,
                "product": prod,
                "prod_revenue": float(pg["bz_sales"].sum()),
                "prod_profit": float(pg["bz_profit"].sum(skipna=True)),
                "total_bz": total,
                "prod_rev_share": float(pg["bz_sales"].sum()) / total * 100.0,
            })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    # 每股核心产品 = 收入最大产品
    core_idx = out.groupby("ts_code")["prod_revenue"].idxmax()
    out["is_core"] = False
    out.loc[core_idx, "is_core"] = True
    return out.reset_index(drop=True)


# ══════════════════════════════════════════════════════
# 四、细分赛道归属
# ══════════════════════════════════════════════════════

def _subsector_keywords(subsector_map: dict[str, Any], l3_name: str) -> list[tuple[str, list[str]]]:
    """返回该三级行业的 (细分赛道, 关键词) 列表；未收录 → [行业名去后缀] 单一赛道。"""
    entry = subsector_map.get(l3_name)
    if entry and entry.get("subsectors"):
        return [(k, list(v)) for k, v in entry["subsectors"].items()]
    from .features import build_keywords, _strip_level
    base = _strip_level(l3_name) if l3_name else ""
    return [(base if base else l3_name, build_keywords(l3_name))]


def subsector_match(uni: pd.DataFrame, mainbz: pd.DataFrame,
                    subsector_map: dict[str, Any]) -> pd.DataFrame:
    """每只股票 → 细分赛道归属（含全部匹配与主赛道）。

    匹配规则：产品的 bz_item 命中赛道关键词（最长关键词优先，避免跨赛道污染）；
    未命中任何赛道关键词的股票 → 归属到该三级行业的默认赛道（产品数据缺失时为 LOW_CONFIDENCE）。

    返回列：ts_code, l3_name, l3_code, chain, subsector, matched_revenue,
            matched_profit, subsector_purity(%), confidence, is_primary
    """
    l3_map = uni[["ts_code", "l3_code", "l3_name"]].drop_duplicates()
    if mainbz is None or mainbz.empty:
        out = l3_map.copy()
        out["chain"] = ""
        out["subsector"] = out["l3_name"].fillna("")
        out["matched_revenue"] = np.nan
        out["matched_profit"] = np.nan
        out["subsector_purity"] = 50.0
        out["confidence"] = "LOW"
        out["is_primary"] = True
        return out

    alias = load_product_alias()
    agg = product_agg(mainbz, alias)
    if agg.empty:
        # 主营数据不可用：退化到行业名赛道
        out = l3_map.copy()
        out["chain"] = ""
        out["subsector"] = out["l3_name"].fillna("")
        out["matched_revenue"] = np.nan
        out["matched_profit"] = np.nan
        out["subsector_purity"] = 50.0
        out["confidence"] = "LOW"
        out["is_primary"] = True
        return out

    # 每产品 → (最长命中关键词, 所属赛道)
    def _match_products(g: pd.DataFrame, subs: list[tuple[str, list[str]]]) -> pd.DataFrame:
        """按最长关键词为每条产品行匹配唯一赛道（产品名先标准化）。"""
        matched = []
        for _, r in g.iterrows():
            item = str(r["_item"])
            best_kw, best_sub, best_len = "", "", 0
            for sub_name, kws in subs:
                for kw in kws:
                    if kw and kw in item and len(kw) > best_len:
                        best_kw, best_sub, best_len = kw, sub_name, len(kw)
            if best_sub:
                matched.append({"bz_item": item, "bz_sales": float(r["bz_sales"]),
                                "bz_profit": float(r.get("bz_profit") or np.nan),
                                "subsector": best_sub, "kw": best_kw})
        return pd.DataFrame(matched)

    # 复用于 product_agg 的原始行（重新从 mainbz 取，保证 bz_item 完整）
    mb = mainbz.copy()
    mb["bz_item"] = mb["bz_item"].fillna("").astype(str)
    for c in ("bz_sales", "bz_profit"):
        mb[c] = pd.to_numeric(mb.get(c), errors="coerce")
    mb = mb[mb["bz_sales"].notna() & (mb["bz_sales"] > 0)]
    mb = mb[mb["bz_item"].apply(
        lambda x: isinstance(x, str) and x not in AGG_ROWS and x != "")]
    if mb.empty:
        return pd.DataFrame()
    # 标准化产品名后再匹配，解决跨期命名不一致（如 刀具产品→精密刀具）导致的纯度低估
    mb["_item"] = mb["bz_item"].apply(lambda x: canonicalize(x, alias))
    mb["_end"] = mb["end_date"].fillna("").astype(str)
    mb = mb.sort_values(["ts_code", "_end"]).groupby("ts_code").tail(80)

    rows = []
    for code, l3, l3n in l3_map.itertuples(index=False):
        subs = _subsector_keywords(subsector_map, l3n)
        g = mb[mb["ts_code"] == code]
        total = float(g["bz_sales"].sum()) if len(g) else np.nan
        mm = _match_products(g, subs)
        if mm.empty:
            # 未识别产品 → 行业级默认赛道（避免污染具体细分赛道）
            default_sub = l3n if l3n else subs[0][0]
            rows.append({"ts_code": code, "l3_code": l3, "l3_name": l3n,
                         "chain": subsector_map.get(l3n, {}).get("chain", ""),
                         "subsector": default_sub,
                         "matched_revenue": np.nan, "matched_profit": np.nan,
                         "subsector_purity": 50.0, "confidence": "LOW",
                         "is_primary": True})
            continue
        for sub, sg in mm.groupby("subsector"):
            rev = float(sg["bz_sales"].sum())
            rows.append({"ts_code": code, "l3_code": l3, "l3_name": l3n,
                         "chain": subsector_map.get(l3n, {}).get("chain", ""),
                         "subsector": sub,
                         "matched_revenue": rev,
                         "matched_profit": float(sg["bz_profit"].sum(skipna=True)),
                         "subsector_purity": rev / total * 100.0 if total and total > 0 else 50.0,
                         "confidence": "HIGH", "is_primary": False})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # 主赛道 = 匹配收入最高者
    pri_idx = df.loc[df["matched_revenue"].notna()].groupby("ts_code")[
        "matched_revenue"].idxmax()
    df.loc[pri_idx, "is_primary"] = True
    return df


def primary_subsector(df: pd.DataFrame) -> pd.DataFrame:
    """从 subsector_match 结果中提取每股主赛道（panel 用）。

    返回列：ts_code, subsector, chain, subsector_purity(%), product_confidence
    """
    if df is None or df.empty:
        return pd.DataFrame()
    prim = df[df["is_primary"]].copy()
    prim = prim.rename(columns={"confidence": "product_confidence",
                                "subsector_purity": "product_purity"})
    prim["product_confidence"] = prim["product_confidence"].fillna("LOW")
    prim["product_purity"] = pd.to_numeric(prim.get("product_purity"), errors="coerce").fillna(50.0)
    return prim[["ts_code", "subsector", "chain",
                 "product_purity", "product_confidence"]]


def product_rev_growth(mainbz: pd.DataFrame, uni: pd.DataFrame,
                       subsector_map: dict[str, Any]) -> pd.DataFrame:
    """核心产品收入同比增速代理（最近两期主营构成中「匹配赛道收入」的同比）。

    返回：ts_code, prod_rev_growth(%)
    用于 V2 ChallengerScore / NEXT_LEADER 的「产品收入增长」维度。
    主营构成按年披露，故该代理为年度增速，属慢变量，不会每天跳变。
    """
    if mainbz is None or mainbz.empty or uni is None or uni.empty:
        return pd.DataFrame()
    l3_map = uni[["ts_code", "l3_name"]].drop_duplicates()
    alias = load_product_alias()
    mb = mainbz.copy()
    mb["bz_item"] = mb["bz_item"].fillna("").astype(str)
    mb["bz_sales"] = pd.to_numeric(mb.get("bz_sales"), errors="coerce")
    mb = mb[mb["bz_sales"].notna() & (mb["bz_sales"] > 0)]
    mb = mb[mb["bz_item"].apply(
        lambda x: isinstance(x, str) and x not in AGG_ROWS and x != "")]
    if mb.empty:
        return pd.DataFrame()
    # 产品名标准化后匹配，保持与赛道归属同口径
    mb["_item"] = mb["bz_item"].apply(lambda x: canonicalize(x, alias))
    mb["_end"] = mb["end_date"].fillna("").astype(str)
    # 每股最近 4 期（2年 × 1期/年，留足两期对比）
    mb = mb.sort_values(["ts_code", "_end"]).groupby("ts_code").tail(80)

    rows = []
    for code, l3n in l3_map.itertuples(index=False):
        subs = _subsector_keywords(subsector_map, str(l3n))
        kws = [kw for _, kw_list in subs for kw in kw_list]
        if not kws:
            continue
        g = mb[mb["ts_code"] == code]
        per_period: dict[str, float] = {}
        for _end, gg in g.groupby("_end"):
            matched = gg[gg["_item"].apply(lambda x: any(k in x for k in kws))]["bz_sales"]
            if len(matched):
                per_period[_end] = float(matched.sum())
        if len(per_period) >= 2:
            ends = sorted(per_period)
            p1, p2 = per_period[ends[-2]], per_period[ends[-1]]
            if p1 and p1 > 0:
                rows.append({"ts_code": code,
                             "prod_rev_growth": round((p2 / p1 - 1.0) * 100.0, 2)})
    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════
# 五、ProductPosition（细分赛道内部排名）
# ══════════════════════════════════════════════════════

def product_position(panel: pd.DataFrame, subsector_df: pd.DataFrame) -> pd.DataFrame:
    """计算每股 ProductPosition（0~100）。

    ProductPosition = 40%产品收入排名 + 30%产品利润排名 + 20%产能/销量排名(缺失剔除)
                      + 10%市值排名，均在 (l3_code, subsector) 内 percentile rank。

    返回 panel 加列：product_rank, product_count, product_rev_rank, product_profit_rank,
                     product_cap_rank, product_position, product_low_sample
    """
    panel = panel.copy()
    if subsector_df is None or subsector_df.empty:
        panel["product_rank"] = np.nan
        panel["product_count"] = 0
        panel["product_position"] = np.nan
        panel["product_low_sample"] = False
        return panel

    # 每股主赛道 + 匹配收入/利润
    prim = subsector_df[subsector_df["is_primary"]].copy()
    prim = prim[["ts_code", "l3_code", "subsector", "matched_revenue", "matched_profit"]]
    # 防列冲突：panel 可能已含 l3_code/subsector，重命名 prim 侧列
    rename_map = {c: f"prod_{c}" for c in ("l3_code", "subsector") if c in panel.columns}
    prim = prim.rename(columns=rename_map)
    p = panel.merge(prim, on="ts_code", how="left")
    if "subsector" not in p.columns:
        p["subsector"] = p["subsector_prod"]
    p["_grp"] = p["l3_code"].astype(str) + "|" + p["subsector"].fillna("")

    # 产品收入排名（缺失时用营业收入兜底，避免全 NaN）
    p["_rev_for_rank"] = p["matched_revenue"].where(p["matched_revenue"].notna(),
                                                    pd.to_numeric(p.get("revenue"), errors="coerce"))
    p["_profit_for_rank"] = p["matched_profit"]
    p["_cap_for_rank"] = pd.to_numeric(p.get("total_mv"), errors="coerce")

    def _rank_pct(s: pd.Series) -> pd.Series:
        return s.groupby(p["_grp"]).rank(pct=True, na_option="keep") * 100.0

    p["product_rev_rank"] = _rank_pct(p["_rev_for_rank"])
    p["product_profit_rank"] = _rank_pct(p["_profit_for_rank"])
    p["product_cap_rank"] = _rank_pct(p["_cap_for_rank"])

    cols = ["product_rev_rank", "product_profit_rank", "product_cap_rank"]
    ws = [PRODUCT_POS_W["revenue"], PRODUCT_POS_W["profit"], PRODUCT_POS_W["mv"]]
    # capacity/市占率无可靠数据 → 剔除该分量（权重占比 20% 被剔除），权重重归一化
    arr = p[cols].to_numpy(dtype=float)
    valid = ~np.isnan(arr)
    w = np.asarray(ws, dtype=float)
    wsum = valid @ w
    wn = np.divide(w[None, :] * valid, wsum[:, None],
                   out=np.zeros_like(arr), where=wsum[:, None] > 0)
    p["product_position"] = np.where(
        wsum > 0, np.nansum(wn * arr, axis=1), np.nan)
    p["product_position"] = pd.Series(p["product_position"], index=p.index).clip(0, 100)

    cnt = p.groupby("_grp")["ts_code"].transform("count")
    p["product_count"] = cnt
    p["product_low_sample"] = (cnt > 0) & (cnt < 5)
    p["product_rank"] = p.groupby("_grp")["product_position"].rank(
        ascending=False, method="min")

    out_cols = ["product_position", "product_rank", "product_count",
                "product_rev_rank", "product_profit_rank", "product_cap_rank",
                "product_low_sample", "matched_revenue", "matched_profit"]
    for c in out_cols:
        panel[c] = p[c]
    return panel


# ══════════════════════════════════════════════════════
# 六、产品级领导力（不伪造市场份额）
# ══════════════════════════════════════════════════════

def product_leadership(panel: pd.DataFrame) -> pd.DataFrame:
    """ProductLeadershipScore：产品收入/利润/市值在细分赛道内的排名综合。

    返回 panel 加列：product_leadership, is_product_leader_rank1
    无真实市占率数据 → 不输出 MarketShare，只用排名。
    """
    panel = panel.copy()
    p = panel
    cols = ["product_rev_rank", "product_profit_rank", "product_cap_rank"]
    ws = [0.50, 0.30, 0.20]
    arr = p[cols].to_numpy(dtype=float)
    valid = ~np.isnan(arr)
    w = np.asarray(ws, dtype=float)
    wsum = valid @ w
    wn = np.divide(w[None, :] * valid, wsum[:, None],
                   out=np.zeros_like(arr), where=wsum[:, None] > 0)
    p["product_leadership"] = np.where(
        wsum > 0, np.nansum(wn * arr, axis=1), np.nan)
    p["product_leadership"] = pd.Series(p["product_leadership"], index=p.index).clip(0, 100)
    p["is_product_leader_rank1"] = p["product_rank"] == 1
    return panel


# ══════════════════════════════════════════════════════
# 七、人工知识冲突检测
# ══════════════════════════════════════════════════════

def knowledge_check(panel: pd.DataFrame, knowledge: dict[str, Any]) -> pd.DataFrame:
    """人工知识辅助验证：已知龙头 vs 量化 Top1，不一致时标记 KNOWLEDGE_CONFLICT。

    返回 panel 加列：knowledge_conflict, known_leader, quant_leader
    """
    panel = panel.copy()
    panel["knowledge_conflict"] = False
    panel["known_leader"] = ""
    panel["quant_leader"] = ""

    # 每个细分赛道 → 量化 Top1
    if {"l3_code", "subsector", "product_position"} <= set(panel.columns):
        grp = panel[["l3_code", "subsector", "name", "ts_code", "product_position"]]
        top1 = grp.loc[grp.groupby(["l3_code", "subsector"])["product_position"].idxmax()]
        qmap = {(r.l3_code, r.subsector): r.name for r in top1.itertuples()}
        for sub_name, info in knowledge.items():
            if not isinstance(info, dict):
                continue  # 顶层说明性字符串键
            known = [n for n in info.get("known_leaders", [])]
            if not known:
                continue
            kws = info.get("keywords", [])
            for (l3c, sub), qname in qmap.items():
                # 判断该 (l3, subsector) 是否对应此知识条目（名称或关键词近似）
                if sub != sub_name:
                    continue
                # 跳过行业级默认赛道（产品未识别，Top1 无赛道龙头语义）
                l3n_vals = panel.loc[(panel["l3_code"] == l3c)
                                     & (panel["subsector"] == sub), "l3_name"]
                if len(l3n_vals) and sub == str(l3n_vals.iloc[0]):
                    continue
                hit = any(qname == k or k in str(qname) for k in known)
                mask = (panel["l3_code"] == l3c) & (panel["subsector"] == sub)
                panel.loc[mask, "known_leader"] = "、".join(known)
                panel.loc[mask, "quant_leader"] = str(qname)
                if not hit:
                    panel.loc[mask, "knowledge_conflict"] = True
    return panel


# ══════════════════════════════════════════════════════
# 八、产业链标注
# ══════════════════════════════════════════════════════

def attach_chain(panel: pd.DataFrame, chain_db: dict[str, Any]) -> pd.DataFrame:
    """按细分赛道所属产业链（subsector_map 的 chain）标注 chain_name；无法归类的留空。"""
    panel = panel.copy()
    if "chain" in panel.columns:
        panel["chain_name"] = panel["chain"].fillna("")
    else:
        panel["chain_name"] = ""
    return panel
