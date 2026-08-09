# -*- coding: utf-8 -*-
"""
量能爆发策略 — 大盘闸门 A/B 对比回测

一次性加载全市场数据与信号, 分别用多种大盘闸门撮合, 输出各闸门的
胜率 / 均收益 / 信号数 对比, 用于验证"三指数动量闸门"是否优于
原有"HS300 MA20>MA60 均线闸门".

闸门方案:
  none           无过滤 (基准)
  hs300_ma       原方案: HS300 MA20>MA60
  hs300_mom20    沪深300 20日动量 > 0
  idx3_mom20_0   三指数20日动量均值 > 0   (推荐)
  idx3_mom20_3   三指数20日动量均值 > +3% (严格)
  idx3_mom20_3_w 三指数动量>+3% 且 市场宽度>50%

使用方式:
    python compare_market_gate.py
    python compare_market_gate.py --start 20240101 --end 20260807 --hold 5
"""
from __future__ import annotations
import os, sys, time, argparse
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
# volume_surge_strategy 内部会把 tdx_backtest 插到 path[0], 这里重新把 solo 放回最前,
# 确保加载 solo 版 winrate_vs_market_env.py (tdx_backtest 下有旧版残留同名文件)
sys.path.insert(0, SOLO_DIR)
from winrate_vs_market_env import (build_market_env, compute_market_breadth,
                                   INDEX_CODES, INDEX_NAMES)


# =========================================================
# 闸门定义
# =========================================================
def _make_gates():
    def g_none(env, br):
        return True

    def g_hs300_ma(env, br):
        return bool(env["沪深300_ma20_gt_ma60"] == 1)

    def g_hs300_mom20(env, br):
        return bool(env["沪深300_mom20"] > 0)

    def g_idx3_mom20_0(env, br):
        return bool(env["mom20_avg"] > 0)

    def g_idx3_mom20_3(env, br):
        return bool(env["mom20_avg"] > 3)

    def g_idx3_mom20_3_w(env, br):
        return bool(env["mom20_avg"] > 3 and br >= 0.50)

    return {
        "none":           ("无过滤(基准)", g_none),
        "hs300_ma":       ("HS300 MA20>MA60(原)", g_hs300_ma),
        "hs300_mom20":    ("HS300 20日动量>0", g_hs300_mom20),
        "idx3_mom20_0":   ("三指数动量>0", g_idx3_mom20_0),
        "idx3_mom20_3":   ("三指数动量>+3%", g_idx3_mom20_3),
        "idx3_mom20_3_w": ("三指数动量>+3%+宽度>50%", g_idx3_mom20_3_w),
    }


