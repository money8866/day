# -*- coding: utf-8 -*-
"""
CRR V1.0 报告渲染（Report）

职责：把 engine 的扫描结果渲染成一份自包含 HTML 报告 + 一份 Markdown 摘要，
      如实展示：市场状态、结论分组（BUY/WATCH/NO TRADE）、评分构成、
      数据缺口与代理口径、未实现模块。

原则（与全模块一致）：
  * 缺失一律显示 UNKNOWN，不填补、不省略；
  * 口径差异与代理关系照实披露，不静默掩盖；
  * 不输出用户未定义的内容（目标价、仓位比例等）；
  * 自包含单文件（内联 CSS，无外部依赖），可直接双击打开。

对外 API：
  render_html(R) -> str
  render_markdown(R) -> str
  write_report(R, out_dir=None) -> dict（html/md 路径）
"""
import os
from datetime import datetime

import numpy as np
import pandas as pd

from .config import REPORT_DIR, SCORE_CAP, UNKNOWN, PENDING_MODULES
from .engine import BUY, WATCH, NO_TRADE, _ACTION_CN

_STATE_CN = {
    "NORMAL": "常态",
    "CORRECTION": "回调",
    "SELLOFF": "抛售",
    "PANIC": "恐慌",
    "SELLING_EXHAUSTION": "抛压衰竭",
    "RECOVERY": "修复",
    UNKNOWN: "未知",
}


# ----------------------------------------------------------------------------
# 格式化工具
# ----------------------------------------------------------------------------

def _esc(s):
    """HTML 转义。"""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return np.nan
    return x if np.isfinite(x) else np.nan


def _num(x, nd=2):
    """数值 -> 字符串；缺失为 UNKNOWN。"""
    v = _f(x)
    return UNKNOWN if not np.isfinite(v) else f"{v:.{nd}f}"


def _pct(x, nd=2):
    """小数 -> 百分比字符串；缺失为 UNKNOWN。"""
    v = _f(x)
    return UNKNOWN if not np.isfinite(v) else f"{v * 100:.{nd}f}%"


def _int(x):
    v = _f(x)
    return UNKNOWN if not np.isfinite(v) else str(int(round(v)))


def _cell(x):
    """通用单元格：NaN/None -> UNKNOWN；bool -> 是/否。"""
    if x is None:
        return UNKNOWN
    if isinstance(x, (bool, np.bool_)):
        return "是" if bool(x) else "否"
    if isinstance(x, (int, np.integer)):
        return str(int(x))
    if isinstance(x, (float, np.floating)):
        return UNKNOWN if not np.isfinite(x) else f"{x:.4g}"
    s = str(x)
    return UNKNOWN if s in ("", "nan", "None", "NaN") else s


class _Raw:
    """标记一个单元格为已构造好的 HTML，转义时跳过（用于结论标签等富文本）。"""

    __slots__ = ("html",)

    def __init__(self, html):
        self.html = html


def _cell_html(c):
    """单元格 -> HTML 文本；_Raw 直接透传，其余一律转义。"""
    if isinstance(c, _Raw):
        return c.html
    return _esc(c)


