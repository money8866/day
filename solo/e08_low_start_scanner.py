# -*- coding: utf-8 -*-
"""E08-低位启动分支（S1）验证脚本

形态：E08 的 T0 跳空放量突破（复用 _stage1，口径零漂移）
      + 位置在低位（T0 收盘距 60 日低点 < 阈值，默认 20%）
      + 强势分支（T+1 收盘站上 T0 最高、缺口未补、量不缩）
      + 买点 = T+1~T+10 首次收盘触及 MA5/MA10 且收盘仍 >= T0 最高
      + 离场 = 收盘跌破缺口上沿（T-1 最高）即清仓，最长持有 40 个交易日

本脚本只做验证，不产出信号：
  ① 阈值敏感性 10/15/20/25/30/40%
  ② 分年稳定性
  ③ 单笔最大回撤分布
  ④ 同窗口中证1000 超额与 t 值

用法：
  python e08_low_start_scanner.py
  python e08_low_start_scanner.py --start 20240101 --end 20261106
"""
import argparse
import os
import sqlite3

import numpy as np
import pandas as pd

from e08_scanner import Bars, _stage1, MIN_TOTAL_MV

CACHE = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB = os.path.join(CACHE, "stock_data.db")
HERE = os.path.dirname(os.path.abspath(__file__))

WIN = 10          # 回踩观察窗（T+1 ~ T+10）
HOLD = 40         # 最长持有交易日
THRS = (10, 15, 20, 25, 30, 40)
IDX = "000852.SH"


def load(since, until):
    con = sqlite3.connect(DB, timeout=60)
    d = pd.read_sql_query(
        "SELECT ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol "
        "FROM daily_cache WHERE trade_date>=? AND trade_date<=?",
        con, params=(str(since), str(until)))
    a = pd.read_sql_query(
        "SELECT ts_code, trade_date, adj_factor FROM adj_factor_cache "
        "WHERE trade_date>=? AND trade_date<=?",
        con, params=(str(since), str(until)))
    d = d[~d["ts_code"].str.endswith(".BJ")]
    d = d.dropna(subset=["open", "high", "low", "close", "vol"])
    d = d.merge(a, on=["ts_code", "trade_date"], how="left")
    d = d.sort_values(["ts_code", "trade_date"], kind="mergesort")
    return con, d


