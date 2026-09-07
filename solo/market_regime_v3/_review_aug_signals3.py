# -*- coding: utf-8 -*-
"""8月信号回顾(3)：B级分数分层 + 固定+5日窗口 + 大盘基准对比。"""
import json
import glob
import os
import sqlite3
import statistics as st

DB = r"D:\mystock\cache_daily\stock_data.db"
REPORT_DIR = r"D:\mystock\solo\report_daily"
END_DATE = "20260904"
V8_BUY = {"S", "A", "B"}


def load_events():
    events = []
    for fp in sorted(glob.glob(os.path.join(REPORT_DIR, "right_confirm_buy_202608*.json"))):
        data = json.load(open(fp, encoding="utf-8"))
        td = data.get("trade_date", "")
        for s in data.get("signals", []):
            lvl = (s.get("signal_level") or "?")[:1]
            events.append({"engine": "V8", "date": td, "code": s.get("ts_code", ""),
                           "name": s.get("name", ""), "tag": lvl, "buy": lvl in V8_BUY,
                           "score": s.get("final_score") or 0.0})
    return events


def query(code, start, end):
    conn = sqlite3.connect(DB)
    try:
        return conn.execute(
            "SELECT trade_date, open_qfq, high_qfq, low_qfq, close_qfq, adj_factor "
            "FROM stk_factor_pro WHERE ts_code=? AND trade_date>=? AND trade_date<=? "
            "ORDER BY trade_date", (code, start, end)).fetchall()
    finally:
        conn.close()


def index_bars():
    """尝试拉上证指数（网络）；失败返回 None。"""
    try:
        import sys
        sys.path.insert(0, r"D:\mystock\solo")
        import stock_cache as sc
        pro = sc._get_pro()
        df = pro.index_daily(ts_code="000001.SH", start_date="20260810", end_date=END_DATE)
        if df is None or df.empty:
            return None
        df = df.sort_values("trade_date")
        return list(zip(df["trade_date"].astype(str), df["close"].astype(float)))
    except Exception as e:
        print(f"[指数获取失败: {type(e).__name__}: {e}]")
        return None


def eval_event(ev, idx_map):
    rows = query(ev["code"], ev["date"], END_DATE)
    if not rows or rows[0][0] != ev["date"]:
        return None
    fwd = rows[1:]
    if not fwd:
        return None
    a_entry = fwd[0][1] * fwd[0][5]
    n = len(fwd)
    out = {"ev": ev, "n": n,
           "end": fwd[-1][4] * fwd[-1][5] / a_entry - 1,
           "r5": fwd[4][4] * fwd[4][5] / a_entry - 1 if n > 4 else None,
           "mfe": max(r[2] * r[5] for r in fwd) / a_entry - 1,
           "mae": min(r[3] * r[5] for r in fwd) / a_entry - 1}
    if idx_map:
        d0 = ev["date"]
        dates_after = [d for d in idx_map if d > d0]
        if dates_after:
            d5 = dates_after[4] if len(dates_after) > 4 else dates_after[-1]
            dend = dates_after[-1]
            out["idx_r5"] = idx_map[d5] / idx_map[d0] - 1
            out["idx_end"] = idx_map[dend] / idx_map[d0] - 1
    return out


def agg(rows, label, key="end", rel_key=None):
    rows = [r for r in rows if r.get(key) is not None]
    if not rows:
        print(f"  {label}: n=0")
        return
    vals = [r[key] for r in rows]
    win = sum(1 for v in vals if v > 0)
    line = (f"  {label}: n={len(rows)}  均值{st.mean(vals)*100:+.1f}%  "
            f"中位{st.median(vals)*100:+.1f}%  胜率{win/len(vals)*100:.0f}%")
    if rel_key and all(r.get(rel_key) is not None for r in rows):
        rels = [r[key] - r[rel_key] for r in rows]
        line += (f"  | 超额均值{st.mean(rels)*100:+.1f}%  "
                 f"超额胜率{sum(1 for x in rels if x > 0)/len(rels)*100:.0f}%")
    print(line)


def main():
    events = load_events()
    idx = index_bars()
    idx_map = dict(idx) if idx else None
    if idx_map:
        ds = sorted(idx_map)
        print(f"上证指数基准: {ds[0]}~{ds[-1]}  "
              f"0814→0904 {(idx_map[ds[-1]]/idx_map[[d for d in ds if d>='20260814'][0]]-1)*100:+.1f}%")
        # 分周表现
        for a, b in [("20260814", "20260821"), ("20260821", "20260828"),
                     ("20260828", "20260904")]:
            print(f"  {a}→{b}: {(idx_map[b]/idx_map[a]-1)*100:+.2f}%")

    results = [r for r in (eval_event(e, idx_map) for e in events) if r]

    # 去重首信号
    seen = set()
    firsts = []
    for r in sorted(results, key=lambda x: x["ev"]["date"]):
        key = (r["ev"]["tag"], r["ev"]["code"])
        if key not in seen:
            seen.add(key)
            firsts.append(r)

    print("\n═══ V8 买信号首事件（每票每级别取8月首个）固定窗口 ═══")
    agg([r for r in firsts if r["ev"]["buy"]], "S/A/B 全部 +5日", "r5", "idx_r5")
    agg([r for r in firsts if r["ev"]["buy"]], "S/A/B 全部 至0904", "end", "idx_end")
    for lvl in ["S", "A", "B"]:
        agg([r for r in firsts if r["ev"]["tag"] == lvl], f"{lvl} 至0904", "end", "idx_end")
        agg([r for r in firsts if r["ev"]["tag"] == lvl], f"{lvl} +5日", "r5", "idx_r5")

    print("\n═══ V8 B级首信号 按FinalScore分层（至0904，含超额） ═══")
    for lo, hi in [(70, 74), (74, 78), (78, 86), (86, 101)]:
        agg([r for r in firsts if r["ev"]["tag"] == "B" and lo <= r["ev"]["score"] < hi],
            f"B[{lo},{hi})")

    print("\n═══ V8 C级首信号（对照组，至0904） ═══")
    agg([r for r in firsts if r["ev"]["tag"] == "C"], "C 全部", "end", "idx_end")
    for lo, hi in [(60, 70), (70, 80)]:
        agg([r for r in firsts if r["ev"]["tag"] == "C" and lo <= r["ev"]["score"] < hi],
            f"C[{lo},{hi})")

    print("\n═══ 时间衰减检查：B级首信号按信号周分组（至0904） ═══")
    for wk, (a, b) in [("W1 0814-0820", ("20260814", "20260820")),
                       ("W2 0821-0827", ("20260821", "20260827")),
                       ("W3 0828-0831", ("20260828", "20260831"))]:
        agg([r for r in firsts if r["ev"]["tag"] == "B" and a <= r["ev"]["date"] <= b], wk)


if __name__ == "__main__":
    main()
