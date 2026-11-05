# -*- coding: utf-8 -*-
"""波段收益优化实验：在 V10.7.2 仓位上叠加纯增量 overlay，实测 A/B。

设计约束
--------
* 不改 v10 / v101 / V10.7 源码；所有改动只是「再算一层有效仓位」。
* 纯收紧型 overlay 保证 w_eff <= w_base <= hard_cap，不突破任何既有约束。
* 所有信号只用 <= T 的指数数据；T 日收盘成交，吃 T -> T+1 收益（无未来函数）。
* 主力评估含 5bp 单边换手成本；另给 0 / 10bp 与现金年化 1.5% 敏感性。
* 做过拟合检验：MA 参数网格、5 个指数、上下半年分半期。

输出：_bt_swing_optimizer_metrics.json / _bt_swing_optimizer_nav.csv
"""
import os
import re
import json
import sqlite3
from collections import OrderedDict

import numpy as np
import pandas as pd

SOLO_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(SOLO_DIR)
DB = os.path.join(BASE_DIR, "cache_daily", "stock_data.db")
BENCH = "000300.SH"
TRADING_DAYS = 244
FEE_BP = 5.0
INDICES = [("000300.SH", "沪深300"), ("000001.SH", "上证指数"),
           ("000905.SH", "中证500"), ("000852.SH", "中证1000"),
           ("000985.CSI", "中证全指")]


def load_index(code, start="20240101"):
    con = sqlite3.connect("file:" + DB.replace("\\", "/") + "?mode=ro", uri=True)
    try:
        df = pd.read_sql(
            "SELECT trade_date, close FROM index_daily_cache "
            "WHERE ts_code=? AND trade_date>=? AND trade_date<='20261104' ORDER BY trade_date",
            con, params=(code, start))
    finally:
        con.close()
    df["trade_date"] = df.trade_date.astype(str)
    return df.dropna().drop_duplicates("trade_date").reset_index(drop=True)


def load_plan():
    d = pd.read_csv(os.path.join(SOLO_DIR, "_bt_opt_replay_on.csv"),
                    encoding="utf-8-sig")
    d["trade_date"] = d.trade_date.astype(str)
    out = OrderedDict()
    for _, r in d.iterrows():
        out[r.trade_date] = {
            "w": float(r.target) / 100.0,
            "cap": float(r.hard_cap) / 100.0,
            "state": str(r.eff_state),
            "easing": str(r.easing),
            "rebound": str(r.rebound),
        }
    return out


def build_signals(idx):
    """每个交易日的信号，严格只用 <= 当日 的收盘价。"""
    c = idx.close.to_numpy(float)
    dates = idx.trade_date.tolist()
    out = {}
    for i, d in enumerate(dates):
        s = {"close": c[i]}
        for n in (5, 10, 20, 30, 60):
            s[f"ma{n}"] = float(c[max(0, i - n + 1):i + 1].mean()) if i >= n - 1 else None
        s["ma20_prev5"] = (float(c[max(0, i - 24):i - 4].mean())
                           if i >= 24 else None)
        s["ret20"] = (c[i] / c[i - 20] - 1.0) if i >= 20 else None
        s["vol20"] = (float(np.std(c[i - 19:i + 1] / c[i - 20:i] - 1.0, ddof=1)
                            * np.sqrt(TRADING_DAYS)) if i >= 20 else None)
        win = c[max(0, i - 59):i + 1]
        s["dd60"] = c[i] / float(win.max()) - 1.0
        out[d] = s
    return out


