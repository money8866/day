"""P0 猎狐(T0_CONFIRM)历史回放器

目的：把线上只跑了 2 天(9/15、9/16)的「猎狐」T0 天量确认买点，回放到 2021-2026 全历史，
      把样本从 16 抬到数百，用于标定 P1-P3(gap/type/volr/score/level)阈值。

口径与实时 scan_fox_t0 完全对齐：
  1. 事件锚 = find_event_anchor 等价逻辑：近 MAX_EVENT_AGE(60) 日内 120日分位量能+换手双≥P99，
     再经 extreme_cluster_anchor 天量簇归并取簇首强势日(Same as live)。
  2. 状态 = state_and_features(df, anchor_idx, ep, end=signal_j)；state=='T0_CONFIRM' 且 type!='DISTRIBUTION' 保留。
  3. 去重 = (code, event_date) 首次触发(等价 live fired_map)。
  4. 收益 = 以信号日收盘价为买入基准的前向收益(与 live「尾盘 14:50 定稿、次日无法以收盘价成交」的毛口径一致)。

输出：report_daily/fox_t0_backtest_signals.csv + 控制台分档统计
"""
import os
import sys
import time

sys.path.insert(0, r"D:\mystock\solo")
import numpy as np
import pandas as pd
from multiprocessing import Pool
from w7_second_wave_engine import (CacheReader, state_and_features, anchor_features, ANCHORS,
                                   similarity, alpha_hvt, alpha_trend, alpha_fina, alpha_rs,
                                   alpha_upside, t120_alpha_score, entry_score_v2, MarketCtx,
                                   finite, safe_mean, MAIN_EVENT_PCT, extreme_cluster_anchor,
                                   lifecycle, hvt_future_space, hvt_acceleration, hvt_platform,
                                   hvt_distribution_risk, hvt_v3_score, rank_score_v5,
                                   hvt_type, MIN_BARS, MAX_EVENT_AGE, T0_CONFIRM_MAX_BARS, DATA_START)

DATE_END = "20260917"       # 回放终点（DB 最新交易日）
EVENT_MIN_DATE = "20210101"  # 事件起点
LOAD_MIN_DATE = "20210101"   # 加载历史起点
MIN_HIST = 320               # 最少 K 线
WORKERS = 8
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 0
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_daily", "fox_t0_backtest_signals.csv")

_MKT_DATES = None
_MKT_VALS = None
_ANCHORS = {}


def _init_worker(mdates, mvals, anchors):
    global _MKT_DATES, _MKT_VALS, _ANCHORS
    _MKT_DATES, _MKT_VALS, _ANCHORS = mdates, mvals, anchors


def find_events(df):
    """事件候选：事件日前 120 日分位，量能+换手双≥P99（与引擎 extreme_event 等价）"""
    dates = df["trade_date"].astype(str).to_numpy()
    tr = pd.to_numeric(df.turnover_rate_f, errors="coerce").fillna(0).to_numpy(dtype=float)
    vol = pd.to_numeric(df.vol, errors="coerce").fillna(0).to_numpy(dtype=float)
    n = len(df)
    if n < MIN_HIST:
        return []
    out = []
    for i in range(121, n - 2):
        if dates[i] < EVENT_MIN_DATE:
            continue
        start = i - 120
        p_t = float(np.mean(tr[start:i] <= tr[i]) * 100.0)
        p_v = float(np.mean(vol[start:i] <= vol[i]) * 100.0)
        if min(p_t, p_v) >= MAIN_EVENT_PCT:
            out.append((int(i), float(max(p_t, p_v))))
    return out


def _anchor_as_of(df, ex_idx, ex_ep, j):
    """等价 find_event_anchor(df[:j+1])：候选区间 [max(MIN_BARS, j-61), j-2] 内天量簇归并取锚"""
    lo = max(MIN_BARS, j - MAX_EVENT_AGE - 1)
    hi = j - 2
    a = int(np.searchsorted(ex_idx, lo, "left"))
    b = int(np.searchsorted(ex_idx, hi, "right"))
    if b <= a:
        return None
    cands = [(int(ex_idx[k]), ex_ep[int(ex_idx[k])]) for k in range(a, b)]
    return extreme_cluster_anchor(df, cands)