def _table(headers, rows, cls="", num_cols=(), reason_col=None):
    """构造 <table>；rows 为二维列表。

    num_cols    数值列下标（不换行、右对齐）
    reason_col  判定理由列下标（给足最小宽度）
    """
    out = [f'<table class="{cls}"><thead><tr>']
    for i, h in enumerate(headers):
        c = "num" if i in num_cols else ("reason" if i == reason_col else "")
        attr = f' class="{c}"' if c else ""
        out.append(f"<th{attr}>{_esc(h)}</th>")
    out.append("</tr></thead><tbody>")
    for r in rows:
        cells = []
        for i, c in enumerate(r):
            cc = "num" if i in num_cols else ("reason" if i == reason_col else "")
            attr = f' class="{cc}"' if cc else ""
            cells.append(f"<td{attr}>{_cell_html(c)}</td>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def _kv_table(pairs):
    """键值表。"""
    return _table(["项目", "值"],
                  [[k, v] for k, v in pairs])


# ----------------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------------

_HTML_CSS = """<!DOCTYPE html>
<meta charset="utf-8">
<style>
:root{
  --bg:#0f1115; --panel:#171a21; --panel2:#1e222b; --line:#2a2f3a;
  --fg:#e6e9ef; --muted:#98a2b3; --dim:#6b7280;
  --buy:#22c55e; --watch:#f59e0b; --no:#ef4444; --accent:#3b82f6;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font-family:"Microsoft YaHei","PingFang SC","Hiragino Sans GB",-apple-system,Segoe UI,sans-serif;
  font-size:14px;line-height:1.6}
.wrap{max-width:1280px;margin:0 auto;padding:28px 22px 60px}
h1{font-size:24px;margin:0 0 4px}
h2{font-size:17px;margin:30px 0 12px;padding-left:10px;border-left:4px solid var(--accent)}
h3{font-size:14px;margin:18px 0 8px;color:var(--muted);font-weight:600}
.sub{color:var(--muted);font-size:13px;margin-bottom:6px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:16px 18px;margin:12px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:14px 0}
.kpi{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.kpi .lab{color:var(--muted);font-size:12px}
.kpi .val{font-size:21px;font-weight:700;margin-top:2px}
table{width:100%;border-collapse:collapse;margin:8px 0;font-size:13px}
th,td{border:1px solid var(--line);padding:6px 9px;text-align:left;vertical-align:top}
th{background:var(--panel2);color:var(--muted);font-weight:600;white-space:nowrap}
tbody tr:nth-child(even){background:rgba(255,255,255,.015)}
td{word-break:break-word}
/* 数值列不换行；判定理由列给足宽度，避免被挤成竖排碎字 */
td.num,th.num{white-space:nowrap;text-align:right}
th.reason,td.reason{min-width:260px;white-space:normal;word-break:break-word}
/* 宽表在窄视口下横向滚动，而不是把 11 列压成竖排碎字 */
.tbl-scroll table{min-width:1180px;table-layout:auto}
.tbl-scroll{overflow-x:auto;border-radius:8px;border:1px solid var(--line)}
.tag{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;
  font-weight:700;white-space:nowrap}
.t-BUY{background:rgba(34,197,94,.16);color:var(--buy);border:1px solid rgba(34,197,94,.4)}
.t-WATCH{background:rgba(245,158,11,.16);color:var(--watch);border:1px solid rgba(245,158,11,.4)}
.t-NO_TRADE{background:rgba(239,68,68,.14);color:var(--no);border:1px solid rgba(239,68,68,.35)}
.muted{color:var(--muted)}
.dim{color:var(--dim);font-size:12px}
ul{margin:6px 0 6px 20px;padding:0}
li{margin:3px 0}
.warn{border-left:3px solid var(--watch);background:rgba(245,158,11,.07);padding:8px 12px;border-radius:0 6px 6px 0}
.note{border-left:3px solid var(--accent);background:rgba(59,130,246,.07);padding:8px 12px;border-radius:0 6px 6px 0}
footer{margin-top:40px;padding-top:14px;border-top:1px solid var(--line);color:var(--dim);font-size:12px}
</style>
"""


def _kpi(lab, val, color=None):
    c = f" style='color:{color}'" if color else ""
    return (f"<div class='kpi'><div class='lab'>{_esc(lab)}</div>"
            f"<div class='val'{c}>{_esc(val)}</div></div>")


def _decision_rows(df, limit=None):
    """结论表格行（代码/名称/行业/总分/置信度/关键归因/理由）。"""
    rows = []
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return rows
    d = df if limit is None else df.head(limit)
    for code, r in d.iterrows():
        dec = str(r.get("decision", NO_TRADE))
        rows.append([
            str(code),
            _cell(r.get("name")),
            _cell(r.get("industry")),
            _num(r.get("total_score")),
            _num(r.get("confidence")),
            _pct(r.get("ret20")),
            _pct(r.get("specific_share")),
            _num(r.get("q_score")),
            _num(r.get("v_score")),
            _Raw(f'<span class="tag t-{_esc(dec.replace(" ", "_"))}">'
                 f'{_esc(dec)} / {_esc(_ACTION_CN.get(dec, dec))}</span>'),
            _cell(r.get("decision_reason")),
        ])
    return rows


def _decision_table(df, limit=None):
    headers = ["代码", "名称", "行业", "总分/55", "置信度", "20日跌幅",
               "特异占比", "质量分/30", "估值分/25", "结论", "判定理由"]
    rows = _decision_rows(df, limit)
    if not rows:
        return '<div class="dim">无</div>'
    return ('<div class="tbl-scroll">'
            + _table(headers, rows, num_cols=(3, 4, 5, 6, 7, 8), reason_col=10)
            + "</div>")


def render_html(R):
    """渲染完整 HTML 报告（自包含单文件）。"""
    mkt = R.get("market", {}) or {}
    meta = R.get("meta", {}) or {}
    res = R.get("results")
    buy = R.get("buy")
    watch = R.get("watch")
    no_trade = R.get("no_trade")
    summary = R.get("summary", {}) or {}

    state = str(mkt.get("state", UNKNOWN))
    state_cn = _STATE_CN.get(state, state)
    metrics = mkt.get("metrics", {}) or {}
    vmeta = meta.get("valuation_meta", {}) or {}
    qmeta = meta.get("module_meta", {}).get("quality", {}) or {}

    H = []
    H.append("<!DOCTYPE html><html lang='zh-CN'><head>")
    H.append("<meta charset='utf-8'>")
    H.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    H.append(f"<title>CRR 暴跌错杀扫描 {R.get('scan_date', '')}</title>")
    H.append(_HTML_CSS.replace("<!DOCTYPE html>\n", ""))
    H.append("</head><body><div class='wrap'>")

    # ---- 标题 ----
    H.append(f"<h1>CRR V1.0 暴跌错杀扫描报告 · {R.get('scan_date', UNKNOWN)}</h1>")
    H.append(f"<div class='sub'>Crash Repricing &amp; Recovery · "
             f"仅用本地 Tushare 缓存 + sli/industry DB · "
             f"生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>")

    # ---- 门控与总览 ----
    gate = meta.get("gate", UNKNOWN)
    gate_blocked = bool(summary.get("gate_blocked"))
    H.append("<div class='card'>")
    H.append(f"<div class='sub'>市场门控：{_esc(gate)}</div>")
    H.append("<div class='grid'>")
    H.append(_kpi("市场状态", f"{state} / {state_cn}",
                  "var(--no)" if state in ("PANIC", "SELLOFF") else None))
    H.append(_kpi("候选股", _int(summary.get("n_candidates"))))
    H.append(_kpi("有效评分", _int(summary.get("n_scored"))))
    H.append(_kpi("BUY", _int(summary.get("n_buy")), "var(--buy)"))
    H.append(_kpi("WATCH", _int(summary.get("n_watch")), "var(--watch)"))
    H.append(_kpi("NO TRADE", _int(summary.get("n_no_trade")), "var(--no)"))
    H.append(_kpi("最高总分/55", _cell(summary.get("max_total_score"))))
    H.append(_kpi("平均置信度", _num(meta.get("mean_confidence"))))
    H.append("</div>")
    if gate_blocked:
        H.append("<div class='warn'>当前不在暴跌扫描窗口，按模块一门控规则，"
                 "全市场结论为 NO TRADE —— 本系统不强制每日交易。</div>")
    H.append("</div>")

    # ---- 市场状态指标 ----
    H.append("<h2>一、市场状态（模块一，不占评分）</h2>")
    mk_rows = [
        ["数据基准日", _cell(mkt.get("as_of"))],
        ["状态（含记忆）", f"{state} / {state_cn}"],
        ["当日原始状态", _cell(mkt.get("raw_state"))],
        ["是否暴跌事件段内", _cell(mkt.get("in_crash_segment"))],
        ["事件段编号 / 段长", f"{_cell(mkt.get('segment_id'))} / {_cell(mkt.get('segment_len'))}"],
        ["严重度", _cell(mkt.get("severity"))],
        ["状态明确度", _num(mkt.get("state_confidence"))],
        ["全A 5日收益", _pct(metrics.get("ret5"))],
        ["全A 10日收益", _pct(metrics.get("ret10"))],
        ["全A 20日收益", _pct(metrics.get("ret20"))],
        ["当日下跌家数占比", _pct(metrics.get("down_ratio"))],
        ["近5日下跌占比均值", _pct(metrics.get("down_ratio_ma5"))],
        ["跌停家数（阈值代理）", _int(metrics.get("limit_down"))],
        ["涨停家数（阈值代理）", _int(metrics.get("limit_up"))],
        ["成交额（千元）", _num(metrics.get("amount"), 0)],
        ["成交额/20日均额", _num(metrics.get("amount_ratio20"))],
        ["20日波动率", _num(metrics.get("volatility"), 4)],
        ["波动率/基准倍数", _num(metrics.get("vol_ratio"))],
        ["个股收益中位数", _pct(metrics.get("ret_median"))],
    ]
    H.append("<div class='card'>")
    H.append(_kv_table(mk_rows))
    unknown_fields = mkt.get("unknown_fields") or []
    if unknown_fields:
        H.append(f"<div class='dim'>缺失指标（显示为 UNKNOWN）：{_esc(', '.join(unknown_fields))}</div>")
    H.append("<div class='note'>市场收益 = daily_cache 全A等权 pct_chg/100 逐日累乘（非指数）；"
             "涨跌停家数为 pct_chg 板块阈值代理（|pct_chg|>31 剔除异常）。</div>")
    H.append("</div>")

    # ---- 结论分组 ----
    H.append("<h2>二、结论分组（BUY / WATCH / NO TRADE）</h2>")
    H.append(f"<h3>BUY（{_int(summary.get('n_buy'))} 只"
             f"{'，全部列出' if len(buy) <= 30 else ''}）</h3>")
    H.append(_decision_table(buy))
    n_watch = len(watch) if isinstance(watch, pd.DataFrame) else 0
    H.append(f"<h3>WATCH（{_int(summary.get('n_watch'))} 只"
             + (f"，仅列前 {min(60, n_watch)} 只" if n_watch > 60 else "") + "）</h3>")
    H.append(_decision_table(watch, limit=60))
    n_nt = len(no_trade) if isinstance(no_trade, pd.DataFrame) else 0
    H.append(f"<h3>NO TRADE（{_int(summary.get('n_no_trade'))} 只"
             + (f"，仅列前 {min(30, n_nt)} 只" if n_nt > 30 else "") + "）</h3>")
    H.append(_decision_table(no_trade, limit=30))

    # ---- 评分构成 ----
    H.append("<h2>三、评分构成与判定规则</h2>")
    H.append("<div class='card'>")
    H.append(_kv_table([
        ["总分上限 SCORE_CAP", _int(SCORE_CAP)],
        ["模块三 基本面质量", "30 分（quality）"],
        ["模块四 估值错杀", "25 分（valuation）"],
        ["打分公式", _cell(meta.get("score_note"))],
        ["置信度公式", _cell(meta.get("confidence_note"))],
        ["判定规则", ", ".join(f"{k}={v}" for k, v in
                              (meta.get("decision_rules") or {}).items())],
        ["置信度权重", ", ".join(f"{k}={v}" for k, v in
                                (meta.get("confidence_weights") or {}).items())],
    ]))
    H.append("</div>")

    # ---- 估值明细（模块四） ----
    if res is not None and not res.empty and "v_score" in res.columns:
        H.append("<h3>模块四估值明细（按总分排序前 40）</h3>")
        rows = []
        for code, r in res.head(40).iterrows():
            rows.append([
                str(code), _cell(r.get("name")),
                _num(r.get("v_pe_ttm")), _num(r.get("v_pb")),
                _num(r.get("v_pe_cheap_pct"), 3), _num(r.get("v_pb_cheap_pct"), 3),
                _num(r.get("v_fair_value_gap_raw")),
                _num(r.get("v_forecast_downgrade_raw")),
                _num(r.get("v_drop_vs_downgrade_raw")),
                _num(r.get("v_score")),
                _num(r.get("v_completeness")),
                _cell(r.get("v_pseudo_value")),
                _cell(r.get("v_unknown_fields")),
            ])
        H.append("<div class='tbl-scroll'>")
        H.append(_table(["代码", "名称", "PE_TTM", "PB", "PE便宜分位",
                          "PB便宜分位", "合理市值缺口%", "预告同比%",
                          "跌幅-下修", "估值分/25", "完整度", "伪低估值", "缺失项"],
                         rows, num_cols=(2, 3, 4, 5, 6, 7, 8, 9, 10)))
        H.append("</div>")
        H.append(f"<div class='dim'>估值子项覆盖："
                 + ", ".join(f"{k}={v}" for k, v in
                             (vmeta.get("item_coverage") or {}).items())
                 + f"；EV/EBITDA 无字段恒 UNKNOWN，实际可用权重上限 22 分。</div>")

    # ---- 基本面明细（模块三） ----
    if res is not None and not res.empty and "q_score" in res.columns:
        H.append("<h3>模块三基本面明细（按总分排序前 40）</h3>")
        rows = []
        for code, r in res.head(40).iterrows():
            rows.append([
                str(code), _cell(r.get("name")),
                _num(r.get("q_roe_raw")), _num(r.get("q_roic_raw")),
                _num(r.get("q_revenue_growth_raw")),
                _num(r.get("q_deducted_profit_growth_raw")),
                _num(r.get("q_ocf_match_raw")),
                _num(r.get("q_gross_margin_raw")),
                _num(r.get("q_debt_to_assets_raw")),
                _num(r.get("q_score")), _num(r.get("q_completeness")),
                _cell(r.get("q_veto")), _cell(r.get("q_veto_reasons")),
            ])
        H.append("<div class='tbl-scroll'>")
        H.append(_table(["代码", "名称", "ROE%", "ROIC%", "营收同比%", "扣非同比%",
                          "OCF/净利%", "毛利率%", "资产负债率%", "质量分/30",
                          "完整度", "否决", "否决原因"], rows,
                         num_cols=(2, 3, 4, 5, 6, 7, 8, 9, 10)))
        H.append("</div>")
        H.append(f"<div class='dim'>质量子项覆盖："
                 + ", ".join(f"{k}={v}" for k, v in
                             (qmeta.get("item_coverage") or {}).items())
                 + "</div>")

    # ---- 错杀归因（模块二） ----
    if res is not None and not res.empty and "specific_share" in res.columns:
        H.append("<h3>错杀归因（模块二，前 40）</h3>")
        rows = []
        for code, r in res.head(40).iterrows():
            rows.append([
                str(code), _cell(r.get("name")), _cell(r.get("industry")),
                _pct(r.get("ret20")), _pct(r.get("ret60")),
                _pct(r.get("sys_share")), _pct(r.get("ind_share")),
                _pct(r.get("specific_share")), _num(r.get("resid_z")),
                _num(r.get("data_completeness")),
            ])
        H.append("<div class='tbl-scroll'>")
        H.append(_table(["代码", "名称", "行业", "20日跌幅", "60日跌幅",
                          "系统性占比", "行业性占比", "个股特异占比",
                          "残差z", "数据完整度"], rows,
                         num_cols=(3, 4, 5, 6, 7, 8, 9)))
        H.append("</div>")
        H.append("<div class='dim'>ret{w} 为 (1+pct_chg/100) 逐日累乘（复权口径）；"
                 "归因贡献为日简单收益一阶加总，两者口径不同，不可直接相减等同。</div>")

    # ---- 数据缺口与代理 ----
    H.append("<h2>四、数据缺口、代理口径与未实现模块</h2>")
    H.append("<div class='card'>")
    H.append("<h3>估值模块（模块四）口径说明</h3><ul>")
    for s in (vmeta.get("proxies") or []):
        H.append(f"<li>{_esc(s)}</li>")
    H.append("</ul>")
    H.append("<h3>估值模块已知缺口</h3><ul>")
    for s in (vmeta.get("unknown_notes") or []):
        H.append(f"<li>{_esc(s)}</li>")
    H.append("</ul>")
    H.append("<h3>基本面模块（模块三）口径说明</h3><ul>")
    for s in (qmeta.get("proxies") or []):
        H.append(f"<li>{_esc(s)}</li>")
    H.append("</ul>")
    H.append("<h3>基本面模块已知缺口</h3><ul>")
    for s in (qmeta.get("unknown_notes") or []):
        H.append(f"<li>{_esc(s)}</li>")
    H.append("</ul>")
    H.append("</div>")

    H.append("<div class='card'>")
    H.append("<h3>未实现模块（不参与本次评分）</h3><ul>")
    for s in PENDING_MODULES:
        H.append(f"<li>{_esc(s)}</li>")
    H.append("</ul>")
    H.append("<div class='warn'>以上内容用户未给出指标/阈值/评分口径，"
             "本版本一律不实现、不臆造、不折算进总分。"
             f"当前可解释的分数上限为 {SCORE_CAP} 分（模块三 30 + 模块四 25）。</div>")
    H.append("</div>")

    # ---- as-of ----
    H.append("<h2>五、as-of 防未来保证</h2><div class='card'><ul>")
    for s in list(vmeta.get("asof_guards") or []) + list(qmeta.get("asof_guards") or []):
        H.append(f"<li>{_esc(s)}</li>")
    H.append("</ul></div>")

    H.append(f"<footer>CRR V1.0 · 扫描日 {R.get('scan_date', UNKNOWN)} · "
             f"数据源：本地 Tushare 缓存(SQLite+parquet) + sli/industry DB · "
             f"未引入 TDX · 缺失字段一律 UNKNOWN</footer>")
    H.append("</div></body></html>")
    return "".join(H)


# ----------------------------------------------------------------------------
# Markdown
# ----------------------------------------------------------------------------

def render_markdown(R):
    """渲染 Markdown 摘要。"""
    mkt = R.get("market", {}) or {}
    meta = R.get("meta", {}) or {}
    summary = R.get("summary", {}) or {}
    state = str(mkt.get("state", UNKNOWN))

    L = []
    L.append(f"# CRR 暴跌错杀扫描 {R.get('scan_date', UNKNOWN)}")
    L.append("")
    L.append(f"- 市场状态：**{state} / {_STATE_CN.get(state, state)}**")
    L.append(f"- 门控：{meta.get('gate', UNKNOWN)}")
    L.append(f"- 候选 {summary.get('n_candidates')} / BUY {summary.get('n_buy')} / "
             f"WATCH {summary.get('n_watch')} / NO TRADE {summary.get('n_no_trade')}")
    L.append(f"- 最高总分：{summary.get('max_total_score')} / {SCORE_CAP}")
    L.append("")

    for label, df in (("BUY", R.get("buy")), ("WATCH", R.get("watch"))):
        L.append(f"## {label}")
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            L.append("")
            L.append("无")
            L.append("")
            continue
        L.append("")
        L.append("| 代码 | 名称 | 行业 | 总分 | 置信度 | 质量分 | 估值分 | 判定理由 |")
        L.append("|---|---|---|---|---|---|---|---|")
        for code, r in df.iterrows():
            L.append("| {c} | {n} | {i} | {t} | {cf} | {q} | {v} | {w} |".format(
                c=code, n=_cell(r.get("name")), i=_cell(r.get("industry")),
                t=_num(r.get("total_score")), cf=_num(r.get("confidence")),
                q=_num(r.get("q_score")), v=_num(r.get("v_score")),
                w=_cell(r.get("decision_reason"))))
        L.append("")

    L.append("## 未实现模块（不参与评分）")
    L.append("")
    for s in PENDING_MODULES:
        L.append(f"- {s}")
    L.append("")
    return "\n".join(L)


# ----------------------------------------------------------------------------
# 落盘
# ----------------------------------------------------------------------------

def write_report(R, out_dir=None):
    """写 HTML + Markdown 到 output/crr，返回路径字典。"""
    out_dir = out_dir or REPORT_DIR
    os.makedirs(out_dir, exist_ok=True)
    sd = R.get("scan_date", "unknown")
    html_path = os.path.join(out_dir, f"crr_report_{sd}.html")
    md_path = os.path.join(out_dir, f"crr_report_{sd}.md")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(render_html(R))
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(R))
    return {"html": html_path, "markdown": md_path}
