"""Emerging Cluster Engine + Cluster Evolution Engine（第四、六、七层）。

- 图社区发现：Louvain（networkx 内置），检测到 igraph/leidenalg 时自动升级 Leiden。
- 时间连续性：与昨日/5 日前/10 日前群体做成员+边重叠匹配，避免同一群体每天改名。
- 三类早期机会：EMERGING_CLUSTER / LEADER_EXPANSION / CLUSTER_STRENGTHENING。
- 角色：LEADER / CORE / FOLLOWER / LAGGARD / WEAK（依据历史，不按当前涨幅排名）。
- LinkageScore 只表示关联强度，不代表预期收益，更不是买入信号。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd

from .common import clip01, get_logger
from .data_layer import Slice
from .feature_engine import FeatureSet
from .linkage_engine import NetworkResult

log = get_logger("cluster_engine")

STATES = ("EMERGING_CLUSTER", "LEADER_EXPANSION", "CLUSTER_STRENGTHENING",
          "EXISTING_CLUSTER", "DECAYING_CLUSTER", "OVERHEATED_CLUSTER",
          "BASELINE_FIRST_SNAPSHOT")
LIFECYCLE = ("BIRTH", "EXPANSION", "MERGE", "SPLIT", "STRENGTHEN", "DECAY", "TERMINATION")


@dataclass
class History:
    """由 snapshot_store 构造的历史快照上下文。"""
    available: bool = False
    prev_clusters: pd.DataFrame | None = None
    prev_edges: pd.DataFrame | None = None
    edges_5d: pd.DataFrame | None = None
    edges_20d: pd.DataFrame | None = None
    clusters_5d: pd.DataFrame | None = None
    clusters_10d: pd.DataFrame | None = None
    age: dict[str, int] = field(default_factory=dict)
    cluster_dates: dict[str, str] = field(default_factory=dict)
    # 时间平滑图所需的历史边（decay, DataFrame），严格早于 asof
    smooth_edges: list[tuple[float, pd.DataFrame]] = field(default_factory=list)
    # 各历史群体 id 对应的关联边集合，用于边重叠身份匹配
    cluster_edges: dict[str, set[tuple[str, str]]] = field(default_factory=dict)
    prev_edge_pairs: set[tuple[str, str]] = field(default_factory=set)
    # 群体稳定核心成员：在其存续快照中出现频率达到阈值的股票，作为身份锚点
    core_members: dict[str, set[str]] = field(default_factory=dict)


@dataclass
class ClusterSet:
    asof: str
    clusters: list[dict[str, Any]] = field(default_factory=list)
    members: pd.DataFrame = field(default_factory=pd.DataFrame)
    events: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    flags: dict[str, Any] = field(default_factory=dict)


def _communities(G: nx.Graph, cfg: dict[str, Any]) -> tuple[list[set[str]], str]:
    ccfg = cfg["cluster"]
    algo = str(ccfg.get("algorithm", "louvain")).lower()
    active = nx.Graph()
    active.add_edges_from((u, v, d) for u, v, d in G.edges(data=True) if d.get("weight", 0) > 0)
    if active.number_of_nodes() == 0:
        return [], "none"
    if algo == "leiden":
        try:
            import igraph as ig  # noqa
            import leidenalg as la  # noqa
            nodes = list(active.nodes())
            ix = {n: i for i, n in enumerate(nodes)}
            g = ig.Graph(n=len(nodes), edges=[(ix[u], ix[v]) for u, v in active.edges()], directed=False)
            g.es["weight"] = [float(d.get("weight", 1.0)) for _, _, d in active.edges(data=True)]
            part = la.find_partition(g, la.RBConfigurationVertexPartition,
                                     weights=g.es["weight"],
                                     resolution_parameter=float(ccfg.get("resolution", 1.0)))
            return [{nodes[i] for i in c} for c in part], "leiden"
        except Exception as ex:  # pragma: no cover - 依赖缺失时自动降级
            log.warning("Leiden 不可用(%s)，降级 Louvain", ex)
    comm = nx.algorithms.community.louvain_communities(
        active, weight="weight", resolution=float(ccfg.get("resolution", 1.0)),
        seed=int(ccfg.get("seed", 42)))
    return [set(c) for c in comm], "louvain"


def _overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / max(1, len(a | b))


def _pairset(edges: pd.DataFrame | None, members: set[str] | None = None) -> set[tuple[str, str]]:
    if edges is None or not len(edges):
        return set()
    out: set[tuple[str, str]] = set()
    for a, b in zip(edges["a"].astype(str), edges["b"].astype(str)):
        if members is not None and (a not in members or b not in members):
            continue
        out.add((a, b) if a < b else (b, a))
        out.add((b, a) if a < b else (a, b))
    return out


def _match_identity(members: set[str], cur_pairs: set[tuple[str, str]],
                    history: History, cfg: dict[str, Any]) -> tuple[str | None, list[str]]:
    """与昨日/5日前/10日前群体匹配身份，避免同一群体每天被重新命名。

    相似度同时考虑：成员 Jaccard、跨日稳定核心成员覆盖率、关联边 Jaccard。
    成员因市场轮动部分换血时，关联边重叠往往是更稳定的身份锚点。
    """
    from collections import defaultdict
    ccfg = cfg["cluster"]
    thr = float(ccfg.get("identity_overlap", 0.30))
    w = ccfg.get("identity_weights", {"member": 0.55, "core": 0.20, "edge": 0.25})
    by_cid: dict[str, list[set[str]]] = defaultdict(list)
    for frame in (history.prev_clusters, history.clusters_5d, history.clusters_10d):
        if frame is None or not len(frame):
            continue
        for cid, grp in frame.groupby("cluster_id"):
            by_cid[str(cid)].append(set(grp["ts_code"].astype(str)))
    hits: dict[str, float] = {}
    for cid, sets in by_cid.items():
        old = set().union(*sets)
        core = set.intersection(*sets) if len(sets) >= 2 else old
        jac = _overlap(members, old)
        cov = len(members & core) / max(1, len(core))
        old_pairs = history.cluster_edges.get(cid, set())
        ejac = (len(cur_pairs & old_pairs) / max(1, len(cur_pairs | old_pairs))
                if old_pairs else 0.0)
        score = (float(w.get("member", 0.55)) * jac
                 + float(w.get("core", 0.20)) * cov
                 + float(w.get("edge", 0.25)) * ejac)
        # 稳定核心锚点：群体轮动时成员可能大面积换血，但存续期高频出现的
        # 核心股通常保留。共享 >=2 只核心股即可确认身份，避免每天 BIRTH。
        anchor = history.core_members.get(cid, set())
        n_anchor = len(members & anchor)
        min_anchor = int(ccfg.get("identity_core_anchor", 2))
        if n_anchor >= min_anchor and (jac > 0.10 or ejac > 0.10):
            score = max(score, thr + 0.05 * min(n_anchor, 4) + 0.20 * jac)
        if score >= thr:
            hits[cid] = max(hits.get(cid, 0.0), score)
    if not hits:
        return None, []
    ordered = sorted(hits.items(), key=lambda kv: -kv[1])
    return ordered[0][0], [k for k, _ in ordered[1:]]


def _first_event_day(fs: FeatureSet, j: int, types: tuple[str, ...]) -> int:
    best = 10 ** 6
    for et in types:
        E = fs.events.get(et)
        if E is None:
            continue
        col = E[:, j]
        nz = np.flatnonzero(col)
        if len(nz):
            best = min(best, int(nz[0]))
    return best


def _roles(members: list[str], s: Slice, fs: FeatureSet, net: NetworkResult,
           sub_edges: pd.DataFrame) -> dict[str, str]:
    idx = s.code_index()
    js = [idx[c] for c in members]
    lead_count = {c: 0 for c in members}
    if len(sub_edges):
        for r in sub_edges.itertuples(index=False):
            lb = int(getattr(r, "lead_lag_best", 0))
            if lb > 0:
                lead_count[r.a] = lead_count.get(r.a, 0) + 1
            elif lb < 0:
                lead_count[r.b] = lead_count.get(r.b, 0) + 1
    deg = {c: 0.0 for c in members}
    if len(sub_edges):
        for r in sub_edges.itertuples(index=False):
            w = float(r.edge_score)
            deg[r.a] += w
            deg[r.b] += w
    score = {}
    for c, j in zip(members, js):
        first = _first_event_day(fs, j, ("BREAKOUT", "VOL_UP", "RELATIVE_STRENGTH"))
        early = 1.0 if first < 10 ** 5 else 0.0
        early_score = (1.0 - first / max(1, fs.s.T)) * early
        resid5 = float(fs.l("resid_cum_5")[j])
        resid20 = float(fs.l("resid_cum_20")[j])
        trend = float(fs.l("ma_stack")[j])
        amt_ratio = float(fs.l("amount_ratio")[j])
        ret5 = float(fs.l("ret_5")[j])
        breakout = float(fs.l("vol_breakout_count_20")[j])
        degn = deg[c] / max(1.0, len(members) - 1)
        score[c] = {
            "leader": 0.35 * early_score + 0.22 * clip01(lead_count[c] / 3)
                       + 0.18 * clip01(resid5 * 8) + 0.15 * trend + 0.10 * clip01(breakout / 3),
            "core": 0.45 * clip01(degn) + 0.25 * clip01(resid20 * 5)
                    + 0.20 * trend + 0.10 * clip01(fs.l("pullback_success_20")[j] / 3),
            "follower": 0.30 * clip01(degn) + 0.25 * clip01(fs.l("rs5_change")[j] * 6)
                        + 0.20 * trend + 0.15 * clip01(1 - abs(resid5) * 5)
                        + 0.10 * clip01(1 - early_score),
            "laggard": 0.35 * clip01(degn) + 0.25 * clip01(1 - ret5 * 4)
                       + 0.20 * clip01(fs.l("bottom_rising_count_20")[j] / 3)
                       + 0.20 * clip01(1 - breakout / 3),
            "weak": 0.40 * clip01(-resid5 * 8) + 0.25 * (1 - trend)
                    + 0.20 * clip01(-fs.l("rs5_change")[j] * 6)
                    + 0.15 * clip01(1 - amt_ratio / 2),
        }
    # 先定领涨与核心（依据历史早启动 + 组内关联度），其余按最优角色分配
    roles = {c: "FOLLOWER" for c in members}
    ranked_leader = sorted(members, key=lambda c: -score[c]["leader"])
    ranked_core = sorted(members, key=lambda c: -score[c]["core"])
    if ranked_leader:
        roles[ranked_leader[0]] = "LEADER"
    for c in ranked_core:
        if roles[c] != "LEADER":
            roles[c] = "CORE"
            break
    for c in members:
        if roles[c] in ("LEADER", "CORE"):
            continue
        opts = {k: score[c][k] for k in ("follower", "laggard", "weak")}
        best = max(opts, key=opts.get)
        roles[c] = {"follower": "FOLLOWER", "laggard": "LAGGARD", "weak": "WEAK"}[best]
    # 若弱势得分不占绝对优势，不轻易判 WEAK
    for c in members:
        if roles[c] == "WEAK" and score[c]["weak"] < 0.55:
            roles[c] = "FOLLOWER"
    return roles


def _driver_weight(conf: str) -> float:
    return {"HIGH": 1.0, "MEDIUM": 0.60, "LOW": 0.25, "UNKNOWN": 0.10}.get(str(conf).upper(), 0.10)


def linkage_score(m: dict[str, Any], cfg: dict[str, Any], driver_conf: str = "UNKNOWN") -> float:
    w = cfg["scoring"]["weights"]
    parts = {
        "residual_sync": clip01(m["mean_resid_corr"] / 0.75),
        "volume_price_sync": clip01(0.5 * m["mean_event_sync"] + 0.5 * m["mean_volume_sync"] + 0.25),
        "trend_similarity": clip01(m["mean_trend_similarity"] + 0.15),
        "lead_lag_stability": clip01(m["mean_lead_lag"] + 0.1),
        "network_dynamics": clip01(0.35 * m["freshness"] + 0.30 * m["new_edge_ratio"]
                                   + 0.20 * clip01(m["mean_edge_delta"] * 5 + 0.5)
                                   + 0.15 * m["expansion_score"]),
        "driver_confidence": _driver_weight(driver_conf),
    }
    return round(sum(w[k] * parts[k] for k in w), 2)


def discover_clusters(net: NetworkResult, s: Slice, fs: FeatureSet, cfg: dict[str, Any],
                      history: History, driver_map: dict[str, dict] | None = None) -> ClusterSet:
    ccfg, scfg, acfg = cfg["cluster"], cfg["scoring"], cfg["attribution"]
    gkey = "market_smoothed" if "market_smoothed" in net.graphs else "market"
    communities, algo = _communities(net.graphs[gkey], cfg)
    log.info("社区发现 (%s): %d 个原始社区", algo, len(communities))
    out = ClusterSet(asof=s.asof)
    edges = net.edges
    idx = s.code_index()
    member_rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    raw_clusters = 0
    rejected: dict[str, int] = {}

    for comm in communities:
        raw_clusters += 1
        members = sorted(comm & set(s.codes))
        n = len(members)
        if n < int(ccfg.get("min_size", 4)):
            rejected["small"] = rejected.get("small", 0) + 1
            continue
        if n > int(ccfg.get("max_size", 60)):
            rejected["oversize"] = rejected.get("oversize", 0) + 1
            continue
        sub = edges[edges["a"].isin(members) & edges["b"].isin(members)] if len(edges) else edges
        if len(sub) == 0:
            continue
        density = 2.0 * len(sub) / max(1, n * (n - 1))
        mean_es = float(sub["edge_score"].mean())
        if density < float(ccfg.get("min_density", 0.28)):
            rejected["low_density"] = rejected.get("low_density", 0) + 1
            continue
        if mean_es < float(ccfg.get("min_mean_edge_score", 0.50)):
            rejected["weak_edges"] = rejected.get("weak_edges", 0) + 1
            continue

        cur_pairs = _pairset(sub)
        prev_id, merged = _match_identity(set(members), cur_pairs, history, cfg)
        age = history.age.get(prev_id, 0) + 1 if prev_id else 1
        cid = prev_id if prev_id and prev_id not in used_ids else f"C{s.asof}-{raw_clusters:03d}"
        used_ids.add(cid)
        birth = history.cluster_dates.get(prev_id) if prev_id else s.asof
        freshness = clip01(math.exp(-(age - 1) / float(ccfg.get("freshness_halflife", 8.0))))

        js = [idx[c] for c in members]
        pos_resid = int((fs.l("resid_cum_5")[js] > 0).sum())
        improving = int(((fs.l("ma_stack")[js] >= 2 / 3) &
                         (fs.l("rs5_change")[js] > 0)).sum())
        new_edge_ratio = float(sub["is_new"].mean()) if "is_new" in sub else 1.0
        mean_delta = float(sub["edge_delta"].mean()) if "edge_delta" in sub else 0.0
        mean_corr = float(sub["residual_corr_20"].mean())
        mean_event = float(sub["event_sync"].mean())
        mean_vol = float(sub["volume_sync"].mean())
        mean_trend = float(sub["trend_similarity"].mean())
        mean_ll = float(sub["lead_lag_score"].mean())
        mean_amt_ratio = float(np.mean(fs.l("amount_ratio")[js]))
        mean_ret5 = float(np.mean(fs.l("ret_5")[js]))
        mean_excess5 = float(np.mean(fs.l("excess_mkt_5")[js]))

        # 历史对比：20 日前是否已存在稳定关联（双向配对，避免系统性低估）
        old_pairs = _pairset(history.edges_20d)
        pre_existing_ratio = len(cur_pairs & old_pairs) / max(1, len(cur_pairs))
        prev_pairs_in = {p for p in history.prev_edge_pairs
                         if p[0] in members and p[1] in members}
        density_prev = 2.0 * len(prev_pairs_in) / max(1, n * (n - 1))
        density_delta = density - density_prev

        prev_members: set[str] = set()
        if history.prev_clusters is not None and len(history.prev_clusters) and prev_id:
            prev_members = set(history.prev_clusters[
                history.prev_clusters["cluster_id"] == prev_id]["ts_code"].astype(str))
        new_members = sorted(set(members) - prev_members) if prev_members else []
        lost_members = sorted(prev_members - set(members)) if prev_members else []
        expansion = len(new_members) / max(1, len(prev_members)) if prev_members else 0.0

        roles = _roles(members, s, fs, net, sub)
        leader = next((c for c, r in roles.items() if r == "LEADER"), members[0])
        followers = [c for c, r in roles.items() if r in ("FOLLOWER", "LAGGARD")]

        # 过热 / 衰减
        overheated = (mean_ret5 > float(ccfg.get("overheated_mean_ret_5", 0.30))
                      or mean_amt_ratio > float(ccfg.get("overheated_mean_amount_ratio", 3.0)))

        # 可比边口径：新增边没有昨日基值（delta=0），若混入会把「联动增强」系统性稀释到
        # 0.01 量级，使 0.05 阈值永远无法触发。只对昨日已存在的边衡量强度变化。
        persisted = sub[~sub["is_new"].astype(bool)] if "is_new" in sub else sub
        mean_delta_p = float(persisted["edge_delta"].mean()) if len(persisted) else 0.0
        mean_corr_delta = (float(persisted["corr_delta"].mean())
                           if "corr_delta" in persisted and len(persisted) else 0.0)
        st_cfg = ccfg.get("strengthening", {})
        gw = st_cfg.get("weights", {"edge_delta": 0.50, "corr_delta": 0.30, "density_delta": 0.20})
        gn = st_cfg.get("normalizers", {"edge_delta": 0.025, "corr_delta": 0.05, "density_delta": 0.20})
        go = st_cfg.get("offsets", {"edge_delta": 0.005, "corr_delta": 0.010, "density_delta": 0.050})
        vals = {"edge_delta": mean_delta_p, "corr_delta": mean_corr_delta,
                "density_delta": density_delta}
        gain_score = clip01(sum(float(gw.get(k, 0.0)) *
                               clip01((v + float(go.get(k, 0.0))) / max(1e-9, float(gn.get(k, 1.0))))
                               for k, v in vals.items()))
        neg_resid_ratio = float(np.mean(fs.l("resid_cum_5")[js] < 0))
        lost_ratio = len(lost_members) / max(1, len(prev_members)) if prev_members else 0.0
        decay_thr = float(ccfg.get("decay_edge_delta", -0.015))
        # 衰减必须有「关联真实走弱」的证据，不再仅凭成员换血或整体残差为负就判 DECAY
        decaying = bool(
            mean_delta_p < decay_thr
            or (lost_ratio >= 0.4 and density_delta < 0 and mean_delta_p < 0)
            or (neg_resid_ratio > 0.75 and mean_delta_p < 0 and density_delta < 0))

        # 「新增关联关系持续至少 2 个交易日」指新关系本身被跨日确认，
        # 不能等价于「簇内所有边都连续 2 日」——那会让新群体第一天永远无法成立。
        persisted_edges = int((sub["streak_days"] >= 2).sum()) if "streak_days" in sub else 0
        emerging_ok = (
            pre_existing_ratio < 0.35
            and n >= int(ccfg.get("min_size", 4))
            and pos_resid >= int(ccfg.get("emerging_min_positive_residual", 3))
            and mean_vol > 0.25 and mean_trend > 0.30
            and (new_edge_ratio >= float(ccfg.get("emerging_new_edge_ratio", 0.35)) or age <= 2)
            and (age >= int(ccfg.get("persistence_days", 2)) or persisted_edges >= 2)
        )
        young = age <= int(ccfg.get("emerging_max_age", 5))

        # 三类机会并非互斥：同一群体可同时是新形成 + 正在强化。
        # signals 保留全部标签供回测分组，state 只取一个主状态供日报展示。
        signals: list[str] = []
        if emerging_ok and (prev_id is None or young):
            signals.append("EMERGING_CLUSTER")
        if (prev_members and len(new_members) >= int(ccfg.get("expansion_min_new_members", 1))
                and leader in members):
            signals.append("LEADER_EXPANSION")
        has_persisted = (history.prev_edges is not None
                         and len(persisted) >= int(ccfg.get("strengthening_min_persisted_edges", 2)))
        if (age >= int(ccfg.get("strengthening_min_age", 1)) and has_persisted
                and gain_score >= float(st_cfg.get("min_gain", 0.50)) and not decaying):
            signals.append("CLUSTER_STRENGTHENING")
        if decaying:
            signals.append("DECAYING_CLUSTER")
        if overheated:
            signals.append("OVERHEATED_CLUSTER")
        priority = ("DECAYING_CLUSTER", "OVERHEATED_CLUSTER", "EMERGING_CLUSTER",
                    "LEADER_EXPANSION", "CLUSTER_STRENGTHENING")
        if not history.available:
            state = "BASELINE_FIRST_SNAPSHOT"
        elif not signals:
            state = "EXISTING_CLUSTER"
        else:
            state = next((s for s in priority if s in signals), "EXISTING_CLUSTER")

        stage = ("EARLY_STRENGTHENING" if age <= 5 and mean_delta_p > 0
                 else "MATURE_STRENGTHENING" if mean_delta_p > 0 else "STABLE")
        if state == "DECAYING_CLUSTER":
            stage = "DECAY"
        if state == "OVERHEATED_CLUSTER":
            stage = "OVERHEATED"

        # 板块前置联动（行业等权收益作板块代理）
        inds = [str(s.industry[idx[c]]) for c in members]
        main_ind = max(set(inds), key=inds.count)
        ind_share = inds.count(main_ind) / n
        j0 = idx[members[0]]
        k = min(int(acfg.get("pre_sector_lookback", 10)), s.T)
        sector_ret = float(np.nansum(s.industry_ret[-k:, j0]))
        market_ret = float(np.nansum(s.market_ret[-k:]))
        sector_excess = sector_ret - market_ret
        pre_sector = bool(
            sector_excess < float(acfg.get("pre_sector_max_sector_excess", 0.025))
            and pos_resid >= int(acfg.get("pre_sector_min_positive_residual", 3))
            and (mean_delta > 0 or new_edge_ratio > 0.3)
            and mean_amt_ratio > 1.0
            and leader in members
        )
        cross_sector = bool(len(set(inds)) > 1 and ind_share < float(acfg.get("cross_sector_threshold", 0.50)))

        driver = (driver_map or {}).get(cid, {}) if driver_map else {}
        driver_conf = str(driver.get("confidence", "UNKNOWN"))

        m = {
            "mean_resid_corr": mean_corr, "mean_event_sync": mean_event,
            "mean_volume_sync": mean_vol, "mean_trend_similarity": mean_trend,
            "mean_lead_lag": mean_ll, "mean_edge_delta": mean_delta_p,
            "new_edge_ratio": new_edge_ratio, "freshness": freshness,
            "expansion_score": clip01(expansion),
        }
        ls = linkage_score(m, cfg, driver_conf)
        reliability = clip01(0.4 * float(sub["edge_reliability"].mean())
                             + 0.3 * density + 0.3 * clip01(len(sub) / (n * 2)))
        strengthening = gain_score
        decay_risk = clip01(max(0.0, -mean_delta_p) / 0.20 + len(lost_members) / max(1, n)
                            + 0.3 * neg_resid_ratio)
        sample_risk = clip01(0.6 * (4.0 / n)
                             + 0.4 * float((sub["sample_n"] < 20).mean() if "sample_n" in sub else 0.0))

        rec = {
            "cluster_id": cid, "asof": s.asof, "members": members, "n_members": n,
            "state": state, "stage": stage, "grade": "B",
            "linkage_score": ls, "freshness": round(freshness, 4),
            "reliability": round(reliability, 4), "expansion_score": round(clip01(expansion), 4),
            "strengthening_score": round(strengthening, 4),
            "decay_risk": round(decay_risk, 4), "sample_risk": round(sample_risk, 4),
            "age_days": age, "birth_date": birth, "density": round(density, 4),
            "n_edges": int(len(sub)), "new_edges": int(sub["is_new"].sum()) if "is_new" in sub else len(sub),
            "new_edge_ratio": round(new_edge_ratio, 4), "mean_edge_delta": round(mean_delta, 4),
            "mean_edge_delta_persisted": round(mean_delta_p, 4),
            "mean_corr_delta": round(mean_corr_delta, 4),
            "density_delta": round(density_delta, 4), "gain_score": round(gain_score, 4),
            "signals": signals,
            "mean_resid_corr": round(mean_corr, 4), "positive_resid_stocks": pos_resid,
            "improving_stocks": improving, "mean_excess_5": round(mean_excess5, 4),
            "mean_ret_5": round(mean_ret5, 4), "mean_amount_ratio": round(mean_amt_ratio, 4),
            "pre_existing_edge_ratio_20d": round(pre_existing_ratio, 4),
            "leader": leader, "roles": roles,
            "new_members": new_members, "lost_members": lost_members,
            "main_industry": main_ind, "industry_concentration": round(ind_share, 4),
            "cross_sector": cross_sector, "sector_excess_10": round(sector_excess, 4),
            "pre_sector_emergence": pre_sector, "overheated": overheated,
            "driver_confidence": driver_conf,
            "driver_label": driver.get("label", ""),
            "algorithm": algo,
            "priority": round(ls * (0.65 + 0.35 * freshness), 3),
            "metrics": m,
        }
        rec["grade"] = _grade_from_rec(rec, cfg)
        out.clusters.append(rec)

        for c in members:
            j = idx[c]
            member_rows.append({
                "asof": s.asof, "cluster_id": cid, "ts_code": c,
                "stock_name": str(s.names[j]), "role": roles[c],
                "industry": str(s.industry[j]),
                "excess_mkt_5": round(float(fs.l("excess_mkt_5")[j]), 4),
                "resid_cum_5": round(float(fs.l("resid_cum_5")[j]), 4),
                "resid_corr_20": round(float(_member_corr(sub, c)), 4),
                "amount_ratio": round(float(fs.l("amount_ratio")[j]), 4),
                "ma_stack": round(float(fs.l("ma_stack")[j]), 4),
                "ret_5": round(float(fs.l("ret_5")[j]), 4),
                "trend": str(_trend_text(fs, j)),
                "is_new_member": (prev_id is None) or (c in new_members),
            })
        if not prev_id:
            events.append({"asof": s.asof, "cluster_id": cid, "event_type": "BIRTH",
                           "detail": {"members": members, "n_edges": len(sub)}})
        else:
            if merged:
                events.append({"asof": s.asof, "cluster_id": cid, "event_type": "MERGE",
                               "detail": {"merged_from": merged}})
            if new_members:
                events.append({"asof": s.asof, "cluster_id": cid, "event_type": "EXPANSION",
                               "detail": {"added": new_members}})
            if lost_members:
                events.append({"asof": s.asof, "cluster_id": cid, "event_type": "SPLIT",
                               "detail": {"removed": lost_members}})
        # 强化/衰减是关系层面的信号，不依赖群体 id 是否延续；即使是新建 id，
        # 只要簇内已有昨日可比边，也应完整记录生命周期事件。
        if "CLUSTER_STRENGTHENING" in signals:
            events.append({"asof": s.asof, "cluster_id": cid, "event_type": "STRENGTHEN",
                           "detail": {"mean_edge_delta_persisted": round(mean_delta_p, 4),
                                      "mean_corr_delta": round(mean_corr_delta, 4),
                                      "density_delta": round(density_delta, 4),
                                      "gain_score": round(gain_score, 4)}})
        if "DECAYING_CLUSTER" in signals:
            events.append({"asof": s.asof, "cluster_id": cid, "event_type": "DECAY",
                           "detail": {"mean_edge_delta_persisted": round(mean_delta_p, 4),
                                      "density_delta": round(density_delta, 4)}})

    out.members = pd.DataFrame(member_rows)
    out.events = events
    # 终止的群体
    if history.available and history.prev_clusters is not None and len(history.prev_clusters):
        alive = {c["cluster_id"] for c in out.clusters}
        for cid in set(history.prev_clusters["cluster_id"].astype(str)) - alive:
            out.events.append({"asof": s.asof, "cluster_id": cid,
                               "event_type": "TERMINATION", "detail": {}})

    for c in out.clusters:
        c["rank"] = 0
    ranked = sorted([c for c in out.clusters if c["grade"] in ("S", "A", "B")],
                    key=lambda x: ({"S": 0, "A": 1, "B": 2, "C": 3}[x["grade"]],
                                   -x["priority"]))
    for i, c in enumerate(ranked[:int(scfg.get("max_display_clusters", 5))], 1):
        c["rank"] = i
    out.clusters.sort(key=lambda x: (x["rank"] or 999, -x["priority"]))

    counts: dict[str, int] = {}
    sig_counts: dict[str, int] = {}
    for c in out.clusters:
        counts[c["state"]] = counts.get(c["state"], 0) + 1
        for sg in c.get("signals", []):
            sig_counts[sg] = sig_counts.get(sg, 0) + 1
    out.stats = {
        "raw_communities": raw_clusters,
        "valid_clusters": len(out.clusters),
        "rejected": rejected,
        "algorithm": algo,
        "grade_counts": {g: sum(1 for c in out.clusters if c["grade"] == g) for g in "SABC"},
        "state_counts": counts,
        "signal_counts": sig_counts,
        "emerging_clusters": sig_counts.get("EMERGING_CLUSTER", 0),
        "expanding_clusters": sig_counts.get("LEADER_EXPANSION", 0),
        "strengthening_clusters": sig_counts.get("CLUSTER_STRENGTHENING", 0),
        "decaying_clusters": sig_counts.get("DECAYING_CLUSTER", 0),
        "pre_sector_clusters": sum(1 for c in out.clusters if c["pre_sector_emergence"]),
        "new_edges_today": int(net.stats.get("new_edges", 0)),
        "history_available": history.available,
    }
    out.flags = {
        "TODAY_NO_VALID_CLUSTER": len([c for c in out.clusters if c["grade"] in ("S", "A")]) == 0,
        "BASELINE_SNAPSHOT": not history.available,
        "_baseline_note": "首个快照无历史网络，按规格不硬判新联动，仅建立基线" if not history.available else "",
    }
    log.info("群体完成: 有效 %d 个 (S/A/B/C=%s), 状态=%s", len(out.clusters),
             out.stats["grade_counts"], counts)
    return out


def _grade_from_rec(rec: dict[str, Any], cfg: dict[str, Any]) -> str:
    """统一评级函数：发现阶段与归因完成后各调用一次，保证口径一致。"""
    scfg = cfg["scoring"]
    roles = rec.get("roles", {})
    followers = [c for c, r in roles.items() if r in ("FOLLOWER", "LAGGARD")]
    state = rec["state"]
    if state == "DECAYING_CLUSTER" or rec["decay_risk"] > 0.7:
        return "C"
    if state == "BASELINE_FIRST_SNAPSHOT":
        # 无历史网络时无法证明"新形成/正在扩散/持续强化"，最高只给统计观察级
        return "B" if rec["linkage_score"] >= float(scfg.get("grade_b_min_score", 32)) else "C"
    strengthening = (rec.get("mean_edge_delta_persisted", 0) > 0
                     or rec.get("gain_score", 0) >= 0.5
                     or rec.get("new_edge_ratio", 0) > 0.3)
    if (state in ("EMERGING_CLUSTER", "LEADER_EXPANSION")
            or rec.get("stage") == "EARLY_STRENGTHENING") \
            and strengthening \
            and rec["age_days"] <= int(scfg.get("s_max_freshness_age", 15)) \
            and rec["linkage_score"] >= float(scfg.get("grade_s_min_score", 62)) \
            and rec["mean_excess_5"] > 0 \
            and rec["improving_stocks"] >= int(scfg.get("s_min_improving_stocks", 3)) \
            and rec.get("driver_confidence") in ("HIGH", "MEDIUM") \
            and not rec["overheated"]:
        return "S"
    # A 级必须真的有扩散/强化迹象，不能只因为历史数据多就长期占位
    has_expansion = (rec.get("expansion_score", 0) > 0
                     or rec.get("new_members", []) != []
                     or rec.get("mean_edge_delta_persisted", 0) > 0
                     or rec.get("gain_score", 0) >= 0.5)
    if (rec["leader"] in rec["members"]
            and len(followers) >= int(scfg.get("a_min_followers", 1))
            and has_expansion
            and rec["mean_excess_5"] > float(scfg.get("a_min_mean_excess_5", -0.01))
            and rec["linkage_score"] >= float(scfg.get("grade_a_min_score", 48))
            and rec["reliability"] > 0.45):
        return "A"
    if rec["linkage_score"] >= float(scfg.get("grade_b_min_score", 32)):
        return "B"
    return "C"


def _member_corr(sub: pd.DataFrame, code: str) -> float:
    if not len(sub):
        return 0.0
    m = sub[(sub["a"] == code) | (sub["b"] == code)]
    return float(m["residual_corr_20"].mean()) if len(m) else 0.0


def _trend_text(fs: FeatureSet, j: int) -> str:
    stack = float(fs.l("ma_stack")[j])
    slope = float(fs.l("ma20_slope")[j])
    if stack >= 0.66 and slope > 0:
        return "多头/MA20上行"
    if stack >= 0.33:
        return "均线纠缠/修复中"
    if float(fs.l("above_ma20")[j]) > 0:
        return "站上MA20/待确认"
    return "弱势/结构破坏"


def apply_driver_scores(cs: ClusterSet, driver_map: dict[str, dict], cfg: dict[str, Any]) -> ClusterSet:
    """归因完成后重算含共同驱动可信度的 LinkageScore（不改动群体成员与统计结果）。"""
    for c in cs.clusters:
        d = driver_map.get(c["cluster_id"], {})
        conf = str(d.get("confidence", "UNKNOWN"))
        c["driver_confidence"] = conf
        c["driver_label"] = d.get("label", "")
        c["driver_evidence"] = d.get("evidence", [])
        c["linkage_score"] = linkage_score(c["metrics"], cfg, conf)
        c["grade"] = _grade_from_rec(c, cfg)
        c["priority"] = round(c["linkage_score"] * (0.65 + 0.35 * c["freshness"]), 3)
    ranked = sorted([c for c in cs.clusters if c["grade"] in ("S", "A", "B")],
                    key=lambda x: ({"S": 0, "A": 1, "B": 2, "C": 3}[x["grade"]],
                                   -x["priority"]))
    for c in cs.clusters:
        c["rank"] = 0
    for i, c in enumerate(ranked[:int(cfg["scoring"].get("max_display_clusters", 5))], 1):
        c["rank"] = i
    cs.clusters.sort(key=lambda x: (x["rank"] or 999, -x["priority"]))
    cs.stats["grade_counts"] = {g: sum(1 for c in cs.clusters if c["grade"] == g)
                                for g in "SABC"}
    cs.flags["TODAY_NO_VALID_CLUSTER"] = cs.stats["grade_counts"]["S"] + cs.stats["grade_counts"]["A"] == 0
    return cs
