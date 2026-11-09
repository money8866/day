# -*- coding: utf-8 -*-
"""E08-2 回踩买点初测（临时脚本）

输入 e82_census.csv（普查出的强势分支样本），模拟「等回踩再买」而不是追涨：
  观察窗 T+1 ~ T+10：首次 收盘 <= MA5/MA10 且 收盘仍 >= T0 最高价（结构未破）
  离场三套对照：A 收盘< T0最高 | B 收盘< 缺口上沿 | C 收盘< MA20
  最长持有 40 个交易日
"""
import os
import sqlite3

import numpy as np
import pandas as pd

CACHE = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB = os.path.join(CACHE, "stock_data.db")
HERE = os.path.dirname(os.path.abspath(__file__))

WIN = 10
HOLD = 40


def load_code(con, code):
    d = pd.read_sql_query(
        "SELECT trade_date, open, high, low, close, vol FROM daily_cache "
        "WHERE ts_code=? ORDER BY trade_date", con, params=(code,))
    a = pd.read_sql_query(
        "SELECT trade_date, adj_factor FROM adj_factor_cache WHERE ts_code=? ORDER BY trade_date",
        con, params=(code,))
    if d.empty or a.empty:
        return None
    d = d.merge(a, on="trade_date", how="left")
    adj = pd.to_numeric(d["adj_factor"], errors="coerce").ffill().bfill().to_numpy(float)
    if not np.isfinite(adj[-1]) or adj[-1] <= 0:
        return None
    q = adj / adj[-1]
    return {
        "dates": d["trade_date"].to_numpy(),
        "qh": d["high"].to_numpy(float) * q,
        "qc": d["close"].to_numpy(float) * q,
        "n": len(d),
    }


def simulate(px, i, ma_win, stop):
    qc, qh = px["qc"], px["qh"]
    n = px["n"]
    ma = pd.Series(qc).rolling(ma_win).mean().to_numpy()
    ma20 = pd.Series(qc).rolling(20).mean().to_numpy()
    t0h, gap_top = qh[i], qh[i - 1]
    # 观察窗内首次回踩
    j = -1
    for k in range(i + 1, min(i + 1 + WIN, n)):
        if np.isfinite(ma[k]) and qc[k] <= ma[k] and qc[k] >= t0h:
            j = k
            break
    if j < 0:
        return None
    entry = qc[j]
    line = {"A": np.full(n, t0h), "B": np.full(n, gap_top)}.get(stop, ma20)
    stop_i = -1
    for k in range(j + 1, min(j + 1 + HOLD, n)):
        if np.isfinite(line[k]) and qc[k] < line[k]:
            stop_i = k
            break
    exit_i = stop_i if stop_i > 0 else min(j + HOLD, n - 1)
    ret = (qc[exit_i] / entry - 1.0) * 100.0
    path = qc[j:exit_i + 1]
    return {
        "ret": ret, "days": int(exit_i - j), "held": bool(stop_i > 0),
        "off": int(j - i),
        "d_in": str(px["dates"][j]), "d_out": str(px["dates"][exit_i]),
        "max_gain": (path.max() / entry - 1.0) * 100.0,
        "max_dd": (path.min() / entry - 1.0) * 100.0,
    }


