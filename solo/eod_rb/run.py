# -*- coding: utf-8 -*-
"""
EOD-RB 一键编排：数据可用性 → 日线回测 → 消融 → 稳定性 → 每日示例 → 结论 → 报告。

用法：
  python -X utf8 eod_rb/run.py

产出（output/eod_rb/）：
  report.md / report.html / results.json
"""
import os
import json
import time
import datetime as dt

from .config import EOD_RB_CONFIG as CFG, REPORT_DIR
from .config import MINUTE_START, MINUTE_END
from . import backtest as B
from . import ablation as AB
from . import stability as ST
from . import daily as DL
from . import report as RP


# ═══════════════════════════════════════════════════════════════
# 数据可用性探查
# ═══════════════════════════════════════════════════════════════
def probe_data_availability():
    import sqlite3
    from .config import CACHE_DB_PATH
    conn = sqlite3.connect(CACHE_DB_PATH, timeout=10.0)
    items = []

    try:
        r = conn.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT ts_code) FROM daily_cache").fetchone()
        items.append({"source": "SQLite daily_cache 日线（前复权）", "range": f"{r[0]} ~ {r[1]}",
                      "basis": "前复权", "note": f"约 {r[2]} 只股票"})
    except Exception as e:
        items.append({"source": "SQLite daily_cache", "range": "—", "basis": "", "note": f"读取失败 {e}"})

    try:
        r = conn.execute("SELECT MIN(trade_date), MAX(trade_date) FROM index_daily_cache WHERE ts_code='000001.SH'").fetchone()
        items.append({"source": "index_daily_cache 交易日历", "range": f"{r[0]} ~ {r[1]}", "basis": "—", "note": "用于交易日切分"})
    except Exception as e:
        items.append({"source": "index_daily_cache", "range": "—", "basis": "", "note": f"读取失败 {e}"})
    conn.close()

    # 分钟线样本核查（抽样若干只）
    m_range = None
    try:
        from . import minute as M
        import bts.data as bd
        codes = [c for c in bd.get_stock_pool(exclude_st=True)["ts_code"].head(30)]
        lo, hi = None, None
        n_ok = 0
        for c in codes:
            df = M.load_minute(c)
            if df is not None and not df.empty:
                dmin = df["day"].min()
                dmax = df["day"].max()
                lo = dmin if (lo is None or dmin < lo) else lo
                hi = dmax if (hi is None or dmax > hi) else hi
                n_ok += 1
        if lo and hi:
            m_range = f"{lo} ~ {hi}"
        items.append({"source": "通达信 .lc5 5分钟线（不复权）", "range": m_range or "—",
                      "basis": "不复权", "note": f"抽样 {n_ok}/{len(codes)} 只有分钟数据（约60个交易日）"})
    except Exception as e:
        items.append({"source": "通达信 .lc5 5分钟线", "range": "—", "basis": "", "note": f"读取失败 {e}"})

    items.append({"source": "Tushare stk_mins", "range": "可用", "basis": "限频 1次/分钟",
                  "note": "无法用于批量分钟回测"})

    return {"items": items, "minute_start": MINUTE_START, "minute_end": MINUTE_END}


# ═══════════════════════════════════════════════════════════════
# 运行说明
# ═══════════════════════════════════════════════════════════════
RUN_INSTRUCTIONS = (
    "1) 依赖：pandas / numpy / mootdx；复用 bts 数据层（bts.data）与本地通达信数据。\n"
    "2) 一键全量回测 + 报告：python -X utf8 -m eod_rb.run\n"
    "3) 每日信号（当日分钟数据到位后）：\n"
    "     from eod_rb.daily import generate_daily_signals, build_table\n"
    "     sigs, regime = generate_daily_signals('20260522', EOD_RB_CONFIG)\n"
    "     print(build_table(sigs))\n"
    "4) 消融实验：from eod_rb.ablation import run_ablation\n"
    "5) 参数稳定性：from eod_rb.stability import run_stability\n"
    "6) 全部阈值集中在 eod_rb/config.py 的 EOD_RB_CONFIG，禁止散落硬编码。\n"
    "7) 成本：单边 15bp + 分钟内滑点 5bp；净收益已扣佣金/印花税/过户费/滑点。\n"
    "8) 每份 results.json 记录数据日期、信号生成时间、参数版本、成交假设与退出基准。"
)


