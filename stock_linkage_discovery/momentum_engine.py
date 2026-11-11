"""Momentum Magnitude Engine —— 联动个股涨幅幅度识别（独立模块）。

职责边界：
  本模块**只描述位置**，不判断买点、不改变关联网络与群体成员。
  输入是已完成社区发现的 ClusterSet + 个股特征，输出成员/群体两层的涨幅幅度指标、
  分层标签、启动确认与透支风险，供日报、观察池与回测分层消费。

为什么不把「涨幅足够大」直接做成机会过滤：
  2026-07-01~11-06 的 507 个事件实测，群体 5 日平均涨幅与 T+10 超额显著负相关
  （样本内 Spearman=-0.184, p=0.0002；样本外方向一致）。涨幅越大的联动群体越接近
  末端，涨幅温和或刚回调过的群体反而有正超额。因此：
    - 默认 `strict_filter=false`：幅度仅作为标签 + 风险提示，评级不变；
    - `extended`（已涨透）作为降级/排除买点的风险信号；
    - 用户显式打开严格过滤时才按「有一定幅度」硬筛，并在日报显著标注非默认口径。

复权口径：`fs.l("ret_5"/"ret_10"/"ret_20")` 为截至 asof 的复权累计收益（小数），
全部来自严格 as-of 的特征引擎，无未来数据。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .cluster_engine import ClusterSet
from .common import get_logger
from .data_layer import Slice
from .feature_engine import FeatureSet

log = get_logger("momentum")

DEFAULT_CONFIG_PATH = Path(__file__).with_name("momentum_config.json")

MEMBER_TAGS = ("EXTENDED", "MOVED", "PULLED_BACK", "FLAT")
CLUSTER_STATES = ("DEEP_PULLBACK", "PULLBACK", "FLAT", "EARLY_MOVE", "EXTENDED")


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    cfg = json.loads(p.read_text(encoding="utf-8"))
    cfg["_config_path"] = str(p)
    return cfg


def member_tag(move_5: float, move_10: float, move_20: float,
               mcfg: dict[str, Any]) -> str:
    """成员幅度标签（纯函数，便于单测）。"""
    if (move_20 >= float(mcfg.get("extended_leader_move_20", 0.80))
            or move_20 >= float(mcfg.get("extended_member_move_20", 0.60))
            or move_5 >= 0.25):
        return "EXTENDED"
    if (move_5 >= float(mcfg.get("min_member_move_5", 0.05))
            or move_10 >= float(mcfg.get("min_member_move_10", 0.08))):
        return "MOVED"
    if move_5 <= -0.05:
        return "PULLED_BACK"
    return "FLAT"


def cluster_state(mean_5: float, mean_20: float, extended_ratio: float,
                  mcfg: dict[str, Any]) -> str:
    """群体位置分层（纯函数，便于单测）。分桶口径与回测分层一致。"""
    hi5 = float(mcfg.get("extended_mean_move_5", 0.05))
    hi20 = float(mcfg.get("max_healthy_mean_move_20", 0.30))
    if mean_5 >= hi5 or mean_20 >= hi20 or extended_ratio >= 0.5:
        return "EXTENDED"
    if mean_5 < float(mcfg.get("min_healthy_mean_move_5", -0.10)):
        return "DEEP_PULLBACK"
    if mean_5 < 0:
        return "PULLBACK"
    if mean_5 >= float(mcfg.get("early_move_min_5", 0.02)):
        return "EARLY_MOVE"
    return "FLAT"


def _f(x: Any) -> float:
    try:
        v = float(x)
        return v if np.isfinite(v) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def _series(fs: FeatureSet, key: str, n: int) -> np.ndarray:
    v = np.asarray(fs.l(key), dtype=np.float64)
    return v if len(v) == n else np.zeros(n, dtype=np.float64)


def attach(cs: ClusterSet, s: Slice, fs: FeatureSet,
           mcfg: dict[str, Any]) -> ClusterSet:
    """把成员/群体两层幅度指标写回 ClusterSet（原地修改并返回）。"""
    if not mcfg.get("enabled", True) or not len(cs.members) or not len(cs.clusters):
        return cs
    idx = {str(c): i for i, c in enumerate(s.codes)}
    m5, m10, m20 = (_series(fs, f"ret_{w}", s.N) for w in (5, 10, 20))

    # ---------------- 成员层 ----------------
    tags: list[str] = []
    col5: list[float] = []
    col10: list[float] = []
    col20: list[float] = []
    for r in cs.members.itertuples(index=False):
        j = idx.get(str(r.ts_code))
        v5, v10, v20 = (float(m5[j]), float(m10[j]), float(m20[j])) if j is not None else (0.0, 0.0, 0.0)
        col5.append(round(v5, 4)); col10.append(round(v10, 4)); col20.append(round(v20, 4))
        tags.append(member_tag(v5, v10, v20, mcfg))
    cs.members["move_5"] = col5
    cs.members["move_10"] = col10
    cs.members["move_20"] = col20
    cs.members["move_tag"] = tags

    # ---------------- 群体层 ----------------
    grouped = {str(k): v for k, v in cs.members.groupby("cluster_id")}
    state_counts: dict[str, int] = {k: 0 for k in CLUSTER_STATES}
    n_confirmed = n_extended = n_healthy = 0
    for c in cs.clusters:
        cid = str(c["cluster_id"])
        sub = grouped.get(cid)
        if sub is None or not len(sub):
            c.update({"move_state": "UNKNOWN", "move_confirmed": False,
                      "move_healthy": False, "mean_move_5": 0.0,
                      "move_failed_checks": ["无成员数据"]})
            continue
        a5 = np.asarray(sub["move_5"], dtype=float)
        a20 = np.asarray(sub["move_20"], dtype=float)
        mean5, mean10, mean20 = (float(np.mean(a5)),
                                 float(np.mean(np.asarray(sub["move_10"], dtype=float))),
                                 float(np.mean(a20)))
        tag_arr = list(sub["move_tag"])
        moved = int(sum(t in ("MOVED", "EXTENDED") for t in tag_arr))
        extended = int(sum(t == "EXTENDED" for t in tag_arr))
        ratio = moved / max(1, len(sub))
        leader = str(c.get("leader", ""))
        lrow = sub[sub["ts_code"].astype(str) == leader]
        leader5 = float(lrow["move_5"].iloc[0]) if len(lrow) else float(a5.max())
        others = sub[sub["ts_code"].astype(str) != leader]
        gap = leader5 - (float(np.mean(np.asarray(others["move_5"], dtype=float)))
                         if len(others) else 0.0)

        failed: list[str] = []
        if moved < int(mcfg.get("min_moved_stocks", 3)) and ratio < float(mcfg.get("min_moved_ratio", 0.50)):
            failed.append(f"有幅度成员仅 {moved}/{len(sub)} 只")
        if mean5 < float(mcfg.get("min_cluster_mean_move_5", 0.03)):
            failed.append(f"群体 5 日均值 {_pct(mean5)} 未达启动幅度")
        if gap < float(mcfg.get("min_leader_follower_gap_5", 0.03)):
            failed.append(f"领涨领先仅 {_pct(gap)}，缺领涨-跟随梯度")
        confirmed = not failed

        ext_ratio = extended / max(1, len(sub))
        state = cluster_state(mean5, mean20, ext_ratio, mcfg)
        # 领涨股自身涨透时不因群体均值温和而漏判
        if leader in set(sub["ts_code"].astype(str)):
            lj = sub[sub["ts_code"].astype(str) == leader].index[0]
            if str(sub.loc[lj, "move_tag"]) == "EXTENDED" and state != "EXTENDED":
                state = "EXTENDED"
        healthy = (float(mcfg.get("min_healthy_mean_move_5", -0.10)) <= mean5
                   <= float(mcfg.get("max_healthy_mean_move_5", 0.03))
                   and mean20 <= float(mcfg.get("max_healthy_mean_move_20", 0.30))
                   and state in ("PULLBACK", "FLAT", "EARLY_MOVE"))

        note = (f"5日均值 {_pct(mean5)} / 10日 {_pct(mean10)} / 20日 {_pct(mean20)}；"
                f"有幅度成员 {moved}/{len(sub)}，透支 {extended} 只；"
                f"领涨领先 {_pct(gap)}；位置 {state}")
        c.update({
            "move_state": state,
            "move_confirmed": bool(confirmed),
            "move_healthy": bool(healthy),
            "move_failed_checks": failed,
            "mean_move_5": round(mean5, 4),
            "mean_move_10": round(mean10, 4),
            "mean_move_20": round(mean20, 4),
            "moved_stocks": moved,
            "moved_ratio": round(ratio, 4),
            "extended_stocks": extended,
            "leader_move_5": round(leader5, 4),
            "leader_follower_gap_5": round(gap, 4),
            "move_note": note,
        })
        state_counts[state] = state_counts.get(state, 0) + 1
        n_confirmed += int(confirmed)
        n_extended += int(state == "EXTENDED")
        n_healthy += int(healthy)

    cs.stats.update({
        "move_state_counts": state_counts,
        "move_confirmed_clusters": n_confirmed,
        "move_extended_clusters": n_extended,
        "move_healthy_clusters": n_healthy,
        "move_strict_filter": bool(mcfg.get("strict_filter", False)),
        "move_strict_would_drop": sum(1 for c in cs.clusters
                                      if not c.get("move_confirmed", False)
                                      and c.get("grade") in ("S", "A"))
        if mcfg.get("strict_filter", False) else 0,
    })
    cs.flags["MOMENTUM_STRICT_FILTER"] = bool(mcfg.get("strict_filter", False))
    log.info("涨幅幅度: 群体 %d | 有幅度确认 %d | 位置健康 %d | 已透支 %d | 严格过滤 %s",
             len(cs.clusters), n_confirmed, n_healthy, n_extended,
             "开" if mcfg.get("strict_filter") else "关（仅标签）")
    return cs
