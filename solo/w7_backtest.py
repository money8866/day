import sys
import time
sys.path.insert(0, r"D:\mystock\solo")
import numpy as np
import pandas as pd
from multiprocessing import Pool
from w7_second_wave_engine import (CacheReader, state_and_features,
                                   ANCHORS, anchor_features)

LOOKBACK = 250  # 回测口径固定 250 日（与已完成回测一致；引擎主扫描已改为 700 日）

DATE_END = "20260828"
EVENT_MIN_DATE = "20240101"
TRACK_DAYS = 60
REPEAT_GAP = 5
COST = 0.0035
LIMIT = int(sys.argv[1]) if len(sys.argv) > 1 else 0
WORKERS = 8

_REGIME_MAP = {}


def _init_regime(regime_map):
    global _REGIME_MAP
    _REGIME_MAP = regime_map


def precompute_regime_map(conn):
    """一次聚合全市场每日等权收益，构建 trade_date -> regime 映射（回测热点用，避免每次全表聚合）。"""
    q = """
        SELECT trade_date, AVG(pct_chg) AS m
        FROM stk_factor_pro
        WHERE pct_chg IS NOT NULL
        GROUP BY trade_date
    """
    df = pd.read_sql_query(q, conn).sort_values("trade_date").reset_index(drop=True)
    m = pd.to_numeric(df.m, errors="coerce").fillna(0.0) / 100.0
    cum = (1.0 + m).cumprod()
    dates = df.trade_date.astype(str).to_numpy()
    out = {}
    for i in range(len(df)):
        if i < 24:
            out[dates[i]] = "RANGE"
            continue
        r20 = cum.iloc[i] / cum.iloc[i - 20] - 1.0
        r60 = cum.iloc[i] / cum.iloc[i - 59] - 1.0 if i >= 59 else r20
        if r20 > 0.06 and r60 > 0.03:
            out[dates[i]] = "BULL"
        elif r20 > 0.02 or (r20 > -0.02 and r60 > 0):
            out[dates[i]] = "RECOVERY"
        elif r20 < -0.06 and r60 < -0.03:
            out[dates[i]] = "BEAR"
        else:
            out[dates[i]] = "RANGE"
    return out


def find_events(df):
    from numpy.lib.stride_tricks import sliding_window_view
    turn = pd.to_numeric(df.turnover_rate_f, errors="coerce").fillna(0).to_numpy(dtype=float)
    vol = pd.to_numeric(df.vol, errors="coerce").fillna(0).to_numpy(dtype=float)
    n = len(turn)
    if n <= LOOKBACK + 2:
        return []
    w_t = sliding_window_view(turn[:-1], LOOKBACK)
    w_v = sliding_window_view(vol[:-1], LOOKBACK)
    cur_t = turn[LOOKBACK:]
    cur_v = vol[LOOKBACK:]
    p_t = (w_t <= cur_t[:, None]).mean(axis=1) * 100
    p_v = (w_v <= cur_v[:, None]).mean(axis=1) * 100
    ep = np.maximum(p_t, p_v)
    idxs = np.where(ep >= 98.0)[0] + LOOKBACK
    dates = df["trade_date"].astype(str).to_numpy()
    out = []
    for i in idxs:
        if i >= len(df) - 2 or dates[i] < EVENT_MIN_DATE:
            continue
        out.append((int(i), float(ep[i - LOOKBACK])))
    return out


def signal_hit(state, pp_ok, lock):
    if state in ("SECOND_WAVE", "BREAKOUT_CONFIRM"):
        return True
    if state in ("DRYUP", "ABSORPTION", "RE_EXPANSION") and pp_ok and lock >= 70:
        return True
    return False


def scan_batch(codes_names):
    reader = CacheReader()
    reader.load_all(DATE_END, codes=[c for c, _ in codes_names], min_date="20230101", chunk=500, verbose=False)
    regime_cache = {}
    samples = []
    for code, name in codes_names:
        df = reader.bars(code, DATE_END)
        if len(df) < 320:
            continue
        last_sig_idx = -10 ** 9
        for i, ep in find_events(df):
            for j in range(i + 1, min(i + TRACK_DAYS, len(df) - 6)):
                d = str(df.iloc[j].trade_date)
                sub = df.iloc[:j + 1].copy()
                res = state_and_features(sub, i, ep)
                if not res:
                    continue
                base, state, pp, pp_ok, reexp, breakout, hard_fail, dd, pressure = res
                if not signal_hit(state, pp_ok, base["lock"]):
                    continue
                if j - last_sig_idx < REPEAT_GAP:
                    continue
                last_sig_idx = j
                buy = float(df.iloc[j].close)
                c5 = float(df.iloc[j + 5].close) / buy - 1 - COST if j + 5 < len(df) else np.nan
                c10 = float(df.iloc[j + 10].close) / buy - 1 - COST if j + 10 < len(df) else np.nan
                samples.append({
                    "code": code, "name": name, "event_date": str(df.iloc[i].trade_date),
                    "signal_date": str(df.iloc[j].trade_date), "state": state,
                    "pp": round(pp, 1), "cq": round(base["cq"], 1), "sds": round(base["sds"], 1),
                    "lock": round(base["lock"], 1), "ep": round(ep, 1),
                    "regime": regime_cache[d],
                    "close5": round(c5, 4) if np.isfinite(c5) else np.nan,
                    "close10": round(c10, 4) if np.isfinite(c10) else np.nan,
                })
    reader.close()
    return samples


