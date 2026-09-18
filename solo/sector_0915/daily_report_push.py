"""每日汇总报告的飞书推送：把板块层（Step 3/4）与个股层（Step 5/6）的产物
压缩成一张飞书交互卡片（Card 2.0）并发送。

设计约束：
  - 只读已有产物（data/*.csv 与 output/*.json），不重算、不修改任何 Step 1–6 逻辑。
  - 卡片只描述结构 / 状态 / 资格，不输出 BUY / NO TRADE / 仓位 / 买卖点。
  - 卡片 JSON 先落盘到 output/daily_report_<date>_card.json，再交给 lark-cli 发送。

用法：
    python daily_report_push.py --dry-run           # 只生成卡片 JSON，不发送
    python daily_report_push.py                     # 生成并发送
    python daily_report_push.py --user-id ou_xxx    # 指定收件人（默认发给自己）
    python daily_report_push.py --as user           # 指定发送身份（默认 user）
    python daily_report_push.py --date 20260917     # 指定板块层日期
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

from daily_report_build import (
    BREAKOUT_STATE_CN,
    DIVERGENCE_DROP,
    DIVERGENCE_DROP_1,
    DIVERGENCE_TOP_N,
    GROUP_STATE_CN,
    HVT_STATE_CN,
    MEMBERSHIP_STATUS_CN,
    POOL_CN,
    BoardData,
    _cn,
    _f,
    _pct,
    _pct0,
    _read_json,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# 本机 lark 插件自带 CLI；PATH 里有时优先用 PATH
LARK_CLI_FALLBACK = (
    r"C:\Users\kongx\.trae-cn\plugins\trae-remote-official\lark\1.0.5\bin\lark-cli.exe"
)
DEFAULT_TARGET_OPEN_ID = "ou_782aa2f80ae2ebfcc37b0b9a71209904"  # 本人

HEADER_ICON = "wiki-bitable_colorful"
THEME_LIST_N = 11          # 折叠区展示的优先主题数量
QUALIFIED_TABLE_N = 8      # 结构合格明细表行数上限
GROUP_TABLE_N = 8          # 轮动组明细表行数上限


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
    # 移动端：列宽交给 auto，不写死像素；行数尽量一页放完，免翻页
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


# ════════════════════════════════════════════════════════════════════════
# 数据收集
# ════════════════════════════════════════════════════════════════════════
def collect(date: str | None) -> dict:
    bd = BoardData(date)
    d = bd.date

    tr = bd.market_trend(2)
    if len(tr) >= 2:
        health, health_prev = float(tr["health_mean"].iloc[-1]), float(tr["health_mean"].iloc[-2])
        up, up_prev = float(tr["up_ratio"].iloc[-1]), float(tr["up_ratio"].iloc[-2])
    else:
        health = health_prev = up = up_prev = float("nan")

    x = bd.x
    state_cnt = {}
    if "current_state" in x.columns:
        state_cnt = x["current_state"].astype(str).value_counts().to_dict()

    rot = bd.seos_json.get("rotation_counts", {}) or {}
    in_sectors = []
    out_sectors = []
    if not x.empty and "rotation_signal" in x.columns:
        for _, v in x.iterrows():
            if v.get("rotation_signal") == "ROTATION_IN":
                in_sectors.append(v)
            elif v.get("rotation_signal") == "ROTATION_OUT":
                out_sectors.append(v)

    # 量价背离：成交额占比靠前但健康度大跌
    div = []
    if not x.empty and "theme_amount_share" in x.columns:
        sub = x.nlargest(DIVERGENCE_TOP_N, "theme_amount_share")
        sub = sub[(sub["theme_health_delta_5"] <= DIVERGENCE_DROP)
                  | (sub["theme_health_delta_1"] <= DIVERGENCE_DROP_1)]
        div = list(sub.iterrows())

    cand = _read_json("sector_stock_candidate_top.json")
    themes = sorted(cand.get("top_themes", []) or [],
                    key=lambda t: -(t.get("theme_opportunity_score") or 0))

    sd = _read_json("stock_structure_today.json")
    pools = {
        "structure": _read_json("stock_structure_pool.json"),
        "hvt": _read_json("stock_hvt_pool.json"),
        "rebreakout": _read_json("stock_rebreakout_pool.json"),
        "retest": _read_json("stock_retest_pool.json"),
    }

    return {
        "date": d,
        "health": health, "health_prev": health_prev,
        "up": up, "up_prev": up_prev,
        "state_counts": state_cnt,
        "rotation_counts": rot,
        "in_sectors": in_sectors, "out_sectors": out_sectors,
        "divergence": div,
        "groups": bd.seos_json.get("groups", []) or [],
        "themes": themes,
        "stock_date": sd.get("trade_date", "NA"),
        "summary": sd.get("summary", {}),
        "qualified": sd.get("qualified", []) or [],
        "pools": pools,
    }


# ════════════════════════════════════════════════════════════════════════
# 卡片构造
# ════════════════════════════════════════════════════════════════════════
def build_card(d: dict) -> dict:
    date = d["date"] or "NA"
    n_in = d["rotation_counts"].get("ROTATION_IN", 0)
    n_out = d["rotation_counts"].get("ROTATION_OUT", 0)
    sm = d["summary"]
    n_qual = sm.get("qualified_count", 0)
    n_cand = sm.get("candidate_count", 0)
    delta = d["health"] - d["health_prev"]

    elements = []

    # 1) 指标卡（标签压到 4–5 字，移动端一行放得下；前值细节落在下方要点里）
    elements.append(_kpi_row([
        (_f(d["health"], 1), "板块健康度"),
        (_pct0(d["up"], 1), "上涨占比"),
        (f"{n_in}/{n_out}", "轮入/轮出"),
        (str(n_qual), "结构合格"),
    ], "0px 0px 12px 0px"))

    # 2) 板块层要点（一行一件事，手机上不挤成整段）
    lines = [f"**板块层 {date}**"]
    for v in d["in_sectors"]:
        lines.append(f"- **轮入**：{v['sector_id']} {v['sector_name']}（{v['rotation_group']}）")
    for v in d["out_sectors"]:
        lines.append(f"- **轮出**：{v['sector_id']} {v['sector_name']}（{v['rotation_group']}）")
    if not d["in_sectors"] and not d["out_sectors"]:
        lines.append("- **轮动**：无轮入 / 轮出，50 个板块全部中性")
    elif n_in + n_out <= 2:
        lines.append(f"- 其余 {50 - n_in - n_out} 个中性，**属同步波动日，无强轮动信号**")
    if d["health"] == d["health"]:
        trend = "降温" if delta < 0 else "升温"
        lines.append(f"- **温度**：健康度 {_f(d['health'], 1)}（{delta:+.1f}）· "
                     f"上涨占比 {_pct0(d['up'], 1)} · 整体{trend}")
    if d["divergence"]:
        lines.append(f"- **背离**：成交额 TOP{DIVERGENCE_TOP_N} 中 {len(d['divergence'])} 个健康度大幅回落，"
                     f"**价格走弱、资金未撤离**")
    lines.append(f"- **结构**：合格 {n_qual} 只 · 候选域 {n_cand} 只")
    elements.append(_highlight("\n".join(lines), "0px 0px 12px 0px"))

    # 3) 轮动组状态（4 列以内，列宽 auto）
    groups = [g for g in d["groups"]][:GROUP_TABLE_N]
    if groups:
        elements.append(_table(
            "0px 0px 12px 0px",
            [
                {"name": "g", "display_name": "轮动组", "data_type": "text"},
                {"name": "h", "display_name": "健康度", "data_type": "number",
                 "format": {"precision": 1}},
                {"name": "sd", "display_name": "SEOSΔ5", "data_type": "number",
                 "format": {"precision": 1}},
                {"name": "st", "display_name": "状态", "data_type": "text"},
            ],
            [{"g": f"{g.get('rotation_group')} {g.get('group_name')}",
              "h": _round(g.get("group_health")),
              "sd": _round(g.get("group_seos_delta_5")),
              "st": GROUP_STATE_CN.get(str(g.get("group_rotation_state")), "-")} for g in groups]))

    # 4) 结构合格个股（代码并入名称列、两种状态并成一列，压到 4 列）
    qual = d["qualified"][:QUALIFIED_TABLE_N]
    if qual:
        elements.append(_table(
            "0px 0px 12px 0px",
            [
                {"name": "c", "display_name": "名称", "data_type": "text"},
                {"name": "t", "display_name": "主主题", "data_type": "text"},
                {"name": "q", "display_name": "质量", "data_type": "number",
                 "format": {"precision": 1}},
                {"name": "s", "display_name": "天量换手 · 突破", "data_type": "text"},
            ],
            [{"c": f"{r.get('ts_code')} {r.get('name')}",
              "t": f"{r.get('primary_theme_id')} {theme_name(d, r.get('primary_theme_id'))}",
              "q": _round(r.get("structure_quality")),
              "s": f"{_cn(HVT_STATE_CN, r.get('hvt_state'))} · "
                   f"{_cn(BREAKOUT_STATE_CN, r.get('breakout_state'))}"} for r in qual]))

    # 5) 折叠明细
    fold = []
    if d["themes"]:
        tl = "\n".join(f"- {t.get('theme_id')} {t.get('theme_name')} · "
                       f"{_f(t.get('theme_opportunity_score'), 1)}"
                       for t in d["themes"][:THEME_LIST_N])
        fold.append(_md(f"**优先主题 {len(d['themes'])} 个**（名称 · 机会分）\n{tl}"))
    for v in d["in_sectors"]:
        fold.append(_md(
            f"**轮入 · {v['sector_id']} {v['sector_name']}**（{v['rotation_group']}）\n"
            f"- 参照窗广度 {_f(v.get('breadth'), 4)}（偏弱）\n"
            f"- 广度Δ5 {_f(v.get('breadth_delta_5'), 4)} · "
            f"核心广度Δ5 {_f(v.get('core_breadth_delta_5'), 4)} · "
            f"相对强度拐点Δ5 {_f(v.get('rs_turn_5'), 4)}（同步改善）"))
    for v in d["out_sectors"]:
        fold.append(_md(
            f"**轮出 · {v['sector_id']} {v['sector_name']}**（{v['rotation_group']}）\n"
            f"- 参照窗广度 {_f(v.get('breadth'), 4)}（偏强）\n"
            f"- 广度Δ5 {_f(v.get('breadth_delta_5'), 4)} · "
            f"核心广度Δ5 {_f(v.get('core_breadth_delta_5'), 4)} · "
            f"相对强度拐点Δ5 {_f(v.get('rs_turn_5'), 4)}（转弱）"))
    if d["divergence"]:
        rows = "\n".join(
            f"- {v['sector_name']}：占比 {_pct0(v.get('theme_amount_share'), 2)}"
            f"（Δ5 {_pct(v.get('amount_share_delta_5'))}）· 健康度 {_f(v.get('theme_health'), 1)}"
            f"（Δ1 {_f(v.get('theme_health_delta_1'), 1)}）" for _, v in d["divergence"])
        fold.append(_md(f"**量价背离明细**\n{rows}"))
    p = d["pools"]
    fold.append(_md("**结构池计数**（均为结构池，非买入池）\n"
                    f"- {_cn(POOL_CN, 'STRUCTURE_POOL')} {p['structure'].get('count', 0)} · "
                    f"{_cn(POOL_CN, 'HVT_POOL')} {p['hvt'].get('count', 0)}\n"
                    f"- {_cn(POOL_CN, 'REBREAKOUT_POOL')} {p['rebreakout'].get('count', 0)} · "
                    f"{_cn(POOL_CN, 'RETEST_POOL')} {p['retest'].get('count', 0)}"))
    fold.append(_md(f"<font color='grey'>数据：板块层 {d['date']} · 个股层 {d['stock_date']} · "
                    f"成员快照 {_cn(MEMBERSHIP_STATUS_CN, sm.get('membership_version_status'))}</font>\n"
                    f"<font color='grey'>声明：仅描述板块状态与个股结构资格，不构成 "
                    f"BUY / NO TRADE / 仓位 / 止损 / 目标价；结构池均为结构池而非买入池；"
                    f"执行层（第 7 步）未实现。</font>",
                    size="notation"))
    elements.append(_panel("主题候选池 · 轮动依据 · 声明", fold))

    # 状态标签
    tags = []
    if d["health"] == d["health"]:
        tags.append({"tag": "text_tag",
                     "text": {"tag": "plain_text",
                              "content": "整体降温" if delta < 0 else "整体升温"},
                     "color": "blue"})
    tags.append({"tag": "text_tag",
                 "text": {"tag": "plain_text", "content": "非交易建议"},
                 "color": "neutral"})

    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "width_mode": "default",
            "summary": {"content": f"A股主题量化日报 {date}：板块健康度 {_f(d['health'], 1)}，"
                                   f"结构合格 {n_qual} 只"},
        },
        "header": {
            "title": {"tag": "plain_text", "content": "A股主题量化日报"},
            "subtitle": {"tag": "plain_text",
                         "content": f"{date} · 板块层 + 个股层"},
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


def _round(v, nd: int = 1):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return round(f, nd)


def theme_name(d: dict, theme_id) -> str:
    tid = str(theme_id)
    for t in d["themes"]:
        if str(t.get("theme_id")) == tid:
            return str(t.get("theme_name"))
    return ""


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
    ap = argparse.ArgumentParser(description="把每日汇总报告推成一张飞书交互卡片（只读产物）")
    ap.add_argument("--date", default=None, metavar="YYYYMMDD", help="板块层交易日（默认取最新）")
    ap.add_argument("--user-id", default=DEFAULT_TARGET_OPEN_ID, metavar="ou_xxx",
                    help="收件人 open_id（默认本人）")
    ap.add_argument("--as", dest="identity", default="user", choices=["bot", "user"],
                    help="发送身份（默认 user，托管环境 strict_mode 下只支持 user）")
    ap.add_argument("--cli", default=None, help="lark-cli 可执行文件路径")
    ap.add_argument("--dry-run", action="store_true", help="只生成卡片 JSON，不发送")
    args = ap.parse_args(argv)

    d = collect(args.date)
    card = build_card(d)

    out_path = os.path.join(OUTPUT_DIR, f"daily_report_{d['date']}_card.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(card, fh, ensure_ascii=False, indent=2)
    n_el = len(card["body"]["elements"])
    print(f"写出 {out_path}（顶层视觉块 {n_el} 个）")

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
