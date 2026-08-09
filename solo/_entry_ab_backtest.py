# -*- coding: utf-8 -*-
"""入场价优化 A/B 回测

闸门: 三指数20日动量均值 > +3% (生产配置), 选股: 现评分 Top5 (与生产一致)
四种入场方案对比 (持有期统一为买入后 T+5, 盘中-7%止损):
  base_close  信号日收盘买入 (基准)
  next_open   次日开盘价买入
  next_low    次日最低价买入 (理想上限, 用于量化"回踩买入"理论价值)
  dip1        次日回踩信号日收盘价下方1%触发限价买入, 高开>2%放弃

收益规则: 买入价=buy, 自买入次日起检查 low/buy<=-7% 触发止损(-7),
          否则持有至买入后第5个交易日收盘卖出.
"""
import os, sys, time
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


def trade(df: pd.DataFrame, i_buy: int, buy_close: float, hold: int):
    """买入后持有hold个交易日, 盘中-7%止损. 返回收益%或None(数据不足不计)"""
    for j in range(i_buy + 1, min(i_buy + hold + 1, len(df))):
        if df.iloc[j]["low"] / buy_close - 1 <= STOP / 100.0:
            return STOP
    if i_buy + hold < len(df):
        return (df.iloc[i_buy + hold]["close"] / buy_close - 1) * 100.0
    return None


def run():
    vf = VolSurgeFilters()
    t0 = time.time()
    print("=" * 60)
    print("  入场价优化 A/B 回测 (T+5, 止损-7%, 三指数动量>+3%闸门)")
    print(f"  区间: {START} ~ {END}  每日最多{MAX_DAILY}只")
    print("=" * 60)
    load_stock_names()
    dt = datetime.strptime(START, "%Y%m%d")
    load_start = (dt - timedelta(days=400)).strftime("%Y%m%d")

    # --- 三指数环境 ---
    index_dfs = {}
    for code in INDEX_CODES:
        try:
            df = load_kline(code, start_date=load_start, end_date=END)
            if not df.empty:
                index_dfs[code] = df
        except Exception as e:
            print(f"[Market] {code} 加载失败: {e}")
    env_df = build_market_env(index_dfs) if index_dfs else pd.DataFrame()
    print(f"[Market] 环境特征: {len(env_df)} 天")

    # --- 全市场K线 ---
    kline_dict = {}
    codes_loaded = 0
    for path in iter_all_day_files(markets=("SH", "SZ")):
        ts_code = tdx_filename_to_ts_code(path)
        if not ts_code or ts_code.startswith(("999", "8", "4", "5", "1", "2")):
            continue
        df = load_kline(ts_code, start_date=load_start, end_date=END)
        if df.empty or len(df) < 180:
            continue
        kline_dict[ts_code] = precompute_indicators(df)
        codes_loaded += 1
    print(f"[Load] 加载 {codes_loaded} 只, 耗时 {time.time()-t0:.1f}s")

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
    trade_dates = sorted(d for d in all_dates if START <= d <= END)
    date_idx_map = {c: dict(zip(d["trade_date"], d.index))
                    for c, d in kline_dict.items()}

    # --- 四种入场方案撮合 ---
    res = {k: [] for k in ("base_close", "next_open", "next_low", "dip1")}
    n_dip_skip = 0          # dip1: 未回踩触发而放弃
    n_dip_gap_skip = 0      # dip1: 高开>2%放弃
    passed_days = 0
    for td in trade_dates:
        env = env_df.loc[td] if td in env_df.index else None
        if env is None:
            continue
        if float(env["mom20_avg"]) <= MOM_THRESHOLD:
            continue
        passed_days += 1
        cands = []
        for ts_code, sig in signals_dict.items():
            idx_map = date_idx_map.get(ts_code)
            if not idx_map:
                continue
            i = idx_map.get(td)
            if i is None or i >= len(sig):
                continue
            sc = sig[i]
            if sc > 0:
                cands.append((ts_code, float(sc)))
        if not cands:
            continue
        cands.sort(key=lambda x: -x[1])
        for ts_code in [c[0] for c in cands[:MAX_DAILY]]:
            df = kline_dict.get(ts_code)
            if df is None:
                continue
            i = date_idx_map.get(ts_code, {}).get(td)
            if i is None:
                continue
            if i + 1 >= len(df):     # 需有次日数据
                continue
            close_sig = float(df.iloc[i]["close"])
            open_n = float(df.iloc[i + 1]["open"])
            low_n = float(df.iloc[i + 1]["low"])

            # 1) 基准: 信号日收盘买入
            r = trade(df, i, close_sig, HOLD_DAYS)
            if r is not None:
                res["base_close"].append(r)
            # 2) 次日开盘买入
            r = trade(df, i + 1, open_n, HOLD_DAYS)
            if r is not None:
                res["next_open"].append(r)
            # 3) 次日最低买入(理想上限)
            r = trade(df, i + 1, low_n, HOLD_DAYS)
            if r is not None:
                res["next_low"].append(r)
            # 4) 次日回踩1%限价触发; 高开>2%放弃
            trig = close_sig * 0.99
            if open_n > close_sig * 1.02:
                n_dip_gap_skip += 1
            elif low_n <= trig:
                r = trade(df, i + 1, min(open_n, trig), HOLD_DAYS)
                if r is not None:
                    res["dip1"].append(r)
            else:
                n_dip_skip += 1

    # --- 输出 ---
    names = {"base_close": "信号日收盘(基准)",
             "next_open": "次日开盘",
             "next_low": "次日最低(理想)",
             "dip1": "次日回踩1%触发"}
    print("\n" + "=" * 78)
    print(f"  入场价优化 A/B (闸门通过日 {passed_days} 天, T+{HOLD_DAYS}, 止损-7%)")
    print("=" * 78)
    print(f"  {'方案':<18} {'笔数':>6} {'胜率':>7} {'均收益':>8} {'中位':>7} "
          f"{'盈亏比':>6} {'正期望':>7}")
    rows = []
    for key in ("base_close", "next_open", "next_low", "dip1"):
        rets = np.array(res[key])
        n = len(rets)
        if n == 0:
            print(f"  {names[key]:<18} {0:>6}  无信号")
            continue
        wr = (rets > 0).mean() * 100
        ar = rets.mean()
        md = np.median(rets)
        wins = rets[rets > 0]
        losses = rets[rets <= 0]
        pl = (wins.mean() / abs(losses.mean())) if len(losses) else np.inf
        ev = wr / 100 * wins.mean() + (1 - wr / 100) * losses.mean()
        print(f"  {names[key]:<18} {n:>6} {wr:>6.1f}% {ar:>+7.2f}% "
              f"{md:>+6.2f}% {pl:>5.2f} {ev:>+6.2f}%")
        rows.append({"scheme": key, "name": names[key], "trades": n,
                     "winrate": round(wr, 1), "avg_ret": round(ar, 2),
                     "median": round(md, 2), "pl_ratio": round(pl, 2),
                     "expect": round(ev, 2)})
    print(f"\n  dip1: 未回踩放弃 {n_dip_skip} 笔, 高开>2%放弃 {n_dip_gap_skip} 笔")

    out_dir = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "tdx_backtest", "output"))
    os.makedirs(out_dir, exist_ok=True)
    pd.DataFrame(rows).to_csv(os.path.join(out_dir, "entry_price_compare.csv"),
                              index=False, encoding="utf-8-sig")
    print(f"\n✅ 对比表已保存: {os.path.join(out_dir, 'entry_price_compare.csv')}")


if __name__ == "__main__":
    run()
