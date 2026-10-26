# -*- coding: utf-8 -*-
"""FinchX 3.x 题材热度 -> 龙头 · 每日/盘中快照

数据来源（finchx 3.1.x，均直接返回原生 dict / list）:
  hotlist.sectors          概念板块热榜（热度 / 涨停家数 / 上榜频次 / 关联 ETF）
  hotlist.stocks           热股榜（人气标签=连板高度 / 概念标签 / 热度）
  articles.hot_content     热门话题 topic（话题 + 关联个股）
  盘中模式另加:
  sector.ths_concept_list / ths_concept_quote_snapshot   全量概念实时快照
  market.agp_sentiment / em_breadth                      情绪与涨跌分布
  market.limit_up_pool / broken_limit_pool               连板梯队与炸板

注意：3.x 的 *change_percent / *percent 字段已经是百分数（2.46 表示 +2.46%），
不再像 2.x 那样是比例小数，展示时不要再乘 100。

leader_score 口径（显式、可审计）:
  连板高度 x 20  +  双创加成(300/301/688) 8
  + 题材涨停家数 x 2  + 题材内热度排名分(max(2, 10 - 2*位次))

用法:
  python fx_theme_snapshot.py                        # 热榜版
  python fx_theme_snapshot.py --mode intraday        # 盘中全景版
  python fx_theme_snapshot.py --sectors 40 --stocks 80 --topics 10
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from finchx import FinchX

CN_TZ = ZoneInfo("Asia/Shanghai")
OUT_DIR = Path(__file__).resolve().parent

# 双创板：创业板 300/301、科创板 688
DUAL_BOARD_PREFIX = ("300", "301", "688")

_RE_BOARD = re.compile(r"(\d+)\s*天\s*(\d+)\s*板")
_RE_UP_CONSEC = re.compile(r"连续\s*(\d+)\s*天上榜")
_RE_UP_TIMES = re.compile(r"(\d+)\s*天\s*(\d+)\s*次上榜")
_RE_LIMITUP = re.compile(r"(\d+)\s*家涨停")

SEP = "\u2500" * 46
SEP2 = "\u2550" * 46

# 直连兜底：finchx 3.1.1 的 hot_content 只认 topic/deep_topic，
# 上游一旦出现新的业务类型（如 special 专题页）整份榜单就会抛错，这里自行取数。
_THS_TOPIC_URL = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/topic"
_THS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://eq.10jqka.com.cn/",
}


def to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def fmt_yi(value) -> str:
    """元 -> 亿。"""
    if value is None:
        return "-"
    return f"{float(value) / 1e8:.2f}亿"


def pct(value) -> str:
    """百分数 -> 带符号文本（输入已是 2.46 这种百分点）。"""
    return f"{to_float(value):+.2f}%"


def fmt_pct(value, digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{float(value):.{digits}f}%"


def is_dual_board(code: str) -> bool:
    return bool(code) and code[:3] in DUAL_BOARD_PREFIX


def parse_board_height(popularity_tag) -> int:
    """人气标签 -> 连板高度: '2天1板'->1, '5天3板'->3, '首板'->1, 其余 0。"""
    if not popularity_tag:
        return 0
    m = _RE_BOARD.search(popularity_tag)
    if m:
        return int(m.group(2))
    if "首板" in popularity_tag:
        return 1
    return 0


def parse_theme_freq(hot_tag) -> tuple[int, str]:
    """板块 hot_tag -> 上榜强度: '连续132天上榜'->(132,'连续')；'10天9次上榜'->(9,'频次')。"""
    if not hot_tag:
        return 0, ""
    m = _RE_UP_CONSEC.search(hot_tag)
    if m:
        return int(m.group(1)), "连续"
    m = _RE_UP_TIMES.search(hot_tag)
    if m:
        return int(m.group(2)), "频次"
    return 0, ""


def parse_limitup_count(tag) -> int:
    if not tag:
        return 0
    m = _RE_LIMITUP.search(tag)
    return int(m.group(1)) if m else 0


def resolve_trade_date(fx: FinchX, warnings: list[str]) -> str:
    """取 <= 今天的最近一个 A 股交易日，作为快照标签。"""
    today = datetime.now(CN_TZ).date()
    try:
        rows = fx.reference.trading_calendar(
            (today - timedelta(days=25)).isoformat(), today.isoformat()
        )
        days = [r["date"] for r in rows if r.get("is_trading_day")]
        if days:
            return max(days)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"交易日历失败，回退自然日: {type(exc).__name__}: {exc}")
    return today.isoformat()


def theme_strength_label(limitup_count: int) -> str:
    if limitup_count >= 3:
        return "强"
    if limitup_count >= 1:
        return "中"
    return "弱"


def leader_score(board: int, dual_board: bool, limitup_count: int, position: int) -> int:
    return (
        board * 20
        + (8 if dual_board else 0)
        + limitup_count * 2
        + max(2, 10 - position * 2)
    )


# ============================ 热榜模式 ============================

def fetch_theme_base(fx: FinchX, limit: int, warnings: list[str]) -> list[dict]:
    try:
        rows = fx.hotlist.sectors("concept", limit)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"hotlist.sectors 失败: {type(exc).__name__}: {exc}")
        return []

    themes: list[dict] = []
    for row in rows:
        freq, freq_kind = parse_theme_freq(row.get("hot_tag"))
        themes.append(
            {
                "rank": row.get("rank"),
                "name": row.get("name"),
                "code": row.get("sector_code"),
                "heat": to_float(row.get("heat")),
                "change_pct": to_float(row.get("change_percent")),
                "rank_change": row.get("rank_change") or 0,
                "limitup_count": parse_limitup_count(row.get("tag")),
                "tag": row.get("tag"),
                "hot_tag": row.get("hot_tag"),
                "freq": freq,
                "freq_kind": freq_kind,
                "etf_symbol": row.get("related_etf_code"),
                "etf_name": row.get("related_etf_name"),
                "etf_change_pct": to_float(row.get("related_etf_change_percent")),
                "stocks": [],
            }
        )
    return themes


def fetch_hot_stocks(fx: FinchX, limit: int, warnings: list[str]) -> list[dict]:
    rows = None
    for period in ("24h", "1h"):
        try:
            rows = fx.hotlist.stocks("popular", period, limit)
            break
        except Exception as exc:  # noqa: BLE001
            # 3.1.1 的 market 映射只有 17/33/151，日榜偶有风险警示板(22)会整份失败
            warnings.append(f"hotlist.stocks({period}) 失败: {type(exc).__name__}: {exc}")
    if rows is None:
        return []

    stocks: list[dict] = []
    for row in rows:
        code = row.get("code") or ""
        stocks.append(
            {
                "code": code,
                "name": row.get("name"),
                "heat": to_float(row.get("heat")),
                "change_pct": to_float(row.get("change_percent")),
                "board": parse_board_height(row.get("popularity_tag")),
                "popularity_tag": row.get("popularity_tag"),
                "rank": row.get("rank"),
                "rank_change": row.get("rank_change") or 0,
                "concept_tags": row.get("concept_tags") or [],
                "dual_board": is_dual_board(code),
                "themes": [],
            }
        )
    return stocks


def attach_stocks_to_themes(themes: list[dict], stocks: list[dict]) -> None:
    by_name = {t["name"]: t for t in themes}

    def match(tag: str) -> list[dict]:
        if tag in by_name:
            return [by_name[tag]]
        if len(tag) < 2:
            return []
        return [t for t in themes if tag in t["name"] or t["name"] in tag]

    for stock in stocks:
        matched: list[dict] = []
        for tag in stock["concept_tags"]:
            for theme in match(tag):
                if theme not in matched:
                    matched.append(theme)
        stock["themes"] = [t["name"] for t in matched]
        for theme in matched:
            theme["stocks"].append(stock)

    for theme in themes:
        # 同一只股票可能因多个概念标签重复挂入，去重
        uniq = list({s["code"]: s for s in theme["stocks"]}.values())
        ordered = sorted(uniq, key=lambda s: (-s["board"], -s["heat"]))
        views = []
        for pos, stock in enumerate(ordered):
            if stock["board"] >= 2:
                tier = "龙头"
            elif stock["board"] == 1:
                tier = "首板"
            elif pos < 3:
                tier = "中军"
            else:
                tier = "跟风"
            views.append(
                {
                    "code": stock["code"],
                    "name": stock["name"],
                    "tier": tier,
                    "board": stock["board"],
                    "popularity_tag": stock["popularity_tag"],
                    "dual_board": stock["dual_board"],
                    "change_pct": stock["change_pct"],
                    "heat": stock["heat"],
                    "rank": stock["rank"],
                    "rank_change": stock["rank_change"],
                    "leader_score": leader_score(
                        stock["board"], stock["dual_board"], theme["limitup_count"], pos
                    ),
                }
            )
        theme["stocks"] = views
        theme["strength"] = theme_strength_label(theme["limitup_count"])
        theme["dual_board_count"] = sum(1 for v in views if v["dual_board"])


def fetch_topics_direct(limit: int, warnings: list[str]) -> list[dict]:
    """直连同花顺话题榜，跳过库不认识的业务类型（如 special 专题页）。"""
    try:
        req = urllib.request.Request(
            f"{_THS_TOPIC_URL}?page=1&page_size=30", headers=_THS_HEADERS
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        raw_rows = payload["data"]["topic_list"]
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"话题榜直连失败: {type(exc).__name__}: {exc}")
        return []

    topics: list[dict] = []
    skipped = 0
    for raw in raw_rows:
        if raw.get("type") not in {"topic", "deep_topic"}:
            skipped += 1
            continue
        info = raw.get("attach_info") or {}
        topics.append(
            {
                "rank": len(topics) + 1,
                "title": raw.get("title"),
                "summary": raw.get("description"),
                "heat": to_float(raw.get("hot_value")),
                "url": raw.get("jump_url"),
                "related_stocks": [
                    {
                        "code": item.get("code"),
                        "name": (item.get("name") or "").replace(" ", ""),
                        "change_pct": to_float(item.get("rise_and_fall")),
                    }
                    for item in (info.get("att_stock") or [])
                    if isinstance(item, dict)
                ],
            }
        )
        if len(topics) >= limit:
            break
    if skipped:
        warnings.append(f"话题榜跳过 {skipped} 条未识别业务类型")
    return topics


def fetch_topics(fx: FinchX, limit: int, warnings: list[str]) -> list[dict]:
    try:
        rows = fx.articles.hot_content("topic", limit)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"articles.hot_content 失败，转直连: {type(exc).__name__}: {exc}")
        return fetch_topics_direct(limit, warnings)

    topics = []
    for row in rows:
        topics.append(
            {
                "rank": row.get("rank"),
                "title": row.get("title"),
                "summary": row.get("summary"),
                "heat": to_float(row.get("heat")),
                "url": row.get("url"),
                "related_stocks": [
                    {
                        "code": item.get("code"),
                        "name": (item.get("name") or "").replace(" ", ""),
                        "change_pct": to_float(item.get("change_percent")),
                    }
                    for item in (row.get("related_securities") or [])
                ],
            }
        )
    return topics


def collect_leaders(themes: list[dict], uncovered: list[dict]) -> list[dict]:
    """汇总所有 龙头 / 首板 / 中军；未归类的连板股(>=2板)单列为「未归类」龙头。"""
    rows: list[dict] = []
    for theme in themes:
        for stock in theme["stocks"]:
            if stock["tier"] in {"龙头", "首板", "中军"}:
                rows.append(
                    {
                        "code": stock["code"],
                        "name": stock["name"],
                        "tier": stock["tier"],
                        "theme": theme["name"],
                        "theme_strength": theme["strength"],
                        "board": stock["board"],
                        "popularity_tag": stock["popularity_tag"],
                        "dual_board": stock["dual_board"],
                        "change_pct": stock["change_pct"],
                        "leader_score": stock["leader_score"],
                    }
                )
    for stock in uncovered:
        if stock["board"] >= 2:
            dual = is_dual_board(stock["code"])
            rows.append(
                {
                    "code": stock["code"],
                    "name": stock["name"],
                    "tier": "龙头",
                    "theme": "未归类",
                    "theme_strength": "-",
                    "board": stock["board"],
                    "popularity_tag": stock["popularity_tag"],
                    "dual_board": dual,
                    "change_pct": stock["change_pct"],
                    "leader_score": stock["board"] * 20 + (8 if dual else 0),
                }
            )
    order = {"龙头": 0, "首板": 1, "中军": 2}
    rows.sort(key=lambda r: (order.get(r["tier"], 9), -r["board"], -r["leader_score"]))
    return rows


def build_snapshot(sector_limit: int, stock_limit: int, topic_limit: int) -> dict:
    fx = FinchX()
    warnings: list[str] = []
    trade_date = resolve_trade_date(fx, warnings)
    themes = fetch_theme_base(fx, sector_limit, warnings)
    stocks = fetch_hot_stocks(fx, stock_limit, warnings)
    attach_stocks_to_themes(themes, stocks)
    topics = fetch_topics(fx, topic_limit, warnings)

    uncovered = [s for s in stocks if not s["themes"]]

    return {
        "generated_at": datetime.now(CN_TZ).isoformat(),
        "trade_date": trade_date,
        "source": "finchx 3.x / tonghuashun.hotlist + articles",
        "params": {
            "sector_limit": sector_limit,
            "stock_limit": stock_limit,
            "topic_limit": topic_limit,
        },
        "warnings": warnings,
        "leaders": collect_leaders(themes, uncovered),
        "themes": themes,
        "uncovered_stocks": [
            {
                "code": s["code"],
                "name": s["name"],
                "board": s["board"],
                "popularity_tag": s["popularity_tag"],
                "change_pct": s["change_pct"],
                "heat": s["heat"],
                "concept_tags": s["concept_tags"],
            }
            for s in sorted(uncovered, key=lambda x: (-x["board"], -x["heat"]))
        ],
        "topics": topics,
    }


def print_summary(snapshot: dict) -> None:
    print(SEP2)
    print(f"题材热度 → 龙头 快照  |  交易日 {snapshot['trade_date']}")
    print(f"生成 {snapshot['generated_at'][:19]}  |  来源 {snapshot['source']}")
    if snapshot["warnings"]:
        print(f"告警 {snapshot['warnings']}")
    print(SEP2)

    leaders = snapshot["leaders"]
    print(f"龙头 / 中军 汇总（{len(leaders)} 只）")
    if not leaders:
        print("  (无)")
    for row in leaders:
        dual = " 双创" if row["dual_board"] else ""
        tag = f" {row['popularity_tag']}" if row["popularity_tag"] else ""
        print(
            f"  [{row['tier']}] {row['code']} {row['name']}{dual} "
            f"{pct(row['change_pct'])}{tag} | {row['theme']}({row['theme_strength']}) "
            f"分{row['leader_score']}"
        )
    print(SEP2)

    for theme in snapshot["themes"]:
        head = (
            f"#{theme['rank']} {theme['name']} [{theme['strength']}]  "
            f"热度 {theme['heat']:.0f} | {theme['tag'] or '-'} | {theme['hot_tag'] or '-'}"
        )
        print(head)
        if theme["etf_symbol"]:
            print(
                f"    关联ETF {theme['etf_symbol']} {theme['etf_name']} "
                f"({pct(theme['etf_change_pct'])})"
            )
        if not theme["stocks"]:
            print("    (热股榜内无该题材个股)")
        for row in theme["stocks"][:5]:
            tag = f" {row['popularity_tag']}" if row["popularity_tag"] else ""
            dual = " 双创" if row["dual_board"] else ""
            print(
                f"    [{row['tier']}] {row['code']} {row['name']}{dual} "
                f"{pct(row['change_pct'])}{tag} 分{row['leader_score']}"
            )
        print(SEP)

    if snapshot["topics"]:
        print("当日热门话题 →")
        for topic in snapshot["topics"]:
            rel = "、".join(
                f"{s['name']}({pct(s['change_pct'])})" for s in topic["related_stocks"]
            )
            print(f"  #{topic['rank']} {topic['title']}")
            if rel:
                print(f"      关联 {rel}")
        print(SEP)

    if snapshot["uncovered_stocks"]:
        top = snapshot["uncovered_stocks"][:8]
        print("未归类热股（热榜有、概念榜未覆盖）→")
        for row in top:
            tags = "/".join(row["concept_tags"][:3])
            print(
                f"  {row['code']} {row['name']} {pct(row['change_pct'])} "
                f"{row['popularity_tag'] or ''} [{tags}]"
            )
        print(SEP2)


def render_html(snapshot: dict) -> str:
    def esc(text) -> str:
        return (
            str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    parts: list[str] = []
    for theme in snapshot["themes"]:
        etf = ""
        if theme["etf_symbol"]:
            etf = (
                f'<div class="etf">关联ETF {esc(theme["etf_symbol"])} '
                f'{esc(theme["etf_name"])} {pct(theme["etf_change_pct"])}</div>'
            )
        rows = ""
        for row in theme["stocks"][:6]:
            cls = {"龙头": "l", "首板": "f", "中军": "m"}.get(row["tier"], "g")
            dual = '<span class="dual">双创</span>' if row["dual_board"] else ""
            tag = (
                f'<span class="tag">{esc(row["popularity_tag"])}</span>'
                if row["popularity_tag"]
                else ""
            )
            up = "up" if row["change_pct"] >= 0 else "down"
            rows += (
                "<tr>"
                f'<td><span class="tier {cls}">{row["tier"]}</span></td>'
                f'<td class="code">{esc(row["code"])}</td>'
                f'<td>{esc(row["name"])}{dual}{tag}</td>'
                f'<td class="{up}">{pct(row["change_pct"])}</td>'
                f'<td class="num">{row["leader_score"]}</td>'
                "</tr>"
            )
        if not rows:
            rows = '<tr><td colspan="5" class="empty">热股榜内暂无该题材个股</td></tr>'

        parts.append(
            '<div class="card">'
            f'<div class="th"><span class="rk">#{theme["rank"]}</span>'
            f'<span class="tn">{esc(theme["name"])}</span>'
            f'<span class="st s-{theme["strength"]}">{theme["strength"]}</span>'
            f'<span class="ht">热度 {theme["heat"]:.0f}</span></div>'
            f'<div class="meta">{esc(theme["tag"] or "-")} · {esc(theme["hot_tag"] or "-")}'
            f' · 双创 {theme["dual_board_count"]} 只</div>'
            f"{etf}"
            '<div class="tbl"><table>'
            "<tr><th>角色</th><th>代码</th><th>名称</th><th>涨跌</th><th>分</th></tr>"
            f"{rows}</table></div>"
            "</div>"
        )

    topic_parts = ""
    for topic in snapshot["topics"]:
        rel = "、".join(
            f'{s["name"]}({pct(s["change_pct"])})' for s in topic["related_stocks"]
        )
        rel_html = f'<div class="rel">关联 {esc(rel)}</div>' if rel else ""
        topic_parts += (
            '<div class="topic">'
            f'<span class="trk">#{topic["rank"]}</span> {esc(topic["title"])}'
            f'<div class="sum">{esc(topic["summary"] or "")}</div>{rel_html}</div>'
        )
    if not topic_parts:
        topic_parts = '<div class="empty">无</div>'

    unc = ""
    for row in snapshot["uncovered_stocks"][:12]:
        tags = " / ".join(row["concept_tags"][:3])
        unc += (
            '<div class="uncitem">'
            f'<b>{esc(row["code"])} {esc(row["name"])}</b> '
            f'<span class="{"up" if row["change_pct"] >= 0 else "down"}">'
            f'{pct(row["change_pct"])}</span> '
            f'<span class="tag">{esc(row["popularity_tag"] or "")}</span>'
            f'<div class="unc-tags">{esc(tags)}</div></div>'
        )
    if not unc:
        unc = '<div class="empty">无</div>'

    warn_html = ""
    if snapshot["warnings"]:
        warn_html = (
            '<div class="warn">告警：'
            + "；".join(esc(w) for w in snapshot["warnings"])
            + "</div>"
        )

    lead_rows = ""
    for row in snapshot["leaders"]:
        cls = {"龙头": "l", "首板": "f", "中军": "m"}.get(row["tier"], "g")
        dual = '<span class="dual">双创</span>' if row["dual_board"] else ""
        tag = (
            f'<span class="tag">{esc(row["popularity_tag"])}</span>'
            if row["popularity_tag"]
            else ""
        )
        up = "up" if row["change_pct"] >= 0 else "down"
        lead_rows += (
            "<tr>"
            f'<td><span class="tier {cls}">{row["tier"]}</span></td>'
            f'<td class="code">{esc(row["code"])}</td>'
            f'<td>{esc(row["name"])}{dual}{tag}</td>'
            f'<td>{esc(row["theme"])}<span class="st2">{esc(row["theme_strength"])}</span></td>'
            f'<td class="{up}">{pct(row["change_pct"])}</td>'
            f'<td class="num">{row["leader_score"]}</td>'
            "</tr>"
        )
    if not lead_rows:
        lead_rows = '<tr><td colspan="6" class="empty">无</td></tr>'
    leaders_html = (
        '<div class="card"><div class="tbl"><table>'
        "<tr><th>角色</th><th>代码</th><th>名称</th><th>题材</th><th>涨跌</th><th>分</th></tr>"
        f"{lead_rows}</table></div></div>"
    )

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>题材热度 → 龙头 {snapshot['trade_date']}</title>
<style>
body{{margin:0;padding:12px;background:#f2f4f8;color:#1a1a1a;
font-size:22px;line-height:1.8;-webkit-text-size-adjust:100%;
font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
.hd{{background:linear-gradient(135deg,#2563eb,#1e40af);color:#fff;
border-radius:14px;padding:16px 18px;margin-bottom:14px}}
.hd h1{{font-size:26px;margin:0 0 6px}}
.hd .sub{{font-size:20px;opacity:.9}}
.card{{background:#fff;border-radius:14px;padding:14px 16px;margin-bottom:14px;
box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.th{{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin-bottom:4px}}
.th .rk{{color:#2563eb;font-weight:700}}
.th .tn{{font-size:24px;font-weight:700}}
.st{{font-size:19px;border-radius:6px;padding:1px 9px;color:#fff}}
.s-强{{background:#dc2626}}.s-中{{background:#f59e0b}}.s-弱{{background:#94a3b8}}
.ht{{font-size:19px;color:#64748b}}
.meta{{font-size:19px;color:#64748b;margin-bottom:6px}}
.etf{{font-size:19px;color:#0f766e;margin-bottom:6px}}
.tbl{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;min-width:520px}}
th,td{{padding:7px 10px;text-align:left;font-size:21px;white-space:nowrap;
border-bottom:1px solid #eef1f6}}
th{{background:#f8fafc;color:#475569;font-weight:600}}
.code{{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:20px}}
.num{{text-align:right}}
.tier{{font-size:18px;border-radius:6px;padding:1px 8px;color:#fff}}
.tier.l{{background:#dc2626}}.tier.f{{background:#f97316}}
.tier.m{{background:#2563eb}}.tier.g{{background:#94a3b8}}
.dual{{font-size:17px;color:#7c3aed;border:1px solid #ddd6fe;border-radius:5px;
padding:0 5px;margin-left:6px}}
.tag{{font-size:18px;color:#b45309;background:#fef3c7;border-radius:5px;
padding:0 6px;margin-left:6px}}
.up{{color:#dc2626}}.down{{color:#16a34a}}
.topic{{background:#fff;border-radius:12px;padding:12px 16px;margin-bottom:10px;
box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.trk{{color:#2563eb;font-weight:700}}
.sum{{font-size:20px;color:#475569;margin-top:4px}}
.rel{{font-size:20px;color:#0f766e;margin-top:4px}}
.uncitem{{background:#fff;border-radius:12px;padding:10px 16px;margin-bottom:8px;
box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.unc-tags{{font-size:19px;color:#64748b}}
.st2{{font-size:17px;color:#64748b;border:1px solid #e2e8f0;border-radius:5px;
padding:0 5px;margin-left:6px}}
.sec{{font-size:24px;font-weight:700;margin:18px 0 10px;padding-left:10px;
border-left:5px solid #2563eb}}
.empty{{color:#94a3b8;font-size:20px}}
.warn{{background:#fef2f2;color:#b91c1c;border-radius:10px;padding:10px 14px;
font-size:20px;margin-bottom:12px}}
.ft{{color:#94a3b8;font-size:19px;text-align:center;padding:10px 0 20px}}
</style></head><body>
<div class="hd">
<h1>题材热度 → 龙头 快照</h1>
<div class="sub">交易日 {snapshot['trade_date']} · 生成 {snapshot['generated_at'][11:19]}</div>
</div>
{warn_html}
<div class="sec">龙头 / 中军 汇总（{len(snapshot['leaders'])} 只）</div>
{leaders_html}
<div class="sec">题材热度榜（同花顺概念）</div>
{''.join(parts)}
<div class="sec">当日热门话题 →</div>
{topic_parts}
<div class="sec">未归类热股</div>
{unc}
<div class="ft">数据来源 finchx 3.x / tonghuashun.hotlist · 仅供研究，不构成投资建议</div>
</body></html>"""