def scan_batch(codes_names):
    reader = CacheReader()
    reader.load_all(DATE_END, codes=[c for c, _ in codes_names], min_date=LOAD_MIN_DATE, chunk=500, verbose=False)
    reader.load_fina()
    mkt = MarketCtx(_MKT_DATES, _MKT_VALS)
    samples = []
    logf = open(f"foxt0_w{os.getpid()}.log", "a", encoding="utf-8", buffering=1)
    for code, name in codes_names:
        t0 = time.time()
        df = reader.bars(code, DATE_END)
        n = len(df)
        if n < MIN_HIST:
            continue
        ex = find_events(df)
        if not ex:
            logf.write(f"{code} n={n} ev=0 t={time.time()-t0:.1f}s\n")
            continue
        ex_idx = np.array([e[0] for e in ex], dtype=int)
        ex_ep = {int(e[0]): float(e[1]) for e in ex}
        closes = df.close.to_numpy(dtype=float)
        vols = pd.to_numeric(df.vol, errors="coerce").fillna(0).to_numpy(dtype=float)

        # 信号日候选：T0_CONFIRM 要求 3 <= j - anchor <= T0_CONFIRM_MAX_BARS，锚必为极端事件日，
        # 故所有可能信号日 = ∪ [i+3, i+T0_CONFIRM_MAX_BARS]
        js = set()
        for i, _ in ex:
            for j in range(i + 3, i + T0_CONFIRM_MAX_BARS + 1):
                if j < n - 1:
                    js.add(j)

        fired = {}  # event_date -> signal_j（同锚只取首触发，等价 live fired_map）
        for j in sorted(js):
            anc = _anchor_as_of(df, ex_idx, ex_ep, j)
            if not anc:
                continue
            ai, aep = int(anc[0]), float(anc[1])
            if not (3 <= j - ai <= T0_CONFIRM_MAX_BARS):
                continue
            ev_date = str(df.iloc[ai].trade_date)
            if ev_date in fired:
                continue
            res = state_and_features(df, ai, aep, end=j)
            if not res:
                continue
            base, state, pp, pp_ok, reexp, breakout, major_risk, dd, pressure = res
            if state != "T0_CONFIRM":
                continue
            lc = lifecycle(df, j)
            current = dict(base)
            sim_a = similarity(current, _ANCHORS.get("中际旭创"))
            sim_b = similarity(current, _ANCHORS.get("华正新材"))
            hvt = (sim_a + sim_b) / 2.0
            fina_now, fina_prev = reader.fina(code, as_of=str(df.iloc[j].trade_date))
            dims = {
                "hvt": alpha_hvt(base, hvt, dd),
                "trend": alpha_trend(df, j),
                "fina": alpha_fina(fina_now, fina_prev),
                "rs": alpha_rs(df, j, mkt),
                "upside": alpha_upside(df, j),
                "sector": 50.0,
            }
            t120 = t120_alpha_score(dims)
            fs = hvt_future_space(df, j, lc)
            acc = hvt_acceleration(df, j, mkt)
            plat = hvt_platform(df, ai, j)
            dist_risk = hvt_distribution_risk(df, j, base, lc)
            score, base_score, absorption, penalty = hvt_v3_score(
                base, lc, dims["hvt"], fs, acc, dims["rs"], dims["fina"], plat, dist_risk)
            tp = hvt_type(state, lc, dist_risk)
            if tp == "DISTRIBUTION":   # 与 live scan_fox_t0 一致
                continue
            rank = rank_score_v5(score, lc, dist_risk, dd)
            event_low = finite(df.low.iloc[ai])
            entry = entry_score_v2(df, j, pp, pp_ok, reexp, breakout, event_low, mkt)[0]
            fired[ev_date] = j

            buy = closes[j]

            def fwd(k):
                return closes[k] / buy - 1.0 if k < n and buy > 0 else np.nan

            def mfe(k):
                seg = closes[j + 1:k + 1]
                return seg.max() / buy - 1.0 if len(seg) and buy > 0 else np.nan

            def mae(k):
                seg = closes[j + 1:k + 1]
                return seg.min() / buy - 1.0 if len(seg) and buy > 0 else np.nan

            t0_vol20 = safe_mean(vols[max(0, ai - 20):ai])
            cf_vol20 = safe_mean(vols[max(0, j - 20):j])
            samples.append({
                "code": code, "name": name,
                "event_date": ev_date, "signal_date": str(df.iloc[j].trade_date),
                "state": state, "type": tp, "level": lc["level"],
                "gap": int(j - ai),
                "score": round(score, 1), "base_score": round(base_score, 1), "rank": round(rank, 1),
                "abs": round(absorption, 1), "fs": round(fs, 1), "acc": round(acc, 1),
                "plat": round(plat, 1), "dr": round(dist_risk, 1), "penalty": round(penalty, 1),
                "extension": round(lc["extension"], 2),
                "hvt_d": round(dims["hvt"], 1), "trend_d": round(dims["trend"], 1),
                "fina_d": round(dims["fina"], 1), "rs_d": round(dims["rs"], 1),
                "t120": round(t120, 1), "entry": round(entry, 1),
                "t0_volr": round(finite(vols[ai]) / t0_vol20, 2) if t0_vol20 > 0 else np.nan,
                "cf_volr": round(finite(vols[j]) / cf_vol20, 2) if cf_vol20 > 0 else np.nan,
                "fwd1": fwd(j + 1), "fwd3": fwd(j + 3), "fwd5": fwd(j + 5),
                "fwd10": fwd(j + 10), "fwd20": fwd(j + 20), "fwd60": fwd(j + 60),
                "mfe20": mfe(j + 20), "mae20": mae(j + 20),
                "mfe60": mfe(j + 60), "mae60": mae(j + 60),
            })
        logf.write(f"{code} n={n} ev={len(ex)} sig={len(fired)} t={time.time()-t0:.1f}s\n")
    logf.close()
    reader.close()
    return samples


