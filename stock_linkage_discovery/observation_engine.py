"""Trading Observation Engine（第十二层）—— 与既有策略交叉验证。

本模块不直接产生 BUY 信号。HVT-BULL / W7 / E08 / VSW 负责判断买点，
联动发现只负责找机会。即使出现 LINKAGE_ENTRY，也必须遵守原策略的止损与风控。

信号来源：solo/picks_db/stock_picks.db 的 stock_pick 表（既有策略的真实落库结果）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from .common import get_logger
from .cluster_engine import ClusterSet

log = get_logger("observation_engine")

STRATEGIES = ("HVT-BULL", "W7", "E08", "VSW")
EMPTY = pd.DataFrame(columns=[
    "asof", "cluster_id", "ts_code", "stock_name", "role", "linkage_state",
    "HVT-BULL", "W7", "E08", "VSW", "conclusion", "note",
    "move_5", "move_10", "move_20", "move_tag", "move_state",
])


def _load_signals(cfg: dict[str, Any], asof: str, dates: list[str]) -> dict[str, dict[str, str]]:
    ocfg = cfg["observation"]
    path = Path(cfg["paths"]["picks_db"])
    if not path.exists():
        log.warning("策略信号库不存在: %s", path)
        return {}
    look = int(ocfg.get("signal_lookback_days", 5))
    i = dates.index(asof) if asof in dates else len(dates) - 1
    day_set = set(dates[max(0, i - look + 1): i + 1])
    q = "SELECT pick_date,strategy_id,ts_code,signal FROM stock_pick WHERE pick_date>=? AND pick_date<=?"
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
        try:
            df = pd.read_sql_query(q, con, params=(min(day_set) if day_set else asof, asof))
        finally:
            con.close()
    except Exception as e:
        log.warning("读取策略信号失败: %s", e)
        return {}
    mapping = ocfg.get("strategy_ids", {})
    code2strategy: dict[str, set[str]] = {}
    for name, ids in mapping.items():
        for sid in ids:
            code2strategy.setdefault(str(sid), set()).add(name)
    out: dict[str, dict[str, str]] = {}
    for r in df.itertuples(index=False):
        if str(r.pick_date) not in day_set:
            continue
        for name in code2strategy.get(str(r.strategy_id), set()):
            cell = f"{r.pick_date}:{r.signal}"
            d = out.setdefault(str(r.ts_code), {})
            if name not in d or str(r.pick_date) > d[name][:8]:
                d[name] = cell
    return out


def _pick_watch(members: pd.DataFrame, max_n: int) -> list[dict]:
    """每群体最多 3 只：领涨核心、趋势稳定跟随、尚未充分上涨但改善的滞后股。"""
    if not len(members):
        return []
    picked: list[dict] = []
    for role in ("LEADER", "CORE"):
        sub = members[members["role"] == role]
        if len(sub):
            picked.append(sub.iloc[0].to_dict())
            break
    fol = members[members["role"] == "FOLLOWER"].copy()
    lag = members[members["role"] == "LAGGARD"].copy()
    if len(fol):
        # 位置优先：涨幅已透支的跟随股排在尚未充分释放的股票之后
        fol["_ext"] = (fol.get("move_tag", pd.Series("", index=fol.index)) == "EXTENDED").astype(int)
        picked.append(fol.sort_values(["_ext", "resid_corr_20"],
                                      ascending=[True, False]).iloc[0].to_dict())
    if len(lag):
        lag["_ext"] = (lag.get("move_tag", pd.Series("", index=lag.index)) == "EXTENDED").astype(int)
        picked.append(lag.sort_values(["_ext", "amount_ratio"],
                                      ascending=[True, False]).iloc[0].to_dict())
    seen, out = set(), []
    for p in picked:
        if p["ts_code"] in seen:
            continue
        seen.add(p["ts_code"])
        out.append(p)
        if len(out) >= max_n:
            break
    return out


def cross_validate(cs: ClusterSet, cfg: dict[str, Any], asof: str,
                   dates: list[str]) -> pd.DataFrame:
    ocfg = cfg["observation"]
    mcfg = cfg.get("_momentum") or {}
    if not ocfg.get("enabled", True):
        return EMPTY.copy()
    signals = _load_signals(cfg, asof, dates)
    maxn = int(ocfg.get("max_watch_stocks_per_cluster", 3))
    rows: list[dict[str, Any]] = []
    cluster_state = {c["cluster_id"]: c for c in cs.clusters}
    for cid, grp in cs.members.groupby("cluster_id") if len(cs.members) else []:
        c = cluster_state.get(cid, {})
        for p in _pick_watch(grp, maxn):
            code = str(p["ts_code"])
            sig = signals.get(code, {})
            has_recent = any(sig.get(k) for k in STRATEGIES)
            state = c.get("state", "")
            grade = c.get("grade", "C")
            improving = ("修复" in str(p.get("trend", "")) or "多头" in str(p.get("trend", ""))
                         or float(p.get("excess_mkt_5", 0)) > 0)
            tag = str(p.get("move_tag", ""))
            move5 = float(p.get("move_5", 0) or 0)
            move_state = str(c.get("move_state", ""))
            strict_miss = (bool(mcfg.get("strict_filter", False))
                           and not c.get("move_confirmed", True)
                           and bool(mcfg.get("strict_exclude_from_watch", True)))
            if grade == "C" or state in ("DECAYING_CLUSTER", "OVERHEATED_CLUSTER"):
                conclusion, note = "LINKAGE_INVALID", "联动衰减/过热或群体失效，不进入观察池"
            elif strict_miss:
                conclusion, note = "LINKAGE_INVALID", "严格幅度口径：群体未达到启动幅度确认，退出观察池"
            elif tag == "EXTENDED":
                conclusion, note = "LINKAGE_WATCH", (
                    "个股涨幅已透支（20日涨幅过大 / 领涨已过热），不升级为买点，仅保留位置观察")
            elif has_recent:
                conclusion, note = "LINKAGE_ENTRY", "联动与既有买点同时满足；仍须遵守原策略止损与风控"
            elif improving:
                conclusion, note = "LINKAGE_SETUP", "联动有效且技术形态改善，等待既有策略确认"
            else:
                conclusion, note = "LINKAGE_WATCH", "仅联动观察，等待回踩或策略信号"
            if move_state == "EXTENDED" and conclusion not in ("LINKAGE_INVALID", "LINKAGE_WATCH"):
                note += "；所在群体整体涨幅已透支，需防追高"
            rows.append({
                "asof": asof, "cluster_id": cid, "ts_code": code,
                "stock_name": p.get("stock_name", ""), "role": p.get("role", ""),
                "linkage_state": state,
                "HVT-BULL": sig.get("HVT-BULL", ""), "W7": sig.get("W7", ""),
                "E08": sig.get("E08", ""), "VSW": sig.get("VSW", ""),
                "conclusion": conclusion, "note": note,
                "move_5": round(move5, 4), "move_10": round(float(p.get("move_10", 0) or 0), 4),
                "move_20": round(float(p.get("move_20", 0) or 0), 4),
                "move_tag": tag, "move_state": move_state,
            })
    out = pd.DataFrame(rows) if rows else EMPTY.copy()
    log.info("策略交叉: %d 只观察股, 命中既有信号 %d 只",
             len(out), int(sum(bool(signals.get(str(r.ts_code))) for r in out.itertuples())) if len(out) else 0)
    return out
