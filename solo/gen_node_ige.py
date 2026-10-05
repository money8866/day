# -*- coding: utf-8 -*-
"""
按「产业链环节节点」口径重算 IGE 景气（不是申万三级分摊版）。

与 ige/engine.py 的关系
----------------------
同一个五因子流程：factors.build_level_table 本身支持任意分组列，
这里把分组列从 l3_code 换成 westock.db 的 node 即可。
宇宙 / 财务 / 行情 / 基准 全部复用 ige.engine.build_wide，口径与 IGE 完全一致。

节点成分是「重叠」的（一只票挂多个节点），所以先把 wide 按节点展开再聚合。
节点没有 L2/L1 上级可做样本收缩，故保留节点置信度一律按 HIGH。

小节点处理
----------
N < MIN_NODE_N 的节点直接放弃（只抓大节点大趋势）。
产物中「层级」列区分：可用(15~29) / 偏泛(30~99) / 通用(>=100 行业级大节点)。

用法:
  python -X utf8 gen_node_ige.py [YYYYMMDD]     # 默认 ige.config.SNAPSHOT_DATE
产物:
  report_daily/node_ige_<date>.csv
"""
import os
import sqlite3
import sys

import pandas as pd

from ige import factors as F
from ige.config import (CONF_MULT, GRADE_BANDS, MOM_BANDS, PERS_BANDS,
                        SNAPSHOT_DATE)
from ige.data import load_sli_panel
from ige.engine import build_wide

DB = r"D:/mystock/data/westock_cache/westock.db"
BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "report_daily")

MIN_NODE_N = 15     # 小节点放弃阈值（= IGE SAMPLE_FULL，无需上级混合）
GENERIC = 100       # >=100 只：行业级大节点（标记，不剔除）


def to_ts(code: str) -> str:
    """sh600006 -> 600006.SH ; sz000001 -> 000001.SZ ; bj920110 -> 920110.BJ"""
    if not code:
        return ""
    p, n = code[:2].lower(), code[2:]
    return f"{n}.{p.upper()}" if p in ("sh", "sz", "bj") else code


def load_node_members() -> dict[str, list[str]]:
    con = sqlite3.connect(DB)
    rows = list(con.execute(
        "SELECT node, stock_code FROM chain_stock_node WHERE is_theme_node=0"))
    con.close()
    d: dict[str, set] = {}
    for node, code in rows:
        d.setdefault(node, set()).add(to_ts(code))
    return {k: sorted(v) for k, v in d.items()}


def _mode(s: pd.Series) -> str:
    m = s.dropna().astype(str)
    return m.mode().iat[0] if len(m) else ""


def tier_of(n: int) -> str:
    if n >= GENERIC:
        return "通用"
    if n <= 3:
        return "锐利"
    if n <= 29:
        return "可用"
    return "偏泛"


def _grade(v) -> str:
    if pd.isna(v):
        return "NA"
    for thr, name in GRADE_BANDS:
        if v >= thr:
            return name
    return GRADE_BANDS[-1][1]


