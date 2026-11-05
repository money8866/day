# -*- coding: utf-8 -*-
"""2026 年「按 V10.7 目标仓位买指数」回测（无未来函数）。

口径
----
* 仓位来源：V10.7 全年回放（T 日盘后，仅用 <=T 的数据）的 target_position。
* 执行假设：T 日收盘价成交，承担 T -> T+1 的指数收益（lag=1）；
  另给 lag=2（T+1 收盘才成交，承担 T+1 -> T+2）的保守口径。
* 现金部分默认 0 收益；可选货币基金年化。
* 换手成本：|w_T - w_{T-1}| * fee（单边，bp），在收益日扣除。
* 所有净值从 1.000 起算，与「同期满仓买入持有」严格同区间比较。

运行：python _bt_position_index_pnl.py   （输出 JSON 指标 + 净值 CSV）
"""
import os
import json
import sqlite3

import numpy as np
import pandas as pd

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOLO_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, "cache_daily", "stock_data.db")
START, END = "20250102", "20261104"
TRADING_DAYS = 244
BENCH = "000300.SH"


def load_index(ts_code: str) -> pd.DataFrame:
    con = sqlite3.connect("file:" + DB.replace("\\", "/") + "?mode=ro", uri=True)
    try:
        df = pd.read_sql(
            "SELECT trade_date, close FROM index_daily_cache "
            "WHERE ts_code=? AND trade_date BETWEEN '20241201' AND ? ORDER BY trade_date",
            con, params=(ts_code, END))
    finally:
        con.close()
    df["trade_date"] = df["trade_date"].astype(str)
    return df.dropna().drop_duplicates("trade_date").reset_index(drop=True)


def load_weights() -> dict:
    def rd(name, col):
        d = pd.read_csv(os.path.join(SOLO_DIR, name), encoding="utf-8-sig")
        d["trade_date"] = d["trade_date"].astype(str)
        return dict(zip(d["trade_date"], d[col].astype(float) / 100.0))

    return {
        "V10.7.2目标仓位(P1/P2平滑后)": rd("_bt_opt_replay_on.csv", "target"),
        "V10.7.1目标仓位(平滑前)": rd("_bt_opt_replay_off.csv", "target"),
        # 已验证 V10.7.1 平滑前目标 == V10.7 原始 eff_target；
        # _bt_v107_replay.csv 仅含 2026，两年统一取全区间 off 序列。
        "V10.7原始有效目标": rd("_bt_opt_replay_off.csv", "target"),
        "V10.7.2硬上限": rd("_bt_opt_replay_on.csv", "hard_cap"),
        "原始目标(未平滑)": rd("_bt_opt_replay_on.csv", "raw_target"),
    }


def run(weights: dict, idx: pd.DataFrame, lag: int = 1,
        fee_bp: float = 0.0, cash_annual: float = 0.0) -> pd.Series:
    """返回按「收益实现日」索引的日收益率序列。"""
    dates = idx["trade_date"].tolist()
    close = idx["close"].to_numpy(float)
    pos = {d: i for i, d in enumerate(dates)}
    cash_d = (1.0 + cash_annual) ** (1.0 / TRADING_DAYS) - 1.0
    fee = fee_bp / 10000.0
    prev_w = 0.0
    out = {}
    for d in sorted(weights):
        if d not in pos:
            continue
        i = pos[d]
        # lag=1: 吃 close[i+1]/close[i]（T 日收盘成交）
        # lag=2: 吃 close[i+2]/close[i+1]（T+1 收盘才成交）
        j = i + lag - 1
        if j + 1 >= len(dates):
            continue
        w = float(weights[d])
        r_idx = close[j + 1] / close[j] - 1.0
        r = w * r_idx + (1.0 - w) * cash_d - abs(w - prev_w) * fee
        out[dates[j + 1]] = r
        prev_w = w
    return pd.Series(out, dtype=float).sort_index()


