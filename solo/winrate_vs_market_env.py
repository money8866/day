# -*- coding: utf-8 -*-
"""
量能爆发+宽幅震荡策略 — 胜率 vs 大盘环境相关性回测

复用 tdx_backtest/volume_surge_strategy.py 的信号生成逻辑, 但不再按大盘过滤,
而是对每个买入信号记录当日大盘环境特征, 分组统计 T+N 胜率, 并用 Spearman
相关系数量化"胜率与大环境的相关性".

大盘环境 = 上证指数(000001.SH) + 沪深300(000300.SH) + 创业板指(399006.SZ) 综合评分:
  - bull_count:   三指数 MA20>MA60 多头数量 (0~3)
  - mom20_avg:    三指数 20日动量均值 (%)
  - mom5_avg:     三指数 5日动量均值 (%)
  - day_pct_avg:  三指数当日涨跌幅均值 (%)
  - slope_avg:    三指数 MA20 近10天斜率均值 (%)
  - breadth:      市场宽度 (当日上涨家数占比)

数据源: 通达信本地日线 (C:\\new_tdx)

使用方式:
    python winrate_vs_market_env.py
    python winrate_vs_market_env.py --start 20240101 --end 20260807 --hold 5
    python winrate_vs_market_env.py --max-stocks 500   # 调试: 限加载股票数
"""
from __future__ import annotations
import os, sys, time, argparse
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

TDX_BT = r"d:\mystock\tdx_backtest"
sys.path.insert(0, TDX_BT)
from data_loader import load_kline, iter_all_day_files, tdx_filename_to_ts_code
from strategy_backtest import load_stock_names
from volume_surge_strategy import (precompute_indicators,
                                   volume_surge_strategy_vectorized,
                                   VolSurgeFilters)

# 大盘指数 (综合评分用)
INDEX_CODES = ["000001.SH", "000300.SH", "399006.SZ"]
INDEX_NAMES = {"000001.SH": "上证", "000300.SH": "沪深300", "399006.SZ": "创业板"}


# =========================================================
# 大盘环境特征提取 (三指数综合)
# =========================================================
def _index_feature(df: pd.DataFrame) -> pd.DataFrame:
    """单指数日线 → 环境特征 (index=trade_date)"""
    df = precompute_indicators(df).copy()
    C = df["close"].values
    H = df["high"].values
    ma20 = df["ma20"].values
    ma60 = df["ma60"].values
    n = len(C)

    slope = np.full(n, np.nan)
    if n >= 11:
        slope[10:] = (ma20[10:] / ma20[:-10] - 1) * 100
    mom5 = np.full(n, np.nan)
    if n >= 6:
        mom5[6:] = (C[6:] / C[:-6] - 1) * 100
    mom20 = np.full(n, np.nan)
    if n >= 21:
        mom20[21:] = (C[21:] / C[:-21] - 1) * 100

    return pd.DataFrame({
        "trade_date": df["trade_date"].values,
        "ma20_gt_ma60": (ma20 > ma60).astype(int),
        "slope": slope,
        "day_pct": df["pct_chg"].values,
        "mom5": mom5,
        "mom20": mom20,
    }).dropna(subset=["mom20"]).set_index("trade_date")


def build_market_env(index_dfs: dict) -> pd.DataFrame:
    """三指数日线 → 综合环境特征

    Args:
        index_dfs: {ts_code: df}

    Returns:
        DataFrame index=trade_date, 列:
          bull_count(0~3), mom20_avg, mom5_avg, day_pct_avg, slope_avg,
          以及各指数单项 (母指数_指标)
    """
    feats = {c: _index_feature(df) for c, df in index_dfs.items() if not df.empty}
    feats = {c: f for c, f in feats.items() if not f.empty}
    if not feats:
        return pd.DataFrame()

    all_dates = sorted(set().union(*[set(f.index) for f in feats.values()]))
    out = pd.DataFrame(index=all_dates)
    out.index.name = "trade_date"
    for c, f in feats.items():
        tag = INDEX_NAMES.get(c, c)
        for col in f.columns:
            out[f"{tag}_{col}"] = f[col]

    cols = [c for c in out.columns if c.endswith(("_mom20", "_mom5", "_day_pct", "_slope", "_ma20_gt_ma60"))]
    if cols:
        out["bull_count"] = out[[c for c in cols if c.endswith("_ma20_gt_ma60")]].sum(axis=1) \
            if any(c.endswith("_ma20_gt_ma60") for c in cols) else 0
        out["mom20_avg"] = out[[c for c in cols if c.endswith("_mom20")]].mean(axis=1) \
            if any(c.endswith("_mom20") for c in cols) else np.nan
        out["mom5_avg"] = out[[c for c in cols if c.endswith("_mom5")]].mean(axis=1) \
            if any(c.endswith("_mom5") for c in cols) else np.nan
        out["day_pct_avg"] = out[[c for c in cols if c.endswith("_day_pct")]].mean(axis=1) \
            if any(c.endswith("_day_pct") for c in cols) else np.nan
        out["slope_avg"] = out[[c for c in cols if c.endswith("_slope")]].mean(axis=1) \
            if any(c.endswith("_slope") for c in cols) else np.nan
    return out


