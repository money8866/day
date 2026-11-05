# -*- coding: utf-8 -*-
"""牛市启动识别与及时升仓实验（V10.7.3 之后的研究脚本，不改动生产代码）。

回答的问题
----------
2025 牛市沪深300 +23.49%，按 V10.7.3 仓位只拿到约 +5.8%。
根因诊断显示：主升段（2025-07-25~09-15，指数 +9.83%）系统仓位均值仅
32.8%，且状态在 S1/S2/S3/S5/S6 间反复抖动，8/15 因「降仓冷却」目标被打到
5%、8/28~9/02 指数高位反而判 S3 恐慌、硬上限 5%，9/15 才升到 80%（主升已走完）。

所以本脚本把问题拆成两层分别实测：
  1. **判断层**：哪些「牛市启动」信号足够早（及时性），且不在 2026 熊市反弹里
     反复假阳性（假信号代价）。
  2. **执行层**：信号为真时，用什么动作才能在现有风控框架内真正升上去
     （解除闸门 / 向 hard_cap 补足 / 放宽 P1 升仓限速），收益与回撤代价各是多少。

口径（与前两轮回测严格一致）
---------------------------
* 沪深300、T 日盘后决策、T 日收盘按有效仓位成交、承担 T→T+1 收益、5bp 单边换手。
* 所有信号只用 trade_date <= T 的收盘价 / 微结构数据；未来数据仅用于**评估标注**
  （波段起点、前瞻收益），绝不进入仓位决策。
* 升仓绝不突破当日 hard_cap —— 除非显式开启 ``cap_boost`` 诊断列（需 P0 风险预算
  重新授权，不是默认建议）。
* 同均值恒定仓位对照用于分离「加 beta」与「真择时 alpha」。

输入
----
* cache_daily/stock_data.db :: index_daily_cache (000300.SH)
* cache_micro/micro_daily.db :: micro_daily
* _bt_swing_gate_replay.csv  (443 日生产口径有效仓位，含限速 / 无限速两列)
* _bt_opt_replay_on.csv      (443 日 P1/P2 目标、状态、确认结果)

输出
----
* _bt_bull_entry_metrics.json  全部指标
* _bt_bull_entry_nav.csv       净值序列
* _bt_bull_entry_signals.csv   逐日信号与有效仓位
"""
import os
import sys
import json
import sqlite3

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE_DIR)
DB = os.path.join(ROOT, "cache_daily", "stock_data.db")
MICRO_DB = os.path.join(BASE_DIR, "cache_micro", "micro_daily.db")
BENCH = "000300.SH"
FEE_BP = 5.0
START, END = "20250102", "20261104"
TRADING_DAYS = 244


# ---------------------------------------------------------------- 数据
def load_bench():
    con = sqlite3.connect("file:" + DB.replace("\\", "/") + "?mode=ro", uri=True)
    try:
        df = pd.read_sql(
            "SELECT trade_date, close FROM index_daily_cache "
            "WHERE ts_code=? AND trade_date>=? AND trade_date<=? ORDER BY trade_date",
            con, params=(BENCH, "20240101", END))
    finally:
        con.close()
    df["trade_date"] = df.trade_date.astype(str)
    return df.dropna().drop_duplicates("trade_date").reset_index(drop=True)


def load_micro():
    con = sqlite3.connect("file:" + MICRO_DB.replace("\\", "/") + "?mode=ro",
                          uri=True)
    try:
        # 物理表只有原始计数；up_ratio / zt_dt_ratio 与
        # market_micro.get_row 的派生口径保持一致
        df = pd.read_sql(
            "SELECT trade_date, up_count, total, amount_yi, zt_count, dt_count "
            "FROM micro_daily WHERE trade_date>=? ORDER BY trade_date",
            con, params=("20240101",))
    finally:
        con.close()
    df["trade_date"] = df.trade_date.astype(str)
    df["up_ratio"] = df.up_count / df.total.replace(0, np.nan)
    df["zt_dt_ratio"] = df.zt_count / df.dt_count.replace(0, np.nan)
    return df.dropna(subset=["amount_yi"]).drop_duplicates("trade_date") \
             .reset_index(drop=True)


def load_plan():
    g = pd.read_csv(os.path.join(BASE_DIR, "_bt_swing_gate_replay.csv"),
                    encoding="utf-8-sig")
    p = pd.read_csv(os.path.join(BASE_DIR, "_bt_opt_replay_on.csv"),
                    encoding="utf-8-sig")
    for d in (g, p):
        d["trade_date"] = d.trade_date.astype(str)
    g = g[["trade_date", "state", "base_target", "hard_cap", "effective",
           "default_no_limit", "swing3_no_limit"]].copy()
    p = p[["trade_date", "eff_state", "easing", "rebound", "target",
           "smoother_action"]].copy()
    d = g.merge(p, on="trade_date", how="left")
    return d


