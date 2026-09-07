# -*- coding: utf-8 -*-
"""8月 V8/V9 信号回顾（离线，只读缓存）。

逻辑：
  1. 读取 report_daily/right_confirm_buy_202608*.json（V8）与 breakout_timing_202608*.json（V9）
  2. 每个信号作为一个事件：V8 S/A/B=买信号, C=观察; V9 PRIMARY_BUY/PRIMARY_RETEST_BUY/NEAR_TRIGGER=买信号, 其余=观察
  3. 用 stk_factor_pro 缓存（close*adj_factor 复权空间）计算：
     T+1开盘买入 → +3/+5/+10日收益、期末收益、MFE/MAE、止损/止盈命中
  4. qfq 锚点校验：V9 的 current_price 应≈信号日 raw close（qfq 锚定信号日）
"""
import json
import glob
import os
import sqlite3
import statistics as st

DB = r"D:\mystock\cache_daily\stock_data.db"
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
            events.append({
                "engine": "V8", "date": td, "code": s.get("ts_code", ""),
                "name": s.get("name", ""), "tag": lvl,
                "buy": lvl in V8_BUY,
                "entry_ref": s.get("entry") or 0.0,
                "stop": s.get("stop_loss") or 0.0,
                "tp1": s.get("tp1") or 0.0,
                "score": s.get("final_score"),
                "dist_risk": s.get("distribution_risk", False),
            })
    for fp in sorted(glob.glob(os.path.join(REPORT_DIR, "breakout_timing_202608*.json"))):
        data = json.load(open(fp, encoding="utf-8"))
        td = data.get("trade_date", "")
        for s in data.get("signals", []):
            state = s.get("state", "")
            events.append({
                "engine": "V9", "date": td, "code": s.get("ts_code", ""),
                "name": s.get("name", ""), "tag": state,
                "buy": state in V9_BUY,
                "entry_ref": s.get("current_price") or 0.0,
                "stop": s.get("stop_loss") or 0.0,
                "tp1": s.get("tp1") or 0.0,
                "score": s.get("t1_score"),
                "dist_risk": False,
                "anchor_check": s.get("current_price") or 0.0,
            })
    return events


def query(code, start, end):
    conn = sqlite3.connect(DB)
    try:
        cur = conn.execute(
            "SELECT trade_date, open_qfq, high_qfq, low_qfq, close_qfq, adj_factor "
            "FROM stk_factor_pro WHERE ts_code=? AND trade_date>=? AND trade_date<=? "
            "ORDER BY trade_date", (code, start, end))
        return cur.fetchall()
    finally:
        conn.close()


