"""历史网络快照与群体生命周期存储 —— 本项目最重要的数据资产。

每天必须保存：节点集合、关联边及每条边的强度、群体成员、群体形成时间、
群体变化记录、当日股票池、运行耗时与数据完整性报告。绝不能只保留当天结果。

所有边与群体都带 config_hash + evidence_json，可追溯当时的阈值与计算依据。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .common import get_logger
from .cluster_engine import ClusterSet, History
from .data_layer import Slice
from .feature_engine import FeatureSet
from .linkage_engine import NetworkResult

log = get_logger("snapshot_store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS run_log(
    asof TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, status TEXT,
    config_hash TEXT, timings_json TEXT, data_quality_json TEXT,
    network_stats_json TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS snapshots(
    asof TEXT PRIMARY KEY, n_nodes INTEGER, n_edges INTEGER, n_clusters INTEGER,
    n_new_edges INTEGER, config_hash TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS universe(
    asof TEXT, ts_code TEXT, name TEXT, industry TEXT,
    PRIMARY KEY(asof, ts_code));
CREATE TABLE IF NOT EXISTS nodes(
    asof TEXT, ts_code TEXT, name TEXT, industry TEXT, in_graph INTEGER,
    degree INTEGER, ret_5 REAL, excess_mkt_5 REAL, resid_cum_5 REAL,
    amount_ratio REAL, ma_stack REAL, features_json TEXT,
    PRIMARY KEY(asof, ts_code));
CREATE TABLE IF NOT EXISTS edges(
    asof TEXT, network TEXT, a TEXT, b TEXT,
    residual_corr_20 REAL, residual_corr_10 REAL, residual_corr_60 REAL,
    rank_corr REAL, corr_stability REAL, corr_dual REAL, raw_corr REAL,
    event_sync REAL, volume_sync REAL, trend_similarity REAL,
    lead_lag_score REAL, lead_lag_best INTEGER, sample_n INTEGER,
    p_value REAL, q_value REAL, edge_reliability REAL, edge_score REAL,
    is_new INTEGER, edge_delta REAL, streak_days INTEGER, persisted_5d INTEGER,
    same_industry INTEGER, evidence_json TEXT, config_hash TEXT,
    PRIMARY KEY(asof, network, a, b));
CREATE INDEX IF NOT EXISTS idx_edges_date ON edges(asof);
CREATE INDEX IF NOT EXISTS idx_edges_pair ON edges(a,b);
CREATE TABLE IF NOT EXISTS clusters(
    asof TEXT, cluster_id TEXT, label TEXT, state TEXT, stage TEXT, grade TEXT,
    n_members INTEGER, density REAL, n_edges INTEGER, leader TEXT,
    linkage_score REAL, freshness REAL, reliability REAL, expansion_score REAL,
    strengthening_score REAL, decay_risk REAL, sample_risk REAL, age_days INTEGER,
    birth_date TEXT, driver_confidence TEXT, driver_label TEXT,
    pre_sector_emergence INTEGER, cross_sector INTEGER, rank_no INTEGER,
    detail_json TEXT, config_hash TEXT,
    PRIMARY KEY(asof, cluster_id));
CREATE INDEX IF NOT EXISTS idx_clusters_date ON clusters(asof);
CREATE TABLE IF NOT EXISTS cluster_members(
    asof TEXT, cluster_id TEXT, ts_code TEXT, role TEXT, stock_name TEXT,
    industry TEXT, excess_mkt_5 REAL, resid_corr_20 REAL, amount_ratio REAL,
    trend TEXT, is_new_member INTEGER,
    PRIMARY KEY(asof, cluster_id, ts_code));
CREATE TABLE IF NOT EXISTS cluster_events(
    asof TEXT, cluster_id TEXT, event_type TEXT, detail_json TEXT,
    PRIMARY KEY(asof, cluster_id, event_type));
CREATE TABLE IF NOT EXISTS stock_events(
    asof TEXT, trade_date TEXT, ts_code TEXT, event_type TEXT, strength REAL,
    amount_ratio REAL, pct_chg REAL, fwd_ret_1 REAL, fwd_ret_3 REAL, fwd_ret_5 REAL,
    PRIMARY KEY(asof, trade_date, ts_code, event_type));
CREATE TABLE IF NOT EXISTS observations(
    asof TEXT, cluster_id TEXT, ts_code TEXT, stock_name TEXT, role TEXT,
    linkage_state TEXT, hvt_bull TEXT, w7 TEXT, e08 TEXT, vsw TEXT,
    conclusion TEXT, note TEXT,
    PRIMARY KEY(asof, cluster_id, ts_code));
"""


