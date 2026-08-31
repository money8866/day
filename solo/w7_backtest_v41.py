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
                                   finite, MAIN_EVENT_PCT)

# ===== V4.1 T120 回测：A/B/C 门控实验 =====
# A = 现有模型（signal_hit 布尔门控：SECOND_WAVE/BREAKOUT_CONFIRM 直接入场，缩量态需 PP10+锁筹）
# B = A + Acceptance/HVT_SIM 强化（在 A 基础上加 alpha_hvt>=75）
# C = T120_ALPHA/ENTRY 拆分（t120>=70 且无重大风险入池；t120>=85 高潜，entry 仅定 PRIMARY/ROCKET）
# 评价：T+20/60/120 均值/中位/P90/≥20%/30%/50%、T120 Top10 Capture Rate（全市场可投资池 P90 阈值）
# 口径：全历史分位天量（DATA_START 起，与引擎主扫描一致）；收益为毛收益（未扣成本）；sector 维度固定中性 50（三组共同偏移，不影响对比）

DATE_END = "20260828"
EVENT_MIN_DATE = "20240101"
TRACK_DAYS = 60
REPEAT_GAP = 5
MIN_HIST = 320
WORKERS = 8
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 0
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_daily", "w7_backtest_v41_signals.csv")

_MKT_DATES = None
_MKT_VALS = None
_ANCHORS = {}


def _init_worker(mdates, mvals, anchors):
    global _MKT_DATES, _MKT_VALS, _ANCHORS
    _MKT_DATES, _MKT_VALS, _ANCHORS = mdates, mvals, anchors


def find_events(df):
    """全历史分位天量（与引擎 extreme_event 主口径一致：数据起点至事件前一日，量能+换手双≥P99）"""
    dates = df["trade_date"].astype(str).to_numpy()
    turn = pd.to_numeric(df.turnover_rate_f, errors="coerce").fillna(0).to_numpy(dtype=float)
    vol = pd.to_numeric(df.vol, errors="coerce").fillna(0).to_numpy(dtype=float)
    n = len(turn)
    if n < 252:
        return []
    p_t = pd.Series(turn).expanding().rank(pct=True).to_numpy() * 100.0
    p_v = pd.Series(vol).expanding().rank(pct=True).to_numpy() * 100.0
    ep = np.maximum(p_t, p_v)
    out = []
    for i in range(250, n - 2):
        if p_t[i] >= MAIN_EVENT_PCT and p_v[i] >= MAIN_EVENT_PCT and dates[i] >= EVENT_MIN_DATE:
            out.append((int(i), float(ep[i])))
    return out


def signal_hit(state, pp_ok, lock):
    # A 组门控：现有模型口径
    if state in ("SECOND_WAVE", "BREAKOUT_CONFIRM"):
        return True
    if state in ("DRYUP", "ABSORPTION", "RE_EXPANSION") and pp_ok and lock >= 70:
        return True
    return False


def scan_batch(codes_names):
    reader = CacheReader()
    reader.load_all(DATE_END, codes=[c for c, _ in codes_names], min_date="20230101", chunk=500, verbose=False)
    reader.load_fina()
    mkt = MarketCtx(_MKT_DATES, _MKT_VALS)
    samples = []
    for code, name in codes_names:
        df = reader.bars(code, DATE_END)
        if len(df) < MIN_HIST:
            continue
        events = find_events(df)
        if not events:
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
                base, state, pp, pp_ok, reexp, breakout, major_risk, dd, pressure = res
                d = str(df.iloc[j].trade_date)
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
                entry, _ = entry_score_v2(df, j, pp, pp_ok, reexp, breakout, event_low, mkt)
                a_hit = signal_hit(state, pp_ok, base["lock"])
                b_hit = a_hit and dims["hvt"] >= 75.0
                c_hit = (not major_risk) and t120 >= 70.0 and state not in ("FAILED", "DISTRIBUTION")
                if not (a_hit or b_hit or c_hit):
                    continue
                if j - last_sig_idx < REPEAT_GAP:
                    continue
                last_sig_idx = j
                buy = closes[j]

                def fwd(k):
                    return closes[k] / buy - 1.0 if k < n and buy > 0 else np.nan

                samples.append({
                    "code": code, "name": name,
                    "event_date": str(df.iloc[i].trade_date), "signal_date": d,
                    "state": state, "pp": round(pp, 1), "lock": round(base["lock"], 1),
                    "acc": round(base["acceptance"], 1), "cq": round(base["cq"], 1),
                    "sds": round(base["sds"], 1), "hvt_sim": round(hvt, 1),
                    "alpha_hvt": round(dims["hvt"], 1),
                    "trend_d": round(dims["trend"], 1), "fina_d": round(dims["fina"], 1),
                    "rs_d": round(dims["rs"], 1), "upside_d": round(dims["upside"], 1),
                    "t120": round(t120, 1), "entry": round(entry, 1),
                    "major_risk": bool(major_risk),
                    "a_hit": bool(a_hit), "b_hit": bool(b_hit), "c_hit": bool(c_hit),
                    "fwd20": fwd(j + 20), "fwd60": fwd(j + 60), "fwd120": fwd(j + 120),
                })
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


def stat_group(sub, label):
    if sub is None or sub.empty:
        print(f"\n## {label}: 无样本")
        return
    print(f"\n## {label}  样本={len(sub)}")
    for col, tag in (("fwd20", "T+20"), ("fwd60", "T+60"), ("fwd120", "T+120")):
        d = sub[col].dropna()
        if d.empty:
            print(f"  {tag}: 无完整样本")
            continue
        print(f"  {tag}: 均值={d.mean() * 100:+.2f}% 中位={d.median() * 100:+.2f}% P90={d.quantile(0.9) * 100:+.1f}% "
              f"胜率={(d > 0).mean() * 100:.1f}% ≥20%={(d >= 0.2).mean() * 100:.1f}% "
              f"≥30%={(d >= 0.3).mean() * 100:.1f}% ≥50%={(d >= 0.5).mean() * 100:.1f}% (n={len(d)})")
    if "thr120" in sub.columns:
        dd = sub.dropna(subset=["fwd120", "thr120"])
        if len(dd):
            cap = (dd.fwd120 >= dd.thr120).mean() * 100
            print(f"  Top10 Capture(T120): {cap:.1f}% (n={len(dd)})")