# =========================================================
# 市场宽度统计
# =========================================================
def compute_market_breadth(kline_dict: dict) -> dict:
    """遍历全市场K线, 统计每日上涨家数占比

    Returns:
        dict[trade_date] = 上涨占比 (0~1)
    """
    up_count, tot_count = {}, {}
    for ts_code, df in kline_dict.items():
        if "pct_chg" not in df.columns:
            continue
        for d, pc in zip(df["trade_date"].values, df["pct_chg"].values):
            tot_count[d] = tot_count.get(d, 0) + 1
            if pd.notna(pc) and pc > 0:
                up_count[d] = up_count.get(d, 0) + 1
    breadth = {}
    for d, t in tot_count.items():
        if t > 100:
            breadth[d] = up_count.get(d, 0) / t
    return breadth


# =========================================================
# 主回测: 记录每笔交易的大盘环境
# =========================================================
def run_backtest(start_date: str = "20240101",
                 end_date: str = None,
                 hold_days: int = 5,
                 max_stocks: int = None,
                 max_daily: int = 5,
                 vf: VolSurgeFilters = None) -> pd.DataFrame:
    """与 volume_surge_strategy.run_backtest 相同的信号与成交逻辑,
    但不过滤大盘, 每笔记录当日环境, 返回明细 DataFrame."""
    if vf is None:
        vf = VolSurgeFilters()
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")

    print("=" * 60)
    print("  量能爆发策略 · 胜率 vs 大盘环境(三指数综合) 相关性回测")
    print(f"  区间: {start_date} ~ {end_date}  持有: T+{hold_days}  每日最多{max_daily}只")
    print(f"  指数: {', '.join(INDEX_NAMES.values())}")
    print("=" * 60)

    t0 = time.time()
    kline_dict = {}
    load_stock_names()
    dt = datetime.strptime(start_date, "%Y%m%d")
    load_start = (dt - timedelta(days=400)).strftime("%Y%m%d")

    # --- 三指数环境 ---
    env_df = pd.DataFrame()
    index_dfs = {}
    for code in INDEX_CODES:
        try:
            df = load_kline(code, start_date=load_start, end_date=end_date)
            if not df.empty:
                index_dfs[code] = df
        except Exception as e:
            print(f"[Market] {code} 加载失败: {e}")
    if index_dfs:
        env_df = build_market_env(index_dfs)
        print(f"[Market] 三指数环境特征: {len(env_df)} 天, "
              f"指标: {[c for c in env_df.columns]}")

    # --- 全市场K线 ---
    codes_loaded = 0
    for path in iter_all_day_files(markets=("SH", "SZ")):
        if max_stocks and codes_loaded >= max_stocks:
            break
        ts_code = tdx_filename_to_ts_code(path)
        if not ts_code or ts_code.startswith(("999", "8", "4", "5", "1", "2")):
            continue
        df = load_kline(ts_code, start_date=load_start, end_date=end_date)
        if df.empty or len(df) < 180:
            continue
        df = precompute_indicators(df)
        kline_dict[ts_code] = df
        codes_loaded += 1
    print(f"[Load] 加载 {codes_loaded} 只股票, 耗时 {time.time()-t0:.1f}s")

    # --- 市场宽度 ---
    t0 = time.time()
    breadth = compute_market_breadth(kline_dict)
    print(f"[Breadth] 市场宽度覆盖 {len(breadth)} 天, 耗时 {time.time()-t0:.1f}s")

    # --- 生成信号 ---
    t0 = time.time()
    signals_dict = {}
    for ts_code, df_pre in kline_dict.items():
        sig = volume_surge_strategy_vectorized(df_pre, ts_code, vf)
        if sig.any():
            signals_dict[ts_code] = sig
    print(f"[Signal] {len(signals_dict)} 只有信号, 耗时 {time.time()-t0:.1f}s")

    # --- 交易日列表 ---
    all_dates = set()
    for df in kline_dict.values():
        all_dates.update(df["trade_date"].tolist())
    trade_dates = sorted(d for d in all_dates if start_date <= d <= end_date)
    date_idx_map = {c: dict(zip(d["trade_date"], d.index))
                    for c, d in kline_dict.items()}
    print(f"[Dates] 回测交易日: {len(trade_dates)}")

    # --- 逐日撮合, 记录每笔环境 ---
    records = []
    for td_idx, td in enumerate(trade_dates):
        candidates = []
        for ts_code, sig in signals_dict.items():
            idx_map = date_idx_map.get(ts_code)
            if not idx_map:
                continue
            i = idx_map.get(td)
            if i is None or i >= len(sig):
                continue
            score = sig[i]
            if score > 0:
                candidates.append((ts_code, float(score)))
        if not candidates:
            continue
        candidates.sort(key=lambda x: -x[1])
        selected = [c[0] for c in candidates[:max_daily]]

        env = env_df.loc[td] if td in env_df.index else None
        for ts_code in selected:
            df = kline_dict.get(ts_code)
            if df is None:
                continue
            idx_list = df.index[df["trade_date"] == td].tolist()
            if not idx_list:
                continue
            i = idx_list[0]
            buy_close = float(df.iloc[i]["close"])
            exit_idx = min(i + hold_days, len(df) - 1)
            stopped = False
            for j in range(i + 1, exit_idx + 1):
                if df.iloc[j]["low"] / buy_close - 1 <= -0.07:
                    ret = -7.0
                    stopped = True
                    break
            if not stopped:
                if i + hold_days < len(df):
                    ret = (df.iloc[i + hold_days]["close"] / buy_close - 1) * 100
                else:
                    continue
            rec = {"trade_date": td, "ts_code": ts_code, "ret": ret}
            if env is not None:
                for col in env.index:
                    rec[col] = env[col]
            rec["breadth"] = breadth.get(td, np.nan)
            records.append(rec)

        if td_idx % 50 == 0:
            print(f"  [{td_idx+1}/{len(trade_dates)}] {td}: 累计 {len(records)} 笔")

    rec_df = pd.DataFrame(records)
    print(f"[Done] 共 {len(rec_df)} 笔交易")
    return rec_df


