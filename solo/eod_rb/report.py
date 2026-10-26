# -*- coding: utf-8 -*-
"""
EOD-RB 报告渲染：接受 run.py 编排后的结果 dict，输出 Markdown 与自包含 HTML（大字号、移动端友好）。
"""
import datetime as dt


# ═══════════════════════════════════════════════════════════════
# 格式化辅助
# ═══════════════════════════════════════════════════════════════
def f_bp(x):
    """净收益（小数）→ bp 字符串"""
    if x is None:
        return "—"
    return f"{x * 10000:+.1f}bp"


def f_pct(x):
    if x is None:
        return "—"
    return f"{x * 100:.1f}%"


def f_num(x, nd=0):
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def _metric_rows(m):
    """核心指标 → [(标签, 值)]"""
    if not m or m.get("signals", 0) == 0:
        return [("信号数", "0"), ("成交数", "0")]
    return [
        ("信号数", f_num(m.get("signals"))),
        ("成交数", f_num(m.get("filled"))),
        ("成交率", f_pct(m.get("fill_rate"))),
        ("平均净收益", f_bp(m.get("mean_net"))),
        ("中位净收益", f_bp(m.get("median_net"))),
        ("胜率", f_pct(m.get("win_rate"))),
        ("平均盈利", f_bp(m.get("avg_win"))),
        ("平均亏损", f_bp(m.get("avg_loss"))),
        ("盈亏比", f_num(m.get("pnl_ratio"), nd=2)),
        ("最大回撤", f_pct(m.get("max_drawdown"))),
    ]


def _kpi(m):
    if not m or m.get("signals", 0) == 0:
        return {"signals": 0, "mean": None, "win": None}
    return {
        "signals": m.get("signals", 0),
        "mean": m.get("mean_net"),
        "win": m.get("win_rate"),
    }


