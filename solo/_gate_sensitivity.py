# -*- coding: utf-8 -*-
"""大盘闸门敏感性回测: 不同三指数20日动量阈值下, 每日Top3的T+5胜率/均收益.

口径与 run_backtest 完全一致: 次日开盘买入, T+5, 盘中-7%硬止损, 每日按距MA20升序取Top3.
额外输出: 按当日三指数动量分组的胜率明细(验证"震荡期个股表现好"是否成立).
"""
import os, sys, time
import numpy as np

TDX_BT = r"d:\mystock\tdx_backtest"
sys.path.insert(0, TDX_BT)
from data_loader import load_kline, iter_all_day_files, tdx_filename_to_ts_code
from strategy_backtest import load_stock_names
from volume_surge_strategy import precompute_indicators, volume_surge_strategy_vectorized, VolSurgeFilters

START, END = "20240101", "20260811"
HOLD, STOP = 5, -7.0
INDEX_3 = ("000001.SH", "000300.SH", "399006.SZ")


def main():
    load_stock_names()
    vf = VolSurgeFilters()
    from datetime import datetime, timedelta
    load_start = (datetime.strptime(START, "%Y%m%d") - timedelta(days=400)).strftime("%Y%m%d")

    # --- 加载三指数动量 ---
    idx3_mom20 = {}
    mom_maps = []
    for code in INDEX_3:
        df = load_kline(code, start_date=load_start, end_date=END)
        if df.empty:
            continue
        df = precompute_indicators(df)
        mom = (df["close"] / df["close"].shift(20) - 1) * 100
        mom_maps.append(dict(zip(df["trade_date"].values, mom.values)))
    if len(mom_maps) == 3:
        _all = sorted(set().union(*[set(m) for m in mom_maps]))
        for _d in _all:
            _vals = [m[_d] for m in mom_maps if _d in m and not pd.isna(m[_d])]
            if len(_vals) == 3:
                idx3_mom20[_d] = float(np.mean(_vals))
    print(f"[Market] 三指数动量 {len(idx3_mom20)} 天")

    # --- 加载全市场 ---
    t0 = time.time()
    kline_dict, signals_dict = {}, {}
    for path in iter_all_day_files(markets=("SH", "SZ")):
        ts_code = tdx_filename_to_ts_code(path)
        if not ts_code or ts_code[0] not in "630":
            continue
        df = load_kline(ts_code, start_date=load_start, end_date=END)
        if df.empty or len(df) < 180:
            continue
        df = precompute_indicators(df)
        sig = volume_surge_strategy_vectorized(df, ts_code, vf)
        if sig.any():
            signals_dict[ts_code] = sig
            kline_dict[ts_code] = df
    print(f"[Load] {len(kline_dict)} 只有信号, 耗时 {time.time()-t0:.1f}s")

    all_dates = set()
    for df in kline_dict.values():
        all_dates.update(df["trade_date"].tolist())
    trade_dates = sorted(d for d in all_dates if START <= d <= END)
    date_idx_map = {}
    for ts_code, df in kline_dict.items():
        date_idx_map[ts_code] = dict(zip(df["trade_date"], df.index))
    print(f"[Dates] {len(trade_dates)} 天")

    # --- 逐日: 记录每日 Top3 收益 + 当日动量 ---
    daily = []  # (date, mom20, ret_list)
    for td in trade_dates:
        td_mom = idx3_mom20.get(td, np.nan)
        cands = []
        for ts_code, sig in signals_dict.items():
            i = date_idx_map.get(ts_code, {}).get(td)
            if i is None or i >= len(sig) or sig[i] <= 0:
                continue
            df = kline_dict[ts_code]
            ma20 = float(df.iloc[i]["ma20"])
            pos = (float(df.iloc[i]["close"]) / ma20 - 1) * 100 if ma20 > 0 else 99.0
            cands.append((ts_code, float(sig[i]), pos))
        if not cands:
            daily.append((td, td_mom, []))
            continue
        cands.sort(key=lambda x: (x[2] > 8, x[2]))
        rets = []
        for ts_code, _, _ in cands[:3]:
            df = kline_dict[ts_code]
            il = df.index[df["trade_date"] == td].tolist()
            if not il or il[0] + 1 >= len(df):
                continue
            i = il[0]
            buy = float(df.iloc[i + 1]["open"])
            exit_i = min(i + 1 + HOLD, len(df) - 1)
            r = None
            for j in range(i + 2, exit_i + 1):
                if float(df.iloc[j]["low"]) / buy - 1 <= STOP / 100:
                    r = STOP
                    break
            if r is None:
                if i + 1 + HOLD < len(df):
                    r = (float(df.iloc[i + 1 + HOLD]["close"]) / buy - 1) * 100
                else:
                    r = None
            if r is not None:
                rets.append(r)
        daily.append((td, td_mom, rets))

    all_ret = [r for _, _, rs in daily for r in rs]

    def stat(rets):
        if not rets:
            return "无信号"
        a = np.array(rets)
        return f"{len(a)}笔 胜率{(a>0).mean()*100:.1f}% 均{a.mean():+.2f}% 中位{np.median(a):+.2f}%"

    print()
    print("=" * 92)
    print("  按当日三指数20日动量分组 (全市场每日Top3, T+5, 止损-7%)")
    print("=" * 92)
    groups = [(-99, -3), (-3, 0), (0, 3), (3, 99)]
    gnames = ["弱市 动量<=-3%", "震荡偏弱 -3~0%", "震荡偏强 0~+3%", "强市 动量>+3%"]
    for (lo, hi), gname in zip(groups, gnames):
        rs = [r for _, m, rl in daily for r in rl if lo <= m < hi]
        nd = sum(1 for _, m, _ in daily if lo <= m < hi)
        print(f"  {gname:<16} 天数={nd:>4}  {stat(rs)}")
    n_mom_none = sum(1 for _, m, _ in daily if pd.isna(m))
    print(f"  {'动量缺失':<16} 天数={n_mom_none:>4}")

    print()
    print("=" * 92)
    print("  不同闸门阈值下全样本结果 (仅当 当日动量>阈值 才买入)")
    print("=" * 92)
    for thr in (3.0, 1.0, 0.0, -3.0, -99.0):
        rs = [r for _, m, rl in daily for r in rl if m > thr]
        nd = sum(1 for _, m, _ in daily if m > thr)
        print(f"  闸门>+{thr:>5.1f}%: 可买天数={nd:>4}  {stat(rs)}")

    # 额外: 有动量但<=0 的日子(当前闸门禁买)单独看
    rs_neg = [r for _, m, rl in daily for r in rl if not pd.isna(m) and m <= 0]
    print()
    print(f"  🔍 当前禁买区(动量<=0, {sum(1 for _,m,_ in daily if not pd.isna(m) and m<=0)}天): {stat(rs_neg)}")


if __name__ == "__main__":
    import pandas as pd
    main()
