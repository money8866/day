#!/usr/bin/env python3
"""
研报数据库查询分析工具

从 SQLite 数据库中查询、搜索、统计历史积累的个股研报数据。
"""

import argparse
import csv
import os
import sqlite3
import sys
from datetime import datetime

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_reports.db")


def get_conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ── 统计概览 ───────────────────────────────────────────────────────

def cmd_stats(db_path):
    conn = get_conn(db_path)
    total = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    stocks = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM reports").fetchone()[0]
    orgs = conn.execute("SELECT COUNT(DISTINCT org_name) FROM reports").fetchone()[0]
    industries = conn.execute("SELECT COUNT(DISTINCT industry) FROM reports").fetchone()[0]
    date_range = conn.execute(
        "SELECT MIN(publish_date), MAX(publish_date) FROM reports"
    ).fetchone()
    today = conn.execute(
        "SELECT COUNT(*) FROM reports WHERE fetch_date=?", (datetime.now().strftime("%Y-%m-%d"),)
    ).fetchone()[0]
    conn.close()

    print("═══ 研报数据库统计 ═══")
    print(f"  累计研报:    {total} 篇")
    print(f"  覆盖个股:    {stocks} 只")
    print(f"  覆盖机构:    {orgs} 家")
    print(f"  覆盖行业:    {industries} 个")
    print(f"  时间跨度:    {date_range[0]} ~ {date_range[1]}")
    print(f"  今日新增:    {today} 篇")


# ── 最近研报 ───────────────────────────────────────────────────────

def cmd_latest(db_path, n=10):
    conn = get_conn(db_path)
    rows = conn.execute("""
        SELECT stock_code, stock_name, report_title, org_name,
               publish_date, rating, rating_change, industry
        FROM reports
        ORDER BY publish_date DESC, rowid DESC
        LIMIT ?
    """, (n,)).fetchall()
    conn.close()

    if not rows:
        print("[INFO] 数据库为空")
        return

    print(f"═══ 最近 {n} 篇研报 ═══\n")
    print(f"{'日期':<12} {'股票':<10} {'名称':<8} {'机构':<10} {'评级':<8} {'行业':<12} {'标题':<40}")
    print("━" * 100)
    for r in rows:
        title = r["report_title"][:38] + ".." if len(r["report_title"] or "") > 40 else (r["report_title"] or "")
        rating_chg = {"1": "↑", "2": "★", "3": "→", "4": "↓", "5": "—"}.get(
            r["rating_change"] or "", ""
        )
        rating_str = f"{r['rating'] or '-'}{rating_chg}"
        print(f"{r['publish_date'] or '':<12} {r['stock_code']:<10} {r['stock_name']:<8} "
              f"{r['org_name'] or '':<10} {rating_str:<8} {r['industry'] or '':<12} {title}")


# ── 按个股搜索 ─────────────────────────────────────────────────────

def cmd_search(db_path, keyword, n=20, detail=False):
    conn = get_conn(db_path)
    like = f"%{keyword}%"
    rows = conn.execute("""
        SELECT * FROM reports
        WHERE stock_code LIKE ? OR stock_name LIKE ? OR report_title LIKE ?
        ORDER BY publish_date DESC
        LIMIT ?
    """, (like, like, like, n)).fetchall()
    conn.close()

    if not rows:
        print(f"[INFO] 未找到包含「{keyword}」的研报")
        return

    print(f"═══ 搜索「{keyword}」共 {len(rows)} 条结果 ═══\n")

    for i, r in enumerate(rows, 1):
        print(f"── {i}. {r['stock_name']}({r['stock_code']})  {r['publish_date']} ──")
        print(f"  标题: {r['report_title']}")
        print(f"  机构: {r['org_name']}  评级: {r['rating']}")
        tp_parts = []
        if r["target_price_low"]:
            tp_parts.append(f"目标价 {r['target_price_low']}")
        if r["target_price_high"] and r["target_price_high"] != r["target_price_low"]:
            tp_parts.append(f"~{r['target_price_high']}")
        eps_parts = []
        if r["eps_this_year"]:
            eps_parts.append(f"本年 EPS {r['eps_this_year']}")
        if r["eps_next_year"]:
            eps_parts.append(f"明年 EPS {r['eps_next_year']}")
        if r["eps_next_two_year"]:
            eps_parts.append(f"后年 EPS {r['eps_next_two_year']}")
        if tp_parts:
            print(f"  {' '.join(tp_parts)}")
        if eps_parts:
            print(f"  {' | '.join(eps_parts)}")
        print(f"  原文: {r['source_url']}")
        if detail and r["core_viewpoints"]:
            text = r["core_viewpoints"]
            preview = text[:300] + "..." if len(text) > 300 else text
            print(f"  核心观点:\n{preview}")
        print()


