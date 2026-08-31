# -*- coding: utf-8 -*-
"""
SLI V2 固定读取接口
===================
下游程序（ER20 / Alpha / HVT / RE-BREAKOUT 等）通过本模块读取 SLI 结果，
**无需关心 SLI 何时重算** —— 接口自动返回 `asof` 之前最近一次可用快照
（asof 缺省为今天；若今天尚未重算，自动回退到上一份结果）。

数据源优先级：
    1. SQLite（sli.db 的 leaderboard_v2 表，多日期快照）
    2. 回退 CSV（output/sli_full_<date>.csv，由 runner 每次全量导出）

返回值约定：
    - 所有便捷函数返回 pandas.DataFrame，且 `.attrs` 携带元信息：
      snapshot_date（快照日期）/ asof / age_days（快照年龄，天）/ source / n_stocks
    - 单股查询 get_stock 返回 dict（含同样元信息）

用法示例：
    from sli.reader import latest_date, get_panel, get_leaderboard_v2, \\
        get_subsector_top5, get_next_leaders, get_earnings_turn, get_radar, get_stock

    meta = latest_date()                  # 最近可用快照日期（如 "20260828"）
    panel = get_panel()                   # 全量面板，自动回退到最近快照
    top100 = get_leaderboard_v2(top=100)  # 全市场 Top100
    sub = get_subsector_top5(subsector="PCB刀具")
    nxt = get_next_leaders(top=30)
    turn = get_earnings_turn(top=30)
    radar = get_radar()
    stock = get_stock(name="鼎泰高科")

面板固定列契约（英文，稳定不随 CSV 中文表头变化）：
    ts_code, name, l3_code, l3_name, subsector, chain,
    sli_v2, industry_score, product_position, profit_quality, growth_v2,
    purity_v2, moat_v2, market_v2, trend_v2, product_purity,
    sub_rank, ind_rank_v2, leader_type_v2, dominance, lifecycle,
    NEXT_LEADER, LEADER_CHALLENGER, EARNINGS_TURN, LEADER_EARNINGS_TURN,
    SUPER_LEADER, challenger_score, sli_v2_T, sli_v2_T60,
    sli（V1）, ind_rank, leader_type（V1）
"""
from __future__ import annotations

import glob
import logging
import os
import sqlite3
from datetime import datetime
from functools import lru_cache
from typing import Any, Optional

import numpy as np
import pandas as pd

from .config import DB_PATH, OUTPUT_DIR

logger = logging.getLogger("sli.reader")

# ── V2 面板固定列契约 ─────────────────────────────────
SCHEMA_V2 = [
    "ts_code", "name", "l3_code", "l3_name", "subsector", "chain",
    "sli_v2", "industry_score", "product_position", "profit_quality",
    "growth_v2", "purity_v2", "moat_v2", "market_v2", "trend_v2",
    "product_purity", "sub_rank", "ind_rank_v2", "leader_type_v2",
    "dominance", "lifecycle", "NEXT_LEADER", "LEADER_CHALLENGER",
    "EARNINGS_TURN", "LEADER_EARNINGS_TURN", "SUPER_LEADER",
    "challenger_score", "sli_v2_T", "sli_v2_T60", "sli", "ind_rank",
    "leader_type",
]


# ── 基础工具 ──────────────────────────────────────────

def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _csv_dates() -> list[str]:
    """扫描 output/ 下 sli_full_*.csv 文件名中的日期，降序返回。"""
    dates = []
    for p in glob.glob(os.path.join(OUTPUT_DIR, "sli_full_*.csv")):
        base = os.path.basename(p)
        d = base.replace("sli_full_", "").replace(".csv", "")
        if d.isdigit() and len(d) == 8:
            dates.append(d)
    return sorted(dates, reverse=True)