# ------------------------------------------------------- 特征 / 启动信号
def build_features(bench, micro, plan):
    """构造只用 T 日及之前数据的信号；未来数据单独列，仅供评估标注。"""
    d = bench[["trade_date", "close"]].copy()
    m = micro[["trade_date", "up_ratio", "amount_yi", "zt_count",
               "zt_dt_ratio"]].copy()
    d = d.merge(m, on="trade_date", how="left")

    c = d["close"]
    for n in (5, 10, 20, 60):
        d[f"ma{n}"] = c.rolling(n, min_periods=n).mean()
    d["ma20_prev5"] = d["ma20"].shift(5)
    d["ma20_prev1"] = d["ma20"].shift(1)
    d["ma60_prev1"] = d["ma60"].shift(1)
    d["hh20"] = c.shift(1).rolling(20, min_periods=20).max()
    d["hh60"] = c.shift(1).rolling(60, min_periods=60).max()
    d["mom20"] = c / c.shift(20) - 1.0
    d["mom10"] = c / c.shift(10) - 1.0
    d["low60"] = c.rolling(60, min_periods=60).min()
    d["dd60"] = c / c.rolling(60, min_periods=60).max() - 1.0
    d["recover60"] = c / d["low60"] - 1.0
    d["breadth5"] = d["up_ratio"].rolling(5, min_periods=5).mean()
    d["amt20"] = d["amount_yi"].rolling(20, min_periods=20).mean()
    d["vol_ratio"] = d["amount_yi"] / d["amt20"]
    d["zt5"] = d["zt_count"].rolling(5, min_periods=5).mean()

    # ---- 单因子信号（缺失 → False，不默认通过） ----
    def b(x):
        return x.fillna(False).astype(bool)

    d["s_ma20up"] = b((c > d.ma20) & (d.ma20 > d.ma20_prev5))
    d["s_ma60above"] = b((c > d.ma20) & (d.ma20 > d.ma60))
    d["s_golden"] = b((d.ma20 > d.ma60) & (d.ma20_prev1 <= d.ma60_prev1))
    d["s_brk20"] = b(c > d.hh20)
    d["s_brk60"] = b(c > d.hh60)
    d["s_mom5"] = b(d.mom20 > 0.05)
    d["s_mom8"] = b(d.mom20 > 0.08)
    d["s_recover"] = b((d.recover60 > 0.08) & (c > d.ma60))
    d["s_breadth"] = b(d.breadth5 > 0.58)
    d["s_volume"] = b(d.vol_ratio > 1.15)
    d["s_zt"] = b((d.zt5 > 60) & (d.zt_dt_ratio > 10))

    # ---- 组合信号 ----
    d["s_trend"] = d.s_ma20up & d.s_breadth
    d["s_trend_mom"] = d.s_trend & d.s_mom5
    d["s_break"] = d.s_brk20 & d.s_breadth & d.s_volume
    d["s_break60"] = d.s_brk60 & d.s_breadth
    d["s_all"] = d.s_trend & d.s_mom5 & d.s_volume
    # ★ 注意：reindex 后 index 是日期字符串，必须用 .values 赋值，
    #   否则与 d 的整数索引对齐会全部变成 NaN（曾导致信号恒为 False）。
    pl = plan.set_index("trade_date")
    d["s_state6"] = pl["state"].reindex(d.trade_date).eq("S6").values
    d["s_rebound"] = pl["rebound"].reindex(d.trade_date).eq("PASS").values
    d["s_any_confirm"] = (
        pl["rebound"].reindex(d.trade_date).eq("PASS").values |
        pl["easing"].reindex(d.trade_date).eq("PASS").values)

    # ---- 仅评估用的未来标注（绝不进入仓位） ----
    d["fwd20"] = c.shift(-20) / c - 1.0
    d["fwd40"] = c.shift(-40) / c - 1.0
    arr = c.to_numpy(dtype=float)
    n = len(arr)
    fwd_best = np.full(n, np.nan)
    for i in range(n - 40):
        fwd_best[i] = arr[i + 1:i + 41].max() / arr[i] - 1.0
    d["fwd40_best"] = fwd_best
    # 局部低点放宽为「距过去 20 日最低收盘 2% 以内」，否则恰好等于最低点太罕见
    d["is_local_low"] = b(c <= c.rolling(20, min_periods=20).min() * 1.02)

    d = d[(d.trade_date >= START) &
          d.trade_date.isin(set(plan.trade_date))].reset_index(drop=True)
    pl = plan.set_index("trade_date")
    for col in ("base_target", "hard_cap", "effective", "default_no_limit",
                "swing3_no_limit", "state", "smoother_action"):
        d[col] = pl[col].reindex(d.trade_date).values
    return d


