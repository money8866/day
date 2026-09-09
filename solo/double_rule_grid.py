# -*- coding: utf-8 -*-
"""
天量翻倍 P0 规则优化 — 入场×止损网格回测（样本内 TDX 未复权日线）
================================================================
纪律（backtest-expert）：参数选择仅用样本内事件(date<20260611)；
样本外台账(20260612起)只用于诊断不参与调参；找参数高原而非峰值；
分年度(2024/2025)+TIER分层一致性检验；v2 冻结后新开台账前向跟踪。

入场变体：
  E0_v1   涨停/一字→次日开盘(次日一字涨停放弃)，否则事件日收盘（v1基线）
  E1_next 统一次日开盘(次日一字涨停放弃)
  E3_pb5  事件收盘-5%限价挂单，5个交易日内成交否则放弃
  E3_pb8  事件收盘-8%限价挂单，5个交易日内成交否则放弃
止损变体（触发次序：开盘跳空≤止损按开盘成交，盘中≤止损按止损价）：
  S0_v1  max(事件日最低, 入场×0.80)    S_none  无止损纯持有(对照)
  S8/S12/S15/S20/S30/S35  固定-8/-12/-15/-20/-30/-35%
  SA15/SA20/SA30  入场×(1−k×ATR20%)，k=1.5/2/3
  S_evlow95  事件日最低×0.95（低点下再让5%）
  S_t20_15   前20交易日免止损，之后固定-15%
  S_trail    浮盈≥+20%止损上移保本，≥+50%锁定+25%（棘轮，无初始止损）
出场：250交易日强制平仓。监测起点：E0 自成交次日（对齐v1台账），E1/E3 自成交当日起。
用法：python -X utf8 double_rule_grid.py [--workers 8]
输出：report_daily/double_rule_grid.csv / double_rule_grid_report.md
"""
import os, sys, time, argparse
from multiprocessing import Pool
import numpy as np
import pandas as pd

SOLO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SOLO_DIR)
import bts.data as D

FWD = 250
EV_CSV = os.path.join(SOLO_DIR, "report_daily/double_sli_pool_events.csv")
OUT_CSV = os.path.join(SOLO_DIR, "report_daily/double_rule_grid.csv")
OUT_MD = os.path.join(SOLO_DIR, "report_daily/double_rule_grid_report.md")

ENTRIES = ["E0_v1", "E1_next", "E3_pb5", "E3_pb8"]
STOPS = ["S0_v1", "S_none", "S8", "S12", "S15", "S20", "S30", "S35",
         "SA15", "SA20", "SA30", "S_evlow95", "S_t20_15", "S_trail"]
FIXED = {"S8": .08, "S12": .12, "S15": .15, "S20": .20, "S30": .30, "S35": .35}
ATRK = {"SA15": 1.5, "SA20": 2.0, "SA30": 3.0}


def _yizi_up(o, h, l, c):
    """一字涨停：无法成交"""
    return (h - l) < 1e-9 and (c / max(o, 1e-9) - 1) > 0.09


