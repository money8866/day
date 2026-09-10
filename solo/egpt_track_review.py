# -*- coding: utf-8 -*-
"""
EGPT 近一月选股跟踪收益复盘 (默认 20260818 ~ 20260909)

执行口径(与策略纪律一致):
  买入 = 信号日次一交易日开盘承接; 涨停开盘视为无法承接(不追高)
  ① 持有至最新收盘(end日)
  ② T+5 收盘
  ③ ATR止损管理: 沿用信号日"ATR动态止损价", 触发即离场(开盘低于止损按开盘价, 盘中触及按止损价)
分组归因: 次日操作 / 主题状态 / 买点确认 / 回踩天数
新策略回放: v1(复盘建议)=回踩1-2日+热度5~15%或无主题+扣非增速>=50
           v2(用户确认)=回踩中+当日涨幅<5%+扣非增速>=50+缩量比<1.0(仅剔放量回踩)
           v2对照=同v2但缩量比0.6~0.8(原窄区间, 实证偏弱, 留作对照)
           v12合并=v1∩v2全条件AND(回踩中+热度甜区/无主题+涨幅<5%+缩量比<1.0+扣非>=50), 三口径最优
字段兜底: 当日涨幅%仅在20260908起的CSV产出, 此前信号由TDX日线自算(信号日收/上根K线收-1), 口径一致
用法: python egpt_track_review.py [--start 20260818] [--end 20260909]
"""
import os
import sys
import glob
import argparse

import numpy as np
import pandas as pd

from tail_backtest_tdx import parse_tdx_day_file, ts_code_to_tdx_file

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = r"d:\mystock\solo"
REPORT_DIR = os.path.join(BASE_DIR, "report_daily")
INDEX_DAY = os.path.join(r"C:\new_tdx", "vipdoc", "sh", "lday", "sh000001.day")

RENAME = {"代码": "code", "名称": "name", "形态阶段": "stage", "次日操作": "op",
          "回踩买点分": "score", "回踩天数": "pull_days", "买点确认": "buy_confirm",
          "现价": "close_px", "ATR动态止损价": "stop_px", "主题": "theme",
          "主题热度%": "heat", "主题状态": "theme_status", "扣非增速": "dty",
          "当日涨幅%": "pct_chg", "回踩缩量比": "shrink", "首阳日期": "sun_date"}


def op_cat(op):
    s = str(op).strip()
    if s in ("--", "-", ""):
        return "候选池(非信号)"
    if s.startswith("✅"):
        return "✅可买入"
    if s.startswith("⚠️"):
        return "⚠️观察"
    if s.startswith("❌"):
        return "❌不买"
    return "其他"


def status_cat(s):
    s = str(s)
    if s.endswith("(优选)"):
        return "主题优选"
    if "(高热度" in s:
        return "高热度⚠️"
    if s.endswith("(白名单)"):
        return "白名单"
    if "(无主题)" in s:
        return "冷门"
    return "其他/非白名单"


def buy_cat(b):
    s = str(b)
    if s.startswith("买点2"):
        return "买点2"
    if s.startswith("买点1"):
        return "买点1"
    return "未突破"


def limit_ratio(code, name):
    c = str(code)
    if c.startswith(("688", "689", "300", "301")):
        return 0.20
    if "ST" in str(name).upper():
        return 0.05
    return 0.10


def load_signals(start, end):
    frames = []
    for f in sorted(glob.glob(os.path.join(REPORT_DIR, "zhongbao_egpt_timing_*.csv"))):
        d = os.path.basename(f).replace("zhongbao_egpt_timing_", "").replace(".csv", "")
        if start <= d <= end:
            df = pd.read_csv(f, dtype={"代码": str}).rename(columns=RENAME)
            df["signal_date"] = d
            frames.append(df)
    if not frames:
        raise SystemExit(f"[错误] {start}~{end} 无信号CSV")
    return pd.concat(frames, ignore_index=True)