SIGNALS = ["s_ma20up", "s_ma60above", "s_golden", "s_brk20", "s_brk60",
           "s_mom5", "s_mom8", "s_recover", "s_breadth", "s_volume",
           "s_zt", "s_trend", "s_trend_mom", "s_break", "s_break60",
           "s_all", "s_state6", "s_rebound", "s_any_confirm"]


# ------------------------------------------------------------ 升仓动作
def desired_weight(d, mode, sig, cap_ratio=0.9, cap_boost=1.0,
                   bull_cap=None):
    """返回「期望有效仓位」(0~1)。只在该日 sig 为真时放宽。

    mode:
      baseline  生产口径（含闸门，无限速列 default_no_limit）
      ungate    信号为真时解除 swing_gate 收紧，回到 P1/P2 目标
      to_cap    信号为真时把仓位补足到 hard_cap*cap_ratio
      fast      信号为真时解除「降仓冷却」造成的低目标：用近 5 日目标上沿
                代替当日被压低的目标（8/15 target=5% 就属于这种自锁）
      combo     to_cap + fast
      bull      combo + 权限豁免：信号为真时把状态权限临时上浮到 bull_cap%
    ``bull_cap`` / ``cap_boost`` > 1 均为**诊断列**：现有 hard_cap 已含 P0
    风险预算反解上限，突破它必须由 V10.7 的 authority 层重新授权并重算
    risk_cap，不能只做一层 overlay。
    """
    gate = (d.default_no_limit / 100.0).to_numpy(dtype=float)
    base = (d.base_target / 100.0).to_numpy(dtype=float)
    cap = np.minimum(1.0, (d.hard_cap / 100.0).to_numpy(dtype=float)
                     * cap_boost)
    s = np.asarray(d[sig], dtype=bool)
    # 近 5 日目标上沿：只用于解除冷却自锁，属于 T 日及之前信息
    recent = pd.Series(base).rolling(5, min_periods=1).max().to_numpy()
    if bull_cap is None:
        cap_eff = cap
    else:
        cap_eff = np.where(s, np.maximum(cap, bull_cap / 100.0), cap)
    if mode == "baseline":
        return gate
    if mode == "ungate":
        return np.where(s, np.maximum(gate, base), gate)
    if mode == "to_cap":
        return np.where(s, np.minimum(cap_eff,
                        np.maximum(gate, cap_eff * cap_ratio)), gate)
    if mode == "fast":
        return np.where(s, np.minimum(cap_eff,
                        np.maximum(gate, recent)), gate)
    if mode in ("combo", "bull"):
        tgt = np.minimum(cap_eff, np.maximum(np.maximum(recent, gate),
                                             cap_eff * cap_ratio))
        return np.where(s, tgt, gate)
    raise ValueError(mode)


def apply_limit(w_des, up_step=10.0):
    """统一升仓限速执行器：升仓 <= up_step pp/日，降仓立即（风险优先）。"""
    return apply_limit_var(w_des, np.full(len(w_des), float(up_step)))


def apply_limit_var(w_des, step_arr):
    """逐日可变升仓限速；降仓永远立即执行。"""
    out, prev = [], None
    for w, st in zip(w_des, step_arr):
        w = float(np.clip(w, 0.0, 1.0))
        if prev is None or w <= prev:
            v = w
        else:
            v = min(w, prev + float(st) / 100.0)
        out.append(v)
        prev = v
    return np.asarray(out)


def latch(sig, hold, exit_mask=None, renew=True):
    """信号锁存：首次触发后保持 hold 个交易日；期间重复触发可续期。

    ``exit_mask`` 为真时立即解除锁存（例如收盘跌破 MA20）。
    ★ 只依赖当前及之前的信号，不含未来数据。
    """
    s = np.asarray(sig, dtype=bool)
    ex = (np.zeros(len(s), dtype=bool) if exit_mask is None
          else np.asarray(exit_mask, dtype=bool))
    out, left = [], 0
    for i, hit in enumerate(s):
        if hit and (renew or left <= 0):
            left = int(hold)
        elif hit and left < int(hold):
            left = int(hold)
        if ex[i]:
            left = 0
        out.append(left > 0)
        if left > 0:
            left -= 1
    return np.asarray(out, dtype=bool)