# ═══════════════════════════════════════════════════════════════
# 结论
# ═══════════════════════════════════════════════════════════════
def build_conclusion(daily_segments):
    oos = daily_segments.get("oos", {}).get("metrics", {})
    n_oos = oos.get("signals", 0)
    mean_oos = oos.get("mean_net")
    ms = "—" if mean_oos is None else f"{mean_oos * 10000:+.1f}bp"

    verdict = "UNPROVEN"
    reason = (
        "第三层「尾盘再启动」依赖分钟级真实行情（禁止用日线伪造尾盘信号）。本机通达信 5 分钟线（.lc5）"
        f"实测仅覆盖约 60 个交易日（{MINUTE_START} ~ {MINUTE_END}），无法满足统计有效的样本量，"
        "更无法做有意义的样本外验证。按策略规范：分钟级历史数据不足时必须输出 UNPROVEN。"
        "因此本策略当前既不能被判定为 PASS，也不能被判定为 FAIL，结论为 UNPROVEN（证据不足，待更长分钟数据后复测）。"
    )
    day_level_note = (
        "作为参考（不构成尾盘策略验证）：日线层 L1+L2（前期强势 + 缩量回踩，T+1 开盘入场）"
        f"在 2025-01~2026-09 三段长历史中样本外产生 {n_oos} 个信号、平均净收益 {ms}。"
        "该结果仅用于说明「缩量回踩」这一日线结构本身是否具有统计意义，不能替代 EOD-RB 的尾盘择时效果。"
    )
    return {"verdict": verdict, "reason": reason, "day_level_note": day_level_note}


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════
def main():
    os.makedirs(REPORT_DIR, exist_ok=True)
    t0 = time.time()
    print("[1/5] 数据可用性探查 ...", flush=True)
    da = probe_data_availability()
    for it in da["items"]:
        print("     ", it["source"], "->", it["range"], flush=True)

    split = CFG["split"]
    exit_mode = CFG["exit"]["mode"]

    print("[2/5] 消融实验（A/B/C/D 分钟窗口 + A/B 日线三段）...", flush=True)
    ablation = AB.run_ablation(CFG, exit_mode=exit_mode)

    print("[3/5] 参数稳定性扰动（日线层 B，训练段）...", flush=True)
    stab_rows = ST.run_stability(CFG, window=(split["train_start"], split["train_end"]),
                                 exit_mode=exit_mode)

    print("[4/5] 每日信号输出示例 ...", flush=True)
    example_date = da.get("minute_end") or "20260522"
    sigs, regime = DL.generate_daily_signals(example_date, CFG, max_rows=30)
    table_md = DL.build_table(sigs) if sigs else "（无候选：当日无 BUY/WATCH 信号）"
    notrade_note = "" if sigs else "当日无尾盘信号满足确认条件 → NO TRADE（信号不足时如实输出）。"

    print("[5/5] 生成结论与报告 ...", flush=True)

    # 组装 daily_segments（日线层 B = trend+pullback）
    def _win(seg):
        return f"{split[seg+'_start']} ~ {split[seg+'_end']}"
    daily_segments = {
        "train": {"window": _win("train"), "metrics": ablation["B_train"]["metrics"], "n": ablation["B_train"]["n"]},
        "val": {"window": _win("val"), "metrics": ablation["B_val"]["metrics"], "n": ablation["B_val"]["n"]},
        "oos": {"window": _win("oos"), "metrics": ablation["B_oos"]["metrics"], "n": ablation["B_oos"]["n"]},
        "oos_note": "样本外区间（OOS）为 20260501~20260930，未参与任何参数选择；时间切分，未随机打乱。",
    }
    for k, v in ablation.items():
        layers = {"A": "L1 前期强势", "B": "L1+L2 缩量回踩", "C": "L1+L2+L3 尾盘", "D": "L1+L2+L3+L4 板块"}
        prefix = k.split("_")[0]
        v["layers"] = layers.get(prefix, prefix)
        if k.endswith("_minute"):
            v["window"] = f"{MINUTE_START} ~ {MINUTE_END}"
        else:
            seg = k.split("_")[1]
            v["window"] = _win(seg)

    conclusion = build_conclusion(daily_segments)

    R = {
        "meta": {
            "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "param_version": CFG["param_version"],
            "cost_one_side_bp": CFG["cost"]["one_side_bp"],
            "slippage_bp": CFG["buy"]["slippage_bp"],
            "exit_mode": exit_mode,
            "min_total_mv_yi": CFG["universe"]["min_total_mv_yi"],
            "min_amount_yi": CFG["universe"]["min_amount_yi"],
        },
        "data_availability": da,
        "daily_segments": daily_segments,
        "ablation": ablation,
        "stability": {"window": f"{split['train_start']} ~ {split['train_end']}", "rows": stab_rows},
        "daily_example": {"trade_date": example_date, "regime": regime, "n": len(sigs or []),
                          "table_md": table_md, "notrade_note": notrade_note},
        "conclusion": conclusion,
        "run_instructions": RUN_INSTRUCTIONS,
    }

    md = RP.render_markdown(R)
    html = RP.render_html(R)

    md_path = os.path.join(REPORT_DIR, "report.md")
    html_path = os.path.join(REPORT_DIR, "report.html")
    json_path = os.path.join(REPORT_DIR, "results.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=2, default=str)

    cost = time.time() - t0
    print(f"\n完成，耗时 {cost:.0f}s", flush=True)
    print(f"结论：{conclusion['verdict']}", flush=True)
    print(f"Markdown: {md_path}", flush=True)
    print(f"HTML:     {html_path}", flush=True)
    print(f"JSON:     {json_path}", flush=True)
    return R


if __name__ == "__main__":
    main()