def stat(sub, label):
    if sub.empty:
        print(f"\n## {label}: 无样本")
        return
    print(f"\n## {label}  样本={len(sub)}")
    for col in ("close5", "close10"):
        d = sub[col].dropna()
        if d.empty:
            continue
        win = (d > 0).mean() * 100
        gain = d[d > 0].mean() if (d > 0).any() else np.nan
        loss = d[d < 0].mean() if (d < 0).any() else np.nan
        pl = abs(gain / loss) if gain == gain and loss == loss and loss != 0 else float("nan")
        print(f"  {col}: 胜率={win:.1f}% 均值={d.mean() * 100:.2f}% 中位={d.median() * 100:.2f}% 盈亏比={pl:.2f}")


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
    print("[bt] 预计算市场环境映射...", flush=True)
    regime_map = precompute_regime_map(reader.conn)
    print(f"[bt] regime_map={len(regime_map)}天", flush=True)
    print(f"[bt] 股池={len(stock_list)} workers={WORKERS} 开始并行扫描...", flush=True)
    batches = [stock_list[i::WORKERS] for i in range(WORKERS)]
    batches = [b for b in batches if b]
    with Pool(WORKERS, initializer=_init_regime, initargs=(regime_map,)) as pool:
        results = pool.map(scan_batch, batches)
    samples = [s for batch in results for s in batch]
    print(f"[bt] 扫描完成 样本={len(samples)} 耗时={time.time()-t0:.0f}s", flush=True)

    S = pd.DataFrame(samples)
    S.to_csv(r"D:\mystock\solo\report_daily\w7_backtest_signals.csv", index=False, encoding="utf-8-sig")

    if S.empty:
        print("无任何信号样本")
    else:
        stat(S, "全部信号")
        print("\n== 按状态分层 ==")
        for s_, g in S.groupby("state"):
            stat(g, f"状态={s_}")
        print("\n== 按年度分层 ==")
        S["year"] = S["signal_date"].str[:4]
        for y, g in S.groupby("year"):
            stat(g, f"年份={y}")
        print("\n== 按市场环境分层 ==")
        for r_, g in S.groupby("regime"):
            stat(g, f"regime={r_}")
        print("\n== 状态分布 ==")
        print(S["state"].value_counts().to_string())
        print("\n== 状态×年度 样本数 ==")
        print(S.pivot_table(index="state", columns="year", values="code", aggfunc="count", fill_value=0).to_string())
        print("\n== 状态×年度 close5均值% ==")
        pv = S.groupby(["state", "year"])["close5"].mean() * 100
        print(pv.round(2).to_string())

    # 锚点对照
    print("\n## 锚点对照")
    for label, (code, adate) in ANCHORS.items():
        adf = reader.bars_sql(code, DATE_END)
        a = anchor_features(adf, adate)
        if not a:
            print(f"{label}: 锚点事件不成立")
            continue
        i = int(adf.index[adf.trade_date.astype(str) == adate][0])
        ep = a["event_percentile"]
        for j in range(i + 1, min(i + TRACK_DAYS, len(adf) - 6)):
            d = str(adf.iloc[j].trade_date)
            res = state_and_features(adf.iloc[:j + 1].copy(), i, ep)
            if not res:
                continue
            base, state, pp, pp_ok, reexp, breakout, hard_fail, dd, pressure = res
            if state == "SECOND_WAVE" and pp_ok:
                buy = float(adf.iloc[j].close)
                c5 = float(adf.iloc[j + 5].close) / buy - 1 if j + 5 < len(adf) else np.nan
                c10 = float(adf.iloc[j + 10].close) / buy - 1 if j + 10 < len(adf) else np.nan
                print(f"{label} 事件{adate} → 首个SECOND_WAVE信号 {d} PP={pp:.0f} close5={c5*100:.1f}% close10={c10*100:.1f}%")
                break
    reader.close()


if __name__ == "__main__":
    main()