def compare_row(tag, g):
    def m(col):
        d = g[col].dropna()
        return d.mean() if len(d) else float("nan")

    def med(col):
        d = g[col].dropna()
        return d.median() if len(d) else float("nan")

    def ge(col, th):
        d = g[col].dropna()
        return (d >= th).mean() if len(d) else float("nan")

    dd = g.dropna(subset=["fwd120", "thr120"]) if "thr120" in g.columns else pd.DataFrame()
    cap = (dd.fwd120 >= dd.thr120).mean() if len(dd) else float("nan")
    print(f"{tag:<8}{len(g):>8}{_pct(m('fwd20')):>10}{_pct(m('fwd60')):>10}{_pct(m('fwd120')):>10}"
          f"{_pct(med('fwd120')):>10}{_pct(ge('fwd120', 0.3)):>10}{_pct(ge('fwd120', 0.5)):>10}"
          f"{cap * 100:>9.1f}%")


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
    print(f"[bt] 股池={len(stock_list)} workers={WORKERS}", flush=True)
    mdates, mvals = reader.market_curve(DATE_END)
    anchors = {}
    for label, (code, adate) in ANCHORS.items():
        anchors[label] = anchor_features(reader.bars_sql(code, DATE_END), adate)
    batches = [stock_list[i::WORKERS] for i in range(WORKERS)]
    batches = [b for b in batches if b]
    print("[bt] 并行扫描启动（A/B/C 三组同场评分）...", flush=True)
    with Pool(WORKERS, initializer=_init_worker, initargs=(mdates, mvals, anchors)) as pool:
        async_res = pool.map_async(scan_batch, batches)
        print("[bt] 主进程加载全市场（Top10 阈值基准）...", flush=True)
        reader.load_all(DATE_END, codes=[c for c, _ in stock_list], min_date="20230101", verbose=True)
        results = async_res.get()
    samples = [s for batch in results for s in batch]
    print(f"[bt] 扫描完成 样本={len(samples)} 耗时={time.time() - t0:.0f}s", flush=True)
    S = pd.DataFrame(samples)
    if S.empty:
        print("无任何信号样本")
        reader.close()
        return
    print("[bt] 计算 Top10 阈值（全市场可投资池未来120日 P90）...", flush=True)
    thr = top10_thresholds(reader, S.signal_date.tolist())
    S["thr120"] = S.signal_date.map(thr)
    reader.close()
    os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
    S.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")

    print("\n== A/B/C 门控对比（若C明显优于A/B则保留V4.1）==")
    print(f"{'组':<8}{'样本':>8}{'T20均值':>10}{'T60均值':>10}{'T120均值':>10}{'T120中位':>10}{'≥30%':>10}{'≥50%':>10}{'Top10捕获':>10}")
    compare_row("A", S[S.a_hit])
    compare_row("B", S[S.b_hit])
    compare_row("C", S[S.c_hit])
    compare_row("C高潜85", S[S.c_hit & (S.t120 >= 85)])
    compare_row("C高潜85+买点", S[S.c_hit & (S.t120 >= 85) & (S.entry >= 80)])
    print("\n== 基线对照（非门控参考）==")
    compare_row("全样本", S)
    compare_row("T120>=85", S[S.t120 >= 85])
    compare_row("A∧C", S[S.a_hit & S.c_hit])

    print("\n== C 组按 t120 分层 ==")
    for lo, hi in ((85, 200), (80, 85), (75, 80), (70, 75), (0, 70)):
        g = S[S.c_hit & (S.t120 >= lo) & (S.t120 < hi)]
        if len(g):
            stat_group(g, f"t120∈[{lo},{hi})")

    print("\n== A 组按状态分层 ==")
    for s_, g in S[S.a_hit].groupby("state"):
        stat_group(g, f"状态={s_}")

    print("\n== 年度稳定性（T120均值 | Top10捕获）==")
    S["year"] = S.signal_date.str[:4]
    for y, g in S.groupby("year"):
        aa, cc = g[g.a_hit], g[g.c_hit]
        dd = cc.dropna(subset=["fwd120", "thr120"])
        cap = (dd.fwd120 >= dd.thr120).mean() * 100 if len(dd) else float("nan")
        m120 = cc.fwd120.dropna()
        print(f"{y}: A样本={len(aa)} C样本={len(cc)} C_T120均值={_pct(m120.mean() if len(m120) else float('nan'))} C_Top10={cap:.1f}%")

    print("\n== ENTRY 有效性检验（C组内，验证买点分数是否正向预测）==")
    ch = S[S.c_hit].dropna(subset=["fwd20"])
    if len(ch) >= 50:
        med = ch.entry.median()
        lo_, hi_ = ch[ch.entry < med], ch[ch.entry >= med]
        print(f"ENTRY分界={med:.0f}: 低半区 T20={_pct(lo_.fwd20.mean())} T120={_pct(lo_.fwd120.mean())} n={len(lo_)} | "
              f"高半区 T20={_pct(hi_.fwd20.mean())} T120={_pct(hi_.fwd120.mean())} n={len(hi_)}")

    print(f"\n[bt] 明细已保存 {CSV_PATH}")
    print(f"[bt] 总耗时={time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