# =========================================================
# 分组统计 + Spearman 相关
# =========================================================
def _grp_summary(df: pd.DataFrame, label: str) -> str:
    n = len(df)
    if n == 0:
        return f"{label:<14} 无样本"
    wr = (df["ret"] > 0).mean() * 100
    ar = df["ret"].mean()
    md = df["ret"].median()
    return (f"{label:<14} n={n:<4} 胜率={wr:5.1f}%  均收益={ar:+6.2f}%  "
            f"中位={md:+6.2f}%")


def analyze(rec_df: pd.DataFrame, hold_days: int):
    print("\n" + "=" * 72)
    print(f"  胜率 vs 大盘环境·三指数综合 (T+{hold_days}, 硬止损-7%)")
    print("=" * 72)

    if rec_df.empty:
        print("  无交易记录")
        return

    # ---- 1. 三指数多头数量 (0~3) ----
    print("\n── 1. 三指数 MA20>MA60 多头数量 ──")
    for v in range(4):
        sub = rec_df[rec_df["bull_count"] == v]
        print("  " + _grp_summary(sub, f"{v}/3多头"))

    # ---- 2. 综合20日动量分档 ----
    print("\n── 2. 三指数20日动量均值分档 ──")
    bins = [(-np.inf, -3), (-3, 0), (0, 3), (3, 6), (6, np.inf)]
    labels = ["<-3%", "-3~0%", "0~+3%", "+3~+6%", ">+6%"]
    for (lo, hi), lab in zip(bins, labels):
        sub = rec_df[(rec_df["mom20_avg"] >= lo) & (rec_df["mom20_avg"] < hi)]
        print("  " + _grp_summary(sub, lab))

    # ---- 3. 综合当日涨跌分档 ----
    print("\n── 3. 三指数当日涨跌均值分档 ──")
    bins3 = [(-np.inf, -1.0), (-1.0, -0.3), (-0.3, 0.3), (0.3, 1.0), (1.0, np.inf)]
    labels3 = ["<-1%", "-1~-0.3%", "-0.3~+0.3%", "+0.3~+1%", ">+1%"]
    for (lo, hi), lab in zip(bins3, labels3):
        sub = rec_df[(rec_df["day_pct_avg"] >= lo) & (rec_df["day_pct_avg"] < hi)]
        print("  " + _grp_summary(sub, lab))

    # ---- 4. 综合MA20斜率分档 ----
    print("\n── 4. 三指数MA20斜率均值分档 ──")
    bins4 = [(-np.inf, -1.0), (-1.0, -0.3), (-0.3, 0.3), (0.3, 1.0), (1.0, np.inf)]
    labels4 = ["<-1%", "-1~-0.3%", "-0.3~+0.3%", "+0.3~+1%", ">+1%"]
    for (lo, hi), lab in zip(bins4, labels4):
        sub = rec_df[(rec_df["slope_avg"] >= lo) & (rec_df["slope_avg"] < hi)]
        print("  " + _grp_summary(sub, lab))

    # ---- 5. 市场宽度分档 ----
    if rec_df["breadth"].notna().any():
        print("\n── 5. 市场宽度 (当日上涨家数占比) 分档 ──")
        bins5 = [(-np.inf, 0.30), (0.30, 0.45), (0.45, 0.60), (0.60, 0.75), (0.75, np.inf)]
        labels5 = ["<30%", "30~45%", "45~60%", "60~75%", ">75%"]
        for (lo, hi), lab in zip(bins5, labels5):
            sub = rec_df[(rec_df["breadth"] > lo) & (rec_df["breadth"] <= hi)]
            print("  " + _grp_summary(sub, lab))
    else:
        print("\n── 5. 市场宽度: 无数据 ──")

    # ---- Spearman 相关性 ----
    print("\n" + "=" * 72)
    print("  Spearman 相关性 (环境指标连续值 vs 单笔收益)")
    print("=" * 72)
    win = (rec_df["ret"] > 0).astype(int)
    metrics = {
        "bull_count": "多头数量(0-3)",
        "day_pct_avg": "三指数当日涨跌",
        "slope_avg": "MA20斜率均值",
        "mom5_avg": "三指数5日动量",
        "mom20_avg": "三指数20日动量",
        "breadth": "市场宽度",
    }
    print(f"  {'指标':<16} {'样本':>6} {'rho(ret)':>9} {'rho(win)':>9} {'p(ret)':>8}  说明")
    for col, lab in metrics.items():
        sub = rec_df[rec_df[col].notna()]
        if len(sub) < 30:
            print(f"  {lab:<16} {len(sub):>6}  样本不足")
            continue
        r_ret, p_ret = spearmanr(sub[col], sub["ret"])
        r_win, _ = spearmanr(sub[col], win.loc[sub.index])
        sig = "**" if p_ret < 0.01 else ("*" if p_ret < 0.05 else "")
        print(f"  {lab:<16} {len(sub):>6} {r_ret:>9.3f} {r_win:>9.3f} {p_ret:>8.3f} {sig}")


# =========================================================
# 主入口
# =========================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="量能策略胜率 vs 大盘环境(三指数)")
    parser.add_argument("--start", default="20240101", help="回测起始日 YYYYMMDD")
    parser.add_argument("--end", default=None, help="回测结束日 YYYYMMDD")
    parser.add_argument("--hold", type=int, default=5, help="持有天数")
    parser.add_argument("--max-stocks", type=int, default=None, help="最大加载股票数")
    parser.add_argument("--max-daily", type=int, default=5, help="每日最多选股数")
    args = parser.parse_args()

    rec_df = run_backtest(args.start, args.end, args.hold, args.max_stocks, args.max_daily)
    analyze(rec_df, args.hold)

    # 保存明细
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tdx_backtest", "output")
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "winrate_market_env_3idx.csv")
    if not rec_df.empty:
        rec_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        print(f"\n✅ 交易明细已保存: {csv_path}")
