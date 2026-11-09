# -*- coding: utf-8 -*-
"""E08-2 强势分支普查（临时脚本，统计完即删）

口径与 e08_scanner._stage1 完全一致（前复权几何 + 六项硬条件），
再筛出被 E08 主动排除的「强势不回头」分支：
    T+1 收盘 >= T0 最高价  且  T+1 收盘 >= 缺口上沿  且  Vol_T1 >= Vol_T0
统计：样本量年度分布、追涨(T+2 开盘)后 3/5/10/20/40 日收益、
      40 日内收盘是否破 T0 最高 / 破缺口上沿、MA20 破位情况。
"""
import os
import sqlite3

import numpy as np
import pandas as pd

CACHE = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB = os.path.join(CACHE, "stock_data.db")

SINCE, UNTIL = "20230101", "20261106"
SIG_START = "20240101"

GAP_MIN = 0.010
T0_MIN_PCT = 3.0
T0_VOL_MULT = 1.5
T0_VOL_MA = 5
RISE_FROM_LOW_MAX = 0.50
LOW_WINDOW = 60
MIN_BARS = 120
MIN_TOTAL_MV = 800000.0   # 万元 = 80 亿

FWD = (0, 3, 5, 10, 20, 40)
HOLD_WIN = 40             # 结构观察窗（T+2 起 40 个交易日）


def _limit(code):
    return 0.195 if str(code).startswith(("300", "301", "688", "689")) else 0.095