def connect(cfg: dict[str, Any]) -> sqlite3.Connection:
    p = Path(cfg["paths"]["linkage_db"])
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init_db(cfg: dict[str, Any]) -> None:
    con = connect(cfg)
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()


def has_snapshot(cfg: dict[str, Any], asof: str) -> bool:
    con = connect(cfg)
    try:
        return con.execute("SELECT 1 FROM snapshots WHERE asof=?", (asof,)).fetchone() is not None
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()


def reset_snapshots(cfg: dict[str, Any]) -> None:
    """清空全部快照（保留 name_history，避免重复构建 PIT 简称库），用于口径变更后完整重放。"""
    con = connect(cfg)
    try:
        con.execute("BEGIN")
        for table in ("run_log", "snapshots", "universe", "nodes", "edges", "clusters",
                      "cluster_members", "cluster_events", "stock_events", "observations"):
            con.execute(f"DELETE FROM {table}")
        con.commit()
    finally:
        con.close()


def delete_snapshot(cfg: dict[str, Any], asof: str) -> None:
    """强制重算前清理当日全部快照数据，避免历史身份/age 被旧结果污染。"""
    con = connect(cfg)
    try:
        con.execute("BEGIN")
        for table in ("snapshots", "universe", "nodes", "edges", "clusters",
                      "cluster_members", "cluster_events", "stock_events", "observations"):
            con.execute(f"DELETE FROM {table} WHERE asof=?", (asof,))
        con.commit()
    finally:
        con.close()


def start_run(cfg: dict[str, Any], asof: str) -> None:
    init_db(cfg)
    con = connect(cfg)
    try:
        con.execute(
            "INSERT OR REPLACE INTO run_log VALUES (?,?,?,?,?,?,?,?,?)",
            (asof, datetime.now().isoformat(timespec="seconds"), None, "RUNNING",
             cfg["_config_hash"], None, None, None, None))
        con.commit()
    finally:
        con.close()


def finish_run(cfg: dict[str, Any], asof: str, status: str, timings: dict,
               quality: dict, net_stats: dict, error: str | None = None) -> None:
    con = connect(cfg)
    try:
        con.execute(
            "UPDATE run_log SET finished_at=?, status=?, timings_json=?, "
            "data_quality_json=?, network_stats_json=?, error=? WHERE asof=?",
            (datetime.now().isoformat(timespec="seconds"), status,
             json.dumps(timings, ensure_ascii=False, default=str),
             json.dumps(quality, ensure_ascii=False, default=str),
             json.dumps(net_stats, ensure_ascii=False, default=str),
             error, asof))
        con.commit()
    finally:
        con.close()