def track(sig, end):
    cal_df = parse_tdx_day_file(INDEX_DAY)
    cal = [d for d in cal_df["trade_date"].tolist() if d <= end]
    cal_pos = {d: i for i, d in enumerate(cal)}

    px_cache = {}
    recs = []
    for r in sig.iterrows():
        row = r[1]
        code = row["code"]
        rec = {"signal_date": row["signal_date"], "code": code, "name": row["name"],
               "op": op_cat(row["op"]), "stage": row["stage"],
               "status": status_cat(row["theme_status"]),
               "buy_confirm": buy_cat(row["buy_confirm"]),
               "score": pd.to_numeric(row["score"], errors="coerce"),
               "pct_chg": pd.to_numeric(row["pct_chg"], errors="coerce"),
               "shrink": pd.to_numeric(row["shrink"], errors="coerce"),
               "pull_days": pd.to_numeric(row["pull_days"], errors="coerce"),
               "heat": pd.to_numeric(row["heat"], errors="coerce"),
               "dty": pd.to_numeric(row["dty"], errors="coerce"),
               "stop_px": pd.to_numeric(row["stop_px"], errors="coerce")}
        if code not in px_cache:
            px_cache[code] = parse_tdx_day_file(ts_code_to_tdx_file(code))
        px = px_cache[code]
        d = row["signal_date"]
        if px is None:
            rec["taken"] = "无行情数据"
            recs.append(rec)
            continue
        bars = px[px["trade_date"] <= end].reset_index(drop=True)
        pos = {dd: i for i, dd in enumerate(bars["trade_date"])}
        if d not in pos:
            rec["taken"] = "信号日停牌"
            recs.append(rec)
            continue
        prev_close = bars.loc[pos[d], "close"]
        i_d = pos[d]
        if i_d >= 1 and pd.isna(rec["pct_chg"]):
            rec["pct_chg"] = round((bars.iloc[i_d]["close"] / bars.iloc[i_d - 1]["close"] - 1) * 100, 2)
        bi = cal_pos.get(d, -1) + 1
        if bi >= len(cal) or cal[bi] > end:
            rec["taken"] = "未到期"
            recs.append(rec)
            continue
        bdate = cal[bi]
        if bdate not in pos:
            found = None
            for k in range(1, 4):
                cand = cal[bi + k] if bi + k < len(cal) else None
                if cand and cand in pos:
                    found = cand
                    break
            if not found:
                rec["taken"] = "停牌无法承接"
                recs.append(rec)
                continue
            bdate = found
            rec["taken"] = "已承接(停牌顺延)"
        else:
            rec["taken"] = "已承接"
        bar = bars.loc[pos[bdate]]
        opn = bar["open"]
        lim = round(prev_close * (1 + limit_ratio(code, row["name"])), 2)
        if opn >= lim - 1e-9:
            rec["taken"] = "涨停开盘未承接"
            recs.append(rec)
            continue
        buy = opn
        rec.update({"buy_date": bdate, "buy_px": buy,
                    "open_gap%": (buy / prev_close - 1) * 100})
        i0 = pos[bdate]
        fwd = bars.iloc[i0:]
        t5i = i0 + 5
        if t5i < len(bars):
            rec["t5%"] = (bars.loc[t5i, "close"] / buy - 1) * 100
        else:
            rec["t5%"] = (bars["close"].iloc[-1] / buy - 1) * 100
        rec["t5_full"] = t5i < len(bars)
        rec["hold%"] = (bars["close"].iloc[-1] / buy - 1) * 100
        stop = rec["stop_px"]
        exit_ret, hit = np.nan, False
        if stop is not None and not (isinstance(stop, float) and np.isnan(stop)):
            for i2 in range(i0, len(bars)):
                b = bars.iloc[i2]
                if b["open"] <= stop:
                    exit_ret, hit = (b["open"] / buy - 1) * 100, True
                    rec["stop_date"] = b["trade_date"]
                    break
                if b["low"] <= stop:
                    exit_ret, hit = (stop / buy - 1) * 100, True
                    rec["stop_date"] = b["trade_date"]
                    break
        rec["stop_hit"] = hit
        rec["stop%"] = exit_ret if hit else rec["hold%"]
        rec["peak%"] = (fwd["high"].max() / buy - 1) * 100
        rec["worst%"] = (fwd["low"].min() / buy - 1) * 100
        recs.append(rec)
    return pd.DataFrame(recs)


def wr_mean(v):
    v = pd.to_numeric(pd.Series(v), errors="coerce").dropna()
    if len(v) == 0:
        return "—"
    return f"{(v > 0).mean() * 100:.0f}% / {v.mean():+.2f}%"


def group_table(t, key, label):
    rows = []
    for k, g in t.groupby(key, observed=True):
        rows.append({label: k, "笔数": len(g),
                     "T+5(胜率/均值)": wr_mean(g["t5%"]),
                     "持有至最新": wr_mean(g["hold%"]),
                     "止损管理": wr_mean(g["stop%"]),
                     "止损触发率": f"{g['stop_hit'].mean() * 100:.0f}%",
                     "峰值均值%": round(g["peak%"].mean(), 2)})
    return pd.DataFrame(rows)


