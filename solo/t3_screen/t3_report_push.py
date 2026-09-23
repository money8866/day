# -*- coding: utf-8 -*-
"""T+3 二筛报告的飞书推送：把 output/t3_screen_<date>.json 压缩成一张飞书
交互卡片（Card 2.0）并发给本人。

设计约束：
  - 只读 output/t3_screen_<date>.json 与 config/thresholds.json，不重算、不改任何结果。
  - 卡片 JSON 先落盘到 output/t3_screen_<date>_card.json，再交给 lark-cli 发送。
  - 只展示，不产生新的买卖判断；档位文字与 JSON 的 action_level 完全一致。

用法：
    python t3_report_push.py --dry-run           # 只生成卡片 JSON，不发送
    python t3_report_push.py                     # 取最新一份报告，生成并发送
    python t3_report_push.py --date 20260922     # 指定交易日
    python t3_report_push.py --user-id ou_xxx    # 指定收件人（默认发给自己）
    python t3_report_push.py --as user           # 指定发送身份（默认 user）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
CFG_PATH = os.path.join(CONFIG_DIR, "thresholds.json")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# 本机 lark 插件自带 CLI；PATH 里有时优先用 PATH
LARK_CLI_FALLBACK = (
    r"C:\Users\kongx\.trae-cn\plugins\trae-remote-official\lark\1.0.5\bin\lark-cli.exe"
)
DEFAULT_TARGET_OPEN_ID = "ou_782aa2f80ae2ebfcc37b0b9a71209904"  # 本人

HEADER_ICON = "wiki-bitable_colorful"
BUY_TABLE_N = 6            # BUY 明细行数上限
COND_TABLE_N = 8           # CONDITIONAL BUY 明细行数上限
SECTOR_TOP_N = 4           # 强势板块展示数
NEWS_ITEM_N = 2            # 每只票在折叠区展示的公告条数
AVOID_TABLE_N = 8          # AVOID 原因展示数

LEVEL_CN = {
    "BUY": "今日可执行",
    "CONDITIONAL_BUY": "有条件买入",
    "WAIT": "等待",
    "AVOID": "规避",
}
STATE_CN = {
    "STRONG_TREND": "强趋势",
    "STRUCTURAL_TREND": "结构性趋势",
    "STRUCTURAL_ROTATION": "结构性轮动",
    "RANGE": "震荡",
    "WEAK": "偏弱",
    "RISK_OFF": "风险规避",
}


# ════════════════════════════════════════════════════════════════════════
# 卡片小组件（移动端优先：不定死列宽、标签短、每行一件事）
# ════════════════════════════════════════════════════════════════════════
def _md(content: str, size: str = "normal", align: str | None = None) -> dict:
    el = {"tag": "markdown", "content": content, "text_size": size}
    if align:
        el["text_align"] = align
    return el


def _kpi_column(value: str, label: str) -> dict:
    return {
        "tag": "column",
        "width": "weighted",
        "weight": 1,
        "background_style": "grey-50",
        "padding": "12px 6px",
        "vertical_spacing": "2px",
        "elements": [
            _md(f"## <font color='blue'>{value}</font>", align="center"),
            _md(f"<font color='grey'>{label}</font>", size="notation", align="center"),
        ],
    }


def _kpi_row(items: list[tuple[str, str]], margin: str) -> dict:
    return {
        "tag": "column_set",
        "flex_mode": "none",
        "horizontal_spacing": "8px",
        "margin": margin,
        "columns": [_kpi_column(v, lb) for v, lb in items],
    }


def _highlight(md_content: str, margin: str) -> dict:
    return {
        "tag": "column_set",
        "flex_mode": "none",
        "margin": margin,
        "columns": [
            {
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "background_style": "blue-50",
                "padding": "12px",
                "vertical_spacing": "4px",
                "elements": [_md(md_content)],
            }
        ],
    }


def _table(margin: str, columns: list, rows: list) -> dict:
    return {
        "tag": "table",
        "margin": margin,
        "page_size": max(1, min(10, len(rows))),
        "row_height": "low",
        "header_style": {"text_size": "notation", "background_style": "grey",
                         "bold": True, "lines": 1},
        "columns": columns,
        "rows": rows,
    }


def _panel(title: str, blocks: list) -> dict:
    return {
        "tag": "collapsible_panel",
        "expanded": False,
        "header": {"title": {"tag": "plain_text", "content": title}},
        "border": {"color": "grey", "corner_radius": "8px"},
        "padding": "8px 12px 8px 12px",
        "vertical_spacing": "4px",
        "elements": blocks,
    }


def _n(v, nd: int = 2) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    if f != f:
        return "-"
    return f"{f:.{nd}f}"


def _sgn_pct(v, nd: int = 1) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    if f != f:
        return "-"
    return f"{f:+.{nd}f}%"


# ════════════════════════════════════════════════════════════════════════
# 数据读取
# ════════════════════════════════════════════════════════════════════════
def latest_report_date() -> str:
    files = glob.glob(os.path.join(OUTPUT_DIR, "t3_screen_*.json"))
    dates = [m.group(1) for m in
             (re.fullmatch(r"t3_screen_(\d{8})\.json", os.path.basename(f)) for f in files) if m]
    if not dates:
        raise SystemExit("找不到 output/t3_screen_YYYYMMDD.json，请先运行 t3_screen_build.py")
    return sorted(dates)[-1]


def load_report(date: str | None) -> tuple[dict, str]:
    d = re.sub(r"\D", "", str(date or "")) or latest_report_date()
    path = os.path.join(OUTPUT_DIR, f"t3_screen_{d}.json")
    if not os.path.exists(path):
        raise SystemExit(f"找不到 {path}，请先运行 t3_screen_build.py --date {d}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh), d


def load_levels() -> dict:
    with open(CFG_PATH, encoding="utf-8") as fh:
        return json.load(fh)["output"]


def by_level(recs: list, level: str) -> list:
    return [r for r in recs if r.get("action_level") == level]


def _buy_zone(plan: dict) -> str:
    if not plan:
        return "-"
    return f"{_n(plan.get('entry_low'))}~{_n(plan.get('entry_high'))}"


def _stop_target(plan: dict) -> str:
    if not plan:
        return "-"
    return f"{_n(plan.get('stop'))}/{_n(plan.get('target'))}"


def _news_tag(r: dict) -> str:
    nw = r.get("news") or {}
    tags = nw.get("flags") or []
    if tags:
        return "、".join(str(t) for t in tags)
    st = str(nw.get("status") or "")
    if st == "AI_DEEPSEEK":
        return "例行"
    if st == "NO_ANNOUNCEMENT":
        return "无公告"
    return "缺失"


# ════════════════════════════════════════════════════════════════════════
# 卡片构造
# ════════════════════════════════════════════════════════════════════════
def build_card(a: dict, date: str) -> dict:
    meta = a.get("meta") or {}
    m = a.get("market") or {}
    ctx = m.get("ctx") or {}
    recs = a.get("records") or []
    lim = load_levels()

    n_buy = len(by_level(recs, "BUY"))
    n_cond = len(by_level(recs, "CONDITIONAL_BUY"))
    n_wait = len(by_level(recs, "WAIT"))
    n_avoid = len(by_level(recs, "AVOID"))
    state_cn = STATE_CN.get(str(m.get("state")), str(m.get("state")))

    elements = []

    # 1) 指标卡
    elements.append(_kpi_row([
        (state_cn, "市场状态"),
        (f"{float(ctx.get('breadth_pct') or 0):.1f}%", "上涨占比"),
        (f"{n_buy}/{n_cond}", "可执行/有条件"),
        (str(len(recs)), "候选票数"),
    ], "0px 0px 12px 0px"))

    # 2) 摘要要点（一行一件事）
    lines = [f"**T+3 二筛 {meta.get('date') or date}**（{meta.get('generated_at')}）"]
    lines.append(f"- **市场**：{state_cn}（{_n(m.get('score'), 1)}/10）· 广度 "
                 f"{m.get('breadth_up')}/{m.get('breadth_dn')}（总 {m.get('breadth_total')}）")
    idx = [i for i in (m.get("indices") or []) if not i.get("missing")]
    if idx:
        lines.append("- **指数 5 日**：" + " · ".join(
            f"{i.get('name')} {_sgn_pct(i.get('ret5'))}" for i in idx))
    lines.append(f"- **分级**：可执行 {n_buy}（上限 {lim['max_buy']}）· 有条件 {n_cond}"
                 f"（上限 {lim['max_conditional']}）· 等待 {n_wait} · 规避 {n_avoid}")
    secs = (a.get("sectors") or [])[:SECTOR_TOP_N]
    if secs:
        lines.append("- **强势板块**：" + "、".join(
            f"{s.get('industry')}（{_n(s.get('avg_pct'), 1)}%）" for s in secs))
    news_on = bool(meta.get("news_enabled"))
    lines.append(f"- **资讯层**：{'已接入 ' + str(meta.get('news_provider') or '') if news_on else '未接入'}")
    elements.append(_highlight("\n".join(lines), "0px 0px 12px 0px"))

    # 3) 可执行（BUY）
    buys = by_level(recs, "BUY")[:BUY_TABLE_N]
    if buys:
        elements.append(_table(
            "0px 0px 12px 0px",
            [
                {"name": "s", "display_name": "可执行", "data_type": "text"},
                {"name": "q", "display_name": "分数", "data_type": "number",
                 "format": {"precision": 1}},
                {"name": "z", "display_name": "买区", "data_type": "text"},
                {"name": "sl", "display_name": "止损/目标", "data_type": "text"},
                {"name": "rr", "display_name": "RR", "data_type": "text"},
            ],
            [{"s": f"{r.get('code')} {r.get('name')}",
              "q": round(float(r.get("scores", {}).get("total") or 0), 1),
              "z": _buy_zone(r.get("plan") or {}),
              "sl": _stop_target(r.get("plan") or {}),
              "rr": _n((r.get("plan") or {}).get("rr"))} for r in buys]))
    else:
        elements.append(_md("<font color='grey'>今日无 BUY 档标的（不为凑数量降低门槛）</font>",
                            size="notation", align="center"))

    # 4) 有条件买入（CONDITIONAL BUY）
    conds = by_level(recs, "CONDITIONAL_BUY")[:COND_TABLE_N]
    if conds:
        elements.append(_table(
            "0px 0px 12px 0px",
            [
                {"name": "s", "display_name": "有条件", "data_type": "text"},
                {"name": "q", "display_name": "分数", "data_type": "number",
                 "format": {"precision": 1}},
                {"name": "z", "display_name": "买区", "data_type": "text"},
                {"name": "rr", "display_name": "RR", "data_type": "text"},
                {"name": "tag", "display_name": "资讯", "data_type": "text"},
            ],
            [{"s": f"{r.get('code')} {r.get('name')}",
              "q": round(float(r.get("scores", {}).get("total") or 0), 1),
              "z": _buy_zone(r.get("plan") or {}),
              "rr": _n((r.get("plan") or {}).get("rr")),
              "tag": _news_tag(r)} for r in conds]))

    # 5) 折叠：资讯核验 · 规避原因 · 口径与声明
    fold = []

    rows = [r for r in recs if (r.get("news") or {}).get("flags")]
    if rows:
        blk = ["**资讯/公告风险标签**"]
        for r in rows[:COND_TABLE_N * 2]:
            nw = r.get("news") or {}
            blk.append(f"- {r.get('code')} {r.get('name')}（{LEVEL_CN.get(str(r.get('action_level')), r.get('action_level'))}）"
                       f"：{'、'.join(str(x) for x in (nw.get('flags') or []))}"
                       f"　{nw.get('summary') or ''}")
        fold.append(_md("\n".join(blk)))

    ex = buys + conds
    if ex:
        det = ["**公告明细（可执行档）**"]
        for r in ex:
            nw = r.get("news") or {}
            items = nw.get("items") or []
            det.append(f"- {r.get('name')}（资讯 {_n(nw.get('score'), 1)}/10）：{nw.get('summary') or '—'}")
            for it in items[:NEWS_ITEM_N]:
                det.append(f"  - {it.get('date') or '-'} {it.get('title') or ''}")
        fold.append(_md("\n".join(det)))

    av = [r for r in by_level(recs, "AVOID")][:AVOID_TABLE_N]
    if av:
        fold.append(_md("**规避原因**\n" + "\n".join(
            f"- {r.get('code')} {r.get('name')}（{_n((r.get('scores') or {}).get('total'), 1)} 分）："
            f"{r.get('decision_reason') or '—'}" for r in av)))

    gates = (a.get("news_pending") or [])
    fold.append(_md(f"**资讯层待补**：{len(gates)} 只"
                    + ("（无）" if not gates else "：" + "、".join(
                        f"{g.get('code')} {g.get('name')}" for g in gates[:10]))))
    for d in (a.get("disclaimer") or []):
        fold.append(_md(f"<font color='grey'>{d}</font>", size="notation"))
    elements.append(_panel("资讯核验 · 公告明细 · 规避原因 · 口径声明", fold))

    # 状态标签
    tags = [{"tag": "text_tag",
             "text": {"tag": "plain_text", "content": "非交易建议"},
             "color": "neutral"}]
    if n_buy:
        tags.insert(0, {"tag": "text_tag",
                        "text": {"tag": "plain_text", "content": f"可执行 {n_buy}"},
                        "color": "green"})
    else:
        tags.insert(0, {"tag": "text_tag",
                        "text": {"tag": "plain_text", "content": "今日无 BUY"},
                        "color": "yellow"})

    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "width_mode": "default",
            "summary": {"content": f"T+3 二筛 {meta.get('date') or date}：{state_cn}，"
                                   f"可执行 {n_buy} / 有条件 {n_cond} / 候选 {len(recs)}"},
        },
        "header": {
            "title": {"tag": "plain_text", "content": "T+3 综合实盘二筛"},
            "subtitle": {"tag": "plain_text",
                         "content": f"{meta.get('date') or date} · {meta.get('unique_stocks')} 只 / "
                                    f"{meta.get('strategy_count')} 策略"},
            "template": "blue",
            "icon": {"tag": "standard_icon", "token": HEADER_ICON},
            "text_tag_list": tags,
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 20px 12px",
            "elements": elements,
        },
    }


# ════════════════════════════════════════════════════════════════════════
# 发送
# ════════════════════════════════════════════════════════════════════════
def resolve_cli() -> str:
    found = shutil.which("lark-cli")
    if found:
        return found
    if os.path.exists(LARK_CLI_FALLBACK):
        return LARK_CLI_FALLBACK
    raise SystemExit("找不到 lark-cli，请把 lark 插件的 bin 目录加入 PATH 或用 --cli 指定")


def send(cli: str, identity: str, target: str, card: dict) -> int:
    payload = json.dumps(card, ensure_ascii=False)
    cmd = [cli, "im", "+messages-send", "--as", identity,
           "--user-id", target, "--msg-type", "interactive", "--content", payload]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if out:
        print(out)
    if proc.returncode != 0:
        if err:
            print(err, file=sys.stderr)
        return proc.returncode
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="把 T+3 二筛报告推成一张飞书交互卡片（只读产物）")
    ap.add_argument("--date", default=None, metavar="YYYYMMDD", help="交易日（默认取最新一份）")
    ap.add_argument("--user-id", default=DEFAULT_TARGET_OPEN_ID, metavar="ou_xxx",
                    help="收件人 open_id（默认本人）")
    ap.add_argument("--as", dest="identity", default="user", choices=["bot", "user"],
                    help="发送身份（默认 user，托管环境 strict_mode 下只支持 user）")
    ap.add_argument("--cli", default=None, help="lark-cli 可执行文件路径")
    ap.add_argument("--dry-run", action="store_true", help="只生成卡片 JSON，不发送")
    args = ap.parse_args(argv)

    a, date = load_report(args.date)
    card = build_card(a, date)

    out_path = os.path.join(OUTPUT_DIR, f"t3_screen_{date}_card.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(card, fh, ensure_ascii=False, indent=2)
    print(f"写出 {out_path}（顶层视觉块 {len(card['body']['elements'])} 个）")

    if args.dry_run:
        print("--dry-run：未发送")
        return 0

    cli = args.cli or resolve_cli()
    rc = send(cli, args.identity, args.user_id, card)
    if rc != 0 and args.identity == "user":
        print("以 user 身份发送失败，改用 bot 身份重试", file=sys.stderr)
        rc = send(cli, "bot", args.user_id, card)
    print("发送成功" if rc == 0 else f"发送失败（exit {rc}）")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