def _sim(o, h, l, c, i0, n, ev, entry, stop):
    lu = ev["lu"]
    ev_low = float(l[i0])
    c0 = float(c[i0])
    if entry == "E0_v1":
        if lu:
            j = i0 + 1
            if j >= n or _yizi_up(o[j], h[j], l[j], c[j]):
                return None
            ep, fd, sf = float(o[j]), j, j + 1
        else:
            ep, fd, sf = c0, i0, i0 + 1
    elif entry == "E1_next":
        j = i0 + 1
        if j >= n or _yizi_up(o[j], h[j], l[j], c[j]):
            return None
        ep, fd, sf = float(o[j]), j, j
    else:
        lim = c0 * (0.95 if entry == "E3_pb5" else 0.92)
        ep = fd = None
        for j in range(i0 + 1, min(i0 + 6, n)):
            if _yizi_up(o[j], h[j], l[j], c[j]):
                continue
            if o[j] <= lim:
                ep, fd = float(o[j]), j
                break
            if l[j] <= lim:
                ep, fd = lim, j
                break
        if ep is None:
            return None
        sf = fd
    if ep <= 0 or sf >= n:
        return None
    if stop in FIXED:
        st = ep * (1 - FIXED[stop])
    elif stop == "S0_v1":
        st = max(ev_low, ep * 0.80)
    elif stop.startswith("SA"):
        st = ep * (1 - ATRK[stop] * ev["atr"] / 100)
    elif stop == "S_evlow95":
        st = ev_low * 0.95
    else:
        st = None
    j_end = min(i0 + FWD, n - 1)
    exit_j, exit_p, reason = None, None, None

    def _run(js, je, stop_fn):
        for j in range(js, je + 1):
            s = stop_fn(j)
            if s is not None:
                if o[j] <= s:
                    return j, float(o[j]), "stop_gap"
                if l[j] <= s:
                    return j, float(s), "stop_hit"
        return None

    if stop in ("S_t20_15",):
        r = _run(sf, j_end, lambda j: None if j < sf + 20 else ep * 0.85)
    elif stop == "S_trail":
        st_t, run_hi = None, -np.inf
        for j in range(sf, j_end + 1):
            if st_t is not None:
                if o[j] <= st_t:
                    r = (j, float(o[j]), "stop_gap")
                    break
                if l[j] <= st_t:
                    r = (j, float(st_t), "stop_hit")
                    break
            run_hi = max(run_hi, float(h[j]))
            if run_hi >= ep * 1.5:
                st_t = ep * 1.25 if st_t is None else max(st_t, ep * 1.25)
            elif run_hi >= ep * 1.2:
                st_t = ep * 1.0 if st_t is None else max(st_t, ep * 1.0)
        else:
            r = None
    else:
        r = _run(sf, j_end, lambda j: st)
    if r:
        exit_j, exit_p, reason = r
    if exit_j is None:
        exit_j, exit_p = j_end, float(c[j_end])
        reason = "t250" if (j_end - i0) >= FWD else "data_end"
    mfe = float(np.max(h[fd:exit_j + 1])) / ep - 1
    return {"entry_p": ep, "exit_p": exit_p, "exit_reason": reason,
            "net": exit_p / ep - 1, "hold": exit_j - fd, "mfe": mfe,
            "fwd250": (n - 1 - i0) >= FWD}


def _worker(job):
    code, evs = job
    df = D.tdx_daily(code)
    if df is None or df.empty:
        df = D.load_daily(code, "20260611", lookback_bars=2000)
    if df is None or df.empty:
        return []
    dates = df["trade_date"].astype(str).to_numpy()
    o = df["open"].to_numpy(float)
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    c = df["close"].to_numpy(float)
    rows = []
    for ev in evs:
        i0 = int(np.searchsorted(dates, ev["date"]))
        if i0 >= len(dates) or dates[i0] != ev["date"]:
            continue
        n = len(dates)
        for entry in ENTRIES:
            for stop in STOPS:
                r = _sim(o, h, l, c, i0, n, ev, entry, stop)
                if r is None:
                    rows.append({"code": code, "date": ev["date"], "tier": ev["tier"],
                                 "year": ev["date"][:4], "entry": entry, "stop": stop,
                                 "filled": 0, "net": np.nan, "hold": np.nan,
                                 "mfe": np.nan, "exit_reason": "notrade", "fwd250": ev["fwd250"]})
                else:
                    rows.append({"code": code, "date": ev["date"], "tier": ev["tier"],
                                 "year": ev["date"][:4], "entry": entry, "stop": stop,
                                 "filled": 1, **r})
    return rows


def _agg(g):
    f = g[g.filled == 1]
    return pd.Series({
        "n_ev": len(g), "fill%": f"{len(f)/len(g)*100:.0f}%" if len(g) else "-",
        "n_tr": len(f),
        "中位%": f"{f.net.median()*100:.1f}" if len(f) else "-",
        "均值%": f"{f.net.mean()*100:.1f}" if len(f) else "-",
        "p10%": f"{f.net.quantile(.10)*100:.1f}" if len(f) else "-",
        "胜率%": f"{(f.net > 0).mean()*100:.0f}" if len(f) else "-",
        "止损%": f"{(f.exit_reason.isin(['stop_hit','stop_gap'])).mean()*100:.0f}" if len(f) else "-",
        "持有": f"{f.hold.median():.0f}" if len(f) else "-",
    })


