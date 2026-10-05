# -*- coding: utf-8 -*-
"""三级行业共振服务（FastAPI，只读共振 JSON 目录）。

数据源：l3_resonance_<YYYYMMDD>.json（l3_resonance_scanner.py 每日盘后生成）
  - /             单页平铺：按交易日倒序，每天一块「行业榜 + 行业内涨停股明细」
  - /api/days     交易日清单（倒序）+ 每日概览
  - /api/feed     最近 N 个交易日的完整数据（页面一次拉取）
  - /api/day      指定交易日的完整数据
  - /api/matrix   行业 × 日期的「行业均涨幅」矩阵（页面画近一月曲线）

约束：全部只读，本服务不写任何文件，不影响盘后生成脚本。

用法：
    python app.py                                    # 127.0.0.1:8004
    python app.py --data /var/www/StockPick/l3res
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA = os.path.join(BASE_DIR, "data")

FILE_RE = re.compile(r"^l3_resonance_(\d{8})\.json$")
WEEK_CN = "一二三四五六日"
MAX_DAYS = 250          # 页面最多一次拉取的交易日数


# ══════════════════════════════════════════════════════════════════════
# 数据读取
# ══════════════════════════════════════════════════════════════════════
def is_date(s: str) -> bool:
    """YYYYMMDD 且是真实存在的日期（同时防目录穿越）。"""
    if not (len(s) == 8 and s.isdigit()):
        return False
    try:
        datetime.strptime(s, "%Y%m%d")
    except ValueError:
        return False
    return True


def list_files(data_dir: str) -> list[tuple[str, str]]:
    """返回 [(date, path)]，按交易日倒序。"""
    if not os.path.isdir(data_dir):
        return []
    out = []
    for name in os.listdir(data_dir):
        m = FILE_RE.match(name)
        if not m:
            continue
        date = m.group(1)
        if not is_date(date):
            continue
        out.append((date, os.path.join(data_dir, name)))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def load_day(path: str, date: str) -> dict | None:
    """读单个交易日 JSON，并把涨停股挂到所属行业下，便于页面平铺渲染。"""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return None

    stocks = raw.get("stocks") or []
    by_l3: dict[str, list] = {}
    for s in stocks:
        by_l3.setdefault(s.get("l3_name") or "", []).append({
            "ts_code": s.get("ts_code"),
            "name": s.get("name"),
            "close": s.get("close"),
            "pct_chg": s.get("pct_chg"),
            "streak": s.get("streak") or 0,
            "amount_yi": s.get("amount_yi"),
            "last_zt_date": s.get("last_zt_date") or "",
            "total_mv_yi": s.get("total_mv_yi"),
            "pe": s.get("pe"),
        })

    industries = []
    for r in raw.get("industries") or []:
        name = r.get("l3_name") or ""
        industries.append({
            "rank": r.get("rank"),
            "l3_code": r.get("l3_code"),
            "l3_name": name,
            "l2_name": r.get("l2_name"),
            "n_zt": r.get("n_zt") or 0,
            "n_members": r.get("n_members") or 0,
            "zt_ratio": r.get("zt_ratio"),
            "max_streak": r.get("max_streak") or 0,
            "avg_pct": r.get("avg_pct"),
            "stocks": by_l3.get(name, []),
        })

    summary = raw.get("summary") or {}
    return {
        "date": date,
        "week": WEEK_CN[datetime.strptime(date, "%Y%m%d").weekday()],
        "generated_at": raw.get("generated_at") or "",
        "params": raw.get("params") or {},
        "summary": summary,
        "industries": industries,
    }


def build_matrix(data_dir: str) -> dict:
    """所有交易日的「行业 × 日期」均涨幅矩阵，供页面画近一月曲线。

    优先取每日 JSON 的 industry_avg（全部三级行业），缺失时回退 industries（仅达标行业）。
    dates 升序；industries[].avg 与 dates 同序，当日无该行业数据为 null。
    """
    files = sorted(list_files(data_dir), key=lambda x: x[0])
    dates = [d for d, _ in files]
    pos = {d: i for i, d in enumerate(dates)}

    series: dict[str, dict] = {}
    for date, path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            continue
        rows = raw.get("industry_avg") or raw.get("industries") or []
        for r in rows:
            code = r.get("l3_code") or r.get("l3_name") or ""
            if not code:
                continue
            rec = series.get(code)
            if rec is None:
                rec = series[code] = {
                    "l3_code": code,
                    "l3_name": r.get("l3_name") or "",
                    "l2_name": r.get("l2_name") or "",
                    "avg": [None] * len(dates),
                }
            v = r.get("avg_pct")
            rec["avg"][pos[date]] = None if v is None else round(float(v), 3)

    return {
        "dates": dates,
        "count": len(dates),
        "industries": sorted(series.values(), key=lambda r: r["l3_name"]),
    }


def day_overview(path: str, date: str) -> dict:
    """轻量概览：只读 JSON 顶层 summary，用于日期清单。"""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        st = os.stat(path)
    except (OSError, ValueError):
        return {"date": date, "week": WEEK_CN[datetime.strptime(date, "%Y%m%d").weekday()],
                "n_zt_total": 0, "n_hit": 0, "n_stocks": 0,
                "size_kb": 0, "mtime": ""}
    sm = raw.get("summary") or {}
    return {
        "date": date,
        "week": WEEK_CN[datetime.strptime(date, "%Y%m%d").weekday()],
        "n_zt_total": sm.get("n_zt_total") or 0,
        "n_hit": sm.get("n_industries_hit") or 0,
        "n_stocks": sm.get("n_industry_zt_stocks") or 0,
        "generated_at": raw.get("generated_at") or "",
        "size_kb": round(st.st_size / 1024, 1),
        "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
    }


# ══════════════════════════════════════════════════════════════════════
# Web 层
# ══════════════════════════════════════════════════════════════════════
def build_app(data_dir: str):
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import FileResponse, JSONResponse

    app = FastAPI(title="三级行业共振", docs_url="/api/docs", redoc_url=None)

    @app.get("/")
    def index():
        page = os.path.join(BASE_DIR, "index.html")
        if not os.path.exists(page):
            raise HTTPException(500, "index.html 缺失")
        return FileResponse(page)

    @app.get("/api/days")
    def api_days():
        items = [day_overview(p, d) for d, p in list_files(data_dir)]
        return {"count": len(items), "latest": items[0]["date"] if items else None,
                "days": items}

    @app.get("/api/feed")
    def api_feed(limit: int = Query(10, ge=1, le=MAX_DAYS)):
        """最近 limit 个交易日的完整数据，交易日倒序。"""
        picked = list_files(data_dir)[:limit]
        days = [x for x in (load_day(p, d) for d, p in picked) if x]
        return {"count": len(days), "available": len(list_files(data_dir)), "days": days}

    @app.get("/api/day")
    def api_day(date: str = Query(...)):
        date = (date or "").strip()
        if not is_date(date):
            raise HTTPException(400, "date 需为 YYYYMMDD")
        path = os.path.join(data_dir, "l3_resonance_%s.json" % date)
        if not os.path.isfile(path):
            raise HTTPException(404, "该交易日无共振数据")
        day = load_day(path, date)
        if day is None:
            raise HTTPException(500, "该交易日数据解析失败")
        return day

    @app.get("/api/matrix")
    def api_matrix():
        """行业 × 日期的均涨幅矩阵（近一月曲线用）。"""
        return build_matrix(data_dir)

    @app.get("/api/health")
    def api_health():
        return JSONResponse({"ok": True, "dir": data_dir,
                             "days": len(list_files(data_dir))})

    return app


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="三级行业共振服务（只读 JSON 目录）")
    ap.add_argument("--data", default=DEFAULT_DATA,
                    help="共振 JSON 目录（l3_resonance_<date>.json）")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址")
    ap.add_argument("--port", type=int, default=8004, help="监听端口")
    ap.add_argument("--reload", action="store_true", help="开发模式自动重载")
    args = ap.parse_args(argv)

    n = len(list_files(args.data))
    print("[l3res] %s  %d 个交易日" % (args.data, n))
    print("[web] http://%s:%d" % (args.host, args.port))

    import uvicorn
    uvicorn.run(build_app(args.data), host=args.host, port=args.port,
                reload=args.reload, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
