# -*- coding: utf-8 -*-
"""E08 分析：跳空 T0 前一交易日的形态（长阳 / 中阳 / 下跌 等）

对 E08 阶段一识别出的全部 T0（跳空放量突破），回看 T0 前一日（i-1）的形态，
统计不同前一日形态下的后续表现，两套口径：
  A 追涨：T+1 开盘买入，T+5 / T+10 / T+20 收盘卖出（全样本）
  B 回踩：E08 实盘口径（T+1 站稳 T0 最高 + 量不缩 → T+1~T+10 首次回踩 MA5/MA10 买入，
          收盘跌破缺口上沿清仓，最长 40 日），附中证1000 超额
"""
import os
import sqlite3

import numpy as np
import pandas as pd

from e08_scanner import Bars, _stage1, MIN_TOTAL_MV
from e08_low_start_scanner import simulate

CACHE = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB = os.path.join(CACHE, "stock_data.db")
HERE = os.path.dirname(os.path.abspath(__file__))
SINCE, UNTIL = "20230101", "20261106"
SIG_START = "20240101"
IDX = "000852.SH"


def load():
    con = sqlite3.connect(DB, timeout=60)
    d = pd.read_sql_query(
        "SELECT ts_code, trade_date, open, high, low, close, pre_close, pct_chg, vol "
        "FROM daily_cache WHERE trade_date>=? AND trade_date<=?", con, params=(SINCE, UNTIL))
    a = pd.read_sql_query(
        "SELECT ts_code, trade_date, adj_factor FROM adj_factor_cache "
        "WHERE trade_date>=? AND trade_date<=?", con, params=(SINCE, UNTIL))
    d = d[~d["ts_code"].str.endswith(".BJ")]
    d = d.dropna(subset=["open", "high", "low", "close", "vol"])
    d = d.merge(a, on=["ts_code", "trade_date"], how="left")
    d = d.sort_values(["ts_code", "trade_date"], kind="mergesort")
    return con, d


def fwd_ret(qc, i, k, n):
    j = i + k
    return (qc[j] / qc[i] - 1.0) * 100.0 if j < n else np.nan


