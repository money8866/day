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
                                   finite, MAIN_EVENT_PCT, extreme_event, lifecycle,
                                   hvt_future_space, hvt_acceleration, hvt_platform,
                                   hvt_distribution_risk, hvt_v3_score, rank_score_v5,
                                   hvt_type, WATCH_MIN_SCORE)

# ===== V5 HVT-V3 四周期回测：T+10/20/60/120 收益分布/胜率/盈亏比/MFE/MAE/右尾概率 =====
# 口径：
#   - HVT 事件：事件日前 120 日分位、量能+换手双≥P99（直接复用引擎 extreme_event，与引擎主口径完全一致）
#   - 信号日：事件后 1~60 日内按 V5 状态机判定 status（PRIMARY_BUY/T120_ROCKET/CONFIRMED/WATCH）
#   - 类型：CORE/MID/EXT/DISTRIBUTION（hvt_type 生命周期分类）
#   - 评分：HVT-V3 BaseScore(25/20/15/15/10/5/10) - DistributionRiskPenalty，Rank=收益风险比
#   - 收益：毛收益（未扣成本）；sector 固定 50；fina 用 point-in-time 防未来函数
# 输出：各分组四周期均值/中位/P90/P10、胜率、盈亏比、MFE/MAE、右尾概率(≥20/40/60/100%)

DATE_END = "20260828"
EVENT_MIN_DATE = "20240101"
TRACK_DAYS = 60
REPEAT_GAP = 5
MIN_HIST = 320
WORKERS = 8
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 0
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_daily", "w7_backtest_v5_signals.csv")

_MKT_DATES = None
_MKT_VALS = None
_ANCHORS = {}


def _init_worker(mdates, mvals, anchors):
    global _MKT_DATES, _MKT_VALS, _ANCHORS
    _MKT_DATES, _MKT_VALS, _ANCHORS = mdates, mvals, anchors


def find_events(df):
    """V5 口径：事件日前 120 日分位天量（与引擎 extreme_event 等价：量能+换手双≥P99）
    numpy 数组实现（避免 pandas 逐行切片开销，回测逐股票批量调用）"""
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


def v5_status(score, entry, trend_confirmed, tp):
    """V5 状态机（与引擎 analyze 一致）：派发不进 A/B 榜；高分高买点=PRIMARY_BUY；≥80=T120_ROCKET；≥70+确认=CONFIRMED"""
    if tp == "DISTRIBUTION":
        return "WATCH"
    if score >= 85 and entry >= 80:
        return "PRIMARY_BUY"
    if score >= 80:
        return "T120_ROCKET"
    if score >= 70 and (trend_confirmed or tp in ("MID", "EXT")):
        return "CONFIRMED"
    return "WATCH"