def _table(df, label, lines):
    lines += [f"\n## {label}\n",
              "| 入场 | 止损 | 事件 | 成交率 | 笔数 | 中位% | 均值% | p10% | 胜率% | 止损% | 持有中位 |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for (e, s), g in df.groupby(["entry", "stop"]):
        a = _agg(g)
        lines.append(f"| {e} | {s} | {a['n_ev']} | {a['fill%']} | {a['n_tr']} | {a['中位%']} | "
                     f"{a['均值%']} | {a['p10%']} | {a['胜率%']} | {a['止损%']} | {a['持有']} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    ev = pd.read_csv(EV_CSV, dtype={"code": str, "date": str})
    ev = ev[ev.date < "20260611"].copy()
    mv = 10 ** (ev.log_mv - 4)
    t1 = (mv <= 30) & (ev.turn_today >= 8) & (ev.rel_hi250 <= -0.25) & (ev.ma60_x < 0.10) & (ev.ma20_x < 0.15)
    t2 = (mv <= 50) & (ev.turn_today >= 8) & (ev.turn_20m >= 3)
    ev["tier"] = np.where(t1, "T1", np.where(t2, "T2", ""))
    ev = ev[ev.tier != ""].copy()
    ev["lu"] = ev.is_limitup.astype(str).str.lower().isin(["true", "1"]) | \
               ev.is_yizi.astype(str).str.lower().isin(["true", "1"])
    ev["atr"] = ev.atr20pct.fillna(4.0)
    ev["fwd250"] = False
    idx = {code: g[["date", "tier", "lu", "atr", "fwd250"]].to_dict("records")
           for code, g in ev.groupby("code")}
    print(f"TIER 事件 {len(ev)}（T1={int((ev.tier == 'T1').sum())} T2={int((ev.tier == 'T2').sum())}）"
          f" 代码 {len(idx)} 变体 {len(ENTRIES) * len(STOPS)}", flush=True)

    t0 = time.time()
    codes = sorted(idx)
    chunks = [list(x) for x in np.array_split(np.asarray(codes, dtype=object), args.workers) if len(x)]
    jobs = [(cc, idx[cc]) for chunk in chunks for cc in chunk]
    with Pool(args.workers) as pool:
        batches = pool.map(_worker, jobs)
    rows = [r for b in batches for r in b]
    g = pd.DataFrame(rows)
    g.to_csv(OUT_CSV, index=False)
    print(f"回测完成 {len(g)} 行 耗时={time.time() - t0:.0f}s → {OUT_CSV}", flush=True)

    L = ["# 天量翻倍 P0 规则网格回测（样本内 20240112~20260610）", "",
         f"- TIER 事件 {len(ev)}（T1={int((ev.tier == 'T1').sum())} / T2={int((ev.tier == 'T2').sum())}），"
         f"入场×止损 = {len(ENTRIES)}×{len(STOPS)}",
         "- 诊断结论(v1台账样本外)：止损线100%由事件日最低主导(-20%为死代码)，TIER1止损距入场中位仅-2.4%，"
         "持有1天即被噪音打掉 → 网格核心问题：止损该多宽/要不要结构性止损，入场要不要让价",
         "- 注意：E3 回踩单未成交=空仓观望，其笔数小于其他入场；对比需同时看成交率与单笔质量", ""]
    _table(g, "一、全体 TIER（含2026短窗口，配对比较有效）", L)
    _table(g[g.tier == "T1"], "二、TIER1 全窗口", L)
    d250 = g[g.fwd250]
    _table(d250[d250.tier == "T1"], "三、TIER1 仅250日前视完整（绝对口径可与r250对照）", L)
    _table(g[g.tier == "T2"], "四、TIER2 全窗口", L)

    L += ["\n## 五、分年度一致性（TIER1 中位净收益%）\n",
          "| 入场 | 止损 | 2024 | 2025 |", "|---|---|---|---|"]
    t1g = g[(g.tier == "T1") & (g.filled == 1)]
    for (e, s), gg in t1g.groupby(["entry", "stop"]):
        y24 = gg[gg.year == "2024"].net.median() * 100
        y25 = gg[gg.year == "2025"].net.median() * 100
        L.append(f"| {e} | {s} | {y24:.1f} | {y25:.1f} |")

    best_e = "E0_v1"
    L += ["\n## 六、固定止损高原（TIER1，观察中位数随止损宽度的平坦度）\n",
          "| 止损 | 中位% | 均值% | 胜率% |", "|---|---|---|---|"]
    t1f = t1g[(t1g.entry == best_e) & (t1g.stop.isin(list(FIXED) + ["S0_v1", "S_none"]))]
    order = ["S0_v1", "S8", "S12", "S15", "S20", "S30", "S35", "S_none"]
    for s in order:
        gg = t1f[t1f.stop == s]
        if len(gg):
            L.append(f"| {s} | {gg.net.median()*100:.1f} | {gg.net.mean()*100:.1f} | {(gg.net > 0).mean()*100:.0f} |")
    with open(OUT_MD, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print("写出:", OUT_MD)
    print("\n".join(L[:60]))


if __name__ == "__main__":
    main()
