# -*- coding: utf-8 -*-
"""
SLI 回测引擎 backtest.py
2023-2026 历史滚动验证（月频期点）：
1. 龙头稳定性：Top1 持续期 / Top3 重合率 / 龙头替换速度
2. 龙头超额收益：细分赛道龙头组合 vs 赛道成分等权 vs 沪深300 vs 中证1000
   持有期 20 / 60 / 120 / 250 交易日（后复权收益）

用法：
  python -m sli.backtest --prepare               # 拉取/补齐 2022-01 ~ 2026-06 历史数据
  python -m sli.backtest --prepare --until 20250601   # 分批拉取（断点续跑）
  python -m sli.backtest --run --start 20230630 --end 20260531
  python -m sli.backtest --run --start 20230630 --end 20260531 --simple

设计约束：
- 防未来函数：财务快照按 ann_date <= 期点过滤；主营构成（仅最近2期）作为
  产品结构慢变量静态复用，与实盘口径一致。
- 收益计算用 adj_factor 后复权价，避免除权除息失真。
- 龙头身份仅由当期 SLI_V2 决定，不采用未来信息。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any, Optional

import numpy as np
import pandas as pd

from .cache import SliCache
from .config import (BACKTEST_BENCH, BACKTEST_HORIZONS, BACKTEST_PERIODS,
                     CACHE_DIR, FILTER_ST, INCLUDE_BJ, LOOKBACK_TRADING_DAYS,
                     LOG_DIR, MAINBZ_PERIODS, OUTPUT_DIR)
from .datasource import DataSource
from .features import (PriceFeatures, annual_moat, build_universe,
                       compute_purity, financial_snapshot,
                       growth_acceleration, prev_period_snapshot)
from .scoring import build_panel, build_panel_v2
from .subsector import load_subsector_map, product_rev_growth, subsector_match
from .utils import load_token, setup_logging, trade_dates_from_cal

logger = logging.getLogger("sli.backtest")

PRICE_START = "20220101"    # 行情起点（为早期期点留足 MA120 窗口）
FIN_START = "20220101"      # 财务最早报告期（BACKTEST_PERIODS 已含）


# ── 小工具 ────────────────────────────────────────────

def _dstr(x) -> str:
    return str(x).replace("-", "").replace("/", "")[:8]


def _month_end_dates(trade_dates: list[str], start: str, end: str) -> list[str]:
    """返回 [start, end] 区间内每月最后一个交易日（升序）。"""
    out: list[str] = []
    seen: dict[str, str] = {}
    for d in trade_dates:
        if d < start or d > end:
            continue
        ym = d[:6]
        seen[ym] = d  # 每月最后出现的交易日覆盖
    # 按年月排序输出
    for ym in sorted(seen):
        out.append(seen[ym])
    return out


def _enrich_l3(classify_l3: pd.DataFrame, ds: DataSource) -> pd.DataFrame:
    """为 L3 级行业表关联 L2/L1 名称。"""
    if classify_l3.empty or "parent_code" not in classify_l3.columns:
        return classify_l3
    l2 = ds.get_classify("L2")
    l1 = ds.get_classify("L1")
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


# ── 回测器 ────────────────────────────────────────────

class SliBacktest:
    def __init__(self, simple: bool = False) -> None:
        self.log = setup_logging(LOG_DIR)
        self.simple = simple
        self.token = load_token()
        self.cache = SliCache(CACHE_DIR)
        self.ds = DataSource(self.token, self.cache)

    # ── 数据准备（断点续跑） ──────────────────────────

    def prepare(self, until: str = "20260630") -> None:
        log = self.log
        log.info("═══ 回测数据准备  %s ~ %s ═══", PRICE_START, until)

        log.info("[1/4] 财务指标（%d 报告期）...", len(BACKTEST_PERIODS))
        self.ds.get_fina_indicator(BACKTEST_PERIODS)
        self.ds.get_income(BACKTEST_PERIODS)
        self.ds.get_balance(BACKTEST_PERIODS)
        self.ds.get_mainbz(MAINBZ_PERIODS)

        log.info("[2/4] 交易日历...")
        cal = self.ds.get_trade_cal(PRICE_START, until)
        dates = trade_dates_from_cal(cal)
        log.info("     %d 个交易日（%s ~ %s）", len(dates), dates[0], dates[-1])

        log.info("[3/4] 日行情 + daily_basic + 复权因子（逐日，缓存优先）...")
        self.ds.get_daily_dates(dates)
        self.ds.get_daily_basic_dates(dates)
        self.ds.get_adj_factor_dates(dates)

        log.info("[4/4] 指数基准（沪深300 / 中证1000）...")
        for name, code in BACKTEST_BENCH.items():
            self.ds.get_index_daily(code, PRICE_START, until)
            log.info("     %s(%s) 就绪", name, code)
        log.info("═══ 数据准备完成 ═══")

    # ── 数据加载 ──────────────────────────────────────

    def _load_all(self, price_start: str, price_end: str):
        ds = self.ds
        log = self.log
        log.info("[加载] 行业分类与全历史成分...")
        classify_l3 = ds.get_classify("L3")
        if "is_pub" in classify_l3.columns:
            classify_l3 = classify_l3[classify_l3["is_pub"].astype(int) == 1]
        classify_l3 = _enrich_l3(classify_l3, ds)
        index_codes = classify_l3["index_code"].tolist()
        # 成分缓存含全历史 in_date/out_date：优先复用已有 members_*（避免重复拉取）
        import glob
        mfiles = glob.glob(os.path.join(CACHE_DIR, "members_*.parquet"))
        members = pd.DataFrame()
        if mfiles:
            latest = max(mfiles, key=os.path.getmtime)
            if os.path.basename(latest) != f"members_{price_end}.parquet":
                members = pd.read_parquet(latest)
                log.info("     复用成分缓存 %s", os.path.basename(latest))
        if members.empty:
            members = ds.get_members(index_codes, price_end)
        basic = ds.get_stock_basic()

        log.info("[加载] 交易日历与行情窗口...")
        cal = ds.get_trade_cal(price_start, price_end)
        all_dates = trade_dates_from_cal(cal)

        log.info("[加载] 日行情 / daily_basic / 复权因子...")
        daily = ds.get_daily_dates(all_dates)
        daily_basic = ds.get_daily_basic_dates(all_dates)
        adj_factor = ds.get_adj_factor_dates(all_dates)

        log.info("[加载] 财务指标（%d 期）...", len(BACKTEST_PERIODS))
        fina = ds.get_fina_indicator(BACKTEST_PERIODS)
        income = ds.get_income(BACKTEST_PERIODS)
        balance = ds.get_balance(BACKTEST_PERIODS)
        mainbz = ds.get_mainbz(MAINBZ_PERIODS)

        log.info("[加载] 指数基准...")
        index = {}
        for name, code in BACKTEST_BENCH.items():
            index[name] = ds.get_index_daily(code, price_start, price_end)

        return (classify_l3, members, basic, all_dates, daily, daily_basic,
                adj_factor, fina, income, balance, mainbz, index)

    # ── 回测主流程 ────────────────────────────────────

    def run(self, start: str, end: str) -> dict[str, Any]:
        log = self.log
        log.info("═══ SLI V2 回测  %s ~ %s ═══", start, end)

        # 先定期点，再由期点推导价格窗口（避免全量拉取）
        cal_all = self.ds.get_trade_cal(PRICE_START, end)
        all_dates_full = trade_dates_from_cal(cal_all)
        period_dates = _month_end_dates(all_dates_full, start, end)
        if not period_dates:
            raise RuntimeError("区间内无月末期点")
        i0 = max(0, all_dates_full.index(period_dates[0]) - LOOKBACK_TRADING_DAYS)
        i1 = min(len(all_dates_full) - 1,
                 all_dates_full.index(period_dates[-1]) + max(BACKTEST_HORIZONS))
        price_start_d = all_dates_full[i0]
        price_end_d = all_dates_full[i1]
        log.info("[窗口] %d 个期点，价格窗口 %s ~ %s",
                 len(period_dates), price_start_d, price_end_d)

        (classify_l3, members, basic, all_dates, daily, daily_basic,
         adj_factor, fina, income, balance, mainbz, index) = \
            self._load_all(price_start_d, price_end_d)

        # ── 后复权收盘价（防止除权失真） ──
        log.info("[回测] 构建后复权收盘价...")
        close_raw = (daily[["trade_date", "ts_code", "close"]]
                     .pivot(index="trade_date", columns="ts_code", values="close")
                     .sort_index().astype(float))
        adj = (adj_factor[["trade_date", "ts_code", "adj_factor"]]
               .pivot(index="trade_date", columns="ts_code", values="adj_factor")
               .sort_index().ffill())
        base = adj.iloc[-1]                      # 最新复权因子为基准
        close_adj = close_raw.multiply(adj.div(base), axis=1)
        dates = close_adj.index.tolist()

        # ── 静态慢变量（主营仅2期 → 产品结构/纯度/护城河全期复用） ──
        log.info("[回测] 计算静态慢变量（纯度/护城河/细分赛道）...")
        date_T = dates[-1]
        uni_T = build_universe(classify_l3, members, basic, date_T)
        if FILTER_ST:
            uni_T = uni_T[~uni_T["is_st"]].reset_index(drop=True)
        snap_T = financial_snapshot(fina, income, balance, date_T)
        purity = compute_purity(mainbz, uni_T, snap_T)
        moat = annual_moat(uni_T, fina, date_T)
        subsector_map = load_subsector_map()
        subsector_df = subsector_match(uni_T, mainbz, subsector_map)
        prod_growth = product_rev_growth(mainbz, uni_T, subsector_map)
        log.info("     细分赛道 %d 个，产品增速代理 %d 只",
                 subsector_df["subsector"].nunique() if not subsector_df.empty else 0,
                 len(prod_growth))

        # ── 行情特征（全量一次） ──
        pf = PriceFeatures(daily)
        pf.prepare()

        # daily_basic 透视（ffill）
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
            df = pd.DataFrame(out).reset_index()
            df = df.rename(columns={df.columns[0]: "ts_code"})
            return df

        # ── 期点面板循环 ──
        period_dates = _month_end_dates(dates, start, end)
        log.info("[回测] %d 个月末期点：%s ... %s",
                 len(period_dates), period_dates[0], period_dates[-1])

        snapshots: list[pd.DataFrame] = []
        for d in period_dates:
            uni = build_universe(classify_l3, members, basic, d)
            if FILTER_ST:
                uni = uni[~uni["is_st"]].reset_index(drop=True)
            price_at = pf.eval_at(d)
            if price_at.empty:
                log.warning("期点 %s 行情为空，跳过", d)
                continue
            snap = financial_snapshot(fina, income, balance, d)
            prev = prev_period_snapshot(fina, d)
            accel = growth_acceleration(fina, d)
            snap = snap.merge(prev, on="ts_code", how="left")
            panel = build_panel(uni, price_at, dbasic_at(d), snap, accel, purity, moat)
            panel = build_panel_v2(panel, subsector_df, prod_growth)
            panel["period"] = d
            snapshots.append(panel)
            log.info("  期点 %s：面板 %d 只 / %d 赛道，龙头 Top1 %d 个",
                     d, len(panel),
                     panel["subsector"].nunique() if "subsector" in panel else 0,
                     int((panel["product_rank"] == 1).sum()))
        if not snapshots:
            raise RuntimeError("无有效期点面板")

        # ── 组装各期龙头池 / Top3 ──
        leader_rows: list[dict[str, Any]] = []
        top3_rows: list[dict[str, Any]] = []
        for pan in snapshots:
            d = pan["period"].iloc[0]
            g = pan[pan["product_rank"] == 1].copy()
            for _, r in g.iterrows():
                leader_rows.append({
                    "period": d, "l3_code": r["l3_code"], "l3_name": r["l3_name"],
                    "subsector": r["subsector"], "ts_code": r["ts_code"],
                    "name": r["name"], "sli_v2": r.get("sli_v2", np.nan),
                })
            top3 = (pan[pan["product_rank"] <= 3]
                    .sort_values(["l3_code", "subsector", "product_rank"]))
            for _, r in top3.iterrows():
                top3_rows.append({
                    "period": d, "l3_code": r["l3_code"], "subsector": r["subsector"],
                    "rank": int(r["product_rank"]), "ts_code": r["ts_code"],
                    "name": r["name"],
                })
        leaders = pd.DataFrame(leader_rows)
        top3 = pd.DataFrame(top3_rows)

        # ── 未来收益（后复权） ──
        log.info("[回测] 计算未来收益与超额...")
        close_idx = {d: i for i, d in enumerate(dates)}

        def _fut_ret(d: str, codes: list[str], h: int) -> float:
            i = close_idx.get(d)
            if i is None or i + h >= len(dates):
                return float("nan")
            d_f = dates[i + h]
            c0 = close_adj.loc[d, codes].astype(float)
            c1 = close_adj.loc[d_f, codes].astype(float)
            valid = c0.notna() & c1.notna() & (c0 > 0)
            if not valid.any():
                return float("nan")
            return float((c1[valid] / c0[valid] - 1.0).mean() * 100.0)

        # 赛道成分等权基准（防未来：只用当期成分）
        unis = {d: build_universe(classify_l3, members, basic, d) for d in period_dates}
        if FILTER_ST:
            unis = {d: u[~u["is_st"]].reset_index(drop=True) for d, u in unis.items()}

        excess_rows: list[dict[str, Any]] = []
        for d in period_dates:
            uni_d = unis[d]
            lb = leaders[leaders["period"] == d]
            for h in BACKTEST_HORIZONS:
                ldr_codes = lb["ts_code"].dropna().unique().tolist()
                ind_codes = uni_d["ts_code"].dropna().unique().tolist()
                r_ldr = _fut_ret(d, ldr_codes, h)
                r_ind = _fut_ret(d, ind_codes, h)
                row: dict[str, Any] = {
                    "period": d, "horizon": h,
                    "n_leaders": len(ldr_codes),
                    "leader_ret": r_ldr, "industry_ret": r_ind,
                    "excess_vs_industry": (r_ldr - r_ind) if pd.notna(r_ldr) and pd.notna(r_ind) else np.nan,
                }
                for name, idxdf in index.items():
                    if idxdf is None or idxdf.empty:
                        row[f"{name}_ret"] = np.nan
                        row[f"excess_vs_{name}"] = np.nan
                        continue
                    idf = idxdf.copy()
                    idf["trade_date"] = idf["trade_date"].astype(str)
                    idf = idf.sort_values("trade_date").set_index("trade_date")["close"]
                    if d in idf.index and d in close_idx and close_idx[d] + h < len(dates):
                        d_f = dates[close_idx[d] + h]
                        if d_f in idf.index:
                            b0, b1 = float(idf.loc[d]), float(idf.loc[d_f])
                            if b0 > 0:
                                row[f"{name}_ret"] = (b1 / b0 - 1.0) * 100.0
                                row[f"excess_vs_{name}"] = (r_ldr - row[f"{name}_ret"]) if pd.notna(r_ldr) else np.nan
                excess_rows.append(row)
        excess = pd.DataFrame(excess_rows)

        # ── 稳定性指标 ──
        log.info("[回测] 计算龙头稳定性...")
        stability = self._stability(leaders, top3)

        # ── 输出 ──
        paths = self._emit(leaders, top3, excess, stability, start, end, unis)
        log.info("═══ 回测完成 ═══")
        return {"paths": paths, "n_periods": len(period_dates),
                "n_leaders": len(leaders), "excess_rows": len(excess)}

    # ── 稳定性分析 ────────────────────────────────────

    def _stability(self, leaders: pd.DataFrame, top3: pd.DataFrame) -> dict[str, Any]:
        """Top1 持续期 / Top3 重合率 / Top1 替换速度。"""
        if leaders.empty:
            return {}
        periods = sorted(leaders["period"].unique())
        # 每赛道 Top1 序列
        ldr = leaders.sort_values("period").copy()
        ldr["_grp"] = ldr["l3_code"].astype(str) + "|" + ldr["subsector"]
        # 相邻期同赛道 Top1 是否保持不变
        ldr["_prev_code"] = ldr.groupby("_grp")["ts_code"].shift(1)
        ldr["_keep"] = ldr["ts_code"] == ldr["_prev_code"]
        keep_cnt = int(ldr["_keep"].sum())
        n_cmp = int(ldr["_keep"].notna().sum())
        keep_rate = keep_cnt / n_cmp if n_cmp else np.nan

        # Top1 平均连续持续期（游程）
        runs: list[int] = []
        for _, g in ldr.groupby("_grp"):
            code = None
            run = 0
            for c in g["ts_code"].tolist():
                if c == code:
                    run += 1
                else:
                    if run:
                        runs.append(run)
                    code, run = c, 1
            if run:
                runs.append(run)
        avg_run = float(np.mean(runs)) if runs else np.nan

        # Top3 相邻期 Jaccard 重合率
        t3 = top3.sort_values("period").copy()
        t3["_grp"] = t3["l3_code"].astype(str) + "|" + t3["subsector"]
        jac_scores: list[float] = []
        for (g, grp) in t3.groupby("_grp"):
            prev_set: set[str] | None = None
            for _, r in grp.iterrows():
                cur = {r["ts_code"]}
                for _, r2 in grp[grp["period"] == r["period"]].iterrows():
                    cur.add(r2["ts_code"])
                if prev_set is not None:
                    inter = len(prev_set & cur)
                    union = len(prev_set | cur)
                    if union:
                        jac_scores.append(inter / union)
                prev_set = cur
        top3_jaccard = float(np.mean(jac_scores)) if jac_scores else np.nan

        # 替换速度：Top1 更替次数 / 可比较期对
        replace_rate = 1.0 - keep_rate if pd.notna(keep_rate) else np.nan
        return {
            "n_periods": len(periods),
            "n_subsector_runs": len(runs),
            "top1_keep_rate": round(keep_rate, 4) if pd.notna(keep_rate) else None,
            "top1_avg_duration": round(avg_run, 2) if pd.notna(avg_run) else None,
            "top1_replace_rate": round(replace_rate, 4) if pd.notna(replace_rate) else None,
            "top3_jaccard": round(top3_jaccard, 4) if pd.notna(top3_jaccard) else None,
        }

    # ── 输出 ──────────────────────────────────────────

    def _emit(self, leaders, top3, excess, stability, start, end, unis) -> dict[str, str]:
        tag = f"{_dstr(start)}_{_dstr(end)}"
        paths: dict[str, str] = {}

        lp = os.path.join(OUTPUT_DIR, f"sli_backtest_leaders_{tag}.csv")
        leaders.to_csv(lp, index=False, encoding="utf-8-sig")
        paths["leaders"] = lp

        ep = os.path.join(OUTPUT_DIR, f"sli_backtest_excess_{tag}.csv")
        excess.to_csv(ep, index=False, encoding="utf-8-sig")
        paths["excess"] = ep

        # 稳定性 CSV
        sp = os.path.join(OUTPUT_DIR, f"sli_backtest_stability_{tag}.csv")
        if stability:
            pd.DataFrame([stability]).to_csv(sp, index=False, encoding="utf-8-sig")
        else:
            pd.DataFrame().to_csv(sp, index=False, encoding="utf-8-sig")
        paths["stability"] = sp

        # 汇总报告（超额收益 × 年份 × 持有期）
        rep = os.path.join(OUTPUT_DIR, f"sli_backtest_report_{tag}.md")
        self._write_report(rep, excess, stability, start, end)
        paths["report"] = rep
        return paths

    def _write_report(self, path: str, excess: pd.DataFrame,
                      stability: dict[str, Any], start: str, end: str) -> None:
        lines: list[str] = []
        lines.append("# SLI V2 回测报告（滚动验证）\n")
        lines.append(f"- 回测区间：{start} ~ {end}（月末期点）")
        lines.append(f"- 期点数：{stability.get('n_periods', len(excess['period'].unique()) if len(excess) else 0)}")
        if excess.empty:
            lines.append("\n无有效超额收益样本。")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            return

        lines.append(f"- 龙头样本期点数：{len(excess)}")
        lines.append("")

        # 稳定性
        lines.append("## 一、龙头稳定性")
        if stability:
            st_map = {
                "top1_keep_rate": "Top1 相邻期保持率",
                "top1_avg_duration": "Top1 平均持续期（月）",
                "top1_replace_rate": "Top1 替换率",
                "top3_jaccard": "Top3 相邻期 Jaccard 重合率",
            }
            for k, label in st_map.items():
                v = stability.get(k)
                lines.append(f"- {label}：{v if v is not None else '—'}")
        lines.append("")

        # 超额收益总表（horizon × 基准）
        lines.append("## 二、龙头组合超额收益（总体）")
        head = ["持有期"] + [f"vs {b}" for b in
                ["行业成分等权"] + list(BACKTEST_BENCH.keys())]
        lines.append("| " + " | ".join(head) + " |")
        lines.append("|" + "---|" * len(head))
        for h in BACKTEST_HORIZONS:
            sub = excess[excess["horizon"] == h]
            if sub.empty:
                continue
            cells = [f"{h}日"]
            for col in ["excess_vs_industry"] + [f"excess_vs_{b}" for b in BACKTEST_BENCH]:
                avg = sub[col].mean()
                win = (sub[col] > 0).mean() * 100.0 if sub[col].notna().any() else np.nan
                cells.append(f"{avg:+.2f}% / 胜率{win:.0f}%"
                             if pd.notna(avg) else "—")
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

        # 逐年超额
        lines.append("## 三、逐年超额收益（vs 行业成分等权，60日）")
        ex = excess[excess["horizon"] == 60].copy()
        ex["year"] = ex["period"].str[:4]
        lines.append("| 年份 | 期点数 | 龙头60日 | 行业60日 | 超额 | 胜率 |")
        lines.append("|---|:--:|:--:|:--:|:--:|:--:|")
        for yr, g in ex.groupby("year"):
            rl, ri = g["leader_ret"].mean(), g["industry_ret"].mean()
            exx = g["excess_vs_industry"].mean()
            win = (g["excess_vs_industry"] > 0).mean() * 100.0
            lines.append(f"| {yr} | {len(g)} | {rl:+.2f}% | {ri:+.2f}% | "
                         f"{exx:+.2f}% | {win:.0f}% |")
        lines.append("")

        # 结论
        lines.append("## 四、结论")
        h60 = excess[excess["horizon"] == 60]
        if not h60.empty and h60["excess_vs_industry"].notna().any():
            avg = h60["excess_vs_industry"].mean()
            win = (h60["excess_vs_industry"] > 0).mean() * 100.0
            lines.append(f"- 60日口径：龙头组合相对赛道成分等权平均超额 "
                         f"{avg:+.2f}%，胜率 {win:.0f}%。")
        lines.append("- 注：超额收益基于月末期点调仓的等权组合，未计交易成本；")
        lines.append("  产品层（细分赛道归属/纯度）按最近期主营静态复用。")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))


# ── CLI ───────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="sli.backtest",
        description="SLI V2 历史滚动回测（龙头稳定性 + 超额收益）",
    )
    ap.add_argument("--prepare", action="store_true", help="拉取/补齐历史数据")
    ap.add_argument("--run", action="store_true", help="执行回测")
    ap.add_argument("--until", default="20260630", help="prepare 数据截止日（默认 20260630）")
    ap.add_argument("--start", default="20230630", help="回测起始月（默认 20230630）")
    ap.add_argument("--end", default="20260531", help="回测截止月（默认 20260531）")
    ap.add_argument("--simple", action="store_true", help="简化模式（跳过生命周期面板）")
    args = ap.parse_args(argv)

    bt = SliBacktest(simple=args.simple)
    try:
        if args.prepare:
            bt.prepare(until=args.until)
        if args.run:
            result = bt.run(start=args.start, end=args.end)
            print(f"\n回测完成：{result['n_periods']} 期点 / {result['n_leaders']} 龙头样本")
            for k, v in result["paths"].items():
                print(f"  {k:<12} {v}")
        if not args.prepare and not args.run:
            ap.print_help()
            return 0
        return 0
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"\n回测失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
