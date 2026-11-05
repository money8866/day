# -*- coding: utf-8 -*-
"""激进主升段策略实验（V10.7.3 之后的研究脚本，不修改任何生产代码）。

回答的问题
----------
用户要求「激进策略，博取大主升段高收益」。上一轮 _bt_bull_entry.py 的结论是
「不突破 hard_cap 时平均仓位仅 22.4%，2025 只拿 +7.13%（满仓 +23.49%）」。
本脚本把激进的三个维度全部打开并实测：

  1. **仓位上限豁免**：信号确认后临时把有效仓位打到 80% / 100% / 120% / 150%，
     突破当日 hard_cap。诊断列，生产上必须由 P0 风险预算重算 risk_cap 并
     由 V10.7 authority 层显式授权，不能只做一层 overlay。
  2. **升仓速度**：锁存期 20/30/50pp/日 甚至无限速，其余时段沿用 10pp/日。
  3. **杠杆**：>100% 部分按融资利率计息（默认年化 6.5%），并做利率敏感性。

最关键的不是进场，而是**退出纪律**：跌破 MA10 / MA20 / 持仓峰值跟踪止损 /
锁存到期 + 重新入场冷却。锁存状态机只用 <=T 数据，无未来函数。

口径（与前三轮回测严格一致）
---------------------------
* 沪深300、T 日盘后决策、T 日收盘按有效仓位成交、承担 T→T+1 收益、5bp 单边换手。
* 现金部分默认 0%（另给 1.5% 敏感性）；杠杆部分按 margin_annual 付息。
* 所有信号只用 trade_date <= T 的收盘价 / 微结构 / 系统状态；未来数据仅用于
  **评估标注**（主升波段起点、天花板），绝不进入仓位决策。
* 同均值恒定仓位对照用于分离「加 beta」与「真择时 alpha」。

输入
----
* cache_daily/stock_data.db :: index_daily_cache (000300.SH)
* cache_micro/micro_daily.db :: micro_daily
* _bt_swing_gate_replay.csv (443 日生产口径有效仓位)
* _bt_opt_replay_on.csv     (443 日 P1/P2 目标、状态、确认结果)
* 复用 _bt_bull_entry.py 的数据加载 / 特征 / 评估 / 指标函数

输出
----
* _bt_bull_aggressive_metrics.json  全部指标
* _bt_bull_aggressive_nav.csv       净值序列
* _bt_bull_aggressive_daily.csv     逐日信号、锁存、有效仓位
* _bt_bull_aggressive_profile.json  建议落地档位参数（不自动写入生产配置）
"""
import os
import json
import sqlite3

import numpy as np
import pandas as pd

import _bt_bull_entry as be

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE_DIR)
DB = os.path.join(ROOT, "cache_daily", "stock_data.db")
BENCH = be.BENCH
FEE_BP = be.FEE_BP
TRADING_DAYS = be.TRADING_DAYS
END = be.END
MARGIN_ANNUAL = 0.065

# 关键区间（决策日口径）
BULL_SEG = ("20250725", "20250915")   # 2025 主升段
FAKE_SEG = ("20260323", "20260515")   # 2026 假反弹段


# ------------------------------------------------------------ 激进信号
def add_signals(d):
    """在 _bt_bull_entry.build_features 的结果上追加激进档专用信号。"""
    def b(x):
        return x.fillna(False).astype(bool)

    c = d.close
    d["ma10_prev5"] = d.ma10.shift(5)
    d["s_trend_fast"] = b((c > d.ma10) & (d.ma10 > d.ma10_prev5) &
                          (d.breadth5 > 0.60))
    d["s_bull_early"] = b(d.breadth5 > 0.58) | d.s_any_confirm
    d["s_bull_confirm"] = b((c > d.hh60) & (d.ma20 > d.ma60) &
                            (d.breadth5 > 0.55) & (d.vol_ratio > 1.05))
    d["s_momo"] = b((d.mom20 > 0.05) & (c > d.ma20))
    d["s_momo_strong"] = b((d.mom20 > 0.08) & (c > d.ma20) &
                           (d.ma20 > d.ma60))
    d["s_bull_follow"] = b((c > d.ma20) & (d.ma20 > d.ma60) &
                           (d.ma20 > d.ma20_prev5) & (d.breadth5 > 0.55))
    return d


AGG_SIGNALS = ["s_bull_early", "s_breadth", "s_any_confirm", "s_trend",
               "s_trend_fast", "s_break", "s_break60", "s_bull_confirm",
               "s_bull_follow", "s_momo", "s_momo_strong", "s_state6",
               "s_all"]


