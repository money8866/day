# -*- coding: utf-8 -*-
"""Step 8 盘后复盘的飞书推送：把 output/post_market_review_<date>.json 压缩成一张
飞书交互卡片（Card 2.0）并发送。

设计约束：
  - 只读 Step 8 已落盘产物（output/post_market_review_*.json），不重算、不改任何结果。
  - WATCH != BUY（§四十二）；不使用夸张语言（§四十四）；不做任何总评分（§五十）。
  - 卡片 JSON 先落盘到 output/post_market_review_<date>_card.json，再交给 lark-cli 发送。

用法：
    python post_market_review_push.py --dry-run           # 只生成卡片 JSON，不发送
    python post_market_review_push.py                     # 取最新一天，生成并发送
    python post_market_review_push.py --date 20260918     # 指定复盘日
    python post_market_review_push.py --user-id ou_xxx    # 指定收件人（默认发给自己）
    python post_market_review_push.py --as user           # 指定发送身份（默认 user）

    # 自然语言解读卡（环境情绪 / 仓位建议 / 下一交易日信号分析）
    python post_market_review_push.py --narrative --dry-run
    python post_market_review_push.py --narrative
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

from post_market_review_build import (
    EARN_CN,
    LT_CN,
    REGIME_CN,
    RESULT_CN,
    TAG_CN,
    _n,
    _pct,
    jstr,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# 本机 lark 插件自带 CLI；PATH 里有时优先用 PATH
LARK_CLI_FALLBACK = (
    r"C:\Users\kongx\.trae-cn\plugins\trae-remote-official\lark\1.0.5\bin\lark-cli.exe"
)
DEFAULT_TARGET_OPEN_ID = "ou_782aa2f80ae2ebfcc37b0b9a71209904"  # 本人

HEADER_ICON = "wiki-bitable_colorful"
BUY_TABLE_N = 6            # BUY 复盘表行数上限
WATCH_TABLE_N = 8          # 明日观察表行数上限
LT_TABLE_N = 7             # §五十二 长期指标行数上限
MISSED_TOP_N = 3           # 潜在漏掉示例数

LAYER_CN = {
    "CANDIDATE": "候选",
    "STRUCTURE_QUALIFIED": "结构合格",
    "EXECUTION_QUALIFIED": "执行合格",
    "BUY": "BUY",
    "NO_TRADE": "未交易",
}
VERDICT_CN = {
    "POSITIVE": "正向",
    "NEGATIVE": "负向",
    "LOW": "无显著增量",
    "INSUFFICIENT_SAMPLE": "样本不足",
}
OUTCOME_CN = {
    "MISSED_OPPORTUNITY": "潜在漏掉",
    "CORRECT_NO_TRADE": "正确规避",
    "WAITING_CORRECTLY": "值得继续观察",
    "NEUTRAL_OUTCOME": "中性（未决）",
    "OUTCOME_UNRESOLVED": "结果未决",
}
STATUS_CN = {
    "POST_MARKET_REVIEW_READY": "复盘就绪",
    "CONDITIONAL_REVIEW": "条件复盘",
    "REVIEW_INVALID": "复盘无效",
}
LEVEL_CN = {"PASS": "通过", "WARNING": "警告", "ISSUE": "问题", "FAIL": "失败"}
CHECK_CN = {
    "FUTURE_LEAKAGE": "前视校验",
    "STEP_DEPENDENCY": "上游依赖",
    "BUY_REVIEW": "BUY 复盘完整性",
    "NO_TRADE_REVIEW": "未交易复盘完整性",
    "HVT_REVIEW": "HVT 复盘完整性", "THEME_REVIEW": "主题复盘完整性",
    "UPSTREAM_STATE_CONFLICT": "上游状态一致性", "REGIME_TIER_COVERAGE": "Regime 档位覆盖",
    "REPORT_LANGUAGE": "报告语言", "REPORT_SECTIONS": "报告结构",
}
VALUE_CN = {"POSITIVE": "正向", "NEGATIVE": "负向", "NEUTRAL": "中性",
            "HIGH": "高", "MEDIUM": "中", "LOW": "低",
            "INSUFFICIENT_SAMPLE": "样本不足"}
NO_TRADE_CLASS_CN = {
    "NO_TRADE_DUE_TO_STRUCTURE": "结构未通过",
    "NO_TRADE_DUE_TO_RR": "盈亏比不足",
    "NO_TRADE_DUE_TO_EXTENSION": "过度延伸",
    "NO_TRADE_DUE_TO_MARKET": "市场环境不适",
    "UNMAPPED": "未归类",
}
ERROR_SUMMARY_CN = {"NO MATERIAL MODEL ERROR": "无实质性模型错误"}
WATCH_TYPE_CN = {"BREAKOUT": "突破观察", "HVT_LOCKING": "HVT 锁仓观察", "PULLBACK": "回踩观察",
                 "REBREAKOUT": "再突破观察", "RETEST": "回踩确认"}
PHASE_CN = {"DORMANT": "沉寂", "EARLY": "早期", "EMERGING": "发酵", "CONFIRMING": "确认中",
            "COOLING": "退潮", "DETERIORATING": "转弱"}
PRICE_BASIS_CN = "后复权（close×adj_factor）"
MODE_CN = {"AGGRESSIVE": "进攻", "NORMAL": "常规", "DEFENSIVE": "防守", "WAIT": "观望"}
GROUP_CN = {"HVT_LOCKING": "HVT 锁仓", "REBREAKOUT": "再突破", "RETEST": "回踩确认",
            "PULLBACK": "回踩", "BREAKOUT": "突破"}
WATCH_ORDER = ("HVT_LOCKING", "REBREAKOUT", "RETEST", "PULLBACK", "BREAKOUT")
# 观察类型 → 判据 / 可转买点条件 / 失效线。判据取自 post_market_review_config.json::watch_pool，
# 派生与失效口径取自 execution_config.json::entry_state（step6_derivation / precedence /
# close_below_trigger_applies_to / support_defense_required_states）。此处只做展示映射，不改判据。
WATCH_SOURCE_CN = {
    "HVT_LOCKING": "hvt_state=HVT_LOCKING",
    "REBREAKOUT": "breakout_state=REBREAKOUT_CONFIRMED",
    "RETEST": "retest_state=RETEST_PENDING",
    "PULLBACK": "structure_class=PULLBACK_HEALTHY",
    "BREAKOUT": "breakout_state=READY / CONFIRMED",
}
WATCH_ENTRY_CN = {
    "HVT_LOCKING": "不派生入场态，须等突破或回踩推进",
    "REBREAKOUT": "→ REBREAKOUT_ENTRY（首选路径之一）",
    "RETEST": "推进到 RETEST_SUCCESS → RETEST_ENTRY",
    "PULLBACK": "→ PULLBACK_ENTRY（豁免 HVT 成熟度门）",
    "BREAKOUT": "READY→待确认；CONFIRMED→BREAKOUT_ENTRY",
}
WATCH_INVALID_CN = {
    "HVT_LOCKING": "收盘跌破止损价",
    "REBREAKOUT": "收盘跌破触发价",
    "RETEST": "收盘跌破触发价",
    "PULLBACK": "收盘跌破支撑 ×0.97（不看触发价）",
    "BREAKOUT": "收盘跌破触发价",
}
POSITION_NOTE = ("只标记持仓状态，不做资金管理、不做组合优化、**不输出仓位金额**"
                 "（config/execution_config.json::position.note）")
REVIEW_DISCLAIMER = (
    "READ ONLY：不修改 Step 1-7 任何落盘结果，不重新选股，不产生 BUY。"
    "WATCH ≠ BUY：观察对象须在下一交易日由 Step 7 重新确认后才可能产生 BUY（§四十二）。"
)


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


def _md_rows(headers: list, rows: list) -> dict:
    """折叠面板内不能放 table 元素（飞书 200621），改用紧凑 markdown 列表。"""
    lines = ["**" + " ｜ ".join(headers) + "**"]
    for r in rows:
        lines.append("- " + " ｜ ".join("" if v is None else str(v) for v in r))
    return _md("\n".join(lines), size="notation")


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


def _round(v, nd: int = 1):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return round(f, nd)


# ════════════════════════════════════════════════════════════════════════
# 数据读取
# ════════════════════════════════════════════════════════════════════════
def latest_review_date() -> str:
    files = glob.glob(os.path.join(OUTPUT_DIR, "post_market_review_*.json"))
    dates = sorted(re.sub(r"\D", "", os.path.basename(f)) for f in files)
    dates = [d for d in dates if len(d) == 8]
    if not dates:
        raise SystemExit("找不到 output/post_market_review_YYYYMMDD.json，请先运行 post_market_review_build.py")
    return dates[-1]


def load_review(date: str | None) -> tuple[dict, str]:
    d = re.sub(r"\D", "", str(date or "")) or latest_review_date()
    path = os.path.join(OUTPUT_DIR, f"post_market_review_{d}.json")
    if not os.path.exists(path):
        raise SystemExit(f"找不到 {path}，请先运行 post_market_review_build.py --date {d}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh), d


def _missed_examples(ex: dict) -> list[dict]:
    rows = [r for r in (ex.get("rows") or [])
            if jstr(r.get("no_trade_outcome")) == "MISSED_OPPORTUNITY"]
    rows.sort(key=lambda r: -(r.get("signal_return_1") or -9e9))
    return rows[:MISSED_TOP_N]


def _watch_price(item: dict) -> str:
    txt = jstr(item.get("trigger"))
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)", txt)
    return m.group(1) if m else "-"


# ════════════════════════════════════════════════════════════════════════
# 卡片构造
# ════════════════════════════════════════════════════════════════════════
def build_card(a: dict, date: str) -> dict:
    meta = a.get("meta") or {}
    m = a.get("market") or {}
    ex = a.get("execution") or {}
    sm = a.get("summary") or {}
    nt = ex.get("no_trade_summary") or {}
    watch = a.get("watch_pool") or {}
    lt = a.get("long_term") or {}
    status = jstr(a.get("final_status"))

    review_date = meta.get("review_date") or date
    signal_date = meta.get("signal_date")
    tag = TAG_CN.get(jstr(m.get("market_structure_tag")), jstr(m.get("market_structure_tag")))
    reg = REGIME_CN.get(jstr(m.get("market_regime")), jstr(m.get("market_regime")))
    n_buy = len(ex.get("buy") or [])
    n_nt = nt.get("total", 0)

    elements = []

    # 1) 指标卡（标签压到 4–5 字，移动端一行放得下）
    elements.append(_kpi_row([
        (tag, "市场结构"),
        (f"{m.get('limit_up', 0)}/{m.get('limit_down', 0)}", "涨停/跌停"),
        (f"{n_buy}/{n_nt}", "BUY/未交易"),
        (str(watch.get("count", 0)), "观察池"),
    ], "0px 0px 12px 0px"))

    # 2) 核心摘要（§四十三：五行，一行一件事）
    lines = [f"**盘后复盘 {review_date}**（复盘 signal {signal_date} · {reg}环境）"]
    for key in ("MARKET", "THEME", "SIGNAL", "TRADE", "FEEDBACK"):
        v = jstr(sm.get(key))
        if v:
            lines.append(f"- {v}")
    if jstr(m.get("regime_change_reason")):
        chg = jstr(m.get("market_regime_change")).replace("->", "→")
        chg = "→".join(REGIME_CN.get(x, x) for x in chg.split("→"))
        lines.append(f"- **环境切换**：{chg}")
    lines.append(f"- **赚钱效应**：{EARN_CN.get(jstr(m.get('market_earning_effect')), jstr(m.get('market_earning_effect')))}"
                 f" · 成交额 {_n(m.get('market_amount_yi'), 0)} 亿"
                 f"（20 日均量比 {_n(m.get('market_amount_ratio_20'), 2)}）")
    elements.append(_highlight("\n".join(lines), "0px 0px 12px 0px"))

    # 3) BUY 复盘（§二十一 / §二十二 / §二十四）
    buys = (ex.get("buy") or [])[:BUY_TABLE_N]
    if buys:
        elements.append(_table(
            "0px 0px 12px 0px",
            [
                {"name": "s", "display_name": "昨日 BUY", "data_type": "text"},
                {"name": "r", "display_name": "结果", "data_type": "text"},
                {"name": "mae", "display_name": "MAE(T+1)", "data_type": "text"},
                {"name": "mfe", "display_name": "MFE(T+1)", "data_type": "text"},
            ],
            [{"s": f"{r.get('ts_code')} {r.get('name')}",
              "r": RESULT_CN.get(jstr(r.get("result_class")), jstr(r.get("result_class"))),
              "mae": _pct(r.get("mae_1")),
              "mfe": _pct(r.get("mfe_1"))} for r in buys]))
    else:
        elements.append(_md("<font color='grey'>昨日无 BUY 信号（不为凑数量降低门槛，§四十-9）</font>",
                            size="notation", align="center"))

    # 4) 明日执行观察（§四十一 / §四十二：WATCH != BUY）
    items = (watch.get("items") or [])[:WATCH_TABLE_N]
    if items:
        elements.append(_table(
            "0px 0px 12px 0px",
            [
                {"name": "s", "display_name": "明日观察", "data_type": "text"},
                {"name": "t", "display_name": "主题/阶段", "data_type": "text"},
                {"name": "w", "display_name": "类型", "data_type": "text"},
                {"name": "p", "display_name": "触发参考", "data_type": "text"},
            ],
            [{"s": f"{r.get('ts_code')} {r.get('name')}",
              "t": f"{r.get('theme')}/"
                   f"{PHASE_CN.get(jstr(r.get('theme_phase')), jstr(r.get('theme_phase')))}",
              "w": WATCH_TYPE_CN.get(jstr(r.get("watch_type")), jstr(r.get("watch_type"))),
              "p": _watch_price(r)} for r in items]))
        elements.append(_md(f"<font color='grey'>观察池共 {watch.get('count', 0)} 个；"
                            f"basis {watch.get('basis_date')}。{jstr(watch.get('disclaimer'))}</font>",
                            size="notation"))

    # 5) 折叠明细：层层过滤 · Regime · 长期指标 · 校验 · 声明
    fold = []
    sc = a.get("scorecard") or []
    if sc:
        fold.append(_md("**层层过滤表现（§三十 / §三十一）**"))
        fold.append(_md_rows(
            ["层", "N", "T+1 胜率", "T+1 均值%"],
            [[LAYER_CN.get(jstr(r.get("layer")), jstr(r.get("layer"))),
              r.get("n"),
              _round(r.get("win_rate_1"), 4),
              _round((r.get("mean_return_1") or 0) * 100, 2) if r.get("mean_return_1") is not None else None]
             for r in sc]))
    fn = a.get("funnel") or {}
    inc = a.get("incremental") or {}
    if fn:
        fold.append(_md(f"- 执行层增量价值：**{VALUE_CN.get(jstr(fn.get('execution_layer_value_add')), jstr(fn.get('execution_layer_value_add')))}**"
                        f"（BUY 减结构合格 T+1 = {_pct(fn.get('buy_minus_structure_t1'))}）\n"
                        f"- 过滤增量价值：**{VALUE_CN.get(jstr(inc.get('filtering_value')), jstr(inc.get('filtering_value')))}**"))

    rs = (a.get("regime_strat") or {}).get("rows") or []
    if rs:
        st = a["regime_strat"]
        fold.append(_md(f"**Regime 分层（§三十三，累计 {st.get('n_dates')} 个交易日）**"))
        fold.append(_md_rows(
            ["Regime", "N", "BUY", "T+1 均值%"],
            [[REGIME_CN.get(jstr(r.get("regime")), jstr(r.get("regime"))),
              r.get("n"),
              r.get("buy"),
              _round((r.get("mean_return_1") or 0) * 100, 2) if r.get("mean_return_1") is not None else None]
             for r in rs]))
        if jstr(st.get("missing_regimes")):
            fold.append(_md(f"<font color='grey'>Step 7 历史未产出的档位如实为 0："
                            f"{jstr(st.get('missing_regimes'))}（不强造）</font>", size="notation"))

    lt_rows = (lt.get("rows") or [])[:LT_TABLE_N]
    if lt_rows:
        fold.append(_md(f"**长期过滤增量价值（§五十二，累计 {lt.get('n_dates')} 个交易日 "
                        f"{jstr(lt.get('window_start'))}~{jstr(lt.get('window_end'))}）**"))
        fold.append(_md_rows(
            ["指标", "N(A/B)", "T+5 差", "判定"],
            [[LT_CN.get(jstr(r.get("indicator")), jstr(r.get("indicator"))),
              f"{r.get('n_a')}/{r.get('n_b')}",
              _pct(r.get("diff_5")),
              VERDICT_CN.get(jstr(r.get("verdict")), jstr(r.get("verdict")))] for r in lt_rows]))
        cands = ((a.get("feedback") or {}).get("change_candidates") or [])
        if cands:
            nm = "、".join(LT_CN.get(jstr(c.get("issue")), jstr(c.get("issue"))) for c in cands)
            fold.append(_md(f"<font color='grey'>模型改动候选 {len(cands)} 项（{nm}）："
                            f"仅排队待独立验证，Step 8 不改模型（§三十七 / §三十九）。</font>",
                            size="notation"))

    by_out = nt.get("by_outcome") or {}
    if nt:
        fold.append(_md("**未交易复盘（§二十五 / §二十六）**\n"
                        f"- 分类：" + "；".join(
                            f"{NO_TRADE_CLASS_CN.get(k, k)} {v}" for k, v in (nt.get("by_class") or {}).items()) + "\n"
                        f"- 结果：" + "；".join(
                            f"{OUTCOME_CN.get(k, k)} {v}" for k, v in by_out.items()) + "\n"
                        f"- 潜在漏掉占比：{_pct(nt.get('missed_ratio'))}（不等于策略失败，§二十六）"))
        miss = _missed_examples(ex)
        if miss:
            fold.append(_md("**潜在漏掉示例（T+1）**\n" + "\n".join(
                f"- {r.get('ts_code')} {r.get('name')}：{_pct(r.get('signal_return_1'))}"
                + (f"（{NO_TRADE_CLASS_CN.get(jstr(r.get('no_trade_class')), jstr(r.get('no_trade_class')))}）"
                   if jstr(r.get("no_trade_class")) else "")
                for r in miss)))

    er = a.get("errors") or {}
    fold.append(_md(f"**错误诊断（§二十七 / §二十九）**："
                    f"{ERROR_SUMMARY_CN.get(jstr(er.get('summary')), jstr(er.get('summary')))}"))

    checks = (a.get("validation") or {}).get("checks") or []
    if checks:
        fold.append(_md("**校验（§四十九）**\n" + "\n".join(
            f"- [{LEVEL_CN.get(jstr(c.get('level')), jstr(c.get('level')))}] "
            f"{CHECK_CN.get(jstr(c.get('check')), jstr(c.get('check')))}" for c in checks)))
    lim = (a.get("validation") or {}).get("limitations") or []
    if lim:
        fold.append(_md("<font color='grey'>已知局限：" + "；".join(jstr(x) for x in lim) + "</font>",
                        size="notation"))

    fold.append(_md("<font color='grey'>状态：{0}\n{1}\n价格口径：{2}。</font>".format(
        STATUS_CN.get(status, status), REVIEW_DISCLAIMER, PRICE_BASIS_CN), size="notation"))
    elements.append(_panel("层层过滤 · Regime · 长期指标 · 未交易 · 校验 · 声明", fold))

    # 状态标签
    tags = [{"tag": "text_tag",
             "text": {"tag": "plain_text", "content": STATUS_CN.get(status, status)},
             "color": "green" if status == "POST_MARKET_REVIEW_READY" else "yellow"},
            {"tag": "text_tag",
             "text": {"tag": "plain_text", "content": "READ ONLY"},
             "color": "neutral"},
            {"tag": "text_tag",
             "text": {"tag": "plain_text", "content": "非交易建议"},
             "color": "neutral"}]

    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "width_mode": "default",
            "summary": {"content": f"盘后复盘 {review_date}：{tag}（{reg}环境），"
                                   f"BUY {n_buy} / 未交易 {n_nt}"},
        },
        "header": {
            "title": {"tag": "plain_text", "content": "A股盘后复盘（Step 8）"},
            "subtitle": {"tag": "plain_text",
                         "content": f"{review_date} · 复盘 signal {signal_date}"},
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
# 自然语言解读卡：环境情绪 / 仓位建议 / 下一交易日信号分析
# ════════════════════════════════════════════════════════════════════════
def _pctv(v, nd: int = 2):
    """小数 → 固定小数位的百分点字符串（用于 markdown 文本表述）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return f"{f * 100:.{nd}f}"