# ═══════════════════════════════════════════════════════════════
# Markdown 渲染
# ═══════════════════════════════════════════════════════════════
def render_markdown(R):
    L = []
    meta = R.get("meta", {})
    L.append("════════════════════════════════════════════════")
    L.append("# EOD-RB 缩量回踩后的尾盘再启动策略 · 研究报告")
    L.append("════════════════════════════════════════════════")
    L.append("")
    L.append(f"- 生成时间：{meta.get('generated_at', '')}")
    L.append(f"- 参数版本：{meta.get('param_version', '')}")
    L.append(f"- 成本假设：单边 {meta.get('cost_one_side_bp', 15)}bp + 分钟内滑点 5bp（净收益已扣成本）")
    L.append(f"- 退出基准：{meta.get('exit_mode', 'NEXT_CLOSE')}")
    L.append("")

    # 0. 结论前置
    L.append("────────────────────────────────────────────")
    L.append("## 0. 最终结论（前置）")
    L.append("────────────────────────────────────────────")
    L.append(f"**结论：{R.get('conclusion', {}).get('verdict', 'UNPROVEN')}**")
    L.append("")
    L.append(R.get("conclusion", {}).get("reason", ""))
    L.append("")
    if R.get("conclusion", {}).get("day_level_note"):
        L.append(R["conclusion"]["day_level_note"])
        L.append("")

    # 1. 数据可用性
    L.append("────────────────────────────────────────────")
    L.append("## 1. 数据可用性检查")
    L.append("────────────────────────────────────────────")
    L.append("| 数据源 | 区间 | 口径 | 说明 |")
    L.append("|---|---|---|---|")
    for d in R.get("data_availability", {}).get("items", []):
        L.append(f"| {d['source']} | {d['range']} | {d.get('basis','')} | {d.get('note','')} |")
    L.append("")

    # 2. 日线层历史回测（B：前期强势+缩量回踩，T+1开盘入场）
    L.append("────────────────────────────────────────────")
    L.append("## 2. 日线层历史回测（L1+L2，无分钟层，仅供参考）")
    L.append("────────────────────────────────────────────")
    seg = R.get("daily_segments", {})
    L.append("| 区段 | 时间窗 | 信号数 | 成交数 | 平均净收益 | 中位净收益 | 胜率 | 最大回撤 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for key in ("train", "val", "oos"):
        s = seg.get(key, {})
        m = s.get("metrics", {})
        L.append(f"| {key} | {s.get('window','')} | {f_num(m.get('signals'))} | {f_num(m.get('filled'))} "
                 f"| {f_bp(m.get('mean_net'))} | {f_bp(m.get('median_net'))} "
                 f"| {f_pct(m.get('win_rate'))} | {f_pct(m.get('max_drawdown'))} |")
    L.append("")

    # 3. 样本外
    L.append("────────────────────────────────────────────")
    L.append("## 3. 样本外测试（时间切分，未随机打乱）")
    L.append("────────────────────────────────────────────")
    oos = seg.get("oos", {})
    L.append(R.get("daily_segments", {}).get("oos_note", ""))
    L.append("")

    # 4. 消融
    L.append("────────────────────────────────────────────")
    L.append("## 4. 四组消融实验")
    L.append("────────────────────────────────────────────")
    L.append("| 实验 | 层级 | 时间窗 | 信号数 | 平均净收益 | 胜率 | 说明 |")
    L.append("|---|---|---|---|---|---|---|")
    for k, v in R.get("ablation", {}).items():
        m = v.get("metrics", {})
        L.append(f"| {k} | {v.get('layers','')} | {v.get('window','')} | {f_num(m.get('signals'))} "
                 f"| {f_bp(m.get('mean_net'))} | {f_pct(m.get('win_rate'))} | {v.get('note','')} |")
    L.append("")

    # 5. 参数稳定性
    L.append("────────────────────────────────────────────")
    L.append("## 5. 参数稳定性扰动")
    L.append("────────────────────────────────────────────")
    st = R.get("stability", {})
    L.append(f"时间段：{st.get('window','')}｜共 {len(st.get('rows', []))} 组扰动")
    L.append("")
    L.append("| 配置 | 信号数 | 平均净收益(bp) | 胜率 | 最大回撤 |")
    L.append("|---|---|---|---|---|")
    for r in st.get("rows", []):
        L.append(f"| {r['label']} | {f_num(r.get('signals'))} | {f_num(r.get('mean_net_bp'),1)} "
                 f"| {f_pct(r.get('win_rate'))} | {f_pct(r.get('max_dd'))} |")
    L.append("")

    # 6. 每日信号示例
    L.append("────────────────────────────────────────────")
    L.append("## 6. 每日信号输出示例")
    L.append("────────────────────────────────────────────")
    de = R.get("daily_example", {})
    L.append(f"示例交易日：{de.get('trade_date','')}（分钟数据最后可用日）｜市场状态：{de.get('regime','')}｜候选 {de.get('n','0')} 条")
    L.append("")
    L.append(de.get("table_md", ""))
    L.append("")
    L.append(de.get("notrade_note", ""))
    L.append("")

    # 7. 运行说明
    L.append("────────────────────────────────────────────")
    L.append("## 7. 运行说明")
    L.append("────────────────────────────────────────────")
    L.append(R.get("run_instructions", ""))
    L.append("")
    L.append("════════════════════════════════════════════════")
    L.append("非投资建议。本报告仅用于策略研究方法验证。")
    L.append("════════════════════════════════════════════════")
    return "\n".join(L)


# ═══════════════════════════════════════════════════════════════
# HTML 渲染（大字号、移动端友好、自包含）
# ═══════════════════════════════════════════════════════════════
_HTML_CSS = """
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body{margin:0;background:#f4f6fb;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;
       color:#1a2233;font-size:22px;line-height:1.8;-webkit-text-size-adjust:100%;}
  .wrap{max-width:760px;margin:0 auto;padding:0 0 40px;}
  .hero{background:linear-gradient(135deg,#1d4ed8,#3b82f6);color:#fff;padding:28px 20px;}
  .hero h1{margin:0;font-size:28px;line-height:1.35;}
  .hero p{margin:8px 0 0;font-size:20px;opacity:.92;}
  .card{background:#fff;margin:14px;padding:20px 18px;border-radius:14px;box-shadow:0 3px 14px rgba(30,50,90,.08);}
  .card h2{margin:0 0 12px;font-size:24px;border-left:6px solid #2563eb;padding-left:10px;}
  .verdict{background:#fff7ed;border:2px solid #f59e0b;color:#9a3412;}
  .kv{font-size:20px;color:#374151;margin:4px 0;}
  .b{font-weight:700;}
  table{width:100%;border-collapse:collapse;font-size:16px;line-height:1.5;word-break:break-word;}
  .tbl-scroll{overflow-x:auto;}
  th,td{border:1px solid #dbe2ee;padding:8px 8px;text-align:left;font-size:16px;}
  th{background:#eef2f9;font-weight:700;}
  .muted{color:#6b7280;}
  .foot{color:#6b7280;font-size:18px;text-align:center;padding:18px;}
</style>
"""


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _table(headers, rows):
    th = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = ""
    for r in rows:
        tds = "".join(f"<td>{_esc(c)}</td>" for c in r)
        body += f"<tr>{tds}</tr>"
    return f'<div class="tbl-scroll"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def render_html(R):
    meta = R.get("meta", {})
    concl = R.get("conclusion", {})
    H = []
    H.append("<!DOCTYPE html><html lang='zh'><head>" + _HTML_CSS + "</head><body><div class='wrap'>")
    H.append(f"<div class='hero'><h1>EOD-RB 缩量回踩后的尾盘再启动</h1>"
             f"<p>量化策略研究报告 · 参数 {_esc(meta.get('param_version',''))}</p></div>")

    # 结论
    H.append(f"<div class='card verdict'><h2>最终结论</h2>"
             f"<div class='b'>{_esc(concl.get('verdict','UNPROVEN'))}</div>"
             f"<p>{_esc(concl.get('reason',''))}</p>")
    if concl.get("day_level_note"):
        H.append(f"<p class='muted'>{_esc(concl['day_level_note'])}</p>")
    H.append("</div>")

    # 数据可用性
    H.append("<div class='card'><h2>1. 数据可用性检查</h2>")
    rows = []
    for d in R.get("data_availability", {}).get("items", []):
        rows.append([d["source"], d["range"], d.get("basis", ""), d.get("note", "")])
    H.append(_table(["数据源", "区间", "口径", "说明"], rows))
    H.append("</div>")

    # 日线层
    seg = R.get("daily_segments", {})
    H.append("<div class='card'><h2>2. 日线层历史回测（L1+L2，仅供参考）</h2>")
    rows = []
    for key in ("train", "val", "oos"):
        s = seg.get(key, {})
        m = s.get("metrics", {})
        rows.append([key, s.get("window", ""), f_num(m.get("signals")), f_num(m.get("filled")),
                     f_bp(m.get("mean_net")), f_bp(m.get("median_net")),
                     f_pct(m.get("win_rate")), f_pct(m.get("max_drawdown"))])
    H.append(_table(["区段", "时间窗", "信号", "成交", "平均净", "中位净", "胜率", "最大回撤"], rows))
    H.append("</div>")

    # 消融
    H.append("<div class='card'><h2>4. 四组消融实验</h2>")
    rows = []
    for k, v in R.get("ablation", {}).items():
        m = v.get("metrics", {})
        rows.append([k, v.get("layers", ""), v.get("window", ""), f_num(m.get("signals")),
                     f_bp(m.get("mean_net")), f_pct(m.get("win_rate")), v.get("note", "")])
    H.append(_table(["实验", "层级", "时间窗", "信号", "平均净", "胜率", "说明"], rows))
    H.append("</div>")

    # 稳定性
    st = R.get("stability", {})
    H.append("<div class='card'><h2>5. 参数稳定性扰动</h2>")
    H.append(f"<p class='muted'>时间段：{_esc(st.get('window',''))}｜{len(st.get('rows',[]))} 组扰动</p>")
    rows = [[r["label"], f_num(r.get("signals")), f_num(r.get("mean_net_bp"), 1),
             f_pct(r.get("win_rate")), f_pct(r.get("max_dd"))] for r in st.get("rows", [])]
    H.append(_table(["配置", "信号数", "平均净(bp)", "胜率", "最大回撤"], rows))
    H.append("</div>")

    # 每日示例
    de = R.get("daily_example", {})
    H.append("<div class='card'><h2>6. 每日信号输出示例</h2>")
    H.append(f"<p class='kv'>示例交易日：<b>{_esc(de.get('trade_date',''))}</b>｜市场状态：{_esc(de.get('regime',''))}｜候选 {_esc(de.get('n','0'))} 条</p>")
    H.append(_esc(de.get("table_md", "")).replace("\n", "<br>"))
    H.append("</div>")

    # 运行说明
    H.append("<div class='card'><h2>7. 运行说明</h2>")
    H.append(f"<pre style='white-space:pre-wrap;font-size:18px;font-family:inherit;'>{_esc(R.get('run_instructions',''))}</pre>")
    H.append("</div>")

    H.append("<div class='foot'>非投资建议 · 本报告仅用于策略研究方法验证</div>")
    H.append("</div></body></html>")
    return "\n".join(H)