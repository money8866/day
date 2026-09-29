# -*- coding: utf-8 -*-
"""选股信号查询服务（FastAPI + SQLite 只读）。

数据源：picks_db/stock_picks.db
  - stock_pick     每日落库信号（选股结果）
  - pick_tracking  盘后回填的跟踪收益（ret_1d/3d/5d/10d/20d、max_gain、max_drawdown、status）
  - strategy       策略名称表

设计约束：
  - 数据库一律只读打开（file:...?mode=ro），本服务绝不写入，不影响任何现有脚本。
  - 不做任何再计算：分数、买卖位、跟踪收益全部按库中原始值展示。
  - 胜率主口径 = ret_1d > 0（全量样本可用）；3d/5d 同时给出但必须带样本数，
    不以 max_gain 作为主胜率指标（存在最大值期望偏差）。

用法：
    python app.py                       # 127.0.0.1:8765
    python app.py --host 0.0.0.0 --port 8000
    python app.py --reload              # 开发模式
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(os.path.dirname(BASE_DIR), "picks_db", "stock_picks.db")
DEFAULT_REPORTS = os.path.join(BASE_DIR, "reports")   # 复盘报告目录（Final_Self_<date>.html）

MAX_ROWS = 2000          # 单次返回上限，防呆
MIN_SAMPLE = 10          # 统计样本不足的提示线

STATE_CN = {
    "STRONG_TREND": "强趋势",
    "STRUCTURAL_TREND": "结构性趋势",
    "STRUCTURAL_ROTATION": "结构性轮动",
    "RANGE": "震荡",
    "WEAK": "偏弱",
    "RISK_OFF": "风险规避",
}


# ══════════════════════════════════════════════════════════════════════
# 数据库（只读）
# ══════════════════════════════════════════════════════════════════════
def connect(db_path: str) -> sqlite3.Connection:
    uri = "file:%s?mode=ro" % db_path.replace("\\", "/").replace("?", "%3f").replace("#", "%23")
    try:
        con = sqlite3.connect(uri, uri=True, timeout=5)
    except sqlite3.Error:
        con = sqlite3.connect(db_path, timeout=5)   # WAL 等极端情况回退
    con.row_factory = sqlite3.Row
    return con


def query(db_path: str, sql: str, params: tuple = ()) -> list[dict]:
    con = connect(db_path)
    try:
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()


def db_meta(db_path: str) -> dict:
    if not os.path.exists(db_path):
        return {"ok": False, "error": "数据库不存在：%s" % db_path}
    row = query(db_path,
                "select count(*) n, min(pick_date) d0, max(pick_date) d1, "
                "max(created_at) upd from stock_pick")[0]
    trk = query(db_path, "select count(*) n, max(updated_at) upd from pick_tracking")[0]
    return {
        "ok": True,
        "db_path": db_path,
        "db_size_mb": round(os.path.getsize(db_path) / 1048576, 2),
        "pick_rows": row["n"],
        "date_min": row["d0"],
        "date_max": row["d1"],
        "pick_updated": row["upd"],
        "track_rows": trk["n"],
        "track_updated": trk["upd"],
        "served_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ══════════════════════════════════════════════════════════════════════
# 查询实现
# ══════════════════════════════════════════════════════════════════════
def list_dates(db_path: str) -> list[dict]:
    rows = query(db_path, """
        select p.pick_date,
               count(*)                       as n,
               count(distinct p.strategy_id)  as n_strategy,
               count(distinct p.ts_code)      as n_stock,
               (select count(*) from pick_tracking t
                 where t.pick_date = p.pick_date) as n_tracked
          from stock_pick p
         group by p.pick_date
         order by p.pick_date desc
    """)
    return rows


def list_strategies(db_path: str) -> list[dict]:
    return query(db_path, """
        select s.strategy_id,
               coalesce(nullif(s.strategy_name,''), s.strategy_id) as strategy_name,
               count(p.id) as n,
               min(p.pick_date) as d0,
               max(p.pick_date) as d1
          from strategy s
          left join stock_pick p on p.strategy_id = s.strategy_id
         group by s.strategy_id
         order by n desc, s.strategy_id
    """)


def fetch_picks(db_path: str, date: str | None, strategy: str | None,
                keyword: str | None) -> list[dict]:
    where, params = [], []
    if date:
        where.append("p.pick_date = ?")
        params.append(date)
    if strategy:
        where.append("p.strategy_id = ?")
        params.append(strategy)
    if keyword:
        where.append("(p.ts_code like ? or p.stock_name like ? or p.reason like ? "
                     "or p.action like ? or p.signal like ?)")
        like = "%" + keyword + "%"
        params += [like] * 5
    sql = """
        select p.pick_date, p.strategy_id,
               coalesce(nullif(s.strategy_name,''), p.strategy_id) as strategy_name,
               p.ts_code, p.stock_name, p.close, p.pct_chg, p.signal, p.action,
               p.score, p.rank_no, p.industry, p.reason,
               p.stop_price, p.target_price, p.indicators, p.details, p.created_at,
               t.base_close, t.last_date, t.last_close,
               t.ret_1d, t.ret_3d, t.ret_5d, t.ret_10d, t.ret_20d,
               t.max_high, t.max_low, t.max_gain, t.max_drawdown,
               t.status as track_status, t.hit_date, t.updated_at as track_updated
          from stock_pick p
          left join strategy s on s.strategy_id = p.strategy_id
          left join pick_tracking t
                 on t.pick_date   = p.pick_date
                and t.strategy_id = p.strategy_id
                and t.ts_code     = p.ts_code
    """
    if where:
        sql += " where " + " and ".join(where)
    sql += " order by p.pick_date desc, p.strategy_id, p.score desc, p.ts_code limit %d" % MAX_ROWS
    return query(db_path, sql, tuple(params))


def fetch_stats(db_path: str, days: int | None) -> dict:
    """按策略聚合胜率。days=None 表示全量，否则取最近 N 个交易日。"""
    cond, params = "", []
    if days:
        ds = query(db_path, "select distinct pick_date from pick_tracking "
                            "order by pick_date desc limit ?", (days,))
        if not ds:
            return {"rows": [], "overall": None, "scope": "无数据"}
        marks = ",".join("?" * len(ds))
        cond = " where t.pick_date in (%s)" % marks
        params = [d["pick_date"] for d in ds]

    rows = query(db_path, """
        select t.strategy_id,
               coalesce(nullif(s.strategy_name,''), t.strategy_id) as strategy_name,
               min(t.pick_date) as d0, max(t.pick_date) as d1,
               count(*)                                             as n,
               sum(case when t.ret_1d > 0 then 1 else 0 end)         as w1,
               avg(t.ret_1d)                                         as a1,
               sum(case when t.ret_3d > 0 then 1 else 0 end)         as w3,
               count(t.ret_3d)                                       as n3,
               avg(t.ret_3d)                                         as a3,
               sum(case when t.ret_5d > 0 then 1 else 0 end)         as w5,
               count(t.ret_5d)                                       as n5,
               avg(t.ret_5d)                                         as a5,
               sum(case when t.status = 'TARGET_HIT' then 1 else 0 end) as hit_target,
               sum(case when t.status = 'STOP_HIT'   then 1 else 0 end) as hit_stop,
               sum(case when t.status = 'EXPIRED'    then 1 else 0 end) as expired,
               sum(case when t.status = 'ACTIVE'     then 1 else 0 end) as active,
               avg(t.max_gain)                                       as avg_max_gain,
               avg(t.max_drawdown)                                   as avg_max_dd
          from pick_tracking t
          left join strategy s on s.strategy_id = t.strategy_id
    """ + cond + " group by t.strategy_id order by n desc", tuple(params))

    def pct(w, n):
        if not n:
            return None
        return round(100.0 * (w or 0) / n, 1)

    def avg(v):
        return None if v is None else round(float(v), 2)

    out = []
    agg = {"n": 0, "w1": 0, "s1": 0.0, "n3": 0, "w3": 0, "s3": 0.0,
           "n5": 0, "w5": 0, "s5": 0.0, "hit_target": 0, "hit_stop": 0,
           "active": 0, "expired": 0}
    for r in rows:
        n = r["n"]
        agg["n"] += n
        agg["w1"] += r["w1"] or 0
        agg["s1"] += (r["a1"] or 0.0) * n
        agg["n3"] += r["n3"]
        agg["w3"] += r["w3"] or 0
        agg["s3"] += (r["a3"] or 0.0) * r["n3"]
        agg["n5"] += r["n5"]
        agg["w5"] += r["w5"] or 0
        agg["s5"] += (r["a5"] or 0.0) * r["n5"]
        for k in ("hit_target", "hit_stop", "active", "expired"):
            agg[k] += r[k] or 0
        item = {
            "strategy_id": r["strategy_id"],
            "strategy_name": r["strategy_name"],
            "d0": r["d0"], "d1": r["d1"],
            "n": n,
            "w1": pct(r["w1"], n), "a1": avg(r["a1"]),
            "n3": r["n3"], "w3": pct(r["w3"], r["n3"]), "a3": avg(r["a3"]),
            "n5": r["n5"], "w5": pct(r["w5"], r["n5"]), "a5": avg(r["a5"]),
            "hit_target": r["hit_target"], "hit_stop": r["hit_stop"],
            "expired": r["expired"], "active": r["active"],
            "avg_max_gain": avg(r["avg_max_gain"]),
            "avg_max_dd": avg(r["avg_max_dd"]),
            "sample_ok": n >= MIN_SAMPLE,
        }
        out.append(item)

    tot_n = sum(x["n"] for x in out)
    tot_n3 = sum(x["n3"] for x in out)
    tot_n5 = sum(x["n5"] for x in out)
    overall = {
        "n": tot_n,
        "w1": pct(agg["w1"], agg["n"]),
        "a1": avg(agg["s1"] / agg["n"]) if agg["n"] else None,
        "n3": tot_n3,
        "w3": pct(agg["w3"], agg["n3"]),
        "a3": avg(agg["s3"] / agg["n3"]) if agg["n3"] else None,
        "n5": tot_n5,
        "w5": pct(agg["w5"], agg["n5"]),
        "a5": avg(agg["s5"] / agg["n5"]) if agg["n5"] else None,
        "hit_target": agg["hit_target"],
        "hit_stop": agg["hit_stop"],
        "active": agg["active"],
        "expired": agg["expired"],
    }

    scope = "全量" if not days else "最近 %d 个交易日" % len(params)
    return {"rows": out, "overall": overall, "scope": scope, "min_sample": MIN_SAMPLE}


# ══════════════════════════════════════════════════════════════════════
# 复盘报告（tushare_quant.py 每日生成的 Final_Self_<date>.html）
# ══════════════════════════════════════════════════════════════════════
REPORT_PREFIX = "Final_Self_"
REPORT_SUFFIX = ".html"


def list_reports(report_dir: str) -> list[dict]:
    """列出已有复盘报告，按交易日倒序。"""
    if not os.path.isdir(report_dir):
        return []
    out = []
    for name in os.listdir(report_dir):
        if not (name.startswith(REPORT_PREFIX) and name.endswith(REPORT_SUFFIX)):
            continue
        date = name[len(REPORT_PREFIX):-len(REPORT_SUFFIX)]
        if not (len(date) == 8 and date.isdigit()):
            continue
        st = os.stat(os.path.join(report_dir, name))
        out.append({
            "date": date,
            "size_kb": round(st.st_size / 1024, 1),
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


def read_report(report_dir: str, date: str | None) -> str | None:
    """读取某交易日报告 HTML；date 为空取最新一期；不存在返回 None。"""
    if not date:
        items = list_reports(report_dir)
        if not items:
            return None
        date = items[0]["date"]
    if not (len(date) == 8 and date.isdigit()):     # 纯日期校验，防目录穿越
        return None
    path = os.path.join(report_dir, "%s%s%s" % (REPORT_PREFIX, date, REPORT_SUFFIX))
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ══════════════════════════════════════════════════════════════════════
# Web 层
# ══════════════════════════════════════════════════════════════════════
def build_app(db_path: str, report_dir: str = DEFAULT_REPORTS):
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

    app = FastAPI(title="选股信号查询", docs_url="/api/docs", redoc_url=None)

    @app.get("/")
    def index():
        page = os.path.join(BASE_DIR, "index.html")
        if not os.path.exists(page):
            raise HTTPException(500, "index.html 缺失")
        return FileResponse(page)

    @app.get("/api/meta")
    def api_meta():
        m = db_meta(db_path)
        if not m["ok"]:
            raise HTTPException(500, m["error"])
        m["strategies"] = list_strategies(db_path)
        return m

    @app.get("/api/dates")
    def api_dates():
        return {"dates": list_dates(db_path)}

    @app.get("/api/picks")
    def api_picks(date: str | None = Query(None), strategy: str | None = Query(None),
                  q: str | None = Query(None)):
        if date:
            date = date.strip()
            if date and (not date.isdigit() or len(date) != 8):
                raise HTTPException(400, "date 需为 YYYYMMDD")
        rows = fetch_picks(db_path, date or None, strategy or None, (q or "").strip() or None)
        return {"count": len(rows), "records": rows}

    @app.get("/api/stats")
    def api_stats(days: int | None = Query(None, ge=1, le=250)):
        return fetch_stats(db_path, days)

    @app.get("/api/reports")
    def api_reports():
        return {"reports": list_reports(report_dir)}

    @app.get("/api/report")
    def api_report(date: str | None = Query(None)):
        date = (date or "").strip() or None
        html = read_report(report_dir, date)
        if html is None:
            return HTMLResponse(
                "<!DOCTYPE html><html lang='zh-CN'><meta charset='UTF-8'>"
                "<body style='font-family:-apple-system,\"Microsoft YaHei\",sans-serif;"
                "padding:48px;color:#7a8496;background:#f5f7fa'>"
                "<h3 style='color:#1f2430'>暂无复盘报告</h3>"
                "<p>%s</p></body></html>"
                % ("该交易日（%s）未生成报告。" % date if date else "报告目录为空。"),
                status_code=404)
        return HTMLResponse(html)

    @app.get("/api/health")
    def api_health():
        return JSONResponse({"ok": True, "db": db_path, "reports": len(list_reports(report_dir))})

    return app


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="选股信号查询服务（只读 stock_picks.db）")
    ap.add_argument("--db", default=DEFAULT_DB, help="stock_picks.db 路径")
    ap.add_argument("--reports", default=DEFAULT_REPORTS, help="复盘报告目录（Final_Self_<date>.html）")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（云服务器用 0.0.0.0）")
    ap.add_argument("--port", type=int, default=8765, help="监听端口")
    ap.add_argument("--reload", action="store_true", help="开发模式自动重载")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        raise SystemExit("找不到数据库：%s" % args.db)
    info = db_meta(args.db)
    print("[db] %s  %.2f MB  信号 %d 条  %s~%s  跟踪 %d 条"
          % (args.db, info["db_size_mb"], info["pick_rows"],
             info["date_min"], info["date_max"], info["track_rows"]))
    rpts = list_reports(args.reports)
    print("[report] %s  %d 期%s" % (args.reports, len(rpts),
                                    "  最新 " + rpts[0]["date"] if rpts else "（暂无）"))
    print("[web] http://%s:%d  （API 文档 http://%s:%d/api/docs）"
          % (args.host, args.port, args.host, args.port))

    import uvicorn
    uvicorn.run(build_app(args.db, args.reports), host=args.host, port=args.port,
                reload=args.reload, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
