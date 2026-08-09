# -*- coding: utf-8 -*-
"""下蹲质量因子 A/B 回测

闸门: 三指数20日动量均值 > +3% (生产配置)
三种选股方案对比:
  base     基准: 现策略评分 Top5
  f60      下蹲过滤: 下蹲分>=60 才允许入, 再按现评分 Top5
  rank     下蹲重排序: 综合分=现评分+下蹲分 排序 Top5

下蹲质量评分(信号日 T, 往前20日窗口找高点):
  回踩到位30分(pullback -5~-10%满分) / 缩量25分 / 收敛20分 / 支撑15分 / 时间10分
  无下蹲结构(高点不在 T-15~T-3 / 回调不在-3~-15%) → 0 分
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
START, END = "20240101", "20260807"


def squat_score(df: pd.DataFrame, i: int) -> float:
    """信号日 T=i 的下蹲质量评分 0~100, 无下蹲结构返回 0"""
    C = df["close"].values
    H = df["high"].values
    L = df["low"].values
    V = df["vol"].values
    w = 20
    s = max(0, i - w)
    if i - s < 12:
        return 0.0
    hw = H[s:i + 1]
    pk_local = int(np.argmax(hw))
    pk_pos = s + pk_local            # 全局索引
    peak_price = hw[pk_local]
    squat_days = i - pk_pos
    if not (2 <= squat_days <= 15):
        return 0.0
    pullback = (C[i] / peak_price - 1) * 100
    if not (-15.0 <= pullback <= -3.0):
        return 0.0
    # 缩量: 下蹲期(peak后~T) 均量 / 窗口内放量段均量
    squat_vol = float(np.mean(V[pk_pos + 1:i + 1])) if i > pk_pos else 0.0
    base_vol = float(np.mean(V[s:pk_pos + 1])) if pk_pos > s else 0.0
    vol_ratio = squat_vol / max(base_vol, 1.0)
    # 收敛: 下蹲期振幅 / 窗口振幅
    if i > pk_pos:
        amp_squat = float(np.mean(
            (H[pk_pos + 1:i + 1] - L[pk_pos + 1:i + 1])
            / np.maximum(C[pk_pos + 1:i + 1], 0.01)))
    else:
        amp_squat = 1.0
    amp_win = float(np.mean(
        (H[s:i + 1] - L[s:i + 1]) / np.maximum(C[s:i + 1], 0.01)))
    conv = amp_squat / max(amp_win, 1e-9)
    ma20 = float(df["ma20"].iloc[i])
    ma10 = float(df["ma10"].iloc[i])

    score = 0.0
    depth = -pullback
    # 回踩到位 30
    if 5.0 <= depth <= 10.0:
        score += 30
    elif 3.0 <= depth < 5.0:
        score += 30 * (depth - 3.0) / 2.0
    elif 10.0 < depth <= 15.0:
        score += 30 * (1 - (depth - 10.0) / 5.0 * 0.5)
    # 缩量 25
    if vol_ratio <= 0.6:
        score += 25
    elif vol_ratio < 1.0:
        score += 25 * (1 - (vol_ratio - 0.6) / 0.4)
    # 收敛 20
    if conv <= 0.6:
        score += 20
    elif conv < 1.0:
        score += 20 * (1 - (conv - 0.6) / 0.4)
    # 支撑 15
    if C[i] > ma20:
        score += 15
    elif C[i] > ma10:
        score += 10
    else:
        score += 5
    # 时间 10
    if 3 <= squat_days <= 8:
        score += 10
    elif squat_days in (2, 9, 10, 11, 12):
        score += 6
    else:
        score += 3
    return score


def run():
    vf = VolSurgeFilters()
    t0 = time.time()
    print("=" * 60)
    print("  下蹲质量因子 A/B 回测 (T+5, 止损-7%, 三指数动量>+3%闸门)")
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

    # --- 信号 + 下蹲分 ---
    t0 = time.time()
    signals_dict = {}
    squat_dict = {}   # ts_code -> {trade_date: squat_score}
    for ts_code, df_pre in kline_dict.items():
        sig = volume_surge_strategy_vectorized(df_pre, ts_code, vf)
        if not sig.any():
            continue
        signals_dict[ts_code] = sig
        sq_map = {}
        idxs = np.where(sig)[0]
        tds = df_pre["trade_date"].values
        for i in idxs:
            sq_map[str(tds[i])] = squat_score(df_pre, int(i))
        squat_dict[ts_code] = sq_map
    print(f"[Signal] {len(signals_dict)} 只有信号+下蹲分, 耗时 {time.time()-t0:.1f}s")

    all_dates = set()
    for df in kline_dict.values():
        all_dates.update(df["trade_date"].tolist())
    trade_dates = sorted(d for d in all_dates if START <= d <= END)
    date_idx_map = {c: dict(zip(d["trade_date"], d.index))
                    for c, d in kline_dict.items()}

    # --- 三种方案逐日撮合 ---
    res = {k: [] for k in ("base", "f60", "rank")}
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
                sq = squat_dict.get(ts_code, {}).get(str(td), 0.0)
                cands.append((ts_code, float(sc), float(sq)))
        if not cands:
            continue
        cands.sort(key=lambda x: -x[1])          # 现评分降序
        # base: 现评分 Top5
        sel_base = [c[0] for c in cands[:MAX_DAILY]]
        # f60: 下蹲>=60 过滤后按现评分 Top5
        cands_f = [c for c in cands if c[2] >= 60]
        sel_f60 = [c[0] for c in cands_f[:MAX_DAILY]] if cands_f else []
        # rank: 综合分=现评分+下蹲分 排序 Top5
        cands_r = sorted(cands, key=lambda x: -(x[1] + x[2]))
        sel_rank = [c[0] for c in cands_r[:MAX_DAILY]]

        for key, sel in (("base", sel_base), ("f60", sel_f60),
                         ("rank", sel_rank)):
            for ts_code in sel:
                df = kline_dict.get(ts_code)
                if df is None:
                    continue
                il = df.index[df["trade_date"] == td].tolist()
                if not il:
                    continue
                i = il[0]
                buy_close = float(df.iloc[i]["close"])
                stopped = False
                for j in range(i + 1, min(i + HOLD_DAYS + 1, len(df))):
                    if df.iloc[j]["low"] / buy_close - 1 <= -0.07:
                        res[key].append(-7.0)
                        stopped = True
                        break
                if not stopped and i + HOLD_DAYS < len(df):
                    res[key].append(
                        (df.iloc[i + HOLD_DAYS]["close"] / buy_close - 1) * 100)

    # --- 输出 ---
    names = {"base": "基准(现评分Top5)",
             "f60": "下蹲>=60过滤",
             "rank": "综合分重排序"}
    print("\n" + "=" * 78)
    print(f"  下蹲因子 A/B (闸门通过日 {passed_days} 天, T+{HOLD_DAYS}, 止损-7%)")
    print("=" * 78)
    print(f"  {'方案':<16} {'信号':>6} {'胜率':>7} {'均收益':>8} {'中位':>7} "
          f"{'盈亏比':>6} {'正期望':>7}")
    rows = []
    for key in ("base", "f60", "rank"):
        rets = np.array(res[key])
        n = len(rets)
        if n == 0:
            print(f"  {names[key]:<16} {0:>6}  无信号")
            continue
        wr = (rets > 0).mean() * 100
        ar = rets.mean()
        md = np.median(rets)
        wins = rets[rets > 0]
        losses = rets[rets <= 0]
        pl = (wins.mean() / abs(losses.mean())) if len(losses) else np.inf
        ev = wr / 100 * wins.mean() + (1 - wr / 100) * losses.mean()
        print(f"  {names[key]:<16} {n:>6} {wr:>6.1f}% {ar:>+7.2f}% "
              f"{md:>+6.2f}% {pl:>5.2f} {ev:>+6.2f}%")
        rows.append({"scheme": key, "name": names[key], "signals": n,
                     "winrate": round(wr, 1), "avg_ret": round(ar, 2),
                     "median": round(md, 2), "pl_ratio": round(pl, 2),
                     "expect": round(ev, 2)})
    # 下蹲分分布参考
    all_sq = []
    for ts_code, sq_map in squat_dict.items():
        all_sq.extend(sq_map.values())
    arr_sq = np.array(all_sq)
    print(f"\n  下蹲分分布: n={len(arr_sq)} 平均={arr_sq.mean():.1f} "
          f"中位={np.median(arr_sq):.0f} ≥60占比={(arr_sq >= 60).mean()*100:.0f}%")

    out_dir = os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "tdx_backtest", "output"))
    os.makedirs(out_dir, exist_ok=True)
    pd.DataFrame(rows).to_csv(os.path.join(out_dir, "squat_factor_compare.csv"),
                              index=False, encoding="utf-8-sig")
    print(f"\n✅ 对比表已保存: {os.path.join(out_dir, 'squat_factor_compare.csv')}")


if __name__ == "__main__":
    run()
