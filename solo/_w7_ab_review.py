# -*- coding: utf-8 -*-
"""W7 新口径（类型A/类型B）统一复查工具。

用途：跟踪库样本积累到一定量后，一次性复盘新口径信号的真实表现。
  - 数据源：picks_db/stock_picks.db  strategy_id='w7_ab'（与旧口径 w7_hvt 隔离）
  - 收益口径：base_close = 选股日收盘价（stock_pick_db 全局口径），T+N = 选股日之后第 N 个交易日收盘
  - 超额口径：相对上证指数（000001.SH）同期收益，逐笔对齐交易日

分组维度：
  1) 总体
  2) 类型A（T0_PULLBACK_HOLD）/ 类型B（T0_REBREAK）
  3) 市场环境（BULL / RECOVERY / RANGE / BEAR）—— 类型A 在 BEAR 下被质量门阻断
  4) 执行状态（w7_status）

用法：
  python _w7_ab_review.py                # 输出到控制台
  python _w7_ab_review.py --md           # 同时写 report_daily/w7_ab_review_YYYYMMDD.md
  python _w7_ab_review.py --min-n 30     # 样本量不足时给出醒目提示（默认 30）
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import date

SOLO = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(SOLO, "picks_db", "stock_picks.db")
CACHE_DB = r"D:\mystock\cache_daily\stock_data.db"
REPORT_DIR = os.path.join(SOLO, "report_daily")
STRATEGY_ID = "w7_ab"
INDEX_CODE = "000001.SH"
HORIZONS = (1, 3, 5, 10, 20)
TYPE_CN = {"T0_PULLBACK_HOLD": "类型A-缩量回踩止跌低吸",
           "T0_REBREAK": "类型B-回踩后突破T0收盘"}


def load_rows():
    """读 w7_ab 的落库记录 + 跟踪结果 + indicators 结构字段。"""
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        rows = con.execute("""
            SELECT p.pick_date, p.ts_code, p.stock_name, p.signal, p.close, p.score,
                   p.stop_price, p.reason, p.indicators,
                   t.base_close, t.last_date, t.last_close, t.last_date,
                   t.ret_1d, t.ret_3d, t.ret_5d, t.ret_10d, t.ret_20d,
                   t.max_gain, t.max_drawdown, t.status, t.hit_date
            FROM stock_pick p
            LEFT JOIN pick_tracking t
              ON t.pick_date = p.pick_date AND t.strategy_id = p.strategy_id
             AND t.ts_code = p.ts_code
            WHERE p.strategy_id = ?
            ORDER BY p.pick_date, p.ts_code
        """, (STRATEGY_ID,)).fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        ind = {}
        try:
            ind = json.loads(r[8]) if r[8] else {}
        except (TypeError, ValueError):
            ind = {}
        out.append({
            "pick_date": r[0], "ts_code": r[1], "stock_name": r[2], "signal": r[3],
            "close": r[4], "score": r[5], "stop_price": r[6], "reason": r[7],
            "t0_date": ind.get("t0_date"), "t0_open": ind.get("t0_open"),
            "t0_close": ind.get("t0_close"), "pull_low": ind.get("pull_low"),
            "pull_bars": ind.get("pull_bars"), "pull_vol_ratio": ind.get("pull_vol_ratio"),
            "stall": ind.get("stall"), "break_volr": ind.get("break_volr"),
            "market_regime": ind.get("market_regime") or "UNKNOWN",
            "w7_status": ind.get("w7_status") or "UNKNOWN",
            "w7_fail_line": ind.get("w7_fail_line"), "w7_fail_name": ind.get("w7_fail_name"),
            "base_close": r[9], "last_date": r[11], "last_close": r[12],
            "ret": {h: r[13 + i] for i, h in enumerate(HORIZONS)},
            "max_gain": r[18], "max_drawdown": r[19], "status": r[20], "hit_date": r[21],
        })
    return out


def load_index():
    """上证指数收盘序列（升序）：{trade_date: close}。"""
    if not os.path.exists(CACHE_DB):
        return {}
    con = sqlite3.connect(f"file:{CACHE_DB}?mode=ro", uri=True)
    try:
        return {str(d): float(c) for d, c in con.execute(
            "SELECT trade_date, close FROM index_daily_cache "
            "WHERE ts_code=? AND close > 0 ORDER BY trade_date", (INDEX_CODE,))}
    finally:
        con.close()


def index_return(idx, pick_date, n):
    """指数在 pick_date 之后第 n 个交易日的累计收益（%）。"""
    dates = sorted(d for d in idx if d > pick_date)
    if len(dates) < n:
        return None
    base = idx.get(pick_date)
    if not base:
        return None
    return round((idx[dates[n - 1]] / base - 1) * 100, 2)


def stat_block(rows, idx, label, horizon):
    """一组信号的统计：样本量、胜率、平均/中位收益、超额（相对上证）。"""
    vals = [r["ret"].get(horizon) for r in rows]
    valid = [(r, v) for r, v in zip(rows, vals) if v is not None]
    if not valid:
        return {"label": label, "n": 0, "n_total": len(rows), "horizon": horizon}
    rets = [v for _, v in valid]
    ex = []
    for r, v in valid:
        ie = index_return(idx, r["pick_date"], horizon)
        if ie is not None:
            ex.append(round(v - ie, 2))
    wins = sum(1 for v in rets if v > 0)
    out = {
        "label": label, "horizon": horizon, "n": len(valid), "n_total": len(rows),
        "win_rate": round(wins / len(valid) * 100, 1),
        "avg": round(sum(rets) / len(valid), 2),
        "median": round(sorted(rets)[len(rets) // 2], 2),
        "best": round(max(rets), 2), "worst": round(min(rets), 2),
    }
    if ex:
        out["ex_avg"] = round(sum(ex) / len(ex), 2)
        out["ex_win_rate"] = round(sum(1 for e in ex if e > 0) / len(ex) * 100, 1)
    return out


def group_stats(rows, idx, keyfn, label, horizon):
    buckets = {}
    for r in rows:
        buckets.setdefault(keyfn(r), []).append(r)
    return [stat_block(v, idx, f"{label}={k}", horizon) for k, v in sorted(buckets.items())]


def fmt_table(blocks, header_cols=("分组", "样本", "胜率%", "均值%", "中位%", "超额均值%", "超额胜率%")):
    lines = ["| " + " | ".join(header_cols) + " |",
             "| " + " | ".join(["--"] * len(header_cols)) + " |"]
    for b in blocks:
        if b["n"] == 0:
            lines.append(f"| {b['label']} | 0/{b['n_total']} | - | - | - | - | - |")
            continue
        lines.append(
            f"| {b['label']} | {b['n']}/{b['n_total']} | {b['win_rate']} | {b['avg']} | "
            f"{b['median']} | {b.get('ex_avg', '-')} | {b.get('ex_win_rate', '-')} |")
    return "\n".join(lines)


def build_report(rows, idx, min_n):
    L = []
    n_all = len(rows)
    L.append("# W7 新口径统一复查报告（类型A / 类型B）")
    L.append("")
    L.append(f"- 生成日期：{date.today().strftime('%Y-%m-%d')}")
    L.append(f"- 数据源：`{DB}` strategy_id=`{STRATEGY_ID}`")
    L.append(f"- 收益口径：base_close = 选股日收盘价；T+N = 选股日之后第 N 个交易日收盘")
    L.append(f"- 超额口径：相对上证指数({INDEX_CODE})同期收益，逐笔对齐交易日")
    L.append(f"- 信号总数：**{n_all}** 条")
    if n_all < min_n:
        L.append("")
        L.append(f"> ⚠️ **样本量不足（{n_all} < {min_n}）**：结论不具备统计意义，仅作流水记录。")
        L.append("> 建议积累至 30 条以上再据此调整策略参数。")
    L.append("")

    # 一、总体
    L.append("## 一、总体表现")
    L.append("")
    for h in HORIZONS:
        blocks = [stat_block(rows, idx, "全部", h)]
        L.append(f"### T+{h}")
        L.append("")
        L.append(fmt_table(blocks))
        L.append("")

    # 二、分类型
    L.append("## 二、分信号类型")
    L.append("")
    L.append("| 类型 | 说明 |")
    L.append("| -- | -- |")
    L.append("| T0_PULLBACK_HOLD | 类型A-缩量回踩至T0开盘价上方，出现止跌信号 |")
    L.append("| T0_REBREAK | 类型B-回踩后突破T0日收盘价 |")
    L.append("")
    for h in (3, 10):
        blocks = group_stats(rows, idx, lambda r: r["signal"], "类型", h)
        L.append(f"### T+{h}")
        L.append("")
        L.append(fmt_table(blocks))
        L.append("")

    # 三、分市场环境
    L.append("## 三、分市场环境")
    L.append("")
    L.append("> 类型A（低吸）在 BEAR 环境被质量门阻断，仅保留突破确认；环境维度是本策略的关键归因。")
    L.append("")
    for h in (3, 10):
        blocks = group_stats(rows, idx, lambda r: r["market_regime"], "环境", h)
        L.append(f"### T+{h}")
        L.append("")
        L.append(fmt_table(blocks))
        L.append("")

    # 四、分执行状态
    L.append("## 四、分执行状态（w7_status）")
    L.append("")
    for h in (3, 10):
        blocks = group_stats(rows, idx, lambda r: r["w7_status"], "执行", h)
        L.append(f"### T+{h}")
        L.append("")
        L.append(fmt_table(blocks))
        L.append("")

    # 五、明细
    L.append("## 五、信号明细")
    L.append("")
    L.append("| 选股日 | 代码 | 名称 | 类型 | 环境 | 执行 | 收盘 | 失效位 | T+1 | T+3 | T+5 | T+10 | 最大收益 | 最大回撤 | 状态 |")
    L.append("| -- | -- | -- | -- | -- | -- | --: | --: | --: | --: | --: | --: | --: | --: | -- |")
    for r in rows:
        def g(h):
            v = r["ret"].get(h)
            return "-" if v is None else f"{v}"
        L.append(
            f"| {r['pick_date']} | {r['ts_code']} | {r['stock_name']} | "
            f"{TYPE_CN.get(r['signal'], r['signal'])} | {r['market_regime']} | {r['w7_status']} | "
            f"{r['close']} | {r['w7_fail_line'] or '-'} | {g(1)} | {g(3)} | {g(5)} | {g(10)} | "
            f"{r['max_gain'] if r['max_gain'] is not None else '-'} | "
            f"{r['max_drawdown'] if r['max_drawdown'] is not None else '-'} | {r['status']} |")
    L.append("")

    # 六、结论提示
    L.append("## 六、结论与后续动作")
    L.append("")
    if n_all < min_n:
        L.append(f"- 当前样本 {n_all} 条，**不足以否定或肯定策略**；历史标定（300 只样本 / 2024-2026）显示")
        L.append("  类型A T+3 超额 +0.09%、T+10 超额 +0.86%，类型B T+3 超额 +1.99%（但类型B 标定样本仅 29 条）。")
        L.append("- 跟踪库新口径样本的跟踪结果不应与标定结论直接对冲，二者样本来源与口径不同。")
        L.append(f"- 下一轮复查建议在样本量达到 {min_n} 条后进行。")
    else:
        L.append(f"- 样本量已达 {n_all} 条，可结合上表判断新口径是否稳定优于基准。")
        L.append("- 建议同时关注分类型与分环境两维，单看总体均值易被结构因素掩盖。")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true", help="写出 Markdown 报告")
    ap.add_argument("--min-n", type=int, default=30, help="样本量不足提示阈值")
    args = ap.parse_args()

    rows = load_rows()
    idx = load_index()
    if not rows:
        print("[w7_ab_review] w7_ab 无记录，退出")
        return 0
    print(f"[w7_ab_review] 新口径信号 {len(rows)} 条，跨度 "
          f"{rows[0]['pick_date']} ~ {rows[-1]['pick_date']}")
    for h in HORIZONS:
        b = stat_block(rows, idx, "全部", h)
        if b["n"]:
            print(f"  T+{h}: n={b['n']}/{b['n_total']} 胜率 {b['win_rate']}% 均值 {b['avg']}% "
                  f"中位 {b['median']}% 超额均值 {b.get('ex_avg', '-')}%")
    if args.md:
        os.makedirs(REPORT_DIR, exist_ok=True)
        p = os.path.join(REPORT_DIR, f"w7_ab_review_{date.today().strftime('%Y%m%d')}.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(build_report(rows, idx, args.min_n))
        print(f"[w7_ab_review] 报告已写: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