def main():
    con, d = load()
    print("日线 %d 行 / %d 只" % (len(d), d["ts_code"].nunique()), flush=True)

    rows = []
    for code, g in d.groupby("ts_code", sort=False):
        if len(g) < 130:
            continue
        b = Bars(code, g)
        n = len(b)
        for i in range(61, n - 2):
            if str(b.dates[i]) < SIG_START:
                continue
            s = _stage1(b, i)
            if s is None:
                continue
            p = i - 1
            rng = b.h[p] - b.l[p]
            up_sh = (b.h[p] - max(b.o[p], b.c[p])) / rng if rng > 0 else np.nan
            dn_sh = (min(b.o[p], b.c[p]) - b.l[p]) / rng if rng > 0 else np.nan
            body = (b.c[p] - b.o[p]) / b.o[p] * 100.0
            base_v = np.nanmean(b.vol[p - 4:p + 1])
            own_gap = (b.ql[p] - b.qh[p - 1]) / b.qh[p - 1] * 100.0 if p >= 1 else np.nan
            o1 = b.o[i + 1]
            fwd = {(k, tag): ((b.qc[i + k] / (o1 if tag == "open" else b.qc[i]) - 1.0) * 100.0
                              if i + k < n else np.nan)
                   for k in (5, 10, 20) for tag in ("open", "close")}
            sim5 = simulate(b, i, 5)
            sim10 = simulate(b, i, 10)
            rows.append({
                "ts_code": code, "date_t0": s["date_t0"], "year": s["date_t0"][:4],
                "gap": s["gap"] * 100.0, "t0_pct": s["t0_pct"], "t0_vol_mult": s["t0_vol_mult"],
                "rise_from_low": s["rise_from_low"] * 100.0,
                "platform_break": s["platform_break"],
                "prev_date": str(b.dates[p]), "prev_pct": b.pct[p], "prev_body": body,
                "prev_vol_mult": b.vol[p] / base_v if base_v > 0 else np.nan,
                "prev_up_sh": up_sh, "prev_dn_sh": dn_sh, "prev_own_gap": own_gap,
                "prev_close_pos": (b.c[p] - b.l[p]) / rng if rng > 0 else np.nan,
                **{"r%d_%s" % (k, tag): v for (k, tag), v in fwd.items()},
                "sim5_ret": sim5["ret"] if sim5 else np.nan,
                "sim5_mx": sim5["max_gain"] if sim5 else np.nan,
                "sim5_off": sim5["off"] if sim5 else np.nan,
                "sim5_in": sim5["d_in"] if sim5 else "",
                "sim5_out": sim5["d_out"] if sim5 else "",
                "sim10_ret": sim10["ret"] if sim10 else np.nan,
                "sim10_mx": sim10["max_gain"] if sim10 else np.nan,
            })
    df = pd.DataFrame(rows)
    print("阶段一 T0 样本 %d 个 / %d 只" % (len(df), df["ts_code"].nunique()), flush=True)

    # 市值 >= 80 亿
    mv = {}
    for code, gg in df.groupby("ts_code"):
        ds = sorted(gg["date_t0"].unique())
        q = ("SELECT trade_date, total_mv FROM daily_basic_cache WHERE ts_code=? AND trade_date IN (%s)"
             % ",".join(["?"] * len(ds)))
        try:
            for dd, m in con.execute(q, [code] + ds):
                mv[(code, str(dd))] = m
        except Exception:
            pass
    df["mv_yi"] = pd.to_numeric(
        pd.Series([mv.get((c, t), np.nan) for c, t in zip(df["ts_code"], df["date_t0"])]),
        errors="coerce") / 10000.0
    n0 = len(df)
    df = df[df["mv_yi"] >= MIN_TOTAL_MV / 10000.0].copy()
    print("市值>=80亿：%d 个（剔除 %d）" % (len(df), n0 - len(df)), flush=True)

    # 中证1000 基准
    idx = pd.read_sql_query(
        "SELECT trade_date, close FROM index_daily_cache WHERE ts_code=?", con, params=(IDX,))
    ic = dict(zip(idx["trade_date"].astype(str), pd.to_numeric(idx["close"], errors="coerce")))
    df["bench5"] = [(ic.get(o) / ic.get(i) - 1.0) * 100.0 if ic.get(o) and ic.get(i) else np.nan
                    for i, o in zip(df["sim5_in"], df["sim5_out"])]
    df["sim5_ex"] = df["sim5_ret"] - df["bench5"]

    # 前一日形态分类
    def cls(p):
        if p >= 7:
            return "1_长阳(>=7%)"
        if p >= 3:
            return "2_中阳(3~7%)"
        if p >= 0:
            return "3_小阳(0~3%)"
        if p > -3:
            return "4_小阴(-3~0%)"
        if p > -7:
            return "5_中阴(-7~-3%)"
        return "6_大阴(<=-7%)"
    df["prev_cls"] = df["prev_pct"].map(cls)
    df["prev_sh"] = np.where(df["prev_up_sh"] >= 0.5, "长上影(>=50%)",
                             np.where(df["prev_up_sh"] >= 0.25, "中上影(25~50%)", "短上影(<25%)"))
    df["gap_b"] = pd.cut(df["gap"], [1, 2, 4, 100], labels=["缺口1~2%", "缺口2~4%", "缺口>=4%"])
    df.to_csv(os.path.join(HERE, "e08_prev_t0.csv"), index=False, encoding="utf-8-sig")

    def table(g, by):
        out = g.groupby(by).agg(
            n=("ts_code", "size"),
            r5=("r5_open", "mean"), r10=("r10_open", "mean"), r20=("r20_open", "mean"),
            w5=("r5_open", lambda s: (s > 0).mean() * 100),
            sim_n=("sim5_ret", "count"),
            sim=("sim5_ret", "mean"), sim_med=("sim5_ret", "median"),
            sim_ex=("sim5_ex", "mean"),
            sim_w=("sim5_ret", lambda s: (s.dropna() > 0).mean() * 100),
            sim_mx=("sim5_mx", "mean"),
            sim10=("sim10_ret", "mean"),
            rise=("rise_from_low", "mean"),
        )
        return out

    hdr = ("%-16s %5s %7s %7s %7s %5s | %5s %7s %7s %7s %5s %6s %7s")
    print("\n== 按 T0 前一日形态分组 ==")
    print(hdr % ("前一日", "n", "追T5", "追T10", "追T20", "胜T5",
                 "回踩n", "回踩MA5", "中位", "超额", "胜率", "最大盈", "MA10"))
    t = table(df, "prev_cls")
    for k, r in t.iterrows():
        print("%-16s %5d %+6.2f%% %+6.2f%% %+6.2f%% %4.0f%% | %5d %+6.2f%% %+6.2f%% %+6.2f%% %4.0f%% %+5.1f%% %+6.2f%%"
              % (k, r["n"], r["r5"], r["r10"], r["r20"], r["w5"],
                 r["sim_n"], r["sim"], r["sim_med"], r["sim_ex"], r["sim_w"], r["sim_mx"], r["sim10"]))

    print("\n== 按前一日上影线占比 ==")
    print(hdr % ("上影", "n", "追T5", "追T10", "追T20", "胜T5",
                 "回踩n", "回踩MA5", "中位", "超额", "胜率", "最大盈", "MA10"))
    for k, r in table(df, "prev_sh").iterrows():
        print("%-16s %5d %+6.2f%% %+6.2f%% %+6.2f%% %4.0f%% | %5d %+6.2f%% %+6.2f%% %+6.2f%% %4.0f%% %+5.1f%% %+6.2f%%"
              % (k, r["n"], r["r5"], r["r10"], r["r20"], r["w5"],
                 r["sim_n"], r["sim"], r["sim_med"], r["sim_ex"], r["sim_w"], r["sim_mx"], r["sim10"]))

    print("\n== 交叉：前一日形态 × 缺口（回踩 MA5 均值 / 胜率 / n） ==")
    pv = df.pivot_table(index="prev_cls", columns="gap_b", values="sim5_ret",
                        aggfunc=["mean", lambda s: (s.dropna() > 0).mean() * 100, "count"], observed=False)
    print(pv.round(2).to_string())

    print("\n== 前一日阳线是否放量（长阳/中阳 再切分） ==")
    sub = df[df["prev_cls"].isin(["1_长阳(>=7%)", "2_中阳(3~7%)"])].copy()
    sub["vol_b"] = pd.cut(sub["prev_vol_mult"], [0, 1.0, 1.5, 2.0, 99],
                          labels=["缩量<1x", "1~1.5x", "1.5~2x", ">=2x"])
    g = sub.groupby(["prev_cls", "vol_b"], observed=False).agg(
        n=("ts_code", "size"), r5=("r5_open", "mean"),
        sim=("sim5_ret", "mean"), sim_ex=("sim5_ex", "mean"),
        w=("sim5_ret", lambda s: (s.dropna() > 0).mean() * 100), mx=("sim5_mx", "mean"))
    print(g.round(2).to_string())

    print("\n== 前一日收在当日最高附近（close_pos>=0.8）/ 掉在低位（<=0.3） ==")
    df["pos_b"] = pd.cut(df["prev_close_pos"], [0, 0.3, 0.8, 1.0],
                         labels=["低位收<=30%", "中间", "高位收>=80%"], include_lowest=True)
    g2 = df.groupby("pos_b", observed=False).agg(
        n=("ts_code", "size"), r5=("r5_open", "mean"),
        sim=("sim5_ret", "mean"), sim_ex=("sim5_ex", "mean"),
        w=("sim5_ret", lambda s: (s.dropna() > 0).mean() * 100))
    print(g2.round(2).to_string())

    print("\n== 分年稳定性：长阳 / 中阳 / 小阳 / 阴线（回踩 MA5 均值） ==")
    pv2 = df.pivot_table(index="year", columns="prev_cls", values="sim5_ret",
                         aggfunc=["mean", "count"], observed=False)
    print(pv2.round(2).to_string())

    print("\n明细已导出 e08_prev_t0.csv")


if __name__ == "__main__":
    main()
