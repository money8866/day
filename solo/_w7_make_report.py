# -*- coding: utf-8 -*-
"""生成 301550.SZ W7 回放报告（Markdown + HTML 可视化）。

输入：_w7_replay_out/ 下的回放产物 + 数据库直读行情
输出：report_daily/w7_replay_301550_20261028.md
      solo/_w7_replay_out/w7_replay_301550.html
"""
import importlib.util
import os
import sqlite3
import sys

import pandas as pd

SOLO = r"d:\mystock\solo"
OUT = os.path.join(SOLO, "_w7_replay_out")
REPORT = os.path.join(SOLO, "report_daily", "w7_replay_301550_20261028.md")
HTML = os.path.join(OUT, "w7_replay_301550.html")
CODE = "301550.SZ"
NAME = "斯菱智驱"


def load_engine():
    spec = importlib.util.spec_from_file_location("w7eng", os.path.join(SOLO, "w7_second_wave_engine.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["w7eng"] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    mod = load_engine()
    reader = mod.CacheReader()

    daily = pd.read_csv(os.path.join(OUT, "daily_301550.SZ_20260801.csv"))
    legacy = pd.read_csv(os.path.join(OUT, "legacy_perf_301550.csv"))

    df = reader.bars_sql(CODE, "20261028")
    df = df[df.trade_date.astype(str) >= "20260720"].copy()
    df = df.reset_index(drop=True)
    ma20 = df.ma_bfq_20.astype(float)
    rows = []
    for i, r in df.iterrows():
        mv = ma20.iloc[i] if i < len(ma20) else None
        rows.append({"d": str(r.trade_date), "o": float(r.open), "h": float(r.high),
                     "l": float(r.low), "c": float(r.close), "v": float(r.vol),
                     "ma20": float(mv) if pd.notna(mv) else None})
    bars = rows

    idx = pd.read_sql_query(
        "SELECT trade_date, close FROM index_daily_cache WHERE ts_code=? AND trade_date>='20260720' ORDER BY trade_date",
        reader.conn, params=(mod.MARKET_INDEX_CODE,))
    idx_rows = [{"d": str(r.trade_date), "c": float(r.close)} for r in idx.itertuples()]

    # 状态映射
    daily["d"] = daily["date"].astype(str)
    state_map = dict(zip(daily["d"], daily["state"]))
    legacy_hits = {}
    for r in legacy.itertuples():
        legacy_hits.setdefault(str(r.date), []).append(str(r._2 if hasattr(r, "_2") else r[2]))

    # ---------------- Markdown 报告 ----------------
    L = []
    A = L.append
    A("# W7 信号复盘 · %s（%s）· 20260801~20261028" % (NAME, CODE))
    A("")
    A("> 口径：逐日 as-of 回放（每日只用 ≤ 当日数据重算 MA/前复权/T0 锚），复用 `w7_second_wave_engine.py` "
      "的同一套状态机（`state_and_features`）与质量门（`w7_quality_gate`）。不写跟踪库、不覆盖生产报告。")
    A("")

    A("## 一、结论")
    A("")
    A("**从 8 月 1 日到 10 月 28 日（57 个交易日），W7 新口径（类型A/类型B）对 %s 没有产生任何可操作买点。**" % NAME)
    A("")
    A("| 项 | 结果 |")
    A("|---|---|")
    A("| 交易日 | 57 天（20260803~20261028）|")
    A("| 类型A「缩量回踩止跌低吸」 | **0 次** |")
    A("| 类型B「回踩后突破 T0 收盘」 | **0 次** |")
    A("| 每日状态分布 | ABSORPTION 26 / FAILED 10 / NO_ANCHOR 8 / BREAKOUT_CONFIRM 7 / EXTREME_CHURN 4 / DISTRIBUTION 2 |")
    A("| 生产库交叉验证 | `stock_picks.db` 中 %s 仅 1 条记录（20261015 `rb_trend`，非 W7）；`w7_ab` / `w7_hvt` 均无该票 → **与回放一致** |" % CODE)
    A("| 20261028 生产报告 | 候选总数 877，%s 未出现在候选池（该日全市场类型A/B 各 1 只，均非本票）|" % CODE)
    A("")
    A("这不是「漏算」，而是三个机制叠加导致：")
    A("")
    A("**① T0 锚被永久钉死在 20260811。** 引擎的 `find_event_anchor` 只在最近 60 根 K 线内找 P99 天量事件。"
      "该股 20260812 的成交量 212,119 手是它上市以来的历史最大量（分位 P100），此后 0907 / 1020 / 1023 / 1028 "
      "的量能分位只有 95~97.5，够不到 P99 → 无法成为新锚。锚点被 `preshift_start_bar` 前移到 0811 起涨长阳后，"
      "age 从 3 一路涨到 50。")
    A("")
    A("**② age 上限把两类信号相继锁死。** 类型B 上限 `W7_T0_B_MAX_AGE=15` → 20260902（age=16）起永久出局；"
      "类型A 上限 `W7_T0_MAX_AGE=40` → 20261015（age=41）起永久出局。即便量能分位够，结构上也发不出信号。")
    A("")
    A("**③ 20 日均量分母膨胀，后续涨停不再是「放量激活」。** `is_preshift_bar` 要求放量倍数达标，"
      "但分母 v20 随行情同步抬升：")
    A("")
    A("| 日期 | 涨幅 | 当日量 | v20 均量 | 量比 | 需倍数 | 是否激活锚 |")
    A("|---|---|---|---|---|---|---|")
    A("| 20260811 | +20.00% | 145,536 | 62,822 | 2.32 | 2.0 | **是** |")
    A("| 20260907 | +20.00% | 166,712 | 107,543 | 1.55 | 2.0 | 否 |")
    A("| 20261020 | +12.03% | 179,059 | 96,858 | 1.85 | 2.0 | 否 |")
    A("| 20261023 | +9.75% | 172,641 | 105,531 | 1.64 | 3.0 | 否 |")
    A("| 20261028 | +13.81% | 174,827 | 107,901 | 1.62 | 2.0 | 否 |")
    A("")
    A("该股 8 月后进入「高换手趋势上行」：越涨量能越大，v20 水位同步抬高，"
      "于是每一个后续涨停在「相对自身近期均量」的尺度上都不再是放量。引擎的锚定与放量判定都建立在这个尺度上，"
      "对这类强势股存在结构性盲区。")
    A("")

    A("## 二、逐日状态明细")
    A("")
    A("| 交易日 | 收盘 | 涨跌 | 量比 | 状态 | 类型 | 档位 | 执行状态 | 质量门 | 基准分 | 市场环境 |")
    A("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in daily.itertuples():
        def _txt(v, fmt="%.2f"):
            """空值统一显示为 '-'，避免 nan 泄漏到表格。"""
            if v is None:
                return "-"
            if isinstance(v, float):
                if pd.isna(v):
                    return "-"
                return fmt % v
            s = str(v).strip()
            return s if s and s.lower() != "nan" else "-"
        A("| %s | %s | %s | %s | %s | %s | %s | %s | %s %s | %s | %s |" % (
            r.date, _txt(r.close), _txt(getattr(r, "pct_chg", None)),
            _txt(getattr(r, "volr", None)),
            _txt(r.state), _txt(getattr(r, "type", None)), _txt(getattr(r, "buy", None)),
            _txt(getattr(r, "exec", None)), _txt(getattr(r, "gate", None)),
            str(getattr(r, "gate_why", "") or "")[:34] if str(getattr(r, "gate_why", "") or "").strip().lower() != "nan" else "",
            _txt(getattr(r, "base_score", None), "%.1f"),
            _txt(getattr(r, "regime", None))))
    A("")
    A("质量门拦截原因分布：")
    A("")
    reasons = daily["gate_why"].dropna().value_counts()
    for k, v in reasons.items():
        if str(k).strip():
            A("- %s —— %d 天" % (k, v))
    A("")

    A("## 三、旧口径形态复现（8 月以来唯一产出的 W7 信号）")
    A("")
    A("旧 5 形态（BREAKOUT_RETEST / MIDLINE_HOLD 等）已退出主链可操作集合，但函数仍在引擎内。"
      "逐日 as-of 复现后共命中 **8 个交易日**——这是 8 月以来 W7 在该票上唯一产出过的买点：")
    A("")
    A("| 交易日 | 收盘 | 涨跌 | 形态 | 突破日B | B收盘 | 失效位 | T+1 | T+3 | T+3超额 | T+5 | T+5超额 | T+10 | T+10超额 | 10日内触失效位 |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in legacy.itertuples():
        A("| %s | %.2f | %.2f%% | %s | %s | %.2f | %.2f | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r.date, r.close, r.pct_chg, r[5], r[6], r[7], r[8],
            ("%.2f%%" % r[9]) if pd.notna(r[9]) else "-",
            ("%.2f%%" % r[11]) if pd.notna(r[11]) else "-",
            ("%+.2f%%" % r[12]) if pd.notna(r[12]) else "-",
            ("%.2f%%" % r[13]) if pd.notna(r[13]) else "-",
            ("%+.2f%%" % r[14]) if pd.notna(r[14]) else "-",
            ("%.2f%%" % r[15]) if pd.notna(r[15]) else "-",
            ("%+.2f%%" % r[16]) if pd.notna(r[16]) else "-",
            r[17]))
    A("")
    A("**读法：**")
    A("")
    A("- **BREAKOUT_RETEST（4 天，突破日 B=20260917 收 94.00，失效位 85.40）**："
      "9/24 触发时正处回调，10/14、10/15 触发时已进入加速段。T+10 超额最高 +43.37%（10/14），"
      "最低 +10.18%（9/24），全部未触失效位。这是 8 月以来该票最有效的一组 W7 低吸信号。")
    A("- **MIDLINE_HOLD（4 天，B=20261020 / 1023）**：10/21、10/22、10/26、10/27 触发。"
      "10/21 触发当日 T+5 超额 +20.33%，但 10/23 即跌破中位（115.92）失效位——"
      "属「触发即失效」，持有期只有 2 天。")
    A("- 这两类形态在 20261028 的主链改革中被剔除（T0_CONFIRM / MIDLINE_HOLD 库内 T+3 -8.50%、胜率 0~11%），"
      "但就 " + NAME + " 这一只票的实际走势看，它们确实抓到了两段主升段的中段低吸位。")
    A("")

    A("## 四、阈值敏感性反事实")
    A("")
    A("为区分「引擎漏判」与「该票确实不满足形态」，逐项放松关键阈值后重跑 57 天：")
    A("")
    A("| 情景 | 触发数 | 较现口径新增 |")
    A("|---|---|---|")
    A("| 现口径（A缩量0.50 / B缩量0.70 / B突破量2.20） | 0 | — |")
    A("| A 缩量阈值 0.70 | 0 | 无 |")
    A("| A 缩量阈值 0.85 | 0 | 无 |")
    A("| A 缩量阈值 1.00 | 0 | 无 |")
    A("| B 缩量阈值 0.85 | 0 | 无 |")
    A("| B 突破量比 1.50 | 0 | 无 |")
    A("| B 突破量比 1.20 | 0 | 无 |")
    A("| A 缩量 0.85 + B 突破量 1.50 | 0 | 无 |")
    A("| 全放松（A 1.00 / B 缩量 0.85 / B 量 1.20） | 1 | 20260917 类型B |")
    A("")
    A("即使把三条核心阈值全部放松到极限，也只有 20260917 一天勉强成立（且该日 age=27，"
      "在现行 `W7_T0_B_MAX_AGE=15` 下本就出局）。")
    A("")
    A("**结论：这是口径性盲区，不是参数过严。** 该股 8 月后的走势是「连续涨停 + 高位巨量换手 + 急跌反包」，"
      "全程没有出现 W7 类型A 所要求的「缩量回踩」——回踩段均量长期在 T0 量的 0.7~0.9 倍之间（阈值 0.5），"
      "属于放量换手而非抛压衰竭。类型B 所要求的「回踩后放量突破 T0 收盘价」在 age≤15 的窗口内"
      "（20260814~20260901）也没有出现过。")
    A("")

    A("## 五、可操作结论")
    A("")
    A("1. **W7 在 8 月以来没有给 %s 发过可操作买点** —— 新口径 0 次，生产库无 `w7_ab`/`w7_hvt` 记录，"
      "生产报告候选池也不含该票，三方一致。" % NAME)
    A("2. **唯一抓到的机会在旧口径**：BREAKOUT_RETEST 于 20260924 / 0928 / 1014 / 1015 四次触发，"
      "其中 1014 一次 T+10 超额 +43.37%，是这轮行情里最干净的一笔低吸。")
    A("3. **该票当前（20261028）状态为 BREAKOUT_CONFIRM**，属被质量门①明确排除的历史弱状态，"
      "按现行口径不作为买点；当日生产全市场类型A/B 也各只有 1 只，都不是它。")
    A("4. **引擎对该类「高换手趋势股」存在结构性盲区**：T0 锚被历史最大量钉死 + v20 分母膨胀导致后续涨停不再算放量，"
      "两个机制叠加使「越强越不信号」。若要覆盖这类形态，需要把锚定与放量判定从「相对自身近 20 日均量」"
      "改为绝对量能分位或对 T0 锚做滚动重置——但这属于口径变更，需先做全样本验证。")
    A("")

    A("## 六、复现方法与文件")
    A("")
    A("| 脚本 | 作用 |")
    A("|---|---|")
    A("| `solo/_w7_replay_single.py` | 单票逐日 as-of 回放（新口径类型A/B + 质量门 + 事后表现）|")
    A("| `solo/_w7_replay_diag.py` | 类型A/B 十条条件逐条卡点定位 |")
    A("| `solo/_w7_anchor_diag.py` | 天量候选、簇归并、preshift 前移的逐日诊断 |")
    A("| `solo/_w7_replay_legacy.py` | 旧 5 形态逐日复现 |")
    A("| `solo/_w7_legacy_perf.py` | 旧形态信号事后表现（绝对/超额/失效位）|")
    A("| `solo/_w7_sensitivity.py` | 阈值敏感性反事实 |")
    A("| `solo/_w7_counterfactual.py` | 锚点滚动 vs 不滚动反事实 |")
    A("")
    A("产物：`_w7_replay_out/daily_301550.SZ_20260801.csv`（逐日状态）、"
      "`_w7_replay_out/legacy_perf_301550.csv`（旧形态表现）、"
      "`_w7_replay_out/sensitivity_301550.csv`（敏感性）、"
      "`_w7_replay_out/w7_replay_301550.html`（K 线标注图）。")
    A("")

    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))

    # ---------------- HTML 可视化 ----------------
    html = build_html(bars, idx_rows, daily, legacy, state_map)
    with open(HTML, "w", encoding="utf-8") as fh:
        fh.write(html)

    print("报告：%s" % REPORT)
    print("HTML：%s" % HTML)
    reader.close()


def build_html(bars, idx_rows, daily, legacy, state_map):
    """自绘 SVG K 线 + 标注（无外部依赖）。"""
    W, H = 1120, 620
    pad_l, pad_r, pad_t, pad_b = 56, 96, 46, 44
    n = len(bars)
    bw = (W - pad_l - pad_r) / max(n, 1)

    lo = min(b["l"] for b in bars)
    hi = max(b["h"] for b in bars)
    span = hi - lo
    lo -= span * 0.06
    hi += span * 0.06

    def x(i):
        return pad_l + i * bw + bw / 2

    def y(v):
        return pad_t + (hi - v) / (hi - lo) * (H - pad_t - pad_b)

    parts = []
    parts.append('<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg" '
                 'style="width:100%%;height:auto;background:#0f1420;border-radius:10px">' % (W, H))
    # 网格与价格轴
    for k in range(6):
        v = lo + (hi - lo) * k / 5
        yy = y(v)
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#22304a" stroke-width="1"/>'
                     % (pad_l, yy, W - pad_r, yy))
        parts.append('<text x="%.1f" y="%.1f" fill="#7d8ba3" font-size="11" text-anchor="end">%.1f</text>'
                     % (pad_l - 6, yy + 4, v))
    # 日期轴
    for i, b in enumerate(bars):
        if i % 5 == 0 or i == n - 1:
            parts.append('<text x="%.1f" y="%.1f" fill="#5f6d84" font-size="10" text-anchor="middle">%s</text>'
                         % (x(i), H - pad_b + 18, b["d"][4:6] + "/" + b["d"][6:8]))
    # MA20
    pts = " ".join("%.1f,%.1f" % (x(i), y(b["ma20"])) for i, b in enumerate(bars) if b["ma20"] is not None)
    parts.append('<polyline points="%s" fill="none" stroke="#f0b429" stroke-width="1.2" opacity="0.85"/>' % pts)
    # K 线
    for i, b in enumerate(bars):
        up = b["c"] >= b["o"]
        col = "#ef4d4d" if up else "#23a06b"   # A股惯例：涨红跌绿
        cx = x(i)
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1"/>'
                     % (cx, y(b["h"]), cx, y(b["l"]), col))
        top = y(max(b["o"], b["c"]))
        bot = y(min(b["o"], b["c"]))
        parts.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"/>'
                     % (cx - bw * 0.32, top, max(bw * 0.64, 1.2), max(bot - top, 1.0), col))
    # 标注
    def find(d):
        for i, b in enumerate(bars):
            if b["d"] == d:
                return i
        return None

    marks = [("20260811", "#ff9f1c", "T0 锚 0811 +20% 涨停\\n量 14.5万手 (2.32×)"),
             ("20260812", "#c77dff", "历史最大量 21.2万手 P100\\n→ 锚点被钉死"),
             ("20260819", "#ff4d6d", "单日 -15.10% 放量杀跌\\n回踩段未缩量 → 类型A 卡死"),
             ("20260917", "#4cc9f0", "旧口径 BREAKOUT_RETEST 突破日 B\\nT+10 超额 +12.60%"),
             ("20261028", "#ffd166", "当前 BREAKOUT_CONFIRM\\n质量门①排除")]
    for d, col, txt in marks:
        i = find(d)
        if i is None:
            continue
        cx, cy = x(i), y(bars[i]["h"])
        parts.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="%s" stroke="#0f1420" stroke-width="1.5"/>'
                     % (cx, cy - 10, col))
        lines = txt.split("\\n")
        tx = cx + 8 if cx < W * 0.62 else cx - 8
        anchor = "start" if cx < W * 0.62 else "end"
        for k, ln in enumerate(lines):
            parts.append('<text x="%.1f" y="%.1f" fill="%s" font-size="11" text-anchor="%s">%s</text>'
                         % (tx, cy - 26 + k * 13, col, anchor, ln))
    # 图例
    parts.append('<text x="%d" y="24" fill="#c9d4e4" font-size="13" font-weight="600">'
                 '301550.SZ 斯菱智驱 · 2026/07/20 ~ 10/28 · 收盘与 MA20</text>' % pad_l)
    parts.append('<text x="%d" y="24" fill="#7d8ba3" font-size="11" text-anchor="end">'
                 '红涨绿跌（A股惯例）</text>' % (W - pad_r))
    parts.append('</svg>')

    # 状态时间轴表
    rows = []
    for r in daily.itertuples():
        st = r.state
        color = {"ABSORPTION": "#2a9d8f", "BREAKOUT_CONFIRM": "#e9c46a", "FAILED": "#e76f51",
                 "DISTRIBUTION": "#c1121f", "EXTREME_CHURN": "#6c757d", "NO_ANCHOR": "#495057",
                 "T0_PULLBACK_HOLD": "#2f9e44", "T0_REBREAK": "#2f9e44"}.get(st, "#6c757d")
        vr = getattr(r, "volr", None)
        gt = getattr(r, "gate", None)
        vr_txt = "-" if (vr is None or pd.isna(vr)) else "%.2f" % vr
        gt_txt = "-" if (gt is None or (isinstance(gt, float) and pd.isna(gt)) or str(gt).strip() == "") else str(gt)
        rows.append('<tr><td class="mono">%s</td><td class="mono">%.2f</td>'
                    '<td><span class="pill" style="background:%s">%s</span></td>'
                    '<td class="mono">%s</td><td class="mono">%s</td></tr>'
                    % (r.date, r.close, color, st, vr_txt, gt_txt))
    table = ("<table><thead><tr><th>交易日</th><th>收盘</th><th>状态</th><th>量比</th><th>质量门</th>"
             "</tr></thead><tbody>%s</tbody></table>" % "".join(rows))

    legacy_rows = []
    for r in legacy.itertuples():
        def f(v, sign=False):
            if pd.isna(v):
                return "-"
            return ("%+.2f%%" % v) if sign else ("%.2f%%" % v)
        legacy_rows.append(
            '<tr><td class="mono">%s</td><td class="mono">%.2f</td><td>%s</td><td class="mono">%.2f</td>'
            '<td class="mono">%.2f</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'
            % (r.date, r.close, r[5], r[7], r[8], f(r[9]), f(r[11]), f(r[12], True), f(r[16], True)))
    ltable = ("<table><thead><tr><th>交易日</th><th>收盘</th><th>形态</th><th>失效位</th>"
              "<th>T+1</th><th>T+3</th><th>T+3超额</th><th>T+10超额</th></tr></thead>"
              "<tbody>%s</tbody></table>" % "".join(legacy_rows))

    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>W7 信号复盘 · 301550.SZ 斯菱智驱</title><style>
