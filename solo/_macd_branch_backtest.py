# -*- coding: utf-8 -*-
"""MACD 形态分支回测：红柱回调(非共进) vs 绿柱缩短(共进形态) vs 刚刚红柱 vs 加速度

口径与生产一致: 三指数20日动量>+3%闸门, 次日开盘买入, 持有T+5, 盘中-7%止损.
信号来源: volume_surge_strategy_vectorized (tdx), 与 volume_surge_select 同一套.
分类: 按生产 volume_surge_strategy 第12步五分支优先级 ①加速度→②绿柱缩短→③刚刚红柱→④红柱回调缩短→⑤红柱回调后反弹.
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
from strategy_backtest import load_stock_names, _rolling_mean_np
from volume_surge_strategy import (precompute_indicators,
                                   volume_surge_strategy_vectorized,
                                   VolSurgeFilters)
from winrate_vs_market_env import build_market_env, INDEX_CODES

MOM_THRESHOLD = 3.0
HOLD_DAYS = 5
MAX_DAILY = 3          # 生产每日 Top3
STOP = -7.0
START, END = "20240101", "20260814"

BRANCHES = ("加速度(①最早)", "绿柱缩短即将红柱(②共进形态)", "刚刚红柱(③)",
            "红柱回调缩短(④)", "红柱回调后反弹(⑤)")
RED_PULLBACK = ("红柱回调缩短(④)", "红柱回调后反弹(⑤)")


def compute_base_score(df_pre, i):
    """复刻策略第1/2/3/10步的 base_score（仅用于分支①的70分门槛）"""
    H = df_pre["high"].values; L = df_pre["low"].values
    C = df_pre["close"].values; VOL = df_pre["vol"].values
    start = max(0, i - 200)
    vol_arr = VOL[start:i + 1]
    vol_ma20_local = _rolling_mean_np(vol_arr, 20)
    vol_ratio_local = vol_arr / np.maximum(vol_ma20_local, 1)
    max_vol_ratio = float(np.nanmax(vol_ratio_local))
    vol_ratio_gt2 = int(np.sum(vol_ratio_local > 2.0))
    amplitude = (H[start:i + 1] - L[start:i + 1]) / np.maximum(C[start:i + 1], 0.01) * 100
    avg_amplitude = float(np.mean(amplitude[-120:]))
    amp_gt8_count = int(np.sum(amplitude > 8))
    range_high = float(np.max(H[start:i + 1]))
    range_low = float(np.min(L[start:i + 1]))
    range_swing = (range_high / range_low - 1) * 100 if range_low > 0 else 0
    return (min(max_vol_ratio / 5.0, 1) * 30 + min(vol_ratio_gt2 / 7, 1) * 20 +
            min(avg_amplitude / 7, 1) * 20 + min(amp_gt8_count / 15, 1) * 15 +
            min(range_swing / 60, 1) * 15)


def classify_branch(df_pre, i, base_score):
    """复刻生产第12步五分支优先级, 返回分支名"""
    macd_bar = df_pre["macd_bar"].values
    cur = float(macd_bar[i]); prev = float(macd_bar[i - 1]); prev2 = float(macd_bar[i - 2])
    # ① 加速度由负转正（最早）: base_score >= 70
    if i >= 3:
        accel_1 = cur - prev; accel_2 = prev - prev2
        if (cur < 0 and cur > min(macd_bar[i - 3:i]) and
                accel_2 < 0 < accel_1 and base_score >= 70):
            return "加速度(①最早)"
    # ② 绿柱连续缩短 → 共进形态
    if cur < 0 and cur > prev > prev2:
        return "绿柱缩短即将红柱(②共进形态)"
    # ③ 刚刚红柱
    if prev < 0 < cur:
        return "刚刚红柱(③)"
    # ④ 红柱回调缩短
    if cur > 0 and prev > 0 and cur < abs(macd_bar[i - 4]) * 0.7:
        return "红柱回调缩短(④)"
    # ⑤ 红柱回调后反弹
    if cur > 0 and prev > 0 and cur > prev and prev < prev2:
        return "红柱回调后反弹(⑤)"
    return "未知"


def trade(df, i_buy, buy_close, hold):
    for j in range(i_buy + 1, min(i_buy + hold + 1, len(df))):
        if df.iloc[j]["low"] / buy_close - 1 <= STOP / 100.0:
            return STOP
    if i_buy + hold < len(df):
        return (df.iloc[i_buy + hold]["close"] / buy_close - 1) * 100.0
    return None


def stats(rets):
    rets = np.asarray(rets, dtype=float)
    n = len(rets)
    if n == 0:
        return None
    wr = (rets > 0).mean() * 100
    ar = rets.mean()
    md = np.median(rets)
    wins = rets[rets > 0]; losses = rets[rets <= 0]
    pl = (wins.mean() / abs(losses.mean())) if len(losses) else np.inf
    ev = wr / 100 * wins.mean() + (1 - wr / 100) * losses.mean()
    return {"trades": n, "winrate": round(wr, 1), "avg_ret": round(ar, 2),
            "median": round(md, 2), "pl_ratio": round(pl, 2), "expect": round(ev, 2)}


def run():
    vf = VolSurgeFilters()
    t0 = time.time()
    print("=" * 72)
    print("  MACD 形态分支回测 (T+5, 止损-7%, 三指数动量>+3%闸门)")
    print(f"  区间: {START} ~ {END}  信号: volume_surge_strategy_vectorized")
    print("=" * 72)
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
    print(f"[Market] 环境特征: {len(env_df)} 天")

    kline_dict = {}
    for path in iter_all_day_files(markets=("SH", "SZ")):
        ts_code = tdx_filename_to_ts_code(path)
        if not ts_code or ts_code[0] not in "630":
            continue
        df = load_kline(ts_code, start_date=load_start, end_date=END)
        if df.empty or len(df) < 180:
            continue
        kline_dict[ts_code] = precompute_indicators(df)
    print(f"[Load] 加载 {len(kline_dict)} 只, 耗时 {time.time()-t0:.1f}s")

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

    # 每条交易记录: (branch, mom_gate_pass, in_top3_r3, in_top3_r4, pos_ma20, ret)
    recs = []
    n_branch = {}
    # 形态偏好档（与生产 r4 排序一致）：红柱回调缩短④最优，即将红柱②最差
    MACD_RANK_BT = {
        '红柱回调缩短(④)': 0,
        '红柱回调后反弹(⑤)': 1,
        '刚刚红柱(③)': 1,
        '加速度(①最早)': 1,
        '绿柱缩短即将红柱(②共进形态)': 2,
    }
    for td in trade_dates:
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
            df_pre = kline_dict[ts_code]
            base = compute_base_score(df_pre, i)
            br = classify_branch(df_pre, i, base)
            c_i = float(df_pre.iloc[i]["close"])
            m_i = float(df_pre.iloc[i]["ma20"])
            pos = (c_i / m_i - 1) * 100 if m_i > 0 else 0.0
            cands.append((ts_code, float(sc), br, pos))
            n_branch[br] = n_branch.get(br, 0) + 1
        if not cands:
            continue
        # r3（生产现役）: 距MA20<=3优先 + 距MA20升序 + 评分降序
        top3_r3 = sorted(cands, key=lambda x: (
            x[3] > 3, x[3], -x[1]))[:MAX_DAILY]
        # r4（新增）: 贴地优先 + 红柱回调缩短分支优先 + 距MA20升序 + 评分降序
        top3_r4 = sorted(cands, key=lambda x: (
            x[3] > 3, MACD_RANK_BT.get(x[2], 1), x[3], -x[1]))[:MAX_DAILY]
        r3_codes = {c[0] for c in top3_r3}
        r4_codes = {c[0] for c in top3_r4}

        env = env_df.loc[td] if td in env_df.index else None
        mom = float(env["mom20_avg"]) if env is not None else None

        for ts_code, _, br, pos in cands:
            df_pre = kline_dict.get(ts_code)
            if df_pre is None:
                continue
            i = date_idx_map.get(ts_code, {}).get(td)
            if i is None or i + 1 >= len(df_pre):
                continue
            r = trade(df_pre, i + 1, float(df_pre.iloc[i + 1]["open"]), HOLD_DAYS)
            if r is not None:
                recs.append((br, mom, ts_code in r3_codes, ts_code in r4_codes, pos, r))

    # 汇总
    def _table(recs_sub, title):
        rows = []
        print("\n" + "=" * 96)
        print(f"  {title}  共 {len(recs_sub)} 笔")
        print("=" * 96)
        print(f"  {'分组':<26} {'笔数':>6} {'胜率':>7} {'均收益':>8} {'中位':>7} {'盈亏比':>6} {'正期望':>7}")
        order = [("全量信号(基准)", recs_sub),
                 ("每日Top3_r3(生产现役)", [r for r in recs_sub if r[2]]),
                 ("每日Top3_r4(新增形态优先)", [r for r in recs_sub if r[3]]),
                 ("红柱回调④⑤合计(今日形态)", [r for r in recs_sub if r[0] in RED_PULLBACK]),
                 ("红柱回调④⑤∩Top3_r4", [r for r in recs_sub if r[0] in RED_PULLBACK and r[3]]),
                 ("绿柱缩短②共进形态", [r for r in recs_sub if r[0].startswith("绿柱缩短")]),
                 ("绿柱缩短②∩Top3_r4", [r for r in recs_sub if r[0].startswith("绿柱缩短") and r[3]]),
                 ("刚刚红柱③", [r for r in recs_sub if r[0].startswith("刚刚红柱")]),
                 ("加速度①", [r for r in recs_sub if r[0].startswith("加速度")]),
                 ]
        for name, rs in order:
            st = stats([r[5] for r in rs])
            if st is None:
                print(f"  {name:<26} {'0':>6}  无信号")
                continue
            print(f"  {name:<26} {st['trades']:>6} {st['winrate']:>6.1f}% "
                  f"{st['avg_ret']:>+7.2f}% {st['median']:>+6.2f}% "
                  f"{st['pl_ratio']:>5.2f} {st['expect']:>+6.2f}%")
            rows.append({"group": name, **st})
        # 细分分支
        print("  --- 细分分支 ---")
        for br in BRANCHES:
            rs = [r for r in recs_sub if r[0] == br]
            st = stats([r[5] for r in rs])
            if st is None:
                print(f"  {br:<26} {'0':>6}  无信号")
                continue
            print(f"  {br:<26} {st['trades']:>6} {st['winrate']:>6.1f}% "
                  f"{st['avg_ret']:>+7.2f}% {st['median']:>+6.2f}% "
                  f"{st['pl_ratio']:>5.2f} {st['expect']:>+6.2f}%")
        return rows

    def _bucket_label(pos):
        if pos <= 0:
            return "≤0%(跌破/贴地)"
        if pos <= 3:
            return "0~3%(贴地)"
        if pos <= 5:
            return "3~5%"
        if pos <= 8:
            return "5~8%"
        return ">8%(追高)"

    BUCKETS = ["≤0%(跌破/贴地)", "0~3%(贴地)", "3~5%", "5~8%", ">8%(追高)"]

    def _bucket_table(recs_sub, title, groups):
        """按 距MA20 分档输出 胜率/均收益/正期望 矩阵"""
        print("\n" + "=" * 96)
        print(f"  📏 {title}  (按距MA20分档, 信号日收盘距MA20)")
        print("=" * 96)
        header = f"  {'形态分组':<18}" + "".join(f"{b:>17}" for b in BUCKETS)
        print(header)
        for gname, mask in groups:
            print(f"  {'-'*96}")
            for metric in ("胜率%", "均收益%", "正期望%"):
                cells = []
                for b in BUCKETS:
                    rs = [r for r in recs_sub if mask(r) and _bucket_label(r[4]) == b]
                    st = stats([r[5] for r in rs])
                    if st is None:
                        cells.append("  -/-  ")
                        continue
                    if metric == "胜率%":
                        cells.append(f"{st['trades']}笔/{st['winrate']:.0f}%")
                    elif metric == "均收益%":
                        cells.append(f"{st['avg_ret']:+.1f}%")
                    else:
                        cells.append(f"{st['expect']:+.1f}%")
                print(f"  {gname+metric:<18}" + "".join(f"{c:>17}" for c in cells))

    gated = [r for r in recs if r[1] is not None and r[1] > MOM_THRESHOLD]
    ungated = [r for r in recs if r[1] is not None]
    rows = _table(gated, f"✅ 闸门内 (三指数动量>+{MOM_THRESHOLD}%)")
    rows += _table(ungated, "📊 全部日期 (无闸门, 参考)")

    # --- 距MA20 分档矩阵 (闸门内) ---
    groups = [
        ("全量", lambda r: True),
        ("红柱回调④⑤", lambda r: r[0] in RED_PULLBACK),
        ("Top3_r4", lambda r: r[3]),
        ("绿柱缩短②", lambda r: r[0].startswith("绿柱缩短")),
        ("刚刚红柱③", lambda r: r[0].startswith("刚刚红柱")),
        ("加速度①", lambda r: r[0].startswith("加速度")),
    ]
    _bucket_table(gated, "闸门内", groups)
    _bucket_table(ungated, "全部日期(无闸门参考)", groups)

    # --- 关键组合结论行 ---
    print("\n" + "=" * 96)
    print("  关键组合 (闸门内)")
    print("=" * 96)
    combos = [
        ("红柱回调④⑤∩距MA20≤3%", lambda r: r[0] in RED_PULLBACK and r[4] <= 3),
        ("红柱回调④⑤∩距MA20≤3%∩Top3_r4", lambda r: r[0] in RED_PULLBACK and r[4] <= 3 and r[3]),
        ("红柱回调④⑤∩距MA20≤5%", lambda r: r[0] in RED_PULLBACK and r[4] <= 5),
        ("红柱回调④⑤∩距MA20>8%", lambda r: r[0] in RED_PULLBACK and r[4] > 8),
        ("绿柱缩短②∩距MA20≤3%", lambda r: r[0].startswith("绿柱缩短") and r[4] <= 3),
        ("绿柱缩短②∩距MA20≤3%∩Top3_r4", lambda r: r[0].startswith("绿柱缩短") and r[4] <= 3 and r[3]),
        ("全量∩距MA20≤3%", lambda r: r[4] <= 3),
        ("全量∩距MA20≤3%∩Top3_r4", lambda r: r[4] <= 3 and r[3]),
        ("Top3_r4∩距MA20≤3%", lambda r: r[3] and r[4] <= 3),
        ("Top3_r4∩红柱回调缩短④", lambda r: r[3] and r[0].startswith("红柱回调缩短")),
    ]
    for name, mask in combos:
        st = stats([r[5] for r in gated if mask(r)])
        if st is None:
            print(f"  {name:<34} 无信号")
            continue
        print(f"  {name:<34} {st['trades']:>5}笔 {st['winrate']:>5.1f}% "
              f"{st['avg_ret']:>+6.2f}% 中位{st['median']:>+6.2f}% "
              f"盈亏比{st['pl_ratio']:>4.2f} 期望{st['expect']:>+6.2f}%")
        rows.append({"group": name, "gate": "on", **st})
    for name, mask in combos:
        st = stats([r[5] for r in ungated if mask(r)])
        if st is None:
            continue
        rows.append({"group": name, "gate": "off", **st})

    print("\n  信号形态分布 (闸门内/全部):")
    for br in BRANCHES + ("未知",):
        gn = sum(1 for r in gated if r[0] == br)
        al = n_branch.get(br, 0)
        print(f"    {br:<24} 闸门内 {gn:>6}  |  全部 {al:>6}")

    out_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tdx_backtest", "output"))
    os.makedirs(out_dir, exist_ok=True)
    pd.DataFrame(rows).to_csv(os.path.join(out_dir, "macd_branch_backtest.csv"),
                              index=False, encoding="utf-8-sig")
    print(f"\n✅ 对比表已保存: {os.path.join(out_dir, 'macd_branch_backtest.csv')}")


if __name__ == "__main__":
    run()
