"""Dynamic Linkage Engine —— 动态股票关联网络（第三层）。

分层计算（严禁对全部股票对无差别做复杂计算）：
  Step 1 候选预筛：短周期残差 Pearson 全矩阵（一次 BLAS）+ 轻量行为向量余弦
                    （残差序列 / 成交额变化 / 趋势状态），每只股票只留 Top-K。
  Step 2 仅对候选对计算完整 EdgeScore 的 7 类指标。
  Step 3 过滤 + 稀疏关联矩阵 / networkx 图（两套网络：market / market_industry）。
  Step 4 交给 cluster_engine 做社区发现。

防伪联动：原始收益相关高、剔除市场后相关消失 => 不认定；
同行业且被行业因子解释掉的联动 => 标记 industry_explained 并剔除。
多重检验：置换随机基准 + Benjamini-Hochberg FDR 控制。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd
from scipy.special import erfc

from .common import clip01, get_logger, rankdata_axis0, zscore
from .data_layer import Slice
from .feature_engine import FeatureSet

log = get_logger("linkage_engine")

EDGE_COLS = [
    "a", "b", "residual_corr_20", "residual_corr_10", "residual_corr_60",
    "rank_corr", "corr_stability", "corr_dual", "raw_corr", "same_industry",
    "event_sync", "volume_sync", "trend_similarity", "lead_lag_score",
    "lead_lag_best", "sample_n", "p_value", "q_value", "edge_reliability",
    "edge_score", "is_new", "edge_delta", "streak_days",
]


@dataclass
class NetworkResult:
    asof: str
    edges: pd.DataFrame
    graphs: dict[str, nx.Graph] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    corr20: np.ndarray | None = None
    codes: list[str] = field(default_factory=list)


def _corr_matrix(X: np.ndarray) -> np.ndarray:
    """标准化列后一次矩阵乘得到全市场 Pearson 相关（廉价预筛用）。"""
    A = np.nan_to_num(np.asarray(X, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    A = A - A.mean(axis=0, keepdims=True)
    sd = np.sqrt((A * A).sum(axis=0))
    A = A / np.where(sd > 1e-9, sd, 1.0)
    # 列已做 L2 归一化，点积即为 Pearson 相关，不能再除以自由度
    C = A.T @ A
    return np.clip(C.astype(np.float32), -1.0, 1.0)


def _batch_corr(A: np.ndarray, ii: np.ndarray, jj: np.ndarray) -> np.ndarray:
    """批量相关：A 为 (w,N)，返回每对 (i,j) 的 Pearson。"""
    X = np.nan_to_num(np.asarray(A, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    a = X[:, ii] - X[:, ii].mean(axis=0)
    b = X[:, jj] - X[:, jj].mean(axis=0)
    sa = np.sqrt((a * a).sum(axis=0))
    sb = np.sqrt((b * b).sum(axis=0))
    den = np.where(sa * sb > 1e-9, sa * sb, np.nan)
    return np.nan_to_num(np.clip((a * b).sum(axis=0) / den, -1, 1), nan=0.0)


def _dilate(E: np.ndarray) -> np.ndarray:
    """事件矩阵 ±1 日膨胀，用于容差同步匹配。"""
    E = np.asarray(E, dtype=np.int8)
    z = np.zeros((1, E.shape[1]), dtype=np.int8)
    return (E | np.vstack([E[1:], z]) | np.vstack([z, E[:-1]])).astype(np.int8)


def bh_fdr(p: np.ndarray) -> np.ndarray:
    n = len(p)
    if n == 0:
        return p
    order = np.argsort(p)
    ranked = p[order] * n / np.arange(1, n + 1)
    q = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n, dtype=np.float64)
    out[order] = np.clip(q, 0.0, 1.0)
    return out


def _pair_key(i: int, j: int) -> tuple[int, int]:
    return (i, j) if i < j else (j, i)


def prescreen(s: Slice, fs: FeatureSet, resid: np.ndarray, cfg: dict[str, Any],
              C20: np.ndarray, C10: np.ndarray, C60: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Step 1：分层候选预筛，返回 (ii, jj) 上三角候选对。"""
    lcfg = cfg["linkage"]
    N = s.N
    K = int(lcfg.get("max_candidates_per_stock", 40))
    block = int(lcfg.get("candidate_block_size", 512))
    maxpairs = int(lcfg.get("max_candidate_pairs", 400000))
    thr20, thr10 = float(lcfg["corr_threshold_20"]), float(lcfg["corr_threshold_10"])

    # 轻量行为向量：10 日残差 + 成交额变化 + 趋势状态 + 20 日斜率
    w = min(10, s.T)
    parts = [
        zscore(np.asarray(resid[-w:], dtype=np.float32).T),
        zscore(np.asarray(fs.series_of("amount_ratio")[-w:], dtype=np.float32).T),
        zscore(np.asarray(fs.trend_state[-w:], dtype=np.float32).T),
        zscore(np.asarray(fs.series_of("ma20_slope")[-w:], dtype=np.float32).T),
    ]
    Z = np.concatenate(parts, axis=1)
    norm = np.linalg.norm(Z, axis=1, keepdims=True)
    Z = Z / np.where(norm > 1e-9, norm, 1.0)

    cand: set[tuple[int, int]] = set()
    base = 0.45 * np.maximum(C20, C10) + 0.25 * C60
    for st in range(0, N, block):
        en = min(N, st + block)
        sim = Z[st:en] @ Z.T
        score = np.nan_to_num(base[st:en] + 0.30 * sim, nan=0.0, posinf=0.0, neginf=0.0)
        score[np.arange(en - st), np.arange(st, en)] = -9.0
        k = min(K, N - 1)
        top = np.argpartition(-score, k, axis=1)[:, :k]
        mins = float(lcfg.get("prescreen_min_similarity", 0.45))
        for r in range(en - st):
            i = st + r
            for j in top[r]:
                j = int(j)
                if i == j or score[r, j] < mins:
                    continue
                cand.add(_pair_key(i, j))

    # 硬阈值兜底：相关明显但行为向量未排进 Top-K 的对也要纳入
    hard = (C20 >= thr20 * 0.85) | (C10 >= thr10 * 0.85)
    hi, hj = np.nonzero(np.triu(hard, 1))
    for i, j in zip(hi.tolist()[:maxpairs], hj.tolist()[:maxpairs]):
        cand.add((int(i), int(j)))

    if len(cand) > maxpairs:
        arr = np.array(list(cand), dtype=np.int32)
        vals = np.maximum(C20[arr[:, 0], arr[:, 1]], C10[arr[:, 0], arr[:, 1]])
        keep = np.argpartition(-vals, maxpairs)[:maxpairs]
        arr = arr[keep]
        ii, jj = arr[:, 0], arr[:, 1]
    else:
        arr = np.array(sorted(cand), dtype=np.int32) if cand else np.empty((0, 2), dtype=np.int32)
        ii, jj = (arr[:, 0], arr[:, 1]) if len(arr) else (np.array([], dtype=np.int32),
                                                          np.array([], dtype=np.int32))
    info = {
        "universe_pairs": N * (N - 1) // 2,
        "candidate_pairs": int(len(ii)),
        "candidate_ratio": round(len(ii) / max(1, N * (N - 1) // 2), 5),
        "prescreen_topk": K,
        "prescreen_method": "residual_corr_matrix + behavior_cosine_topk",
    }
    log.info("预筛: %d / %d 对 (%.3f%%)", info["candidate_pairs"], info["universe_pairs"],
             info["candidate_ratio"] * 100)
    return ii, jj, info


def _chunk_metrics(s: Slice, fs: FeatureSet, resid: np.ndarray, resid_dual: np.ndarray,
                   raw: np.ndarray, ii: np.ndarray, jj: np.ndarray,
                   cfg: dict[str, Any], industry: np.ndarray,
                   C20: np.ndarray, C10: np.ndarray, C60: np.ndarray,
                   Crank: np.ndarray, null_mu: float, null_sd: float) -> pd.DataFrame:
    lcfg = cfg["linkage"]
    W = int(lcfg.get("long_window", 60))
    X = np.asarray(resid[-W:], dtype=np.float32)
    Xd = np.asarray(resid_dual[-W:], dtype=np.float32)
    R = np.asarray(raw[-W:], dtype=np.float32)
    amt = np.asarray(fs.series_of("amount_ratio")[-W:], dtype=np.float32)
    trend = np.asarray(fs.trend_state[-W:], dtype=np.float32)
    slope = np.asarray(fs.series_of("ma20_slope")[-W:], dtype=np.float32)
    event_mats = {}
    for et, E in fs.events.items():
        e = np.asarray(E[-W:], dtype=np.int8)
        event_mats[et] = (e, _dilate(e))

    weights = lcfg["edge_weights"]
    lags = list(lcfg.get("lead_lag_lags", [1, 2, 3]))
    chunk = 20000
    frames = []
    for st in range(0, len(ii), chunk):
        a, b = ii[st:st + chunk], jj[st:st + chunk]
        P = len(a)
        c20 = C20[a, b].astype(np.float64)
        c10 = C10[a, b].astype(np.float64)
        c60 = C60[a, b].astype(np.float64)
        crank = Crank[a, b].astype(np.float64)
        rawc = _batch_corr(R[-20:], a, b).astype(np.float64)
        dual = _batch_corr(Xd[-20:], a, b).astype(np.float64)

        # 相关性稳定度：60 日切成 3 段 20 日
        seg = np.stack([_batch_corr(X[k:k + 20], a, b) for k in (0, 20, 40) if k + 20 <= W], axis=1)
        stability = 1.0 - np.minimum(1.0, seg.std(axis=1))

        # 同步异动：容差 ±1 日的匹配率，重点事件加权
        sync_parts, sync_w = [], []
        for et, (E, D) in event_mats.items():
            ci = E[:, a].sum(axis=0).astype(np.float64)
            cj = E[:, b].sum(axis=0).astype(np.float64)
            m = (E[:, a] * D[:, b]).sum(axis=0) + (E[:, b] * D[:, a]).sum(axis=0)
            val = m / np.maximum(ci + cj, 1.0)
            sync_parts.append(np.clip(val / 2.0, 0, 1))
            sync_w.append(1.6 if et in ("BREAKOUT", "VOL_UP", "RELATIVE_STRENGTH") else 0.8)
        event_sync = np.average(np.stack(sync_parts, axis=1), axis=1, weights=sync_w)

        vol_sync = np.clip((_batch_corr(amt[-20:], a, b) + 0.2) / 0.9, 0, 1)
        tr_corr = _batch_corr(trend[-20:], a, b)
        sl_corr = _batch_corr(slope[-20:], a, b)
        state_gap = np.abs(fs.l("ma_stack")[a] - fs.l("ma_stack")[b]) / 3.0
        ret_gap = np.abs(fs.l("ret_20")[a] - fs.l("ret_20")[b])
        trend_sim = np.clip(0.45 * tr_corr + 0.25 * sl_corr + 0.30
                            - 0.25 * state_gap - 0.15 * np.minimum(ret_gap, 1.0), 0, 1)

        # 领先滞后：互相关的最大绝对值与方向，双窗口一致性
        ll_best = np.zeros(P, dtype=np.int8)
        # X_i(t-lag) 与 X_j(t) 互相关（批量）
        best_abs = np.zeros(P)
        for lag in lags:
            xa = X[W - 20 - lag: W - lag, :][:, a]
            xb = X[W - 20:, :][:, b]
            v = _batch_corr_aligned(xa, xb)
            take = np.abs(v) > best_abs
            ll_best = np.where(take, np.sign(v) * lag, ll_best).astype(np.int8)
            best_abs = np.where(take, np.abs(v), best_abs)
        ll_30 = np.zeros(P)
        for lag in lags:
            xa = X[W - 30 - lag: W - lag, :][:, a]
            xb = X[W - 30:, :][:, b]
            v = _batch_corr_aligned(xa, xb)
            ll_30 = np.maximum(ll_30, np.abs(v))
        lead_lag = np.clip(0.7 * best_abs + 0.3 * (1.0 - np.minimum(1.0, np.abs(best_abs - ll_30))), 0, 1)

        # 有效样本数（两股同时有行情）
        va = np.asarray(s.valid[-20:], dtype=bool)
        sample_n = (va[:, a] & va[:, b]).sum(axis=0)
        sample_factor = np.minimum(1.0, sample_n / 20.0)

        # 置换随机基准校准的 Fisher-z 解析尾概率。
        # 纯经验 p 的分辨率受置换次数限制（最小 1/M），当显著对很少时 BH-FDR 会全部拒绝；
        # 用置换样本估计零分布均值/标准差，再取解析正态尾概率，p 值连续且仍由随机基准校准。
        z = np.arctanh(np.clip(c20, -0.999, 0.999)) * np.sqrt(np.maximum(sample_n - 3, 1))
        zsc = np.abs(z - null_mu) / max(null_sd, 1e-6)
        p = np.clip(erfc(zsc / sqrt(2.0)), 1e-300, 1.0)

        residual_sync = np.clip((0.55 * np.maximum(c20, c10) + 0.25 * c60
                                 + 0.20 * crank + 0.10 * stability) / 1.10, 0, 1)
        reliability = np.clip((0.45 * sample_factor + 0.35 * stability
                               + 0.20 * np.clip(1 - p * 2, 0, 1)), 0, 1)
        edge_score = (weights["residual_sync"] * residual_sync
                      + weights["event_sync"] * event_sync
                      + weights["volume_sync"] * vol_sync
                      + weights["trend_similarity"] * trend_sim
                      + weights["lead_lag"] * lead_lag) * (0.65 + 0.35 * reliability)

        frames.append(pd.DataFrame({
            "ia": a, "ib": b,
            "residual_corr_20": c20, "residual_corr_10": c10, "residual_corr_60": c60,
            "rank_corr": crank, "corr_stability": stability, "corr_dual": dual,
            "raw_corr": rawc, "same_industry": industry[a] == industry[b],
            "event_sync": event_sync, "volume_sync": vol_sync,
            "trend_similarity": trend_sim, "lead_lag_score": lead_lag,
            "lead_lag_best": ll_best, "sample_n": sample_n,
            "p_value": p, "edge_reliability": reliability, "edge_score": edge_score,
        }))
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(out):
        out["q_value"] = bh_fdr(out["p_value"].to_numpy())
    return out


def _batch_corr_aligned(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """两个已经对齐的 (w,P) 矩阵逐列相关。"""
    a = np.nan_to_num(np.asarray(a, dtype=np.float32), nan=0.0)
    b = np.nan_to_num(np.asarray(b, dtype=np.float32), nan=0.0)
    a = a - a.mean(axis=0)
    b = b - b.mean(axis=0)
    sa, sb = np.sqrt((a * a).sum(axis=0)), np.sqrt((b * b).sum(axis=0))
    den = np.where(sa * sb > 1e-9, sa * sb, np.nan)
    return np.nan_to_num(np.clip((a * b).sum(axis=0) / den, -1, 1), nan=0.0)


def build_network(s: Slice, fs: FeatureSet, resid, cfg: dict[str, Any],
                  history_edges: pd.DataFrame | None = None,
                  history_edges_5d: pd.DataFrame | None = None) -> NetworkResult:
    lcfg = cfg["linkage"]
    W = int(lcfg.get("long_window", 60))
    X = np.asarray(resid.resid_mkt[-W:], dtype=np.float32)
    Xd = np.asarray(resid.resid_mkt_ind[-W:], dtype=np.float32)
    raw = np.asarray(s.ret[-W:], dtype=np.float32)
    N = s.N

    C20, C10, C60 = _corr_matrix(X[-20:]), _corr_matrix(X[-10:]), _corr_matrix(X)
    rk = rankdata_axis0(np.asarray(X[-20:], dtype=np.float64))
    Crank = _corr_matrix(np.asarray(rk, dtype=np.float32))

    # 置换随机基准：随机配对的 |corr20| 零分布
    rng = np.random.default_rng(20261108)
    M = int(lcfg.get("permutation_benchmarks", 24)) * 200
    ri = rng.integers(0, N, M)
    rj = (ri + 1 + rng.integers(0, max(1, N - 1), M)) % N
    perm_corr = _batch_corr(X[-20:], ri, rj)
    perm = np.abs(perm_corr)
    # 置换零分布的 Fisher-z 均值/标准差，用于校准解析 p 值
    z_null = np.arctanh(np.clip(perm_corr, -0.999, 0.999)) * np.sqrt(max(20 - 3, 1))
    null_mu = float(z_null.mean())
    null_sd = float(max(z_null.std(), 1e-3))

    ii, jj, pre = prescreen(s, fs, resid.resid_mkt, cfg, C20, C10, C60)
    stats = dict(pre)
    if len(ii) == 0:
        log.warning("无候选对，返回空网络")
        return NetworkResult(asof=s.asof, edges=pd.DataFrame(columns=EDGE_COLS),
                             graphs={k: nx.Graph() for k in lcfg.get("networks", ["market", "market_industry"])},
                             stats=stats, corr20=C20, codes=list(s.codes))

    m = _chunk_metrics(s, fs, resid.resid_mkt, resid.resid_mkt_ind, s.ret,
                       ii, jj, cfg, s.industry, C20, C10, C60, Crank, null_mu, null_sd)
    stats["full_metric_pairs"] = int(len(m))

    thr20, thr10 = float(lcfg["corr_threshold_20"]), float(lcfg["corr_threshold_10"])
    gate20 = m["residual_corr_20"] >= thr20
    gate10 = m["residual_corr_10"] >= thr10
    if bool(cfg["linkage"].get("require_both_windows", False)):
        corr_gate = gate20 & gate10
    else:
        corr_gate = gate20 | gate10
    pseudo = (m["raw_corr"] > 0.60) & (m["residual_corr_20"] < thr20 * 0.60)
    industry_explained = (m["same_industry"]
                          & (m["corr_dual"] < float(lcfg.get("min_dual_network_corr", 0.30)))
                          & ((m["residual_corr_20"] - m["corr_dual"]) > 0.25))
    conditions = {
        "corr_gate": corr_gate,
        "not_market_driven_pseudo": ~pseudo,
        "not_industry_explained": ~industry_explained,
        "dual_network_corr": m["corr_dual"] >= float(lcfg.get("min_dual_network_corr", 0.45)),
        "long_window_corr": m["residual_corr_60"] >= float(lcfg.get("min_long_window_corr", 0.25)),
        "corr_stability": m["corr_stability"] >= float(lcfg.get("min_corr_stability", 0.55)),
        "min_effective_sample": m["sample_n"] >= int(lcfg.get("min_effective_sample", 15)),
        "min_edge_score": m["edge_score"] >= float(lcfg.get("min_edge_score", 0.45)),
        "min_reliability": m["edge_reliability"] >= float(lcfg.get("min_reliability", 0.45)),
        "fdr": m["q_value"] <= float(lcfg.get("fdr_alpha", 0.05)),
    }
    keep = np.logical_and.reduce([np.asarray(v, dtype=bool) for v in conditions.values()])
    # 逐条累计拒绝数，便于阈值调试与数据完整性报告
    progressive = np.ones(len(m), dtype=bool)
    rejection: dict[str, int] = {}
    for name, cond in conditions.items():
        before = int(progressive.sum())
        progressive &= np.asarray(cond, dtype=bool)
        rejection[name] = before - int(progressive.sum())
    stats.update({
        "pairs_corr_gate": int(corr_gate.sum()),
        "rejected_by_condition": rejection,
        "rejected_market_driven_pseudo": int((corr_gate & pseudo).sum()),
        "rejected_industry_explained": int((corr_gate & industry_explained).sum()),
        "kept_edges": int(keep.sum()),
    })

    e = m[keep].copy().reset_index(drop=True)
    if len(e):
        e["a"] = [s.codes[int(i)] for i in e["ia"]]
        e["b"] = [s.codes[int(i)] for i in e["ib"]]
    else:
        e["a"], e["b"] = pd.Series(dtype=str), pd.Series(dtype=str)
    e = e.drop(columns=[c for c in ("ia", "ib") if c in e.columns])

    # 与历史快照对比：新增边、强度变化、连续维持天数
    e["is_new"] = True
    e["edge_delta"] = 0.0
    e["streak_days"] = 1
    if history_edges is not None and len(history_edges) and len(e):
        prev = {(r.a, r.b): r for r in history_edges.itertuples(index=False)}
        for k, row in e.iterrows():
            key = (row["a"], row["b"]) if row["a"] < row["b"] else (row["b"], row["a"])
            p = prev.get(key)
            if p is not None:
                e.at[k, "is_new"] = False
                e.at[k, "edge_delta"] = round(float(row["edge_score"]) - float(getattr(p, "edge_score", 0)), 4)
                e.at[k, "streak_days"] = int(getattr(p, "streak_days", 1)) + 1
    if history_edges_5d is not None and len(history_edges_5d) and len(e):
        old = set(zip(history_edges_5d["a"], history_edges_5d["b"]))
        e["persisted_5d"] = [((a, b) in old or (b, a) in old) for a, b in zip(e["a"], e["b"])]
    else:
        e["persisted_5d"] = False

    for c in EDGE_COLS:
        if c not in e.columns:
            e[c] = 0
    e = e[EDGE_COLS + (["persisted_5d"] if "persisted_5d" in e.columns else [])]

    # 两套稀疏图
    graphs: dict[str, nx.Graph] = {}
    G = nx.Graph()
    G.add_nodes_from(s.codes)
    for r in e.itertuples(index=False):
        G.add_edge(r.a, r.b, weight=float(r.edge_score), residual_corr=float(r.residual_corr_20))
    graphs["market"] = G
    G2 = nx.Graph()
    G2.add_nodes_from(s.codes)
    d = e[e["corr_dual"] >= float(lcfg.get("min_dual_network_corr", 0.30))]
    for r in d.itertuples(index=False):
        G2.add_edge(r.a, r.b, weight=float(r.corr_dual))
    graphs["market_industry"] = G2
    stats.update({
        "graph_nodes_market": G.number_of_nodes(),
        "graph_edges_market": G.number_of_edges(),
        "graph_edges_dual": G2.number_of_edges(),
        "new_edges": int(e["is_new"].sum()) if len(e) else 0,
        "mean_edge_score": round(float(e["edge_score"].mean()), 4) if len(e) else 0.0,
        "perm_null_abs_corr_p95": round(float(np.quantile(perm, 0.95)), 4),
        "null_method": "fisher_z_permutation_calibrated",
        "null_z_mean": round(null_mu, 4), "null_z_sd": round(null_sd, 4),
        "permutation_pairs": int(len(perm)),
    })
    log.info("网络完成: %d 条有效边 (新增 %d), 双调整校验 %d 条",
             len(e), stats["new_edges"], G2.number_of_edges())
    return NetworkResult(asof=s.asof, edges=e, graphs=graphs, stats=stats,
                         corr20=C20, codes=list(s.codes))
