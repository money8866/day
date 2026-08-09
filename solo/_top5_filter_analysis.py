# -*- coding: utf-8 -*-
"""Top5 优化空间分析: 特征分桶胜率 + 过滤方案 A/B (同等大盘环境)

框架与 _strongbuy_vs_top5_backtest.py 一致:
闸门=三指数20日动量>+3%, 买入=次日开盘, 持有T+5, 盘中-7%止损.

输出:
1) 各特征维度分桶胜率表 (评分/距MA20/回撤比例/今日量比/当日涨幅/区间振幅)
2) 过滤方案 A/B: 基准纯评分Top5 vs 若干过滤组合后取Top5
"""
import os, sys, time, pickle
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

TDX_BT = r"d:\mystock\tdx_backtest"
SOLO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SOLO_DIR)
sys.path.insert(0, TDX_BT)
from data_loader import load_kline, iter_all_day_files, tdx_filename_to_ts_code
from strategy_backtest import load_stock_names
from volume_surge_strategy import (precompute_indicators,
                                   volume_surge_strategy_vectorized,
                                   VolSurgeFilters)
sys.path.insert(0, SOLO_DIR)
from winrate_vs_market_env import build_market_env, INDEX_CODES

MOM_THRESHOLD = 3.0
HOLD_DAYS = 5
MAX_DAILY = 5
STOP = -7.0
START, END = "20240101", "20260807"


def signal_features(df_pre, i, score):
    """信号日特征提取. 返回 dict"""
    C = df_pre["close"].values
    H = df_pre["high"].values
    L = df_pre["low"].values
    VOL = df_pre["vol"].values
    ma20 = df_pre["ma20"].values
    vol_ratio = df_pre["vol_ratio"].values
    macd_bar = df_pre["macd_bar"].values
    if i < 5:
        return None

    pos_ma20 = (C[i] / ma20[i] - 1) * 100 if ma20[i] > 0 else 0.0
    today_vr = float(vol_ratio[i]) if not np.isnan(vol_ratio[i]) else 0.0

    # ABC 回撤结构 (与 vectorized 第6/8步一致)
    low_arr = L[max(0, i - 200):i + 1]
    vol_200_start = max(0, i - 199)
    vol_200 = VOL[vol_200_start:i + 1]
    peak_vol_idx = int(np.argmax(vol_200))
    peak_vol_price = float(H[vol_200_start + peak_vol_idx])
    a_low = float(np.min(low_arr[:peak_vol_idx + 1]))
    a_gain = (peak_vol_price / a_low - 1) * 100 if a_low > 0 else 0
    if peak_vol_idx < len(low_arr) - 3:
        b_low = float(np.min(low_arr[peak_vol_idx:]))
        b_drop = (1 - b_low / peak_vol_price) * 100
        retrace_ratio = b_drop / a_gain * 100 if a_gain > 0 else 0
    else:
        retrace_ratio = 0.0

    # 区间特征
    start = max(0, i - 200)
    close_arr = C[start:i + 1]
    high_arr = H[start:i + 1]
    amplitude = (high_arr - low_arr) / np.maximum(close_arr, 0.01) * 100
    avg_amp = float(np.mean(amplitude[-120:]))
    range_swing = (float(np.max(high_arr)) / float(np.min(low_arr)) - 1) * 100

    cur_bar = float(macd_bar[i])
    prev_bar = float(macd_bar[i - 1])
    pct_chg = float(df_pre.iloc[i]["pct_chg"])

    return {"score": float(score), "pos_ma20": pos_ma20,
            "retrace": retrace_ratio, "vol_ratio": today_vr,
            "pct_chg": pct_chg, "amp": avg_amp, "swing": range_swing,
            "macd": cur_bar, "macd_prev": prev_bar}


def trade(df, i_buy, buy_close, hold):
    for j in range(i_buy + 1, min(i_buy + hold + 1, len(df))):
        if df.iloc[j]["low"] / buy_close - 1 <= STOP / 100.0:
            return STOP
    if i_buy + hold < len(df):
        return (df.iloc[i_buy + hold]["close"] / buy_close - 1) * 100.0
    return None