# -------------------------------------------------------------- 评估
def evaluate(w, d, bench, fee_bp=FEE_BP, date_range=None, cash_annual=0.0):
    """T 日权重 w[T] → 承担 close[T+1]/close[T]-1。"""
    dates = bench.trade_date.tolist()
    close = bench.close.values
    pos = {x: i for i, x in enumerate(dates)}
    wmap = dict(zip(d.trade_date, w))
    cash_d = cash_annual / TRADING_DAYS
    rec, prev = [], None
    for dt in d.trade_date:
        i = pos.get(dt)
        if i is None or i + 1 >= len(dates):
            continue
        if date_range and not (date_range[0] <= dt <= date_range[1]):
            continue
        wt = float(wmap[dt])
        r_idx = close[i + 1] / close[i] - 1.0
        fee = (abs(wt - prev) * fee_bp / 10000) if prev is not None else 0.0
        r = wt * r_idx + (1 - wt) * cash_d - fee
        rec.append((dates[i + 1], r, wt, r_idx))
        prev = wt
    return pd.DataFrame(rec, columns=["date", "ret", "w", "idx_ret"])


def evaluate_constant(w_const, d, bench, fee_bp=FEE_BP, date_range=None,
                      cash_annual=0.0):
    return evaluate(np.full(len(d), w_const), d, bench, fee_bp,
                    date_range, cash_annual)


def metrics(df):
    if df.empty:
        return {}
    nav = (1 + df.ret).cumprod()
    dd = (nav / nav.cummax() - 1).min()
    up = df[df.idx_ret > 0]
    dn = df[df.idx_ret < 0]
    turn = float(df.w.diff().abs().sum() + df.w.iloc[0])
    return {
        "总收益%": round((nav.iloc[-1] - 1) * 100, 2),
        "最大回撤%": round(dd * 100, 2),
        "年化波动%": round(df.ret.std() * np.sqrt(TRADING_DAYS) * 100, 2),
        "Sharpe": round(df.ret.mean() / df.ret.std() * np.sqrt(TRADING_DAYS), 2)
                  if df.ret.std() > 0 else None,
        "平均仓位%": round(df.w.mean() * 100, 2),
        "累计换手(倍)": round(turn, 2),
        "上行捕获": round(df.loc[up.index, "ret"].sum() /
                        df.loc[up.index, "idx_ret"].sum(), 3) if len(up) else None,
        "下行捕获": round(df.loc[dn.index, "ret"].sum() /
                        df.loc[dn.index, "idx_ret"].sum(), 3) if len(dn) else None,
        "收益回撤比": round(((nav.iloc[-1] - 1) * 100) / abs(dd * 100), 2)
                    if dd < 0 else None,
    }


def signal_quality(d, sig):
    """信号质量：触发次数、前瞻 20 日胜率、假信号比例、每年触发天数。"""
    x = d[d[sig] & d.fwd20.notna()]
    if x.empty:
        return {"触发天数": 0}
    f = x.fwd20
    by_year = {}
    for y in ("2025", "2026"):
        by_year[y] = int((d[d[sig] & (d.trade_date.str[:4] == y)]).shape[0])
    # 只在「首次触发」(前 5 日未触发) 上统计，避免连续信号重复计数
    first = d[sig] & ~d[sig].shift(1).fillna(False).astype(bool) & \
            ~d[sig].shift(2).fillna(False).astype(bool) & \
            ~d[sig].shift(3).fillna(False).astype(bool) & \
            ~d[sig].shift(4).fillna(False).astype(bool) & \
            ~d[sig].shift(5).fillna(False).astype(bool)
    xf = d[first & d.fwd20.notna()]
    return {
        "触发天数": int(d[sig].sum()),
        "首次触发次数": int(len(xf)),
        "2025触发": by_year["2025"],
        "2026触发": by_year["2026"],
        "前瞻20日均值%": round(f.mean() * 100, 2),
        "前瞻20日胜率": round((f > 0).mean(), 3),
        "假信号率(20日<-3%)": round((f < -0.03).mean(), 3),
        "首次触发前瞻20日均值%": round(xf.fwd20.mean() * 100, 2)
                            if len(xf) else None,
        "首次触发胜率": round((xf.fwd20 > 0).mean(), 3) if len(xf) else None,
    }


