#!/usr/bin/env python3
"""
东方财富个股研报核心观点提取器 + 数据库存储

从 data.eastmoney.com/report/stock.jshtml 获取最新个股研报，
自动抓取研报正文，提炼核心观点，存储到 SQLite 数据库并输出 Markdown 报告。
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from typing import Any

import requests
from bs4 import BeautifulSoup

# ── 配置 ──────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://data.eastmoney.com/report/stock.jshtml",
}

API_LIST = "http://reportapi.eastmoney.com/report/list"
DETAIL_URL_TPL = "https://data.eastmoney.com/report/info/{infoCode}.html"
REQUEST_INTERVAL = 0.5
DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stock_reports.db")


# ── 数据库操作 ─────────────────────────────────────────────────────

def init_db(db_path: str):
    """初始化数据库表结构"""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            info_code      TEXT PRIMARY KEY,
            stock_code     TEXT NOT NULL,
            stock_name     TEXT NOT NULL,
            report_title   TEXT,
            org_name       TEXT,
            publish_date   TEXT,
            industry       TEXT,
            rating         TEXT,
            rating_change  TEXT,
            researcher     TEXT,
            target_price_high REAL,
            target_price_low  REAL,
            eps_this_year  REAL,
            pe_this_year   REAL,
            eps_next_year  REAL,
            pe_next_year   REAL,
            eps_next_two_year REAL,
            pe_next_two_year  REAL,
            core_viewpoints TEXT,
            source_url     TEXT,
            fetch_date     TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_reports_stock
            ON reports(stock_code, stock_name)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_reports_date
            ON reports(publish_date)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_reports_org
            ON reports(org_name)
    """)
    # 用于跟踪的视图：各股票研报覆盖次数
    conn.execute("""
        CREATE VIEW IF NOT EXISTS v_stock_coverage AS
        SELECT stock_code, stock_name,
               COUNT(*)           AS report_count,
               MAX(publish_date)  AS last_report_date,
               GROUP_CONCAT(DISTINCT org_name) AS institutions
        FROM reports
        GROUP BY stock_code, stock_name
    """)
    conn.commit()
    conn.close()


def get_existing_codes(db_path: str) -> set:
    """返回数据库中已有的 info_code 集合（用于追加模式）"""
    conn = sqlite3.connect(db_path)
    codes = {r[0] for r in conn.execute("SELECT info_code FROM reports").fetchall()}
    conn.close()
    return codes