def factor_for(name, plan_d, sig, prev_w):
    """返回有效仓位。纯收紧型：<= w_base；授权型用 cap 兜底并单独标注。"""
    w, cap, st = plan_d["w"], plan_d["cap"], plan_d["state"]
    ma20, ma60 = sig.get("ma20"), sig.get("ma60")
    above_ma = (ma20 is not None and sig["close"] > ma20)
    slope_up = (sig.get("ma20_prev5") is not None and ma20 is not None
                and ma20 > sig["ma20_prev5"])

    def gate(ok, off):
        return w if ok else w * off

    if name == "baseline":
        return w
    if re.match(r"^ma\d+_(0(\.\d+)?|1(\.0+)?)$", name):
        n, off = name.split("_")[0], float(name.split("_")[1])
        man = sig.get(n)
        return w if (man is not None and sig["close"] > man) else w * off
    if name == "ma20_slope_025":
        return gate(above_ma and slope_up, 0.25)
    if name == "ma20_slope_0":
        return gate(above_ma and slope_up, 0.0)
    if name == "mom20_025":
        return gate(sig["ret20"] is not None and sig["ret20"] > 0, 0.25)
    if name == "dd_brake":
        return w * (0.3 if sig["dd60"] <= -0.05 else 1.0)
    if name == "vol_scale":
        v = sig["vol20"]
        return w * (float(np.clip(0.15 / v, 0.4, 1.0)) if v and v > 0 else 1.0)
    if name == "state_s2s5":
        return w * (0.3 if st in ("S2", "S5") else 1.0)
    if name == "state_all":
        f = {"S2": 0.3, "S5": 0.3, "S4": 0.6}.get(st, 1.0)
        return w * f
    if name == "trend_boost":
        # 授权型：仅在 hard_cap 余量内放宽，需配置显式开启
        if above_ma and slope_up and st in ("S1", "S6"):
            return min(cap, w * 1.3)
        return w
    if name.startswith("combo_"):
        parts = name.split("_")[1:]
        f = 1.0
        boost = "boost" in parts
        for p in parts:
            if p.startswith("ma") and p[2:].isdigit():
                man = sig.get(p)
                if man is None or sig["close"] <= man:
                    f *= 0.25
            elif p == "slope":
                if not (above_ma and slope_up):
                    f *= 0.25
            elif p == "s2s5":
                if st in ("S2", "S5"):
                    f *= 0.3
            elif p == "dd":
                if sig["dd60"] <= -0.05:
                    f *= 0.3
            elif p == "boost":
                pass
        w_eff = w * f
        if boost and above_ma and slope_up and st in ("S1", "S6"):
            w_eff = min(cap, w_eff * 1.3)
        return min(w_eff, cap)
    raise KeyError(name)


def deadband(w_eff, prev_w, band=0.05):
    if prev_w is not None and abs(w_eff - prev_w) < band:
        return prev_w
    return w_eff


def evaluate(strategy, idx, plan, sigs, fee_bp=FEE_BP,
             cash_annual=0.0, band=0.0, date_range=None, w_scale=1.0,
             max_up_step=None):
    dates = idx.trade_date.tolist()
    pos = {d: i for i, d in enumerate(dates)}
    close = idx.close.to_numpy(float)
    cash_d = (1 + cash_annual) ** (1 / TRADING_DAYS) - 1
    prev_w, rec = 0.0, []
    for d, pd_ in plan.items():
        i = pos.get(d)
        if i is None or i + 1 >= len(dates) or d not in sigs:
            continue
        w = min(factor_for(strategy, pd_, sigs[d], prev_w) * w_scale,
                pd_["cap"])
        if band:
            w = deadband(w, prev_w, band)
        if max_up_step and rec and w > prev_w + max_up_step:
            w = prev_w + max_up_step
        r_idx = close[i + 1] / close[i] - 1.0
        r = w * r_idx + (1 - w) * cash_d - abs(w - prev_w) * fee_bp / 10000
        rec.append((dates[i + 1], r, w, r_idx))
        prev_w = w
    df = pd.DataFrame(rec, columns=["date", "ret", "w", "idx_ret"])
    if date_range:
        df = df[(df.date >= date_range[0]) & (df.date <= date_range[1])]
        return df.reset_index(drop=True)
    return df


def evaluate_constant(w, idx, plan, sigs, fee_bp=FEE_BP,
                      cash_annual=0.0, date_range=None):
    """同均值恒定仓位对照：只在首日建仓产生换手成本。"""
    dates = idx.trade_date.tolist()
    pos = {d: i for i, d in enumerate(dates)}
    close = idx.close.to_numpy(float)
    cash_d = (1 + cash_annual) ** (1 / TRADING_DAYS) - 1
    prev_w, rec = 0.0, []
    for d in plan:
        i = pos.get(d)
        if i is None or i + 1 >= len(dates):
            continue
        r_idx = close[i + 1] / close[i] - 1.0
        r = w * r_idx + (1 - w) * cash_d - abs(w - prev_w) * fee_bp / 10000
        rec.append((dates[i + 1], r, w, r_idx))
        prev_w = w
    df = pd.DataFrame(rec, columns=["date", "ret", "w", "idx_ret"])
    if date_range:
        df = df[(df.date >= date_range[0]) & (df.date <= date_range[1])]
        return df.reset_index(drop=True)
    return df