# =========================================================
# 主流程
# =========================================================
def run_compare(start_date: str = "20240101",
                end_date: str = None,
                hold_days: int = 5,
                max_stocks: int = None,
                max_daily: int = 5):
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")
    vf = VolSurgeFilters()

    print("=" * 60)
    print("  量能爆发策略 · 大盘闸门 A/B 对比回测")
    print(f"  区间: {start_date} ~ {end_date}  持有: T+{hold_days}  每日最多{max_daily}只")
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
        print(f"[Market] 三指数环境特征: {len(env_df)} 天")

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
        kline_dict[ts_code] = precompute_indicators(df)
        codes_loaded += 1
    print(f"[Load] 加载 {codes_loaded} 只股票, 耗时 {time.time()-t0:.1f}s")

    t0 = time.time()
    breadth = compute_market_breadth(kline_dict)
    print(f"[Breadth] 市场宽度覆盖 {len(breadth)} 天, 耗时 {time.time()-t0:.1f}s")

    # --- 信号 ---
    t0 = time.time()
    signals_dict = {}
    for ts_code, df_pre in kline_dict.items():
        sig = volume_surge_strategy_vectorized(df_pre, ts_code, vf)
        if sig.any():
            signals_dict[ts_code] = sig
    print(f"[Signal] {len(signals_dict)} 只有信号, 耗时 {time.time()-t0:.1f}s")

    all_dates = set()
    for df in kline_dict.values():
        all_dates.update(df["trade_date"].tolist())
    trade_dates = sorted(d for d in all_dates if start_date <= d <= end_date)
    date_idx_map = {c: dict(zip(d["trade_date"], d.index))
                    for c, d in kline_dict.items()}
    print(f"[Dates] 回测交易日: {len(trade_dates)}")

    # --- 各闸门逐日撮合 ---
    gates = _make_gates()
    results = {}
    for gkey, (gname, gfunc) in gates.items():
        returns, skipped, n_days = [], 0, 0
        for td in trade_dates:
            env = env_df.loc[td] if td in env_df.index else None
            br = breadth.get(td, np.nan)
            if env is None:
                skipped += 1
                continue
            if not gfunc(env, br):
                skipped += 1
                continue
            n_days += 1
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
            if os.environ.get("GATE_DEBUG") and candidates:
                print(f"    [debug] {td}: {[c[0] for c in candidates]}")
            if not candidates:
                continue
            candidates.sort(key=lambda x: -x[1])
            for cand in candidates[:max_daily]:
                ts_code = cand[0]
                df = kline_dict.get(ts_code)
                if df is None:
                    continue
                il = df.index[df["trade_date"] == td].tolist()
                if not il:
                    continue
                i = il[0]
                buy_close = float(df.iloc[i]["close"])
                stopped = False
                for j in range(i + 1, min(i + hold_days + 1, len(df))):
                    if df.iloc[j]["low"] / buy_close - 1 <= -0.07:
                        returns.append(-7.0)
                        stopped = True
                        break
                if not stopped:
                    if i + hold_days < len(df):
                        returns.append(
                            (df.iloc[i + hold_days]["close"] / buy_close - 1) * 100)
        results[gkey] = {"name": gname, "returns": np.array(returns),
                         "skipped": skipped, "n_days": n_days}
        print(f"  [gate={gname}] 完成, 信号数={len(returns)}")

    # --- 输出对比表 ---
    print("\n" + "=" * 78)
    print(f"  大盘闸门对比 (T+{hold_days}, 硬止损-7%)")
    print("=" * 78)
    print(f"  {'闸门':<20} {'天数':>5} {'跳过':>5} {'信号':>6} {'胜率':>7} "
          f"{'均收益':>8} {'中位':>7} {'盈亏比':>6} {'正期望':>6}")
    for gkey, r in results.items():
        rets = r["returns"]
        n = len(rets)
        if n == 0:
            print(f"  {r['name']:<20} {r['n_days']:>5} {r['skipped']:>5} {0:>6}  无信号")
            continue
        wr = (rets > 0).mean() * 100
        ar = rets.mean()
        md = np.median(rets)
        wins = rets[rets > 0]
        losses = rets[rets <= 0]
        pl = (wins.mean() / abs(losses.mean())) if len(losses) else np.inf
        ev = wr / 100 * wins.mean() + (1 - wr / 100) * losses.mean()
        print(f"  {r['name']:<20} {r['n_days']:>5} {r['skipped']:>5} {n:>6} "
              f"{wr:>6.1f}% {ar:>+7.2f}% {md:>+6.2f}% {pl:>5.2f} {ev:>+5.2f}%")

    # 保存
    out_dir = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "tdx_backtest", "output"))
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    for gkey, r in results.items():
        rets = r["returns"]
        rows.append({"gate": gkey, "name": r["name"], "n_days": r["n_days"],
                     "skipped": r["skipped"], "signals": len(rets),
                     "winrate": (rets > 0).mean() * 100 if len(rets) else np.nan,
                     "avg_ret": rets.mean() if len(rets) else np.nan})
    pd.DataFrame(rows).to_csv(os.path.join(out_dir, "market_gate_compare.csv"),
                              index=False, encoding="utf-8-sig")
    print(f"\n✅ 对比表已保存: {os.path.join(out_dir, 'market_gate_compare.csv')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="大盘闸门 A/B 对比")
    parser.add_argument("--start", default="20240101")
    parser.add_argument("--end", default=None)
    parser.add_argument("--hold", type=int, default=5)
    parser.add_argument("--max-stocks", type=int, default=None)
    parser.add_argument("--max-daily", type=int, default=5)
    args = parser.parse_args()
    run_compare(args.start, args.end, args.hold, args.max_stocks, args.max_daily)