def main():
    con = sqlite3.connect(DB, timeout=60)
    print("[1/4] 读取日线 ...", flush=True)
    d = pd.read_sql_query(
        "SELECT ts_code, trade_date, open, high, low, close, pct_chg, vol FROM daily_cache "
        "WHERE trade_date>=? AND trade_date<=?", con, params=(SINCE, UNTIL))
    print("    日线 %d 行" % len(d), flush=True)
    a = pd.read_sql_query(
        "SELECT ts_code, trade_date, adj_factor FROM adj_factor_cache "
        "WHERE trade_date>=? AND trade_date<=?", con, params=(SINCE, UNTIL))
    print("    复权 %d 行" % len(a), flush=True)

    # 北交所不参与
    d = d[~d["ts_code"].str.endswith(".BJ")]
    d = d.dropna(subset=["open", "high", "low", "close", "vol"])
    d = d.merge(a, on=["ts_code", "trade_date"], how="left")
    d = d.sort_values(["ts_code", "trade_date"], kind="mergesort")
    print("    合并后 %d 行，股票 %d 只" % (len(d), d["ts_code"].nunique()), flush=True)

    rows = []
    n_t0_total = 0
    print("[2/4] 逐股识别 T0 与强势分支 ...", flush=True)
    for code, g in d.groupby("ts_code", sort=False):
        n = len(g)
        if n < MIN_BARS + 2:
            continue
        o = g["open"].to_numpy(dtype=float)
        h = g["high"].to_numpy(dtype=float)
        lo = g["low"].to_numpy(dtype=float)
        c = g["close"].to_numpy(dtype=float)
        pct = g["pct_chg"].to_numpy(dtype=float)
        vol = g["vol"].to_numpy(dtype=float)
        dates = g["trade_date"].to_numpy()
        adj = pd.to_numeric(g["adj_factor"], errors="coerce").ffill().bfill().to_numpy(dtype=float)
        if not np.isfinite(adj[-1]) or adj[-1] <= 0:
            continue
        q = adj / adj[-1]
        qo, qh, ql, qc = o * q, h * q, lo * q, c * q

        hp = np.empty(n)
        hp[0] = np.nan
        hp[1:] = qh[:-1]
        base_v = pd.Series(vol).shift(1).rolling(T0_VOL_MA).mean().to_numpy()
        low60 = pd.Series(ql).rolling(LOW_WINDOW, min_periods=LOW_WINDOW).min().to_numpy()
        ma20 = pd.Series(qc).rolling(20).mean().to_numpy()

        with np.errstate(invalid="ignore", divide="ignore"):
            gap = (ql - hp) / hp
            rise = qc / low60 - 1.0
        ok = ((gap >= GAP_MIN) & (c > o) & (pct >= T0_MIN_PCT)
              & (vol >= T0_VOL_MULT * base_v) & (rise < RISE_FROM_LOW_MAX))
        ok &= np.isfinite(gap) & np.isfinite(base_v) & np.isfinite(low60)
        idx = np.arange(n)
        ok &= idx >= max(MIN_BARS, LOW_WINDOW + 1)
        ok &= dates >= SIG_START

        cand = idx[ok]
        n_t0_total += len(cand)
        if len(cand) == 0:
            continue

        lim = _limit(code)
        for i in cand:
            i1 = i + 1
            i2 = i + 2
            if i2 >= n:
                continue
            # 强势分支：T+1 收盘站上 T0 最高，缺口未补，量能不缩
            if not (qc[i1] >= qh[i] and qc[i1] >= hp[i] and vol[i1] >= vol[i]):
                continue
            entry = qo[i2]
            buyable = entry < qc[i1] * (1.0 + lim)
            fwd = {}
            for k in FWD:
                j = i2 + k
                fwd[k] = (qc[j] / entry - 1.0) * 100.0 if j < n else np.nan
            # 以 T+2 收盘为买点的口径（更贴近 E08 的收盘确认）
            g2 = {}
            for k in FWD:
                j = i2 + k
                g2[k] = (qc[j] / qc[i2] - 1.0) * 100.0 if j < n else np.nan

            end = min(i2 + HOLD_WIN, n)
            w = qc[i2:end]
            wh = qh[i2:end]
            if len(w) < 5:
                continue
            brk_t0 = bool((w < qh[i]).any())
            brk_gap = bool((w < hp[i]).any())
            d_brk_t0 = int(np.argmax(w < qh[i])) if brk_t0 else -1
            ma_w = ma20[i2:end]
            below = np.isfinite(ma_w) & (w < ma_w)
            brk_ma20 = bool(below.any())
            d_brk_ma20 = int(np.argmax(below)) if brk_ma20 else -1
            max_gain = (wh.max() / entry - 1.0) * 100.0
            max_dd = (w.min() / entry - 1.0) * 100.0

            rows.append({
                "ts_code": code, "date_t0": str(dates[i]),
                "gap": float(gap[i]) * 100.0, "t0_pct": float(pct[i]),
                "t0_vol_mult": float(vol[i] / base_v[i]),
                "rise_from_low": float(rise[i]) * 100.0,
                "t1_gain": float(qc[i1] / qc[i] - 1.0) * 100.0,
                "t1_vol_mult": float(vol[i1] / vol[i]),
                "t1_over_t0": float(qc[i1] / qh[i] - 1.0) * 100.0,
                "t2_over_t0": float(qc[i2] / qh[i] - 1.0) * 100.0,
                "entry": float(entry),
                "buyable": bool(buyable),
                **{"r%d" % k: fwd[k] for k in FWD},
                **{"g%d" % k: g2[k] for k in FWD},
                "max_gain": float(max_gain), "max_dd": float(max_dd),
                "brk_t0": brk_t0, "d_brk_t0": d_brk_t0,
                "brk_gap": brk_gap,
                "brk_ma20": brk_ma20, "d_brk_ma20": d_brk_ma20,
                "bars": int(end - i2),
            })

    print("    T0 有效样本 %d 个" % n_t0_total, flush=True)
    if not rows:
        print("无强势分支样本")
        return
    df = pd.DataFrame(rows)

    # 市值过滤（仅对候选逐股回查，避免全表扫描）
    print("[3/4] 回查总市值 ...", flush=True)
    mv = {}
    for code, gg in df.groupby("ts_code"):
        ds = sorted(gg["date_t0"].unique())
        q = ("SELECT trade_date, total_mv FROM daily_basic_cache WHERE ts_code=? AND trade_date IN (%s)"
             % ",".join(["?"] * len(ds)))
        try:
            cur = con.execute(q, [code] + ds)
            for dd, m in cur.fetchall():
                mv[(code, str(dd))] = m
        except Exception:
            pass
    df["total_mv"] = [mv.get((c, dd), np.nan) for c, dd in zip(df["ts_code"], df["date_t0"])]
    df["mv_yi"] = pd.to_numeric(df["total_mv"], errors="coerce") / 10000.0
    df_full = df.copy()
    df = df[df["mv_yi"] >= MIN_TOTAL_MV / 10000.0].copy()
    df = df[df["buyable"]].copy()
    print("    市值>=80亿且T+2可成交：%d 个（过滤前 %d）" % (len(df), len(df_full)), flush=True)

    if df.empty:
        print("过滤后无样本")
        return

    df["year"] = df["date_t0"].str[:4]
    df["month"] = df["date_t0"].str[:6]
    df.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "e82_census.csv"),
              index=False, encoding="utf-8-sig")

    print("\n[4/4] 统计", flush=True)
    print("\n== 样本量（年度） ==")
    print(df.groupby("year").size().to_string())
    print("\n== 强势分支 / T0 总数 ==")
    print(df.groupby("year").size().to_string())
    print("\n== 月度分布 ==")
    print(df.groupby("month").size().to_string())

    print("\n== 追涨(T+2 开盘)后收益 %（均值 / 中位 / 胜率 / 样本） ==")
    for k in FWD:
        s = df["r%d" % k].dropna()
        if len(s) == 0:
            continue
        print("  T+%-2d  %7.2f%%  %7.2f%%  %5.1f%%  n=%d"
              % (k, s.mean(), s.median(), (s > 0).mean() * 100, len(s)))

    print("\n== 40 日内结构存续 ==")
    print("  收盘跌破 T0 最高 : %5.1f%%   (中位破位日 T+%d)"
          % (df["brk_t0"].mean() * 100,
             int(df.loc[df["brk_t0"], "d_brk_t0"].median()) if df["brk_t0"].any() else -1))
    print("  收盘跌破 缺口上沿: %5.1f%%" % (df["brk_gap"].mean() * 100))
    print("  收盘跌破 MA20    : %5.1f%%   (中位破位日 T+%d)"
          % (df["brk_ma20"].mean() * 100,
             int(df.loc[df["brk_ma20"], "d_brk_ma20"].median()) if df["brk_ma20"].any() else -1))
    print("\n== 区间极值（相对 T+2 开盘价 %） ==")
    print("  最大浮盈 均值 %.2f%% / 中位 %.2f%%" % (df["max_gain"].mean(), df["max_gain"].median()))
    print("  最大回撤 均值 %.2f%% / 中位 %.2f%%" % (df["max_dd"].mean(), df["max_dd"].median()))

    print("\n== 分组：是否跌破 T0 最高（40日内） ==")
    g = df.groupby("brk_t0").agg(n=("ts_code", "size"),
                                 r10=("r10", "mean"), r20=("r20", "mean"), r40=("r40", "mean"),
                                 mx=("max_gain", "mean"))
    print(g.round(2).to_string())

    print("\n== 分组：是否跌破 MA20 ==")
    g2 = df.groupby("brk_ma20").agg(n=("ts_code", "size"),
                                    r10=("r10", "mean"), r20=("r20", "mean"), r40=("r40", "mean"),
                                    mx=("max_gain", "mean"), dd=("max_dd", "mean"))
    print(g2.round(2).to_string())

    print("\n== 收紧口径对照（买点统一改为 T+2 收盘） ==")
    rules = [
        ("R0 原始", df["t1_gain"] > -999),
        ("R1 T+1涨>=5%", df["t1_gain"] >= 5.0),
        ("R2 R1+量>=1.5x", (df["t1_gain"] >= 5.0) & (df["t1_vol_mult"] >= 1.5)),
        ("R3 R2+守住2日", (df["t1_gain"] >= 5.0) & (df["t1_vol_mult"] >= 1.5) & (df["t2_over_t0"] >= 0)),
        ("R4 R3+缓冲2%", (df["t1_gain"] >= 5.0) & (df["t1_vol_mult"] >= 1.5)
         & (df["t2_over_t0"] >= 0) & (df["t1_over_t0"] >= 2.0)),
        ("R5 R3+缺口>=2%", (df["t1_gain"] >= 5.0) & (df["t1_vol_mult"] >= 1.5)
         & (df["t2_over_t0"] >= 0) & (df["gap"] >= 2.0)),
    ]
    hdr = "%-16s %5s %4s %4s %4s %5s | %7s %7s %7s %7s | %6s %6s"
    print(hdr % ("口径", "n", "24", "25", "26", "破T0%", "G5", "G10", "G20", "G40", "最大盈", "最大亏"))
    for name, m in rules:
        s = df[m]
        if s.empty:
            print("%-16s 无样本" % name)
            continue
        yr = s["year"].value_counts()
        cells = []
        for k in (5, 10, 20, 40):
            v = s["g%d" % k].dropna()
            cells.append("%+6.1f/%.0f%%" % (v.mean(), (v > 0).mean() * 100) if len(v) else "     -")
        print("%-16s %5d %4d %4d %4d %5.0f%% | %s | %6.1f %6.1f"
              % (name, len(s), yr.get("2024", 0), yr.get("2025", 0), yr.get("2026", 0),
                 s["brk_t0"].mean() * 100, " ".join(cells),
                 s["max_gain"].mean(), s["max_dd"].mean()))

    print("\n明细已导出 e82_census.csv")


if __name__ == "__main__":
    main()