def simulate(a, i, ma_win):
    """从 T0 索引 i 出发，返回成交明细 dict 或 None"""
    n = len(a)
    t0h = a.qh[i]
    gap_top = a.qh[i - 1]
    if i + 2 >= n:
        return None
    if not (a.qc[i + 1] >= t0h and a.qc[i + 1] >= gap_top and a.vol[i + 1] >= a.vol[i]):
        return None
    ma = pd.Series(a.qc).rolling(ma_win).mean().to_numpy()
    j = -1
    for k in range(i + 1, min(i + 1 + WIN, n)):
        if np.isfinite(ma[k]) and a.qc[k] <= ma[k] and a.qc[k] >= t0h:
            j = k
            break
    if j < 0:
        return None
    entry = a.qc[j]
    exit_i = min(j + HOLD, n - 1)
    for k in range(j + 1, min(j + 1 + HOLD, n)):
        if a.qc[k] < gap_top:
            exit_i = k
            break
    path = a.qc[j:exit_i + 1]
    return {
        "d_in": str(a.dates[j]), "d_out": str(a.dates[exit_i]),
        "ret": float(a.qc[exit_i] / entry - 1.0) * 100.0,
        "days": int(exit_i - j), "off": int(j - i),
        "max_gain": float(path.max() / entry - 1.0) * 100.0,
        "max_dd": float(path.min() / entry - 1.0) * 100.0,
        "t0_date": str(a.dates[i]), "gap": float((a.ql[i] - gap_top) / gap_top) * 100.0,
        "t1_gain": float(a.qc[i + 1] / a.qc[i] - 1.0) * 100.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20240101", help="信号起始日（含预热更早的数据）")
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    con = sqlite3.connect(DB, timeout=60)
    until = args.end or str(con.execute("SELECT MAX(trade_date) FROM daily_cache").fetchone()[0])
    con.close()
    since = str(int(args.start) - 10000)   # 往前推约一年做 60 日低点/120 根预热
    print("载入 %s ~ %s ..." % (since, until), flush=True)
    con, d = load(since, until)
    print("  %d 行 / %d 只" % (len(d), d["ts_code"].nunique()), flush=True)

    rows = []
    for code, g in d.groupby("ts_code", sort=False):
        if len(g) < 130:
            continue
        a = Bars(code, g)
        n = len(a)
        for i in range(61, n - 2):
            if str(a.dates[i]) < args.start:
                continue
            d0 = _stage1(a, i)
            if d0 is None:
                continue
            for ma_win in (5, 10):
                r = simulate(a, i, ma_win)
                if r is None:
                    continue
                rows.append({"ts_code": code, "ma": ma_win,
                             "rise_from_low": d0["rise_from_low"] * 100.0,
                             "t0_vol_mult": d0["t0_vol_mult"],
                             "platform_break": d0["platform_break"], **r})
    df = pd.DataFrame(rows)
    if df.empty:
        print("无样本")
        return
    print("  可入场成交 %d 笔" % len(df), flush=True)

    # 市值过滤（>80 亿，与 E08 宇宙一致）
    con = sqlite3.connect(DB, timeout=60)
    mv = {}
    for code, gg in df.groupby("ts_code"):
        ds = sorted(gg["t0_date"].unique())
        q = ("SELECT trade_date, total_mv FROM daily_basic_cache WHERE ts_code=? AND trade_date IN (%s)"
             % ",".join(["?"] * len(ds)))
        try:
            for dd, m in con.execute(q, [code] + ds):
                mv[(code, str(dd))] = m
        except Exception:
            pass
    idx = pd.read_sql_query(
        "SELECT trade_date, close FROM index_daily_cache WHERE ts_code=?", con, params=(IDX,))
    con.close()
    df["mv_yi"] = [pd.to_numeric(mv.get((c, t)), errors="coerce") / 10000.0
                   for c, t in zip(df["ts_code"], df["t0_date"])]
    df = df[df["mv_yi"] >= MIN_TOTAL_MV / 10000.0].copy()
    ic = dict(zip(idx["trade_date"].astype(str), pd.to_numeric(idx["close"], errors="coerce")))
    df["bench"] = [(ic.get(o) / ic.get(i) - 1.0) * 100.0 if ic.get(o) and ic.get(i) else np.nan
                   for i, o in zip(df["d_in"], df["d_out"])]
    df["ex"] = df["ret"] - df["bench"]
    df["year"] = df["d_in"].str[:4]
    df.to_csv(os.path.join(HERE, "e08_low_start_trades.csv"), index=False, encoding="utf-8-sig")

    # ① 阈值敏感性
    print("\n== ① 位置阈值敏感性（MA5 / 止损=缺口上沿） ==")
    hdr = "%-6s %4s %8s %8s %8s %6s %7s %7s"
    print(hdr % ("阈值", "n", "收益均值", "中位", "超额", "t值", "胜率", "回撤p50"))
    for thr in THRS:
        s = df[(df["ma"] == 5) & (df["rise_from_low"] < thr)]
        if len(s) < 3:
            continue
        t = s["ex"].mean() / (s["ex"].std() / len(s) ** 0.5)
        print(hdr % ("<%d%%" % thr, len(s), "%+7.2f%%" % s["ret"].mean(),
                     "%+7.2f%%" % s["ret"].median(), "%+7.2f%%" % s["ex"].mean(),
                     "%5.2f" % t, "%5.0f%%" % ((s["ret"] > 0).mean() * 100),
                     "%+6.1f%%" % s["max_dd"].median()))

    print("\n== ① 阈值敏感性（MA10） ==")
    print(hdr % ("阈值", "n", "收益均值", "中位", "超额", "t值", "胜率", "回撤p50"))
    for thr in THRS:
        s = df[(df["ma"] == 10) & (df["rise_from_low"] < thr)]
        if len(s) < 3:
            continue
        t = s["ex"].mean() / (s["ex"].std() / len(s) ** 0.5)
        print(hdr % ("<%d%%" % thr, len(s), "%+7.2f%%" % s["ret"].mean(),
                     "%+7.2f%%" % s["ret"].median(), "%+7.2f%%" % s["ex"].mean(),
                     "%5.2f" % t, "%5.0f%%" % ((s["ret"] > 0).mean() * 100),
                     "%+6.1f%%" % s["max_dd"].median()))

    base = df[(df["ma"] == 5) & (df["rise_from_low"] < 20)]

    # ② 分年
    print("\n== ② 分年稳定性（低位<20%，MA5） ==")
    g = base.groupby("year").agg(n=("ret", "size"), ret=("ret", "mean"), ex=("ex", "mean"),
                                 win=("ret", lambda s: (s > 0).mean() * 100),
                                 dd=("max_dd", "median"))
    print(g.round(2).to_string())

    # ③ 回撤
    print("\n== ③ 单笔回撤与盈亏分布（低位<20%%，MA5，n=%d） ==" % len(base))
    print("  平均收益 %+.2f%% / 中位 %+.2f%% / 胜率 %.0f%%"
          % (base["ret"].mean(), base["ret"].median(), (base["ret"] > 0).mean() * 100))
    print("  单笔最大浮盈 均值 %+.1f%% / 中位 %+.1f%%" % (base["max_gain"].mean(), base["max_gain"].median()))
    print("  单笔最大回撤 均值 %+.1f%% / 中位 %+.1f%% / p90 %+.1f%% / 最差 %+.1f%%"
          % (base["max_dd"].mean(), base["max_dd"].median(),
             base["max_dd"].quantile(0.1), base["max_dd"].min()))
    print("  盈亏比（均值口径）%.2f" % (base.loc[base["ret"] > 0, "ret"].mean()
                                 / abs(base.loc[base["ret"] <= 0, "ret"].mean())))
    print("  持有天数 均值 %.0f / 中位 %.0f" % (base["days"].mean(), base["days"].median()))

    # ④ 超额 t 与样本集中度
    t = base["ex"].mean() / (base["ex"].std() / len(base) ** 0.5)
    print("\n== ④ 同窗口中证1000 超额 ==")
    print("  全样本          n=%d 收益 %+.2f%% 超额 %+.2f%%  t=%.2f  胜率 %.0f%%"
          % (len(base), base["ret"].mean(), base["ex"].mean(), t, (base["ret"] > 0).mean() * 100))
    for lab, m in (("剔除T0在202409", ~base["t0_date"].str.startswith("202409")),
                   ("剔除入场在202409", ~base["d_in"].str.startswith("202409"))):
        s = base[m]
        if len(s) < 4:
            continue
        t2 = s["ex"].mean() / (s["ex"].std() / len(s) ** 0.5)
        print("  %-16s n=%d 收益 %+.2f%% 超额 %+.2f%%  t=%.2f  胜率 %.0f%%"
              % (lab, len(s), s["ret"].mean(), s["ex"].mean(), t2, (s["ret"] > 0).mean() * 100))

    print("\n== ④ 样本时间集中度（前 5 个月） ==")
    cnt = base["d_in"].str[:6].value_counts().head(5)
    for k, v in cnt.items():
        sub = base[base["d_in"].str[:6] == k]
        print("  %s  n=%3d（%.0f%%）  收益 %+.2f%%  超额 %+.2f%%"
              % (k, v, v / len(base) * 100, sub["ret"].mean(), sub["ex"].mean()))

    print("\n明细已导出 e08_low_start_trades.csv")


if __name__ == "__main__":
    main()