# ------------------------------------------------- 锁存 + 退出状态机
def aggressive_lock(d, entry_sig, exit_mode="ma20", hold=10,
                    cooldown=0, trail_pct=0.04):
    """逐日锁存状态。只用当期及之前的收盘价 / 均线 / 信号，无未来数据。

    exit_mode:
      ma20 / ma10  收盘跌破对应均线立即解除
      trail        持仓期最高收盘回撤超过 trail_pct 立即解除
      none         仅靠锁存到期解除
    cooldown: 触发退出后 N 个交易日内不允许重新入场（防止反复打脸）
    """
    s = np.asarray(d[entry_sig], dtype=bool)
    close = d.close.to_numpy(dtype=float)
    ma10 = d.ma10.to_numpy(dtype=float)
    ma20 = d.ma20.to_numpy(dtype=float)
    out, left, cool, peak = [], 0, 0, None
    for i in range(len(d)):
        c = close[i]
        exit_now = False
        if left > 0:
            if exit_mode == "ma20" and np.isfinite(ma20[i]):
                exit_now = c < ma20[i]
            elif exit_mode == "ma10" and np.isfinite(ma10[i]):
                exit_now = c < ma10[i]
            elif exit_mode == "trail":
                peak = c if peak is None else max(peak, c)
                if peak > 0 and c / peak - 1.0 <= -trail_pct:
                    exit_now = True
        if cool > 0:
            cool -= 1
        if exit_now:
            left, peak = 0, None
            cool = int(cooldown)
        if s[i] and cool == 0:
            left = int(hold)
            if peak is None:
                peak = c
        on = left > 0
        out.append(on)
        if on:
            left -= 1
        else:
            peak = None
    return np.asarray(out, dtype=bool)


def apply_limit_lev(w_des, step_arr, max_w=2.0):
    """支持杠杆的升仓限速：升仓 <= step pp/日，降仓立即执行。

    _bt_bull_entry.apply_limit_var 会把权重 clip 到 [0,1]，
    本实验需要 >1 的杠杆权重，故独立实现（口径完全一致）。
    """
    out, prev = [], None
    for w, st in zip(w_des, step_arr):
        w = float(np.clip(w, 0.0, max_w))
        if prev is None or w <= prev:
            v = w
        else:
            v = min(w, prev + float(st) / 100.0)
        out.append(v)
        prev = v
    return np.asarray(out)


def aggressive_weight(d, lock, target_cap=1.0, up_fast=50.0,
                      up_slow=10.0, base_col="default_no_limit"):
    """锁存期把期望仓位打到 target_cap，其余保持生产闸门口径。

    ``target_cap`` > 1 为杠杆（诊断列）。升仓限速在锁存期放宽到 up_fast，
    其余时段沿用 up_slow；降仓永远立即执行。
    """
    gate = (d[base_col].to_numpy(dtype=float) / 100.0)
    w_des = np.where(lock, np.maximum(gate, float(target_cap)), gate)
    steps = np.where(lock, float(up_fast), float(up_slow))
    return apply_limit_lev(w_des, steps), w_des


# -------------------------------------------------------------- 评估
def evaluate_agg(w, d, bench, fee_bp=FEE_BP, cash_annual=0.0,
                 margin_annual=MARGIN_ANNUAL, date_range=None):
    """支持杠杆的评估：wt<=1 时现金部分生息，wt>1 时超出部分按融资利率付息。"""
    dates = bench.trade_date.tolist()
    close = bench.close.values
    pos = {x: i for i, x in enumerate(dates)}
    wmap = dict(zip(d.trade_date, w))
    cash_d = cash_annual / TRADING_DAYS
    margin_d = margin_annual / TRADING_DAYS
    rec, prev = [], None
    for dt in d.trade_date:
        i = pos.get(dt)
        if i is None or i + 1 >= len(dates):
            continue
        if date_range and not (date_range[0] <= dt <= date_range[1]):
            continue
        wt = float(wmap[dt])
        r_idx = close[i + 1] / close[i] - 1.0
        if wt <= 1.0:
            r = wt * r_idx + (1 - wt) * cash_d
        else:
            r = wt * r_idx - (wt - 1.0) * margin_d
        fee = (abs(wt - prev) * fee_bp / 10000) if prev is not None else 0.0
        rec.append((dates[i + 1], r - fee, wt, r_idx))
        prev = wt
    return pd.DataFrame(rec, columns=["date", "ret", "w", "idx_ret"])


def metrics_agg(df):
    return be.metrics(df)


def evaluate_const(w_const, d, bench, **kw):
    return evaluate_agg(np.full(len(d), float(w_const)), d, bench, **kw)


def full_metrics(w, d, bench, margin_annual=MARGIN_ANNUAL, fee_bp=FEE_BP):
    df = evaluate_agg(w, d, bench, fee_bp=fee_bp,
                      margin_annual=margin_annual)
    m = metrics_agg(df)
    const = metrics_agg(evaluate_const(
        float(np.mean(w)), d, bench, fee_bp=fee_bp,
        margin_annual=margin_annual))
    m["同均值恒定仓位收益%"] = const["总收益%"]
    m["波段alpha(pp)"] = round(m["总收益%"] - const["总收益%"], 2)
    for tag, (lo, hi) in (("2025", ("20250101", "20251231")),
                          ("2026", ("20260101", "20261104"))):
        sub = evaluate_agg(w, d, bench, fee_bp=fee_bp,
                           margin_annual=margin_annual,
                           date_range=(lo, hi))
        sm = metrics_agg(sub)
        m[f"_{tag}收益%"] = sm["总收益%"]
        m[f"_{tag}回撤%"] = sm["最大回撤%"]
    m["_主升段收益%"] = metrics_agg(evaluate_agg(
        w, d, bench, fee_bp=fee_bp, margin_annual=margin_annual,
        date_range=BULL_SEG))["总收益%"]
    m["_假反弹段收益%"] = metrics_agg(evaluate_agg(
        w, d, bench, fee_bp=fee_bp, margin_annual=margin_annual,
        date_range=FAKE_SEG))["总收益%"]
    return m


