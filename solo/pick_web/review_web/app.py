# -*- coding: utf-8 -*-
"""每日复盘服务（FastAPI，只读复盘报告目录）。

数据源：reports/Final_Self_<YYYYMMDD>.html（盘后脚本每日生成）
  - /            日期列表页，进入即是日期清单
  - /api/reports 报告清单（交易日倒序）
  - /api/report  某期报告 HTML（date 省略则取最新一期）

约束：全部只读，本服务不写任何文件，不影响盘后生成脚本。

用法：
    python app.py                                          # 127.0.0.1:8003
    python app.py --reports /var/www/StockPick/reports
"""
from __future__ import annotations

import argparse
import os
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPORTS = os.path.join(BASE_DIR, "reports")

REPORT_PREFIX = "Final_Self_"
REPORT_SUFFIX = ".html"
WEEK_CN = "一二三四五六日"
MONTH_CN = "一二三四五六七八九十十一十二"


# ══════════════════════════════════════════════════════════════════════
# 报告目录
# ══════════════════════════════════════════════════════════════════════
def is_date(s: str) -> bool:
    """YYYYMMDD 且是真实存在的日期。"""
    if not (len(s) == 8 and s.isdigit()):
        return False
    try:
        datetime.strptime(s, "%Y%m%d")
    except ValueError:
        return False
    return True


def list_reports(report_dir: str) -> list[dict]:
    """列出已有复盘报告，按交易日倒序。"""
    if not os.path.isdir(report_dir):
        return []
    out = []
    for name in os.listdir(report_dir):
        if not (name.startswith(REPORT_PREFIX) and name.endswith(REPORT_SUFFIX)):
            continue
        date = name[len(REPORT_PREFIX):-len(REPORT_SUFFIX)]
        if not is_date(date):
            continue
        st = os.stat(os.path.join(report_dir, name))
        out.append({
            "date": date,
            "ym": date[:6],
            "week": WEEK_CN[datetime.strptime(date, "%Y%m%d").weekday()],
            "size_kb": round(st.st_size / 1024, 1),
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            "mtime_date": datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M"),
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
    if not is_date(date):                            # 纯日期校验，防目录穿越
        return None
    path = os.path.join(report_dir, "%s%s%s" % (REPORT_PREFIX, date, REPORT_SUFFIX))
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def empty_page(title: str, msg: str, code: int):
    from fastapi.responses import HTMLResponse
    return HTMLResponse(
        "<!DOCTYPE html><html lang='zh-CN'><meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<body style='font-family:-apple-system,\"Microsoft YaHei\",sans-serif;"
        "padding:48px 22px;color:#7a8496;background:#f2f4f7;font-size:17px'>"
        "<h3 style='color:#1f2430'>%s</h3><p style='margin-top:8px'>%s</p>"
        "<p style='margin-top:18px'><a href='./' style='color:#1a5fb4'>返回日期列表</a></p>"
        "</body></html>" % (title, msg),
        status_code=code)


# ══════════════════════════════════════════════════════════════════════
# Web 层
# ══════════════════════════════════════════════════════════════════════
def build_app(report_dir: str):
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

    app = FastAPI(title="每日复盘", docs_url="/api/docs", redoc_url=None)

    @app.get("/")
    def index():
        page = os.path.join(BASE_DIR, "index.html")
        if not os.path.exists(page):
            raise HTTPException(500, "index.html 缺失")
        return FileResponse(page)

    @app.get("/api/reports")
    def api_reports():
        items = list_reports(report_dir)
        return {"count": len(items), "latest": items[0]["date"] if items else None,
                "reports": items}

    @app.get("/api/report")
    def api_report(date: str | None = Query(None)):
        date = (date or "").strip() or None
        if date and not is_date(date):
            raise HTTPException(400, "date 需为 YYYYMMDD")
        html = read_report(report_dir, date)
        if html is None:
            return empty_page("暂无复盘报告",
                              "该交易日（%s）未生成报告。" % date if date else "报告目录为空。",
                              404)
        return HTMLResponse(html)

    @app.get("/api/health")
    def api_health():
        return JSONResponse({"ok": True, "dir": report_dir,
                             "reports": len(list_reports(report_dir))})

    return app


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="每日复盘服务（只读报告目录）")
    ap.add_argument("--reports", default=DEFAULT_REPORTS,
                    help="复盘报告目录（Final_Self_<date>.html）")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址")
    ap.add_argument("--port", type=int, default=8003, help="监听端口")
    ap.add_argument("--reload", action="store_true", help="开发模式自动重载")
    args = ap.parse_args(argv)

    rpts = list_reports(args.reports)
    print("[review] %s  %d 期%s" % (args.reports, len(rpts),
                                    "  最新 " + rpts[0]["date"] if rpts else "（暂无）"))
    print("[web] http://%s:%d" % (args.host, args.port))

    import uvicorn
    uvicorn.run(build_app(args.reports), host=args.host, port=args.port,
                reload=args.reload, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
