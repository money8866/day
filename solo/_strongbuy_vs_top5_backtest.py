# -*- coding: utf-8 -*-
"""强买 vs Top5 胜率 A/B 回测 (同等大盘环境)

闸门: 三指数20日动量均值 > +3% (与生产一致), 买入: 次日开盘, 持有 T+5, 盘中-7%止损.
分组对比 (同一批信号集, 同一闸门通过日):
  strong_all   强买全量   (满足强买结构条件的全部信号, 不限数量/评分)
  strong_top5  强买∩Top5  (评分Top5中同时满足强买条件)
  top5_base    评分Top5   (生产基准: 每日按评分取前5)
  top5_nonstrong Top5非强买 (评分Top5中不满足强买条件)
  nonstrong_all 非强买全量 (所有不满足强买条件的信号)
  all_sig      全量信号    (参考: 不做Top5截断)
  strong_first 强买优先Top5 (生产当前逻辑: 强买按评分优先, 补足非强买, 取5)

强买判定: 复刻 volume_surge_select.py detect_volume_surge_swing 的5条条件.
tdx 信号要求收盘>=MA20, 故"回踩MA20下方+刚红柱"与"评分65-80+量比1.0-1.5+回踩MA20"
两条在本信号集天然不触发, 实际生效为: 中回调+刚红柱 / 浅回调+刚红柱+评分>=70 /
红柱回调(缩短或反弹)+评分>=70+量比>=0.9 (20260810 门槛由1.0放宽至0.9, 允许放量后温和缩量整理).
评分口径: tdx total_score = base_score + 强者恒强加分 (与select的纯量能评分略有差异).
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


def is_strong_buy(df_pre, i, score):
    """复刻 select 强买判定 (tdx 指标版). 返回 (是否强买, 原因)"""
    C = df_pre["close"].values
    H = df_pre["high"].values
    L = df_pre["low"].values
    VOL = df_pre["vol"].values
    ma20 = df_pre["ma20"].values
    vol_ratio = df_pre["vol_ratio"].values
    macd_bar = df_pre["macd_bar"].values
    macd_status = df_pre["macd_status"].values
    if i < 5:
        return False, ""

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
    retrace_type = ('浅回调' if retrace_ratio < 30
                    else ('中回调' if retrace_ratio < 50 else '深回调'))

    cur_bar = float(macd_bar[i])
    prev_bar = float(macd_bar[i - 1])
    prev2_bar = float(macd_bar[i - 2])
    fresh_red = (macd_status[i] == 2)                     # 刚刚红柱
    red_retrace = (cur_bar > 0 and prev_bar > 0
                   and cur_bar < abs(macd_bar[i - 4]) * 0.7)  # 红柱回调缩短
    red_bounce = (cur_bar > 0 and prev_bar > 0
                  and cur_bar > prev_bar and prev_bar < prev2_bar)  # 红柱回调后反弹

    if pos_ma20 < 0 and fresh_red:
        return True, '回踩MA20下方+刚红柱'
    if retrace_type == '中回调' and fresh_red:
        return True, '中回调+刚红柱'
    if retrace_type == '浅回调' and fresh_red and score >= 70:
        return True, '浅回调+刚红柱+高评分'
    if 65 <= score < 80 and 1.0 <= today_vr < 1.5 and -3 <= pos_ma20 < 0:
        return True, '评分65-80+量比1.0-1.5+回踩MA20'
    if (red_retrace or red_bounce) and score >= 70 and today_vr >= 0.9:
        return True, '红柱回调+高评分+量比达标'
    return False, ""


def trade(df, i_buy, buy_close, hold):
    """买入后持有hold个交易日, 盘中-7%止损. 返回收益%或None"""
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
    print("  强买 vs Top5 胜率 A/B (T+5, 止损-7%, 三指数动量>+3%闸门)")
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
        if not ts_code or ts_code[0] not in "630":
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

    # --- 分组撮合 (同闸门, 同入场, 同止损) ---
    groups = {k: [] for k in ("strong_all", "strong_top5", "top5_base",
                              "top5_nonstrong", "nonstrong_all", "all_sig",
                              "strong_first")}
    n_strong = 0          # 强买信号累计笔数
    n_reason = {}         # 强买原因分布
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
            if sc <= 0:
                continue
            strong, reason = is_strong_buy(kline_dict[ts_code], i, float(sc))
            cands.append((ts_code, float(sc), strong))
            if strong:
                n_strong += 1
                n_reason[reason] = n_reason.get(reason, 0) + 1
        if not cands:
            continue
        cands.sort(key=lambda x: -x[1])
        top5 = cands[:MAX_DAILY]
        strong_all = [c for c in cands if c[2]]
        strong_top5 = [c for c in top5 if c[2]]
        top5_nonstrong = [c for c in top5 if not c[2]]
        strong_sorted = sorted(strong_all, key=lambda x: -x[1])
        rest_sorted = sorted([c for c in cands if not c[2]], key=lambda x: -x[1])
        strong_first = (strong_sorted + rest_sorted)[:MAX_DAILY]

        def _trade(sel, gkey):
            for ts_code, _, _ in sel:
                df = kline_dict.get(ts_code)
                if df is None:
                    continue
                i = date_idx_map.get(ts_code, {}).get(td)
                if i is None or i + 1 >= len(df):
                    continue
                r = trade(df, i + 1, float(df.iloc[i + 1]["open"]), HOLD_DAYS)
                if r is not None:
                    groups[gkey].append(r)

        _trade(strong_all, "strong_all")
        _trade(strong_top5, "strong_top5")
        _trade(top5, "top5_base")
        _trade(top5_nonstrong, "top5_nonstrong")
        _trade([c for c in cands if not c[2]], "nonstrong_all")
        _trade(cands, "all_sig")
        _trade(strong_first, "strong_first")

    # --- 输出 ---
    names = {"strong_all": "强买全量(结构信号)",
             "strong_top5": "强买∩Top5",
             "top5_base": "评分Top5(生产基准)",
             "top5_nonstrong": "Top5非强买",
             "nonstrong_all": "非强买全量",
             "all_sig": "全量信号(参考)",
             "strong_first": "强买优先Top5(生产当前)"}
    print("\n" + "=" * 84)
    print(f"  强买 vs Top5 胜率对比 (闸门通过日 {passed_days} 天, T+{HOLD_DAYS}, 止损-7%)")
    print("=" * 84)
    print(f"  {'分组':<22} {'笔数':>6} {'胜率':>7} {'均收益':>8} {'中位':>7} "
          f"{'盈亏比':>6} {'正期望':>7}")
    rows = []
    for key in ("top5_base", "strong_all", "strong_first", "strong_top5",
                "top5_nonstrong", "nonstrong_all", "all_sig"):
        rets = np.array(groups[key])
        n = len(rets)
        if n == 0:
            print(f"  {names[key]:<22} {0:>6}  无信号")
            continue
        wr = (rets > 0).mean() * 100
        ar = rets.mean()
        md = np.median(rets)
        wins = rets[rets > 0]
        losses = rets[rets <= 0]
        pl = (wins.mean() / abs(losses.mean())) if len(losses) else np.inf
        ev = wr / 100 * wins.mean() + (1 - wr / 100) * losses.mean()
        print(f"  {names[key]:<22} {n:>6} {wr:>6.1f}% {ar:>+7.2f}% "
              f"{md:>+6.2f}% {pl:>5.2f} {ev:>+6.2f}%")
        rows.append({"group": key, "name": names[key], "trades": n,
                     "winrate": round(wr, 1), "avg_ret": round(ar, 2),
                     "median": round(md, 2), "pl_ratio": round(pl, 2),
                     "expect": round(ev, 2)})

    print("\n  强买信号累计 " + f"{n_strong} 笔, 原因分布:")
    for k, v in sorted(n_reason.items(), key=lambda x: -x[1]):
        print(f"    {k}: {v}")

    out_dir = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "tdx_backtest", "output"))
    os.makedirs(out_dir, exist_ok=True)
    pd.DataFrame(rows).to_csv(os.path.join(out_dir, "strongbuy_vs_top5_compare.csv"),
                              index=False, encoding="utf-8-sig")
    print(f"\n✅ 对比表已保存: {os.path.join(out_dir, 'strongbuy_vs_top5_compare.csv')}")


if __name__ == "__main__":
    run()
