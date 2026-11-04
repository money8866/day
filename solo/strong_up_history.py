# -*- coding: utf-8 -*-
"""强势股次日上涨比（strong_up）的历史序列与连续性分级。

指标定义（与 _intraday_monitor.py / market_regime_v10.py 完全一致）::

    strong_up(T) = T-1 日涨停名单中，在 T 日 pct_chg > 0 的比例（百分数 0~100）

历史来源：``cache_daily/stock_data.db`` 的 ``daily_cache`` 日线，按同一套涨停阈值重算
（已实测对齐盘后报告：20261102 = 67.4%、20261103 = 49.2%）。
盘中当日值由 ``_intraday_monitor`` 通过 ``sync()`` 写入——收盘前 daily_cache 没有当日行。

分级（阈值全部走 config_v101_position.json → money_effect.strong_up_continuity）::

    ALERT   当日 < alert_below(35%)        强势股负反馈警报      ← 最高优先级
    BOOST   含当日连续 boost_days(3) 日 ≥ boost_min(55%)   确认短线赚钱效应增强
    REPAIR  含当日连续 repair_days(2) 日 ≥ repair_min(50%)  确认修复
    PASS    当日 ≥ pass_min(50%)           通过
    FAIL    其余                          未过

★ 设计边界：分级是**信号层**，不改变 ``money_pass`` 的布尔判定与仓位体系。
  通过线仍为 ≥50%，避免未经回测就改动 V10.1 权限表。待分级信号完成历史回测后，
  可在 ``_intraday_monitor.analyze()`` 里把 ALERT / BOOST 显式接入仓位。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_DB = _HERE.parent / "cache_daily" / "stock_data.db"
_CACHE = _HERE / "cache_strongup" / "strong_up_history.json"

try:
    from market_regime_v10 import _limit_thresholds as _thr
except Exception:  # 兜底：与 v10 保持同一套判定，不依赖其导入副作用
    def _thr(ts_code: str) -> float:
        code = str(ts_code)
        return 20.0 if code.startswith(("688", "300", "301", "8", "92")) else 10.0


# ---------------------------------------------------------------- 缓存 IO
def _read() -> dict:
    try:
        return json.loads(_CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(hist: dict) -> None:
    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(json.dumps(hist, ensure_ascii=False, indent=1,
                                     sort_keys=True), encoding="utf-8")
    except Exception:
        pass  # 缓存写失败不阻断主流程，分级会退化为「仅当日」


# ---------------------------------------------------------------- 交易日历
def trading_calendar(end_date: str, lookback: int = 40) -> list:
    """权威交易日序列（daily_cache 的 distinct trade_date），末尾补上盘中当日。"""
    end_date = str(end_date)
    try:
        with sqlite3.connect(str(_DB)) as c:
            ds = [r[0] for r in c.execute(
                "select distinct trade_date from daily_cache "
                "where trade_date <= ? order by trade_date", (end_date,))]
    except Exception:
        ds = []
    ds = [str(d) for d in ds]
    if not ds or ds[-1] != end_date:
        ds.append(end_date)          # 收盘前当日尚未入库
    return ds[-lookback:]


# ---------------------------------------------------------------- 历史回填
def _backfill(cal: list) -> dict:
    """一次性读入窗口内日线，按 T-1 涨停名单重算每日 strong_up。"""
    import pandas as pd
    if len(cal) < 2:
        return {}
    with sqlite3.connect(str(_DB)) as c:
        df = pd.read_sql(
            "select ts_code, trade_date, pct_chg from daily_cache "
            "where trade_date between ? and ?", c, params=(cal[0], cal[-1]))
    if df.empty:
        return {}
    df["trade_date"] = df["trade_date"].astype(str)
    by_day = {d: g for d, g in df.groupby("trade_date")}
    out = {}
    for i in range(1, len(cal)):
        prev, cur = cal[i - 1], cal[i]
        a, b = by_day.get(prev), by_day.get(cur)
        if a is None or b is None:
            continue
        t = a["ts_code"].map(_thr)
        pct = a["pct_chg"].astype(float)
        zt = set(a.loc[(pct >= t * 0.99) & (pct < t + 0.1), "ts_code"])
        if not zt:
            continue
        sub = b[b["ts_code"].isin(zt)]["pct_chg"].astype(float)
        if len(sub):
            out[cur] = round(float((sub > 0).mean() * 100), 2)
    return out


def sync(trade_date: str, today_value, lookback: int = 40) -> None:
    """回填缺失历史 + 记录盘中当日值。同一天多次调用幂等（当日值覆盖为最新）。"""
    trade_date = str(trade_date)
    cal = trading_calendar(trade_date, lookback)
    hist = _read()
    # 只补算缺 close 的历史日（当日无日线，由下面的 intraday 覆盖）
    missing = [d for d in cal if d < trade_date
               and (hist.get(d) or {}).get("close") is None]
    if missing:
        for d, v in _backfill(cal).items():
            hist.setdefault(d, {})["close"] = v
    if today_value is not None:
        hist.setdefault(trade_date, {})["intraday"] = round(float(today_value), 2)
    _write(hist)


def series(trade_date: str, lookback: int = 5) -> list:
    """最近 lookback 个交易日的 [(date, value), ...]；当日优先用盘中值，缺值为 None。"""
    trade_date = str(trade_date)
    cal = trading_calendar(trade_date, 40)[-lookback:]
    hist = _read()
    out = []
    for d in cal:
        rec = hist.get(d) or {}
        v = (rec.get("intraday") if d == trade_date else None)
        if v is None:
            v = rec.get("close")
        out.append((d, v))
    return out


# ---------------------------------------------------------------- 分级判定
def _streak(seq: list, threshold: float) -> int:
    """从当日往前数「连续 ≥ threshold」的天数；缺值或不满足即中断。"""
    n = 0
    for _, v in reversed(seq):
        if v is not None and v >= threshold:
            n += 1
        else:
            break
    return n


def _cfg_grade(cfg: dict) -> dict:
    m = (cfg or {}).get("strong_uptrend", {}).get("money_effect", {})
    g = m.get("strong_up_continuity") or {}
    return {
        # 通过线单一事实来源：沿用 money_effect.strong_stock_up_ratio_min（0~1 → 百分数）
        "pass_min": float(m.get("strong_stock_up_ratio_min", 0.5) * 100),
        "repair_days": int(g.get("repair_days", 2)),
        "repair_min": float(g.get("repair_min", 50.0)),
        "boost_days": int(g.get("boost_days", 3)),
        "boost_min": float(g.get("boost_min", 55.0)),
        "alert_below": float(g.get("alert_below", 35.0)),
    }


def classify(trade_date: str, today_value, cfg: dict, seq: list = None) -> dict:
    """返回第四项的连续性分级。today_value 为百分数（0~100）。"""
    g = _cfg_grade(cfg)
    if today_value is None:
        return {"grade": "NA", "label": "强股上涨比不可用", "alert": False,
                "streak_repair": 0, "streak_boost": 0, "recent": [],
                "recent_str": "—", "thresholds": g}
    seq = seq if seq is not None else series(trade_date, 5)
    # 当日值直接采用调用方算出的实时值，不依赖缓存写入时序
    if seq and today_value is not None:
        seq = list(seq)
        seq[-1] = (seq[-1][0], float(today_value))

    s_repair = _streak(seq, g["repair_min"])
    s_boost = _streak(seq, g["boost_min"])

    if today_value is not None and today_value < g["alert_below"]:
        grade, label = "ALERT", "强势股负反馈警报"
    elif s_boost >= g["boost_days"]:
        grade, label = "BOOST", "确认短线赚钱效应增强"
    elif s_repair >= g["repair_days"]:
        grade, label = "REPAIR", "确认修复"
    elif today_value is not None and today_value >= g["pass_min"]:
        grade, label = "PASS", "通过"
    else:
        grade, label = "FAIL", "未过"

    recent = [(d, v) for d, v in seq[-3:]]
    return {
        "grade": grade, "label": label,
        "alert": grade == "ALERT",
        "streak_repair": s_repair, "streak_boost": s_boost,
        "recent": recent,
        "recent_str": " → ".join(f"{v:.1f}%" if v is not None else "—"
                                 for _, v in recent),
        "thresholds": g,
    }


def fmt_recent(gr: dict) -> str:
    """近 3 日序列的可读串，用于报告 / 微信。"""
    return " → ".join(f"{v:.1f}%" if v is not None else "—"
                      for _, v in gr.get("recent", []))