def save_snapshot(cfg: dict[str, Any], asof: str, s: Slice, fs: FeatureSet,
                  net: NetworkResult, cs: ClusterSet, obs: pd.DataFrame,
                  timings: dict, quality: dict) -> None:
    ch = cfg["_config_hash"]
    con = connect(cfg)
    now = datetime.now().isoformat(timespec="seconds")
    try:
        con.execute("BEGIN")
        con.execute("DELETE FROM snapshots WHERE asof=?", (asof,))
        con.execute("DELETE FROM universe WHERE asof=?", (asof,))
        con.execute("DELETE FROM nodes WHERE asof=?", (asof,))
        con.execute("DELETE FROM edges WHERE asof=?", (asof,))
        con.execute("DELETE FROM clusters WHERE asof=?", (asof,))
        con.execute("DELETE FROM cluster_members WHERE asof=?", (asof,))
        con.execute("DELETE FROM cluster_events WHERE asof=?", (asof,))
        con.execute("DELETE FROM stock_events WHERE asof=?", (asof,))
        con.execute("DELETE FROM observations WHERE asof=?", (asof,))

        con.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?)",
                    (asof, s.N, int(len(net.edges)), len(cs.clusters),
                     int(net.stats.get("new_edges", 0)), ch, now))

        con.executemany("INSERT OR REPLACE INTO universe VALUES (?,?,?,?)",
                        [(asof, c, str(s.names[i]), str(s.industry[i])) for i, c in enumerate(s.codes)])

        G = net.graphs.get("market")
        deg = dict(G.degree()) if G is not None else {}
        node_rows = []
        idx = s.code_index()
        for j, c in enumerate(s.codes):
            compact = {k: round(float(fs.l(k)[j]), 5) for k in
                       ("ret_5", "ret_20", "excess_mkt_5", "resid_cum_5", "resid_cum_20",
                        "amount_ratio", "turnover_change", "ma_stack", "ma20_slope",
                        "max_drawdown_20", "vol_up_count_20", "vol_breakout_count_20",
                        "bottom_rising_count_20")}
            node_rows.append((asof, c, str(s.names[j]), str(s.industry[j]),
                              int(deg.get(c, 0) > 0), int(deg.get(c, 0)),
                              compact["ret_5"], compact["excess_mkt_5"], compact["resid_cum_5"],
                              compact["amount_ratio"], compact["ma_stack"],
                              json.dumps(compact, ensure_ascii=False)))
        con.executemany("INSERT OR REPLACE INTO nodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", node_rows)

        ev_base = {
            "model": "causal_rolling_ols_market",
            "windows": [cfg["linkage"][k] for k in ("long_window", "mid_window", "short_window")],
            "thresholds": {"corr20": cfg["linkage"]["corr_threshold_20"],
                           "corr10": cfg["linkage"]["corr_threshold_10"],
                           "min_edge_score": cfg["linkage"]["min_edge_score"],
                           "fdr_alpha": cfg["linkage"]["fdr_alpha"]},
            "config_hash": ch, "asof": asof,
        }
        for network in cfg["linkage"].get("networks", ["market", "market_industry"]):
            e = net.edges if network == "market" else net.edges[
                net.edges["corr_dual"] >= cfg["linkage"].get("min_dual_network_corr", 0.30)] \
                if len(net.edges) else net.edges
            rows = []
            for r in e.itertuples(index=False):
                evidence = dict(ev_base)
                evidence.update({
                    "residual_corr_20": round(float(r.residual_corr_20), 4),
                    "raw_corr": round(float(r.raw_corr), 4),
                    "corr_dual": round(float(r.corr_dual), 4),
                    "sample_n": int(r.sample_n),
                    "q_value": round(float(r.q_value), 6),
                    "corr_delta": round(float(getattr(r, "corr_delta", 0.0)), 4),
                    "null_method": net.stats.get("null_method", "fisher_z_permutation_calibrated"),
                    "null_z_mean": net.stats.get("null_z_mean"),
                    "null_z_sd": net.stats.get("null_z_sd"),
                })
                rows.append((
                    asof, network, r.a, r.b, float(r.residual_corr_20), float(r.residual_corr_10),
                    float(r.residual_corr_60), float(r.rank_corr), float(r.corr_stability),
                    float(r.corr_dual), float(r.raw_corr), float(r.event_sync),
                    float(r.volume_sync), float(r.trend_similarity), float(r.lead_lag_score),
                    int(r.lead_lag_best), int(r.sample_n), float(r.p_value), float(r.q_value),
                    float(r.edge_reliability), float(r.edge_score), int(bool(r.is_new)),
                    float(r.edge_delta), int(r.streak_days),
                    int(bool(getattr(r, "persisted_5d", False))), int(bool(r.same_industry)),
                    json.dumps(evidence, ensure_ascii=False), ch))
            if rows:
                con.executemany("INSERT OR REPLACE INTO edges VALUES (" + ",".join(["?"] * 28) + ")", rows)

        for c in cs.clusters:
            detail = {k: v for k, v in c.items() if k not in
                      ("cluster_id", "asof", "state", "stage", "grade", "n_members",
                       "density", "n_edges", "leader", "linkage_score", "freshness",
                       "reliability", "expansion_score", "strengthening_score",
                       "decay_risk", "sample_risk", "age_days", "birth_date",
                       "driver_confidence", "driver_label", "pre_sector_emergence",
                       "cross_sector", "rank", "metrics")}
            con.execute("INSERT OR REPLACE INTO clusters VALUES (" + ",".join(["?"] * 26) + ")", (
                asof, c["cluster_id"], c.get("driver_label", ""), c["state"], c["stage"],
                c["grade"], c["n_members"], c["density"], c["n_edges"], c["leader"],
                c["linkage_score"], c["freshness"], c["reliability"], c["expansion_score"],
                c["strengthening_score"], c["decay_risk"], c["sample_risk"], c["age_days"],
                c["birth_date"], c["driver_confidence"], c["driver_label"],
                int(bool(c["pre_sector_emergence"])), int(bool(c["cross_sector"])),
                c.get("rank", 0), json.dumps(detail, ensure_ascii=False, default=str), ch))

        if len(cs.members):
            con.executemany(
                "INSERT OR REPLACE INTO cluster_members VALUES (" + ",".join(["?"] * 11) + ")",
                [(asof, row["cluster_id"], row["ts_code"], row["role"],
                  row["stock_name"], row["industry"], row["excess_mkt_5"],
                  row["resid_corr_20"], row["amount_ratio"], row["trend"],
                  int(bool(row["is_new_member"]))) for _, row in cs.members.iterrows()])

        for e in cs.events:
            con.execute("INSERT OR REPLACE INTO cluster_events VALUES (?,?,?,?)",
                        (asof, e["cluster_id"], e["event_type"],
                         json.dumps(e.get("detail", {}), ensure_ascii=False, default=str)))

        # 个股事件：全市场当日 + 群体成员近 20 日（控制体量，保证可追溯）
        cluster_codes = set(cs.members["ts_code"].astype(str)) if len(cs.members) else set()
        ev_rows = [(
            asof, r["trade_date"], r["ts_code"], r["event_type"], r["strength"],
            r["amount_ratio"], r["pct_chg"], r["fwd_ret_1"], r["fwd_ret_3"], r["fwd_ret_5"])
            for r in fs.event_rows
            if r["trade_date"] == asof or r["ts_code"] in cluster_codes]
        if ev_rows:
            con.executemany("INSERT OR REPLACE INTO stock_events VALUES (?,?,?,?,?,?,?,?,?,?)", ev_rows)

        if len(obs):
            con.executemany("INSERT OR REPLACE INTO observations VALUES (" + ",".join(["?"] * 12) + ")",
                            [(asof, row.get("cluster_id", ""), row.get("ts_code", ""),
                              row.get("stock_name", ""), row.get("role", ""),
                              row.get("linkage_state", ""), row.get("HVT-BULL", ""),
                              row.get("W7", ""), row.get("E08", ""), row.get("VSW", ""),
                              row.get("conclusion", ""), row.get("note", ""))
                             for _, row in obs.iterrows()])

        con.execute("UPDATE run_log SET finished_at=?, status=?, timings_json=?, "
                    "data_quality_json=?, network_stats_json=? WHERE asof=?",
                    (now, "OK",
                     json.dumps(timings, ensure_ascii=False, default=str),
                     json.dumps(quality, ensure_ascii=False, default=str),
                     json.dumps(net.stats, ensure_ascii=False, default=str), asof))
        con.commit()
        log.info("快照已存: %s (节点 %d / 边 %d / 群体 %d / 股票池 %d)",
                 asof, s.N, len(net.edges), len(cs.clusters), len(node_rows))
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _edges(con: sqlite3.Connection, date: str | None, network: str = "market") -> pd.DataFrame:
    if not date:
        return pd.DataFrame(columns=["asof", "a", "b", "edge_score", "streak_days"])
    return pd.read_sql_query(
        "SELECT asof,a,b,edge_score,residual_corr_20,streak_days,is_new "
        "FROM edges WHERE asof=? AND network=?", con, params=(date, network))


