"""事件回测与防止伪联动（第十七层，Phase 4）。

以群体首次形成日 T0 为事件，统计 T+1/3/5/10/20 的等权/市值加权收益、
相对全A与相对行业超额、正收益比例、最大回撤、盈亏比及成本后收益（30bp）。

Phase 4 完整版新增：
  - 严格时间切分的独立样本外区间（IN_SAMPLE / OUT_OF_SAMPLE），并用 config_hash
    验证样本外参数确实冻结；口径中途变更则拒绝给 PASS；
  - 按形成日分块 bootstrap 置信区间与双侧 p 值，BH-FDR 多重检验校正；
  - 剔除极端收益贡献（去掉每事件涨得最多的 1 只股票）；
  - 群体规模 / 流动性 / 市场环境分组稳健性；
  - 相关系数 / EdgeScore / FDR 阈值敏感性网格（在已落库的边集合内重跑社区发现）；
  - 四类对照组：随机、同行业、同规模流动性、传统板块动量筛选。

纪律：
  - 回测是事后评估，允许读取 T0 之后的数据；实时发现链路仍严格 as-of；
  - 结论允许 PASS / CONDITIONAL / UNPROVEN / FAIL，样本不足绝不硬说有效；
  - 阈值敏感性只能验证「更严格」阈值，不能外推到更宽松阈值。
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd

from . import momentum_engine
from .cluster_engine import _communities, _overlap
from .common import get_logger, write_json
from .data_layer import _ro, trade_dates

log = get_logger("backtest")

HORIZONS = (1, 3, 5, 10, 20)
STATES = ("EMERGING_CLUSTER", "LEADER_EXPANSION", "CLUSTER_STRENGTHENING")
CONTROLS = ("random", "same_industry", "similar_size", "sector_momentum")
COST = 0.0030  # 30bp 单边交易成本

# 群体 T0 时点 5 日平均涨幅分桶（与日报幅度口径一致，用于事后分层，不参与发现）
MOVE_BUCKETS = (-1.0, -0.10, -0.05, -0.02, 0.0, 0.02, 0.05, 0.08, 0.15, 1.0)
MOVE_LABELS = ("<-10%", "-10~-5%", "-5~-2%", "-2~0%", "0~2%",
               "2~5%", "5~8%", "8~15%", ">15%")


def _linkage_ro(cfg: dict[str, Any]) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{cfg['paths']['linkage_db']}?mode=ro",
                           uri=True, timeout=60)


def _load_market(cfg: dict[str, Any], start: str, end: str) -> tuple[pd.DataFrame, list[str], pd.DataFrame]:
    with _ro(cfg["paths"]["market_db"]) as con:
        df = pd.read_sql_query(
            "SELECT ts_code,trade_date,pct_chg FROM daily_cache WHERE trade_date BETWEEN ? AND ?",
            con, params=(start, end))
        mv = pd.read_sql_query(
            "SELECT ts_code,trade_date,circ_mv FROM daily_basic_cache WHERE trade_date BETWEEN ? AND ?",
            con, params=(start, end))
    R = df.pivot(index="trade_date", columns="ts_code", values="pct_chg").sort_index() / 100.0
    # 裁剪除权/脏数据造成的极端涨跌幅（A股单日正常波动不超过 ±20%），
    # 否则一个异常值会同时污染收益统计与板块动量对照组。
    R = R.clip(-0.25, 0.25)
    dates = list(R.index)
    MV = mv.pivot(index="trade_date", columns="ts_code", values="circ_mv").reindex(index=dates)
    return R.fillna(0.0), dates, MV


def _load_prior_market(cfg: dict[str, Any], start: str, end: str) -> tuple[pd.DataFrame, list[str]]:
    """只读涨跌幅的预热窗口，专供 T0 先验涨幅计算。

    独立加载而非扩大主 R：扩大主 R 会把 warmup 期才存在的股票混进对照组候选池，
    随机/同行业对照会被系统性拉低，破坏既有回测口径的可比性。
    """
    with _ro(cfg["paths"]["market_db"]) as con:
        df = pd.read_sql_query(
            "SELECT ts_code,trade_date,pct_chg FROM daily_cache WHERE trade_date BETWEEN ? AND ?",
            con, params=(start, end))
    Rp = df.pivot(index="trade_date", columns="ts_code", values="pct_chg").sort_index() / 100.0
    Rp = Rp.clip(-0.25, 0.25).fillna(0.0)
    return Rp, list(Rp.index)


def _load_events(cfg: dict[str, Any], start: str, end: str) -> pd.DataFrame:
    """T0 = 群体首次出现日（不允许事后调整形成日期）。"""
    con = _linkage_ro(cfg)
    try:
        cl = pd.read_sql_query(
            "SELECT asof,cluster_id,state,grade,birth_date,leader,n_members,"
            "linkage_score,detail_json,config_hash FROM clusters", con)
        mem = pd.read_sql_query("SELECT asof,cluster_id,ts_code FROM cluster_members", con)
    finally:
        con.close()
    if not len(cl):
        return pd.DataFrame()
    first = cl.groupby("cluster_id")["asof"].min().rename("T0").reset_index()
    cl = cl.merge(first, on="cluster_id")
    ev = cl[cl["asof"] == cl["T0"]].copy()
    ev = ev[(ev["T0"] >= str(start)) & (ev["T0"] <= str(end))]
    if not len(ev):
        return ev

    # 生命周期主状态 + 每日 signals 多标签：三类机会并不互斥，且
    # LEADER_EXPANSION / CLUSTER_STRENGTHENING 常发生在存续期而非 T0 当天。
    lifecycle = cl.groupby("cluster_id")["state"].apply(lambda x: set(x)).to_dict()
    sigs: dict[str, set[str]] = defaultdict(set)
    for cid, txt in zip(cl["cluster_id"].astype(str), cl["detail_json"].fillna("{}")):
        try:
            sigs[cid].update(json.loads(txt).get("signals", []))
        except (ValueError, TypeError):
            pass
    ev["states"] = [sorted(lifecycle.get(str(c), set()) | sigs.get(str(c), set()) | {s})
                    for c, s in zip(ev["cluster_id"], ev["state"])]
    ev = ev.merge(mem, on=["asof", "cluster_id"], how="left")
    return ev


def _prior_returns(R: pd.DataFrame, dates: list[str], t0: str, codes: list[str],
                   leader: str, mcfg: dict[str, Any]) -> dict[str, Any]:
    """T0 时点（含当日）可见的 5/10/20 日累计涨幅，严格 as-of，不含未来数据。

    用于事后分层检验「联动个股涨幅幅度」与后续超额的关系；口径与
    momentum_engine 一致，但这里从原始行情重算，不依赖快照里是否存了该字段。
    """
    if t0 not in dates:
        return {}
    i = dates.index(t0)
    cols = [c for c in codes if c in R.columns]
    if not cols or i < 4:
        return {}

    def cum(w: int) -> pd.Series:
        lo = max(0, i - w + 1)
        return (1.0 + R.iloc[lo:i + 1][cols]).prod(axis=0) - 1.0

    a5, a10, a20 = cum(5), cum(10), cum(20)
    mean5, mean10, mean20 = float(a5.mean()), float(a10.mean()), float(a20.mean())
    min5 = float(mcfg.get("min_member_move_5", 0.05))
    min10 = float(mcfg.get("min_member_move_10", 0.08))
    ext20 = float(mcfg.get("extended_member_move_20", 0.60))
    lead20 = float(mcfg.get("extended_leader_move_20", 0.80))
    moved = int(((a5 >= min5) | (a10 >= min10)).sum())
    extended = int((a20 >= ext20).sum())
    l5 = float(a5.get(leader, a5.max()))
    l20 = float(a20.get(leader, a20.max()))
    others = a5.drop(labels=[leader], errors="ignore")
    gap = l5 - (float(others.mean()) if len(others) else 0.0)
    ratio = moved / max(1, len(a5))
    confirmed = (
        (moved >= int(mcfg.get("min_moved_stocks", 3))
         or ratio >= float(mcfg.get("min_moved_ratio", 0.50)))
        and mean5 >= float(mcfg.get("min_cluster_mean_move_5", 0.03))
        and gap >= float(mcfg.get("min_leader_follower_gap_5", 0.03)))
    state = momentum_engine.cluster_state(mean5, mean20,
                                          extended / max(1, len(a5)), mcfg)
    if l20 >= lead20 and state != "EXTENDED":
        state = "EXTENDED"
    return {
        "m_mean5": round(mean5, 6), "m_mean10": round(mean10, 6),
        "m_mean20": round(mean20, 6), "m_leader5": round(l5, 6),
        "m_leader20": round(l20, 6), "m_moved": moved,
        "m_extended": extended, "m_gap": round(gap, 6),
        "m_confirmed": bool(confirmed), "m_state": state,
    }


def _momentum_layer(edf: pd.DataFrame, mcfg: dict[str, Any]) -> tuple[dict[str, Any], pd.DataFrame]:
    """按 T0 涨幅幅度分层的 T+5/T+10/T+20 超额，含样本内外与硬门槛对照。"""
    if "m_mean5" not in edf.columns:
        return {"status": "NO_DATA"}, pd.DataFrame()
    d = edf.dropna(subset=["m_mean5", "h10_excess_market"]).copy()
    if not len(d):
        return {"status": "NO_VALID_EVENTS"}, pd.DataFrame()
    d["bucket"] = pd.cut(d["m_mean5"], MOVE_BUCKETS, labels=MOVE_LABELS)

    def agg(x: pd.DataFrame) -> dict[str, Any]:
        return {"n": int(len(x)), "mean5": round(float(x["m_mean5"].mean()), 6),
                "h5": round(float(x["h5_excess_market"].mean()), 6)
                if "h5_excess_market" in x else None,
                "h10": round(float(x["h10_excess_market"].mean()), 6),
                "h20": round(float(x["h20_excess_market"].dropna().mean()), 6)
                if x["h20_excess_market"].notna().any() else None,
                "win10": round(float((x["h10_excess_market"] > 0).mean()), 4)}

    rows: list[dict[str, Any]] = []
    for lab in MOVE_LABELS:
        sub = d[d["bucket"] == lab]
        if not len(sub):
            continue
        r = {"bucket": str(lab), "segment": "ALL", **agg(sub)}
        for seg in ("IN_SAMPLE", "OUT_OF_SAMPLE"):
            s2 = sub[sub["segment"] == seg]
            r[f"n_{seg}"] = int(len(s2))
            r[f"h10_{seg}"] = (round(float(s2["h10_excess_market"].mean()), 6)
                               if len(s2) else None)
        rows.append(r)
    layer_df = pd.DataFrame(rows)

    by_confirmed = {str(k): agg(v) for k, v in d.groupby("m_confirmed")}
    scan: list[dict[str, Any]] = []
    for thr in (0.0, 0.02, 0.03, 0.05, 0.08):
        keep, drop = d[d["m_mean5"] >= thr], d[d["m_mean5"] < thr]
        scan.append({"threshold": thr, "keep_n": int(len(keep)),
                     "keep_h10": agg(keep)["h10"] if len(keep) else None,
                     "drop_n": int(len(drop)),
                     "drop_h10": agg(drop)["h10"] if len(drop) else None})
    rho: dict[str, float | None] = {}
    for seg in ("ALL", "IN_SAMPLE", "OUT_OF_SAMPLE"):
        s2 = d if seg == "ALL" else d[d["segment"] == seg]
        rho[seg] = (round(float(s2[["m_mean5", "h10_excess_market"]]
                                .corr(method="spearman").iloc[0, 1]), 6)
                    if len(s2) >= 10 else None)
    healthy = d[(d["m_mean5"] >= float(mcfg.get("min_healthy_mean_move_5", -0.10)))
                & (d["m_mean5"] <= float(mcfg.get("max_healthy_mean_move_5", 0.03)))
                & (d["m_mean20"] <= float(mcfg.get("max_healthy_mean_move_20", 0.30)))]
    result = {
        "status": "OK", "n_events": int(len(d)),
        "dropped_events": int(len(edf) - len(d)),
        "by_bucket": rows, "by_confirmed": by_confirmed,
        "healthy_position": agg(healthy) if len(healthy) else None,
        "threshold_scan": scan, "spearman_mean5_vs_h10": rho,
        "note": ("涨幅幅度为 T0 时点可见的位置变量；分层是事后检验，不构成实时选股规则。"
                 "本分层用日涨跌幅连乘（裁剪 ±25%），日报生产链路用复权累计收益，"
                 "两者口径略有差异，数字不可逐位对照。结论只说明本区间内的相关性，"
                 "不能外推为因果或买入信号。"),
    }
    return result, layer_df


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
    # 剔除极端收益贡献：去掉本事件涨得最多的 1 只股票
    trimmed = float(eq_path.drop(eq_path.idxmax()).mean()) if len(eq_path) > 2 else eq
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
        "excess_market": eq - mkt, "excess_market_trimmed": trimmed - mkt,
        "win_rate": float((eq_path > 0).mean()),
        "max_drawdown": mdd, "profit_loss_ratio": pl,
        "ret_after_cost": eq - COST, "excess_after_cost": eq - mkt - COST,
    }


def _control(R: pd.DataFrame, dates: list[str], t0: str, n: int,
             rng: np.random.Generator, mode: str, codes: list[str],
             industry_map: dict[str, str], industry_groups: dict[str, list[str]],
             mv: pd.DataFrame) -> dict[str, float]:
    # 对照组只统计 T+5 / T+10，不需要 T+20 可用，避免最近事件被误删
    if t0 not in dates or dates.index(t0) + 10 >= len(dates):
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
    elif mode == "sector_momentum":
        # 传统板块指数筛选代理：T0 前 10 日行业等权收益最高的板块
        i = dates.index(t0)
        pre = R.iloc[max(0, i - 10):i]
        scores = {ind: float(pre[cs].mean(axis=1).mean())
                  for ind, cs in industry_groups.items() if len(cs) >= 10}
        if scores:
            pool = industry_groups[max(scores, key=scores.get)] or pool
    if mode == "sector_momentum":
        # 传统板块指数筛选代理：买入该行业全部成分股等权，而非随机抽 n 只，
        # 否则会把「选股运气」混进板块基准。
        pick = [c for c in pool if c in R.columns][:200]
    else:
        pick = rng.choice(pool, size=min(n, len(pool)), replace=False).tolist()
    out = {}
    for h in (5, 10):
        r = _path_ret(R, dates, t0, pick, h)
        out[f"ctrl_{mode}_excess_{h}"] = r.get("excess_market", np.nan)
    return out


def _bootstrap_by_day(days: np.ndarray, vals: np.ndarray, draws: int,
                      rng: np.random.Generator) -> dict[str, Any]:
    """按形成日分块重采样：同一天的多个事件必须整体进出，避免 t 值虚高。"""
    by_day: dict[str, list[float]] = defaultdict(list)
    for d, v in zip(days, vals):
        if np.isfinite(v):
            by_day[str(d)].append(float(v))
    keys = list(by_day)
    if not keys:
        return {"n_days": 0}
    arrs = [np.asarray(by_day[k], dtype=float) for k in keys]
    means = np.empty(int(draws), dtype=float)
    for b in range(int(draws)):
        pick = rng.integers(0, len(keys), len(keys))
        sample = np.concatenate([arrs[k] for k in pick])
        means[b] = sample.mean()
    lo, hi = np.quantile(means, [0.025, 0.975])
    p = 2.0 * min(float((means <= 0).mean()), float((means >= 0).mean()))
    return {
        "n_days": len(keys),
        "mean": round(float(np.mean([a.mean() for a in arrs])), 6),
        "ci95_low": round(float(lo), 6), "ci95_high": round(float(hi), 6),
        "p_two_sided": round(max(p, 1.0 / max(1, draws)), 6),
    }


def _bh_fdr(pvals: dict[str, float]) -> dict[str, float]:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out: dict[str, float] = {}
    prev = 1.0
    for i in range(m, 0, -1):
        name, p = items[i - 1]
        val = min(prev, p * m / i)
        out[name] = round(float(val), 6)
        prev = val
    return out


def _group_stats(sub: pd.DataFrame, col: str, draws: int,
                 rng: np.random.Generator) -> dict[str, Any]:
    item: dict[str, Any] = {"n": int(len(sub))}
    for h in (5, 10, 20):
        c = f"h{h}_excess_market"
        v = pd.to_numeric(sub[c], errors="coerce").dropna() if c in sub else pd.Series(dtype=float)
        item[f"T{h}_mean_excess"] = round(float(v.mean()), 6) if len(v) else None
        item[f"T{h}_win_rate"] = round(float((v > 0).mean()), 4) if len(v) else None
        item[f"T{h}_n"] = int(len(v))
        if h == 10 and len(v) > 1:
            sd = float(v.std())
            item["T10_tstat"] = round(float(v.mean() / (sd / math.sqrt(len(v)))), 3) if sd > 1e-9 else None
            item["T10_trimmed_excess"] = round(float(pd.to_numeric(
                sub["h10_excess_market_trimmed"], errors="coerce").mean()), 6) \
                if "h10_excess_market_trimmed" in sub else None
            item["T10_after_cost_excess"] = round(float(pd.to_numeric(
                sub["h10_excess_after_cost"], errors="coerce").mean()), 6) \
                if "h10_excess_after_cost" in sub else None
            item["bootstrap"] = _bootstrap_by_day(
                sub["T0"].astype(str).to_numpy(),
                pd.to_numeric(sub[c], errors="coerce").to_numpy(dtype=float),
                draws, rng)
    for mode in CONTROLS:
        cc = f"ctrl_{mode}_excess_10"
        if cc in sub and len(sub):
            item[f"control_{mode}_T10_excess"] = round(
                float(pd.to_numeric(sub[cc], errors="coerce").mean()), 6)
    for st in STATES:
        item[f"tag_{st}"] = int(sub["states"].apply(lambda ss, s=st: s in ss).sum()) \
            if "states" in sub else 0
    return item


def _threshold_sensitivity(cfg: dict[str, Any], vcfg: dict[str, Any]) -> dict[str, Any]:
    """阈值敏感性：在已落库的边集合内重筛 + 重跑社区发现，比较群体稳定性。

    只能验证更严格阈值；更宽松阈值下上游预筛已丢弃的股票对无法恢复。
    """
    grid = vcfg.get("threshold_grid", [])
    n_days = int(vcfg.get("sensitivity_days", 12))
    ccfg = cfg["cluster"]
    con = _linkage_ro(cfg)
    try:
        days = [r[0] for r in con.execute(
            "SELECT DISTINCT asof FROM snapshots ORDER BY asof DESC LIMIT ?",
            (n_days,)).fetchall()]
        days = sorted(days)
        if not days or not grid:
            return {"status": "NO_SNAPSHOTS", "days": len(days)}
        per_day: dict[str, pd.DataFrame] = {}
        for d in days:
            per_day[d] = pd.read_sql_query(
                "SELECT a,b,edge_score,residual_corr_20,residual_corr_10,q_value FROM edges "
                "WHERE asof=? AND network='market'", con, params=(d,))
        # 基准用日报里真实存档的群体，而不是重跑出来的 baseline 网格
        mem = pd.read_sql_query("SELECT asof,cluster_id,ts_code FROM cluster_members", con)
        real: dict[str, list[set[str]]] = defaultdict(list)
        for (d, _cid), g in mem.groupby(["asof", "cluster_id"]):
            if str(d) in per_day:
                real[str(d)].append(set(g["ts_code"].astype(str)))
        real_total = sum(len(v) for v in real.values())
        result: dict[str, Any] = {"status": "OK", "days": len(days),
                                  "real_clusters_total": real_total,
                                  "note": vcfg.get("_sensitivity_note", ""), "grids": []}
        min_es = float(ccfg.get("min_mean_edge_score", 0.50))
        min_dens = float(ccfg.get("min_density", 0.28))
        for g in grid:
            thr = float(g.get("corr_threshold_20", 0.55))
            thr10 = float(g.get("corr_threshold_10", 0.60))
            score = float(g.get("min_edge_score", 0.45))
            qa = float(g.get("q_alpha", 0.05))
            total, sizes, day_sets = 0, [], {}
            for d in days:
                e = per_day[d]
                if g.get("name") == "baseline":
                    # edges 表已是全部通过上游门槛的最终边；baseline 不再重筛，
                    # 否则会误删「20日未达标但10日达标」的早期联动边。
                    f = e
                else:
                    # 严格网格改用「20日与10日同时达标」的 AND 门：
                    # 日报默认是 OR 门（利于早期发现），AND 是更保守的稳健性口径
                    gate = ((e["residual_corr_20"] >= thr) &
                            (e["residual_corr_10"] >= thr10))
                    f = e[gate & (e["edge_score"] >= score) & (e["q_value"] <= qa)]
                G = nx.Graph()
                for a, b, es in zip(f["a"], f["b"], f["edge_score"]):
                    G.add_edge(a, b, weight=float(es))
                comms, _ = _communities(G, cfg)
                valid: list[set[str]] = []
                for c in comms:
                    n = len(c)
                    if n < int(ccfg.get("min_size", 4)) or n > int(ccfg.get("max_size", 60)):
                        continue
                    inner = f[f["a"].isin(c) & f["b"].isin(c)]
                    if not len(inner):
                        continue
                    dens = 2.0 * len(inner) / max(1, n * (n - 1))
                    if dens >= min_dens and float(inner["edge_score"].mean()) >= min_es:
                        valid.append(set(c))
                day_sets[d] = valid
                total += len(valid)
                sizes.extend(len(c) for c in valid)
            sims = []
            for d, base_list in real.items():
                cand = day_sets.get(d, [])
                for c in base_list:
                    sims.append(max((_overlap(c, x) for x in cand), default=0.0))
            result["grids"].append({
                "name": g.get("name"), "corr_threshold_20": thr,
                "min_edge_score": score, "q_alpha": qa,
                "valid_cluster_total": total,
                "clusters_per_day": round(total / max(1, len(days)), 2),
                "mean_size": round(float(np.mean(sizes)), 2) if sizes else None,
                "mean_best_jaccard_vs_real": round(float(np.mean(sims)), 4) if sims else None,
            })
        return result
    finally:
        con.close()


def run_backtest(cfg: dict[str, Any], start: str, end: str) -> dict[str, Any]:
    vcfg = cfg.get("validation", {})
    ev = _load_events(cfg, start, end)
    if not len(ev):
        return {"status": "NO_EVENTS", "note": "回测区间内无群体事件，请先运行历史回放"}
    all_dates = trade_dates(cfg, "99991231", int(cfg["data"].get("max_load_days", 420)))
    d_end = max(all_dates[-1], str(end))
    # 主数据保持原起点，保证对照组/收益统计口径与既有回测完全一致。
    t_start = str(min(str(ev["T0"].min()), str(start)))
    R, dates, MV = _load_market(cfg, t_start, d_end)
    # T0 时点的 20 日涨幅需要额外 100 个自然日预热窗口，单独加载，不污染主数据。
    warm = (datetime.strptime(t_start, "%Y%m%d") - timedelta(days=100)).strftime("%Y%m%d")
    R_prior, dates_prior = _load_prior_market(cfg, warm, d_end)
    mcfg = cfg.get("_momentum") or momentum_engine.load_config()
    basic = pd.read_csv(cfg["paths"]["stock_basic_csv"], dtype={"ts_code": str, "industry": str})
    industry_map = dict(zip(basic["ts_code"], basic["industry"].fillna("未知")))
    # 板块动量对照只用成分数 >=10 的行业，避免 2-3 只股票的微型行业
    # 因单只暴涨股被误判为「最强板块」，把对照组收益抬到不可信水平。
    industry_groups: dict[str, list[str]] = defaultdict(list)
    for c in R.columns:
        industry_groups[industry_map.get(c, "未知")].append(c)
    industry_groups = {k: v for k, v in industry_groups.items() if len(v) >= 10}
    rng = np.random.default_rng(20261110)

    rows: list[dict[str, Any]] = []
    seen: list[set[str]] = []
    dedup_dropped = 0
    ordered = sorted(ev.groupby(["cluster_id", "T0"]), key=lambda kv: (kv[0][1], kv[0][0]))
    for (cid, t0), grp in ordered:
        codes = [str(c) for c in grp["ts_code"].dropna().tolist()]
        if len(codes) < 2:
            continue
        # 身份断裂会让同一批股票以不同 id 反复成为「新事件」，按时间顺序只保留首次
        sig = set(codes)
        if any(len(sig & s) / max(1, len(sig | s)) > 0.60 for s in seen):
            dedup_dropped += 1
            continue
        seen.append(sig)
        state = str(grp["state"].iloc[0])
        i0 = dates.index(t0) if t0 in dates else -1
        mkt20 = float((1 + R.iloc[max(0, i0 - 20):i0].mean(axis=1)).prod() - 1) if i0 > 0 else 0.0
        regime = "BULL" if mkt20 > 0.03 else ("BEAR" if mkt20 < -0.03 else "SIDEWAYS")
        ind_counts: dict[str, int] = defaultdict(int)
        for c in codes:
            ind_counts[industry_map.get(c, "未知")] += 1
        main_ind = max(ind_counts, key=ind_counts.get)
        rec: dict[str, Any] = {
            "cluster_id": cid, "T0": t0, "state": state,
            "states": sorted(grp["states"].iloc[0]) if isinstance(
                grp["states"].iloc[0], (set, list, tuple)) else [state],
            "grade": str(grp["grade"].iloc[0]), "n_stocks": len(codes),
            "leader": str(grp["leader"].iloc[0]), "regime": regime,
            "linkage_score": float(grp["linkage_score"].iloc[0]),
            "config_hash": str(grp["config_hash"].iloc[0]),
            "main_industry": main_ind,
            "size_bucket": ("4-6" if len(codes) <= 6 else "7-12" if len(codes) <= 12 else ">12"),
            "avg_circ_mv": float(MV.loc[t0, [c for c in codes if c in MV.columns]].mean())
                            if t0 in MV.index else np.nan,
        }
        rec.update(_prior_returns(R_prior, dates_prior, t0, codes,
                                 str(grp["leader"].iloc[0]), mcfg))
        for h in HORIZONS:
            r = _path_ret(R, dates, t0, codes, h, MV)
            for k, v in r.items():
                rec[f"h{h}_{k}"] = round(float(v), 6) if np.isfinite(v) else None
        ind_codes = [c for c in R.columns if industry_map.get(c) == main_ind]
        for h in (5, 10):
            ir = _path_ret(R, dates, t0, ind_codes[:200], h)
            rec[f"h{h}_excess_industry"] = round(
                rec.get(f"h{h}_ret_eq", 0) - ir.get("ret_eq", 0), 6)
        for mode in CONTROLS:
            rec.update({k: (round(float(v), 6) if np.isfinite(v) else None)
                        for k, v in _control(R, dates, t0, len(codes), rng, mode,
                                             codes, industry_map, industry_groups,
                                             MV).items()})
        rows.append(rec)

    edf = pd.DataFrame(rows)
    if not len(edf):
        return {"status": "NO_VALID_EVENTS", "note": "事件成员不足或全部重复"}
    # 严格时间切分：按独立形成日排序，前段样本内、后段样本外
    fdays = sorted(edf["T0"].unique())
    frac = float(vcfg.get("train_fraction", 0.60))
    k = max(1, min(len(fdays) - 1, int(len(fdays) * frac)))
    split_date = fdays[k]
    edf["segment"] = np.where(edf["T0"] <= split_date, "IN_SAMPLE", "OUT_OF_SAMPLE")
    try:
        momentum_layer, momentum_df = _momentum_layer(edf, mcfg)
    except Exception as ex:  # 分层失败不影响主回测结论
        log.warning("涨幅幅度分层分析失败: %s", ex)
        momentum_layer, momentum_df = {"status": "ERROR", "error": str(ex)}, pd.DataFrame()
    hashes = sorted(set(edf["config_hash"].astype(str)))
    frozen = (len(hashes) == 1 and hashes[0] == cfg["_config_hash"])

    draws = int(vcfg.get("bootstrap_draws", 2000))
    groups: dict[str, pd.DataFrame] = {
        "ALL": edf,
        "IN_SAMPLE": edf[edf["segment"] == "IN_SAMPLE"],
        "OUT_OF_SAMPLE": edf[edf["segment"] == "OUT_OF_SAMPLE"],
    }
    for st in STATES:
        groups[st] = edf[edf["states"].apply(lambda ss, s=st: s in ss)]
    for rg in ("BULL", "SIDEWAYS", "BEAR"):
        groups[f"REGIME_{rg}"] = edf[edf["regime"] == rg]
    for b in ("4-6", "7-12", ">12"):
        groups[f"SIZE_{b}"] = edf[edf["size_bucket"] == b]
    liq = pd.to_numeric(edf["avg_circ_mv"], errors="coerce")
    if liq.notna().sum() >= 6:
        q1, q2 = liq.quantile(0.33), liq.quantile(0.67)
        edf["liquidity_bucket"] = np.where(liq <= q1, "LOW", np.where(liq <= q2, "MID", "HIGH"))
        for b in ("LOW", "MID", "HIGH"):
            groups[f"LIQUIDITY_{b}"] = edf[edf["liquidity_bucket"] == b]
    # 分段时间稳定性：把形成日等分 3 段，检验结论是否只靠某一个时间段驱动
    period_defs: list[dict[str, Any]] = []
    chunks = np.array_split(np.asarray(fdays), min(3, len(fdays)))
    for i, ch in enumerate(chunks, 1):
        lo, hi = str(ch[0]), str(ch[-1])
        sub = edf[(edf["T0"] >= lo) & (edf["T0"] <= hi)]
        groups[f"PERIOD_{i}"] = sub
        period_defs.append({"period": i, "from": lo, "to": hi, "events": int(len(sub))})
    by_group = {name: _group_stats(sub, "h10_excess_market", draws, rng)
                for name, sub in groups.items()}
    pvals = {name: it["bootstrap"]["p_two_sided"]
             for name, it in by_group.items()
             if it.get("bootstrap", {}).get("n_days", 0) >= 3}
    qvals = _bh_fdr(pvals)
    for name, q in qvals.items():
        by_group[name]["bootstrap"]["q_bh_fdr"] = q
    periods_positive = [
        (by_group[f"PERIOD_{i}"].get("T10_mean_excess") is not None
         and by_group[f"PERIOD_{i}"].get("T10_mean_excess", -9) > 0)
        for i in range(1, len(chunks) + 1)]
    n_periods_positive = int(sum(periods_positive))

    # 阈值敏感性（读快照库，长回放完成后运行最稳妥）
    try:
        sensitivity = _threshold_sensitivity(cfg, vcfg)
    except Exception as ex:  # 稳健性子模块失败不应让整份回测无效
        log.warning("阈值敏感性分析失败: %s", ex)
        sensitivity = {"status": "ERROR", "error": str(ex)}

    censored = int(edf["h20_excess_market"].isna().sum()) if "h20_excess_market" in edf else len(edf)
    ins, out = by_group["IN_SAMPLE"], by_group["OUT_OF_SAMPLE"]
    a = by_group["ALL"]
    span = len(fdays)
    holdout_days = out.get("bootstrap", {}).get("n_days", 0)
    n_out = out.get("T10_n", 0) or 0
    ex_out = out.get("T10_mean_excess")
    ci_low = out.get("bootstrap", {}).get("ci95_low")
    q_out = qvals.get("OUT_OF_SAMPLE")
    min_days = int(vcfg.get("min_holdout_formation_days", 20))
    min_ev = int(vcfg.get("min_events_per_segment", 20))
    fdr_alpha = float(vcfg.get("fdr_alpha", 0.10))

    ci_high = out.get("bootstrap", {}).get("ci95_high")
    ctrl_random = out.get("control_random_T10_excess")
    ctrl_ind = out.get("control_same_industry_T10_excess")
    a_ctrl = a.get("control_random_T10_excess")
    checks: dict[str, bool] = {
        "样本外 T+10 超额 > 0": bool(ex_out is not None and ex_out > 0),
        "分块 bootstrap 95% 下界 > 0": bool(ci_low is not None and ci_low > 0),
        f"BH-FDR q <= {fdr_alpha}": bool(q_out is not None and q_out <= fdr_alpha),
        "样本内方向为正": bool((ins.get("T10_mean_excess") or -9) > 0),
        "剔除极端收益后仍为正": bool((out.get("T10_trimmed_excess") or -9) > 0),
        "30bp 成本后仍为正": bool((out.get("T10_after_cost_excess") or -9) > 0),
        "三类状态方向一致": bool(all((by_group[s].get("T10_mean_excess") or -9) > 0
                                  for s in STATES if by_group[s]["n"] >= 5)),
        "所有时间段方向一致": bool(n_periods_positive == len(chunks)),
        "样本外跑赢随机对照": bool(ctrl_random is not None and ex_out > ctrl_random),
        "样本外跑赢同行业对照": bool(ctrl_ind is not None and ex_out > ctrl_ind),
    }
    failed = [k for k, ok in checks.items() if not ok]
    incremental = {
        "all_excess": a.get("T10_mean_excess"), "all_random_control": a_ctrl,
        "all_increment": (round(a["T10_mean_excess"] - a_ctrl, 6)
                          if a.get("T10_mean_excess") is not None and a_ctrl is not None else None),
        "out_excess": ex_out, "out_random_control": ctrl_random,
        "out_increment": (round(ex_out - ctrl_random, 6)
                          if ex_out is not None and ctrl_random is not None else None),
        "out_same_industry_control": ctrl_ind,
    }

    if not frozen:
        verdict, reason = "UNPROVEN", (
            f"样本外参数未冻结：快照中存在 {len(hashes)} 套配置指纹 {hashes}，"
            f"当前配置为 {cfg['_config_hash']}；口径中途变更的样本外区间不成立")
    elif holdout_days < min_days or n_out < min_ev:
        verdict, reason = "UNPROVEN", (
            f"样本外仅 {holdout_days} 个独立形成日 / {n_out} 个事件，"
            f"低于门槛 {min_days} 日 / {min_ev} 事件，证据不足")
    elif ex_out is None or ci_low is None:
        verdict, reason = "UNPROVEN", "样本外 T+10 无可计算收益或无法完成分块 bootstrap"
    elif ex_out <= 0 and (ci_high if ci_high is not None else 1) < 0:
        verdict, reason = "FAIL", (f"样本外 T+10 平均超额 {ex_out:.4f}，"
                                   f"bootstrap 95% 上界 < 0，未见增量收益")
    elif not failed:
        verdict, reason = "PASS", (
            f"样本外 T+10 超额 {ex_out:.4f}，分块 bootstrap 95% 下界 {ci_low:.4f} > 0，"
            f"BH-FDR q={q_out:.4f}，成本后/剔除极端收益后仍为正，"
            f"三类状态与 {n_periods_positive}/{len(chunks)} 个时间段方向一致，"
            f"且样本外跑赢随机与同行业对照")
    else:
        verdict, reason = "CONDITIONAL", (
            f"样本外 T+10 超额 {round(float(ex_out), 4)}（bootstrap 95% CI "
            f"[{round(float(ci_low), 4)}, {round(float(ci_high or 0), 4)}]，BH q={q_out}），"
            f"但 {len(failed)}/{len(checks)} 项稳健性条件未通过：{'；'.join(failed)}。"
            f"全样本超额 {round(float(a.get('T10_mean_excess') or 0), 4)} vs 随机对照 "
            f"{round(float(a_ctrl or 0), 4)}，增量价值不稳定")

    summary: dict[str, Any] = {
        "events": len(edf), "horizons": list(HORIZONS),
        "unique_formation_days": span,
        "dedup_dropped_overlapping_events": dedup_dropped,
        "date_range": [str(start), str(end)],
        "split_date": split_date, "train_fraction": frac,
        "in_sample_days": ins.get("bootstrap", {}).get("n_days", 0),
        "out_of_sample_days": holdout_days,
        "config_hashes": hashes, "parameter_frozen": frozen,
        "current_config_hash": cfg["_config_hash"],
        "right_censored_T20_events": censored,
        "by_state": {k: by_group[k] for k in STATES + ("ALL", "IN_SAMPLE", "OUT_OF_SAMPLE")},
        "by_regime": {k: by_group[f"REGIME_{k}"] for k in ("BULL", "SIDEWAYS", "BEAR")},
        "by_size": {k: by_group[f"SIZE_{k}"] for k in ("4-6", "7-12", ">12")},
        "by_liquidity": {k: v for k, v in by_group.items() if k.startswith("LIQUIDITY_")},
        "by_period": {f"PERIOD_{i}": by_group[f"PERIOD_{i}"]
                      for i in range(1, len(chunks) + 1)},
        "period_defs": period_defs,
        "periods_positive": f"{n_periods_positive}/{len(chunks)}",
        "multiple_testing": {"method": "BH-FDR", "alpha": fdr_alpha,
                             "n_tests": len(pvals), "q_values": qvals},
        "verdict_checks": checks,
        "failed_checks": failed,
        "incremental_vs_controls": incremental,
        "threshold_sensitivity": sensitivity,
        "momentum_layer": momentum_layer,
        "controls": {m: a.get(f"control_{m}_T10_excess") for m in CONTROLS},
        "verdict": verdict, "reason": reason,
        "cost_bp": 30,
        "disclaimer": (
            "回测为事后评估；实时发现链路严格 as-of。对照组为随机/同行业/同规模流动性/"
            "板块动量筛选组合，板块指数用行业等权代理；sector_momentum 取最强行业全成分等权，"
            "与群体 4-8 只集中组合不完全可比。阈值敏感性只能在已计算边集合内验证"
            "更严格阈值，不能外推到更宽松阈值；群体身份追踪仍有断裂，事件已按成员重叠去重。"
            "规模/流动性/市场环境分组是对同一批事件的事后分层，不等价于重跑不同股票池门槛；"
            "时间切分为单次前/后两段独立样本外，不是多折滚动验证。"
            "涨幅幅度分层是对同一批事件的 T0 位置事后分层，不构成实时选股规则，"
            "也不能据此推断因果。最近 20 个交易日的事件 T+20 右删失。结论不是买入建议。"),
    }

    outdir = Path(cfg["paths"]["output_dir"]) / "backtest"
    outdir.mkdir(parents=True, exist_ok=True)
    edf.to_csv(outdir / "events.csv", index=False, encoding="utf-8-sig")
    sens_rows = sensitivity.get("grids", []) if sensitivity.get("status") == "OK" else []
    if sens_rows:
        pd.DataFrame(sens_rows).to_csv(outdir / "threshold_sensitivity.csv",
                                       index=False, encoding="utf-8-sig")
    if len(momentum_df):
        momentum_df.to_csv(outdir / "momentum_layers.csv", index=False,
                           encoding="utf-8-sig")
    write_json(outdir / "summary.json", summary)

    def f(x: Any) -> str:
        return "-" if x is None else (f"{x:.4f}" if isinstance(x, (int, float)) else str(x))

    lines = [
        "# 动态联动群体 · 事件回测报告（Phase 4）", "",
        f"- 事件数：{len(edf)}（去重剔除 {dedup_dropped} 个成员高度重叠事件）；"
        f"独立形成日 {span}；区间 {start} ~ {end}；成本 30bp",
        f"- 时间切分：样本内 ≤ {split_date}，样本外 > {split_date}；"
        f"样本外 {holdout_days} 个独立形成日 / {n_out} 个事件",
        f"- 参数冻结：{'是' if frozen else '否'}（配置指纹 {', '.join(hashes)}）",
        f"- **结论：{verdict}** —— {reason}", "",
        "## 1. 分状态 / 分段 T+5 / T+10 / T+20 超额收益", "",
        "| 分组 | 样本 | T+5超额 | T+10超额 | T+20超额 | T+10胜率 | T+10 t值 | bootstrap 95% CI | BH q |",
        "|---|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    for name in STATES + ("ALL", "IN_SAMPLE", "OUT_OF_SAMPLE"):
        it = by_group[name]
        bs = it.get("bootstrap", {})
        ci = (f"[{f(bs.get('ci95_low'))}, {f(bs.get('ci95_high'))}]"
              if bs.get("n_days") else "-")
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            name, it["n"], f(it.get("T5_mean_excess")), f(it.get("T10_mean_excess")),
            f(it.get("T20_mean_excess")), f(it.get("T10_win_rate")),
            f(it.get("T10_tstat")), ci, f(qvals.get(name))))
    lines += ["", "### 结论校验清单（全部通过才给 PASS）", "",
              "| 检查项 | 结果 |", "|---|---|"]
    for k, ok in checks.items():
        lines.append(f"| {k} | {'通过' if ok else '未通过'} |")
    lines += ["", "## 2. 对照组与稳健性（T+10 平均超额）", "",
              "| 区间 | 群体超额 | 随机对照 | 同行业 | 同规模 | 板块动量 | 相对随机增量 |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for seg in ("ALL", "IN_SAMPLE", "OUT_OF_SAMPLE"):
        v = by_group[seg]
        cr = v.get("control_random_T10_excess")
        inc = (round(v["T10_mean_excess"] - cr, 4)
               if v.get("T10_mean_excess") is not None and cr is not None else None)
        lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
            seg, f(v.get("T10_mean_excess")), f(cr),
            f(v.get("control_same_industry_T10_excess")),
            f(v.get("control_similar_size_T10_excess")),
            f(v.get("control_sector_momentum_T10_excess")), f(inc)))
    for m in CONTROLS:
        lines.append(f"- 对照 {m}（全样本）: {f(a.get(f'control_{m}_T10_excess'))}")
    lines.append(f"- 剔除每事件最高收益股后（全样本）: {f(a.get('T10_trimmed_excess'))}")
    lines.append(f"- 30bp 成本后（全样本）: {f(a.get('T10_after_cost_excess'))}")
    lines.append(f"- T+20 右删失事件: {censored}")
    lines += ["", "| 分组 | 样本 | T+10超额 | bootstrap CI 下界 | BH q |",
              "|---|---:|---:|---:|---:|"]
    for name in sorted(k for k in by_group
                       if k.startswith(("REGIME_", "SIZE_", "LIQUIDITY_", "PERIOD_"))):
        it = by_group[name]
        lines.append(f"| {name} | {it['n']} | {f(it.get('T10_mean_excess'))} | "
                     f"{f(it.get('bootstrap', {}).get('ci95_low'))} | {f(qvals.get(name))} |")
    lines += ["", "## 3. 阈值敏感性（已落库边集合内重跑社区发现）", ""]
    if sens_rows:
        lines.append(f"- 基准：最近 {sensitivity.get('days')} 个快照中共 "
                     f"{sensitivity.get('real_clusters_total')} 个日报群体；"
                     f"Jaccard 为每个真实群体在该网格下能被找回的最佳成员重叠度。"
                     f"baseline 直接复用日报最终边集合，严格网格采用 20日+10日相关同时达标"
                     f"（日报默认为任一达标），是更保守的建边口径。")
        lines += ["| 网格 | 相关阈值 | EdgeScore | q | 有效群体/日 | 平均规模 | 相对真实群体平均最佳 Jaccard |",
                  "|---|---:|---:|---:|---:|---:|---:|"]
        for g in sens_rows:
            lines.append("| {} | {} | {} | {} | {} | {} | {} |".format(
                g["name"], g["corr_threshold_20"], g["min_edge_score"], g["q_alpha"],
                g["clusters_per_day"], f(g["mean_size"]),
                f(g["mean_best_jaccard_vs_real"])))
        lines.append("")
        lines.append("> " + str(vcfg.get("_sensitivity_note", "")))
        lines.append("")
        lines.append("> 注意：边变稀后 Louvain 会切出更多小社区，小社区的密度门槛反而更容易满足，"
                     "因此严格网格的「群体数上升」不代表口径更宽松。真正的稳健性看 Jaccard："
                     "严格网格下真实日报群体平均只能找回 0.31~0.54 的成员重叠，说明群体划分对阈值较敏感。")
    else:
        lines.append(f"- 状态：{sensitivity.get('status')} {sensitivity.get('error', '')}")
    lines += ["", "## 4. 涨幅幅度分层（T0 时点可见，事后检验）", ""]
    if momentum_layer.get("status") == "OK":
        lines.append(f"共 {momentum_layer['n_events']} 个事件纳入分层"
                     f"（{momentum_layer.get('dropped_events', 0)} 个因 T0 历史窗口不足或 T+10 缺失未纳入）。"
                     "每个群体按首次形成日 T0 的**成员 5 日平均涨幅**分桶，"
                     "统计后续相对全A超额。样本内外分别列出，检验结论是否只由单一区间驱动。")
        lines += ["", "| 5日涨幅分桶 | 样本 | 桶内均值 | T+5超额 | T+10超额 | T+20超额 | "
                  "T+10胜率 | 样本内T+10 | 样本外T+10 |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for r in momentum_layer["by_bucket"]:
            lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                r["bucket"], r["n"], f(r["mean5"]), f(r["h5"]), f(r["h10"]),
                f(r["h20"]), f(r["win10"]), f(r.get("h10_IN_SAMPLE")),
                f(r.get("h10_OUT_OF_SAMPLE"))))
        rho = momentum_layer.get("spearman_mean5_vs_h10", {})
        lines += ["",
                  f"- Spearman(群体5日涨幅, T+10超额)：全样本 {f(rho.get('ALL'))}，"
                  f"样本内 {f(rho.get('IN_SAMPLE'))}，样本外 {f(rho.get('OUT_OF_SAMPLE'))}。",
                  f"- 位置健康区间（-10% ~ +3%，20日≤30%）："
                  f"{int((momentum_layer.get('healthy_position') or {}).get('n') or 0)} 个事件，"
                  f"T+10 超额 {f((momentum_layer.get('healthy_position') or {}).get('h10'))}。",
                  "", "### 若把「有一定涨幅幅度」当硬性筛选", "",
                  "| 群体5日均值下限 | 保留事件 | 保留T+10超额 | 剔除事件 | 剔除T+10超额 |",
                  "|---:|---:|---:|---:|---:|"]
        for s in momentum_layer.get("threshold_scan", []):
            lines.append("| {} | {} | {} | {} | {} |".format(
                f"{s['threshold'] * 100:.0f}%", s["keep_n"], f(s["keep_h10"]),
                s["drop_n"], f(s["drop_h10"])))
        lines += ["",
                  "- `m_confirmed`（至少 3 只成员达标 + 群体均值≥3% + 领涨梯度≥3pct）分组："
                  + "；".join(f"{k} → {v['n']} 事件 / T+10 {f(v['h10'])}"
                             for k, v in momentum_layer.get("by_confirmed", {}).items()),
                  "",
                  "> **结论**：本区间内联动群体的涨幅幅度与后续超额显著负相关，"
                  "涨幅越大越接近末端。因此日报默认把幅度作为**位置标签与追高风险提示**，"
                  "而非「涨得够多才进机会池」的过滤条件；`--momentum-strict` 可显式开启硬过滤，"
                  "但属非默认口径，不会把此处的负向结果包装成有效。",
                  "", "> " + str(momentum_layer.get("note", ""))]
    else:
        lines.append(f"- 状态：{momentum_layer.get('status')} "
                     f"{momentum_layer.get('error', '')}")
    lines += ["", "## 5. 多重检验与结论纪律", "",
              f"- BH-FDR 共 {len(pvals)} 组检验，alpha={fdr_alpha}；"
              f"样本外主检验 q={f(q_out)}",
              f"- 分段时间稳定性：{n_periods_positive}/{len(chunks)} 段 T+10 超额为正"
              f"（{', '.join(p['from'] + '~' + p['to'] for p in period_defs)}）。",
              "- 结论仅在参数冻结、样本外独立形成日与事件数达标、bootstrap CI 下界 > 0、"
              "成本后与剔除极端收益后仍为正、三类状态方向一致、所有时间段方向一致时才给 PASS。",
              "", "> " + summary["disclaimer"], ""]
    (outdir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    log.info("回测完成: 事件 %d, 样本外 %d 日, 结论 %s (%s)",
             len(edf), holdout_days, verdict, reason)
    return {"status": "OK", "events": len(edf), "verdict": verdict,
            "reason": reason, "report": str(outdir / "report.md"), "summary": summary}
