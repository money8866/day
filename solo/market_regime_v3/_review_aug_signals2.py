# -*- coding: utf-8 -*-
"""8月信号回顾补充：去重视角 + 除权票识别 + 双触发统计。"""
import json
import glob
import os
import sys
import statistics as st

sys.path.insert(0, r"D:\mystock\solo")
import stock_cache as sc

REPORT_DIR = r"D:\mystock\solo\report_daily"
END_DATE = "20260904"
V8_BUY = {"S", "A", "B"}
V9_BUY = {"PRIMARY_BUY", "PRIMARY_RETEST_BUY", "NEAR_TRIGGER"}


def load_events():
    events = []
    for fp in sorted(glob.glob(os.path.join(REPORT_DIR, "right_confirm_buy_202608*.json"))):
        data = json.load(open(fp, encoding="utf-8"))
        td = data.get("trade_date", "")
        for s in data.get("signals", []):
            lvl = (s.get("signal_level") or "?")[:1]
            events.append({"engine": "V8", "date": td, "code": s.get("ts_code", ""),
                           "name": s.get("name", ""), "tag": lvl, "buy": lvl in V8_BUY,
                           "stop": s.get("stop_loss") or 0.0, "tp1": s.get("tp1") or 0.0})
    for fp in sorted(glob.glob(os.path.join(REPORT_DIR, "breakout_timing_202608*.json"))):
        data = json.load(open(fp, encoding="utf-8"))
        td = data.get("trade_date", "")
        for s in data.get("signals", []):
            state = s.get("state", "")
            events.append({"engine": "V9", "date": td, "code": s.get("ts_code", ""),
                           "name": s.get("name", ""), "tag": state, "buy": state in V9_BUY,
                           "stop": s.get("stop_loss") or 0.0, "tp1": s.get("tp1") or 0.0,
                           "anchor_check": s.get("current_price") or 0.0})
    return events


def query(code, start, end):
    cols = ["high_qfq", "low_qfq", "close_qfq", "adj_factor"]
    df = sc.fetch_hist_range(start, end, ts_codes=[code], cols=cols)
    if df is None or df.empty:
        return []
    df = df.sort_values("trade_date").reset_index(drop=True)
    return [tuple(r) for r in df[["trade_date"] + cols].itertuples(index=False, name=None)]


def eval_event(ev):
    rows = query(ev["code"], ev["date"], END_DATE)
    if not rows or rows[0][0] != ev["date"]:
        return None
    fwd = rows[1:]
    if not fwd:
        return None
    factor_d = rows[0][4]
    a_entry = fwd[0][1] * fwd[0][4]
    if not a_entry:
        return None
    end_ret = fwd[-1][3] * fwd[-1][4] / a_entry - 1
    mfe = max(r[1] * r[4] for r in fwd) / a_entry - 1
    mae = min(r[2] * r[4] for r in fwd) / a_entry - 1
    stop_hit = tp_hit = False
    if ev["stop"] and factor_d:
        stop_hit = any(r[2] * r[4] <= ev["stop"] * factor_d for r in fwd)
    if ev["tp1"] and factor_d:
        tp_hit = any(r[1] * r[4] >= ev["tp1"] * factor_d for r in fwd)
    return {"ev": ev, "end": end_ret, "mfe": mfe, "mae": mae,
            "stop": stop_hit, "tp": tp_hit,
            "anchor_dev": abs((rows[0][3] / ev["anchor_check"] - 1) * 100)
            if ev.get("anchor_check") else None}


def agg(rows, label):
    if not rows:
        print(f"  {label}: n=0")
        return
    ends = [r["end"] for r in rows]
    win = sum(1 for e in ends if e > 0)
    both = sum(1 for r in rows if r["stop"] and r["tp"])
    print(f"  {label}: n={len(rows)}  均值{st.mean(ends)*100:+.1f}%  中位{st.median(ends)*100:+.1f}%  "
          f"胜率{win}/{len(ends)}={win/len(ends)*100:.0f}%  "
          f"止损率{sum(1 for r in rows if r['stop'])/len(rows)*100:.0f}%  "
          f"止盈率{sum(1 for r in rows if r['tp'])/len(rows)*100:.0f}%  双触发{both}")


def main():
    events = load_events()
    results = [r for r in (eval_event(e) for e in events) if r]

    # 除权嫌疑票
    print("═══ qfq锚点偏差>2%（疑似除权/数据缺口，影响止损止盈对比）═══")
    for r in results:
        if r["anchor_dev"] is not None and r["anchor_dev"] > 2:
            ev = r["ev"]
            print(f"  {ev['date']} {ev['engine']} {ev['code']} {ev['name']}  偏差{r['anchor_dev']:.1f}%")

    # 去重：每只票取8月首次买信号（V8 B级也算首次观察口径分开）
    print("\n═══ 去重视角（每票仅保留8月首个该级别事件）═══")
    for eng, tags in [("V8", ["S", "A", "B"]), ("V8", ["C"]), ("V9", None)]:
        seen = set()
        firsts = []
        for r in sorted(results, key=lambda x: x["ev"]["date"]):
            ev = r["ev"]
            if ev["engine"] != eng:
                continue
            if tags is not None and ev["tag"] not in tags:
                continue
            if tags is None and ev["buy"]:
                key = (eng, ev["code"])
            else:
                key = (eng, ev["tag"], ev["code"]) if tags is None else (eng, ev["code"])
                if tags is None and not ev["buy"]:
                    key = (eng, "OBS", ev["code"])
            if key in seen:
                continue
            seen.add(key)
            firsts.append(r)
        label = f"{eng} {'/'.join(tags) if tags else ('买状态' if eng=='V9' else '非买状态')}"
        agg(firsts, label + " 首信号")

    # V8 B级：按首个信号日的分数分层
    print("\n═══ V8 B级首信号 按分数分层 ═══")
    seen = set()
    b_first = []
    for r in sorted(results, key=lambda x: x["ev"]["date"]):
        ev = r["ev"]
        if ev["engine"] == "V8" and ev["tag"] == "B" and ("V8", ev["code"]) not in seen:
            seen.add(("V8", ev["code"]))
            b_first.append(r)
    for lo, hi in [(70, 74), (74, 78), (78, 101)]:
        sub = [r for r in b_first if lo <= (r["ev"].get("score") or 0) < hi]
        agg(sub, f"B分数[{lo},{hi})")

    # 止损率最高的B级样本列举
    print("\n═══ V8 B级首信号中 MAE 最差 8 个 ═══")
    worst = sorted(b_first, key=lambda r: r["mae"])[:8]
    for r in worst:
        ev = r["ev"]
        print(f"  {ev['date']} {ev['code']} {ev['name']:<8} 期末{r['end']*100:+.1f}%  "
              f"MAE{r['mae']*100:+.1f}%  MFE{r['mfe']*100:+.1f}%  "
              f"止损{'√' if r['stop'] else '×'} 止盈{'√' if r['tp'] else '×'}")


if __name__ == "__main__":
    main()