# ------------------------------------------------------- 主升段天花板
def ceiling(d, bench, fee_bp=FEE_BP):
    """用未来标注构造「完美知道主升段」的理想权重，仅作上界诊断。

    未来数据只用于标注起点，绝不进入任何可交易策略。
    """
    starts = d[d.is_local_low & (d.fwd40_best > 0.10)]
    picked, last = [], -999
    for i in starts.index:
        if i - last >= 20:
            picked.append(i)
            last = i
    w = np.zeros(len(d))
    for i in picked:
        w[i:min(i + 40, len(d))] = 1.0
    rows = {
        "主升波段数": len(picked),
        "起点": [d.trade_date.iloc[i] for i in picked],
        "理想满仓(仅评估)": full_metrics(w, d, bench, fee_bp=fee_bp),
        "理想1.5倍杠杆(仅评估)": full_metrics(w * 1.5, d, bench,
                                       fee_bp=fee_bp),
        "理想满仓覆盖天数": int((w > 0).sum()),
    }
    return rows


# --------------------------------------------------- 长历史样本外检验
def long_history():
    """2021-01-04 ~ 2026-11-04（1413 日）纯信号锁存策略检验。

    ★ 口径差异说明：两年主实验非锁存期持有生产闸门仓位，这里没有 2021-2024
      的 V10.7 回放序列，故非锁存期一律 0 仓位，用于检验**信号本身**在
      2021-2024 样本外是否成立，不与两年主实验的绝对数值直接对比。
    """
    con = sqlite3.connect("file:" + DB.replace("\\", "/") + "?mode=ro",
                          uri=True)
    idx = pd.read_sql(
        "SELECT trade_date, close FROM index_daily_cache "
        "WHERE ts_code=? AND trade_date>=? ORDER BY trade_date",
        con, params=(BENCH, "20200101"))
    con.close()
    idx["trade_date"] = idx.trade_date.astype(str)
    mc = sqlite3.connect("file:" + os.path.join(
        BASE_DIR, "cache_micro", "micro_daily.db").replace("\\", "/")
        + "?mode=ro", uri=True)
    mic = pd.read_sql(
        "SELECT trade_date, up_count, total FROM micro_daily "
        "WHERE trade_date>=? ORDER BY trade_date", mc, params=("20200101",))
    mc.close()
    mic["trade_date"] = mic.trade_date.astype(str)
    mic["up_ratio"] = mic.up_count / mic.total.replace(0, np.nan)
    d = idx.merge(mic[["trade_date", "up_ratio"]], on="trade_date", how="left")
    for n in (5, 10, 20, 30, 60, 120):
        d[f"ma{n}"] = d.close.rolling(n, min_periods=n).mean()
    d["breadth5"] = d.up_ratio.rolling(5, min_periods=5).mean()
    d = d[d.trade_date >= "20210104"].reset_index(drop=True)
    bench = idx.reset_index(drop=True)
    years = (2021, 2022, 2023, 2024, 2025, 2026)

    def sig_of(ma, th):
        col, prev = d[f"ma{ma}"], d[f"ma{ma}"].shift(5)
        return ((d.close > col) & (col > prev) &
                (d.breadth5 > th)).fillna(False).astype(bool)

    def weight_of(ma, th, ex, hold=10, cap=1.0, up=50.0):
        d["_s"] = sig_of(ma, th)
        lk = aggressive_lock(d, "_s", ex, hold)
        return apply_limit_lev(np.where(lk, float(cap), 0.0),
                               np.full(len(d), up)), lk

    def stat(w):
        full = metrics_agg(evaluate_agg(w, d, bench))
        ys = {}
        for y in years:
            ys[str(y)] = metrics_agg(evaluate_agg(
                w, d, bench, date_range=(f"{y}0101", f"{y}1231")))["总收益%"]
        return {"全期收益%": full["总收益%"],
                "全期最大回撤%": full["最大回撤%"],
                "全期平均仓位%": full["平均仓位%"],
                "分年收益%": ys,
                "2021-2024样本外合计%": round(sum(ys[str(y)] for y in
                                             years[:4]), 2),
                "样本外最差年份%": min(ys[str(y)] for y in years[:4]),
                "收益回撤比": full["收益回撤比"]}

    out = {"区间": f"{d.trade_date.iloc[0]}~{d.trade_date.iloc[-1]}"
                  f"（{len(d)} 个决策日）",
           "口径": "纯信号锁存、非锁存期0仓位、满仓/杠杆按融资利率计息、"
                  "5bp换手；仅检验信号本身在2021-2024样本外的有效性"}
    wA, lkA = weight_of(20, 0.58, "none")
    wB, lkB = weight_of(10, 0.60, "ma10")
    out["A_主升段激进档"] = dict(stat(wA), 参数={
        "信号": "MA20上行+5日广度>0.58", "退出": "none(锁存10日自然到期)",
        "目标仓位": 1.0, "升仓pp/日": 50}, 锁存天数=int(lkA.sum()))
    out["B_长历史稳健档"] = dict(stat(wB), 参数={
        "信号": "MA10上行+5日广度>0.60", "退出": "收盘跌破MA10",
        "目标仓位": 1.0, "升仓pp/日": 50}, 锁存天数=int(lkB.sum()))
    f60 = (d.close > d.ma60).fillna(False).to_numpy(dtype=bool)
    out["A加MA60长期过滤"] = stat(wA * f60)
    out["A上B下双档(MA60切换)"] = stat(np.where(f60, wA, wB))
    ones = np.ones(len(d))
    out["满仓买入持有"] = stat(ones)
    out["杠杆敏感性_A档"] = {}
    for cp in (0.8, 1.0, 1.2, 1.5, 2.0):
        ww, _ = weight_of(20, 0.58, "none", cap=cp)
        out["杠杆敏感性_A档"][f"{int(cp*100)}%"] = stat(ww)["全期收益%"]
    # 48 组网格：MA窗口 × 广度阈值 × 退出方式
    grid = []
    for ma in (10, 20, 30):
        for th in (0.54, 0.56, 0.58, 0.60):
            for ex in ("none", "ma10", "ma20", "trail"):
                ww, _ = weight_of(ma, th, ex)
                s = stat(ww)
                grid.append({"MA": ma, "广度阈值": th, "退出": ex,
                             "全期收益%": s["全期收益%"],
                             "全期回撤%": s["全期最大回撤%"],
                             "样本外合计%": s["2021-2024样本外合计%"],
                             "样本外最差%": s["样本外最差年份%"],
                             "2025%": s["分年收益%"]["2025"],
                             "2026%": s["分年收益%"]["2026"]})
    out["参数网格48组"] = sorted(grid, key=lambda x: -x["样本外合计%"])
    by_ex = {}
    for ex in ("none", "ma10", "ma20", "trail"):
        sub = [g for g in grid if g["退出"] == ex]
        by_ex[ex] = {
            "全期收益均值%": round(float(np.mean(
                [g["全期收益%"] for g in sub])), 2),
            "样本外合计均值%": round(float(np.mean(
                [g["样本外合计%"] for g in sub])), 2),
            "样本外最差均值%": round(float(np.mean(
                [g["样本外最差%"] for g in sub])), 2),
            "2021-2024为正的组数": sum(1 for g in sub
                                  if g["样本外合计%"] > 0),
        }
    out["退出方式汇总"] = by_ex
    return out