def eval_event(ev):
    """返回事件评估 dict 或 None（数据不足）。复权空间: A_t = close_qfq_t * adj_factor_t 与
    A_t = raw_t * factor_t 等价（qfq 与 bfq 差一个常数因子，比值不受锚点影响）。"""
    # 取信号日及其后所有K线
    rows = query(ev["code"], ev["date"], END_DATE)
    if not rows:
        return None
    dates = [r[0] for r in rows]
    if dates[0] != ev["date"]:
        # 信号日无缓存行，取第一条作为基准（用其 adj_factor 归一）
        return None
    factor_d = rows[0][5]
    if not factor_d or factor_d <= 0:
        return None
    fwd = rows[1:]
    if not fwd:
        return {"ev": ev, "skip": "信号日后无K线"}
    # qfq 空间直接用：entry = T+1 open_qfq（V8/V9 的 qfq 与缓存同为 tushare 口径，
    # 但缓存可能被后续日期重新锚定 → 用 adj_factor 比值修正回信号日锚）
    # A-space: a_t = qfq_t * factor_t；相对收益 = a_t / a_entry - 1
    a_entry = fwd[0][1] * fwd[0][5]
    if not a_entry or a_entry <= 0:
        return {"ev": ev, "skip": "入场价异常"}
    n = len(fwd)
    def ret_at(k):
        if n > k:
            return fwd[k][4] * fwd[k][5] / a_entry - 1
        return None
    highs = [r[2] * r[5] / a_entry for r in fwd]
    lows = [r[3] * r[5] / a_entry for r in fwd]
    mfe = max(highs) - 1
    mae = min(lows) - 1
    end_ret = fwd[-1][4] * fwd[-1][5] / a_entry - 1
    # 止损/止盈（以 JSON 价格为 qfq@信号日口径 → 换算 A-space: p * factor_d）
    stop_a = (ev["stop"] or 0) * factor_d
    tp_a = (ev["tp1"] or 0) * factor_d
    stop_hit, tp_hit, first = False, False, ""
    for i, r in enumerate(fwd):
        lo, hi = r[3] * r[5], r[2] * r[5]
        if tp_a > 0 and hi >= tp_a and not tp_hit:
            tp_hit = True
            first = first or "TP1"
        if stop_a > 0 and lo <= stop_a and not stop_hit:
            stop_hit = True
            first = first or "STOP"
    # 锚点校验（V9）：current_price(qfq@D) vs raw close@D = qfq@D（锚定日即信号日应相等）
    anchor_dev = None
    if ev.get("anchor_check"):
        raw_close_d = rows[0][4]  # close_qfq@D（缓存当前锚）
        # 缓存行的 close_qfq 锚不一定在 D，用 factor 修正：raw_D = close_qfq_D * factor_D / factor_D
        # 实际上 raw_D = close_qfq_D * factor_latest/factor_D 未知 → 用 close_qfq_D*factor_D 与
        # entry_ref*factor_D 同空间比较（entry_ref 为 qfq@D → A_space = ref*factor_D）
        a_ref = ev["anchor_check"] * factor_d
        a_close_d = rows[0][4] * factor_d
        if a_close_d > 0:
            anchor_dev = (a_close_d / a_ref - 1) * 100
    return {
        "ev": ev, "n": n,
        "r3": ret_at(2), "r5": ret_at(4), "r10": ret_at(9),
        "end": end_ret, "mfe": mfe, "mae": mae,
        "stop_hit": stop_hit, "tp_hit": tp_hit, "first": first,
        "anchor_dev": anchor_dev,
    }


def agg(res_list, label):
    if not res_list:
        print(f"  {label}: 0 个事件")
        return
    ends = [r["end"] for r in res_list if r["end"] is not None]
    mfes = [r["mfe"] for r in res_list]
    maes = [r["mae"] for r in res_list]
    stops = [r for r in res_list if r["stop_hit"]]
    tps = [r for r in res_list if r["tp_hit"]]
    r5s = [r["r5"] for r in res_list if r["r5"] is not None]
    r10s = [r["r10"] for r in res_list if r["r10"] is not None]
    win = sum(1 for e in ends if e > 0)
    def pct(x): return f"{x*100:+.1f}%"
    print(f"  {label}: n={len(res_list)}  期末收益 均值{pct(st.mean(ends))} 中位{pct(st.median(ends))} "
          f"胜率{win}/{len(ends)}={win/max(1,len(ends))*100:.0f}%")
    if r5s:
        print(f"      +5日 均值{pct(st.mean(r5s))} 中位{pct(st.median(r5s))}   "
              f"+10日 均值{pct(st.mean(r10s))} 中位{pct(st.median(r10s))}" if r10s else
              f"      +5日 均值{pct(st.mean(r5s))} 中位{pct(st.median(r5s))}")
    print(f"      MFE 均值{pct(st.mean(mfes))} 最大{pct(max(mfes))}   "
          f"MAE 均值{pct(st.mean(maes))} 最差{pct(min(maes))}")
    print(f"      止损命中 {len(stops)}/{len(res_list)}  止盈(TP1)命中 {len(tps)}/{len(res_list)}")


