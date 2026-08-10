# -*- coding: utf-8 -*-
"""参数 A/B 回测：一次信号计算+缓存，网格撮合优化生产输出参数

优化维度:
  ma20_band   距MA20阈值(r2排序: <=band 优先), 基准8, 测 [5,8,10,12]
  max_daily   每日最多只数, 基准5, 测 [3,5,7]
  score_min   评分下限, 基准65, 测 [60,65,70]
  hold_days   持有期, 基准5, 测 [3,5,10]

口径与主回测一致: 三指数20日动量均值>+3%闸门, 次日开盘买入, 盘中-7%止损, r2排序.
"""
import os, sys, time, pickle
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

TDX_BT = r"d:\mystock\tdx_backtest"
SOLO_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TDX_BT)
from data_loader import load_kline, iter_all_day_files, tdx_filename_to_ts_code
from strategy_backtest import load_stock_names
from volume_surge_strategy import precompute_indicators, volume_surge_strategy_vectorized, VolSurgeFilters

MOM_THRESHOLD = 3.0
STOP = -7.0
START, END = "20240101", "20260807"
CACHE = os.path.join(TDX_BT, "output", "param_ab_cache.pkl")


def load_or_build_cache(force=False):
    """信号集缓存: (kline_dict, signals_dict, idx3_mom20, trade_dates)"""
    if os.path.exists(CACHE) and not force:
        with open(CACHE, "rb") as f:
            data = pickle.load(f)
        print(f"[Cache] 加载缓存: {CACHE}")
        return data

    t0 = time.time()
    print("=" * 60)
    print("  信号集构建中 (首次约10-15分钟, 之后走缓存)")
    print("=" * 60)
    load_stock_names()

    # 大盘状态: 三指数20日动量均值
    dt = datetime.strptime(START, "%Y%m%d")
    load_start = (dt - timedelta(days=400)).strftime("%Y%m%d")
    idx3_mom20 = {}
    try:
        mom_maps = []
        for _code in ("000001.SH", "000300.SH", "399006.SZ"):
            _df = load_kline(_code, start_date=load_start, end_date=END)
            if not _df.empty:
                _df = precompute_indicators(_df)
                _mom = (_df["close"] / _df["close"].shift(20) - 1) * 100
                mom_maps.append(dict(zip(_df["trade_date"].values, _mom.values)))
        if len(mom_maps) == 3:
            _all = sorted(set().union(*[set(m) for m in mom_maps]))
            for _d in _all:
                _vals = [m[_d] for m in mom_maps if _d in m and not pd.isna(m[_d])]
                if len(_vals) == 3:
                    idx3_mom20[_d] = float(np.mean(_vals))
        print(f"[Market] 三指数动量: {len(idx3_mom20)} 天, >+3%天="
              f"{sum(1 for v in idx3_mom20.values() if v > MOM_THRESHOLD)}")
    except Exception as e:
        print(f"[Market] 加载失败: {e}")

    # 全市场K线
    kline_dict, codes_loaded = {}, 0
    for path in iter_all_day_files(markets=("SH", "SZ")):
        ts_code = tdx_filename_to_ts_code(path)
        if not ts_code or ts_code[0] not in "630":
            continue
        df = load_kline(ts_code, start_date=load_start, end_date=END)
        if df.empty or len(df) < 180:
            continue
        kline_dict[ts_code] = precompute_indicators(df)
        codes_loaded += 1
    print(f"[Load] {codes_loaded} 只, {time.time()-t0:.1f}s")

    # 信号
    vf = VolSurgeFilters()
    t0 = time.time()
    signals_dict = {}
    for ts_code, df_pre in kline_dict.items():
        sig = volume_surge_strategy_vectorized(df_pre, ts_code, vf)
        if sig.any():
            signals_dict[ts_code] = sig
    print(f"[Signal] {len(signals_dict)} 只有信号, {time.time()-t0:.1f}s")

    all_dates = set()
    for df in kline_dict.values():
        all_dates.update(df["trade_date"].tolist())
    trade_dates = sorted(d for d in all_dates if START <= d <= END)

    data = (kline_dict, signals_dict, idx3_mom20, trade_dates)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "wb") as f:
        pickle.dump(data, f)
    print(f"[Cache] 已保存: {CACHE}")
    return data


