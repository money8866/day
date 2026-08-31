# -*- coding: utf-8 -*-
"""
SLI 报告层
- 全市场龙头排行榜（A）
- 每三级行业 Top3（B）
- 龙头加速榜（C）
- 下一代龙头榜（D）
- LEADER_REASON 表
- CSV + SQLite 双保存 + 数据质量报告
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("sli.report")

# 排行榜（A）列：中文表头 → 面板列名
LEADER_COLS = [
    ("排名", "rank"), ("代码", "ts_code"), ("名称", "name"),
    ("一级行业", "l1_name"), ("二级行业", "l2_name"), ("三级行业", "l3_name"),
    ("SLI", "sli"), ("Scale", "scale_score"), ("Profit", "profit_score"),
    ("Growth", "growth_score"), ("Purity", "purity_score"), ("Moat", "moat_score"),
    ("Market", "market_score"), ("Trend", "trend_score"),
    ("行业排名", "ind_rank"), ("LeaderGap", "leader_gap"), ("差距级别", "gap_band"),
    ("生命周期", "lifecycle"), ("龙头类型", "leader_type"),
    ("NEXT_LEADER", "NEXT_LEADER"), ("EARNINGS_TURN", "LEADER_EARNINGS_TURN"),
]

TOP3_COLS = [
    ("三级行业", "l3_name"), ("排名", "rank"), ("代码", "ts_code"),
    ("名称", "name"), ("SLI", "sli"), ("龙头类型", "leader_type"),
    ("生命周期", "lifecycle"),
]

ACCEL_COLS = [
    ("排名", "rank"), ("代码", "ts_code"), ("名称", "name"), ("三级行业", "l3_name"),
    ("SLI", "sli"), ("Growth", "growth_score"), ("Market", "market_score"),
    ("SLI60提升", "sli60_delta"), ("加速分", "accel_score"),
    ("NEXT_LEADER强化", "NEXT_LEADER_STRONG"), ("生命周期", "lifecycle"),
    ("龙头类型", "leader_type"),
]

NEXT_COLS = [
    ("排名", "rank"), ("代码", "ts_code"), ("名称", "name"), ("三级行业", "l3_name"),
    ("SLI", "sli"), ("Growth", "growth_score"), ("Market", "market_score"),
    ("Profit", "profit_score"), ("SLI_T", "sli_T"), ("SLI_T60", "sli_T60"),
    ("生命周期", "lifecycle"), ("龙头类型", "leader_type"),
]

# ══════════════════════════════════════════════════════
# SLI V2 输出列定义（细分赛道级）
# ══════════════════════════════════════════════════════
LEADER_V2_COLS = [
    ("排名", "rank"), ("代码", "ts_code"), ("名称", "name"),
    ("一级行业", "l1_name"), ("二级行业", "l2_name"), ("三级行业", "l3_name"),
    ("产业链", "chain"), ("细分赛道", "subsector"), ("核心产品", "subsector"),
    ("SLI_V2", "sli_v2"), ("Industry", "industry_score"),
    ("Product", "product_position"), ("ProfitQ", "profit_quality"),
    ("Growth", "growth_v2"), ("Purity", "purity_v2"), ("Moat", "moat_v2"),
    ("Market", "market_v2"), ("Trend", "trend_v2"),
    ("赛道排名", "sub_rank"), ("行业排名", "ind_rank_v2"),
    ("龙头类型", "leader_type_v2"), ("Dominance", "dominance"),
    ("生命周期", "lifecycle"), ("NEXT", "NEXT_LEADER"),
    ("CHALLENGER", "LEADER_CHALLENGER"), ("拐点", "LEADER_EARNINGS_TURN"),
    ("SUPER", "SUPER_LEADER"),
]

SUBSECTOR_TOP_COLS = [
    ("三级行业", "l3_name"), ("细分赛道", "subsector"), ("排名", "rank"),
    ("代码", "ts_code"), ("名称", "name"), ("SLI_V2", "sli_v2"),
    ("Product", "product_position"), ("Purity", "purity_v2"),
    ("龙头类型", "leader_type_v2"), ("Dominance", "dominance"),
    ("生命周期", "lifecycle"),
]

NEXT_V2_COLS = [
    ("排名", "rank"), ("代码", "ts_code"), ("名称", "name"),
    ("三级行业", "l3_name"), ("细分赛道", "subsector"),
    ("SLI_V2", "sli_v2"), ("Growth", "growth_v2"),
    ("Product", "product_position"), ("Market", "market_v2"),
    ("Challenger", "challenger_score"), ("SLI_V2提升", "sli_v2_delta"),
    ("龙头类型", "leader_type_v2"), ("生命周期", "lifecycle"),
]

TURN_V2_COLS = [
    ("排名", "rank"), ("代码", "ts_code"), ("名称", "name"),
    ("三级行业", "l3_name"), ("细分赛道", "subsector"),
    ("SLI_V2", "sli_v2"), ("拐点类型", "turn_type"),
    ("龙头类型", "leader_type_v2"), ("生命周期", "lifecycle"),
]


def _pick(df: pd.DataFrame, cols: list[tuple[str, str]]) -> pd.DataFrame:
    keep = [(zh, en) for zh, en in cols if en in df.columns]
    out = df[[en for _, en in keep]].copy()
    out.columns = [zh for zh, _ in keep]
    return out


class SliReport:
    def __init__(self, output_dir: str, db_path: str) -> None:
        self.output_dir = output_dir
        self.db_path = db_path
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

    # ── A. 全市场龙头排行榜 ────────────────────────────
    def leaderboard(self, panel: pd.DataFrame, date: str, top: Optional[int] = None) -> str:
        df = panel.sort_values("sli", ascending=False, na_position="last").reset_index(drop=True)
        df.insert(0, "rank", np.arange(1, len(df) + 1))
        if "ind_rank" in df.columns:
            df["ind_rank"] = df["ind_rank"].fillna(0).astype(int)
        if top:
            df = df.head(top)
        out = _pick(df, LEADER_COLS)
        path = os.path.join(self.output_dir, f"sli_leaderboard_{date}.csv")
        out.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info("排行榜已写入 %s（%d 行）", path, len(out))
        return path

    # ── B. 每三级行业 Top3 ─────────────────────────────
    def industry_top3(self, panel: pd.DataFrame, date: str) -> str:
        p = panel[panel["ind_rank"] <= 3].copy()
        p = p.sort_values(["l3_code", "ind_rank"])
        p["ind_rank"] = p["ind_rank"].fillna(0).astype(int)
        p.insert(0, "rank", p["ind_rank"])
        out = _pick(p, TOP3_COLS)
        path = os.path.join(self.output_dir, f"sli_industry_top3_{date}.csv")
        out.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info("行业Top3已写入 %s（%d 行）", path, len(out))
        return path

    # ── C. 龙头加速榜 ──────────────────────────────────
    def acceleration(self, panel: pd.DataFrame, date: str, top: Optional[int] = 100) -> str:
        p = panel[panel["LEADER_ACCELERATION"]].copy()
        p["sli60_delta"] = p["sli_T"] - p["sli_T60"]
        p["accel_score"] = (p["sli"] * 0.4 + p["growth_score"] * 0.3
                            + p["market_score"] * 0.2 + p["sli60_delta"].clip(0) * 0.1)
        p = p.sort_values("accel_score", ascending=False).reset_index(drop=True)
        p.insert(0, "rank", np.arange(1, len(p) + 1))
        if top:
            p = p.head(top)
        out = _pick(p, ACCEL_COLS)
        path = os.path.join(self.output_dir, f"sli_acceleration_{date}.csv")
        out.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info("龙头加速榜已写入 %s（%d 行）", path, len(out))
        return path

    # ── D. 下一代龙头榜 ────────────────────────────────
    def next_leader(self, panel: pd.DataFrame, date: str, top: Optional[int] = 30) -> str:
        p = panel[panel["NEXT_LEADER"]].copy()
        p = p.sort_values("sli", ascending=False).reset_index(drop=True)
        p.insert(0, "rank", np.arange(1, len(p) + 1))
        if top:
            p = p.head(top)
        out = _pick(p, NEXT_COLS)
        path = os.path.join(self.output_dir, f"sli_next_leader_{date}.csv")
        out.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info("下一代龙头榜已写入 %s（%d 行）", path, len(out))
        return path

    # ── LEADER_REASON ──────────────────────────────────
    def reasons(self, reasons: pd.DataFrame, date: str) -> str:
        path = os.path.join(self.output_dir, f"sli_leader_reasons_{date}.csv")
        reasons.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info("LEADER_REASON 已写入 %s（%d 行）", path, len(reasons))
        return path

    # ── 行业筛选输出 ───────────────────────────────────
    def industry_view(self, panel: pd.DataFrame, reasons: pd.DataFrame,
                      date: str, industry: str) -> str:
        sub = panel[panel["l3_name"].astype(str).str.contains(industry, na=False)].copy()
        sub = sub.sort_values("sli", ascending=False).reset_index(drop=True)
        sub.insert(0, "rank", np.arange(1, len(sub) + 1))
        out = _pick(sub, LEADER_COLS)
        path = os.path.join(self.output_dir, f"sli_industry_{industry}_{date}.csv")
        out.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info("行业视图已写入 %s（%d 行）", path, len(out))
        # 同时输出该行业 LEADER_REASON
        sub_r = reasons[reasons["l3_name"].astype(str).str.contains(industry, na=False)]
        if len(sub_r):
            path_r = os.path.join(self.output_dir, f"sli_industry_{industry}_reasons_{date}.csv")
            sub_r.to_csv(path_r, index=False, encoding="utf-8-sig")
        return path

    # ── SQLite ─────────────────────────────────────────
    @staticmethod
    def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
        cur = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
        return cur.fetchone() is not None

    @staticmethod
    def _replace_date(conn: sqlite3.Connection, table: str, df: pd.DataFrame,
                      date: str) -> None:
        """按 trade_date 覆盖当日快照，保留其他日期的历史快照。"""
        if SliReport._table_exists(conn, table):
            conn.execute(f"DELETE FROM {table} WHERE trade_date=?", (date,))
        df.to_sql(table, conn, if_exists="append", index=False)

    def to_sqlite(self, panel: pd.DataFrame, quality: dict[str, Any], date: str) -> None:
        """写入 SQLite：V1/V2 全面板 + 数据质量，按日期保留多日快照。

        - leaderboard     ：V1 视角全面板（含全部列，供历史对比）
        - leaderboard_v2  ：V2 全面板（与 leaderboard 同结构，供 reader 接口读取）
        - data_quality    ：当日数据质量统计
        下游通过 sli.reader 读取，无需关心本库何时更新。
        """
        conn = sqlite3.connect(self.db_path)
        try:
            df = panel.sort_values("sli", ascending=False, na_position="last").copy()
            df.insert(0, "rank", np.arange(1, len(df) + 1))
            df["trade_date"] = date
            self._replace_date(conn, "leaderboard", df, date)
            if "sli_v2" in df.columns:
                self._replace_date(conn, "leaderboard_v2", df, date)
            qdf = pd.DataFrame([
                {"api": k, **{kk: vv for kk, vv in v.items()}}
                for k, v in quality.items()
            ])
            if len(qdf):
                qdf["trade_date"] = date
                self._replace_date(conn, "data_quality", qdf, date)
            logger.info("SQLite 已写入 %s（%s）", self.db_path, date)
        finally:
            conn.close()

    # ── 数据质量报告 ───────────────────────────────────
    def quality_report(self, quality: dict[str, Any], panel: pd.DataFrame,
                       date: str) -> str:
        lines: list[str] = [
            f"# SLI 数据质量报告  {date}",
            "",
            "## 接口调用统计",
            "",
            "| 接口 | 调用次数 | 失败次数 | 返回行数 | 备注 |",
            "|---|---|---|---|---|",
        ]
        for api, q in quality.items():
            lines.append(f"| {api} | {q.get('calls', 0)} | {q.get('fails', 0)} "
                         f"| {q.get('rows', 0)} | {str(q.get('note', ''))[:60]} |")
        lines.append("")
        lines.append("## 面板覆盖率")
        lines.append("")
        lines.append("| 字段 | 非空数 | 缺失率 |")
        lines.append("|---|---|---|")
        key_cols = ["sli", "scale_score", "profit_score", "growth_score",
                    "purity_score", "moat_score", "market_score", "trend_score",
                    "purity", "roe", "total_mv", "rs60"]
        n = len(panel)
        for c in key_cols:
            if c not in panel.columns:
                continue
            nonnull = int(panel[c].notna().sum())
            lines.append(f"| {c} | {nonnull} | {(1 - nonnull / n) * 100:.1f}% |")
        lines.append("")
        lines.append("## 行业样本量")
        lines.append("")
        low = int(panel["low_sample"].sum()) if "low_sample" in panel.columns else 0
        n_ind = int(panel["l3_code"].nunique()) if "l3_code" in panel.columns else 0
        lines.append(f"- 三级行业数：{n_ind}")
        lines.append(f"- LOW_SAMPLE（行业成分<5）：{low}")
        lines.append(f"- 股票总数：{n}")
        path = os.path.join(self.output_dir, f"sli_quality_report_{date}.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        logger.info("质量报告已写入 %s", path)
        return path

    # ══════════════════════════════════════════════════════
    # SLI V2 输出（细分赛道级）
    # ══════════════════════════════════════════════════════

    def _sorted_v2(self, panel: pd.DataFrame, top: Optional[int] = None) -> pd.DataFrame:
        df = panel.sort_values("sli_v2", ascending=False, na_position="last").reset_index(drop=True)
        df.insert(0, "rank", np.arange(1, len(df) + 1))
        if top:
            df = df.head(top)
        return df

    def leaderboard_v2(self, panel: pd.DataFrame, date: str,
                       top: Optional[int] = 100) -> str:
        """输出1：全市场 Top100（SLI_V2 视角）。"""
        df = self._sorted_v2(panel, top)
        for c in ("sub_rank", "ind_rank_v2"):
            if c in df.columns:
                df[c] = df[c].fillna(0).astype(int)
        out = _pick(df, LEADER_V2_COLS)
        path = os.path.join(self.output_dir, f"sli_v2_leaderboard_{date}.csv")
        out.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info("V2排行榜已写入 %s（%d 行）", path, len(out))
        return path

    def subsector_top5(self, panel: pd.DataFrame, date: str,
                       top: int = 5) -> str:
        """输出2：每个细分赛道 Top5（sub_rank 按 l3_code|subsector 组内 SLI_V2 排名）。"""
        p = panel[panel["sub_rank"] <= top].copy()
        p = p.sort_values(["l3_code", "subsector", "sub_rank"])
        p["sub_rank"] = p["sub_rank"].fillna(0).astype(int)
        p.insert(0, "rank", p["sub_rank"])
        out = _pick(p, SUBSECTOR_TOP_COLS)
        path = os.path.join(self.output_dir, f"sli_v2_subsector_top5_{date}.csv")
        out.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info("细分赛道Top5已写入 %s（%d 行 / %d 赛道）", path, len(out),
                    p["subsector"].nunique() if "subsector" in p.columns else 0)
        return path

    def next_leader_v2_report(self, panel: pd.DataFrame, date: str,
                              top: Optional[int] = 30) -> str:
        """输出3：下一代龙头 Top30（NEXT_LEADER=TRUE，按 ChallengerScore+Growth+SLI增速排序）。"""
        p = panel[panel["NEXT_LEADER"] == True].copy()  # noqa: E712
        if p.empty:
            p = panel.loc[[], [c for _, c in NEXT_V2_COLS if c in panel.columns]]
        else:
            p["sli_v2_delta"] = p["sli_v2_T"] - p["sli_v2_T60"]
            p["_key"] = (p["challenger_score"].fillna(0)
                         + p["growth_v2"].fillna(0)
                         + p["sli_v2_delta"].clip(0))
            p = p.sort_values("_key", ascending=False).reset_index(drop=True)
            p.insert(0, "rank", np.arange(1, len(p) + 1))
            if top:
                p = p.head(top)
        out = _pick(p, NEXT_V2_COLS)
        path = os.path.join(self.output_dir, f"sli_v2_next_leader_{date}.csv")
        out.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info("下一代龙头Top30已写入 %s（%d 行）", path, len(out))
        return path

    def earnings_turn_report(self, panel: pd.DataFrame, date: str,
                             top: Optional[int] = 30) -> str:
        """输出4：龙头+业绩拐点 Top30（SLI_V2≥80 且拐点）。"""
        p = panel[panel["LEADER_EARNINGS_TURN"] == True].copy()  # noqa: E712
        if p.empty:
            p = panel.loc[[], [c for _, c in TURN_V2_COLS if c in panel.columns]]
        else:
            p["turn_type"] = np.where(
                p.get("EARNINGS_TURN", False) == True, "负转正",  # noqa: E712
                np.where(p.get("EARNINGS_ACCELERATION", False) == True, "加速", ""))  # noqa: E712
            p = self._sorted_v2(p, top)
        out = _pick(p, TURN_V2_COLS)
        path = os.path.join(self.output_dir, f"sli_v2_earnings_turn_{date}.csv")
        out.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info("龙头+业绩拐点已写入 %s（%d 行）", path, len(out))
        return path

    def radar(self, panel: pd.DataFrame, date: str) -> str:
        """输出：产业龙头雷达 —— 每个细分赛道输出第1/2/3名 + 各类型龙头。"""
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
                m = g[g.get(col, False) == True]  # noqa: E712
                d[typ] = str(m.iloc[0].get("name", "")) if len(m) else ""
            rows.append(d)
        out = pd.DataFrame(rows)
        path = os.path.join(self.output_dir, f"sli_v2_radar_{date}.csv")
        out.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info("产业龙头雷达已写入 %s（%d 赛道）", path, len(out))
        return path

    # ── V2 LEADER_REASON ──────────────────────────────
    def reasons_v2(self, reasons: pd.DataFrame, date: str) -> str:
        path = os.path.join(self.output_dir, f"sli_v2_leader_reasons_{date}.csv")
        reasons.to_csv(path, index=False, encoding="utf-8-sig")
        logger.info("V2 LEADER_REASON 已写入 %s（%d 行）", path, len(reasons))
        return path
