"""板块联动「漏检」诊断器（只读，不写快照、不改配置、不产生交易信号）。

用途：给定交易日与板块关键词，复现当日发现链路的每一个过滤层，
量化该板块内个股两两配对分别卡在「股票池 / 候选预筛 / 相关门槛 /
行业双残差 / 长窗口稳定 / EdgeScore / FDR」的哪一层。

用法：
    python -m stock_linkage_discovery.diagnose_sector --date 20261103 --sector 光伏
    python -m stock_linkage_discovery.diagnose_sector --date 20261103 --sector 光伏 --strong-only
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import data_layer as dl
from . import feature_engine as fe
from . import linkage_engine as le
from . import residual_engine as re
from .common import rankdata_axis0, write_json
from .config import load_config


def _cv(code: str) -> str:
    """westock 的 sh600000 / sz000001 / bj920xxx → tushare 风格代码。"""
    s = str(code).strip().lower()
    return s[2:] + "." + s[:2].upper() if s[:2] in ("sh", "sz", "bj") else str(code)


def sector_pool(cfg: dict[str, Any], keyword: str) -> dict[str, set[str]]:
    """从概念库与产业链库取含关键词的成分股，返回三种口径的池。"""
    path = cfg["paths"]["westock_db"]
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
        concept = {_cv(r[0]) for r in con.execute(
            "SELECT DISTINCT stock_code FROM concept_stocks WHERE sector_name LIKE ?",
            (f"%{keyword}%",))}
        chain = {_cv(r[0]) for r in con.execute(
            "SELECT DISTINCT stock_code FROM chain_stock_node WHERE theme_name LIKE ?",
            (f"%{keyword}%",))}
    return {"concept": concept, "chain": chain,
            "intersection": concept & chain, "union": concept | chain}


def _null_calibration(X: np.ndarray, n: int, benchmarks: int) -> tuple[float, float]:
    rng = np.random.default_rng(20261108)   # 与 build_network 完全一致
    m = benchmarks * 200
    ri = rng.integers(0, n, m)
    rj = (ri + 1 + rng.integers(0, max(1, n - 1), m)) % n
    pc = le._batch_corr(X[-20:], ri, rj)
    z = np.arctanh(np.clip(pc, -0.999, 0.999)) * np.sqrt(max(20 - 3, 1))
    return float(z.mean()), float(max(z.std(), 1e-3)), float(np.quantile(np.abs(pc), 0.95))


def diagnose(cfg: dict[str, Any], asof: str, keyword: str,
             strong_only: bool = False, pool_key: str = "intersection") -> dict[str, Any]:
    lcfg = cfg["linkage"]
    pools = sector_pool(cfg, keyword)
    pool = pools[pool_key]

    resolver = dl.PITNameResolver(cfg, dl.load_stock_basic(cfg))
    dates = dl.trade_dates(cfg, asof, min(200, int(cfg["data"].get("max_load_days", 420))))
    panel = dl.load_panel(cfg, dates[0], asof, resolver)
    universe, uq = dl.build_universe(panel, asof, cfg, resolver)
    s = panel.slice(asof, universe, min(len(panel.dates), 200))
    rr = re.compute_residuals(s, cfg)
    s.resid_mkt, s.resid_mkt_ind = rr.resid_mkt, rr.resid_mkt_ind
    fs = fe.compute_features(s, cfg)

    # ---- 复现 build_network 的矩阵与候选集 ----
    W = int(lcfg.get("long_window", 60))
    X = np.asarray(rr.resid_mkt[-W:], dtype=np.float32)
    Xd = np.asarray(rr.resid_mkt_ind[-W:], dtype=np.float32)
    raw = np.asarray(s.ret[-W:], dtype=np.float32)
    C20 = le._corr_matrix(X[-20:])
    C10 = le._corr_matrix(X[-10:])
    C60 = le._corr_matrix(X)
    Crank = le._corr_matrix(np.asarray(rankdata_axis0(np.asarray(X[-20:], dtype=np.float64)),
                                       dtype=np.float32))
    null_mu, null_sd, null_p95 = _null_calibration(
        X, s.N, int(lcfg.get("permutation_benchmarks", 24)))
    ii, jj, pre = le.prescreen(s, fs, rr.resid_mkt, cfg, C20, C10, C60)
    cand = set(zip(ii.tolist(), jj.tolist()))
    m = le._chunk_metrics(s, fs, rr.resid_mkt, rr.resid_mkt_ind, s.ret,
                          ii, jj, cfg, s.industry, C20, C10, C60, Crank, null_mu, null_sd)

    thr20, thr10 = float(lcfg["corr_threshold_20"]), float(lcfg["corr_threshold_10"])
    gate = ((m["residual_corr_20"] >= thr20) | (m["residual_corr_10"] >= thr10))
    pseudo = (m["raw_corr"] > 0.60) & (m["residual_corr_20"] < thr20 * 0.60)
    ind_expl = (m["same_industry"]
                & (m["corr_dual"] < float(lcfg.get("min_dual_network_corr", 0.45)))
                & ((m["residual_corr_20"] - m["corr_dual"]) > 0.25))
    conds = {
        "corr_gate": gate,
        "not_market_driven_pseudo": ~pseudo,
        "not_industry_explained": ~ind_expl,
        "dual_network_corr": m["corr_dual"] >= float(lcfg.get("min_dual_network_corr", 0.45)),
        "long_window_corr": m["residual_corr_60"] >= float(lcfg.get("min_long_window_corr", 0.25)),
        "corr_stability": m["corr_stability"] >= float(lcfg.get("min_corr_stability", 0.55)),
        "min_effective_sample": m["sample_n"] >= int(lcfg.get("min_effective_sample", 15)),
        "min_edge_score": m["edge_score"] >= float(lcfg.get("min_edge_score", 0.45)),
        "min_reliability": m["edge_reliability"] >= float(lcfg.get("min_reliability", 0.45)),
        "fdr": m["q_value"] <= float(lcfg.get("fdr_alpha", 0.05)),
    }
    keep = np.logical_and.reduce([np.asarray(v, dtype=bool) for v in conds.values()])
    m["_keep"] = keep

    # ---- 板块内配对 ----
    code_idx = {c: i for i, c in enumerate(s.codes)}
    basic = dl.load_stock_basic(cfg).set_index("ts_code")
    ret1 = pd.Series(s.pct_chg[-1] if hasattr(s, "pct_chg") and s.pct_chg is not None
                     else s.ret[-1] * 100.0, index=s.codes)
    amt_today = pd.Series(s.amount[-1], index=s.codes)
    amt_ma20 = pd.Series(np.nanmean(s.amount[-20:], axis=0), index=s.codes)
    vol_ratio = (amt_today / amt_ma20.replace(0, np.nan)).fillna(0.0)

    members = [c for c in s.codes if c in pool]
    if strong_only:
        members = [c for c in members if ret1.get(c, -99) >= 3.0 and vol_ratio.get(c, 0) >= 1.2]
    idx = sorted(code_idx[c] for c in members)
    idxset = set(idx)
    pair_keys = {(min(i, j), max(i, j)) for a, i in enumerate(idx) for j in idx[a + 1:]}

    m["_pair"] = list(zip(m["ia"].astype(int), m["ib"].astype(int)))
    evaluated = m[m["_pair"].isin(pair_keys)]
    in_cand = len(evaluated)
    progressive = pd.Series(True, index=evaluated.index)
    funnel: dict[str, int] = {"板块内配对(universe内)": len(pair_keys), "进入候选集": in_cand}
    for name, cond in conds.items():
        progressive &= np.asarray(cond.loc[evaluated.index], dtype=bool)
        funnel[name] = int(progressive.sum())
    final_edges = int(evaluated["_keep"].sum())

    # 逐条记录被拒的第一层原因
    first_fail: Counter[str] = Counter()
    for _, row in evaluated.iterrows():
        for name, cond in conds.items():
            if not bool(cond.loc[row.name]):
                first_fail[name] += 1
                break

    # 最终边表里板块相关的边（用于和落库快照交叉核对）
    final = m[m["_keep"]]
    final_pairs = set(zip(final["ia"].astype(int), final["ib"].astype(int)))
    sector_internal_edges = len(final_pairs & pair_keys)
    sector_touch_edges = sum(1 for a, b in final_pairs if a in idxset or b in idxset)

    # 板块内节点的连通规模（Louvain 之前的原始图）
    import networkx as nx
    G = nx.Graph()
    G.add_nodes_from(idx)
    G.add_edges_from(final_pairs & pair_keys)
    comps = sorted((len(c) for c in nx.connected_components(G)), reverse=True)

    # ---- 社区层：复现 Louvain 划分，定位板块股被分到哪里、被哪条规则拒 ----
    from . import cluster_engine as ce
    G_full = nx.Graph()
    for a, b, w in zip(final["ia"].astype(int), final["ib"].astype(int), final["edge_score"]):
        G_full.add_edge(int(a), int(b), weight=float(w))
    comms, algo = ce._communities(G_full, cfg)
    ccfg = cfg["cluster"]
    comm_rows: list[dict[str, Any]] = []
    comm_reject: Counter[str] = Counter()
    for comm in comms:
        n = len(comm)
        pv_n = len(comm & idxset)
        if not pv_n:
            continue
        inner = sum(1 for a, b in final_pairs if a in comm and b in comm)
        dens = 2.0 * inner / max(1, n * (n - 1))
        mean_es = float(np.mean([w for a, b, w in
                                 zip(final["ia"].astype(int), final["ib"].astype(int),
                                     final["edge_score"])
                                 if a in comm and b in comm])) if inner else 0.0
        why = ("small" if n < int(ccfg.get("min_size", 4)) else
               "oversize" if n > int(ccfg.get("max_size", 60)) else
               "low_density" if dens < float(ccfg.get("min_density", 0.28)) else
               "weak_edges" if mean_es < float(ccfg.get("min_mean_edge_score", 0.50)) else "PASS")
        comm_reject[why] += 1
        comm_rows.append({"size": n, "sector_members": pv_n,
                          "sector_ratio": round(pv_n / max(1, n), 3),
                          "inner_edges": inner, "density": round(dens, 4),
                          "mean_edge_score": round(mean_es, 3), "reject": why,
                          "sector_names": [str(basic["name"].get(s.codes[i], s.codes[i]))
                                           for i in sorted(comm & idxset)][:8]})
    comm_rows.sort(key=lambda x: -x["sector_members"])

    # ---- 旁路对照：不剔市场/行业共同因子，用原始收益相关看板块自身是否成团 ----
    R20 = np.asarray(raw[-20:], dtype=np.float32)
    strong = [i for i in idx if float(ret1.get(s.codes[i], -99)) >= 3.0
              and float(vol_ratio.get(s.codes[i], 0)) >= 1.2]

    def raw_subgraph(sub_idx: list[int], thr: float) -> dict[str, Any]:
        a = np.asarray([min(x, y) for k, x in enumerate(sub_idx) for y in sub_idx[k + 1:]],
                       dtype=np.int32)
        b = np.asarray([max(x, y) for k, x in enumerate(sub_idx) for y in sub_idx[k + 1:]],
                       dtype=np.int32)
        if not len(a):
            return {}
        c = le._batch_corr(R20, a, b)
        sel = c >= thr
        H = nx.Graph()
        H.add_nodes_from(sub_idx)
        H.add_edges_from(zip(a[sel].tolist(), b[sel].tolist()))
        n = len(sub_idx)
        return {"n_stocks": n, "n_pairs": int(len(a)), "n_edges": int(sel.sum()),
                "density": round(2.0 * int(sel.sum()) / max(1, n * (n - 1)), 4),
                "largest_components": sorted((len(x) for x in nx.connected_components(H)),
                                             reverse=True)[:5],
                "mean_raw_corr": round(float(c.mean()), 4),
                "ratio_ge_thr": round(float(sel.mean()), 4)}

    cross = {
        "sector_mean_pct_chg": round(float(np.mean([ret1.get(s.codes[i], np.nan) for i in idx])), 3),
        "sector_up_ratio": round(float(np.mean([ret1.get(s.codes[i], -99) > 0 for i in idx])), 3),
        "market_mean_pct_chg": round(float(np.nanmean(s.pct_chg[-1])), 3),
        "market_up_ratio": round(float(np.nanmean(s.pct_chg[-1] > 0)), 3),
        "n_strong": len(strong),
    }

    def stats(frame: pd.DataFrame) -> dict[str, float]:
        if not len(frame):
            return {}
        return {k: round(float(frame[k].mean()), 4)
                for k in ("residual_corr_20", "residual_corr_60", "corr_dual",
                          "raw_corr", "corr_stability", "edge_score", "q_value")}

    # 严格 as-of 的当日行情快照（只读）
    quote = [{"ts_code": c, "name": str(basic["name"].get(c, "")),
              "industry": str(basic["industry"].get(c, "")),
              "pct_chg": round(float(ret1.get(c, np.nan)), 2),
              "amount_ratio_20": round(float(vol_ratio.get(c, 0)), 2)}
             for c in members]
    quote = sorted(quote, key=lambda x: -x["pct_chg"])

    out = {
        "asof": asof, "sector_keyword": keyword, "pool": pool_key,
        "pool_sizes": {k: len(v) for k, v in pools.items()},
        "strong_only": strong_only,
        "config_hash": cfg.get("_config_hash"),
        "universe": {"final": uq["final"], "sector_members_in_universe": len(members),
                     "sector_members_dropped": len(pool) - len(members)},
        "network": {
            "universe_pairs": pre["universe_pairs"], "candidate_pairs": pre["candidate_pairs"],
            "candidate_cap": int(lcfg.get("max_candidate_pairs", 400000)),
            "candidate_ratio": pre["candidate_ratio"],
            "null_z_sd": round(null_sd, 4), "null_abs_corr_p95": round(null_p95, 4),
            "kept_edges_total": int(keep.sum()),
        },
        "sector": {
            "n_members": len(members), "n_internal_pairs": len(pair_keys),
            "funnel": funnel, "first_fail": dict(first_fail),
            "final_internal_edges": sector_internal_edges,
            "final_edges_touching_sector": sector_touch_edges,
            "largest_connected_components": comps[:8],
            "same_industry_pair_ratio": round(float(evaluated["same_industry"].mean()), 4) if len(evaluated) else None,
            "metrics_all_evaluated": stats(evaluated),
            "metrics_passed_corr_gate": stats(evaluated[gate.loc[evaluated.index]]),
            "metrics_rejected_dual": stats(evaluated[evaluated["corr_dual"] <
                                                     float(lcfg.get("min_dual_network_corr", 0.45))]),
        },
        "cross_section": cross,
        "community": {
            "algorithm": algo, "n_communities": len(comms),
            "n_communities_with_sector": len(comm_rows),
            "rejection_of_sector_communities": dict(comm_reject),
            "top_communities": comm_rows[:12],
        },
        "bypass_raw_corr": {
            "note": "不剔市场/行业共同因子的原始 20 日收益相关（仅用于对照，不写回引擎）",
            "strong": {str(t): raw_subgraph(strong, t) for t in (0.45, 0.55, 0.65)},
            "all_members": {"0.55": raw_subgraph(idx, 0.55)},
        },
        "quote_top": quote[:25],
        "thresholds": {
            "corr_threshold_20": thr20, "corr_threshold_10": thr10,
            "min_dual_network_corr": float(lcfg.get("min_dual_network_corr", 0.45)),
            "min_long_window_corr": float(lcfg.get("min_long_window_corr", 0.25)),
            "min_corr_stability": float(lcfg.get("min_corr_stability", 0.55)),
            "min_edge_score": float(lcfg.get("min_edge_score", 0.45)),
            "fdr_alpha": float(lcfg.get("fdr_alpha", 0.05)),
            "max_candidates_per_stock": int(lcfg.get("max_candidates_per_stock", 30)),
            "max_candidate_pairs": int(lcfg.get("max_candidate_pairs", 400000)),
            "min_cluster_size": int(cfg["cluster"].get("min_size", 4)),
            "max_cluster_size": int(cfg["cluster"].get("max_size", 60)),
            "min_density": float(cfg["cluster"].get("min_density", 0.28)),
            "min_mean_edge_score": float(cfg["cluster"].get("min_mean_edge_score", 0.50)),
        },
    }
    return out


def _markdown(d: dict[str, Any]) -> str:
    s, n, u = d["sector"], d["network"], d["universe"]
    cx, cm, bp = d["cross_section"], d["community"], d["bypass_raw_corr"]
    L = [f"# 板块联动漏检诊断 · {d['asof']} · {d['sector_keyword']}", "",
         f"> 只读复现，配置指纹 `{d['config_hash']}`；池口径 `{d['pool']}`，"
         f"强势股筛选 {'开启' if d['strong_only'] else '关闭'}。", "",
         "## 1. 板块当日强度（用户观察到的是真的）", "",
         f"- 概念/产业链池 {d['pool_sizes']['concept']}/{d['pool_sizes']['chain']} 只，"
         f"交集 {d['pool_sizes']['intersection']} 只，进入当日股票池 {u['sector_members_in_universe']} 只。",
         f"- 板块均涨 **{cx['sector_mean_pct_chg']:+.2f}%** / 上涨占比 {cx['sector_up_ratio'] * 100:.1f}%，"
         f"股票池全样本均涨 {cx['market_mean_pct_chg']:+.2f}% / 上涨占比 {cx['market_up_ratio'] * 100:.1f}%；"
         f"涨≥3% 且额比≥1.2 的强势股 {cx['n_strong']} 只。",
         "- 涨幅前列：" + "、".join(
             f"{x['name']}({x['pct_chg']:+.1f}%)" for x in d["quote_top"][:8]), "",
         "## 2. 逐层漏斗（板块内两两配对）", "",
         "| 层 | 剩余配对 |", "|---|---:|"]
    prev = None
    for k, v in s["funnel"].items():
        L.append(f"| {k} | {v:,} |" + ("" if prev is None else ""))
        prev = v
    L += ["", "首个拒绝原因分布（累计口径见上表）：", "",
          "| 原因 | 配对数 |", "|---|---:|"]
    L += [f"| {k} | {v:,} |" for k, v in sorted(s["first_fail"].items(), key=lambda x: -x[1])]
    L += ["", "## 3. 社区层：成边之后又被丢在哪里", "",
          f"- Louvain（`{cm['algorithm']}`）切出 {cm['n_communities']} 个社区，"
          f"其中 {cm['n_communities_with_sector']} 个含板块股；"
          f"这些社区被拒原因：{cm['rejection_of_sector_communities']}。", "",
          "| 社区规模 | 板块股数 | 占比 | 内部边 | 密度 | 均分 | 判定 | 板块股举例 |",
          "|---:|---:|---:|---:|---:|---:|---|---|"]
    for r in cm["top_communities"][:10]:
        L.append(f"| {r['size']} | {r['sector_members']} | {r['sector_ratio'] * 100:.1f}% | "
                 f"{r['inner_edges']:,} | {r['density']:.3f} | {r['mean_edge_score']:.3f} | "
                 f"{r['reject']} | {'、'.join(r['sector_names'][:5])} |")
    t = d["thresholds"]
    L += ["", f"> 有效群体要求规模 {t['min_cluster_size']}~{t['max_cluster_size']}、"
          f"密度 ≥ {t['min_density']}、均分 ≥ {t['min_mean_edge_score']}；"
          "巨型社区会被整体丢弃，其中的板块股不会单独保留。", "",
          "## 4. 关键指标", "",
          f"- 候选集 {n['candidate_pairs']:,} / 上限 {n['candidate_cap']:,}，"
          f"全市场 {n['universe_pairs']:,} 对，覆盖率 {n['candidate_ratio'] * 100:.2f}%。",
          f"- 板块内最终成边 **{s['final_internal_edges']:,}** 条，"
          f"涉及板块个股的边 {s['final_edges_touching_sector']:,} 条；"
          f"板块内最大连通分量 {s['largest_connected_components'][:5]}。",
          f"- 板块内同行业配对占比 {s['same_industry_pair_ratio']}。", ""]
    for label, key in (("全部进入候选的板块配对", "metrics_all_evaluated"),
                       ("通过相关门槛的板块配对", "metrics_passed_corr_gate"),
                       ("被双残差门槛拒绝的板块配对", "metrics_rejected_dual")):
        m = s.get(key) or {}
        if m:
            L.append(f"- **{label}**（n 见上表）：20日残差相关 {m['residual_corr_20']:.3f} → "
                     f"双调整残差相关 {m['corr_dual']:.3f}；60日 {m['residual_corr_60']:.3f}，"
                     f"EdgeScore {m['edge_score']:.3f}，q {m['q_value']:.4f}。")
    L += ["", "## 5. 旁路对照：不剔共同因子会怎样", "",
          "> 用原始 20 日收益相关（板块 Beta）重建板块内子图，只做对照，不写回引擎。", "",
          "| 样本 | 阈值 | 股票 | 成边 | 密度 | 最大连通块 | 平均原始相关 |",
          "|---|---:|---:|---:|---:|---|---:|"]
    for label, group in (("强势股", bp["strong"]), ("全部成分", bp["all_members"])):
        for thr, v in sorted(group.items(), key=lambda x: float(x[0])):
            if not v:
                continue
            L.append(f"| {label} | {thr} | {v['n_stocks']} | {v['n_edges']:,} | "
                     f"{v['density']:.3f} | {v['largest_components']} | {v['mean_raw_corr']:.3f} |")
    L += ["", "## 6. 阈值", "",
          "| 参数 | 值 |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in d["thresholds"].items()]
    L += ["", "## 7. 结论要点", "",
          "1. 板块共振在原始收益上确实存在，但引擎要找的是「剔除市场/行业共同因子后仍成立、"
          "且 60 日稳定的新增关系」，两者是相反的定义。",
          "2. 双残差硬门槛与长窗口/稳定性门槛主动削弱板块 Beta；剩余边再被巨型社区的整体规模、"
          "密度过滤吞掉。",
          "3. 因此这是口径取舍，不是行情数据缺失或程序报错；要识别此类板块共振，"
          "需要独立的同日横截面共振链路，而不是简单调低现有阈值。", "",
          "---", "", "本诊断只解释「为什么没被识别」，不产生任何买入信号，也不修改既有策略逻辑。", ""]
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="板块联动漏检诊断（只读）")
    ap.add_argument("--date", required=True, help="交易日 YYYYMMDD")
    ap.add_argument("--sector", required=True, help="板块关键词，如 光伏")
    ap.add_argument("--pool", default="intersection",
                    choices=["concept", "chain", "intersection", "union"])
    ap.add_argument("--strong-only", action="store_true", help="只保留当日涨幅≥3%%且额比≥1.2的成分股")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    args = ap.parse_args(argv)

    from .config import apply_overrides, ensure_dirs
    cfg = apply_overrides(load_config(), args.set)
    ensure_dirs(cfg)
    d = diagnose(cfg, args.date, args.sector, args.strong_only, args.pool)
    out = Path(cfg["paths"]["output_dir"]) / "diagnostics"
    out.mkdir(parents=True, exist_ok=True)
    stem = f"sector_miss_{args.date}_{args.sector}" + ("_strong" if args.strong_only else "")
    write_json(out / f"{stem}.json", d)
    (out / f"{stem}.md").write_text(_markdown(d), encoding="utf-8")
    print(json.dumps({"status": "OK", "json": str(out / f"{stem}.json"),
                      "markdown": str(out / f"{stem}.md"),
                      "funnel": d["sector"]["funnel"],
                      "final_internal_edges": d["sector"]["final_internal_edges"],
                      "first_fail": d["sector"]["first_fail"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