def upsert_report(db_path: str, rpt: dict, core_text: str, append_only: bool = False):
    """插入一篇研报到数据库（append_only=True 时仅新增，不覆盖已有）"""
    def _n(v):
        """安全转数值"""
        if v is None or v == "":
            return None
        try:
            return round(float(v), 4)
        except (ValueError, TypeError):
            return None

    info_code = rpt.get("infoCode", "")
    if not info_code:
        return

    # 从 API 响应中提取目标价（字段可能叫 indvAimPriceT / indvAimPriceL）
    target_high = _n(rpt.get("indvAimPriceT"))
    target_low = _n(rpt.get("indvAimPriceL"))
    # 有些报告只有一个目标价
    if target_high is not None and target_low is None:
        target_low = target_high
    elif target_low is not None and target_high is None:
        target_high = target_low

    publish_date = (rpt.get("publishDate") or "")[:10]
    today_str = datetime.now().strftime("%Y-%m-%d")

    conn = sqlite3.connect(db_path)
    stmt = "INSERT OR IGNORE" if append_only else "INSERT OR REPLACE"
    conn.execute(f"""
        {stmt} INTO reports (
            info_code, stock_code, stock_name, report_title,
            org_name, publish_date, industry, rating, rating_change, researcher,
            target_price_high, target_price_low,
            eps_this_year, pe_this_year,
            eps_next_year, pe_next_year,
            eps_next_two_year, pe_next_two_year,
            core_viewpoints, source_url, fetch_date
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        info_code,
        rpt.get("stockCode", ""),
        rpt.get("stockName", ""),
        rpt.get("title", ""),
        rpt.get("orgSName") or rpt.get("orgName", ""),
        publish_date,
        rpt.get("indvInduName", ""),
        rpt.get("emRatingName") or "",
        str(rpt.get("ratingChange", "")),
        rpt.get("researcher", ""),
        target_high,
        target_low,
        _n(rpt.get("predictThisYearEps")),
        _n(rpt.get("predictThisYearPe")),
        _n(rpt.get("predictNextYearEps")),
        _n(rpt.get("predictNextYearPe")),
        _n(rpt.get("predictNextTwoYearEps")),
        _n(rpt.get("predictNextTwoYearPe")),
        core_text,
        DETAIL_URL_TPL.format(infoCode=info_code),
        today_str,
    ))
    conn.commit()
    conn.close()


# ── 工具函数 ──────────────────────────────────────────────────────


def safe_request(url: str, params: dict | None = None, retries: int = 3) -> requests.Response | None:
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            if attempt < retries - 1:
                time.sleep(REQUEST_INTERVAL * (attempt + 1))
                continue
            print(f"  [WARN] 请求失败: {url} — {e}", file=sys.stderr)
    return None


def parse_rating_change(code: str | int | None) -> str:
    mapping = {1: "调高", 2: "首次", 3: "维持", 4: "调低", 5: "无"}
    if code is None or code == "":
        return "-"
    try:
        return mapping.get(int(code), f"未知({code})")
    except (ValueError, TypeError):
        return "-"


def parse_em_rating(name: str | None) -> str:
    return name or "-"


def extract_core_content(html_text: str) -> dict[str, Any]:
    """从研报详情页 HTML 提取核心内容"""
    soup = BeautifulSoup(html_text, "html.parser")
    result = {"title": "", "full_text": "", "sections": []}

    h1 = soup.find("h1") or soup.find("h2") or soup.find(class_="title")
    if h1:
        result["title"] = h1.get_text(strip=True)

    content_selectors = [
        {"class_": "zw-content"}, {"class_": "ctx-body"},
        {"class_": "ctx-content"}, {"class_": "left"},
        {"class_": "stocknews-content"},
    ]
    content_div = None
    for sel in content_selectors:
        content_div = soup.find("div", **sel)
        if content_div:
            break

    if not content_div:
        all_ps = soup.find_all("p")
        texts = []
        for p in all_ps:
            t = p.get_text(strip=True)
            if len(t) > 20 and not any(kw in t for kw in
                                        ["东方财富", "风险提示", "免责", "上一篇", "下一篇", "相关", "收藏"]):
                texts.append(t)
        if texts:
            result["full_text"] = "\n\n".join(texts)
        return result

    noise_keywords = [
        "免责", "风险提示", "东方财富", "上一篇", "下一篇", "数据来源",
        "郑重声明", "更多点击查看", "今日最新", "查看PDF原文",
        "扫一扫下载", "copyright", "版权所有",
        "数据推荐", "调高投资评级", "调低投资评级", "首次评级",
        "股票盈利预测排行", "最新研究报告", "买入评级个股",
        "热门机构", "机构一致看多", "更多热门", "行业追踪",
    ]
    for tag in content_div.find_all(["p", "div", "h3", "h4", "strong"]):
        t = tag.get_text(strip=True)
        if not t or len(t) < 5:
            continue
        if any(kw in t for kw in noise_keywords):
            continue
        if t.startswith("http") or t.startswith("www."):
            continue
        is_header = tag.name in ("h3", "h4", "strong")
        if is_header:
            result["sections"].append({"header": t, "content": []})
        else:
            if result["sections"]:
                result["sections"][-1]["content"].append(t)
            else:
                result["sections"].append({"header": "正文", "content": [t]})
        result["full_text"] += t + "\n"
    return result


def fetch_report_list(page_size: int = 10, days: int = 1, page_no: int = 1) -> list[dict]:
    end_date = datetime.now()
    begin_date = end_date - timedelta(days=days)
    params = {
        "pageSize": page_size, "pageNo": page_no,
        "beginTime": begin_date.strftime("%Y-%m-%d"),
        "endTime": end_date.strftime("%Y-%m-%d"),
        "qType": 0, "industryCode": "*", "code": "*",
        "fields": "", "rating": "", "ratingChange": "", "orgCode": "",
    }
    resp = safe_request(API_LIST, params=params)
    if not resp:
        print("[ERROR] 获取研报列表失败", file=sys.stderr)
        return []
    try:
        data = resp.json()
    except json.JSONDecodeError:
        print("[ERROR] 解析研报列表 JSON 失败", file=sys.stderr)
        return []
    reports = data.get("data", [])
    total = data.get("hits", 0)
    total_pages = data.get("TotalPage", 1)
    print(f"[INFO] 共 {total} 条研报, {total_pages} 页, 当前第 {page_no} 页")
    return reports


def fetch_report_detail(info_code: str) -> dict[str, Any] | None:
    url = DETAIL_URL_TPL.format(infoCode=info_code)
    resp = safe_request(url)
    if not resp:
        return None
    return extract_core_content(resp.text)


def generate_markdown_report(reports: list[dict], max_reports: int = 5,
                              fetch_detail: bool = True) -> str:
    lines = []
    lines.append("# 东方财富个股研报 · 核心观点速览\n")
    lines.append(f"> 数据来源：东方财富数据中心 | 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n")

    for i, rpt in enumerate(reports[:max_reports]):
        stock_name = rpt.get("stockName", "")
        stock_code = rpt.get("stockCode", "")
        title = rpt.get("title", "")
        org_name = rpt.get("orgSName") or rpt.get("orgName", "")
        pub_date = (rpt.get("publishDate") or "")[:10]
        rating = parse_em_rating(rpt.get("emRatingName"))
        rating_chg = parse_rating_change(rpt.get("ratingChange"))
        industry = rpt.get("indvInduName", "")
        researcher = rpt.get("researcher", "")
        info_code = rpt.get("infoCode", "")

        eps_this = rpt.get("predictThisYearEps", "")
        pe_this = rpt.get("predictThisYearPe", "")
        eps_next = rpt.get("predictNextYearEps", "")
        pe_next = rpt.get("predictNextYearPe", "")
        eps_next2 = rpt.get("predictNextTwoYearEps", "")
        pe_next2 = rpt.get("predictNextTwoYearPe", "")

        lines.append(f"\n─── {i+1}. {stock_name}（{stock_code}）───\n")
        lines.append(f"**报告标题**：{title}")
        lines.append(f"**机构**：{org_name}　**日期**：{pub_date}")
        lines.append(f"**行业**：{industry}　**评级**：{rating}（{rating_chg}）")
        lines.append(f"**研究员**：{researcher}")

        # 目标价
        tp_high = rpt.get("indvAimPriceT", "")
        tp_low = rpt.get("indvAimPriceL", "")
        if tp_high:
            lines.append(f"**目标价**：{tp_low}~{tp_high}" if tp_low and tp_low != tp_high else f"**目标价**：{tp_high}")

        eps_parts = []
        if eps_this and pe_this:
            eps_parts.append(f"本年 EPS {eps_this}（PE {pe_this}）")
        if eps_next and pe_next:
            eps_parts.append(f"明年 EPS {eps_next}（PE {pe_next}）")
        if eps_next2 and pe_next2:
            eps_parts.append(f"后年 EPS {eps_next2}（PE {pe_next2}）")
        if eps_parts:
            lines.append(f"**盈利预测**：{' | '.join(eps_parts)}")
        if info_code:
            lines.append(f"**研报原文**：[查看详情]({DETAIL_URL_TPL.format(infoCode=info_code)})")

        if fetch_detail and info_code:
            print(f"[INFO] 正在提取: {stock_name}({stock_code}) — {title[:30]}...")
            detail = fetch_report_detail(info_code)
            if detail:
                sections = detail.get("sections", [])
                core_paragraphs = []
                for sec in sections:
                    header = sec.get("header", "")
                    contents = sec.get("content", [])
                    if header and header not in ("正文",):
                        core_paragraphs.append(f"\n**{header}**")
                    for c in contents:
                        if len(c) > 15:
                            core_paragraphs.append(c)
                if not core_paragraphs:
                    paragraphs = [p.strip() for p in detail.get("full_text", "").split("\n") if p.strip()]
                    for p in paragraphs:
                        if len(p) > 30:
                            core_paragraphs.append(p)
                if core_paragraphs:
                    lines.append("\n**核心观点**：")
                    for p in core_paragraphs[:8]:
                        lines.append(f"> {p}")
                else:
                    lines.append("\n> *未能提取到正文内容*")
            else:
                lines.append("\n> *研报详情提取失败*")
            time.sleep(REQUEST_INTERVAL)
        lines.append("")
    return "\n".join(lines)


# ── 主入口 ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="东方财富个股研报核心观点提取器 + 数据库存储",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python fetch_stock_reports.py                         # 最近1天+入库+终端输出\n"
            "  python fetch_stock_reports.py --days 3 --top 20      # 最近3天取20篇\n"
            "  python fetch_stock_reports.py --no-detail --no-db    # 仅列表，不抓正文不入库\n"
            "  python fetch_stock_reports.py --db my_reports.db     # 指定数据库路径\n"
            "  python fetch_stock_reports.py -o today.md            # 输出到文件\n"
        ),
    )
    parser.add_argument("--days", type=int, default=1, help="回溯天数（默认 1）")
    parser.add_argument("--top", type=int, default=10, help="提取前 N 篇（默认 10）")
    parser.add_argument("--no-detail", action="store_true", help="不抓取正文详情")
    parser.add_argument("--no-db", action="store_true", help="不入库")
    parser.add_argument("--append", action="store_true", help="追加模式：仅新增不存在的研报，跳过已入库的")
    parser.add_argument("--db", type=str, default=DEFAULT_DB, help=f"数据库路径（默认 {DEFAULT_DB}）")
    parser.add_argument("--output", "-o", type=str, default="", help="输出到文件（默认终端）")
    args = parser.parse_args()

    # 初始化数据库
    if not args.no_db:
        init_db(args.db)
        print(f"[INFO] 数据库: {args.db}")

    print(f"[INFO] 获取最近 {args.days} 天的个股研报...")
    reports = fetch_report_list(page_size=args.top, days=args.days)
    if not reports:
        print("[ERROR] 未获取到研报数据", file=sys.stderr)
        sys.exit(1)

    print(f"[INFO] 获取到 {len(reports)} 篇研报")

    # 追加模式：查询已存在的研报，仅处理新报告
    existing_codes = get_existing_codes(args.db) if (not args.no_db and args.append) else set()
    new_reports = [r for r in reports if r.get("infoCode", "") not in existing_codes] if args.append else reports
    skipped = len(reports) - len(new_reports)
    if args.append and skipped:
        print(f"[INFO] 追加模式: {skipped} 篇已存在已跳过, {len(new_reports)} 篇待处理")

    # 逐篇处理：抓取详情 → 入库 → 收集
    saved = 0
    for rpt in new_reports:
        info_code = rpt.get("infoCode", "")
        stock_name = rpt.get("stockName", "")
        stock_code = rpt.get("stockCode", "")
        title = rpt.get("title", "")
        core_text = ""

        if not args.no_detail and info_code:
            print(f"[INFO] 提取: {stock_name}({stock_code}) — {title[:30]}...")
            detail = fetch_report_detail(info_code)
            if detail:
                # 拼接核心观点文本
                parts = []
                for sec in detail.get("sections", []):
                    h = sec.get("header", "")
                    if h and h not in ("正文",):
                        parts.append(h)
                    parts.extend(c for c in sec.get("content", []) if len(c) > 15)
                if not parts:
                    ft = detail.get("full_text", "")
                    parts = [p for p in ft.split("\n") if len(p.strip()) > 30]
                core_text = "\n".join(parts)
            time.sleep(REQUEST_INTERVAL)

        if not args.no_db:
            upsert_report(args.db, rpt, core_text, append_only=args.append)
            saved += 1

    if saved:
        print(f"[INFO] 本次新增入库 {saved} 篇研报")

    # 输出 Markdown 报告
    markdown = generate_markdown_report(reports, max_reports=args.top,
                                         fetch_detail=not args.no_detail)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(markdown)
        print(f"[INFO] 报告已保存至: {args.output}")
    else:
        print("\n" + markdown)

    # 入库统计
    if not args.no_db:
        conn = sqlite3.connect(args.db)
        total = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        today_count = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE fetch_date=?", (datetime.now().strftime("%Y-%m-%d"),)
        ).fetchone()[0]
        stocks = conn.execute("SELECT COUNT(*) FROM v_stock_coverage").fetchone()[0]
        conn.close()
        print(f"\n═══ 数据库统计 ═══")
        print(f"  累计研报: {total} 篇 | 今日新增: {today_count}")
        print(f"  覆盖个股: {stocks} 只")
        print(f"  查询分析: python query_reports.py")


if __name__ == "__main__":
    main()