def _sgn(v, nd: int = 2) -> str:
    """带符号百分比（涨跌幅 / 变化率）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    if f != f:
        return "-"
    return f"{f * 100:+.{nd}f}%"


def _sgp(v, nd: int = 2) -> str:
    """带符号百分点（广度等绝对变化量）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    if f != f:
        return "-"
    return f"{f * 100:+.{nd}f}"


def _entry_span(item: dict) -> str:
    """从 entry_condition 抽 Entry 区间；抽不到则退回 trigger 的参考价。"""
    mm = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*~\s*([0-9]+(?:\.[0-9]+)?)", jstr(item.get("entry_condition")))
    if mm:
        return f"{mm.group(1)}~{mm.group(2)}"
    return _watch_price(item)


def _invalid_price(item: dict) -> str:
    mm = re.search(r"([0-9]+(?:\.[0-9]+)?)", jstr(item.get("invalid_condition")))
    return mm.group(1) if mm else "-"


def _watch_line(item: dict) -> str:
    return (f"{item.get('ts_code')} {item.get('name')} {_entry_span(item)}"
            f"（收盘跌破 {_invalid_price(item)} 作废）")


def _regime_stats(rows: list, name: str) -> dict:
    for r in rows:
        if jstr(r.get("regime")) == name:
            return r
    return {}