def timeliness(d, sig):
    """及时性：客观标注主升波段起点（未来 40 日涨幅 > 10% 的局部低点），
    测算信号首次触发的滞后与「已吃掉涨幅占比」。未来数据仅用于评估。"""
    starts = d[d.is_local_low & (d.fwd40_best > 0.10)]
    # 去掉 20 日内的重复起点
    picked, last = [], -999
    for i in starts.index:
        if i - last >= 20:
            picked.append(i)
            last = i
    rows = []
    for i in picked:
        end = min(i + 40, len(d) - 1)
        seg = d.loc[i:end]
        hit = seg[seg[sig]]
        total = float(d.close.iloc[end] / d.close.iloc[i] - 1.0)
        if hit.empty:
            rows.append({"起点": d.trade_date.iloc[i], "滞后天数": None,
                         "波段涨幅%": round(total * 100, 2)})
        else:
            j = hit.index[0]
            lag = int(j - i)
            eaten = float(d.close.iloc[j] / d.close.iloc[i] - 1.0)
            rows.append({
                "起点": d.trade_date.iloc[i], "触发日": d.trade_date.iloc[j],
                "滞后天数": lag, "波段涨幅%": round(total * 100, 2),
                "触发时已涨%": round(eaten * 100, 2),
                "踏空占比": round(eaten / total, 3) if total > 0 else None,
            })
    lags = [r["滞后天数"] for r in rows if r.get("滞后天数") is not None]
    eats = [r["踏空占比"] for r in rows if r.get("踏空占比") is not None]
    return {
        "主升波段数": len(rows),
        "捕捉到的波段": len(lags),
        "平均滞后(交易日)": round(float(np.mean(lags)), 1) if lags else None,
        "中位滞后(交易日)": round(float(np.median(lags)), 1) if lags else None,
        "平均踏空占比": round(float(np.mean(eats)), 3) if eats else None,
        "明细": rows,
    }