def scan_batch(codes_names):
    reader = CacheReader()
    reader.load_all(DATE_END, codes=[c for c, _ in codes_names], min_date="20230101", chunk=500, verbose=False)
    reader.load_fina()
    mkt = MarketCtx(_MKT_DATES, _MKT_VALS)
    samples = []
    logf = open(f"bt5_w{os.getpid()}.log", "a", encoding="utf-8", buffering=1)
    for idx, (code, name) in enumerate(codes_names):
        t0 = time.time()
        df = reader.bars(code, DATE_END)
        if len(df) < MIN_HIST:
            logf.write(f"{code} n={len(df)} t={time.time()-t0:.1f}s\n")
            continue
        events = find_events(df)
        if not events:
            logf.write(f"{code} n={len(df)} ev=0 t={time.time()-t0:.1f}s\n")
            continue
        closes = df.close.to_numpy(dtype=float)
        n = len(df)
        last_sig_idx = -10 ** 9
        for i, ep in events:
            event_low = finite(df.low.iloc[i])
            for j in range(i + 1, min(i + TRACK_DAYS, n - 2)):
                res = state_and_features(df, i, ep, end=j)
                if not res:
                    continue
                # REPEAT_GAP 去重提前：语义不变（距上次输出日<5 必然不输出），省掉被跳过日的 V5 全维度计算
                if j - last_sig_idx < REPEAT_GAP:
                    continue
                base, state, pp, pp_ok, reexp, breakout, major_risk, dd, pressure = res
                d = str(df.iloc[j].trade_date)
                # V5 生命周期 + 四周期维度（与引擎 analyze 完全同口径）
                lc = lifecycle(df, j)
                sim_a = similarity(base, _ANCHORS.get("中际旭创"))
                sim_b = similarity(base, _ANCHORS.get("华正新材"))
                hvt = (sim_a + sim_b) / 2.0
                dims = {
                    "hvt": alpha_hvt(base, hvt, dd),
                    "trend": alpha_trend(df, j),
                    "fina": alpha_fina(*reader.fina(code, as_of=d)),
                    "rs": alpha_rs(df, j, mkt),
                    "upside": alpha_upside(df, j),
                    "sector": 50.0,
                }
                t120 = t120_alpha_score(dims)
                fs = hvt_future_space(df, j, lc)
                acc = hvt_acceleration(df, j, mkt)
                plat = hvt_platform(df, i, j)
                dist_risk = hvt_distribution_risk(df, j, base, lc)
                score, base_score, absorption, penalty = hvt_v3_score(base, lc, dims["hvt"], fs, acc, dims["rs"], dims["fina"], plat, dist_risk)
                rank = rank_score_v5(score, lc, dist_risk, dd)
                tp = hvt_type(state, lc, dist_risk)
                trend_confirmed = breakout or reexp or dims["trend"] >= 70
                # entry 仅对可能 PRIMARY_BUY 的日计算（score≥85 才需要 entry 判定）
                entry = entry_score_v2(df, j, pp, pp_ok, reexp, breakout, event_low, mkt)[0] if score >= 85 else 0.0
                status = v5_status(score, entry, trend_confirmed, tp)
                if status == "WATCH" and base_score < WATCH_MIN_SCORE:
                    continue
                last_sig_idx = j
                buy = closes[j]

                def fwd(k):
                    return closes[k] / buy - 1.0 if k < n and buy > 0 else np.nan

                def mfe(k):
                    seg = closes[j + 1:k + 1]
                    return seg.max() / buy - 1.0 if len(seg) and buy > 0 else np.nan

                def mae(k):
                    seg = closes[j + 1:k + 1]
                    return seg.min() / buy - 1.0 if len(seg) and buy > 0 else np.nan

                samples.append({
                    "code": code, "name": name,
                    "event_date": str(df.iloc[i].trade_date), "signal_date": d,
                    "state": state, "type": tp, "status": status,
                    "score": round(score, 1), "base_score": round(base_score, 1), "rank": round(rank, 1),
                    "abs": round(absorption, 1), "fs": round(fs, 1), "acc": round(acc, 1),
                    "plat": round(plat, 1), "dr": round(dist_risk, 1), "penalty": round(penalty, 1),
                    "level": lc["level"], "extension": round(lc["extension"], 2),
                    "hvt_d": round(dims["hvt"], 1), "trend_d": round(dims["trend"], 1),
                    "fina_d": round(dims["fina"], 1), "rs_d": round(dims["rs"], 1),
                    "t120": round(t120, 1), "entry": round(entry, 1),
                    "fwd10": fwd(j + 10), "fwd20": fwd(j + 20), "fwd60": fwd(j + 60), "fwd120": fwd(j + 120),
                    "mfe10": mfe(j + 10), "mfe20": mfe(j + 20), "mfe60": mfe(j + 60), "mfe120": mfe(j + 120),
                    "mae10": mae(j + 10), "mae20": mae(j + 20), "mae60": mae(j + 60), "mae120": mae(j + 120),
                })
        logf.write(f"{code} n={len(df)} ev={len(events)} t={time.time()-t0:.1f}s\n")
    logf.close()
    reader.close()
    return samples


def top10_thresholds(reader, signal_dates):
    """每个信号日：全市场可投资池未来120日毛收益的 P90 阈值（Top10% 分界）"""
    sd = np.array(sorted(set(signal_dates)))
    buckets = {d: [] for d in sd}
    for code, f in reader.frames.items():
        n = len(f)
        if n < 130:
            continue
        dts = f.trade_date.astype(str).to_numpy()
        cls = f.close.to_numpy(dtype=float)
        pos = np.searchsorted(dts, sd, side="left")
        posc = np.clip(pos, 0, n - 1)
        valid = (posc < n - 120) & (dts[posc] == sd) & (cls[posc] > 0)
        for k in np.where(valid)[0]:
            p = pos[k]
            buckets[sd[k]].append(cls[p + 120] / cls[p] - 1.0)
    return {d: (float(np.percentile(v, 90)) if len(v) >= 30 else np.nan) for d, v in buckets.items()}


def _pct(x):
    return f"{x * 100:+.2f}%" if x == x else "-"


HORIZONS = ((10, "T+10"), (20, "T+20"), (60, "T+60"), (120, "T+120"))


