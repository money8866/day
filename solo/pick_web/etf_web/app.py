# -*- coding: utf-8 -*-
"""ETF 每日复盘服务（FastAPI，只读 position_gate 输出目录）。

数据源（全部只读，本服务不写任何文件，不影响盘后分析脚本）：
  csi2000_position_gate_daily.csv   每日核心输出（§28 规格列）
  csi2000_position_gate_events.csv  逐日事件（状态切换 / 加减仓 / Risk-Off / 背离）
  csi2000_etf_trade_plan.json       最新 ETF 执行计划

页面：/ 日期清单 → 点某日看该日仓位的文字说明与图示。
本服务只做「读出来 + 摆出来」，不改写、不重算 Gate 信号；仅由日线列派生
MA60 方向（ma60/ma60[t-20]-1，与 Gate 口径一致）与当日涨跌幅用于展示。

用法：
    python app.py --data ..\\..\\position_gate\\output
    python app.py --data /var/www/EtfWeb/data --host 127.0.0.1 --port 8005
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA = os.path.normpath(
    os.path.join(BASE_DIR, "..", "..", "position_gate", "output"))

DAILY_FILE = "csi2000_position_gate_daily.csv"
EVENTS_FILE = "csi2000_position_gate_events.csv"
PLAN_FILE = "csi2000_etf_trade_plan.json"

SERIES_DAYS = 60            # 详情页图示回看窗口（交易日）
MA60_SLOPE_LOOKBACK = 20    # MA60 方向的回看步长（与 Gate 的 ma60_slope_20 一致）
POSITION_STEP_PCT = 5.0     # 仓位展示量化步长
STEP_UP = 10.0              # §15 单日最大加仓幅度

DAILY_FLOAT_COLS = (
    "csi2000_close", "ma20", "ma60", "ma20_slope", "breadth_ma20",
    "breadth_change_5", "new_high_ratio", "new_low_ratio",
    "rs20_vs_all", "rs20_vs_1000", "volume_ratio",
    "position_min", "position_target", "position_max", "position",
)

# §11/§12 六档状态区间（%）。仅用于图示与「下一档」换算；
# 分析侧的唯一真值在 position_gate/config.py，此处为展示用副本。
REGIME_BANDS = [
    {"regime": "OFF",          "min": 0.0,  "target": 5.0,  "max": 15.0},
    {"regime": "RECOVERY",     "min": 15.0, "target": 20.0, "max": 30.0},
    {"regime": "RANGE",        "min": 25.0, "target": 35.0, "max": 45.0},
    {"regime": "EXPANSION",    "min": 40.0, "target": 50.0, "max": 65.0},
    {"regime": "TREND",        "min": 60.0, "target": 70.0, "max": 85.0},
    {"regime": "STRONG_TREND", "min": 80.0, "target": 90.0, "max": 100.0},
]

REGIME_CN = {
    "OFF": "风险关闭",
    "RECOVERY": "修复",
    "RANGE": "震荡",
    "EXPANSION": "扩张",
    "TREND": "趋势",
    "STRONG_TREND": "强趋势",
}

WEEK_CN = "一二三四五六日"


# ══════════════════════════════════════════════════════════════════════
# 数据读取（带 mtime 热加载缓存）
# ══════════════════════════════════════════════════════════════════════
def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_daily_cache: dict = {"mtime": 0.0, "rows": []}
_events_cache: dict = {"mtime": 0.0, "map": {}}
_plan_cache: dict = {"mtime": 0.0, "data": None}


def load_daily(data_dir: str) -> list[dict]:
    path = os.path.join(data_dir, DAILY_FILE)
    mt = _mtime(path)
    if mt and mt != _daily_cache["mtime"]:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        for r in rows:
            for k in DAILY_FLOAT_COLS:
                r[k] = _f(r.get(k))
            r["riskoff_count"] = int(_f(r.get("riskoff_count")) or 0)
            r["full_exposure_eligible"] = str(
                r.get("full_exposure_eligible", "")).strip().lower() in ("true", "1", "yes")
        rows.sort(key=lambda r: r["date"])
        _daily_cache.update(mtime=mt, rows=rows)
    elif not mt:
        _daily_cache.update(mtime=0.0, rows=[])
    return _daily_cache["rows"]


def load_events(data_dir: str) -> dict:
    """date -> [{event, detail}]"""
    path = os.path.join(data_dir, EVENTS_FILE)
    mt = _mtime(path)
    if mt != _events_cache["mtime"]:
        m: dict = {}
        if mt:
            with open(path, encoding="utf-8-sig", newline="") as fh:
                for r in csv.DictReader(fh):
                    m.setdefault(r.get("date", ""), []).append({
                        "event": r.get("event", ""), "detail": r.get("detail", "")})
        _events_cache.update(mtime=mt, map=m)
    return _events_cache["map"]


def load_plan(data_dir: str) -> dict | None:
    path = os.path.join(data_dir, PLAN_FILE)
    mt = _mtime(path)
    if mt != _plan_cache["mtime"]:
        data = None
        if mt:
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                data = None
        _plan_cache.update(mtime=mt, data=data)
    return _plan_cache["data"]


# ══════════════════════════════════════════════════════════════════════
# 展示派生
# ══════════════════════════════════════════════════════════════════════
def _quantize(p: float) -> float:
    return round(p / POSITION_STEP_PCT) * POSITION_STEP_PCT


def _band_of(regime: str) -> dict | None:
    for b in REGIME_BANDS:
        if b["regime"] == regime:
            return b
    return None


def _arrow(up: bool) -> str:
    return "↑" if up else "↓"


def day_row(rows: list[dict], date: str | None) -> dict | None:
    if not rows:
        return None
    if not date:
        return rows[-1]
    for r in rows:
        if r["date"] == date:
            return r
    return None


def build_blocks(r: dict, rows: list[dict], i: int, events: list[dict]) -> list[dict]:
    """把该交易日的仓位判断拆成便于前端渲染的文字块（对齐 §29 格式）。"""
    prev = rows[i - 1] if i > 0 else None
    ma60_slope = None
    if i >= MA60_SLOPE_LOOKBACK:
        base = rows[i - MA60_SLOPE_LOOKBACK].get("ma60")
        if base and r.get("ma60"):
            ma60_slope = (r["ma60"] / base - 1) * 100
    chg = None
    if prev and prev.get("csi2000_close"):
        chg = (r["csi2000_close"] / prev["csi2000_close"] - 1) * 100

    pos, pmin = r.get("position"), r.get("position_min")
    ptgt, pmax = r.get("position_target"), r.get("position_max")
    full = bool(r.get("full_exposure_eligible"))
    ev_names = {e["event"] for e in events}
    div = "INDEX_BREADTH_DIVERGENCE" in ev_names
    pull = "HEALTHY_PULLBACK" in ev_names

    band = _band_of(str(r.get("regime")))
    blocks = []

    blocks.append({"kind": "kv", "title": "仓位", "items": [
        ["市场状态", "%s（%s）" % (r.get("regime"), REGIME_CN.get(str(r.get("regime")), "-"))],
        ["当前仓位", "%.0f%%" % (pos or 0)],
        ["允许区间", "%.0f%%～%.0f%%" % (pmin or 0, pmax or 0)],
        ["目标中枢", "%.0f%%" % (ptgt or 0)],
        ["满仓资格", "YES" if full else "NO"],
        ["当日涨跌", ("%+.2f%%" % chg) if chg is not None else "-"],
    ]})

    blocks.append({"kind": "lines", "title": "趋势", "lines": [
        "Close vs MA20：%s" % _arrow((r.get("csi2000_close") or 0) > (r.get("ma20") or 0)),
        "MA20：%s（5日斜率 %+.2f）" % (_arrow((r.get("ma20_slope") or 0) > 0),
                                      r.get("ma20_slope") or 0.0),
        "MA60：%s%s" % (_arrow((ma60_slope or 0) > 0),
                        "" if ma60_slope is None else "（20日斜率 %+.2f%%）" % ma60_slope),
    ]})

    blocks.append({"kind": "lines", "title": "宽度", "lines": [
        "MA20 上方比例：%.1f%%" % (r.get("breadth_ma20") or 0.0),
        "5日变化：%+.1fpp" % (r.get("breadth_change_5") or 0.0),
        "新高 / 新低比例：%.2f%% / %.2f%%" % (r.get("new_high_ratio") or 0.0,
                                            r.get("new_low_ratio") or 0.0),
    ]})

    blocks.append({"kind": "lines", "title": "相对强度", "lines": [
        "RS20 vs 全A：%s（%+.2fpp）" % ("+" if (r.get("rs20_vs_all") or 0) > 0 else "-",
                                       r.get("rs20_vs_all") or 0.0),
        "RS20 vs 中证1000：%s（%+.2fpp）" % ("+" if (r.get("rs20_vs_1000") or 0) > 0 else "-",
                                            r.get("rs20_vs_1000") or 0.0),
    ]})

    vr = r.get("volume_ratio") or 0.0
    blocks.append({"kind": "lines", "title": "量价", "lines": [
        "量比（20日）：%.2f（%s）" % (vr, "放量" if vr >= 1 else "缩量"),
        "价格方向：%s" % ("↑" if (chg or 0) > 0 else "↓" if chg is not None else "-"),
    ]})

    blocks.append({"kind": "lines", "title": "风险", "lines": [
        "Risk-Off：%d" % int(r.get("riskoff_count") or 0),
        "指数背离：%s" % ("YES" if div else "NO"),
        "健康回调：%s" % ("YES" if pull else "NO"),
    ]})

    op = ["当前：%.0f%%　区间 %.0f%%～%.0f%%　目标中枢 %.0f%%" %
          (pos or 0, pmin or 0, pmax or 0, ptgt or 0)]
    if pos is not None and pmax is not None and pos < pmax:
        op.append("下一档：%.0f%%" % min(_quantize(pos + STEP_UP), pmax))
    else:
        op.append("已达上限" if full else "已在上限，等待满仓资格确认")
    blocks.append({"kind": "lines", "title": "操作", "lines": op + [
        "",
        "加仓条件：Breadth ≥65% ＋ RS继续改善 ＋ 无Risk-Off",
        "减仓条件：Close < MA20 或 Breadth快速恶化 或 Risk-Off ≥2",
    ]})

    if r.get("reason"):
        blocks.append({"kind": "chips", "title": "依据",
                       "chips": [s.strip() for s in str(r["reason"]).split("|") if s.strip()]})
    return blocks


def day_payload(rows: list[dict], ev_map: dict, plan: dict | None, date: str | None) -> dict | None:
    r = day_row(rows, date)
    if r is None:
        return None
    i = rows.index(r)
    lo = max(0, i - SERIES_DAYS + 1)
    series = [{
        "date": x["date"],
        "close": x.get("csi2000_close"),
        "ma20": x.get("ma20"),
        "ma60": x.get("ma60"),
        "breadth_ma20": x.get("breadth_ma20"),
        "position": x.get("position"),
        "position_target": x.get("position_target"),
        "position_min": x.get("position_min"),
        "position_max": x.get("position_max"),
        "regime": x.get("regime"),
    } for x in rows[lo:i + 1]]
    events = ev_map.get(r["date"], [])
    return {
        "date": r["date"],
        "week": WEEK_CN[datetime.strptime(r["date"], "%Y%m%d").weekday()],
        "regime": r.get("regime"),
        "regime_cn": REGIME_CN.get(str(r.get("regime")), "-"),
        "position": r.get("position"),
        "position_min": r.get("position_min"),
        "position_target": r.get("position_target"),
        "position_max": r.get("position_max"),
        "full_exposure_eligible": bool(r.get("full_exposure_eligible")),
        "riskoff_count": int(r.get("riskoff_count") or 0),
        "series_days": len(series),
        "series": series,
        "events": events,
        "blocks": build_blocks(r, rows, i, events),
        "plan": plan if (plan and str(plan.get("date")) == r["date"]) else None,
    }


# ══════════════════════════════════════════════════════════════════════
# Web 层
# ══════════════════════════════════════════════════════════════════════
def build_app(data_dir: str):
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import FileResponse, JSONResponse

    app = FastAPI(title="ETF 每日复盘", docs_url="/api/docs", redoc_url=None)

    @app.get("/")
    def index():
        page = os.path.join(BASE_DIR, "index.html")
        if not os.path.exists(page):
            raise HTTPException(500, "index.html 缺失")
        return FileResponse(page)

    @app.get("/api/meta")
    def api_meta():
        rows = load_daily(data_dir)
        plan = load_plan(data_dir)
        return {
            "ok": True,
            "count": len(rows),
            "date_min": rows[0]["date"] if rows else None,
            "date_max": rows[-1]["date"] if rows else None,
            "has_plan": plan is not None,
            "plan_date": plan.get("date") if plan else None,
            "bands": REGIME_BANDS,
            "regime_cn": REGIME_CN,
            "series_days": SERIES_DAYS,
            "served_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    @app.get("/api/dates")
    def api_dates():
        rows = load_daily(data_dir)
        items = [{
            "date": r["date"],
            "ym": r["date"][:6],
            "week": WEEK_CN[datetime.strptime(r["date"], "%Y%m%d").weekday()],
            "regime": r.get("regime"),
            "position": r.get("position"),
            "position_min": r.get("position_min"),
            "position_max": r.get("position_max"),
            "riskoff_count": int(r.get("riskoff_count") or 0),
            "full_exposure_eligible": bool(r.get("full_exposure_eligible")),
        } for r in rows]
        items.reverse()
        return {"count": len(items), "latest": items[0]["date"] if items else None,
                "dates": items}

    @app.get("/api/day")
    def api_day(date: str | None = Query(None)):
        d = (date or "").strip() or None
        if d and not (len(d) == 8 and d.isdigit()):
            raise HTTPException(400, "date 需为 YYYYMMDD")
        payload = day_payload(load_daily(data_dir), load_events(data_dir),
                              load_plan(data_dir), d)
        if payload is None:
            raise HTTPException(404, "该交易日无数据：%s" % (d or "(空)"))
        return payload

    @app.get("/api/plan")
    def api_plan():
        plan = load_plan(data_dir)
        if plan is None:
            raise HTTPException(404, "暂无 ETF 执行计划（%s）" % PLAN_FILE)
        return plan

    @app.get("/api/health")
    def api_health():
        rows = load_daily(data_dir)
        return JSONResponse({"ok": True, "data_dir": data_dir, "rows": len(rows)})

    return app


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ETF 每日复盘服务（只读 position_gate 输出）")
    ap.add_argument("--data", default=DEFAULT_DATA, help="position_gate 输出目录")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址")
    ap.add_argument("--port", type=int, default=8005, help="监听端口")
    ap.add_argument("--reload", action="store_true", help="开发模式自动重载")
    args = ap.parse_args(argv)

    rows = load_daily(args.data)
    if not rows:
        print("[etf] 警告：未读到 %s" % os.path.join(args.data, DAILY_FILE))
    else:
        print("[etf] %s  %d 个交易日  %s~%s"
              % (args.data, len(rows), rows[0]["date"], rows[-1]["date"]))
    if load_plan(args.data) is None:
        print("[etf] 警告：未读到 %s" % os.path.join(args.data, PLAN_FILE))
    print("[web] http://%s:%d" % (args.host, args.port))

    import uvicorn
    uvicorn.run(build_app(args.data), host=args.host, port=args.port,
                reload=args.reload, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