# -------------------------------------------------------------- 主流程
def main():
    bench = be.load_bench()
    micro = be.load_micro()
    plan = be.load_plan()
    d = add_signals(be.build_features(bench, micro, plan))
    res = {
        "区间": f"{d.trade_date.iloc[0]}~{END}（{len(d)} 个决策日）",
        "口径": "沪深300、T日盘后决策/T日收盘成交/T→T+1收益、5bp单边换手、"
               "现金0%(另给1.5%敏感性)、杠杆按年化6.5%融资计息、"
               "信号只用<=T数据；突破hard_cap为诊断列需P0重算授权",
        "生产基准": {
            "生产口径(含闸门+限速)": full_metrics(
                d.effective.to_numpy(dtype=float) / 100.0, d, bench),
            "上一轮最优(不破hard_cap)": None,
            "满仓买入持有": full_metrics(np.ones(len(d)), d, bench),
        },
    }

    # 上一轮最优（广度锁存+MA20退出+补足hard_cap+30pp条件提速）
    best_col = "_latch_breadth_h10_exitMA"
    if best_col not in d.columns:
        ex = d.close < d.ma20
        d[best_col] = be.latch(d.s_breadth.to_numpy(dtype=bool), 10,
                               ex.to_numpy(dtype=bool))
    w_prev = be.desired_weight(d, "combo", best_col, cap_ratio=0.9)
    steps_prev = np.where(d[best_col].to_numpy(dtype=bool), 30.0, 10.0)
    res["生产基准"]["上一轮最优(不破hard_cap)"] = full_metrics(
        be.apply_limit_var(w_prev, steps_prev), d, bench)
    prod_w = d.effective.to_numpy(dtype=float) / 100.0

    # ---- 1) 信号 × 退出方式 网格（固定 100% 仓位 / 10日锁存 / 50pp提速）----
    grid1, navs = {}, {}
    for sig in AGG_SIGNALS:
        for ex in ("ma20", "ma10", "trail", "none"):
            lock = aggressive_lock(d, sig, exit_mode=ex, hold=10,
                                   cooldown=0, trail_pct=0.04)
            tag = f"{sig}|{ex}"
            w, _ = aggressive_weight(d, lock, target_cap=1.0,
                                     up_fast=50.0, up_slow=10.0)
            grid1[tag] = full_metrics(w, d, bench)
            grid1[tag]["_锁存天数"] = int(lock.sum())
            if ex == "ma20":
                navs[f"sig_{sig}"] = (1 + evaluate_agg(
                    w, d, bench).ret).cumprod().set_axis(
                        evaluate_agg(w, d, bench).date).to_frame(
                            f"sig_{sig}")
    res["网格1_信号x退出"] = grid1

    # ---- 2) 挑出两年收益最高的组合，做仓位/锁存/限速网格 ----
    ranked = sorted(grid1.items(), key=lambda x: -x[1]["总收益%"])
    top = [k for k, _ in ranked[:4]]
    grid2 = {}
    for tag in top:
        sig, ex = tag.split("|")
        for cap in (0.8, 0.9, 1.0, 1.2, 1.5):
            for hold in (5, 10, 20, 30):
                lock = aggressive_lock(d, sig, exit_mode=ex, hold=hold,
                                       cooldown=0, trail_pct=0.04)
                w, _ = aggressive_weight(d, lock, target_cap=cap,
                                         up_fast=50.0, up_slow=10.0)
                k2 = f"{tag}|cap{cap}|h{hold}"
                m = full_metrics(w, d, bench)
                grid2[k2] = {x: m[x] for x in
                             ("总收益%", "最大回撤%", "收益回撤比",
                              "平均仓位%", "波段alpha(pp)", "_2025收益%",
                              "_2026收益%", "_2026回撤%", "_主升段收益%",
                              "_假反弹段收益%")}
    res["网格2_仓位x锁存"] = grid2

    # ---- 3) 候选方案（含冷却、升仓速度、杠杆） ----
    cands, SPECS = {}, {}

    def add(name, sig, ex, hold, cap, up, cool=0, trail=0.04):
        lock = aggressive_lock(d, sig, exit_mode=ex, hold=hold,
                               cooldown=cool, trail_pct=trail)
        w, w_des = aggressive_weight(d, lock, target_cap=cap,
                                     up_fast=up, up_slow=10.0)
        m = full_metrics(w, d, bench)
        m["_锁存天数"] = int(lock.sum())
        m["_限速触发天数"] = int((np.diff(w, prepend=w[0]) > 1e-9).sum())
        cands[name] = m
        SPECS[name] = {"信号": sig, "退出": ex, "锁存日": hold,
                       "目标仓位": cap, "锁存期升仓pp/日": up,
                       "冷却日": cool, "跟踪止损": trail}
        navs[name] = (1 + evaluate_agg(w, d, bench).ret).cumprod() \
            .set_axis(evaluate_agg(w, d, bench).date).to_frame(name)
        return w, lock

    w_prod_lim, _ = aggressive_weight(
        d, aggressive_lock(d, "s_bull_early", "ma20", hold=10),
        target_cap=1.0, up_fast=10.0)
    cands["A_满仓但沿用10pp限速"] = full_metrics(w_prod_lim, d, bench)

    wA, lkA = add("B_早期信号_满仓_MA20退出", "s_bull_early", "ma20",
                  10, 1.0, 50.0)
    wB, lkB = add("C_突破确认_满仓_MA20退出", "s_bull_confirm", "ma20",
                  20, 1.0, 50.0)
    wC, lkC = add("D_趋势跟随_满仓_MA20退出", "s_bull_follow", "ma20",
                  20, 1.0, 50.0)
    wD, lkD = add("E_趋势跟随_满仓_跟踪止损4%", "s_bull_follow", "trail",
                  20, 1.0, 50.0, trail=0.04)
    wE, lkE = add("F_趋势跟随_1.2倍杠杆", "s_bull_follow", "ma20",
                  20, 1.2, 50.0)
    wF, lkF = add("G_趋势跟随_1.5倍杠杆", "s_bull_follow", "ma20",
                  20, 1.5, 50.0)
    wG, lkG = add("H_早期信号_1.2倍杠杆_MA10退出", "s_bull_early", "ma10",
                  10, 1.2, 50.0, cool=5)
    wH, lkH = add("I_动量强_1.2倍杠杆_跟踪止损", "s_momo_strong", "trail",
                  15, 1.2, 50.0, cool=5, trail=0.04)
    wI, lkI = add("J_广度锁存_1.2倍杠杆_MA20退出", "s_breadth", "ma20",
                  10, 1.2, 50.0, cool=5)
    wJ, lkJ = add("K_全条件_1.5倍杠杆", "s_all", "ma20",
                  20, 1.5, 50.0, cool=5)
    wK, lkK = add("L_趋势跟随_满仓_无限速", "s_bull_follow", "ma20",
                  20, 1.0, 200.0)
    # ★ 网格1 暴露的最优形态：趋势锁存 + 不做主动退出（锁存到期自然退）
    wL, lkL = add("M_趋势锁存_满仓_无主动退出", "s_trend", "none",
                  10, 1.0, 50.0)
    wM, lkM = add("N_趋势锁存_1.2倍杠杆_无主动退出", "s_trend", "none",
                  10, 1.2, 50.0)
    wN, lkN = add("O_趋势锁存_1.5倍杠杆_无主动退出", "s_trend", "none",
                  10, 1.5, 50.0)
    wO, lkO = add("P_快趋势严阈值_满仓_MA10退出", "s_trend_fast", "ma10",
                  10, 1.0, 50.0)
    wP, lkP = add("Q_快趋势严阈值_1.2倍杠杆", "s_trend_fast", "ma10",
                  10, 1.2, 50.0)
    res["候选方案"] = cands
    res["候选参数"] = SPECS

    # ---- 4) 最优档稳健性：成本 / 融资利率 / 多指数 / 现金 ----
    best_name = max((k for k in cands if not k.startswith("A_")),
                    key=lambda k: cands[k]["总收益%"])
    sp = SPECS[best_name]
    lock_b = aggressive_lock(d, sp["信号"], sp["退出"], sp["锁存日"],
                             sp["冷却日"], trail_pct=sp["跟踪止损"])
    w_b, _ = aggressive_weight(d, lock_b, sp["目标仓位"],
                               sp["锁存期升仓pp/日"], 10.0)
    rob = {"方案": best_name, "参数": dict(sp)}
    rob["成本敏感性"] = {}
    for fee in (0.0, 5.0, 10.0, 20.0):
        rob["成本敏感性"][f"{fee}bp"] = {
            "本方案": metrics_agg(evaluate_agg(
                w_b, d, bench, fee_bp=fee))["总收益%"],
            "生产口径": metrics_agg(evaluate_agg(
                prod_w, d, bench, fee_bp=fee))["总收益%"],
            "满仓": metrics_agg(evaluate_agg(
                np.ones(len(d)), d, bench, fee_bp=fee))["总收益%"],
        }
    rob["融资利率敏感性"] = {}
    for ma in (0.0, 0.04, 0.065, 0.09):
        rob["融资利率敏感性"][f"{ma*100:.1f}%"] = metrics_agg(
            evaluate_agg(w_b, d, bench, margin_annual=ma))["总收益%"]
    rob["现金1.5%"] = metrics_agg(evaluate_agg(
        w_b, d, bench, cash_annual=0.015))["总收益%"]
    con = sqlite3.connect("file:" + DB.replace("\\", "/") + "?mode=ro", uri=True)
    multi = {}
    for code in ("000300.SH", "000001.SH", "000905.SH", "000852.SH",
                 "000985.CSI"):
        bx = pd.read_sql(
            "SELECT trade_date, close FROM index_daily_cache "
            "WHERE ts_code=? AND trade_date>=? AND trade_date<=? "
            "ORDER BY trade_date", con, params=(code, "20240101", END))
        bx["trade_date"] = bx.trade_date.astype(str)
        bx = bx.dropna().drop_duplicates("trade_date").reset_index(drop=True)
        if len(bx) < 100:
            continue
        multi[code] = {
            "本方案": metrics_agg(evaluate_agg(w_b, d, bx))["总收益%"],
            "生产口径": metrics_agg(evaluate_agg(prod_w, d, bx))["总收益%"],
            "满仓": metrics_agg(evaluate_agg(
                np.ones(len(d)), d, bx))["总收益%"],
        }
    con.close()
    rob["多指数"] = multi
    rob["主升段与假反弹"] = {
        "2025主升段": {
            "本方案": metrics_agg(evaluate_agg(w_b, d, bench,
                                  date_range=BULL_SEG))["总收益%"],
            "生产口径": metrics_agg(evaluate_agg(prod_w, d, bench,
                                    date_range=BULL_SEG))["总收益%"],
            "满仓": metrics_agg(evaluate_agg(np.ones(len(d)), d, bench,
                               date_range=BULL_SEG))["总收益%"]},
        "2026假反弹段": {
            "本方案": metrics_agg(evaluate_agg(w_b, d, bench,
                                  date_range=FAKE_SEG))["总收益%"],
            "生产口径": metrics_agg(evaluate_agg(prod_w, d, bench,
                                    date_range=FAKE_SEG))["总收益%"],
            "满仓": metrics_agg(evaluate_agg(np.ones(len(d)), d, bench,
                               date_range=FAKE_SEG))["总收益%"]},
    }
    # 参数敏感性：主升段信号阈值 / 锁存长度 / 目标仓位 / 限速
    sens = {"锁存长度": {}, "目标仓位": {}, "升仓限速": {},
            "广度阈值xMA窗口": {}, "去掉最好N日": {}}
    for h in (5, 10, 15, 20, 30, 40):
        lk = aggressive_lock(d, sp["信号"], sp["退出"], h, sp["冷却日"],
                             trail_pct=sp["跟踪止损"])
        ww, _ = aggressive_weight(d, lk, sp["目标仓位"],
                                  sp["锁存期升仓pp/日"], 10.0)
        sens["锁存长度"][f"{h}日"] = metrics_agg(
            evaluate_agg(ww, d, bench))["总收益%"]
    for cp in (0.6, 0.8, 1.0, 1.2, 1.5, 2.0):
        ww, _ = aggressive_weight(d, lock_b, cp,
                                  sp["锁存期升仓pp/日"], 10.0)
        sens["目标仓位"][f"{int(cp*100)}%"] = metrics_agg(
            evaluate_agg(ww, d, bench))["总收益%"]
    for st in (10, 20, 30, 50, 100, 200):
        ww, _ = aggressive_weight(d, lock_b, sp["目标仓位"],
                                  float(st), 10.0)
        sens["升仓限速"][f"{st}pp"] = metrics_agg(
            evaluate_agg(ww, d, bench))["总收益%"]
    # 广度阈值 × MA 窗口：判断是否参数孤点
    d["ma30"] = d.close.rolling(30, min_periods=30).mean()
    for ma in (10, 20, 30, 60):
        col = d[f"ma{ma}"]
        for th in (0.54, 0.56, 0.58, 0.60, 0.62):
            d["_grid"] = ((d.close > col) & (col > col.shift(5)) &
                          (d.breadth5 > th)).fillna(False).astype(bool)
            lk = aggressive_lock(d, "_grid", sp["退出"], sp["锁存日"],
                                 sp["冷却日"], trail_pct=sp["跟踪止损"])
            ww, _ = aggressive_weight(d, lk, sp["目标仓位"],
                                      sp["锁存期升仓pp/日"], 10.0)
            sens["广度阈值xMA窗口"][f"MA{ma}_th{th}"] = metrics_agg(
                evaluate_agg(ww, d, bench))["总收益%"]
    # 去掉收益最好的 N 日：检验是否靠少数几根大阳线
    dfb = evaluate_agg(w_b, d, bench)
    for n in (1, 3, 5, 10):
        drop = set(dfb.ret.nlargest(n).index)
        dfd = dfb[~dfb.index.isin(drop)]
        sens["去掉最好N日"][f"去掉{n}日"] = round(
            ((1 + dfd.ret).prod() - 1) * 100, 2)
    rob["参数敏感性"] = sens
    # 逐月收益归因（决策日口径）
    monthly = {}
    for ym, grp in dfb.assign(ym=dfb.date.str[:6]).groupby("ym"):
        monthly[ym] = round(((1 + grp.ret).prod() - 1) * 100, 2)
    rob["逐月收益%"] = monthly
    res["最优档稳健性"] = rob

    # ---- 4b) 长历史样本外检验（决定性） ----
    res["长历史样本外检验"] = long_history()

    # ---- 5) 主升段天花板（未来标注，仅评估） ----
    res["主升段天花板"] = ceiling(d, bench)

    # ---- 6) 建议落地档位（三档，均附长历史代价） ----
    lh = res["长历史样本外检验"]
    lk_st = aggressive_lock(d, "s_trend_fast", "ma10", 10)
    w_st, _ = aggressive_weight(d, lk_st, 1.0, 50.0, 10.0)
    st_m = full_metrics(w_st, d, bench)
    lk_ma60 = (d.close > d.ma60).fillna(False).to_numpy(dtype=bool)
    lk_a = aggressive_lock(d, "s_trend", "none", 10)
    w_a60, _ = aggressive_weight(d, lk_a, 1.0, 50.0, 10.0)
    w_a60 = np.where(lk_ma60, w_a60,
                     d.default_no_limit.to_numpy(dtype=float) / 100.0)
    a60_m = full_metrics(w_a60, d, bench)
    res["建议档位"] = {
        "_note": "供确认后落进 swing_gate/position_optimizer 的参数；本脚本不写生产配置。"
                 "突破 hard_cap 必须由 P0 风险预算重算 risk_cap 并由 authority 显式授权。"
                 "两年=2025-2026 主实验口径；长历史=2021-2026 纯信号口径(非锁存期0仓位)，"
                 "两者绝对数值不可直接比较，长历史只用于判断信号能否外推。",
        "steady_长历史稳健": {
            "信号": "MA10上行 + 5日广度>0.60", "退出": "收盘跌破MA10",
            "锁存日": 10, "目标仓位": 1.0, "锁存期升仓pp/日": 50,
            "两年收益%": st_m["总收益%"], "两年回撤%": st_m["最大回撤%"],
            "两年2025%": st_m["_2025收益%"], "两年2026%": st_m["_2026收益%"],
            "长历史全期%": lh["B_长历史稳健档"]["全期收益%"],
            "长历史样本外合计%": lh["B_长历史稳健档"]["2021-2024样本外合计%"],
            "长历史最差年份%": lh["B_长历史稳健档"]["样本外最差年份%"],
            "适用": "优先控制回撤、可接受牛市踏空"},
        "recommended_主升段激进": {
            **rob["参数"],
            "两年收益%": cands[best_name]["总收益%"],
            "两年回撤%": cands[best_name]["最大回撤%"],
            "两年2025%": cands[best_name]["_2025收益%"],
            "两年2026%": cands[best_name]["_2026收益%"],
            "主升段收益%": cands[best_name]["_主升段收益%"],
            "长历史同形态全期%": lh["A_主升段激进档"]["全期收益%"],
            "长历史同形态2021%": lh["A_主升段激进档"]["分年收益%"]["2021"],
            "加MA60长期过滤两年收益%": a60_m["总收益%"],
            "加MA60长期过滤两年回撤%": a60_m["最大回撤%"],
            "适用": "用户诉求：博主升段高收益，需接受震荡/慢熊年份的双位数亏损"},
        "aggressive_杠杆": {
            "信号": "s_trend", "退出": "none", "锁存日": 10,
            "目标仓位": 1.5, "锁存期升仓pp/日": 50,
            "两年收益%": cands["O_趋势锁存_1.5倍杠杆_无主动退出"]["总收益%"],
            "两年回撤%": cands["O_趋势锁存_1.5倍杠杆_无主动退出"]["最大回撤%"],
            "长历史1.5倍全期%": lh["杠杆敏感性_A档"]["150%"],
            "结论": "★ 不推荐：长历史全期为负，融资成本+震荡市反复打脸吞噬杠杆收益"},
        "conservative_不破上限": {
            "信号": "s_breadth", "退出": "ma20", "锁存日": 10,
            "目标仓位": 0.9, "锁存期升仓pp/日": 30, "冷却日": 5,
            "_note": "上一轮已验证的 10.7.3 增量口径，需要 P0 授权豁免时作为回退档"},
    }

    # ---- 7) 逐日明细 / 净值 ----
    daily = d[["trade_date", "close", "state", "hard_cap",
               "default_no_limit", "effective", "ma10", "ma20",
               "breadth5", "mom20", "s_bull_early", "s_bull_confirm",
               "s_bull_follow", "s_breadth", "s_momo_strong"]].copy()
    daily["w_prod"] = prod_w * 100
    daily["w_best"] = w_b * 100
    daily["lock_best"] = lock_b.astype(int)
    daily.to_csv(os.path.join(BASE_DIR, "_bt_bull_aggressive_daily.csv"),
                 index=False, encoding="utf-8-sig", float_format="%.4f")
    base_nav = evaluate_agg(prod_w, d, bench)
    prev_nav = evaluate_agg(be.apply_limit_var(w_prev, steps_prev), d, bench)
    full_nav = evaluate_agg(np.ones(len(d)), d, bench)
    st_nav = evaluate_agg(w_st, d, bench)
    navdf = pd.concat([
        (1 + base_nav.ret).cumprod().set_axis(base_nav.date).to_frame("生产口径"),
        (1 + prev_nav.ret).cumprod().set_axis(prev_nav.date)
            .to_frame("上一轮最优_不破上限"),
        (1 + st_nav.ret).cumprod().set_axis(st_nav.date)
            .to_frame("长历史稳健档"),
        (1 + evaluate_agg(w_b, d, bench).ret).cumprod()
            .set_axis(evaluate_agg(w_b, d, bench).date).to_frame("激进最优档"),
        (1 + full_nav.ret).cumprod().set_axis(full_nav.date).to_frame("满仓买入持有"),
    ], axis=1)
    navdf.to_csv(os.path.join(BASE_DIR, "_bt_bull_aggressive_nav.csv"),
                 encoding="utf-8-sig", float_format="%.6f")
    with open(os.path.join(BASE_DIR, "_bt_bull_aggressive_metrics.json"),
              "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    with open(os.path.join(BASE_DIR, "_bt_bull_aggressive_profile.json"),
              "w", encoding="utf-8") as f:
        json.dump(res["建议档位"], f, ensure_ascii=False, indent=2)

    # ---- 控制台摘要 ----
    print("rows", len(d), "grid1", len(grid1), "grid2", len(grid2))
    print("=== 生产基准（5bp）===")
    for k, v in res["生产基准"].items():
        print(f"  {k:26s} 收益{v['总收益%']:7.2f} 回撤{v['最大回撤%']:7.2f} "
              f"均仓{v['平均仓位%']:6.1f} 2025:{v['_2025收益%']:6.2f} "
              f"2026:{v['_2026收益%']:6.2f}")
    print("=== 网格1 top10（100%仓位/10日锁存/50pp提速）===")
    for k, v in ranked[:10]:
        print(f"  {k:32s} 收益{v['总收益%']:7.2f} 回撤{v['最大回撤%']:7.2f} "
              f"比{v['收益回撤比']:6.2f} 均仓{v['平均仓位%']:6.1f} "
              f"alpha{v['波段alpha(pp)']:6.2f} 锁存{v['_锁存天数']:3d} "
              f"2026:{v['_2026收益%']:6.2f}")
    print("=== 候选方案 ===")
    for k, v in cands.items():
        print(f"  {k:30s} 收益{v['总收益%']:7.2f} 回撤{v['最大回撤%']:7.2f} "
              f"均仓{v['平均仓位%']:6.1f} alpha{v['波段alpha(pp)']:6.2f} "
              f"主升{v['_主升段收益%']:6.2f} 假反弹{v['_假反弹段收益%']:6.2f}")
    print("=== 最优档 ===", best_name, rob["参数"])
    print("成本", json.dumps(rob["成本敏感性"], ensure_ascii=False, default=float))
    print("多指数", json.dumps(rob["多指数"], ensure_ascii=False))
    print("去掉最好N日", rob["参数敏感性"]["去掉最好N日"])
    print("天花板", json.dumps(res["主升段天花板"], ensure_ascii=False)[:400])
    print("=== 长历史样本外 ===")
    for k in ("A_主升段激进档", "B_长历史稳健档", "A加MA60长期过滤",
              "A上B下双档(MA60切换)", "满仓买入持有"):
        v = lh[k]
        print(f"  {k:26s} 全期{v['全期收益%']:7.2f} 回撤{v['全期最大回撤%']:7.2f} "
              f"样本外{v['2021-2024样本外合计%']:7.2f} "
              f"2021:{v['分年收益%']['2021']:6.2f} 2025:{v['分年收益%']['2025']:6.2f}")
    print("  杠杆敏感性", lh["杠杆敏感性_A档"])
    print("  退出方式汇总", json.dumps(lh["退出方式汇总"], ensure_ascii=False))
    print("=== 建议档位 ===")
    print(json.dumps(res["建议档位"], ensure_ascii=False, indent=1)[:2200])


if __name__ == "__main__":
    main()
