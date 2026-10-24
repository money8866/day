# -*- coding: utf-8 -*-
"""
概念热榜（同花顺 · 经 FinchX）取数 + 盘后复盘小节生成器。

用法:
  python _finchx_hotlist.py [--date YYYYMMDD] [--limit 20] [--out-dir DIR] [--quiet]

输出（默认落盘 <脚本目录>/report_daily/hotlist/）:
  concept_hotlist_{date}.json   当日快照（rows = finchx 原始返回，另附 fetched_at）
  concept_hotlist_{date}.md     复盘小节（TOP20 表格 + 自动观察，可直接嵌入报告/邮件）
stdout 打印 md 全文（--quiet 时只打印一行 JSON 状态，便于程序解析）。

设计要点:
  1. 只负责取数与成文，不碰邮件/推送；上游故障降级不炸场：SourceError → 生成「暂不可用」
     小节并退出码 0，让复盘主流程继续。
  2. 与历史快照自动对比算环比：新上榜 / 掉出榜 / 热度增幅前三 / 名次跃升前三。
     同日重复运行覆盖当日快照（幂等）。
  3. 百分比沿用 finchx 约定：1.23 即 1.23%（输出时补 % 号）。
  4. 「科技链」等归类为概念名关键词匹配，方法在输出中注明；所有观察仅为数据描述，
     不构成投资建议。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(SCRIPT_DIR, "report_daily", "hotlist")
CN_TZ = timezone(timedelta(hours=8))

# 概念名关键词 → 科技链归类（仅用于统计描述，非投资建议）
TECH_KEYWORDS = ("机器人", "AI", "人工智能", "智能", "算力", "芯片", "半导体",
                 "光模块", "CPO", "MLOps", "数据", "软件", "云计算", "PCB", "存储")


def now_cn():
    return datetime.now(CN_TZ)


def fetch_hotlist(limit):
    """返回 finchx 原始 rows；finchx 缺失或上游故障分别抛 ImportError/SourceError。"""
    from finchx import FinchX  # noqa
    fx = FinchX(timeout=20.0)
    return fx.hotlist.sectors(sector_type="concept", limit=limit)


def fmt_pct(v):
    return "—" if v is None else f"{v:+.2f}%"


def fmt_num(v):
    return "—" if v is None else f"{v:,.0f}"


def limit_up_count(row):
    """从 tag（如 '27家涨停'）提取涨停家数。"""
    tag = row.get("tag") or ""
    m = re.search(r"(\d+)\s*家涨停", tag)
    return int(m.group(1)) if m else None


def etf_cell(row):
    name = row.get("related_etf_name")
    if not name:
        return "—"
    chg = row.get("related_etf_change_percent")
    return f"{name} {fmt_pct(chg)}" if chg is not None else name


def load_prev_snapshot(out_dir, date):
    """取 date 之前最近的一份快照；没有返回 None。"""
    best = None
    for fn in os.listdir(out_dir):
        m = re.fullmatch(r"concept_hotlist_(\d{8})\.json", fn)
        if not m or m.group(1) >= date:
            continue
        p = os.path.join(out_dir, fn)
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            if best is None or data.get("date", "") > best.get("date", ""):
                best = data
        except Exception:
            continue
    return best


def build_analysis(rows, prev):
    """基于当日 rows 与昨日快照生成观察结论（纯数据描述）。"""
    heats = [r.get("heat") or 0 for r in rows]
    total_heat = sum(heats)
    top3 = rows[:3]

    tech = [r for r in rows if any(k in (r.get("name") or "") for k in TECH_KEYWORDS)]
    tech_lu = sum(limit_up_count(r) or 0 for r in tech)

    rank_moves = [(r.get("name"), r.get("rank_change")) for r in rows
                  if (r.get("rank_change") or 0) != 0]

    out = {
        "total_heat": total_heat,
        "top3_names": [r.get("name") for r in top3],
        "top3_heat_share": (sum(r.get("heat") or 0 for r in top3) / total_heat * 100)
                           if total_heat else None,
        "tech_count": len(tech),
        "tech_names": [r.get("name") for r in tech[:5]],
        "tech_limit_up": tech_lu,
        "rank_moves": rank_moves,
        "prev_date": None, "new_entries": [], "dropped": [],
        "heat_up_top": [], "rank_jump_top": [],
    }

    if prev:
        prev_rows = prev.get("rows") or []
        prev_by_name = {r.get("name"): r for r in prev_rows}
        cur_by_name = {r.get("name"): r for r in rows}
        out["prev_date"] = prev.get("date")
        out["new_entries"] = [r.get("name") for r in rows if r.get("name") not in prev_by_name]
        out["dropped"] = [r.get("name") for r in prev_rows if r.get("name") not in cur_by_name]
        ups = []
        for r in rows:
            p = prev_by_name.get(r.get("name"))
            if p and (p.get("heat") or 0) > 0 and (r.get("heat") or 0) > 0:
                ups.append((r.get("name"), (r["heat"] - p["heat"]) / p["heat"] * 100))
        out["heat_up_top"] = sorted(ups, key=lambda x: -x[1])[:3]
        jumps = []
        for r in rows:
            p = prev_by_name.get(r.get("name"))
            if p and p.get("rank") and r.get("rank"):
                jumps.append((r.get("name"), p["rank"] - r["rank"]))  # 正=排名上升
        out["rank_jump_top"] = sorted(jumps, key=lambda x: -x[1])[:3]
    return out


def render_md(date, rows, ana, fetched_at, degraded_note=None):
    L = []
    L.append(f"## 🔥 概念热榜 TOP{len(rows)}（同花顺口径 · 取数 {fetched_at}）")
    L.append("")
    if degraded_note:
        L.append(f"> ⚠️ {degraded_note}")
        L.append("")
        return "\n".join(L)

    L.append("| 排名 | 概念 | 涨跌幅 | 热度 | 涨停家数 | 上榜标签 | 关联ETF |")
    L.append("|---:|---|---:|---:|---:|---|---|")
    for r in rows:
        L.append("| {rank} | {name} | {chg} | {heat} | {lu} | {tag} | {etf} |".format(
            rank=r.get("rank"), name=r.get("name"), chg=fmt_pct(r.get("change_percent")),
            heat=fmt_num(r.get("heat")), lu=limit_up_count(r) if limit_up_count(r) is not None else "—",
            tag=r.get("hot_tag") or "—", etf=etf_cell(r)))
    L.append("")
    L.append("**自动观察**（基于上表与历史快照的数据描述，非投资建议）：")
    if ana["top3_heat_share"] is not None:
        L.append(f"- 热度集中度：TOP3（{'、'.join(ana['top3_names'])}）合计占 TOP{len(rows)} 总热度 "
                 f"{ana['top3_heat_share']:.0f}%")
    L.append(f"- 科技链归类（关键词匹配：机器人/AI/芯片/算力等）：上榜 {ana['tech_count']} 个概念"
             f"（{'、'.join(ana['tech_names'])}），涉及涨停 {ana['tech_limit_up']} 家")
    if ana["rank_moves"]:
        moves = " · ".join(f"{n} {'↑' if d > 0 else '↓'}{abs(d)}" for n, d in ana["rank_moves"][:6])
        L.append(f"- 榜内名次变动：{moves}")
    if ana["prev_date"]:
        L.append(f"- 环比 {ana['prev_date']}：新上榜 {('、'.join(ana['new_entries']) or '无')}；"
                 f"掉出榜 {('、'.join(ana['dropped']) or '无')}")
        if ana["heat_up_top"]:
            L.append("- 热度增幅前三：" + " · ".join(f"{n} {d:+.0f}%" for n, d in ana["heat_up_top"]))
        if ana["rank_jump_top"] and ana["rank_jump_top"][0][1] > 0:
            L.append("- 名次跃升前三：" + " · ".join(f"{n} ↑{d}" for n, d in ana["rank_jump_top"]))
    L.append("")
    L.append("> 热度值口径为同花顺热榜（单位以来源为准）；「科技链」为名称关键词归类，非官方行业分类。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="快照日期 YYYYMMDD，默认今天（北京时间）")
    ap.add_argument("--limit", type=int, default=20, help="取前 N 名，默认 20")
    ap.add_argument("--out-dir", default=DEFAULT_OUT, help="快照输出目录")
    ap.add_argument("--quiet", action="store_true", help="只输出一行 JSON 状态")
    a = ap.parse_args()

    date = a.date or now_cn().strftime("%Y%m%d")
    os.makedirs(a.out_dir, exist_ok=True)
    md_path = os.path.join(a.out_dir, f"concept_hotlist_{date}.md")
    json_path = os.path.join(a.out_dir, f"concept_hotlist_{date}.json")
    fetched_at = now_cn().strftime("%Y-%m-%d %H:%M")

    try:
        rows = fetch_hotlist(a.limit)
    except ImportError:
        print("finchx 未安装：pip install finchx==3.1.1", file=sys.stderr)
        print(json.dumps({"ok": False, "stage": "import", "date": date}, ensure_ascii=False))
        return 2
    except Exception as e:
        note = f"概念热榜数据源暂不可用（{type(e).__name__}: {str(e)[:80]}），本节略。"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(render_md(date, [], {}, fetched_at, degraded_note=note))
        if not a.quiet:
            print(render_md(date, [], {}, fetched_at, degraded_note=note))
        else:
            print(json.dumps({"ok": False, "stage": "source", "date": date,
                              "error": f"{type(e).__name__}: {str(e)[:120]}",
                              "md": md_path}, ensure_ascii=False))
        return 0

    prev = load_prev_snapshot(a.out_dir, date)
    ana = build_analysis(rows, prev)
    md = render_md(date, rows, ana, fetched_at)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"date": date, "fetched_at": fetched_at, "source": "finchx/hotlist.sectors(concept)",
                   "rows": rows}, f, ensure_ascii=False, indent=1)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md + "\n")

    if a.quiet:
        print(json.dumps({"ok": True, "date": date, "rows": len(rows),
                          "md": md_path, "json": json_path,
                          "prev_date": ana["prev_date"],
                          "new_entries": ana["new_entries"]}, ensure_ascii=False))
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