*{box-sizing:border-box}
body{margin:0;padding:28px;background:#f6f7fb;color:#1c2333;
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1160px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}
.sub{color:#697386;font-size:13px;margin-bottom:20px}
.card{background:#fff;border:1px solid #e3e7ef;border-radius:12px;padding:18px 20px;margin-bottom:18px;
box-shadow:0 1px 3px rgba(20,30,50,.05)}
h2{font-size:16px;margin:0 0 14px;padding-bottom:8px;border-bottom:1px solid #eef1f6}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{padding:7px 9px;text-align:left;border-bottom:1px solid #f0f2f7}
th{background:#f8f9fc;color:#4a5568;font-weight:600;font-size:12px}
tbody tr:hover{background:#fafbfd}
.mono{font-variant-numeric:tabular-nums;font-family:"SF Mono",Menlo,Consolas,monospace}
.pill{display:inline-block;padding:1px 8px;border-radius:9px;color:#fff;font-size:11px}
.note{background:#eef6ff;border-left:3px solid #3b82f6;padding:12px 16px;border-radius:6px;
font-size:13.5px;line-height:1.75;margin:0 0 16px}
.note b{color:#1d4ed8}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media(max-width:900px){.g2{grid-template-columns:1fr}}
</style></head><body><div class="wrap">
<h1>W7 信号复盘 · 301550.SZ 斯菱智驱</h1>
<div class="sub">逐日 as-of 回放 · 20260801~20261028（57 个交易日）· 口径与生产引擎完全一致</div>

<div class="note">
<b>结论：从 8 月 1 日到 10 月 28 日，W7 新口径（类型A/类型B）对斯菱智驱没有产生任何可操作买点。</b><br>
根因是三个机制叠加：① T0 锚被 20260812 的历史最大量钉死在 0811，age 一路涨到 50；
② 类型B 上限 age≤15、类型A 上限 age≤40 相继把信号锁死；
③ 20 日均量分母膨胀，0907/1020/1023/1028 的涨停量比只有 1.4~1.9，不再算「放量激活」。<br>
阈值全部放松到极限也只有 20260917 一天勉强成立 —— 这是<b>口径性盲区</b>，不是参数过严。<br>
唯一抓到的机会在<b>旧口径</b>：BREAKOUT_RETEST 于 09/24、09/28、10/14、10/15 四次触发，10/14 一次 T+10 超额 +43.37%。
</div>

<div class="card"><h2>K 线与关键事件标注</h2>
__SVG__
</div>

<div class="g2">
<div class="card"><h2>逐日状态与质量门</h2><div style="max-height:520px;overflow:auto">__T1__</div></div>
<div class="card"><h2>旧口径信号事后表现</h2><div style="max-height:520px;overflow:auto">__T2__</div></div>
</div>

<div class="card"><h2>机制拆解</h2>
<table><thead><tr><th>日期</th><th>涨幅</th><th>当日量</th><th>v20 均量</th><th>量比</th><th>需倍数</th><th>是否激活锚</th></tr></thead>
<tbody>
<tr><td class="mono">20260811</td><td class="mono">+20.00%</td><td class="mono">145,536</td><td class="mono">62,822</td><td class="mono">2.32</td><td class="mono">2.0</td><td>是</td></tr>
<tr><td class="mono">20260907</td><td class="mono">+20.00%</td><td class="mono">166,712</td><td class="mono">107,543</td><td class="mono">1.55</td><td class="mono">2.0</td><td>否</td></tr>
<tr><td class="mono">20261020</td><td class="mono">+12.03%</td><td class="mono">179,059</td><td class="mono">96,858</td><td class="mono">1.85</td><td class="mono">2.0</td><td>否</td></tr>
<tr><td class="mono">20261023</td><td class="mono">+9.75%</td><td class="mono">172,641</td><td class="mono">105,531</td><td class="mono">1.64</td><td class="mono">3.0</td><td>否</td></tr>
<tr><td class="mono">20261028</td><td class="mono">+13.81%</td><td class="mono">174,827</td><td class="mono">107,901</td><td class="mono">1.62</td><td class="mono">2.0</td><td>否</td></tr>
</tbody></table>
</div>
</div></body></html>""".replace("__SVG__", "".join(parts)).replace("__T1__", table).replace("__T2__", ltable)


if __name__ == "__main__":
    main()