def rule_row(tag, g):
    d = g.sort_values("signal_date").drop_duplicates("code")
    return {"策略/基准": tag, "笔数": len(g), "只数(去重)": len(d),
            "T+5": wr_mean(g["t5%"]), "T+5去重": wr_mean(d["t5%"]),
            "持有至最新": wr_mean(g["hold%"]), "止损管理": wr_mean(g["stop%"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20260818")
    ap.add_argument("--end", default="20260909")
    args = ap.parse_args()
    sig = load_signals(args.start, args.end)
    print(f"窗口 {args.start}~{args.end} 信号总数: {len(sig)}  覆盖股票: {sig['code'].nunique()}")
    t = track(sig, args.end)
    print("\n[承接情况]")
    print(t["taken"].value_counts().to_string())

    ok = t[t["taken"] == "已承接"].copy()
    if ok.empty:
        print("无已承接信号")
        return
    ok["pull_bucket"] = ok["pull_days"].map(
        lambda v: "0日(首阳次日)" if v == 0 else ("1日" if v == 1 else ("2日" if v == 2 else "≥3日")))
    pool = ok[ok["op"] == "候选池(非信号)"]
    act = ok[ok["op"] != "候选池(非信号)"].copy()
    print(f"\n[候选池参照(次日操作=--, 非推送信号) {len(pool)} 笔]  T+5 {wr_mean(pool['t5%'])}  持有 {wr_mean(pool['hold%'])}")

    print(f"\n[推送信号(✅/⚠️/❌) 已承接 {len(act)} 笔 三口径总览]  胜率/均值")
    for col, lab in [("t5%", "T+5"), ("hold%", "持有至最新"), ("stop%", "ATR止损管理")]:
        print(f"  {lab:8s}: {wr_mean(act[col])}")
    print(f"  止损触发率: {act['stop_hit'].mean() * 100:.0f}%   平均开盘溢价: {act['open_gap%'].mean():+.2f}%")

    for key, label in [("op", "次日操作"), ("status", "主题状态"),
                       ("buy_confirm", "买点确认"), ("pull_bucket", "回踩天数")]:
        print(f"\n[分组归因 · {label}]  (T+5 = 胜率/均值)")
        print(group_table(act, key, label).to_string(index=False))

    buy = act[act["op"] == "✅可买入"].copy()
    if len(buy):
        cols = ["signal_date", "code", "name", "status", "buy_confirm", "pull_bucket",
                "buy_px", "open_gap%", "t5%", "hold%", "stop_hit", "stop%", "peak%"]
        show = buy[cols].copy()
        for c in ["open_gap%", "t5%", "hold%", "stop%", "peak%"]:
            show[c] = show[c].round(2)
        show["stop_hit"] = show["stop_hit"].map({True: "触发", False: "未触发"})
        print(f"\n[✅次日可买入 明细 {len(buy)} 笔]")
        print(show.to_string(index=False))

    lo = act[act["open_gap%"] <= 5]
    print(f"\n[稳健口径 · 信号中剔除开盘溢价>5% 后 {len(lo)} 笔]  "
          f"T+5 {wr_mean(lo['t5%'])}  持有 {wr_mean(lo['hold%'])}")

    first = act.sort_values("signal_date").drop_duplicates("code")
    print(f"\n[每股首次信号去重 {len(first)} 笔]  "
          f"T+5 {wr_mean(first['t5%'])}  持有 {wr_mean(first['hold%'])}  "
          f"止损管理 {wr_mean(first['stop%'])}")

    anchor = act[(act["status"] == "主题优选") & (act["buy_confirm"] != "未突破")]
    print(f"\n[锚点对照 · 主题优选×买点确认 {len(anchor)} 笔]")
    print(f"  实测 T+5: {wr_mean(anchor['t5%'])}   "
          f"对照脚本回测 T+5 64.3% / +3.13%(42笔) / 推送口径 72% / +5.2%")

    # ===== 新策略v1回放(复盘建议调整): 回踩1-2日 + 热度5~15%或无主题 + 扣非增速>=50 =====
    pull_ok = act["pull_days"].isin([1, 2])
    heat_ok = ((act["heat"] >= 5) & (act["heat"] < 15)) | act["heat"].isna()
    r1_rule = act[pull_ok & heat_ok].copy()
    v1_rule = r1_rule[r1_rule["dty"] >= 50].copy()

    # ===== 新策略v2回放(用户确认): 回踩中 + 当日涨幅<5% + 扣非>=50 + 缩量比<1.0(剔放量) =====
    pull_stage = act["stage"] == "回踩中"
    pct_ok = act["pct_chg"] < 5
    dty_ok = act["dty"] >= 50
    v2_base = act[pull_stage & pct_ok & dty_ok].copy()
    v2_rule = act[pull_stage & pct_ok & dty_ok & (act["shrink"] < 1.0)].copy()
    v2_mild = act[pull_stage & pct_ok & dty_ok & (act["shrink"] >= 0.6) & (act["shrink"] <= 0.8)].copy()

    # ===== 合并策略v12: v1∩v2 全条件AND (回踩中+热度甜区/无主题+涨幅<5%+缩量比<1.0+扣非>=50) =====
    v12_rule = act[pull_stage & heat_ok & pct_ok & (act["shrink"] < 1.0) & dty_ok].copy()

    cmp_df = pd.DataFrame([
        rule_row("旧策略: 仅✅(分>=60/买点确认)", buy),
        rule_row("v1: 回踩1-2日+热度甜区/无主题+扣非>=50", v1_rule),
        rule_row("v2: 回踩中+涨幅<5%+扣非>=50+缩量比<1.0", v2_rule),
        rule_row("v12合并: v1∩v2(+热度甜区+涨幅<5%+缩量比<1.0)", v12_rule),
        rule_row("v2对照: 缩量比0.6~0.8(原窄区间)", v2_mild),
        rule_row("v2参照: 回踩中+涨幅<5%+扣非>=50(不含缩量比)", v2_base),
        rule_row("基准: 候选池(非推送信号)", pool),
    ])
    print("\n[新旧策略同窗口对比]  (数值 = 胜率/均值)")
    print(cmp_df.to_string(index=False))

    pct_sub = act[pull_stage & dty_ok].copy()
    pct_sub["涨幅区间"] = pd.cut(pct_sub["pct_chg"], [-100, 0, 3, 5, 100],
                              labels=["<0%", "0~3%", "3~5%", ">=5%"]).astype(str).replace("nan", "缺失")
    print("\n[v2分桶 · 当日涨幅]  (回踩中+扣非>=50, 验证涨幅<5%边界)")
    print(group_table(pct_sub, "涨幅区间", "涨幅区间").to_string(index=False))

    shrink_sub = v2_base.copy()
    shrink_sub["缩量比区间"] = pd.cut(shrink_sub["shrink"], [0, 0.6, 0.8, 1.0, 100],
                                 labels=["<0.6", "0.6~0.8", "0.8~1.0", ">=1.0"]).astype(str).replace("nan", "缺失")
    print("\n[v2分桶 · 回踩缩量比]  (回踩中+涨幅<5%+扣非>=50, 验证0.6~0.8边界)")
    print(group_table(shrink_sub, "缩量比区间", "缩量比区间").to_string(index=False))

    if len(v2_rule):
        print(f"  v2构成: op={v2_rule['op'].value_counts().to_dict()}")
        print(f"          status={v2_rule['status'].value_counts().to_dict()}")
        daily = [{"信号日": d, "笔数": len(g),
                  "T+5": wr_mean(g["t5%"]), "持有": wr_mean(g["hold%"])}
                 for d, g in v2_rule.groupby("signal_date")]
        print("\n[新策略v2 · 按信号日]")
        print(pd.DataFrame(daily).to_string(index=False))
        cols = ["signal_date", "code", "name", "op", "status", "buy_confirm",
                "pct_chg", "shrink", "open_gap%", "t5%", "hold%", "stop_hit", "stop%", "peak%"]
        show = pd.concat([v2_rule.nsmallest(3, "t5%")[cols],
                          v2_rule.nlargest(3, "t5%")[cols]]).copy()
        for c2 in ["pct_chg", "shrink", "open_gap%", "t5%", "hold%", "stop%", "peak%"]:
            show[c2] = show[c2].round(2)
        show["stop_hit"] = show["stop_hit"].map({True: "触发", False: "未触发"})
        print("\n[新策略v2 · 最差3笔/最好3笔]")
        print(show.to_string(index=False))

    out = os.path.join(REPORT_DIR, f"egpt_track_review_{args.end}.csv")
    t.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n明细已保存: {out}")


if __name__ == "__main__":
    main()