def metrics(r: pd.Series) -> dict:
    if len(r) == 0:
        return {}
    nav = (1.0 + r).cumprod()
    total = float(nav.iloc[-1] - 1.0)
    n = len(r)
    ann = float((1.0 + total) ** (TRADING_DAYS / n) - 1.0)
    mdd = float((nav / nav.cummax() - 1.0).min())
    vol = float(r.std(ddof=1) * np.sqrt(TRADING_DAYS))
    sharpe = float(r.mean() / r.std(ddof=1) * np.sqrt(TRADING_DAYS)) if r.std(ddof=1) > 0 else 0.0
    return {
        "总收益%": round(total * 100, 2),
        "年化%": round(ann * 100, 2),
        "最大回撤%": round(mdd * 100, 2),
        "年化波动%": round(vol * 100, 2),
        "Sharpe": round(sharpe, 2),
        "收益天数": n,
        "末值": round(float(nav.iloc[-1]), 4),
    }


def buy_hold(idx: pd.DataFrame, r_dates) -> pd.Series:
    dates = idx["trade_date"].tolist()
    close = idx["close"].to_numpy(float)
    pos = {d: i for i, d in enumerate(dates)}
    out = {}
    for d in r_dates:
        i = pos.get(d)
        if i and i > 0:
            out[d] = close[i] / close[i - 1] - 1.0
    return pd.Series(out, dtype=float).sort_index()