def bucket_stats(rows, key, buckets, labels):
    """分桶统计: rows=[(feat, ret)], feat[key]分桶"""
    out = []
    for lo, hi in buckets:
        sel = [r for f, r in rows if lo <= f[key] < hi]
        if not sel:
            out.append((labels[buckets.index((lo, hi))], 0, np.nan, np.nan))
            continue
        arr = np.array(sel)
        out.append((labels[buckets.index((lo, hi))], len(arr),
                    (arr > 0).mean() * 100, arr.mean()))
    return out


def run():
    vf = VolSurgeFilters()
    t0 = time.time()
    print("=" * 60)
    print("  Top5 优化空间分析 (T+5, 止损-7%, 三指数动量>+3%闸门)")
    print(f"  区间: {START} ~ {END}")
    print("=" * 60)
    load_stock_names()
    dt = datetime.strptime(START, "%Y%m%d")
    load_start = (dt - timedelta(days=400)).strftime("%Y%m%d")

    index_dfs = {}
    for code in INDEX_CODES:
        try:
            df = load_kline(code, start_date=load_start, end_date=END)
            if not df.empty:
                index_dfs[code] = df
        except Exception as e:
            print(f"[Market] {code} 加载失败: {e}")
    env_df = build_market_env(index_dfs) if index_dfs else pd.DataFrame()

    kline_dict = {}
    for path in iter_all_day_files(markets=("SH", "SZ")):
        ts_code = tdx_filename_to_ts_code(path)
        if not ts_code or ts_code[0] not in "630":
            continue
        df = load_kline(ts_code, start_date=load_start, end_date=END)
        if df.empty or len(df) < 180:
            continue
        kline_dict[ts_code] = precompute_indicators(df)
    print(f"[Load] {len(kline_dict)} 只, 耗时 {time.time()-t0:.1f}s")

    t0 = time.time()
    signals_dict = {}
    cache_path = os.path.join(SOLO_DIR, "_cache_signals.pkl")
    if os.path.exists(cache_path):
        with open(cache_path, "rb") as f:
            cache = pickle.load(f)
        if cache.get("dtype") == "float64" and cache.get("pool") == "630":
            signals_dict = cache["signals_dict"]
            print(f"[Signal] 从缓存加载 {len(signals_dict)} 只有信号")
        else:
            print("[Signal] 缓存池子/版本不匹配(旧: %s %s), 重新生成"
                  % (cache.get("dtype"), cache.get("pool")))
    if not signals_dict:
        for ts_code, df_pre in kline_dict.items():
            sig = volume_surge_strategy_vectorized(df_pre, ts_code, vf)
            if sig.any():
                signals_dict[ts_code] = sig
        with open(cache_path, "wb") as f:
            pickle.dump({"dtype": "float64", "pool": "630",
                         "signals_dict": signals_dict}, f)
        print(f"[Signal] 生成 {len(signals_dict)} 只有信号, 已缓存, 耗时 {time.time()-t0:.1f}s")

    all_dates = set()
    for df in kline_dict.values():
        all_dates.update(df["trade_date"].tolist())
    trade_dates = sorted(d for d in all_dates if START <= d <= END)
    date_idx_map = {c: dict(zip(d["trade_date"], d.index))
                    for c, d in kline_dict.items()}

    # --- 逐日收集候选信号 + T+5 收益 ---
    rows = []            # (feat_dict, ret) 全量候选
    daily = []           # [(td, [(ts_code, score, feat, ret)])]
    for td in trade_dates:
        env = env_df.loc[td] if td in env_df.index else None
        if env is None:
            continue
        if float(env["mom20_avg"]) <= MOM_THRESHOLD:
            continue
        cands = []
        for ts_code, sig in signals_dict.items():
            idx_map = date_idx_map.get(ts_code)
            if not idx_map:
                continue
            i = idx_map.get(td)
            if i is None or i >= len(sig):
                continue
            sc = sig[i]
            if sc <= 0:
                continue
            feat = signal_features(kline_dict[ts_code], i, float(sc))
            if feat is None:
                continue
            df = kline_dict[ts_code]
            if i + 1 >= len(df):
                continue
            r = trade(df, i + 1, float(df.iloc[i + 1]["open"]), HOLD_DAYS)
            if r is None:
                continue
            rows.append((feat, r))
            cands.append((ts_code, float(sc), feat, r))
        if cands:
            daily.append((td, cands))
    print(f"[Data] 闸门通过日 {len(daily)}, 全量候选 {len(rows)} 笔, 耗时 {time.time()-t0:.1f}s")

    # --- 评分分布 (验证dtype修复) ---
    scores = np.array([f["score"] for f, _ in rows])
    print("\n  ◆ 评分分布: min=%.1f  p25=%.1f  p50=%.1f  p75=%.1f  max=%.1f"
          % (scores.min(), np.percentile(scores, 25), np.percentile(scores, 50),
             np.percentile(scores, 75), scores.max()))

    # --- 特征分桶胜率 ---
    def _prt(title, key, buckets, labels):
        print(f"\n  ◆ {title}")
        print(f"    {'分段':<14}{'笔数':>6}{'胜率':>8}{'均收益':>9}")
        for lb, n, wr, ar in bucket_stats(rows, key, buckets, labels):
            if n == 0:
                print(f"    {lb:<14}{0:>6}    —      —")
            else:
                print(f"    {lb:<14}{n:>6}{wr:>7.1f}%{ar:>+8.2f}%")

    _prt("评分分段", "score",
         [(65, 70), (70, 75), (75, 80), (80, 85), (85, 90), (90, 200)],
         ["65-70", "70-75", "75-80", "80-85", "85-90", ">=90"])
    _prt("距MA20(%)", "pos_ma20",
         [(0, 2), (2, 5), (5, 8), (8, 12), (12, 999)],
         ["0-2", "2-5", "5-8", "8-12", ">=12"])
    _prt("回撤比例(%)", "retrace",
         [(0, 20), (20, 30), (30, 40), (40, 50)],
         ["<20", "20-30", "30-40", "40-50"])
    _prt("今日量比", "vol_ratio",
         [(0, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 999)],
         ["<1.0", "1.0-1.5", "1.5-2.0", "2.0-3.0", ">=3.0"])
    _prt("当日涨幅(%)", "pct_chg",
         [(1, 2), (2, 3), (3, 5), (5, 7), (7, 10)],
         ["1-2", "2-3", "3-5", "5-7", "7-9.5"])
    _prt("区间振幅(%)", "swing",
         [(35, 50), (50, 70), (70, 999)],
         ["35-50", "50-70", ">=70"])

    # --- 过滤方案 A/B: 过滤后取当日 Top5 ---
    def _sim_filter(flt):
        """flt(feat)->bool; 返回过滤后每日Top5收益列表"""
        rets = []
        for td, cands in daily:
            sel = [c for c in cands if flt(c[2])]
            if not sel:
                continue
            sel.sort(key=lambda x: -x[1])
            for _, _, _, r in sel[:MAX_DAILY]:
                rets.append(r)
        return rets

    def _sim_rank(rank_key):
        """按特征排序取当日 Top5 (rank_key(feat)->值, 升序)"""
        rets = []
        for td, cands in daily:
            if not cands:
                continue
            sel = sorted(cands, key=lambda c: rank_key(c[2]))
            for _, _, _, r in sel[:MAX_DAILY]:
                rets.append(r)
        return rets

    def _summary(rs):
        if not rs:
            return 0, np.nan, np.nan, np.nan, np.nan
        arr = np.array(rs)
        wins = arr[arr > 0]
        losses = arr[arr <= 0]
        pl = wins.mean() / abs(losses.mean()) if len(losses) else np.inf
        return len(arr), (arr > 0).mean() * 100, arr.mean(), np.median(arr), pl

    plans = [
        ("base 纯评分Top5(基准)", lambda f: True),
        ("f1 评分>=75", lambda f: f["score"] >= 75),
        ("f2 评分>=80", lambda f: f["score"] >= 80),
        ("f3 距MA20<=8%", lambda f: f["pos_ma20"] <= 8),
        ("f4 评分>=75 且 距MA20<=10", lambda f: f["score"] >= 75 and f["pos_ma20"] <= 10),
        ("f5 浅回调(<30)", lambda f: f["retrace"] < 30),
        ("f6 评分>=75 且 浅回调 且 量比>=1.0",
         lambda f: f["score"] >= 75 and f["retrace"] < 30 and f["vol_ratio"] >= 1.0),
        ("f7 当日涨幅2-7%", lambda f: 2 <= f["pct_chg"] < 7),
        ("f8 评分>=75 且 当日涨幅2-7%",
         lambda f: f["score"] >= 75 and 2 <= f["pct_chg"] < 7),
        ("f9 距MA20<=8 且 浅回调 且 评分>=70",
         lambda f: f["pos_ma20"] <= 8 and f["retrace"] < 30 and f["score"] >= 70),
        ("f10 距MA20<=5% 过滤", lambda f: f["pos_ma20"] <= 5),
        ("f13 距MA20<=8 且 涨幅>=5%",
         lambda f: f["pos_ma20"] <= 8 and f["pct_chg"] >= 5),
        ("f14 距MA20<=8 且 量比>=1.5",
         lambda f: f["pos_ma20"] <= 8 and f["vol_ratio"] >= 1.5),
        ("f15 距MA20<=8 且 浅回调 且 量比>=1.0",
         lambda f: f["pos_ma20"] <= 8 and f["retrace"] < 30 and f["vol_ratio"] >= 1.0),
    ]
    rank_plans = [
        ("r1 按距MA20升序取Top5", lambda f: f["pos_ma20"]),
        ("r2 按距MA20升序(<=8)取Top5",
         lambda f: f["pos_ma20"] if f["pos_ma20"] <= 8 else 999),
        ("r3 复合分=评分-2*距MA20 排序", lambda f: -f["score"] + 2 * f["pos_ma20"]),
        ("r4 复合分=评分-3*距MA20 排序", lambda f: -f["score"] + 3 * f["pos_ma20"]),
    ]
    print("\n" + "=" * 84)
    print("  过滤方案 A/B (闸门通过日每日Top5, 同入场同止损)")
    print("=" * 84)
    print(f"  {'方案':<34}{'笔数':>6}{'胜率':>8}{'均收益':>9}{'中位':>8}{'盈亏比':>7}")
    out_rows = []
    for name, flt in plans:
        rs = _sim_filter(flt)
        n, wr, ar, md, pl = _summary(rs)
        if n == 0:
            print(f"  {name:<34}{0:>6}   无信号")
            continue
        print(f"  {name:<34}{n:>6}{wr:>7.1f}%{ar:>+8.2f}%{md:>+7.2f}%{pl:>6.2f}")
        out_rows.append({"plan": name, "trades": n, "winrate": round(wr, 1),
                         "avg_ret": round(ar, 2), "median": round(md, 2),
                         "pl_ratio": round(pl, 2)})
    print("  --- 排序方案 (改排序逻辑) ---")
    for name, rank_key in rank_plans:
        rs = _sim_rank(rank_key)
        n, wr, ar, md, pl = _summary(rs)
        if n == 0:
            print(f"  {name:<34}{0:>6}   无信号")
            continue
        print(f"  {name:<34}{n:>6}{wr:>7.1f}%{ar:>+8.2f}%{md:>+7.2f}%{pl:>6.2f}")
        out_rows.append({"plan": name, "trades": n, "winrate": round(wr, 1),
                         "avg_ret": round(ar, 2), "median": round(md, 2),
                         "pl_ratio": round(pl, 2)})

    out_dir = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "tdx_backtest", "output"))
    os.makedirs(out_dir, exist_ok=True)
    pd.DataFrame(out_rows).to_csv(os.path.join(out_dir, "top5_filter_compare.csv"),
                                  index=False, encoding="utf-8-sig")
    print(f"\n✅ 过滤方案对比已保存: {os.path.join(out_dir, 'top5_filter_compare.csv')}")


if __name__ == "__main__":
    run()