def _clusters(con: sqlite3.Connection, date: str | None) -> pd.DataFrame:
    if not date:
        return pd.DataFrame(columns=["cluster_id", "ts_code"])
    return pd.read_sql_query(
        "SELECT c.cluster_id, m.ts_code FROM clusters c JOIN cluster_members m "
        "ON c.asof=m.asof AND c.cluster_id=m.cluster_id WHERE c.asof=?", con, params=(date,))


def load_history(cfg: dict[str, Any], dates: list[str], asof_idx: int) -> History:
    """构造昨日 / 5日前 / 10日前 / 20日前 的网络上下文。"""
    con = connect(cfg)
    try:
        def d(back: int) -> str | None:
            i = asof_idx - back
            return dates[i] if i >= 0 else None
        prev, d5, d10, d20 = d(1), d(5), d(10), d(20)
        cur = dates[asof_idx]
        known = con.execute("SELECT DISTINCT asof FROM snapshots WHERE asof < ?", (cur,)).fetchall()
        known_dates = {r[0] for r in known}
        # 严格 as-of：只有早于当前日期的快照才算历史，未来快照绝不参与
        available = bool(known_dates)
        h = History(available=available,
                    prev_clusters=_clusters(con, prev),
                    prev_edges=_edges(con, prev),
                    edges_5d=_edges(con, d5), edges_20d=_edges(con, d20),
                    clusters_5d=_clusters(con, d5), clusters_10d=_clusters(con, d10))
        if available:
            rows = con.execute(
                "SELECT cluster_id, COUNT(DISTINCT asof) n, MIN(asof) first "
                "FROM clusters WHERE asof < ? GROUP BY cluster_id", (cur,)
            ).fetchall()
            h.age = {str(cid): int(n) for cid, n, _ in rows}
            h.cluster_dates = {str(cid): str(first) for cid, _, first in rows}
            freq = con.execute(
                "SELECT cluster_id, ts_code, COUNT(*) n FROM cluster_members "
                "WHERE asof < ? GROUP BY cluster_id, ts_code", (cur,)
            ).fetchall()
            min_ratio = float(cfg["cluster"].get("core_member_min_ratio", 0.60))
            for cid, code, n in freq:
                days = h.age.get(str(cid), 0)
                if days >= 1 and int(n) / days >= min_ratio:
                    h.core_members.setdefault(str(cid), set()).add(str(code))
        # 时间平滑图：只用严格早于 asof 的历史边，禁止引入未来快照
        sm = cfg["cluster"].get("temporal_smoothing", {})
        if sm.get("enabled", False):
            for k, decay in enumerate(sm.get("decay", [0.45, 0.25, 0.12]), start=1):
                ed = _edges(con, d(k))
                if len(ed):
                    h.smooth_edges.append((float(decay), ed))
        # 群体身份的历史边锚点：把两端都在同一群体成员集内的当日边归属该 id
        for mdate, medges in ((prev, h.prev_edges), (d5, h.edges_5d),
                              (d10, _edges(con, d10))):
            if mdate is None or medges is None or not len(medges):
                continue
            frame = _clusters(con, mdate)
            if not len(frame):
                continue
            # 一次遍历所有边：用「股票 -> 所属群体」映射求交集归属，
            # 避免 群体数 × 边数 的 Python 双重循环拖慢每日回放。
            code2cid: dict[str, set[str]] = {}
            for cid, code in zip(frame["cluster_id"].astype(str),
                                 frame["ts_code"].astype(str)):
                code2cid.setdefault(code, set()).add(cid)
            for a, b in zip(medges["a"].astype(str), medges["b"].astype(str)):
                common = code2cid.get(a, set()) & code2cid.get(b, set())
                if not common:
                    continue
                k = (a, b) if a < b else (b, a)
                for cid in common:
                    bucket = h.cluster_edges.setdefault(cid, set())
                    bucket.add(k)
                    bucket.add((k[1], k[0]))
        if h.prev_edges is not None and len(h.prev_edges):
            h.prev_edge_pairs = {(a, b) if a < b else (b, a)
                                 for a, b in zip(h.prev_edges["a"].astype(str),
                                                 h.prev_edges["b"].astype(str))}
        h.prev_edges = h.prev_edges if len(h.prev_edges) else None
        h.edges_5d = h.edges_5d if len(h.edges_5d) else None
        h.edges_20d = h.edges_20d if len(h.edges_20d) else None
        h.prev_clusters = h.prev_clusters if len(h.prev_clusters) else None
        h.clusters_5d = h.clusters_5d if len(h.clusters_5d) else None
        h.clusters_10d = h.clusters_10d if len(h.clusters_10d) else None
        return h
    finally:
        con.close()


def load_edges(cfg: dict[str, Any], date: str, network: str = "market") -> pd.DataFrame:
    con = connect(cfg)
    try:
        return _edges(con, date, network)
    finally:
        con.close()