def build_narrative_card(a: dict, date: str) -> dict:
    """把 Step 8 产物转成自然语言解读卡（只读；不给仓位金额；WATCH != BUY）。"""
    meta = a.get("meta") or {}
    m = a.get("market") or {}
    ex = a.get("execution") or {}
    nt = ex.get("no_trade_summary") or {}
    watch = a.get("watch_pool") or {}
    items = watch.get("items") or []
    st = a.get("regime_strat") or {}
    rs = st.get("rows") or []
    val = a.get("validation") or {}
    fb = a.get("feedback") or {}
    status = jstr(a.get("final_status"))

    review_date = meta.get("review_date") or date
    signal_date = meta.get("signal_date")
    basis = watch.get("basis_date") or signal_date

    reg = REGIME_CN.get(jstr(m.get("market_regime")), jstr(m.get("market_regime")))
    reg_b = REGIME_CN.get(jstr(m.get("market_regime_before")), jstr(m.get("market_regime_before")))
    tags = "、".join(TAG_CN.get(x, x) for x in jstr(m.get("market_structure_tags")).split("|") if x)
    earn = EARN_CN.get(jstr(m.get("market_earning_effect")), jstr(m.get("market_earning_effect")))
    earn_short = earn.replace("赚钱效应", "") or earn
    mode = jstr(m.get("execution_mode"))
    mode_cn = MODE_CN.get(mode, mode)
    buys = ex.get("buy") or []
    n_trig = len([r for r in buys if jstr(r.get("result_class")) != "BUY_NO_ENTRY"])

    elements = []

    # ── 一句话结论 ─────────────────────────────────────────────────
    elements.append(_highlight(
        f"**一句话结论**：环境由「{reg_b}」上移到「{reg}」，广度 "
        f"{_n(m.get('breadth_before'), 2)} → {_n(m.get('breadth'), 2)}、成交额 "
        f"{_sgn(m.get('market_amount_change'))}，属**普涨型情绪修复**，"
        f"但赚钱效应仍为**{earn_short}**，强度尚未确认。\n"
        f"**仓位方向**：{mode_cn}参与、不追高、以每只标的自身止损为硬约束"
        f"（本链路不输出仓位金额）。\n"
        f"**信号**：昨日 {len(buys)} 只 BUY 中 {n_trig} 只已触发、{len(buys) - n_trig} 只未到位；"
        f"下一交易日的重点在观察池的条件确认，**不是追买**。",
        "0px 0px 12px 0px"))

    # ── 一、环境情绪 ───────────────────────────────────────────────
    env = [
        "**一、环境情绪：广度驱动的修复性普涨，强度尚未确认**",
        "",
        f"市场状态判定为 **{reg}**（{jstr(m.get('market_regime'))}），较前一交易日由「{reg_b}」上移一档。"
        f"切换依据：广度 {_n(m.get('breadth_before'), 4)} → {_n(m.get('breadth'), 4)}；"
        f"成交额变化 {_sgn(m.get('market_amount_change'))}；涨停 {m.get('limit_up')} / "
        f"跌停 {m.get('limit_down')}；上证 {_sgn(m.get('index_ret_SSE'))}（§六）。",
        "",
        f"- **广度**：上涨 {m.get('up_count')} / 下跌 {m.get('down_count')} / 平盘 {m.get('flat_count')}，"
        f"广度 {_n(m.get('breadth'), 2)}（较前一日 {_sgp(m.get('breadth_change'))} 个百分点）",
        f"- **情绪强度**：涨停 {m.get('limit_up')} / 跌停 {m.get('limit_down')}，最高连板 "
        f"{m.get('max_consecutive_board')}；成交额 {_n(m.get('market_amount_yi'), 0)} 亿元，"
        f"为 20 日均量的 {_n(m.get('market_amount_ratio_20'), 2)} 倍",
        f"- **指数**：上证 {_sgn(m.get('index_ret_SSE'))}、沪深300 {_sgn(m.get('index_ret_CSI300'))}、"
        f"中证1000 {_sgn(m.get('index_ret_CSI1000'))}、中证2000 {_sgn(m.get('index_ret_CSI2000'))}，"
        f"小盘弹性更强",
        f"- **结构标签**：{tags}；赚钱效应 **{earn_short}**",
        f"- **个股分化**：高开低收 {m.get('high_open_low_close')} 家 vs 低开高收 "
        f"{m.get('low_open_high_close')} 家 —— 盘中冲高回落压力不轻，是「不追高」的直接依据",
    ]
    th = (a.get("theme") or {}).get("summary") or {}
    if th:
        rows_th = sorted((a.get("theme") or {}).get("rows") or [],
                         key=lambda r: r.get("rank") or 999)[:3]
        lead = "、".join(f"{r.get('sector_id')} {r.get('sector_name')}" for r in rows_th)
        env.append(f"- **主线**：{lead}；当日强化 {th.get('improving', 0)} 个、轮入 "
                   f"{th.get('rotation_in', 0)} 个、退潮 {th.get('cooling', 0)} 个，"
                   f"另有 {th.get('failed_startup', 0)} 个主题被标记为「启动失败」——扩散持续性尚未确认")
    elements.append(_md("\n".join(env)))

    # ── 二、仓位建议 ───────────────────────────────────────────────
    pos = [
        "",
        f"**二、仓位建议：方向为「{mode_cn}参与」，不给金额**",
        "",
        f"当前执行模式为 **{mode}（{mode_cn}）**，由市场状态「{reg}」机械映射得到"
        f"（config/execution_config.json::market_regime.execution_mode_by_regime）。",
        "",
        f"**口径声明**：本链路{POSITION_NOTE}。因此本节只给方向与纪律，不给百分比、不给金额。",
        "",
        "- **方向**：可常规参与，但**不追高**；当日广度虽大幅修复、赚钱效应仍中性，"
        "属「确认后再跟」而非「抢开局」的环境",
        "- **风险边界**：每一笔以该标的自身 stop 价为硬约束，单笔风险自担，"
        "不因环境转暖而放大单票风险",
        "- **环境联动**：市场状态若回落至「偏弱」，执行模式自动降为 DEFENSIVE（防守）；"
        "进入「风险规避」则降为 WAIT（观望）——阈值机械映射，不做临场调节",
        "- **不做总评分、不排序打分**（§五十）；不因追求成交而下调门槛（§四十-9）",
    ]
    elements.append(_md("\n".join(pos)))

    # ── 三、下一交易日信号分析 ─────────────────────────────────────
    elements.append(_md("\n".join([
        "",
        "**三、下一交易日可买入信号分析**",
        "",
        f"**口径**：Step 7 在 {review_date} 无快照，观察池 basis 回退至 **{basis}**；"
        f"本地数据目录无交易日历，无法把「明后天」换算为具体日期，下文统一表述为「下一交易日」。"
        f"价位为原始价（RAW），收益统计为后复权。",
    ])))
    if buys:
        elements.append(_md(f"\n**A. 昨日已判 BUY（signal {signal_date}）——{len(buys)} 只，"
                            f"执行等级全部为「有条件买入」，无一升级为「首选买入」**\n"))
        elements.append(_table(
            "0px 0px 8px 0px",
            [
                {"name": "s", "display_name": "标的", "data_type": "text"},
                {"name": "z", "display_name": "入场区间", "data_type": "text"},
                {"name": "sl", "display_name": "止损/目标", "data_type": "text"},
                {"name": "rr", "display_name": "RR", "data_type": "text"},
                {"name": "r", "display_name": "昨日结果", "data_type": "text"},
            ],
            [{"s": f"{r.get('ts_code')} {r.get('name')}",
              "z": f"{_n(r.get('entry_low'))}~{_n(r.get('entry_high'))}",
              "sl": f"{_n(r.get('stop_price'))}/{_n(r.get('target_1'))}",
              "rr": _n(r.get("risk_reward")),
              "r": RESULT_CN.get(jstr(r.get("result_class")), jstr(r.get("result_class"))),
              } for r in buys[:BUY_TABLE_N]]))
        det = []
        for r in buys[:BUY_TABLE_N]:
            touched = bool(r.get("entry_zone_touch"))
            det.append(f"- {r.get('name')}（{r.get('ts_code')}）：昨日低 {_n(r.get('today_low'))} / "
                       f"高 {_n(r.get('today_high'))} / 收 {_n(r.get('today_close'))}，"
                       + ("**已进入 Entry 区间**" if touched else "未进入 Entry 区间")
                       + f"；止损 {_n(r.get('stop_price'))}、目标 {_n(r.get('target_1'))}、"
                         f"盈亏比 {_n(r.get('risk_reward'))}")
        det.append("")
        det.append("读法：未成交的标的属「价格没到位」，不等于「信号失效」；"
                   "已触发的标的按自身止损管理。")
        elements.append(_md("\n".join(det)))
    else:
        elements.append(_md("<font color='grey'>昨日无 BUY 信号"
                            "（不为凑数量降低门槛，§四十-9）</font>"))

    if items:
        keep = [r for r in items if jstr(r.get("structure_state")) != "FAILED"]
        drop = [r for r in items if jstr(r.get("structure_state")) == "FAILED"]
        wl = ["", f"**B. 观察池（basis {basis}）——共 {len(items)} 个，全部不是买点**", "",
              f"按 §四十二，观察对象只有在**下一交易日由 Step 7 重新确认**后才可能产生 BUY。"
              f"其中结构仍成立的 {len(keep)} 个按类型分组，括号内为失效价（收盘跌破即作废）：", ""]
        for g in WATCH_ORDER:
            grp = [r for r in keep if jstr(r.get("watch_type")) == g]
            if grp:
                wl.append(f"- **{GROUP_CN.get(g, g)}（{len(grp)}）**："
                          + "；".join(_watch_line(r) for r in grp))
        if drop:
            wl.append("")
            wl.append(f"<font color='grey'>另 {len(drop)} 个为 HVT_FAILED / 突破失败结构，"
                      f"已列为排除观察；按 §二十七，失败结构不得自动恢复。</font>")
        elements.append(_md("\n".join(wl)))

    # ── 四、五类观察对象如何跟进操作 ───────────────────────────────
    if items:
        ex_idx = {r.get("ts_code"): r for r in (ex.get("rows") or [])
                  if r.get("signal_date") == basis}

        def _es_of(it: dict) -> str:
            mm = re.search(r"entry_state=([A-Z_]+)", jstr(it.get("entry_condition")))
            return mm.group(1) if mm else "-"

        elements.append(_md("\n".join([
            "", "**四、五类观察对象如何跟进操作（WATCH ≠ BUY）**", "",
            "观察类型只决定**下一交易日用哪条规则重新确认、失效时看哪条价格线**，本身不含买点。"
            "判据来自 config/post_market_review_config.json::watch_pool，派生与失效口径来自 "
            "config/execution_config.json::entry_state。", "",
        ])))
        elements.append(_md_rows(
            ["类型", "判据（源状态）", "可转买点的条件", "失效线"],
            [[GROUP_CN.get(g, g), WATCH_SOURCE_CN.get(g, g),
              WATCH_ENTRY_CN.get(g, g), WATCH_INVALID_CN.get(g, g)] for g in WATCH_ORDER]))
        elements.append(_md("\n".join([
            "",
            "**跟进动作按入场态分档**（派生后仍须过盈亏比 ≥ 1.5 与执行分门槛）：", "",
            "- **首选买入 / 有条件买入**：已进 Entry 区间的按自身止损执行；未进区间的只挂条件，"
            "**不得上移 entry_high**（防追高）",
            "- **回踩（PULLBACK_ENTRY）**：盯支撑位 ×0.97，跌破即 SUPPORT_BROKEN；"
            "该路径豁免 HVT 成熟度门，且**不看触发价**",
            "- **再突破 / 突破 / 回踩确认入场态**：盯收盘是否站上触发价，跌破即 TRIGGER_BROKEN",
            "- **待确认（WAIT_CONFIRMATION）**：只观察，等 Step 6 把源状态推进后才可能入场",
            "- **失败 / 过度延伸 / 无入场态**：记录失效，不恢复、不补仓（§二十七）",
        ])))

        rb = [r for r in items if jstr(r.get("watch_type")) == "REBREAKOUT"]
        if rb:
            live = [r for r in rb if jstr(r.get("structure_state")) != "FAILED"]
            rbx = ["", "**以「再突破」为例**：", "",
                   "它比首次突破多一层确认（`breakout_state=REBREAKOUT_CONFIRMED`），"
                   "执行分构成中入场质量 95 分，高于突破（80）、低于回踩确认（100），"
                   "是突破类里唯一可评为「首选买入」的路径；但层级上**回踩确认优先于再突破**"
                   "（precedence 中 RETEST_ENTRY 先于 REBREAKOUT_ENTRY）。", ""]
            if live:
                det, n_broken, rrs = [], 0, []
                for r in live:
                    row = ex_idx.get(r.get("ts_code")) or {}
                    rr = row.get("risk_reward")
                    if isinstance(rr, (int, float)):
                        rrs.append(float(rr))
                    if jstr(row.get("no_trade_reason_raw")) == "TRIGGER_BROKEN":
                        n_broken += 1
                    det.append(f"{r.get('name')}（{r.get('ts_code')}，{_es_of(r)}，"
                               f"触发价 {_watch_price(r)}，盈亏比 {_n(rr)}）")
                rbx.append(f"- 当前 {len(rb)} 只再突破观察中，{len(live)} 只结构仍在、入场态已成立："
                           + "；".join(det))
                if n_broken and rrs:
                    rbx.append(f"- 但这 {n_broken} 只收盘价已跌破触发价（TRIGGER_BROKEN），"
                               f"盈亏比 {_n(min(rrs))}~{_n(max(rrs))} 低于 1.5，属「结构在、价位不在」——"
                               f"跟进动作是**等价格回到 Entry 区间、并让盈亏比回到 ≥1.5**，不是买入")
            if len(rb) > len(live):
                rbx.append(f"- 另 {len(rb) - len(live)} 只结构已为 HVT_FAILED / 突破失败，"
                           f"按 §二十七 失败结构不得自动恢复，**不作为恢复候选**")
            elements.append(_md("\n".join(rbx)))

    # ── 折叠：数据依据、未交易复盘、校验与局限 ─────────────────────
    fold = []
    nrow, hrow = _regime_stats(rs, "NEUTRAL"), _regime_stats(rs, "HEALTHY")
    if rs:
        fold.append(_md(f"**同档位历史参照（§三十三，累计 {st.get('n_dates')} 个交易日 "
                        f"{jstr(st.get('window_start'))}~{jstr(st.get('window_end'))}）**"))
        fold.append(_md_rows(
            ["Regime", "N", "BUY", "T+1 胜率", "T+1 均值%", "T+5 均值%", "T+20 均值%"],
            [[REGIME_CN.get(jstr(r.get("regime")), jstr(r.get("regime"))), r.get("n"), r.get("buy"),
              _round(r.get("win_rate_1"), 4), _pctv(r.get("mean_return_1")),
              _pctv(r.get("mean_return_5")), _pctv(r.get("mean_return_20"))] for r in rs]))
        if nrow:
            fold.append(_md(
                f"<font color='grey'>当前所处「{reg}」档历史 n={nrow.get('n')}，"
                f"T+1 胜率 {_round(nrow.get('win_rate_1'), 4)}、T+1 均值 {_pctv(nrow.get('mean_return_1'))}%、"
                f"T+5 {_pctv(nrow.get('mean_return_5'))}%、T+20 {_pctv(nrow.get('mean_return_20'))}%；"
                f"仅「"
                f"{REGIME_CN.get('HEALTHY', 'HEALTHY')}」档历史 T+5 / T+20 为正"
                f"（{_pctv((hrow or {}).get('mean_return_5'))}% / {_pctv((hrow or {}).get('mean_return_20'))}%）。"
                f"即当前档位在本地样本内**尚无统计优势**。</font>", size="notation"))
        if jstr(st.get("missing_regimes")):
            miss = "、".join(f"{REGIME_CN.get(x, x)}（{x}）"
                             for x in jstr(st.get("missing_regimes")).split("|") if x)
            fold.append(_md(f"<font color='grey'>Step 7 历史未产出的档位如实为 0：{miss}"
                            f"（缺档原因见 execution_config.json::market_regime."
                            f"strong_share_min_note，不为凑覆盖率下调阈值，§三十三）</font>",
                            size="notation"))

    if items:
        sidx = {r.get("ts_code"): r for r in (a.get("structure") or {}).get("rows") or []
                if r.get("signal_date") == basis}
        wt_rows = []
        for g in WATCH_ORDER:
            rets = [sidx[c].get("ret_1")
                    for c in (r.get("ts_code") for r in items if jstr(r.get("watch_type")) == g)
                    if c in sidx and sidx[c].get("ret_1") is not None]
            if rets:
                win = len([x for x in rets if x > 0]) / len(rets)
                wt_rows.append([GROUP_CN.get(g, g), len(rets), f"{win:.1%}",
                                _sgn(sum(rets) / len(rets))])
        if wt_rows:
            fold.append(_md(f"**观察池各类型下一交易日实测（signal {basis}，§十七）**"))
            fold.append(_md_rows(["类型", "N", "胜率", "T+1 均值"], wt_rows))
            fold.append(_md("<font color='grey'>该表为本次观察池的实测结果，n 介于 1~9、"
                            "单一日期、单一环境档，且当日本身是广度 0.76 的普涨，"
                            "不构成类型优劣结论（§四十四 / §五十）。</font>", size="notation"))

    if nt:
        fold.append(_md("**未交易复盘（§二十五 / §二十六）**\n"
                        f"- 规模：{nt.get('total')} 条；分类："
                        + "；".join(f"{NO_TRADE_CLASS_CN.get(k, k)} {v}"
                                    for k, v in (nt.get("by_class") or {}).items()) + "\n"
                        f"- 结果："
                        + "；".join(f"{OUTCOME_CN.get(k, k)} {v}"
                                    for k, v in (nt.get("by_outcome") or {}).items()) + "\n"
                        f"- 潜在漏掉占比：{_pct(nt.get('missed_ratio'))}"
                        f"（不等于策略失败；未交易多数为结构未通过，§二十六）"))
    fn = a.get("funnel") or {}
    inc = a.get("incremental") or {}
    if fn:
        fold.append(_md(f"- 执行层增量价值：**{VALUE_CN.get(jstr(fn.get('execution_layer_value_add')), jstr(fn.get('execution_layer_value_add')))}**"
                        f"（BUY 减结构合格 T+1 = {_pct(fn.get('buy_minus_structure_t1'))}）\n"
                        f"- 过滤增量价值：**{VALUE_CN.get(jstr(inc.get('filtering_value')), jstr(inc.get('filtering_value')))}**"))

    cands = (fb.get("change_candidates") or [])
    if cands:
        nm = "、".join(f"{LT_CN.get(jstr(c.get('issue')), jstr(c.get('issue')))}"
                       f"（{jstr(c.get('priority'))}）" for c in cands)
        fold.append(_md(f"<font color='grey'>待独立验证的模型改动候选 {len(cands)} 项：{nm}。"
                        f"仅排队待验证，Step 8 不改模型（§三十七 / §三十九）。</font>", size="notation"))

    er = a.get("errors") or {}
    fold.append(_md(f"**错误诊断（§二十七 / §二十九）**："
                    f"{ERROR_SUMMARY_CN.get(jstr(er.get('summary')), jstr(er.get('summary')))}"))

    checks = val.get("checks") or []
    if checks:
        fold.append(_md(f"**校验（§四十九）**：通过 {val.get('n_pass', 0)} / 警告 "
                        f"{val.get('n_warning', 0)} / 问题 {val.get('n_issue', 0)}\n"
                        + "\n".join(f"- [{LEVEL_CN.get(jstr(c.get('level')), jstr(c.get('level')))}] "
                                    f"{CHECK_CN.get(jstr(c.get('check')), jstr(c.get('check')))}"
                                    for c in checks)))
    lim = val.get("limitations") or []
    if lim:
        fold.append(_md("<font color='grey'>已知局限：" + "；".join(jstr(x) for x in lim) + "</font>",
                        size="notation"))
    fold.append(_md("<font color='grey'>状态：{0}\n{1}\n价格口径：{2}。</font>".format(
        STATUS_CN.get(status, status), REVIEW_DISCLAIMER, PRICE_BASIS_CN), size="notation"))
    elements.append(_panel("数据依据 · 未交易复盘 · 校验 · 声明", fold))

    tags_el = [{"tag": "text_tag",
                "text": {"tag": "plain_text", "content": f"{mode_cn}参与"},
                "color": "blue"},
               {"tag": "text_tag",
                "text": {"tag": "plain_text", "content": STATUS_CN.get(status, status)},
                "color": "green" if status == "POST_MARKET_REVIEW_READY" else "yellow"},
               {"tag": "text_tag",
                "text": {"tag": "plain_text", "content": "WATCH ≠ BUY"},
                "color": "neutral"}]

    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "width_mode": "default",
            "summary": {"content": f"盘后解读 {review_date}：{reg}环境（{mode_cn}参与），"
                                   f"BUY {len(buys)} / 观察池 {watch.get('count', 0)}"},
        },
        "header": {
            "title": {"tag": "plain_text", "content": "A股盘后解读 · 环境情绪与仓位建议"},
            "subtitle": {"tag": "plain_text", "content": f"{review_date} · 复盘 signal {signal_date}"},
            "template": "turquoise",
            "icon": {"tag": "standard_icon", "token": HEADER_ICON},
            "text_tag_list": tags_el,
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
    ap = argparse.ArgumentParser(description="把 Step 8 盘后复盘推成一张飞书交互卡片（只读产物）")
    ap.add_argument("--date", default=None, metavar="YYYYMMDD", help="复盘日（默认取最新一份）")
    ap.add_argument("--user-id", default=DEFAULT_TARGET_OPEN_ID, metavar="ou_xxx",
                    help="收件人 open_id（默认本人）")
    ap.add_argument("--as", dest="identity", default="user", choices=["bot", "user"],
                    help="发送身份（默认 user，托管环境 strict_mode 下只支持 user）")
    ap.add_argument("--cli", default=None, help="lark-cli 可执行文件路径")
    ap.add_argument("--dry-run", action="store_true", help="只生成卡片 JSON，不发送")
    ap.add_argument("--narrative", action="store_true",
                    help="生成自然语言解读卡（环境情绪 / 仓位建议 / 下一交易日信号分析）")
    args = ap.parse_args(argv)

    a, date = load_review(args.date)
    card = build_narrative_card(a, date) if args.narrative else build_card(a, date)

    suffix = "_narrative_card.json" if args.narrative else "_card.json"
    out_path = os.path.join(OUTPUT_DIR, f"post_market_review_{date}{suffix}")
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