def main() -> None:
    end_date = (sys.argv[1] if len(sys.argv) > 1 else SNAPSHOT_DATE).replace("-", "")

    wide = build_wide(end_date)
    sli = load_sli_panel(end_date)
    bench = {f"b{k[1:]}": float(pd.to_numeric(wide[k], errors="coerce").mean())
             for k in ("w20", "w60", "w120") if k in wide.columns}

    mem = load_node_members()
    mp = pd.DataFrame([(ts, nd) for nd, ss in mem.items() for ts in ss],
                      columns=["ts_code", "node"]).drop_duplicates()
    wn = wide.merge(mp, on="ts_code", how="inner")
    print(f"节点 {len(mem)}  展开后 节点×个股 关系 {len(wn)}  "
          f"（命中宇宙 {wn['ts_code'].nunique()} 只）")

    # 节点 = 分组列，走与 L3 完全相同的五因子打分
    tab = F.build_level_table(wn, "node", sli, bench)
    tab = tab[tab["n_total"] >= MIN_NODE_N].copy()          # 放弃小节点

    # 生命周期 + 修正 → IGE_ADJ（节点无上级，置信度 HIGH）
    lc = tab.apply(lambda r: F.classify_lifecycle(
        r["dg0"], r["dg1"], r["pg0"], r["pg1"], r["macc"]), axis=1)
    tab = tab.join(pd.DataFrame(list(lc),
                                columns=["industry_lifecycle", "lifecycle_adjustment"],
                                index=tab.index))
    tab["ige_adj"] = (tab["ige"] + tab["lifecycle_adjustment"]).clip(0.0, 100.0)

    # MOM / PERSISTENCE：节点横截面分位（无混合步骤，直接对原始分做分位）
    tab["ige_mom"] = F.pct_rank(tab["mom_raw"])
    tab["ige_persistence"] = F.pct_rank(tab["pers_raw"])
    tab["ige_mom_band"] = F.band_of(tab["ige_mom"], MOM_BANDS)
    tab["ige_persistence_band"] = F.band_of(tab["ige_persistence"], PERS_BANDS)
    tab["industry_confidence"] = "HIGH"
    tab["ige_effective"] = (tab["ige_adj"] * CONF_MULT["HIGH"]).clip(0.0, 100.0)

    # 主导行业（参考列 + 类型标签用）
    dom = wn.groupby("node").agg(**{
        "主导申万一级": ("l1_name", _mode),
        "主导申万三级": ("l3_name", _mode)})
    tab = tab.join(dom)
    tab["l1_name"] = tab["主导申万一级"]

    tab["industry_elasticity_type"] = F.industry_type_of(tab)
    tab["industry_elasticity_state"] = F.industry_state_of(tab["industry_lifecycle"], tab["ige"])
    tab["acceleration_confirm"] = F.acceleration_confirm_of(tab)
    tab["structural_elasticity"] = F.structural_elasticity_of(tab)
    tab["cyclical_high_elasticity"] = F.cyclical_high_elasticity_of(tab)
    tab["cyclical_low_persistence"] = F.cyclical_low_persistence_of(tab)
    tab["ige_opportunity_type"] = F.opportunity_type_of(tab)
    tab["t120_rocket_core"] = F.rocket_core_of(tab, sia_col=None)
    tab["rank_tier"], tab["rank_reason"] = F.rank_tier(tab, sia_col=None)
    tab["industry_elasticity_grade"] = tab["ige_adj"].apply(_grade)
    tab["层级"] = tab["n_total"].apply(lambda n: tier_of(int(n)))

    out = tab.rename_axis("node").reset_index().rename(columns={
        "node": "节点", "n_total": "个股数", "n_q0": "有数据票数",
        "demand_score": "需求分", "profit_elasticity_score": "利润弹性分",
        "acceleration_score": "加速度分", "supply_demand_score": "供需分",
        "market_elasticity_score": "市场弹性分",
        "ige": "IGE基础", "ige_adj": "IGE_ADJ",
        "ige_mom": "IGE_MOM", "ige_persistence": "IGE_PERSISTENCE",
        "ige_effective": "IGE_EFFECTIVE", "rank_tier": "分层",
        "rank_reason": "分层理由", "industry_elasticity_grade": "等级",
        "industry_elasticity_type": "弹性类型",
        "industry_elasticity_state": "生命周期状态",
        "ige_opportunity_type": "机会类型",
        "structural_elasticity": "结构弹性",
        "cyclical_high_elasticity": "周期高弹性",
        "acceleration_confirm": "加速确认",
        "ige_mom_band": "动量档", "ige_persistence_band": "持续档",
        "industry_lifecycle": "生命周期", "lifecycle_adjustment": "修正",
        "t120_rocket_core": "最严格门槛"})
    cols = ["节点", "个股数", "有数据票数", "层级", "主导申万一级", "主导申万三级",
            "需求分", "利润弹性分", "加速度分", "供需分", "市场弹性分",
            "IGE基础", "生命周期", "修正", "IGE_ADJ", "IGE_MOM", "动量档",
            "IGE_PERSISTENCE", "持续档", "IGE_EFFECTIVE", "弹性类型",
            "生命周期状态", "机会类型", "结构弹性", "周期高弹性", "加速确认",
            "最严格门槛", "分层", "分层理由", "等级"]
    out["_t"] = out["分层"].replace(0, 6)
    out = out.sort_values(["_t", "IGE_ADJ", "IGE_MOM"],
                          ascending=[True, False, False],
                          na_position="last").drop(columns="_t")[cols]

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"node_ige_{end_date}.csv")
    out.to_csv(path, index=False, encoding="utf-8-sig")

    cnt = out["层级"].value_counts().to_dict()
    print(f"保留节点（N>={MIN_NODE_N}）{len(out)}   "
          + "  ".join(f"{k} {cnt.get(k, 0)}" for k in ("可用", "偏泛", "通用")))
    print(f"输出: {path}")


if __name__ == "__main__":
    main()