# ── 个股研报时间线 ────────────────────────────────────────────────

def cmd_timeline(db_path, stock_code):
    conn = get_conn(db_path)
    rows = conn.execute("""
        SELECT publish_date, org_name, rating, rating_change, report_title,
               target_price_high, target_price_low,
               eps_this_year, eps_next_year
        FROM reports
        WHERE stock_code = ?
        ORDER BY publish_date DESC
    """, (stock_code,)).fetchall()
    conn.close()

    if not rows:
        print(f"[INFO] 未找到 {stock_code} 的研报")
        return

    # 获取股票名称
    name = rows[0]["report_title"] or ""

    print(f"═══ {stock_code} 研报时间线（共 {len(rows)} 篇）═══\n")
    for r in rows:
        chg = {"1": "↑调高", "2": "★首次", "3": "→维持", "4": "↓调低", "5": "—"}.get(
            r["rating_change"] or "", ""
        )
        rating = f"{r['rating'] or '-'}{chg}" if chg else r['rating'] or '-'
        target = ""
        if r["target_price_high"]:
            target = f" 目标价:{r['target_price_low']}~{r['target_price_high']}" if (
                r["target_price_low"] and r["target_price_low"] != r["target_price_high"]
            ) else f" 目标价:{r['target_price_high']}"
        eps = ""
        if r["eps_this_year"] and r["eps_next_year"]:
            eps = f" EPS:{r['eps_this_year']}→{r['eps_next_year']}"
        elif r["eps_this_year"]:
            eps = f" EPS:{r['eps_this_year']}"

        print(f"  {r['publish_date']}  [{r['org_name']:<8}] {rating:<10}{target}{eps}")
        print(f"    {r['report_title']}")
        print()


# ── 机构活跃度排名 ────────────────────────────────────────────────

def cmd_orgs(db_path, n=10):
    conn = get_conn(db_path)
    rows = conn.execute("""
        SELECT org_name,
               COUNT(*) AS cnt,
               COUNT(DISTINCT stock_code) AS stocks,
               MAX(publish_date) AS last_date
        FROM reports
        WHERE org_name IS NOT NULL AND org_name != ''
        GROUP BY org_name
        ORDER BY cnt DESC
        LIMIT ?
    """, (n,)).fetchall()
    conn.close()

    print(f"═══ 最活跃机构 TOP {n} ═══\n")
    print(f"{'排名':<4} {'机构':<12} {'研报数':<8} {'覆盖个股':<10} {'最近研报':<12}")
    print("━" * 50)
    for i, r in enumerate(rows, 1):
        print(f"{i:<4} {r['org_name']:<12} {r['cnt']:<8} {r['stocks']:<10} {r['last_date'] or '':<12}")


# ── 行业覆盖排名 ────────────────────────────────────────────────

def cmd_industries(db_path, n=10):
    conn = get_conn(db_path)
    rows = conn.execute("""
        SELECT industry,
               COUNT(*) AS cnt,
               COUNT(DISTINCT stock_code) AS stocks,
               MAX(publish_date) AS last_date
        FROM reports
        WHERE industry IS NOT NULL AND industry != ''
        GROUP BY industry
        ORDER BY cnt DESC
        LIMIT ?
    """, (n,)).fetchall()
    conn.close()

    print(f"═══ 行业研报覆盖 TOP {n} ═══\n")
    print(f"{'排名':<4} {'行业':<14} {'研报数':<8} {'覆盖个股':<10} {'最近研报':<12}")
    print("━" * 50)
    for i, r in enumerate(rows, 1):
        print(f"{i:<4} {r['industry']:<14} {r['cnt']:<8} {r['stocks']:<10} {r['last_date'] or '':<12}")