def stats_block(sub, label):
    """四周期统计：均值/中位/P90/P10、胜率、盈亏比、MFE/MAE、右尾概率"""
    print(f"\n### {label}  样本={len(sub)}")
    if sub.empty:
        return
    for k, tag in HORIZONS:
        d = sub[f"fwd{k}"].dropna()
        if d.empty:
            print(f"  {tag}: 无完整样本")
            continue
        wins = d[d > 0]
        losses = d[d < 0]
        pl = (wins.mean() / abs(losses.mean())) if len(wins) and len(losses) else float("nan")
        mf = sub[f"mfe{k}"].dropna()
        ma = sub[f"mae{k}"].dropna()
        print(f"  {tag}: 均值={_pct(d.mean())} 中位={_pct(d.median())} P90={_pct(d.quantile(0.9))} P10={_pct(d.quantile(0.1))} "
              f"胜率={(d > 0).mean() * 100:.1f}% 盈亏比={pl:.2f} "
              f"MFE均值={_pct(mf.mean())} MAE均值={_pct(ma.mean())} (n={len(d)})")
        print(f"    右尾: ≥20%={(d >= 0.2).mean() * 100:.1f}% ≥40%={(d >= 0.4).mean() * 100:.1f}% "
              f"≥60%={(d >= 0.6).mean() * 100:.1f}% ≥100%={(d >= 1.0).mean() * 100:.1f}%")


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
    print(f"[bt5] 股池={len(stock_list)} workers={WORKERS} 事件口径=120日分位AND≥P99", flush=True)
    mdates, mvals = reader.market_curve(DATE_END)
    anchors = {}
    for label, (code, adate) in ANCHORS.items():
        anchors[label] = anchor_features(reader.bars_sql(code, DATE_END), adate)
    batches = [stock_list[i::WORKERS] for i in range(WORKERS)]
    batches = [b for b in batches if b]
    print("[bt5] 并行扫描启动（V5 状态机 + 四周期评价）...", flush=True)
    with Pool(WORKERS, initializer=_init_worker, initargs=(mdates, mvals, anchors)) as pool:
        async_res = pool.map_async(scan_batch, batches)
        print("[bt5] 主进程加载全市场（Top10 阈值基准）...", flush=True)
        reader.load_all(DATE_END, codes=[c for c, _ in stock_list], min_date="20230101", verbose=True)
        results = async_res.get()
    samples = [s for batch in results for s in batch]
    print(f"[bt5] 扫描完成 样本={len(samples)} 耗时={time.time() - t0:.0f}s", flush=True)
    S = pd.DataFrame(samples)
    if S.empty:
        print("无任何信号样本")
        reader.close()
        return
    print("[bt5] 计算 Top10 阈值（全市场可投资池未来120日 P90）...", flush=True)
    thr = top10_thresholds(reader, S.signal_date.tolist())
    S["thr120"] = S.signal_date.map(thr)
    reader.close()
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    S.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")
    print(f"[bt5] 明细已保存 {CSV_PATH}")

    print("\n" + "═" * 100)
    print("HVT-V3 四周期回测总览（毛收益，未扣成本；事件=120日分位双≥P99）")
    print("═" * 100)

    stats_block(S, "全样本（V5 有效信号）")
    for st, g in S.groupby("status"):
        stats_block(g, f"状态={st}")
    for tp, g in S.groupby("type"):
        stats_block(g, f"类型={tp}")

    print("\n" + "═" * 100)
    print("A/B 榜与 WATCH 对比（非 WATCH = PRIMARY_BUY/T120_ROCKET/CONFIRMED）")
    print("═" * 100)
    core_ab = S[(S.type == "CORE") & (S.status != "WATCH")]
    ext_ab = S[(S.type == "EXT") & (S.status != "WATCH")]
    mid_ab = S[(S.type == "MID") & (S.status != "WATCH")]
    watch = S[S.status == "WATCH"]
    stats_block(core_ab, "A榜 CORE（非WATCH）")
    stats_block(ext_ab, "B榜 EXT（非WATCH）")
    stats_block(mid_ab, "MID（非WATCH）")
    stats_block(watch, "C榜 WATCH")

    print("\n" + "═" * 100)
    print("按 HVT-V3 总分分层（评价分数阈值是否正向）")
    print("═" * 100)
    for lo, hi in ((85, 200), (80, 85), (75, 80), (70, 75), (WATCH_MIN_SCORE, 70)):
        g = S[(S.score >= lo) & (S.score < hi)]
        if len(g):
            stats_block(g, f"score∈[{lo},{hi})")

    print("\n" + "═" * 100)
    print("右尾捕获 vs 全市场（T+120 P90 阈值）")
    print("═" * 100)
    dd = S.dropna(subset=["fwd120", "thr120"])
    if len(dd):
        for tag, g in (("全样本", dd), ("A榜CORE", dd[(dd.type == "CORE") & (dd.status != "WATCH")]),
                       ("B榜EXT", dd[(dd.type == "EXT") & (dd.status != "WATCH")]),
                       ("T120_ROCKET", dd[dd.status == "T120_ROCKET"])):
            if len(g):
                cap = (g.fwd120 >= g.thr120).mean() * 100
                print(f"  {tag:<12} n={len(g):>5}  Top10捕获={cap:.1f}%  T120均值={_pct(g.fwd120.mean())}")

    print("\n" + "═" * 100)
    print("年度稳定性（T+20/T+60/T+120 均值 | ≥40%右尾概率）")
    print("═" * 100)
    S["year"] = S.signal_date.str[:4]
    for y, g in S.groupby("year"):
        for col, tag in (("fwd20", "T+20"), ("fwd60", "T+60"), ("fwd120", "T+120")):
            d = g[col].dropna()
            if len(d):
                print(f"  {y}: {tag}均值={_pct(d.mean())} ≥40%={(d >= 0.4).mean() * 100:.1f}% (n={len(d)})", end="  ")
        print()

    print(f"\n[bt5] 总耗时={time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