def normalized_scale(strategy, idx, plan, sigs, target_mean):
    """二分搜索 w_scale，使缩放后（受 hard_cap 约束）平均仓位回到 target_mean。"""
    lo, hi = 1.0, 50.0
    for _ in range(24):
        mid = (lo + hi) / 2
        m = evaluate(strategy, idx, plan, sigs, w_scale=mid).w.mean()
        if m < target_mean:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def metrics(df):
    if df.empty:
        return {}
    r = df.ret
    nav = (1 + r).cumprod()
    total = float(nav.iloc[-1] - 1)
    mdd = float((nav / nav.cummax() - 1).min())
    vol = float(r.std(ddof=1) * np.sqrt(TRADING_DAYS))
    up = df[df.idx_ret > 0]
    dn = df[df.idx_ret < 0]
    turn = float(df.w.diff().abs().sum())
    return {
        "总收益%": round(total * 100, 2),
        "最大回撤%": round(mdd * 100, 2),
        "年化波动%": round(vol * 100, 2),
        "Sharpe": round(float(r.mean() / r.std(ddof=1) * np.sqrt(TRADING_DAYS)), 2)
                  if r.std(ddof=1) > 0 else 0.0,
        "收益回撤比": round(total / abs(mdd), 2) if mdd < 0 else None,
        "上行捕获": round(float(up.ret.mean() / up.idx_ret.mean()), 2) if len(up) else None,
        "下行捕获": round(float(dn.ret.mean() / dn.idx_ret.mean()), 2) if len(dn) else None,
        "平均仓位%": round(float(df.w.mean()) * 100, 1),
        "累计换手(倍)": round(turn, 2),
        "末值": round(float(nav.iloc[-1]), 4),
    }