# ── 导出 CSV ─────────────────────────────────────────────────────

def cmd_export(db_path, output, stock=None):
    conn = get_conn(db_path)
    if stock:
        rows = conn.execute("SELECT * FROM reports WHERE stock_code=? ORDER BY publish_date DESC", (stock,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM reports ORDER BY publish_date DESC").fetchall()
    conn.close()

    if not rows:
        print("[INFO] 没有数据可导出")
        return

    fieldnames = [
        "info_code", "stock_code", "stock_name", "report_title", "org_name",
        "publish_date", "industry", "rating", "rating_change",
        "target_price_high", "target_price_low",
        "eps_this_year", "pe_this_year", "eps_next_year", "pe_next_year",
        "eps_next_two_year", "pe_next_two_year",
        "source_url", "fetch_date",
    ]
    with open(output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(dict(r))
    print(f"[INFO] 已导出 {len(rows)} 条记录到 {output}")


# ── 评级变动发现 ────────────────────────────────────────────────

def cmd_rating_changes(db_path, n=20):
    """找出最近的评级上调/下调事件"""
    conn = get_conn(db_path)
    rows = conn.execute("""
        SELECT stock_code, stock_name, org_name, publish_date,
               rating, rating_change, report_title,
               target_price_high, target_price_low
        FROM reports
        WHERE rating_change IN ('1', '4')
        ORDER BY publish_date DESC
        LIMIT ?
    """, (n,)).fetchall()
    conn.close()

    if not rows:
        print("[INFO] 未发现评级变动记录")
        return

    print(f"═══ 最近评级变动事件（TOP {n}）═══\n")
    print(f"{'日期':<12} {'股票':<10} {'名称':<8} {'机构':<10} {'变动':<8} {'目标价':<12} {'标题':<40}")
    print("━" * 100)
    for r in rows:
        chg_label = "↑调高" if r["rating_change"] == "1" else "↓调低"
        target = ""
        if r["target_price_high"]:
            target = f"{r['target_price_low']}~{r['target_price_high']}" if (
                r["target_price_low"] and r["target_price_low"] != r["target_price_high"]
            ) else f"{r['target_price_high']}"
        title = (r["report_title"] or "")[:38] + ".." if len(r["report_title"] or "") > 40 else (r["report_title"] or "")
        print(f"{r['publish_date']:<12} {r['stock_code']:<10} {r['stock_name']:<8} "
              f"{r['org_name'] or '':<10} {chg_label:<8} {target:<12} {title}")


# ── 目标价对比 ──────────────────────────────────────────────────

def cmd_compare(db_path, stock_code):
    """对比同一股票不同机构的目标价"""
    conn = get_conn(db_path)
    rows = conn.execute("""
        SELECT publish_date, org_name, rating, target_price_high,
               target_price_low, eps_this_year, eps_next_year, researcher
        FROM reports
        WHERE stock_code = ? AND target_price_high IS NOT NULL
        ORDER BY publish_date DESC
    """, (stock_code,)).fetchall()
    conn.close()

    name = ""
    if rows:
        conn2 = get_conn(db_path)
        r = conn2.execute(
            "SELECT stock_name FROM reports WHERE stock_code=? LIMIT 1", (stock_code,)
        ).fetchone()
        if r:
            name = r["stock_name"]
        conn2.close()

    if not rows:
        print(f"[INFO] {stock_code} 暂无带目标价的研报")
        return

    print(f"═══ {stock_code} {name} 目标价对比（{len(rows)} 篇）═══\n")
    print(f"{'日期':<12} {'机构':<12} {'评级':<8} {'目标价':<16} {'EPS(本/明)':<16}")
    print("━" * 65)
    for r in rows:
        tp = f"{r['target_price_low']}~{r['target_price_high']}" if (
            r["target_price_low"] and r["target_price_low"] != r["target_price_high"]
        ) else f"{r['target_price_high'] or '':<10}"
        eps = f"{r['eps_this_year'] or ''}/{r['eps_next_year'] or ''}"
        print(f"{r['publish_date']:<12} {r['org_name']:<12} {r['rating'] or '-':<8} {tp:<16} {eps:<16}")


# ── 主入口 ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="研报数据库查询分析工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python query_reports.py                             # 统计概览\n"
            "  python query_reports.py latest                      # 最近20篇\n"
            "  python query_reports.py latest -n 50                # 最近50篇\n"
            "  python query_reports.py search 阳光电源             # 搜索个股\n"
            "  python query_reports.py search 300274 --detail      # 显示核心观点\n"
            "  python query_reports.py timeline 300274             # 个股研报时间线\n"
            "  python query_reports.py orgs                        # 机构排名\n"
            "  python query_reports.py industries                  # 行业排名\n"
            "  python query_reports.py rating-changes              # 评级变动\n"
            "  python query_reports.py compare 300274              # 目标价对比\n"
            "  python query_reports.py export reports.csv          # 导出CSV\n"
            "  python query_reports.py export --stock 300274 out.csv\n"
        ),
    )
    parser.add_argument("--db", type=str, default=DEFAULT_DB, help="数据库路径")

    sub = parser.add_subparsers(dest="command", metavar="命令")

    # stats (default)
    # latest
    p_latest = sub.add_parser("latest", help="最近研报")
    p_latest.add_argument("-n", type=int, default=20, help="条数")

    # search
    p_search = sub.add_parser("search", help="搜索个股/标题")
    p_search.add_argument("keyword", help="关键词（股票代码/名称/标题）")
    p_search.add_argument("-n", type=int, default=20, help="最多条数")
    p_search.add_argument("--detail", action="store_true", help="显示核心观点")

    # timeline
    p_tl = sub.add_parser("timeline", help="个股研报时间线")
    p_tl.add_argument("stock_code", help="股票代码")

    # orgs
    p_orgs = sub.add_parser("orgs", help="机构活跃度排名")
    p_orgs.add_argument("-n", type=int, default=10, help="TOP N")

    # industries
    p_ind = sub.add_parser("industries", help="行业覆盖排名")
    p_ind.add_argument("-n", type=int, default=10, help="TOP N")

    # rating-changes
    p_rc = sub.add_parser("rating-changes", help="评级变动事件")
    p_rc.add_argument("-n", type=int, default=20, help="条数")

    # compare
    p_cmp = sub.add_parser("compare", help="目标价对比")
    p_cmp.add_argument("stock_code", help="股票代码")

    # export
    p_exp = sub.add_parser("export", help="导出CSV")
    p_exp.add_argument("output", help="CSV文件路径")
    p_exp.add_argument("--stock", help="指定股票代码")

    args = parser.parse_args()

    if not os.path.exists(args.db):
        print(f"[ERROR] 数据库不存在: {args.db}")
        print("请先运行 python fetch_stock_reports.py 抓取研报入库")
        sys.exit(1)

    cmds = {
        "latest": lambda: cmd_latest(args.db, args.n),
        "search": lambda: cmd_search(args.db, args.keyword, args.n, args.detail),
        "timeline": lambda: cmd_timeline(args.db, args.stock_code),
        "orgs": lambda: cmd_orgs(args.db, args.n),
        "industries": lambda: cmd_industries(args.db, args.n),
        "rating-changes": lambda: cmd_rating_changes(args.db, args.n),
        "compare": lambda: cmd_compare(args.db, args.stock_code),
        "export": lambda: cmd_export(args.db, args.output, args.stock),
    }

    fn = cmds.get(args.command)
    if fn:
        fn()
    else:
        cmd_stats(args.db)


if __name__ == "__main__":
    main()