# ============================ 盘中模式 ============================

def _concept_row(record: dict | None) -> dict | None:
    if not record:
        return None
    name = record.get("name")
    if not name:
        return None
    stamp = " ".join(
        part for part in (record.get("date"), record.get("time")) if part
    )
    return {
        "name": name,
        "code": record.get("code"),
        "change_rate": to_float(record.get("change_percent")),
        "index_level": to_float(record.get("index_level")),
        "amount": record.get("amount"),
        "net_money_flow": record.get("net_inflow"),
        "rise_count": record.get("up_count"),
        "fall_count": record.get("down_count"),
        "source_timestamp": stamp or None,
    }


def fetch_concept_panorama(fx: FinchX, workers: int, warnings: list[str]) -> list[dict]:
    """全量概念实时快照：概念目录(代码/名称) -> 逐个 ths_concept_quote_snapshot，并发。"""
    try:
        catalog = fx.sector.ths_concept_list()
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"ths_concept_list 失败: {type(exc).__name__}: {exc}")
        return []

    def one(item: dict):
        try:
            return _concept_row(fx.sector.ths_concept_quote_snapshot(item["code"])), None
        except Exception as exc:  # noqa: BLE001
            return None, f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        first = list(pool.map(one, catalog))

    rows = [r for r, _ in first if r]
    pending = [(item, err) for item, (_, err) in zip(catalog, first) if err]
    for _ in range(2):
        if not pending:
            break
        # 首轮失败多为并发下的偶发响应异常，降半速补两轮
        time.sleep(0.3)
        with ThreadPoolExecutor(max_workers=max(1, workers // 2)) as pool:
            again = list(pool.map(one, [item for item, _ in pending]))
        rows.extend(r for r, _ in again if r)
        pending = [
            (item, err)
            for (item, _), (_, err) in zip(pending, again)
            if err
        ]
    if pending:
        warnings.append(
            f"概念快照缺失 {len(pending)}/{len(catalog)} 个"
            f"（已重试 2 轮，样例 {pending[0][1][:60]}）"
        )
    rows.sort(key=lambda r: r["change_rate"], reverse=True)
    return rows


def fetch_object(fx: FinchX, method: str, warnings: list[str]) -> dict:
    try:
        return getattr(fx.market, method)() or {}
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{method} 失败: {type(exc).__name__}: {exc}")
        return {}


def fetch_rows(fx: FinchX, method: str, warnings: list[str]) -> list[dict]:
    try:
        return getattr(fx.market, method)() or []
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"{method} 失败: {type(exc).__name__}: {exc}")
        return []


def build_ladder(limit_up: list[dict]) -> dict[int, list[dict]]:
    ladder: dict[int, list[dict]] = {}
    for row in limit_up:
        days = int(row.get("consecutive_limit_up_days") or 1)
        ladder.setdefault(days, []).append(row)
    return dict(sorted(ladder.items(), reverse=True))


def build_intraday(workers: int, top: int) -> dict:
    fx = FinchX()
    warnings: list[str] = []
    trade_date = resolve_trade_date(fx, warnings)
    concepts = fetch_concept_panorama(fx, workers, warnings)
    sentiment = fetch_object(fx, "agp_sentiment", warnings)
    breadth = fetch_object(fx, "em_breadth", warnings)
    limit_up = fetch_rows(fx, "limit_up_pool", warnings)
    broken = fetch_rows(fx, "broken_limit_pool", warnings)

    return {
        "generated_at": datetime.now(CN_TZ).isoformat(),
        "trade_date": trade_date,
        "source": "finchx 3.x intraday (tonghuashun.concept / eastmoney / aigupiao)",
        "params": {"workers": workers, "top": top},
        "warnings": warnings,
        "concept_count": len(concepts),
        "concepts": concepts[:top],
        "sentiment": sentiment,
        "breadth": breadth,
        "limit_up": limit_up,
        "ladder": {str(k): v for k, v in build_ladder(limit_up).items()},
        "broken": broken,
    }


def print_intraday(snapshot: dict) -> None:
    s = snapshot["sentiment"]
    b = snapshot["breadth"]
    print(SEP2)
    print(f"盘中题材全景  |  交易日 {snapshot['trade_date']}")
    print(f"生成 {snapshot['generated_at'][:19]}  |  概念 {snapshot['concept_count']} 个")
    if snapshot["warnings"]:
        print(f"告警 {snapshot['warnings']}")
    print(SEP2)

    print("情绪仪表盘")
    print(
        f"  市场温度 {s.get('market_temperature', '-')}"
        f" | 总成交 {fmt_yi(s.get('total_turnover'))}"
        f" | 较昨 {fmt_yi(s.get('turnover_change_amount'))}"
    )
    print(
        f"  涨停 {b.get('limit_up_count', '-')} | 跌停 {b.get('limit_down_count', '-')}"
        f" | 涨 {b.get('advancing', '-')} | 跌 {b.get('declining', '-')}"
        f" | 炸板率 {fmt_pct(s.get('blast_break_percent'))}"
    )
    print(
        f"  连板分布 1板 {s.get('one_limit_up_count', '-')}"
        f" / 2板 {s.get('two_limit_up_count', '-')}"
        f" / 3板 {s.get('three_limit_up_count', '-')}"
        f" / 高位 {s.get('high_limit_up_count', '-')}"
    )
    print(
        f"  晋级率 二板 {fmt_pct(s.get('two_limit_up_promotion_percent'))}"
        f" | 三板 {fmt_pct(s.get('three_limit_up_promotion_percent'))}"
        f" | 高位 {fmt_pct(s.get('high_limit_up_promotion_percent'))}"
    )
    print(SEP)

    print(f"题材强度榜 TOP{len(snapshot['concepts'])}（共 {snapshot['concept_count']} 个概念，按涨幅）")
    for i, row in enumerate(snapshot["concepts"], 1):
        print(
            f"  #{i} {row['name']} {pct(row['change_rate'])}"
            f" 净流入 {fmt_yi(row['net_money_flow'])}"
            f" 涨{row['rise_count']}/跌{row['fall_count']}"
            f" 成交 {fmt_yi(row['amount'])}"
        )
    print(SEP)

    print("连板梯队")
    for days, rows in snapshot["ladder"].items():
        names = "、".join(f"{r['name']}({r.get('industry') or '-'})" for r in rows)
        print(f"  {days}板: {names}")
    print(SEP)

    if snapshot["broken"]:
        print("炸板池")
        for row in snapshot["broken"]:
            print(
                f"  {row['name']} {pct(row.get('change_percent'))}"
                f" 封板价 {to_float(row.get('limit_up_price')):.2f}"
                f" 炸板 {row.get('limit_up_break_count')} 次"
            )
        print(SEP)


def render_intraday_html(snapshot: dict) -> str:
    def esc(text) -> str:
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    s = snapshot["sentiment"]
    b = snapshot["breadth"]

    metrics = [
        ("市场温度", esc(s.get("market_temperature", "-"))),
        ("涨停", esc(b.get("limit_up_count", "-"))),
        ("跌停", esc(b.get("limit_down_count", "-"))),
        ("炸板率", fmt_pct(s.get("blast_break_percent"))),
        ("二板晋级", fmt_pct(s.get("two_limit_up_promotion_percent"), 0)),
        ("三板晋级", fmt_pct(s.get("three_limit_up_promotion_percent"), 0)),
        ("总成交", fmt_yi(s.get("total_turnover"))),
        ("较昨增量", fmt_yi(s.get("turnover_change_amount"))),
    ]
    metric_html = "".join(
        f'<div class="m"><div class="mv">{value}</div><div class="ml">{label}</div></div>'
        for label, value in metrics
    )

    concept_rows = ""
    for i, row in enumerate(snapshot["concepts"], 1):
        up = "up" if row["change_rate"] >= 0 else "down"
        flow_cls = "up" if to_float(row["net_money_flow"]) >= 0 else "down"
        concept_rows += (
            "<tr>"
            f'<td class="rk">{i}</td>'
            f'<td class="nm">{esc(row["name"])}</td>'
            f'<td class="{up}">{pct(row["change_rate"])}</td>'
            f'<td class="{flow_cls}">{fmt_yi(row["net_money_flow"])}</td>'
            f'<td>{row["rise_count"]}/{row["fall_count"]}</td>'
            f'<td>{fmt_yi(row["amount"])}</td>'
            "</tr>"
        )

    ladder_html = ""
    for days, rows in snapshot["ladder"].items():
        chips = "".join(
            f'<span class="chip">{esc(r["name"])}<i>{esc(r.get("industry") or "-")}</i></span>'
            for r in rows
        )
        ladder_html += f'<div class="lad"><b>{days}板</b><span class="c">{len(rows)}只</span>{chips}</div>'
    if not ladder_html:
        ladder_html = '<div class="empty">无</div>'

    broken_html = ""
    for row in snapshot["broken"]:
        broken_html += (
            '<div class="bk">'
            f'<b>{esc(row["name"])}</b>'
            f'<span class="down">{pct(row.get("change_percent"))}</span>'
            f'<span class="sm">封板价 {to_float(row.get("limit_up_price")):.2f}'
            f' · 炸板 {row.get("limit_up_break_count")} 次'
            f' · {esc(row.get("industry") or "-")}</span></div>'
        )
    if not broken_html:
        broken_html = '<div class="empty">无</div>'

    warn_html = ""
    if snapshot["warnings"]:
        warn_html = (
            '<div class="warn">告警：'
            + "；".join(esc(w) for w in snapshot["warnings"])
            + "</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>盘中题材全景 {snapshot['trade_date']}</title>
<style>
body{{margin:0;padding:12px;background:#f2f4f8;color:#1a1a1a;
font-size:22px;line-height:1.8;-webkit-text-size-adjust:100%;
font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
.hd{{background:linear-gradient(135deg,#0f766e,#155e75);color:#fff;
border-radius:14px;padding:16px 18px;margin-bottom:14px}}
.hd h1{{font-size:26px;margin:0 0 6px}}
.hd .sub{{font-size:20px;opacity:.9}}
.grid{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}}
.m{{flex:1 1 30%;background:#fff;border-radius:12px;padding:10px 12px;text-align:center;
box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.mv{{font-size:27px;font-weight:700;color:#0f172a}}
.ml{{font-size:18px;color:#64748b}}
.sec{{font-size:24px;font-weight:700;margin:18px 0 10px;padding-left:10px;
border-left:5px solid #0f766e}}
.card{{background:#fff;border-radius:14px;padding:8px 12px;margin-bottom:14px;
box-shadow:0 1px 4px rgba(0,0,0,.06);overflow-x:auto}}
table{{border-collapse:collapse;width:100%;min-width:600px}}
th,td{{padding:7px 10px;text-align:left;font-size:20px;white-space:nowrap;
border-bottom:1px solid #eef1f6}}
th{{background:#f8fafc;color:#475569;font-weight:600}}
.rk{{color:#0f766e;font-weight:700}}
.nm{{font-weight:600}}
.up{{color:#dc2626}}.down{{color:#16a34a}}
.lad{{background:#fff;border-radius:12px;padding:10px 14px;margin-bottom:8px;
box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.lad b{{font-size:23px;color:#dc2626;margin-right:8px}}
.lad .c{{font-size:18px;color:#94a3b8}}
.chip{{display:inline-block;background:#fef2f2;border-radius:8px;padding:2px 10px;
margin:4px 6px 0 0;font-size:20px}}
.chip i{{font-style:normal;font-size:17px;color:#94a3b8;margin-left:6px}}
.bk{{background:#fff;border-radius:12px;padding:10px 14px;margin-bottom:8px;
box-shadow:0 1px 4px rgba(0,0,0,.06)}}
.bk .sm{{display:block;font-size:18px;color:#64748b}}
.empty{{color:#94a3b8;font-size:20px}}
.warn{{background:#fef2f2;color:#b91c1c;border-radius:10px;padding:10px 14px;
font-size:20px;margin-bottom:12px}}
.ft{{color:#94a3b8;font-size:19px;text-align:center;padding:10px 0 20px}}
</style></head><body>
<div class="hd">
<h1>盘中题材全景</h1>
<div class="sub">交易日 {snapshot['trade_date']} · 生成 {snapshot['generated_at'][11:19]}
 · 覆盖 {snapshot['concept_count']} 个概念</div>
</div>
{warn_html}
<div class="sec">情绪仪表盘</div>
<div class="grid">{metric_html}</div>
<div class="card"><table>
<tr><th>指标</th><th>数值</th></tr>
<tr><td>上涨 / 下跌 / 平盘</td><td>{esc(b.get('advancing', '-'))} / {esc(b.get('declining', '-'))} / {esc(b.get('unchanged', '-'))}</td></tr>
<tr><td>连板分布 1/2/3/高位</td><td>{esc(s.get('one_limit_up_count', '-'))} / {esc(s.get('two_limit_up_count', '-'))} / {esc(s.get('three_limit_up_count', '-'))} / {esc(s.get('high_limit_up_count', '-'))}</td></tr>
<tr><td>高位晋级率</td><td>{fmt_pct(s.get('high_limit_up_promotion_percent'))}</td></tr>
<tr><td>停牌 / 昨日涨停破板变化</td><td>{esc(s.get('stop_trading_count', '-'))} / {fmt_pct(s.get('previous_limit_up_break_change_percent'))}</td></tr>
</table></div>
<div class="sec">题材强度榜（按涨幅 · 共 {snapshot['concept_count']} 个概念）</div>
<div class="card"><table>
<tr><th>#</th><th>概念</th><th>涨幅</th><th>净流入</th><th>涨/跌</th><th>成交</th></tr>
{concept_rows}</table></div>
<div class="sec">连板梯队</div>
{ladder_html}
<div class="sec">炸板池</div>
{broken_html}
<div class="ft">数据来源 finchx 3.x · 同花顺概念 / 东方财富 / 爱股票 · 仅供研究，不构成投资建议</div>
</body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="FinchX 题材热度 -> 龙头 快照")
    parser.add_argument(
        "--mode",
        choices=("hotlist", "intraday"),
        default="hotlist",
        help="hotlist=收盘热榜版; intraday=盘中全景版",
    )
    parser.add_argument("--sectors", type=int, default=30, help="热榜版：概念板块条数")
    parser.add_argument("--stocks", type=int, default=50, help="热榜版：热股榜条数")
    parser.add_argument("--topics", type=int, default=10, help="热榜版：热门话题条数")
    parser.add_argument("--workers", type=int, default=8, help="盘中版：概念并发数")
    parser.add_argument("--top", type=int, default=30, help="盘中版：题材榜展示条数")
    args = parser.parse_args()

    if args.mode == "intraday":
        snapshot = build_intraday(args.workers, args.top)
        print_intraday(snapshot)
        html = render_intraday_html(snapshot)
        prefix = "fx_intraday"
    else:
        snapshot = build_snapshot(args.sectors, args.stocks, args.topics)
        print_summary(snapshot)
        html = render_html(snapshot)
        prefix = "fx_theme_snapshot"

    date_tag = snapshot["trade_date"].replace("-", "")
    json_path = OUT_DIR / f"{prefix}_{date_tag}.json"
    html_path = OUT_DIR / f"{prefix}_{date_tag}.html"
    json_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    html_path.write_text(html, encoding="utf-8")
    print(f"JSON -> {json_path}")
    print(f"HTML -> {html_path}")


if __name__ == "__main__":
    main()