# ---------------------------------------------------------------- 主流程
def main():
    bench = load_bench()
    micro = load_micro()
    plan = load_plan()
    d = build_features(bench, micro, plan)
    res = {"区间": f"{d.trade_date.iloc[0]}~{END}（{len(d)} 个决策日）",
           "口径": f"沪深300、T日盘后决策/T日收盘成交/T→T+1收益、{FEE_BP}bp 单边换手、"
                 "信号只用 <=T 数据、升仓不突破 hard_cap"}

    # ---- 1) 信号质量与及时性 ----
    sq, tl = {}, {}
    for s in SIGNALS:
        sq[s] = signal_quality(d, s)
        tl[s] = timeliness(d, s)
    res["信号质量"] = sq
    res["及时性"] = tl

    # ---- 2) 基准与升仓动作（理论无限速 / 可执行限速 两套） ----
    years = {"2025": ("20250101", "20251231"),
             "2026": ("20260101", "20261104")}
    # (name, mode, sig, cap_ratio, bull_cap)
    combos = [
        ("baseline", "baseline", "s_ma20up", 0.9, None),
        ("ungate|s_trend", "ungate", "s_trend", 0.9, None),
        ("ungate|s_break", "ungate", "s_break", 0.9, None),
        ("ungate|s_brk20", "ungate", "s_brk20", 0.9, None),
        ("ungate|s_state6", "ungate", "s_state6", 0.9, None),
        ("to_cap|s_trend", "to_cap", "s_trend", 0.9, None),
        ("to_cap|s_break", "to_cap", "s_break", 0.9, None),
        ("to_cap|s_brk20", "to_cap", "s_brk20", 0.9, None),
        ("to_cap|s_mom5", "to_cap", "s_mom5", 0.9, None),
        ("to_cap|s_brk60", "to_cap", "s_brk60", 0.9, None),
        ("to_cap|s_all", "to_cap", "s_all", 0.9, None),
        ("to_cap|s_ma60above", "to_cap", "s_ma60above", 0.9, None),
        ("to_cap|s_golden", "to_cap", "s_golden", 0.9, None),
        ("to_cap|s_state6", "to_cap", "s_state6", 0.9, None),
        ("to_cap|s_rebound", "to_cap", "s_rebound", 0.9, None),
        ("combo|s_trend", "combo", "s_trend", 0.9, None),
        ("combo|s_break", "combo", "s_break", 0.9, None),
        ("combo|s_all", "combo", "s_all", 0.9, None),
        ("combo|s_trend|1.0", "combo", "s_trend", 1.0, None),
        # ↓ 权限豁免诊断列：需 V10.7 authority 层重算 + P0 风险预算授权
        ("bull60|s_brk60", "bull", "s_brk60", 0.9, 60),
        ("bull60|s_mom5", "bull", "s_mom5", 0.9, 60),
        ("bull60|s_trend", "bull", "s_trend", 0.9, 60),
        ("bull80|s_brk60", "bull", "s_brk60", 0.9, 80),
        ("bull80|s_mom5", "bull", "s_mom5", 0.9, 80),
        ("bull80|s_break", "bull", "s_break", 0.9, 80),
        ("bull90|s_brk60", "bull", "s_brk60", 0.9, 90),
    ]
    combo_map = {c[0]: c for c in combos}

    def w_of(name):
        _, mode, sig, ratio, bull = combo_map[name]
        return desired_weight(d, mode, sig, cap_ratio=ratio, bull_cap=bull)

    rows, navs = {}, {}
    for name, mode, sig, ratio, bull in combos:
        w = w_of(name)
        w_lim = apply_limit(w, 10.0)
        df = evaluate(w, d, bench)
        dfl = evaluate(w_lim, d, bench)
        m = metrics(df)
        const = metrics(evaluate_constant(df.w.mean(), d, bench))["总收益%"]
        m["同均值恒定仓位收益%"] = const
        m["波段alpha(pp)"] = round(m["总收益%"] - const, 2)
        m["_含10pp限速后收益%"] = metrics(dfl)["总收益%"]
        m["_含10pp限速后均仓%"] = metrics(dfl)["平均仓位%"]
        m["_信号"] = sig
        m["_模式"] = mode
        rows[name] = m
        navs[name] = (1 + df.ret).cumprod().set_axis(df.date).to_frame(name)
        navs[name + "_限速"] = ((1 + dfl.ret).cumprod()
                              .set_axis(dfl.date).to_frame(name + "_限速"))
    # 生产含限速基准（真实落地值）
    prod = evaluate(d.effective.values / 100.0, d, bench)
    rows["生产口径(含闸门+限速)"] = metrics(prod)
    navs["生产口径(含闸门+限速)"] = ((1 + prod.ret).cumprod()
                               .set_axis(prod.date)
                               .to_frame("生产口径(含闸门+限速)"))
    # 满仓
    full = evaluate(np.ones(len(d)), d, bench)
    rows["满仓买入持有"] = metrics(full)
    navs["满仓买入持有"] = ((1 + full.ret).cumprod()
                       .set_axis(full.date).to_frame("满仓买入持有"))
    res["策略对比"] = rows

    # ---- 3) 分年（按决策日归属） ----
    picks = ["baseline", "to_cap|s_trend", "combo|s_trend",
             "combo|s_break", "to_cap|s_state6", "bull60|s_brk60",
             "bull60|s_mom5", "bull80|s_brk60", "bull90|s_brk60"]
    fy = {}
    for y, (lo, hi) in years.items():
        fy[y] = {}
        for name in picks:
            w = w_of(name)
            df = evaluate(w, d, bench, date_range=(lo, hi))
            m = metrics(df)
            cm = metrics(evaluate_constant(df.w.mean(), d, bench,
                                           date_range=(lo, hi)))
            m["同均值恒定%"] = cm["总收益%"]
            m["波段alpha(pp)"] = round(m["总收益%"] - cm["总收益%"], 2)
            fy[y][name] = m
        pm = metrics(evaluate(d.effective.values / 100.0, d, bench,
                              date_range=(lo, hi)))
        fy[y]["生产口径(含闸门+限速)"] = pm
        fy[y]["满仓买入持有"] = metrics(
            evaluate(np.ones(len(d)), d, bench, date_range=(lo, hi)))
    res["分年"] = fy

    # ---- 4) 参数敏感性（防挑最优） ----
    sens = {}
    for mom in (0.03, 0.05, 0.08):
        for br in (0.55, 0.58, 0.60):
            sig = f"_tmp_mom{mom}_br{br}"
            d[sig] = ((d.mom20 > mom) & (d.breadth5 > br) &
                      (d.close > d.ma20)).fillna(False)
            w = desired_weight(d, "combo", sig, cap_ratio=0.9)
            m = metrics(evaluate(w, d, bench))
            sens[f"mom>{mom}&breadth>{br}"] = {
                "全期收益%": m["总收益%"],
                "2025收益%": metrics(evaluate(w, d, bench,
                        date_range=years["2025"]))["总收益%"],
                "2026收益%": metrics(evaluate(w, d, bench,
                        date_range=years["2026"]))["总收益%"],
                "触发天数": int(d[sig].sum()),
            }
    # hard_cap 上浮诊断（需 P0 授权，非默认建议）
    for boost in (1.0, 1.15, 1.3):
        w = desired_weight(d, "combo", "s_trend", 0.9, cap_boost=boost)
        m = metrics(evaluate(w, d, bench))
        sens[f"cap_boost×{boost}"] = {
            "全期收益%": m["总收益%"], "平均仓位%": m["平均仓位%"],
            "最大回撤%": m["最大回撤%"],
            "_note": "仅诊断：突破 hard_cap 需 P0 风险预算重新授权" if boost > 1 else "",
        }
    res["参数敏感性"] = sens

    # ---- 5) 成本 / 现金 / 限速敏感性 ----
    cst = {}
    for name in ("baseline", "combo|s_trend", "bull60|s_brk60"):
        w = w_of(name)
        cst[name] = {}
        for fee in (0.0, 5.0, 10.0):
            cst[name][f"{fee}bp"] = metrics(evaluate(w, d, bench,
                    fee_bp=fee))["总收益%"]
        cst[name]["现金1.5%+5bp"] = metrics(evaluate(
            w, d, bench, cash_annual=0.015))["总收益%"]
        for step in (10, 20, 30):
            cst[name][f"限速{step}pp"] = metrics(evaluate(
                apply_limit(w, float(step)), d, bench))["总收益%"]
    res["成本与限速敏感性"] = cst

    # ---- 5b) 信号锁存 + 升仓速度（回答「及时升仓」的核心） ----
    # 诊断：单日有效的确认信号会「当天进、次日退」，换手 50 倍被成本吃掉。
    # 锁存让确认后维持若干日升仓，并用「收盘跌破 MA20」作为趋势退出。
    exit_mask = (d.close < d.ma20).fillna(False).to_numpy(dtype=bool)
    latch_defs = [
        ("rebound_h5", "s_any_confirm", 5, None),
        ("rebound_h10", "s_any_confirm", 10, None),
        ("rebound_h10_exitMA", "s_any_confirm", 10, exit_mask),
        ("rebound_h20_exitMA", "s_any_confirm", 20, exit_mask),
        ("breadth_h10_exitMA", "s_breadth", 10, exit_mask),
        ("trend_h10_exitMA", "s_trend", 10, exit_mask),
        ("brk20_h10_exitMA", "s_brk20", 10, exit_mask),
        ("volume_h10_exitMA", "s_volume", 10, exit_mask),
    ]
    lt = {}
    for tag, sig, hold, ex in latch_defs:
        col = "_latch_" + tag
        d[col] = latch(d[sig].to_numpy(dtype=bool), hold, ex)
        w = desired_weight(d, "combo", col, cap_ratio=0.9)
        df = evaluate(w, d, bench)
        m = metrics(df)
        const = metrics(evaluate_constant(df.w.mean(), d, bench))["总收益%"]
        m["波段alpha(pp)"] = round(m["总收益%"] - const, 2)
        m["_锁存天数"] = int(d[col].sum())
        # 固定限速 / 条件限速（锁存期内 30pp，其余 10pp）
        m["_限速10pp"] = metrics(evaluate(apply_limit(w, 10), d, bench))["总收益%"]
        steps = np.where(d[col].to_numpy(dtype=bool), 30.0, 10.0)
        m["_锁存期30pp_其余10pp"] = metrics(
            evaluate(apply_limit_var(w, steps), d, bench))["总收益%"]
        for y, (lo, hi) in years.items():
            m[f"_{y}收益%"] = metrics(evaluate(w, d, bench,
                                    date_range=(lo, hi)))["总收益%"]
        lt[tag] = m
    res["锁存与升仓速度"] = lt

    # 及时性阈值稳健性（主升波段标注只有 2 段，换阈值再验一次排序）
    tl2 = {}
    for thr in (0.07, 0.08, 0.10):
        starts = d[d.is_local_low & (d.fwd40_best > thr)]
        picked, last = [], -999
        for i in starts.index:
            if i - last >= 20:
                picked.append(i)
                last = i
        r2 = {}
        for s in SIGNALS:
            eats, lags = [], []
            for i in picked:
                end = min(i + 40, len(d) - 1)
                seg = d.loc[i:end]
                hit = seg[seg[s]]
                total = float(d.close.iloc[end] / d.close.iloc[i] - 1.0)
                if hit.empty or total <= 0:
                    continue
                j = hit.index[0]
                lags.append(j - i)
                eats.append((d.close.iloc[j] / d.close.iloc[i] - 1.0) / total)
            r2[s] = {
                "波段数": len(picked),
                "平均滞后": round(float(np.mean(lags)), 1) if lags else None,
                "平均踏空占比": round(float(np.mean(eats)), 3) if eats else None,
            }
        tl2[f"fwd40_best>{thr}"] = r2
    res["及时性阈值稳健性"] = tl2

    # ---- 5c) 最优方案稳健性 + 升仓速度量化 ----
    best_col = "_latch_breadth_h10_exitMA"
    w_best = desired_weight(d, "combo", best_col, cap_ratio=0.9)
    steps_best = np.where(d[best_col].to_numpy(dtype=bool), 30.0, 10.0)
    w_fast = apply_limit_var(w_best, steps_best)
    w_slow = apply_limit(w_best, 10.0)
    robust = {"方案": "5日上涨占比>0.58 锁存10日 + 收盘跌破MA20退出 + "
                     "向hard_cap补足 + 锁存期升仓限速30pp(其余10pp) + 降仓立即"}
    for fee in (0.0, 5.0, 10.0):
        robust[f"成本{fee}bp"] = {
            "最优方案": metrics(evaluate(w_fast, d, bench, fee_bp=fee))["总收益%"],
            "生产口径": metrics(evaluate(
                d.effective.to_numpy(dtype=float) / 100.0, d, bench,
                fee_bp=fee))["总收益%"],
        }
    robust["现金1.5%+5bp"] = metrics(evaluate(
        w_fast, d, bench, cash_annual=0.015))["总收益%"]
    # 多指数（同一仓位曲线，只换标的）
    con = sqlite3.connect("file:" + DB.replace("\\", "/") + "?mode=ro", uri=True)
    multi = {}
    for code in ("000300.SH", "000001.SH", "000905.SH", "000852.SH",
                 "000985.CSI"):
        try:
            bx = pd.read_sql(
                "SELECT trade_date, close FROM index_daily_cache "
                "WHERE ts_code=? AND trade_date>=? AND trade_date<=? "
                "ORDER BY trade_date", con,
                params=(code, "20240101", END))
        except Exception:
            continue
        bx["trade_date"] = bx.trade_date.astype(str)
        bx = bx.dropna().drop_duplicates("trade_date").reset_index(drop=True)
        if len(bx) < 100:
            continue
        multi[code] = {
            "最优方案": metrics(evaluate(w_fast, d, bx))["总收益%"],
            "生产口径": metrics(evaluate(
                d.effective.to_numpy(dtype=float) / 100.0, d, bx))["总收益%"],
            "满仓": metrics(evaluate(np.ones(len(d)), d, bx))["总收益%"],
        }
    con.close()
    robust["多指数"] = multi
    # 2026 假牛市反弹段（3/23~5/15）
    fake = ("20260323", "20260515")
    robust["2026假反弹段收益%"] = {
        "最优方案": metrics(evaluate(w_fast, d, bench,
                            date_range=fake))["总收益%"],
        "生产口径": metrics(evaluate(
            d.effective.to_numpy(dtype=float) / 100.0, d, bench,
            date_range=fake))["总收益%"],
        "满仓": metrics(evaluate(np.ones(len(d)), d, bench,
                        date_range=fake))["总收益%"],
    }
    # 升仓速度：从低位(<25%)开始的一次连续升仓，达到 60% 所需交易日
    def climb_days(w):
        runs, cur = [], []
        for x in w:
            if x > (cur[-1] + 1e-9 if cur else -1):
                cur.append(x)
            else:
                if cur:
                    runs.append(cur)
                cur = [x]
        if cur:
            runs.append(cur)
        best = None
        for r in runs:
            if r[0] < 0.25 and max(r) >= 0.55:
                k = next(i for i, x in enumerate(r) if x >= 0.55)
                if best is None or k < best:
                    best = k
        return best
    robust["升仓速度_从25%到55%所需交易日"] = {
        "30pp条件限速": climb_days(w_fast),
        "10pp固定限速": climb_days(w_slow),
        "无限速": climb_days(w_best),
    }
    robust["平均仓位%"] = {
        "最优方案": round(float(w_fast.mean() * 100), 2),
        "生产口径": round(float(d.effective.mean()), 2),
    }
    res["最优方案稳健性"] = robust
    navs["最优方案_广度锁存+条件提速"] = (
        (1 + evaluate(w_fast, d, bench).ret).cumprod()
        .set_axis(evaluate(w_fast, d, bench).date)
        .to_frame("最优方案_广度锁存+条件提速"))

    # ---- 6) 逐日信号与仓位明细 ----
    out = d[["trade_date", "close", "state", "base_target", "hard_cap",
             "default_no_limit", "effective", "s_trend", "s_break",
             "s_brk20", "s_mom5", "s_breadth", "s_volume", "s_state6",
             "fwd20"]].copy()
    out["w_baseline"] = desired_weight(d, "baseline", "s_trend") * 100
    out["w_combo_trend"] = desired_weight(d, "combo", "s_trend") * 100
    out["w_combo_break"] = desired_weight(d, "combo", "s_break") * 100
    out.to_csv(os.path.join(BASE_DIR, "_bt_bull_entry_signals.csv"),
               index=False, encoding="utf-8-sig", float_format="%.4f")
    navdf = pd.concat(navs.values(), axis=1)
    navdf.to_csv(os.path.join(BASE_DIR, "_bt_bull_entry_nav.csv"),
                 encoding="utf-8-sig", float_format="%.6f")
    with open(os.path.join(BASE_DIR, "_bt_bull_entry_metrics.json"),
              "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("OK  rows", len(d), "signals", len(SIGNALS), "strategies", len(rows))
    print("主升波段标注:", tl["s_trend"]["主升波段数"])
    for k in ("baseline", "combo|s_trend", "bull60|s_brk60",
              "bull80|s_brk60"):
        v = rows[k]
        print(f"{k:30s} 收益{v['总收益%']:7.2f} 回撤{v['最大回撤%']:7.2f} "
              f"均仓{v['平均仓位%']:6.1f} alpha{v['波段alpha(pp)']:6.2f} "
              f"限速后{v['_含10pp限速后收益%']:7.2f}")


if __name__ == "__main__":
    main()
