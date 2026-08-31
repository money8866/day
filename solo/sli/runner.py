# -*- coding: utf-8 -*-
"""
SLI 运行编排层
行业宇宙 → 行情/财务/主营 → 四时点面板 → 分类/生命周期/加速器/特殊标签 → 报告
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import numpy as np
import pandas as pd

from .cache import SliCache
from .classify import (accelerator, classify_leader, industry_rank, leader_gap,
                       lifecycle, special_tags, trade_alpha, v2_pipeline)
from .config import (CACHE_DIR, DB_PATH, FINANCIAL_PERIODS, FILTER_ST,
                     LOOKBACK_TRADING_DAYS, LOG_DIR, MAINBZ_PERIODS,
                     OUTPUT_DIR)
from .datasource import DataSource
from .features import (PriceFeatures, annual_moat, build_universe, compute_purity,
                       financial_snapshot, growth_acceleration,
                       prev_period_snapshot)
from .reason import build_reasons, build_reasons_v2
from .report import SliReport
from .scoring import build_panel, build_panel_v2
from .subsector import load_subsector_map, product_rev_growth, subsector_match
from .utils import load_token, setup_logging, shift_trade_date, trade_dates_from_cal

logger = logging.getLogger("sli.runner")


def _normalize_date(date: str) -> str:
    return date.replace("-", "").replace("/", "")[:8]


class SliRunner:
    """SLI 引擎运行器。"""

    def __init__(self, date: Optional[str] = None, simple: bool = False) -> None:
        self.log = setup_logging(LOG_DIR)
        self.simple = simple
        self.token = load_token()
        if not self.token:
            self.log.warning("未找到 TUSHARE_TOKEN，将尝试匿名调用")
        self.cache = SliCache(CACHE_DIR)
        self.ds = DataSource(self.token, self.cache)
        self.report = SliReport(OUTPUT_DIR, DB_PATH)
        self.date = _normalize_date(date) if date else ""
        self.panels: dict[str, pd.DataFrame] = {}

    # ── 数据加载 ──────────────────────────────────────

    def _enrich_parent(self, classify_l3: pd.DataFrame) -> pd.DataFrame:
        """为 L3 级行业表关联 L2/L1 名称（一级/二级行业）。"""
        if classify_l3.empty or "parent_code" not in classify_l3.columns:
            return classify_l3
        l2 = self.ds.get_classify("L2")
        l1 = self.ds.get_classify("L1")
        if l2.empty or l1.empty:
            return classify_l3
        l2m = l2.rename(columns={"industry_code": "l2_code",
                                 "industry_name": "l2_name"})[["l2_code", "l2_name", "parent_code"]]
        l1m = l1.rename(columns={"industry_code": "l1_code",
                                 "industry_name": "l1_name"})[["l1_code", "l1_name"]]
        out = classify_l3.merge(l2m, left_on="parent_code", right_on="l2_code",
                                how="left").merge(l1m, left_on="parent_code_y",
                                                  right_on="l1_code", how="left")
        for c in ("l1_name", "l2_name"):
            if c not in out.columns:
                out[c] = ""
        return out

    def _resolve_date(self) -> str:
        if self.date:
            return self.date
        today = pd.Timestamp.now().strftime("%Y%m%d")
        cal = self.ds.get_trade_cal(f"{int(today[:4]) - 1}0101", today)
        dates = trade_dates_from_cal(cal)
        return dates[-1] if dates else today

    def _load_data(self):
        date = self.date
        log = self.log
        log.info("目标交易日: %s", date)

        log.info("[1/7] 行业分类与成分...")
        classify_l3 = self.ds.get_classify("L3")
        if "is_pub" in classify_l3.columns:
            classify_l3 = classify_l3[classify_l3["is_pub"].astype(int) == 1]
        # 关联 L2/L1 名称（一级/二级行业）
        classify_l3 = self._enrich_parent(classify_l3)
        index_codes = classify_l3["index_code"].tolist()
        members = self.ds.get_members(index_codes, date)
        basic = self.ds.get_stock_basic()
        uni = build_universe(classify_l3, members, basic, date)
        if uni.empty:
            raise RuntimeError("行业宇宙为空，请检查 index_classify / index_member / stock_basic 数据")
        if FILTER_ST:
            uni = uni[~uni["is_st"]].reset_index(drop=True)
        log.info("宇宙规模: %d 只股票 / %d 个三级行业",
                 uni["ts_code"].nunique(), uni["l3_code"].nunique())

        log.info("[2/7] 交易日历与行情窗口...")
        cal_start = f"{int(date[:4]) - 2}0101"
        cal = self.ds.get_trade_cal(cal_start, date)
        all_dates = trade_dates_from_cal(cal)
        if not all_dates:
            raise RuntimeError("交易日历为空")
        price_dates = all_dates[-LOOKBACK_TRADING_DAYS:]
        log.info("行情窗口: %s ~ %s（%d 个交易日）",
                 price_dates[0], price_dates[-1], len(price_dates))

        log.info("[3/7] 日行情...")
        daily = self.ds.get_daily_dates(price_dates)
        log.info("[4/7] daily_basic...")
        daily_basic = self.ds.get_daily_basic_dates(price_dates)

        log.info("[5/7] 财务指标（%d 期）...", len(FINANCIAL_PERIODS))
        fina = self.ds.get_fina_indicator(FINANCIAL_PERIODS)
        income = self.ds.get_income(FINANCIAL_PERIODS)
        balance = self.ds.get_balance(FINANCIAL_PERIODS)

        log.info("[6/7] 主营构成（%d 期）...", len(MAINBZ_PERIODS))
        mainbz = self.ds.get_mainbz(MAINBZ_PERIODS)

        log.info("[7/7] 数据加载完成")
        return uni, daily, daily_basic, fina, income, balance, mainbz

    # ── 面板构建（多时点） ──────────────────────────────

    def _build_panels(self, uni, daily, daily_basic, fina, income, balance, mainbz):
        log = self.log
        pf = PriceFeatures(daily)
        pf.prepare()
        self.price_features = pf

        # daily_basic 透视（ffill：截至各时点的最近值）
        dbpiv: dict[str, pd.DataFrame] = {}
        if daily_basic is not None and len(daily_basic):
            for col in ("total_mv", "circ_mv", "pe_ttm", "pb", "turnover_rate", "volume_ratio"):
                if col not in daily_basic.columns:
                    continue
                piv = (daily_basic[["trade_date", "ts_code", col]]
                       .drop_duplicates(["trade_date", "ts_code"])
                       .pivot(index="trade_date", columns="ts_code", values=col))
                piv.index = piv.index.astype(str)
                dbpiv[col] = piv.sort_index().ffill()

        def dbasic_at(d: str) -> pd.DataFrame:
            out: dict[str, pd.Series] = {}
            idx = None
            for col, piv in dbpiv.items():
                if d not in piv.index:
                    continue
                s = piv.loc[d]
                out[col] = s
                idx = s.index
            if not out:
                return pd.DataFrame(columns=["ts_code"])
            df = pd.DataFrame(out)
            df = df.reset_index()
            df = df.rename(columns={df.columns[0]: "ts_code"})
            return df

        date_T = pf.nearest_date(self.date)
        if date_T is None:
            raise RuntimeError(f"交易日历中没有 <= {self.date} 的交易日")
        log.info("T 时点: %s", date_T)

        # 纯度/护城河：慢变量，取 T 时点计算后全时点复用（主营收构成仅覆盖最近2期）
        snap_T = financial_snapshot(fina, income, balance, date_T)
        purity = compute_purity(mainbz, uni, snap_T)
        moat = annual_moat(uni, fina, date_T)
        log.info("纯度覆盖: %s", purity["purity_confidence"].value_counts().to_dict())

        # V2 产品层：细分赛道归属 + 核心产品收入增速代理（一次计算，全时点复用）
        subsector_map = load_subsector_map()
        self.subsector_df = subsector_match(uni, mainbz, subsector_map)
        self.prod_growth = product_rev_growth(mainbz, uni, subsector_map)
        n_sub = (self.subsector_df["subsector"].nunique()
                 if not self.subsector_df.empty else 0)
        n_cov = (self.subsector_df["ts_code"].nunique()
                 if not self.subsector_df.empty else 0)
        log.info("V2细分赛道归属: %d 股票 / %d 赛道, 产品增速代理 %d 只",
                 n_cov, n_sub, len(self.prod_growth))

        offsets = {"T": 0} if self.simple else {"T": 0, "T20": 20, "T60": 60, "T120": 120}
        for label, back in offsets.items():
            d = shift_trade_date(pf.dates, date_T, back) or date_T
            log.info("构建面板 %-5s @ %s", label, d)
            price_at = pf.eval_at(d)
            if price_at.empty:
                log.warning("面板 %s 行情为空，跳过", label)
                continue
            snap = snap_T if label == "T" else financial_snapshot(fina, income, balance, d)
            prev = prev_period_snapshot(fina, d)
            accel = growth_acceleration(fina, d)
            snap = snap.merge(prev, on="ts_code", how="left")
            panel = build_panel(uni, price_at, dbasic_at(d), snap, accel, purity, moat)
            panel = build_panel_v2(panel, self.subsector_df, self.prod_growth)
            self.panels[label] = panel
        if "T" not in self.panels:
            raise RuntimeError("T 时点面板构建失败")
        return self.panels

    # ── 分类流水线 ─────────────────────────────────────

    def _classify(self):
        log = self.log
        panel = self.panels["T"].copy()
        log.info("计算生命周期（T/T-20/T-60/T-120 对比）...")
        lc = lifecycle(self.panels)
        panel = panel.merge(lc, on="ts_code", how="left")
        panel = industry_rank(panel)
        panel, gap_summary = leader_gap(panel)
        panel = classify_leader(panel)
        panel = accelerator(panel)
        panel = special_tags(panel)
        log.info("龙头类型分布: %s", panel["leader_type"].value_counts().to_dict())
        log.info("生命周期分布: %s",
                 panel["lifecycle"].value_counts().to_dict())
        return panel, gap_summary

    def _classify_v2(self, panel: pd.DataFrame) -> pd.DataFrame:
        """V2 分类流水线：基于 sli_v2 的生命周期 → 六类龙头/Dominance/
        Challenger/NEXT_LEADER/SUPER_LEADER/拐点 → 人工知识冲突检测。"""
        log = self.log
        log.info("计算 V2 生命周期（sli_v2 T/T-20/T-60/T-120）...")
        lc = lifecycle(self.panels, col="sli_v2")
        # V1 生命周期已在 _classify 中合并；V2 以 sli_v2 生命周期为准覆盖，
        # 并去掉 rank_* 重复列避免 merge 后缀污染（排名由 classify_v2 重算）
        lc = lc.drop(columns=[c for c in lc.columns if c.startswith("rank_")],
                     errors="ignore")
        panel = panel.drop(columns=["lifecycle"], errors="ignore")
        panel = panel.merge(lc, on="ts_code", how="left")
        panel = v2_pipeline(panel, self.subsector_df, self.prod_growth)
        log.info("V2龙头类型分布: %s",
                 panel["leader_type_v2"].value_counts().to_dict())
        log.info("V2生命周期分布: %s",
                 panel["lifecycle"].value_counts().to_dict())
        n_conf = int(panel.get("knowledge_conflict", pd.Series(False)).sum())
        if n_conf:
            log.warning("人工知识与量化结果冲突 %d 家（KNOWLEDGE_CONFLICT=TRUE）", n_conf)
        return panel

    # ── 主流程 ─────────────────────────────────────────

    def run(self, top: Optional[int] = None, industry: Optional[str] = None,
            er20_csv: Optional[str] = None, v1: bool = False) -> dict[str, Any]:
        date = self.date = self._resolve_date()
        version = "1.0" if v1 else "2.0"
        self.log.info("═══ SLI %s 细分产业龙头识别引擎  %s ═══", version, date)

        uni, daily, daily_basic, fina, income, balance, mainbz = self._load_data()
        self._build_panels(uni, daily, daily_basic, fina, income, balance, mainbz)
        panel, gap_summary = self._classify()
        if not v1:
            panel = self._classify_v2(panel)

        # 与交易系统对接（ER20 可选）
        er20_map = None
        if er20_csv and os.path.exists(er20_csv):
            try:
                df = pd.read_csv(er20_csv, dtype={"ts_code": str})
                sc = next((c for c in df.columns if "ts_code" in c.lower()), None)
                sc20 = next((c for c in df.columns
                             if c.lower().replace("_", "") in ("er20", "er20score", "score")), None)
                if sc and sc20:
                    er20_map = dict(zip(df[sc].astype(str), df[sc20]))
                    self.log.info("已加载 ER20 评分: %d 只", len(er20_map))
            except Exception as exc:
                self.log.warning("ER20 文件解析失败: %s", exc)
        panel = trade_alpha(panel, er20_map)

        self.panel = panel
        self.gap_summary = gap_summary
        return self.emit(top, industry, uni, v2=not v1)

    # ── 输出 ──────────────────────────────────────────

    def emit(self, top: Optional[int], industry: Optional[str],
             uni: pd.DataFrame, v2: bool = True) -> dict[str, Any]:
        date = self.date
        panel = self.panel
        log = self.log

        paths = {
            "leaderboard": self.report.leaderboard(panel, date, top),
            "industry_top3": self.report.industry_top3(panel, date),
            "acceleration": self.report.acceleration(panel, date),
            "next_leader": self.report.next_leader(panel, date),
        }
        reasons = build_reasons(panel)
        paths["reasons"] = self.report.reasons(reasons, date)

        # ── V2 细分赛道级输出 ──
        if v2 and "sli_v2" in panel.columns:
            paths["leaderboard_v2"] = self.report.leaderboard_v2(panel, date, top or 100)
            paths["subsector_top5"] = self.report.subsector_top5(panel, date)
            paths["next_leader_v2"] = self.report.next_leader_v2_report(panel, date)
            paths["earnings_turn_v2"] = self.report.earnings_turn_report(panel, date)
            paths["radar_v2"] = self.report.radar(panel, date)
            reasons_v2 = build_reasons_v2(panel)
            paths["reasons_v2"] = self.report.reasons_v2(reasons_v2, date)

        # 全量面板 CSV（供 ER20/HVT 等下游复用）
        full = panel.sort_values("sli", ascending=False).reset_index(drop=True)
        full.insert(0, "rank", np.arange(1, len(full) + 1))
        full_path = os.path.join(OUTPUT_DIR, f"sli_full_{date}.csv")
        full.to_csv(full_path, index=False, encoding="utf-8-sig")
        paths["full"] = full_path

        if not self.gap_summary.empty:
            gp_path = os.path.join(OUTPUT_DIR, f"sli_gap_summary_{date}.csv")
            self.gap_summary.to_csv(gp_path, index=False, encoding="utf-8-sig")
            paths["gap_summary"] = gp_path

        if industry:
            paths["industry_view"] = self.report.industry_view(panel, reasons, date, industry)

        # 质量报告 + SQLite
        paths["quality"] = self.report.quality_report(self.ds.quality, panel, date)
        self.report.to_sqlite(panel, self.ds.quality, date)

        log.info("════ 输出完成 ════")
        for k, v in paths.items():
            log.info("  %-16s %s", k, v)
        return {"date": date, "paths": paths,
                "n_industries": int(panel["l3_code"].nunique()),
                "n_stocks": int(len(panel)),
                "n_leaders": int((panel["leader_type"] != "NONE").sum())}