def backtest_params(kline_dict, signals_dict, idx3_mom20, trade_dates,
                    ma20_band=8.0, max_daily=5, score_min=65.0, hold_days=5):
    """单参数组撮合, 返回 (胜率, 均收益, 中位, 盈亏比, 期望, 笔数, 选股1-5天)"""
    date_idx_map = {c: dict(zip(d["trade_date"], d.index))
                    for c, d in kline_dict.items()}
    all_returns, daily_counts = [], []
    for td in trade_dates:
        mom = idx3_mom20.get(td)
        if mom is not None and mom <= MOM_THRESHOLD:
            daily_counts.append(0)
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
            if sc < score_min:
                continue
            _df = kline_dict.get(ts_code)
            if _df is None:
                continue
            _ma20 = float(_df.iloc[i]["ma20"])
            _pos = (float(_df.iloc[i]["close"]) / _ma20 - 1) * 100 if _ma20 > 0 else 99.0
            cands.append((ts_code, float(sc), _pos))
        # r2 排序: 距MA20升序优先(<=band在前), >band排尾部补足
        cands.sort(key=lambda x: (x[2] > ma20_band, x[2]))
        selected = [c[0] for c in cands[:max_daily]]
        daily_counts.append(len(selected))

        for ts_code in selected:
            df = kline_dict.get(ts_code)
            if df is None:
                continue
            idx = df.index[df["trade_date"] == td].tolist()
            if not idx:
                continue
            i = idx[0]
            if i + 1 >= len(df):
                continue
            buy_close = float(df.iloc[i + 1]["open"])
            exit_idx = min(i + 1 + hold_days, len(df) - 1)
            stopped = False
            ret = None
            for j in range(i + 2, exit_idx + 1):
                if df.iloc[j]["low"] / buy_close - 1 <= STOP / 100.0:
                    ret = STOP
                    stopped = True
                    break
            if not stopped:
                if i + 1 + hold_days < len(df):
                    ret = (df.iloc[i + 1 + hold_days]["close"] / buy_close - 1) * 100
            if ret is not None:
                all_returns.append(ret)

    arr = np.array(all_returns) if all_returns else np.array([0.0])
    wins = arr[arr > 0]
    losses = arr[arr <= 0]
    wr = (arr > 0).mean() * 100
    pl = (wins.mean() / abs(losses.mean())) if len(losses) else np.inf
    ev = wr / 100 * wins.mean() + (1 - wr / 100) * losses.mean()
    n_1_5 = int(((np.array(daily_counts) >= 1) & (np.array(daily_counts) <= 5)).sum())
    return (round(wr, 1), round(arr.mean(), 2), round(np.median(arr), 2),
            round(pl, 2), round(ev, 2), len(all_returns), n_1_5)


def main():
    force = "--force" in sys.argv
    kline_dict, signals_dict, idx3_mom20, trade_dates = load_or_build_cache(force)

    # ---- 基准复现验证 (8/5/65/5 应与主回测 45.7% 接近) ----
    base = backtest_params(kline_dict, signals_dict, idx3_mom20, trade_dates,
                           ma20_band=8.0, max_daily=5, score_min=65.0, hold_days=5)
    print("\n[基准] 距MA20<=8% / 每日5 / 评分>=65 / T+5:")
    print(f"  胜率{base[0]}% 均收益{base[1]:+.2f}% 中位{base[2]:+.2f}% 盈亏比{base[3]} "
          f"期望{base[4]:+.2f}% 笔数{base[5]} 选股1-5天{base[6]}")

    print("\n" + "=" * 92)
    print("  单维度 A/B (每次只变一个维度, 其余=基准)")
    print("=" * 92)

    def _show(title, r):
        print(f"  {title:<28} 胜率{r[0]:>5.1f}% 均{r[1]:>+6.2f}% 中位{r[2]:>+6.2f}% "
              f"盈亏比{r[3]:>5.2f} 期望{r[4]:>+6.2f}% 笔{r[5]:>5d}")

    print("\n-- 距MA20阈值 --")
    for band in (5, 8, 10, 12):
        _show(f"距MA20<= {band}%", backtest_params(
            kline_dict, signals_dict, idx3_mom20, trade_dates,
            ma20_band=float(band), max_daily=5, score_min=65.0, hold_days=5))

    print("\n-- 每日最多只数 --")
    for md in (3, 5, 7):
        _show(f"每日 {md} 只", backtest_params(
            kline_dict, signals_dict, idx3_mom20, trade_dates,
            ma20_band=8.0, max_daily=md, score_min=65.0, hold_days=5))

    print("\n-- 评分下限 --")
    for sm in (60, 65, 70):
        _show(f"评分 >= {sm}", backtest_params(
            kline_dict, signals_dict, idx3_mom20, trade_dates,
            ma20_band=8.0, max_daily=5, score_min=float(sm), hold_days=5))

    print("\n-- 持有期 --")
    for hd in (3, 5, 10):
        _show(f"T+{hd}", backtest_params(
            kline_dict, signals_dict, idx3_mom20, trade_dates,
            ma20_band=8.0, max_daily=5, score_min=65.0, hold_days=hd))

    # ---- 组合验证: 各维度最优值组合 (若与基准差异明显) ----
    print("\n" + "=" * 92)
    print("  组合验证 (单维度中表现最好的取值组合)")
    print("=" * 92)
    combos = [
        ("基准 8/5/65/5", 8.0, 5, 65.0, 5),
        ("收窄距MA20 5/5/65/5", 5.0, 5, 65.0, 5),
        ("每日3只 8/3/65/5", 8.0, 3, 65.0, 5),
        ("评分70 8/5/70/5", 8.0, 5, 70.0, 5),
        ("每日3+评分70 8/3/70/5", 8.0, 3, 70.0, 5),
        ("每日3+评分70+距5 5/3/70/5", 5.0, 3, 70.0, 5),
    ]
    for name, band, md, sm, hd in combos:
        _show(name, backtest_params(
            kline_dict, signals_dict, idx3_mom20, trade_dates,
            ma20_band=band, max_daily=md, score_min=sm, hold_days=hd))

    print("\n✅ 参数 A/B 完成")


if __name__ == "__main__":
    main()
