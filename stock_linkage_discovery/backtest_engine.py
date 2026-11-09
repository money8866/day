"""事件回测与防止伪联动（第十七层，Phase 4 基础版）。

以群体首次形成日 T0 为事件，统计 T+1/3/5/10/20 的等权/市值加权收益、
相对全A与相对行业超额、正收益比例、最大回撤、盈亏比及成本后收益（30bp）。

纪律：
  - 严格区分 EMERGING_CLUSTER / LEADER_EXPANSION / CLUSTER_STRENGTHENING 三类样本；
  - 建立随机 / 同行业 / 同规模流动性三个对照组；
  - 分市场环境（牛/震荡/熊）与 30bp 成本检验；
  - 结论允许 PASS / CONDITIONAL / UNPROVEN / FAIL，样本不足绝不硬说有效。
  - 回测是事后评估，允许读取 T0 之后的数据；实时信号链路仍严格 as-of。
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .common import get_logger, write_json
from .data_layer import _ro, trade_dates

log = get_logger("backtest")

HORIZONS = (1, 3, 5, 10, 20)
STATES = ("EMERGING_CLUSTER", "LEADER_EXPANSION", "CLUSTER_STRENGTHENING")
COST = 0.0030  # 30bp 单边交易成本


def _linkage(cfg) -> sqlite3.Connection:
    return sqlite3.connect(cfg["paths"]["linkage_db"], timeout=60)


def _load_market(cfg: dict[str, Any], start: str, end: str) -> tuple[pd.DataFrame, list[str], pd.DataFrame]:
    with _ro(cfg["paths"]["market_db"]) as con:
        df = pd.read_sql_query(
            "SELECT ts_code,trade_date,pct_chg FROM daily_cache WHERE trade_date BETWEEN ? AND ?",
            con, params=(start, end))
        mv = pd.read_sql_query(
            "SELECT ts_code,trade_date,circ_mv FROM daily_basic_cache WHERE trade_date BETWEEN ? AND ?",
            con, params=(start, end))
    R = df.pivot(index="trade_date", columns="ts_code", values="pct_chg").sort_index() / 100.0
    dates = list(R.index)
    MV = mv.pivot(index="trade_date", columns="ts_code", values="circ_mv").reindex(index=dates)
    return R.fillna(0.0), dates, MV


def _load_events(cfg: dict[str, Any]) -> pd.DataFrame:
    con = _linkage(cfg)
    try:
        cl = pd.read_sql_query(
            "SELECT asof,cluster_id,state,grade,birth_date,leader,n_members,linkage_score "
            "FROM clusters", con)
        mem = pd.read_sql_query("SELECT asof,cluster_id,ts_code FROM cluster_members", con)
    finally:
        con.close()
    if not len(cl):
        return pd.DataFrame()
    # T0 = 群体首次出现日（不允许事后调整形成日期）
    first = cl.groupby("cluster_id")["asof"].min().rename("T0").reset_index()
    cl = cl.merge(first, on="cluster_id")
    ev = cl[cl["asof"] == cl["T0"]].copy()
    # 群体在生命周期内可能先后经历多种状态；回测按「出现过该状态」多标签分组，
    # 否则 LEADER_EXPANSION / CLUSTER_STRENGTHENING 会因只取 T0 状态而永远无样本。
    lifecycle = cl.groupby("cluster_id")["state"].apply(lambda x: set(x)).to_dict()
    ev["states"] = ev["cluster_id"].map(lifecycle)
    ev = ev.merge(mem, on=["asof", "cluster_id"], how="left")
    return ev


def _path_ret(R: pd.DataFrame, dates: list[str], t0: str, codes: list[str],
              h: int, mv: pd.DataFrame | None = None) -> dict[str, float]:
    if t0 not in dates:
        return {}
    i = dates.index(t0)
    if i + h >= len(dates):
        return {}
    cols = [c for c in codes if c in R.columns]
    if not cols:
        return {}
    sub = R.iloc[i + 1: i + 1 + h][cols]
    eq_path = (1.0 + sub).prod(axis=0) - 1.0
    eq = float(eq_path.mean())
    # 市值加权（用 T0 当日流通市值）
    vw = eq
    if mv is not None and t0 in mv.index:
        w = mv.loc[t0, cols].astype(float)
        w = w.replace(0, np.nan).fillna(w.mean() if np.isfinite(w.mean()) else 1.0)
        if w.sum() > 0:
            vw = float((eq_path * (w / w.sum())).sum())
    mkt = float((1.0 + R.iloc[i + 1: i + 1 + h].mean(axis=1)).prod() - 1.0)
    nav = (1.0 + sub.mean(axis=1)).cumprod()
    mdd = float((nav / nav.cummax() - 1.0).min()) if len(nav) else 0.0
    pos = eq_path[eq_path > 0].mean() if (eq_path > 0).any() else 0.0
    neg = -eq_path[eq_path < 0].mean() if (eq_path < 0).any() else 0.0
    pl = float(pos / neg) if neg > 1e-9 else (3.0 if pos > 0 else 0.0)
    return {
        "ret_eq": eq, "ret_vw": vw, "ret_market": mkt,
        "excess_market": eq - mkt, "win_rate": float((eq_path > 0).mean()),
        "max_drawdown": mdd, "profit_loss_ratio": pl,
        "ret_after_cost": eq - COST, "excess_after_cost": eq - mkt - COST,
    }


def _control(R: pd.DataFrame, dates: list[str], t0: str, n: int,
             rng: np.random.default_rng, mode: str, codes: list[str],
             industry_map: dict[str, str], mv: pd.DataFrame) -> dict[str, float]:
    if t0 not in dates or dates.index(t0) + 20 >= len(dates):
        return {}
    pool = list(R.columns)
    if mode == "same_industry" and codes:
        inds = {industry_map.get(c) for c in codes if industry_map.get(c)}
        pool = [c for c in pool if industry_map.get(c) in inds] or pool
    elif mode == "similar_size" and t0 in mv.index:
        sizes = mv.loc[t0, [c for c in codes if c in mv.columns]].dropna()
        if len(sizes):
            lo, hi = sizes.quantile(0.25), sizes.quantile(0.75)
            cand = mv.loc[t0].dropna()
            pool = list(cand[(cand >= lo) & (cand <= hi)].index) or pool
    pick = rng.choice(pool, size=min(n, len(pool)), replace=False).tolist()
    out = {}
    for h in (5, 10):
        r = _path_ret(R, dates, t0, pick, h)
        out[f"ctrl_{mode}_excess_{h}"] = r.get("excess_market", np.nan)
    return out


def run_backtest(cfg: dict[str, Any], start: str, end: str) -> dict[str, Any]:
    ev = _load_events(cfg)
    if not len(ev):
        return {"status": "NO_EVENTS", "note": "快照库中无群体事件，请先运行历史回放"}
    # 取到最晚 T0 之后 25 个交易日，保证 T+20 可算
    all_dates = trade_dates(cfg, "99991231", int(cfg["data"].get("max_load_days", 420)))
    d_end = all_dates[-1]
    if d_end < end:
        d_end = end
    R, dates, MV = _load_market(cfg, min(ev["T0"].min(), start), d_end)
    basic = pd.read_csv(cfg["paths"]["stock_basic_csv"], dtype={"ts_code": str, "industry": str})
    industry_map = dict(zip(basic["ts_code"], basic["industry"].fillna("未知")))
    rng = np.random.default_rng(20261108)

    rows: list[dict[str, Any]] = []
    seen: list[set[str]] = []
    dedup_dropped = 0
    for (cid, t0), grp in ev.groupby(["cluster_id", "T0"]):
        codes = [str(c) for c in grp["ts_code"].dropna().tolist()]
        if len(codes) < 2:
            continue
        # 身份断裂会让同一批股票以不同 cluster_id 反复成为"新事件"，
        # 造成样本非独立、t 值虚高；成员重叠 >0.6 只保留首次。
        sig = set(codes)
        if any(len(sig & s) / max(1, len(sig | s)) > 0.60 for s in seen):
            dedup_dropped += 1
            continue
        seen.append(sig)
        state = str(grp["state"].iloc[0])
        i0 = dates.index(t0) if t0 in dates else -1
        mkt20 = float((1 + R.iloc[max(0, i0 - 20):i0].mean(axis=1)).prod() - 1) if i0 > 0 else 0.0
        regime = "BULL" if mkt20 > 0.03 else ("BEAR" if mkt20 < -0.03 else "SIDEWAYS")
        rec: dict[str, Any] = {
            "cluster_id": cid, "T0": t0, "state": state,
            "states": sorted(grp["states"].iloc[0]) if isinstance(grp["states"].iloc[0], (set, list, tuple)) else [state],
            "grade": str(grp["grade"].iloc[0]), "n_stocks": len(codes),
            "leader": str(grp["leader"].iloc[0]), "regime": regime,
            "linkage_score": float(grp["linkage_score"].iloc[0]),
            "main_industry": max(
                [industry_map.get(c, "未知") for c in codes],
                key=[industry_map.get(c, "未知") for c in codes].count),
        }
        for h in HORIZONS:
            r = _path_ret(R, dates, t0, codes, h, MV)
            for k, v in r.items():
                rec[f"h{h}_{k}"] = round(float(v), 6) if np.isfinite(v) else None
        # 行业超额（用主行业等权组合）
        ind = rec["main_industry"]
        ind_codes = [c for c in R.columns if industry_map.get(c) == ind]
        for h in (5, 10):
            ir = _path_ret(R, dates, t0, ind_codes[:200], h)
            rec[f"h{h}_excess_industry"] = round(
                rec.get(f"h{h}_ret_eq", 0) - ir.get("ret_eq", 0), 6)
        for mode in ("random", "same_industry", "similar_size"):
            rec.update({k: (round(float(v), 6) if np.isfinite(v) else None)
                        for k, v in _control(R, dates, t0, len(codes), rng, mode,
                                             codes, industry_map, MV).items()})
        rows.append(rec)

    edf = pd.DataFrame(rows)
    span = int(edf["T0"].nunique()) if len(edf) else 0
    summary: dict[str, Any] = {"events": len(edf), "horizons": list(HORIZONS),
                               "unique_formation_days": span,
                               "dedup_dropped_overlapping_events": dedup_dropped}
    state_summary = {}
    for st in STATES + ("ALL",):
        sub = edf if st == "ALL" else edf[edf["states"].apply(lambda ss, s=st: s in ss)]
        item: dict[str, Any] = {"n": int(len(sub))}
        for h in (5, 10, 20):
            col = f"h{h}_excess_market"
            if col in sub and len(sub):
                v = pd.to_numeric(sub[col], errors="coerce").dropna()
                item[f"T{h}_mean_excess"] = round(float(v.mean()), 5) if len(v) else None
                item[f"T{h}_win_rate"] = round(float((v > 0).mean()), 4) if len(v) else None
                item[f"T{h}_tstat"] = (round(float(v.mean() / (v.std() / math.sqrt(len(v)))), 3)
                                       if len(v) > 1 and v.std() > 1e-9 else None)
                item[f"T{h}_n"] = int(len(v))
        for mode in ("random", "same_industry", "similar_size"):
            col = f"ctrl_{mode}_excess_10"
            if col in sub and len(sub):
                item[f"control_{mode}_T10_excess"] = round(
                    float(pd.to_numeric(sub[col], errors="coerce").mean()), 5)
        state_summary[st] = item
    summary["by_state"] = state_summary
    by_regime = {}
    for rg, sub in edf.groupby("regime"):
        col = "h10_excess_market"
        v = pd.to_numeric(sub[col], errors="coerce").dropna() if col in sub else pd.Series(dtype=float)
        by_regime[str(rg)] = {"n": int(len(sub)),
                              "T10_mean_excess": round(float(v.mean()), 5) if len(v) else None}
    summary["by_regime"] = by_regime

    a = state_summary["ALL"]
    n = a.get("T10_n", 0)
    t = a.get("T10_tstat")
    ex = a.get("T10_mean_excess")
    if n is None or n < 30:
        verdict = "UNPROVEN"
        reason = f"样本仅 {n or 0} 个事件（<30），证据不足，不做有效性结论"
    elif span < 40:
        # 无独立样本外区间、无严格滚动验证，样本内显著也不能宣称 PASS
        verdict = "CONDITIONAL"
        reason = (f"样本内 T+10 超额 {ex:.4f} (t={t})，但样本期仅 {span} 个形成日，"
                  f"缺独立样本外区间与严格多重检验校正，结论仅为条件性")
    elif ex is not None and ex <= 0:
        verdict = "FAIL"
        reason = f"T+10 平均超额 {ex:.4f} <= 0，未见稳定增量收益"
    elif t is not None and t > 2 and all((state_summary[s].get("T10_mean_excess") or -9) > 0
                                         for s in STATES if state_summary[s]["n"] >= 5):
        verdict = "PASS"
        reason = f"T+10 超额 {ex:.4f}, t={t}, 各状态均为正"
    else:
        verdict = "CONDITIONAL"
        reason = "仅在部分状态或市场环境下有效"
    summary["verdict"] = verdict
    summary["reason"] = reason
    summary["cost_bp"] = 30
    summary["disclaimer"] = ("回测为事后评估；实时发现链路严格 as-of。"
                             "对照组为随机/同行业/同规模流动性股票组合，板块指数用行业等权代理。"
                             "样本外区间与多重检验的严格 FDR 校正尚未完成，结论偏保守。")

    outdir = Path(cfg["paths"]["output_dir"]) / "backtest"
    outdir.mkdir(parents=True, exist_ok=True)
    edf.to_csv(outdir / "events.csv", index=False, encoding="utf-8-sig")
    write_json(outdir / "summary.json", summary)
    lines = [
        "# 动态联动群体 · 事件回测报告", "",
        f"- 事件数：{len(edf)}（去重剔除 {dedup_dropped} 个成员高度重叠事件）；"
        f"独立形成日：{span}；区间：{start} ~ {end}；成本：30bp",
        f"- **结论：{verdict}** —— {reason}", "",
        "## 分状态 T+5 / T+10 / T+20 超额收益", "",
        "| 状态 | 样本 | T+5超额 | T+10超额 | T+20超额 | T+10胜率 | T+10 t值 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for st in STATES + ("ALL",):
        it = state_summary[st]
        lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            st, it["n"], it.get("T5_mean_excess", "-"), it.get("T10_mean_excess", "-"),
            it.get("T20_mean_excess", "-"), it.get("T10_win_rate", "-"), it.get("T10_tstat", "-")))
    lines += ["", "## 对照组（T+10 平均超额）", ""]
    for mode in ("random", "same_industry", "similar_size"):
        lines.append(f"- {mode}: {a.get(f'control_{mode}_T10_excess', '-')}")
    lines += ["", "## 市场环境", ""]
    for rg, it in by_regime.items():
        lines.append(f"- {rg}: n={it['n']}, T+10超额={it['T10_mean_excess']}")
    lines += ["", "> " + summary["disclaimer"], ""]
    (outdir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    log.info("回测完成: 事件 %d, 结论 %s (%s)", len(edf), verdict, reason)
    return {"status": "OK", "events": len(edf), "verdict": verdict,
            "reason": reason, "report": str(outdir / "report.md"), "summary": summary}