def main():
    idx = load_index(BENCH)
    weights = load_weights()
    avg_w = np.mean(list(weights["V10.7.2目标仓位(P1/P2平滑后)"].values()))
    weights[f"恒定{round(avg_w*100)}%仓位(同均值)"] = {
        d: avg_w for d in weights["V10.7.2目标仓位(P1/P2平滑后)"]}

    result = {"_口径": {
        "区间": f"{START}~{END}（2025 全年 + 2026 年至今，来自 2025-01~2026-11 连续回放）",
        "标的": BENCH + " 沪深300（收盘价）",
        "执行": "T 日盘后决策、T 日收盘成交、承担 T→T+1 收益（lag=1）",
        "现金": "默认 0 收益",
        "成本": "默认 0（另给 5bp / 10bp 单边换手敏感性）",
        "数据来源": "cache_daily/stock_data.db :: index_daily_cache；仓位来自 solo/_bt_*replay*.csv",
    }}

    # ---------- 主口径：lag=1, fee=0 ----------
    navs, rows = {}, {}
    for name, w in weights.items():
        r = run(w, idx, lag=1)
        navs[name] = (1 + r).cumprod()
        rows[name] = metrics(r)
    bh = buy_hold(idx, navs[next(iter(navs))].index)
    navs["满仓买入持有"] = (1 + bh).cumprod()
    rows["满仓买入持有"] = metrics(bh)
    main_key = "V10.7.2目标仓位(P1/P2平滑后)"
    for k, v in rows.items():
        v["超额(vs满仓)%"] = round(v["总收益%"] - rows["满仓买入持有"]["总收益%"], 2)
        if k in weights:
            v["平均仓位%"] = round(float(np.mean(list(weights[k].values()))) * 100, 1)
            wd = list(weights[k].items())
            wd.sort()
            v["累计换手(倍)"] = round(float(sum(abs(wd[i][1] - wd[i - 1][1])
                                            for i in range(1, len(wd)))), 2)
    result["主口径_策略对比"] = rows

    # ---------- 分年（关键样本外检验：2025 牛市 / 2026 熊市） ----------
    r_full = run(weights[main_key], idx, lag=1)
    # 分年按「决策日」归属：T 日决策产生的收益记在 T 所在年份
    _dates = idx["trade_date"].tolist()
    dec_of = {_dates[i + 1]: _dates[i] for i in range(len(_dates) - 1)}
    years = {}
    for y, lo, hi in (("2025", "20250101", "20251231"),
                      ("2026", "20260101", "20261104")):
        keep = [d for d in r_full.index if lo <= dec_of.get(d, "") <= hi]
        rr = r_full.loc[keep]
        bb = buy_hold(idx, rr.index)
        wy = [w for d, w in weights[main_key].items() if lo <= d <= hi]
        cw = float(np.mean(wy)) if wy else 0.0
        rc = cw * bb  # 主口径无成本、现金零收益
        m1, m2, m3 = metrics(rr), metrics(bb), metrics(rc)
        years[y] = {
            "按仓位": m1, "满仓": m2, "恒定同均值仓位": m3,
            "平均仓位%": round(cw * 100, 1),
            "收益天数": m1.get("收益天数"),
            "超额(vs满仓)pp": round(m1["总收益%"] - m2["总收益%"], 2),
            "波段alpha(pp)": round(m1["总收益%"] - m3["总收益%"], 2),
        }
    result["分年"] = years

    # ---------- 敏感性 ----------
    sens = {}
    for lag in (1, 2):
        for fee in (0.0, 5.0, 10.0):
            r = run(weights[main_key], idx, lag=lag, fee_bp=fee)
            bh_i = buy_hold(idx, r.index)
            m = metrics(r)
            m["超额(vs满仓)%"] = round(m["总收益%"] - metrics(bh_i)["总收益%"], 2)
            sens[f"lag{lag}_成本{int(fee)}bp"] = m
    r = run(weights[main_key], idx, lag=1, fee_bp=5.0, cash_annual=0.015)
    sens["lag1_成本5bp_现金年化1.5%"] = metrics(r)
    result["敏感性_V10.7.2"] = sens

    # ---------- 多指数 ----------
    multi = {}
    for code, name in (("000300.SH", "沪深300"), ("000001.SH", "上证指数"),
                       ("000905.SH", "中证500"), ("000852.SH", "中证1000"),
                       ("000985.CSI", "中证全指")):
        try:
            ix = load_index(code)
            r = run(weights[main_key], ix, lag=1)
            bh_i = buy_hold(ix, r.index)
            m = metrics(r)
            b = metrics(bh_i)
            multi[name] = {"按仓位": m["总收益%"], "满仓": b["总收益%"],
                           "超额%": round(m["总收益%"] - b["总收益%"], 2),
                           "按仓位回撤%": m["最大回撤%"], "满仓回撤%": b["最大回撤%"],
                           "收益天数": m["收益天数"]}
        except Exception as e:
            multi[name] = {"error": str(e)}
    result["多指数对照"] = multi

    # ---------- 分月 ----------
    r = run(weights[main_key], idx, lag=1)
    bh = buy_hold(idx, r.index)
    df = pd.DataFrame({"strat": r, "bh": bh}).dropna()
    df["ym"] = [x[:6] for x in df.index]
    monthly = {}
    for ym, g in df.groupby("ym"):
        monthly[ym] = {
            "按仓位%": round(float((1 + g.strat).prod() - 1) * 100, 2),
            "满仓%": round(float((1 + g.bh).prod() - 1) * 100, 2),
        }
    result["分月"] = monthly

    # ---------- 分状态（当日仓位决定下一日收益） ----------
    st = pd.read_csv(os.path.join(SOLO_DIR, "_bt_opt_replay_on.csv"),
                     encoding="utf-8-sig")
    st["trade_date"] = st.trade_date.astype(str)
    st["target"] = st.target.astype(float) / 100.0
    st["eff_state"] = st.eff_state.astype(str)
    dates = idx["trade_date"].tolist()
    nxt = {d: dates[i + 1] for i, d in enumerate(dates[:-1])}
    st["ret_date"] = st.trade_date.map(nxt)
    px = dict(zip(idx.trade_date, idx.close))
    prev = dict(zip(idx.trade_date, idx.close.shift(1)))
    st["idx_ret"] = [px[d] / prev[d] - 1 if d in px else np.nan
                     for d in st.ret_date]
    st["pnl"] = st.target * st.idx_ret
    state_stat = {}
    for s, g in st.groupby("eff_state"):
        state_stat[s] = {"天数": len(g), "平均仓位%": round(g.target.mean() * 100, 1),
                         "指数日均%": round(g.idx_ret.mean() * 100, 3),
                         "贡献合计%": round(g.pnl.sum() * 100, 2)}
    result["分状态"] = state_stat

    # ---------- 输出 ----------
    outdir = SOLO_DIR
    with open(os.path.join(outdir, "_bt_position_index_metrics.json"), "w",
              encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    nav_df = pd.DataFrame(navs)
    nav_df.to_csv(os.path.join(outdir, "_bt_position_index_nav.csv"),
                  encoding="utf-8-sig", float_format="%.6f")
    print(json.dumps({k: v for k, v in result.items() if k != "分月"},
                     ensure_ascii=False, indent=2))
    print("\n分月：", json.dumps(result["分月"], ensure_ascii=False))
    print("\n净值文件：", os.path.join(outdir, "_bt_position_index_nav.csv"))


if __name__ == "__main__":
    main()