def main():
    idx = load_index(BENCH)
    plan = load_plan()
    sigs = build_signals(idx)
    _dates = idx.trade_date.tolist()
    dec_of = {_dates[i + 1]: _dates[i] for i in range(len(_dates) - 1)}

    def yslice(df, lo, hi):
        """按决策日归属切分年份，避免跨年收益错配。"""
        return df[[lo <= dec_of.get(d, "") <= hi for d in df.date]].reset_index(drop=True)

    strategies = ["baseline", "ma20_0.25", "ma20_0", "ma60_0.25", "ma10_0.25",
                  "ma20_slope_025", "mom20_025", "dd_brake", "vol_scale",
                  "state_s2s5", "state_all", "trend_boost",
                  "combo_ma20_s2s5", "combo_ma20_slope_s2s5",
                  "combo_ma20_dd", "combo_ma20_s2s5_dd",
                  "combo_ma20_slope_s2s5_boost"]

    res = {"_口径": {
        "区间": "20250103~20261104（2025 全年 + 2026 年至今，连续回放）",
        "标的": "000300.SH 沪深300 收盘价",
        "执行": "T 日盘后决策、T 日收盘成交、承担 T→T+1 收益；信号只用 <=T 数据",
        "成本": f"主表 {FEE_BP:.0f}bp 单边换手；现金零收益",
        "约束": "除 trend_boost / combo_*_boost 外，有效仓位恒 <= 原目标仓位 <= 硬上限",
    }}

    rows, navs = {}, {}
    base_mean = evaluate("baseline", idx, plan, sigs).w.mean()
    const_base = metrics(evaluate_constant(base_mean, idx, plan, sigs))["总收益%"]
    res["同均值基准"] = {"平均仓位%": round(base_mean * 100, 1),
                     f"恒定{round(base_mean*100,1)}%仓位收益%(5bp)": const_base}
    for s in strategies:
        df = evaluate(s, idx, plan, sigs)
        band_df = evaluate(s, idx, plan, sigs, band=0.05)
        m = metrics(df)
        cm = metrics(evaluate_constant(df.w.mean(), idx, plan, sigs))
        m["同均值恒定仓位收益%"] = cm["总收益%"]
        m["波段alpha(pp)"] = round(m["总收益%"] - cm["总收益%"], 2)
        k = normalized_scale(s, idx, plan, sigs, base_mean)
        ndf = evaluate(s, idx, plan, sigs, w_scale=k)
        nm = metrics(ndf)
        m["归一化缩放系数"] = round(k, 2)
        m["归一化后平均仓位%"] = nm["平均仓位%"]
        m["归一化后收益%"] = nm["总收益%"]
        m["归一化后最大回撤%"] = nm["最大回撤%"]
        m["归一化后上行捕获"] = nm["上行捕获"]
        m["归一化后下行捕获"] = nm["下行捕获"]
        # alpha 必须与「归一化后实际平均仓位」的恒定策略比，才公平
        cnm = metrics(evaluate_constant(ndf.w.mean(), idx, plan, sigs))
        m["归一化后同均值恒定%"] = cnm["总收益%"]
        m["归一化后alpha(pp)"] = round(nm["总收益%"] - cnm["总收益%"], 2)
        m["_归一化可达基准25.9%"] = bool(abs(ndf.w.mean() - base_mean) < 0.005)
        m["_加5pp死区后收益%"] = metrics(band_df)["总收益%"]
        m["_含P1升仓限速10pp后收益%"] = metrics(evaluate(
            s, idx, plan, sigs, max_up_step=0.10))["总收益%"]
        rows[s] = m
        navs[s] = (1 + df.ret).cumprod().set_axis(df.date).to_frame(s)

    cdf = evaluate_constant(base_mean, idx, plan, sigs)
    _clabel = f"恒定{round(base_mean * 100, 1)}%仓位"
    navs[_clabel] = ((1 + cdf.ret).cumprod()
                     .set_axis(cdf.date).to_frame(_clabel))
    fdf = evaluate_constant(1.0, idx, plan, sigs)
    navs["满仓买入持有"] = ((1 + fdf.ret).cumprod()
                          .set_axis(fdf.date).to_frame("满仓买入持有"))
    for s in ("ma20_0", "combo_ma20_s2s5_dd"):
        k = rows[s]["归一化缩放系数"]
        ndf = evaluate(s, idx, plan, sigs, w_scale=k)
        nm = "归一化_" + s
        navs[nm] = (1 + ndf.ret).cumprod().set_axis(ndf.date).to_frame(nm)
    res["策略对比"] = rows

    # ---- 参数网格（过拟合检验） ----
    grid, grid_alpha = {}, {}
    for n in (10, 20, 30, 60):
        for off in (0.0, 0.25, 0.5, 0.75):
            nm = f"ma{n}_{off}"
            gdf = evaluate(nm, idx, plan, sigs)
            gm = metrics(gdf)
            cm = metrics(evaluate_constant(gdf.w.mean(), idx, plan, sigs))
            grid[f"MA{n}_off{off}"] = gm["总收益%"]
            grid_alpha[f"MA{n}_off{off}"] = round(
                gm["总收益%"] - cm["总收益%"], 2)
    res["MA参数网格_收益%"] = grid
    res["MA参数网格_波段alpha(pp)"] = grid_alpha

    # ---- 首选策略的稳健性 ----
    picks = ["baseline", "ma20_0", "ma20_0.25", "state_s2s5", "dd_brake",
             "combo_ma20_s2s5", "combo_ma20_s2s5_dd"]
    sens = {}
    for s in picks:
        sens[s] = {
            "成本0bp": metrics(evaluate(s, idx, plan, sigs, fee_bp=0))["总收益%"],
            "成本5bp": metrics(evaluate(s, idx, plan, sigs, fee_bp=5))["总收益%"],
            "成本10bp": metrics(evaluate(s, idx, plan, sigs, fee_bp=10))["总收益%"],
            "现金1.5%+5bp": metrics(evaluate(
                s, idx, plan, sigs, fee_bp=5, cash_annual=0.015))["总收益%"],
            "2025全年": metrics(yslice(evaluate(s, idx, plan, sigs),
                                       "20250101", "20251231"))["总收益%"],
            "2026至今": metrics(yslice(evaluate(s, idx, plan, sigs),
                                        "20260101", "20261104"))["总收益%"],
        }
    res["稳健性_收益%"] = sens

    # ---- 分年：收益 + 回撤 + 真波段 alpha（各年用自己的同均值恒定对照） ----
    fy = {}
    for y, lo, hi in (("2025", "20250101", "20251231"),
                      ("2026", "20260101", "20261104")):
        fy[y] = {}
        for s in picks:
            df = yslice(evaluate(s, idx, plan, sigs), lo, hi)
            cm = metrics(yslice(evaluate_constant(
                df.w.mean(), idx, plan, sigs), lo, hi))
            m = metrics(df)
            fy[y][s] = {
                "收益%": m["总收益%"], "最大回撤%": m["最大回撤%"],
                "平均仓位%": m["平均仓位%"],
                "同均值恒定%": cm["总收益%"],
                "波段alpha(pp)": round(m["总收益%"] - cm["总收益%"], 2),
                "上行捕获": m["上行捕获"], "下行捕获": m["下行捕获"],
            }
        cfull = yslice(evaluate_constant(base_mean, idx, plan, sigs), lo, hi)
        fy[y]["_恒定全期均值仓位基准"] = metrics(cfull)["总收益%"]
    res["分年"] = fy

    # ---- 多指数 ----
    multi = {}
    for code, name in INDICES:
        ix = load_index(code)
        sg = build_signals(ix)
        multi[name] = {}
        for s in picks:
            multi[name][s] = metrics(evaluate(s, ix, plan, sg))["总收益%"]
    res["多指数_收益%"] = multi

    # ---- 分月（baseline vs 首选） ----
    pm = {}
    for s in ("baseline", "ma20_0", "combo_ma20_s2s5_dd"):
        df = evaluate(s, idx, plan, sigs)
        df["ym"] = df.date.str[:6]
        pm[s] = {ym: round(float((1 + g.ret).prod() - 1) * 100, 2)
                 for ym, g in df.groupby("ym")}
    res["分月_收益%"] = pm

    with open(os.path.join(SOLO_DIR, "_bt_swing_optimizer_metrics.json"),
              "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    navdf = pd.concat([navs[k] for k in navs], axis=1)
    navdf.to_csv(os.path.join(SOLO_DIR, "_bt_swing_optimizer_nav.csv"),
                 encoding="utf-8-sig", float_format="%.6f")
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