def _pct(x):
    return f"{x * 100:+.2f}%" if x == x else "-"


def block(sub, label, keys=("fwd1", "fwd3", "fwd5", "fwd10", "fwd20", "fwd60")):
    if sub.empty:
        print(f"\n### {label}  样本=0")
        return
    print(f"\n### {label}  样本={len(sub)}")
    line = []
    for k in keys:
        d = sub[k].dropna()
        if d.empty:
            line.append(f"{k}=无")
            continue
        line.append(f"{k}: {_pct(d.mean())}/{(d>0).mean()*100:.0f}%")
    print("  " + "  ".join(line))
    for k in ("mfe20", "mae20"):
        d = sub[k].dropna()
        if len(d):
            print(f"  {k}: 均值={_pct(d.mean())} 中位={_pct(d.median())}")


def main():
    t0 = time.time()
    reader = CacheReader()
    universe = reader.universe(DATE_END)
    stock_list = []
    for r in universe.to_dict("records"):
        name = str(r.get("name") or r["ts_code"])
        if "ST" in name.upper() or "退" in name:
            continue
        basic = reader.basic.loc[r["ts_code"]] if r["ts_code"] in reader.basic.index else {}
        list_date = str(basic.get("list_date", "")) if hasattr(basic, "get") else ""
        if list_date and list_date.isdigit() and int(list_date) > int(DATE_END) - 365:
            continue
        stock_list.append((str(r["ts_code"]), name))
    if LIMIT:
        stock_list = stock_list[:LIMIT]
    print(f"[foxt0] 股池={len(stock_list)} workers={WORKERS} 事件口径=120日分位双≥P99 + 簇归并锚", flush=True)
    mdates, mvals = reader.market_curve(DATE_END)
    anchors = {}
    for label, (code, adate) in ANCHORS.items():
        anchors[label] = anchor_features(reader.bars_sql(code, DATE_END), adate)
    batches = [stock_list[i::WORKERS] for i in range(WORKERS)]
    batches = [b for b in batches if b]
    print("[foxt0] 并行回放中（T0_CONFIRM 口径）...", flush=True)
    with Pool(WORKERS, initializer=_init_worker, initargs=(mdates, mvals, anchors)) as pool:
        results = pool.map_async(scan_batch, batches).get()
    samples = [s for batch in results for s in batch]
    reader.close()
    print(f"[foxt0] 回放完成 样本={len(samples)} 耗时={time.time()-t0:.0f}s", flush=True)
    S = pd.DataFrame(samples)
    if S.empty:
        print("无任何 T0_CONFIRM 信号")
        return
    S = S.sort_values(["signal_date", "code"]).reset_index(drop=True)
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    S.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    print(f"[foxt0] 明细已保存 {CSV_PATH}")

    print("\n" + "═" * 96)
    print("猎狐 T0_CONFIRM 全历史回放总览（毛收益，基准=信号日收盘价）")
    print("═" * 96)
    block(S, "全样本")
    S["year"] = S.signal_date.str[:4]
    for y, g in S.groupby("year"):
        block(g, f"年度={y}")

    print("\n" + "═" * 96)
    print("P1 分档：gap（T0锚→确认日的根数）")
    print("═" * 96)
    for lo, hi in ((3, 3), (4, 5), (6, 6), (7, 8), (9, 10)):
        block(S[(S.gap >= lo) & (S.gap <= hi)], f"gap∈[{lo},{hi}]")

    print("\n" + "═" * 96)
    print("P2 分档：type（生命周期）")
    print("═" * 96)
    for tp, g in S.groupby("type"):
        block(g, f"type={tp}")

    print("\n" + "═" * 96)
    print("P3 分档：score / level / 量比")
    print("═" * 96)
    for lo, hi in ((0, 60), (60, 68), (68, 75), (75, 85), (85, 200)):
        block(S[(S.score >= lo) & (S.score < hi)], f"score∈[{lo},{hi})")
    for lv, g in S.groupby("level"):
        block(g, f"level={lv}")
    for lo, hi in ((0, 0.8), (0.8, 1.2), (1.2, 2.0), (2.0, 99)):
        block(S[(S.cf_volr >= lo) & (S.cf_volr < hi)], f"cf_volr∈[{lo},{hi})")

    print(f"\n[foxt0] 总耗时={time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