def main():
    df = pd.read_csv(os.path.join(HERE, "e82_census.csv"), dtype={"ts_code": str, "date_t0": str})
    print("样本 %d 个，股票 %d 只" % (len(df), df["ts_code"].nunique()), flush=True)
    con = sqlite3.connect(DB, timeout=60)
    cache = {}
    res = []
    for _, r in df.iterrows():
        code = r["ts_code"]
        if code not in cache:
            cache[code] = load_code(con, code)
        px = cache[code]
        if px is None:
            continue
        pos = np.where(px["dates"] == r["date_t0"])[0]
        if len(pos) == 0:
            continue
        i = int(pos[0])
        if i < 61 or i + 2 >= px["n"]:
            continue
        qh = px["qh"]
        pb = bool(qh[i - 1] >= np.max(qh[i - 20:i]) * 0.97)   # 平台突破（与 _stage1 同口径）
        for ma_win in (5, 10):
            for stop in ("A", "B", "C"):
                s = simulate(px, i, ma_win, stop)
                if s is None:
                    continue
                res.append({"ts_code": code, "date_t0": r["date_t0"],
                            "year": r["date_t0"][:4], "ma": ma_win, "stop": stop,
                            "pb": pb, "gap": r["gap"], "mv": r["mv_yi"],
                            "rise": r["rise_from_low"], "t1g": r["t1_gain"], **s})

    out = pd.DataFrame(res)
    if out.empty:
        print("无回踩样本")
        return
    # 同窗口基准：中证1000（超额 = 个股收益 - 指数同期收益）
    idx = pd.read_sql_query(
        "SELECT trade_date, close FROM index_daily_cache WHERE ts_code='000852.SH'", con)
    ic = dict(zip(idx["trade_date"].astype(str), pd.to_numeric(idx["close"], errors="coerce")))
    b = [(ic.get(o) / ic.get(i) - 1.0) * 100.0 if ic.get(o) and ic.get(i) else np.nan
         for i, o in zip(out["d_in"], out["d_out"])]
    out["bench"] = b
    out["ex"] = out["ret"] - out["bench"]
    out.to_csv(os.path.join(HERE, "e82_pullback.csv"), index=False, encoding="utf-8-sig")

    n_all = len(df)
    print("\n== 回踩可入场率 ==")
    for ma_win in (5, 10):
        s = out[(out["ma"] == ma_win) & (out["stop"] == "B")]
        print("  MA%-2d : %.1f%% （%d/%d）" % (ma_win, len(s) / n_all * 100, len(s), n_all))

    filters = [
        ("Q0 全部", pd.Series(True, index=out.index)),
        ("Q1 平台突破", out["pb"].fillna(False)),
        ("Q2 缺口>=2%", out["gap"] >= 2.0),
        ("Q3 Q1+Q2", out["pb"].fillna(False) & (out["gap"] >= 2.0)),
        ("Q4 Q3+低位<30%", out["pb"].fillna(False) & (out["gap"] >= 2.0) & (out["rise"] < 30)),
        ("Q5 Q3+市值>=150亿", out["pb"].fillna(False) & (out["gap"] >= 2.0) & (out["mv"] >= 150)),
    ]
    hdr = "%-18s %4s %8s %8s %8s %6s %7s %6s %5s"
    for ma_win, stop in ((5, "B"), (10, "B"), (10, "A")):
        base = out[(out["ma"] == ma_win) & (out["stop"] == stop)]
        print("\n== MA%d + 止损%s ==" % (ma_win, stop))
        print(hdr % ("筛选", "n", "收益均值", "中位", "超额", "胜率", "最大盈", "持有日", "回踩日"))
        for name, m in filters:
            s = base[m.reindex(base.index).fillna(False)]
            if s.empty:
                print("%-18s 无样本" % name)
                continue
            print(hdr % (name, len(s), "%+7.2f%%" % s["ret"].mean(), "%+7.2f%%" % s["ret"].median(),
                         "%+7.2f%%" % s["ex"].mean(),
                         "%5.0f%%" % ((s["ret"] > 0).mean() * 100),
                         "%+6.1f%%" % s["max_gain"].mean(), "%6.1f" % s["days"].mean(),
                         "%5.1f" % s["off"].mean()))

    print("\n== 最干净子集 Q3 / Q4 剔除 202409 ==")
    for name, m in filters[3:5]:
        for ma_win, stop in ((5, "B"), (10, "B")):
            s = out[(out["ma"] == ma_win) & (out["stop"] == stop)]
            s = s[m.reindex(s.index).fillna(False)]
            s = s[~s["date_t0"].str.startswith("202409")]
            if s.empty:
                continue
            print("  %-18s MA%d-%s n=%3d 收益 %+6.2f%% 中位 %+6.2f%% 超额 %+6.2f%% 胜率 %4.0f%% 最大盈 %+5.1f%%"
                  % (name, ma_win, stop, len(s), s["ret"].mean(), s["ret"].median(),
                     s["ex"].mean(), (s["ret"] > 0).mean() * 100, s["max_gain"].mean()))

    print("\n明细已导出 e82_pullback.csv")


if __name__ == "__main__":
    main()