def main():
    events = load_events()
    print(f"8月事件总数: {len(events)}  "
          f"(V8 {sum(1 for e in events if e['engine']=='V8')} / "
          f"V9 {sum(1 for e in events if e['engine']=='V9')})")
    results, skipped = [], []
    for ev in events:
        r = eval_event(ev)
        if r is None:
            skipped.append((ev["engine"], ev["date"], ev["code"], "信号日无缓存"))
        elif "skip" in r:
            skipped.append((ev["engine"], ev["date"], ev["code"], r["skip"]))
        else:
            results.append(r)
    if skipped:
        print("\n[数据不足被跳过]")
        for s in skipped:
            print("  ", s)
    # 锚点校验
    devs = [abs(r["anchor_dev"]) for r in results if r["anchor_dev"] is not None]
    if devs:
        print(f"\n[qfq锚点校验(V9 current_price vs 缓存A空间)] n={len(devs)} "
              f"最大偏差={max(devs):.2f}% 中位={st.median(devs):.2f}%")

    # ── 分组统计 ──
    print("\n═══ V8 买信号 (S/A/B) ═══")
    for lvl in ["S", "A", "B"]:
        agg([r for r in results if r["ev"]["engine"] == "V8" and r["ev"]["tag"] == lvl],
            f"V8 {lvl}")
    agg([r for r in results if r["ev"]["engine"] == "V8" and r["ev"]["tag"] in V8_BUY],
        "V8 S/A/B 合计")
    print("\n═══ V8 观察 (C) ═══")
    agg([r for r in results if r["ev"]["engine"] == "V8" and r["ev"]["tag"] == "C"], "V8 C")

    print("\n═══ V9 买信号 ═══")
    agg([r for r in results if r["ev"]["engine"] == "V9" and r["ev"]["buy"]], "V9 买信号合计")
    print("\n═══ V9 观察 ═══")
    agg([r for r in results if r["ev"]["engine"] == "V9" and not r["ev"]["buy"]], "V9 非买状态")

    # ── 明细：买信号逐条 ──
    print("\n═══ 买信号明细（按期末收益排序）═══")
    buys = [r for r in results if r["ev"]["buy"]]
    buys.sort(key=lambda r: r["end"], reverse=True)
    print(f"{'日期':<9}{'级别':<22}{'代码':<10}{'名称':<10}{'分数':>5} "
          f"{'+5日':>7}{'+10日':>7}{'期末':>7}{'MFE':>7}{'MAE':>7}  止损/止盈")
    for r in buys:
        ev = r["ev"]
        tag = f"{ev['engine']}|{ev['tag']}"
        r5 = f"{r['r5']*100:+.1f}%" if r["r5"] is not None else "  n/a"
        r10 = f"{r['r10']*100:+.1f}%" if r["r10"] is not None else "  n/a"
        print(f"{ev['date']:<9}{tag:<22}{ev['code']:<10}{ev['name']:<10}"
              f"{ev['score'] or 0:>5.1f} {r5:>7}{r10:>7}{r['end']*100:+.1f}%"
              f"{r['mfe']*100:+.1f}%{r['mae']*100:+.1f}%  "
              f"{'STOP先' if r['first']=='STOP' else ('TP1先' if r['first']=='TP1' else '未触发')}")

    # ── 同股重复信号 ──
    from collections import Counter
    cnt = Counter((r["ev"]["engine"], r["ev"]["code"]) for r in results)
    dups = {k: v for k, v in cnt.items() if v >= 3}
    if dups:
        print(f"\n[重复信号≥3次的标的] {dups}")
    uniq = Counter((r["ev"]["engine"], r["ev"]["code"]) for r in results)
    print(f"\n唯一标的数: V8={len({k[1] for k in uniq if k[0]=='V8'})} "
          f"V9={len({k[1] for k in uniq if k[0]=='V9'})}")


if __name__ == "__main__":
    main()