def _db_dates(asof: str) -> list[str]:
    """SQLite 中 leaderboard_v2 的可用快照日期（<=asof），降序。"""
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            cur = conn.execute(
                "SELECT DISTINCT trade_date FROM leaderboard_v2 "
                "WHERE trade_date <= ? ORDER BY trade_date DESC", (asof,))
            return [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
    except Exception as exc:  # 表不存在等
        logger.warning("SQLite 读取失败：%s", exc)
        return []


def latest_date(asof: Optional[str] = None) -> Optional[str]:
    """asof 之前最近一次可用快照日期（YYYYMMDD）。

    asof=None 表示今天；若今天未重算，自动回退到上一份可用快照。
    无任何快照时返回 None。
    """
    asof = asof or _today()
    if len(asof) == 8 and asof.isdigit():
        pass
    elif "-" in asof:
        asof = asof.replace("-", "")
    else:
        raise ValueError(f"asof 格式应为 YYYYMMDD 或 YYYY-MM-DD，收到：{asof!r}")
    db = _db_dates(asof)
    if db:
        return db[0]
    for d in _csv_dates():
        if d <= asof:
            return d
    return None


def _meta(snapshot_date: str, asof: str, source: str, n_stocks: int) -> dict[str, Any]:
    return {
        "snapshot_date": snapshot_date,
        "asof": asof,
        "age_days": max(0, (int(asof) - int(snapshot_date))) if asof.isdigit() else 0,
        "source": source,
        "n_stocks": n_stocks,
    }


@lru_cache(maxsize=16)
def _load_panel_db(snapshot_date: str) -> Optional[pd.DataFrame]:
    """从 SQLite 读取某日期 V2 全面板。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            "SELECT * FROM leaderboard_v2 WHERE trade_date=?", conn,
            params=(snapshot_date,))
    finally:
        conn.close()
    if df.empty:
        return None
    return df


@lru_cache(maxsize=16)
def _load_panel_csv(snapshot_date: str) -> Optional[pd.DataFrame]:
    """回退：从 output/sli_full_<date>.csv 读取某日期全面板。"""
    path = os.path.join(OUTPUT_DIR, f"sli_full_{snapshot_date}.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, encoding="utf-8-sig")
    df["trade_date"] = snapshot_date
    return df


# ── 核心接口 ──────────────────────────────────────────

def get_panel(asof: Optional[str] = None) -> pd.DataFrame:
    """asof 之前最近一次可用快照的全面板（V1+V2 全部列）。

    若该日期 SQLite 无 V2 表，自动回退到 output/ 下的 sli_full CSV。
    返回 DataFrame，`.attrs` 携带 snapshot_date / age_days / source 等元信息。
    """
    asof = asof or _today()
    snapshot = latest_date(asof)
    if snapshot is None:
        raise FileNotFoundError(
            f"未找到 {asof} 之前的 SLI 快照。请先运行 python -m sli.main 生成一次结果。")
    db_df = _load_panel_db(snapshot)
    if db_df is not None:
        df, source = db_df, "sqlite"
    else:
        df = _load_panel_csv(snapshot)
        if df is None:
            raise FileNotFoundError(f"快照 {snapshot} 数据缺失（SQLite 与 CSV 均不可读）")
        source = "csv"
    df.attrs["_sli_meta"] = _meta(snapshot, asof, source, int(len(df)))
    return df


def _sort_v2(panel: pd.DataFrame, top: Optional[int] = None) -> pd.DataFrame:
    p = panel.sort_values("sli_v2", ascending=False, na_position="last").copy()
    p = p.drop(columns=["rank"], errors="ignore").reset_index(drop=True)
    p.insert(0, "rank", np.arange(1, len(p) + 1))
    if top:
        p = p.head(top)
    return p


def get_leaderboard_v2(asof: Optional[str] = None, top: Optional[int] = 100) -> pd.DataFrame:
    """输出1：全市场 Top100（SLI_V2 视角）。top=None 返回全部。"""
    panel = get_panel(asof)
    df = _sort_v2(panel, top)
    df.attrs["_sli_meta"] = panel.attrs["_sli_meta"]
    return df


def get_subsector_top5(asof: Optional[str] = None, top: int = 5,
                       subsector: Optional[str] = None) -> pd.DataFrame:
    """输出2：每个细分赛道 Top5。可传 subsector 过滤单个赛道。"""
    panel = get_panel(asof)
    p = panel[panel["sub_rank"] <= top].copy()
    if subsector:
        p = p[p["subsector"].astype(str) == subsector]
    p = p.sort_values(["l3_code", "subsector", "sub_rank"])
    p["sub_rank"] = p["sub_rank"].fillna(0).astype(int)
    p = p.drop(columns=["rank"], errors="ignore").reset_index(drop=True)
    p.insert(0, "rank", p["sub_rank"])
    p.attrs["_sli_meta"] = panel.attrs["_sli_meta"]
    return p


def get_next_leaders(asof: Optional[str] = None, top: Optional[int] = 30) -> pd.DataFrame:
    """输出3：下一代龙头（NEXT_LEADER=TRUE，按 ChallengerScore+Growth+SLI提升排序）。"""
    panel = get_panel(asof)
    p = panel[panel["NEXT_LEADER"].fillna(False) == True].copy()  # noqa: E712
    if p.empty:
        out = p
    else:
        p["sli_v2_delta"] = p["sli_v2_T"] - p["sli_v2_T60"]
        p["_key"] = (p["challenger_score"].fillna(0)
                     + p["growth_v2"].fillna(0)
                     + p["sli_v2_delta"].clip(0))
        out = p.sort_values("_key", ascending=False).drop(
            columns=["rank"], errors="ignore").reset_index(drop=True)
        out.insert(0, "rank", np.arange(1, len(out) + 1))
        if top:
            out = out.head(top)
    out.attrs["_sli_meta"] = panel.attrs["_sli_meta"]
    return out


def get_earnings_turn(asof: Optional[str] = None, top: Optional[int] = 30) -> pd.DataFrame:
    """输出4：龙头+业绩拐点（LEADER_EARNINGS_TURN=TRUE，SLI_V2≥80 且拐点）。"""
    panel = get_panel(asof)
    p = panel[panel["LEADER_EARNINGS_TURN"].fillna(False) == True].copy()  # noqa: E712
    if not p.empty:
        p["turn_type"] = np.where(
            p.get("EARNINGS_TURN", pd.Series(False, index=p.index)).fillna(False) == True,  # noqa: E712
            "负转正",
            np.where(p.get("EARNINGS_ACCELERATION", pd.Series(False, index=p.index))
                     .fillna(False) == True, "加速", ""))  # noqa: E712
        p = _sort_v2(p, top)
    p.attrs["_sli_meta"] = panel.attrs["_sli_meta"]
    return p


def get_radar(asof: Optional[str] = None) -> pd.DataFrame:
    """产业龙头雷达：每个细分赛道 第1/2/3名 + 各类型龙头。"""
    panel = get_panel(asof)
    rows = []
    grp_key = panel["l3_code"].astype(str) + "|" + panel.get("subsector", "").fillna("")
    for _, g in panel.groupby(grp_key):
        g = g.sort_values("sli_v2", ascending=False)
        top3 = g.head(3)
        d = {
            "产业链": g.iloc[0].get("chain", ""),
            "三级行业": g.iloc[0].get("l3_name", ""),
            "细分赛道": g.iloc[0].get("subsector", ""),
            "第1名": str(top3.iloc[0].get("name", "")) if len(top3) else "",
            "第2名": str(top3.iloc[1].get("name", "")) if len(top3) > 1 else "",
            "第3名": str(top3.iloc[2].get("name", "")) if len(top3) > 2 else "",
        }
        for typ, col in (("绝对龙头", "is_ABSOLUTE_LEADER"),
                         ("产品龙头", "is_PRODUCT_LEADER"),
                         ("成长龙头", "is_GROWTH_LEADER"),
                         ("盈利龙头", "is_PROFIT_LEADER"),
                         ("挑战者", "is_CHALLENGER"),
                         ("下一代龙头", "NEXT_LEADER")):
            m = g[g.get(col, pd.Series(False, index=g.index)).fillna(False) == True]  # noqa: E712
            d[typ] = str(m.iloc[0].get("name", "")) if len(m) else ""
        rows.append(d)
    out = pd.DataFrame(rows)
    out.attrs["_sli_meta"] = panel.attrs["_sli_meta"]
    return out


def get_stock(asof: Optional[str] = None, ts_code: Optional[str] = None,
              name: Optional[str] = None) -> dict[str, Any]:
    """单只股票全部 SLI V2 指标。ts_code 或 name 二选一。"""
    panel = get_panel(asof)
    if ts_code:
        row = panel[panel["ts_code"].astype(str) == ts_code]
    elif name:
        row = panel[panel["name"].astype(str) == name]
    else:
        raise ValueError("ts_code 或 name 必须提供一个")
    if row.empty:
        raise KeyError(f"未在快照中找到：ts_code={ts_code} name={name}")
    out = row.iloc[0].to_dict()
    out["_sli_meta"] = panel.attrs["_sli_meta"]
    return out


# ── CLI：快速检查最近快照 ─────────────────────────────

def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="SLI V2 固定读取接口")
    ap.add_argument("--asof", default=None, help="截至日期 YYYYMMDD，缺省今天")
    ap.add_argument("--top", type=int, default=10, help="排行榜条数")
    args = ap.parse_args()
    d = latest_date(args.asof)
    print(f"最近可用快照: {d}")
    top = get_leaderboard_v2(asof=args.asof, top=args.top)
    print(f"快照元信息: {top.attrs['_sli_meta']}")
    cols = ["rank", "ts_code", "name", "l3_name", "subsector",
            "sli_v2", "leader_type_v2", "lifecycle"]
    cols = [c for c in cols if c in top.columns]
    print(top[cols].to_string(index=False))


if __name__ == "__main__":
    main()
