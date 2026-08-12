# -*- coding: utf-8 -*-
"""TOP3 候选股历史信号胜率统计 (回测口径: 次日开盘买入, T+5, 盘中-7%止损)

统计对象: 20260807 / 20260810 / 20260811 三日算法输出TOP3 去重后的候选股.
数据: tdx_backtest 全市场日线, 信号 = volume_surge_strategy_vectorized (与生产 tdx 版一致).
"""
import os, sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

TDX_BT = r"d:\mystock\tdx_backtest"
SOLO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TDX_BT)
from data_loader import load_kline
from strategy_backtest import load_stock_names
from volume_surge_strategy import precompute_indicators, volume_surge_strategy_vectorized, VolSurgeFilters

STOP = -7.0
HOLD = 5
START, END = "20240101", "20260811"

# 三日 TOP3 去重 (ts_code, 名称)
CANDIDATES = [
    ("603118.SH", "共进股份"),
    ("000938.SZ", "紫光股份"),
    ("002432.SZ", "九安医疗"),
    ("002379.SZ", "宏桥控股"),
    ("301165.SZ", "锐捷网络"),
    ("301536.SZ", "星宸科技"),
    ("603337.SH", "杰克科技"),
]


def trade(df, i_buy, hold=HOLD):
    """次日开盘买入持有hold天, 盘中-7%止损. 返回收益%或None"""
    buy_close = float(df.iloc[i_buy]["open"])
    for j in range(i_buy + 1, min(i_buy + hold + 1, len(df))):
        if df.iloc[j]["low"] / buy_close - 1 <= STOP / 100.0:
            return STOP
    if i_buy + hold < len(df):
        return (df.iloc[i_buy + hold]["close"] / buy_close - 1) * 100.0
    return None


def main():
    load_stock_names()
    vf = VolSurgeFilters()
    dt = datetime.strptime(START, "%Y%m%d")
    load_start = (dt - timedelta(days=400)).strftime("%Y%m%d")

    print("=" * 96)
    print("  TOP3 候选股历史信号胜率 (T+5, 止损-7%, 次日开盘, 全样本2024-01~2026-08)")
    print("=" * 96)
    print(f"  {'名称':<8}{'代码':<12}{'信号数':>6}{'胜率':>8}{'均收益':>9}{'中位':>8}"
          f"{'盈亏比':>7}{'期望':>8}  近90日信号/胜率")
    rows = []
    for ts_code, name in CANDIDATES:
        try:
            df = load_kline(ts_code, start_date=load_start, end_date=END)
        except Exception as e:
            print(f"  {name:<8}{ts_code:<12} 加载失败: {e}")
            continue
        if df is None or df.empty:
            print(f"  {name:<8}{ts_code:<12} 无数据")
            continue
        dfp = precompute_indicators(df)
        sig = volume_surge_strategy_vectorized(dfp, ts_code, vf)
        idx = np.where(sig > 0)[0]
        if len(idx) == 0:
            print(f"  {name:<8}{ts_code:<12} 无信号")
            continue
        rets, recent = [], []
        n_recent = 0
        wr_recent = None
        for i in idx:
            if i + 1 >= len(dfp):
                continue
            r = trade(dfp, i + 1)
            if r is None:
                continue
            rets.append(r)
            td = str(dfp.iloc[i]["trade_date"])
            if td >= "20260501":
                recent.append(r)
        arr = np.array(rets)
        wins = arr[arr > 0]
        losses = arr[arr <= 0]
        wr = (arr > 0).mean() * 100
        pl = (wins.mean() / abs(losses.mean())) if len(losses) else np.inf
        ev = wr / 100 * wins.mean() + (1 - wr / 100) * losses.mean()
        recent_arr = np.array(recent)
        if len(recent_arr) >= 3:
            n_recent = len(recent_arr)
            wr_recent = f"{int((recent_arr > 0).mean() * 100)}%({n_recent}笔)"
        else:
            wr_recent = f"<3笔({len(recent_arr)})"
        print(f"  {name:<8}{ts_code:<12}{len(arr):>6}{wr:>7.1f}%{arr.mean():>+8.2f}%"
              f"{np.median(arr):>+7.2f}%{pl:>6.2f}{ev:>+7.2f}%  {wr_recent}")
        rows.append({"名称": name, "代码": ts_code, "信号数": len(arr),
                     "胜率%": round(wr, 1), "均收益%": round(arr.mean(), 2),
                     "中位%": round(float(np.median(arr)), 2),
                     "盈亏比": round(float(pl), 2), "期望%": round(float(ev), 2)})

    out = os.path.join(TDX_BT, "output", "top3_winrate.csv")
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n✅ 已保存: {out}")


if __name__ == "__main__":
    main()
