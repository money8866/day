# -*- coding: utf-8 -*-
"""「W7-放量洗盘」(SHAKEOUT_HOLD) 历史标定与条件区分度检验（临时脚本，用完删除）。

目的：20261026 全市场一次出 18 只当日买点，需回答
  ① 裸口径到底有没有 alpha（T+1/3/5/10，基准=全市场等权）；
  ② 八条硬条件里哪些有区分度、哪些是「全员满足」的无效条件；
  ③ 同日批量触发（普涨日）是否系统性劣于个股独立触发；
  ④ 哪些维度（量比区间/长阳日距今/回踩深度/生命周期/MA20 缓冲/长阳日质量）能减量而不损 alpha。

口径严格复刻 w7_second_wave_engine.shakeout_hold（含「取最早满足的长阳日」）。
"""
import argparse
import os
import sqlite3
from collections import defaultdict

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

CACHE = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB = os.path.join(CACHE, "stock_data.db")
BASIC = os.path.join(CACHE, "stock_basic.csv")
FWD = (1, 3, 5, 10)

# —— 与 w7_second_wave_engine.py 同步的口径常量 ——
DAY_PCT = 7.0          # 起涨放量长阳：涨幅下限
DAY_VOLR = 1.3         # 起涨放量长阳：量 ≥1.3×前20日均量
CLOSE_POS = 67.0       # 收盘位于当日振幅上 1/3
MAX_AGE = 10           # 长阳日距当当日 ≤10 根
PRE20_RISE = 0.30      # 长阳日前 20 根累计涨幅 ≤30%
VOL_LO = 0.60          # 回踩放量下限（相对长阳日量）
VOL_HI = 2.50          # 回踩放量上限
MAX_EXT = 1.10         # 当日收盘 ≤ 长阳收盘×1.10
LOW_TOL = 0.98         # 回踩期最低 ≥ 中位×0.98
MA10_K = 1.00
MA20_K = 1.00
BASE_DATE = "20240801"  # 生命周期基准日
W = 10                 # 滑窗长度 = MAX_AGE


def rolling_mean_incl(a, w):
    """含当日的滚动均值（满窗，前 w-1 位为 NaN）"""
    a = np.asarray(a, dtype=float)
    n = len(a)
    out = np.full(n, np.nan)
    if n < w:
        return out
    cs = np.concatenate(([0.0], np.cumsum(a)))
    out[w - 1:] = (cs[w:] - cs[:-w]) / w
    return out


def rolling_mean_excl(a, w):
    """不含当日的滚动均值：out[i] = mean(a[i-w:i])（i>=w 才有值）"""
    a = np.asarray(a, dtype=float)
    n = len(a)
    out = np.zeros(n)
    cs = np.concatenate(([0.0], np.cumsum(a)))
    idx = np.arange(w, n)
    out[idx] = (cs[idx] - cs[idx - w]) / w
    return out


def suffix_agg(arr, w):
    """返回 (suffix_max, suffix_min)，形状 (n-w+1, w)：
    行 k 对应 end index i = k+w-1；列 p 的值为 arr[i-w+1+p : i+1] 的 max/min。
    """
    arr = np.asarray(arr, dtype=float)
    if len(arr) < w:
        return np.zeros((0, w)), np.zeros((0, w))
    win = sliding_window_view(arr, w)
    rev = win[:, ::-1]
    smax = np.maximum.accumulate(rev, axis=1)[:, ::-1]
    smin = np.minimum.accumulate(rev, axis=1)[:, ::-1]
    return smax, smin


def load_all(conn, since):
    q = ("SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close, d.pct_chg, d.vol, "
         "       a.adj_factor "
         "  FROM daily_cache AS d "
         "  LEFT JOIN adj_factor_cache AS a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date "
         f" WHERE d.trade_date >= '{since}' ORDER BY d.ts_code, d.trade_date")
    df = pd.read_sql_query(q, conn)
    df = df.dropna(subset=["close", "high", "low", "vol"])
    for c in ("open", "high", "low", "close", "pct_chg", "vol", "adj_factor"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def prep(g):
    """单票预处理：量能/均线/生命周期基准价/滑窗后缀聚合

    口径对齐引擎：
      · 结构比较（半分位/收盘/回踩）用**原始价**（引擎 df.close 为原始价）；
      · MA10/MA20 用引擎同源的前复权口径，但按 as-of 行锚定（adj_i）计算，
        即 MA_i = mean(close×adj, 窗口) / adj_i，避免用未来因子造成前视。
      · 前瞻收益用复权价（close×adj）之比，规避除权跳空。
    """
    c = g.close.to_numpy(float)
    h = g.high.to_numpy(float)
    l = g.low.to_numpy(float)
    o = g.open.to_numpy(float)
    v = g.vol.to_numpy(float)
    pc = g.pct_chg.fillna(0.0).to_numpy(float)
    n = len(c)

    vol20 = rolling_mean_excl(v, 20)          # 长阳日量比基准：前20日均量（不含当日）
    vol20_incl = rolling_mean_incl(v, 20)     # 当日量比（引擎口径：含当日）
    adj = g.adj_factor.ffill().to_numpy(float)
    if not np.isfinite(adj).any():
        adj = np.ones(n)
    adj = np.where(np.isfinite(adj) & (adj > 0), adj, np.nan)
    adj = pd.Series(adj).ffill().bfill().to_numpy(float)
    padj = c * adj                          # 复权价（用于前瞻收益）
    ma10 = rolling_mean_incl(padj, 10) / adj  # as-of 行锚定的前复权 MA
    ma20 = rolling_mean_incl(padj, 20) / adj

    # 生命周期基准：20240801 起的最低价（prefix min）
    dates = g.trade_date.astype(str).to_numpy()
    low_q = np.where(np.isfinite(l), l, np.inf)
    mask = (dates >= BASE_DATE)
    low_post = np.where(mask, low_q, np.inf)
    base_low = np.minimum.accumulate(low_post)
    base_low = np.where(np.isfinite(base_low), base_low, np.nan)

    cmax, _ = suffix_agg(c, W)
    cmin, _ = suffix_agg(c, W)
    _, lmin = suffix_agg(l, W)

    return dict(c=c, h=h, l=l, o=o, v=v, pc=pc, vol20=vol20, vol20_incl=vol20_incl,
                ma10=ma10, ma20=ma20, base_low=base_low, cmax=cmax, cmin=cmin, lmin=lmin,
                dates=dates, adj=adj, padj=padj)


def scan(f):
    """复刻 shakeout_hold：返回命中点列表 [(end_index, j_index, p_star)]"""
    c, h, l, o, v, pc = f["c"], f["h"], f["l"], f["o"], f["v"], f["pc"]
    vol20, ma10, ma20 = f["vol20"], f["ma10"], f["ma20"]
    cmax, cmin, lmin = f["cmax"], f["cmin"], f["lmin"]
    n = len(c)
    if n < W + 22:
        return []
    nrow = n - W + 1
    k = np.arange(nrow)[:, None]        # 行索引
    p = np.arange(W)[None, :]           # 列索引：d = W - p
    J = k + p - 1                       # 长阳日 j 的行索引（列 p）
    valid = J >= 20

    b_close = c[J]
    b_open = o[J]
    b_high = h[J]
    b_low = l[J]
    b_vol = v[J]
    b_pc = pc[J]
    b_vol20 = vol20[J]

    mid = (b_high + b_low) / 2.0
    ok = valid & (b_close > b_open) & (b_pc >= DAY_PCT)
    ok &= b_vol20 > 0
    ok &= np.nan_to_num(b_vol, nan=0.0) >= b_vol20 * DAY_VOLR
    span = b_high - b_low
    cpos = np.where(span > 0, (b_close - b_low) / np.where(span > 0, span, 1.0) * 100.0, 50.0)
    ok &= cpos >= CLOSE_POS
    c20 = np.where(J >= 20, c[np.clip(J - 20, 0, n - 1)], 0.0)
    pre20 = np.where(c20 > 0, b_close / np.where(c20 > 0, c20, 1.0) - 1.0, 0.0)
    ok &= pre20 <= PRE20_RISE
    ok &= mid > 0

    # 回踩段 j+1..end（列 p 的后缀聚合：位置 p..W-1）
    seg_cmax = cmax
    seg_cmin = cmin
    seg_lmin = lmin
    ok &= seg_cmax <= b_high                      # ① 未创长阳新高
    ok &= c[np.arange(nrow)[:, None] + W - 1] <= b_close * MAX_EXT   # ② 未拉开
    ok &= seg_cmin >= mid                        # ③ 收盘不破中位
    ok &= seg_lmin >= mid * LOW_TOL              # ③ 插针容忍
    vr = v[np.arange(nrow)[:, None] + W - 1] / np.where(b_vol > 0, b_vol, 1.0)
    ok &= (vr > VOL_LO) & (vr <= VOL_HI)         # ④ 放量洗盘
    last_i = np.arange(nrow)[:, None] + W - 1
    ok &= ma10[last_i] > 0
    ok &= ma20[last_i] > 0
    ok &= c[last_i] >= ma10[last_i] * MA10_K     # ⑤ 趋势未破
    ok &= c[last_i] >= ma20[last_i] * MA20_K
    ok &= seg_cmin <= b_close * 0.995            # ⑥ 确有回踩

    any_hit = ok.any(axis=1)
    if not any_hit.any():
        return []
    pstar = np.where(ok, p, 10 ** 6).min(axis=1)  # 最小的 p = 最小的 j = 最早长阳日
    out = []
    for kk in np.where(any_hit)[0]:
        ps = int(pstar[kk])
        i = int(kk + W - 1)
        j = int(i - (W - ps))
        out.append((i, j, ps))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20230601")
    ap.add_argument("--from", dest="frm", default="20240101")
    ap.add_argument("--to", dest="to", default="20261026")
    ap.add_argument("--dump", default="")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    basic = pd.read_csv(BASIC, dtype={"ts_code": str, "list_date": str})
    name_map = dict(zip(basic.ts_code, basic.name))
    ind_map = dict(zip(basic.ts_code, basic.industry)) if "industry" in basic.columns else {}
    df = load_all(conn, args.since)
    conn.close()
    df = df[df.trade_date.astype(str) <= args.to]
    # 引擎样本池口径：沪深 A（剔除北交所 8/43/83/87/92 前缀）
    df = df[df.ts_code.str.endswith((".SZ", ".SH"), na=False)]
    df = df[~df.ts_code.str.startswith(("8", "43", "83", "87", "92"), na=False)]
    print(f"载入 {len(df)} 行 / {df.ts_code.nunique()} 只", flush=True)

    stocks = {}
    for code, g in df.groupby("ts_code", sort=False):
        stocks[code] = prep(g.reset_index(drop=True))
    print(f"预处理完成 {len(stocks)} 只", flush=True)

    # —— 全市场等权基准（每日 forward 收益均值，复权价口径）——
    base = {k: defaultdict(list) for k in FWD}
    for code, f in stocks.items():
        pc_ = f["padj"]
        dts = f["dates"]
        for i in range(len(pc_) - max(FWD)):
            for k in FWD:
                if i + k < len(pc_) and pc_[i] > 0:
                    base[k][dts[i]].append(pc_[i + k] / pc_[i] - 1.0)
    base_m = {k: {d: float(np.mean(v)) for d, v in base[k].items()} for k in FWD}
    print("基准完成", flush=True)

    rows = []
    for code, f in stocks.items():
        for (i, j, ps) in scan(f):
            d = f["dates"][i]
            if d < args.frm or d > args.to:
                continue
            c, v, pc = f["c"], f["v"], f["pc"]
            padj = f["padj"]
            b_close = f["c"][j]
            vr = v[i] / v[j] if v[j] > 0 else np.nan
            seg_lo_c = f["cmin"][i - W + 1][ps]
            rec = dict(
                code=code, date=d, b_date=f["dates"][j], age=i - j,
                close=c[i], vr=vr, vr20=v[i] / f["vol20_incl"][i] if f["vol20_incl"][i] > 0 else np.nan,
                day_pc=pc[i], b_pc=pc[j],
                b_volr=v[j] / f["vol20"][j] if f["vol20"][j] > 0 else np.nan,
                b_pre20=f["c"][j] / f["c"][j - 20] - 1 if j >= 20 and f["c"][j - 20] > 0 else np.nan,
                ret_from_b=c[i] / b_close - 1,
                dd_from_b=seg_lo_c / b_close - 1,
                ma20_buf=c[i] / f["ma20"][i] - 1 if f["ma20"][i] > 0 and np.isfinite(f["ma20"][i]) else np.nan,
                life_ext=c[i] / f["base_low"][i] - 1 if np.isfinite(f["base_low"][i]) and f["base_low"][i] > 0 else np.nan,
            )
            for k in FWD:
                rec[f"ret{k}"] = (padj[i + k] / padj[i] - 1.0) if (i + k < len(padj) and padj[i] > 0) else np.nan
            rows.append(rec)
    sig = pd.DataFrame(rows)
    if sig.empty:
        print("无命中")
        return
    # 全市场超额
    for k in FWD:
        sig[f"ex{k}"] = sig[f"ret{k}"] - sig["date"].map(base_m[k])

    # 同日命中数（批量触发程度）
    cnt = sig.groupby("date").size().to_dict()
    sig["batch"] = sig["date"].map(cnt)
    # 当日全市场等权涨跌（普涨日判定）
    mkt_ret = {d: float(np.mean(base[1][d])) for d in base[1]}
    sig["mkt_ret"] = sig["date"].map(mkt_ret)
    sig["b_mkt_ret"] = sig["b_date"].map(mkt_ret)
    sig["name"] = sig["code"].map(lambda c: name_map.get(c, c))
    sig["industry"] = sig["code"].map(lambda c: ind_map.get(c, ""))
    sig = sig.sort_values(["date", "code"]).reset_index(drop=True)

    print(f"\n命中总数 {len(sig)}，覆盖交易日 {sig.date.nunique()}，"
          f"日均 {len(sig)/max(1,sig.date.nunique()):.1f} 只")
    if args.dump:
        sig.to_csv(args.dump, index=False, encoding="utf-8-sig")
        print(f"明细已写 {args.dump}")

    def stat(d, k, label=""):
        if len(d) == 0:
            return
        e = d[f"ex{k}"].dropna()
        r = d[f"ret{k}"].dropna()
        if len(e) == 0:
            return
        print(f"  {label:<22}n={len(e):>6}  均值{r.mean()*100:>7.2f}%  超额{e.mean()*100:>7.2f}%  "
              f"胜率{(e > 0).mean()*100:>5.1f}%  中位{e.median()*100:>7.2f}%")

    print("\n【全样本】")
    for k in FWD:
        stat(sig, k, f"T+{k}")

    print("\n【分年】T+10")
    for y, d in sig.groupby(sig.date.str[:4]):
        stat(d, 10, y)

    print("\n【分年】T+3")
    for y, d in sig.groupby(sig.date.str[:4]):
        stat(d, 3, y)

    print("\n【同日批量度分档】")
    for lo, hi in [(1, 1), (2, 3), (4, 6), (7, 10), (11, 20), (21, 10 ** 6)]:
        d = sig[(sig.batch >= lo) & (sig.batch <= hi)]
        stat(d, 3, f"batch {lo}~{hi}")
        stat(d, 10, f"batch {lo}~{hi}")

    print("\n【起涨日是否为普涨日】")
    for lo, hi in [(-9, 0.005), (0.005, 0.02), (0.02, 10)]:
        d = sig[(sig.b_mkt_ret >= lo) & (sig.b_mkt_ret < hi)]
        stat(d, 3, f"b_mkt {lo}~{hi}")
        stat(d, 10, f"b_mkt {lo}~{hi}")

    print("\n【回踩量比 vr（当日量/长阳日量）】")
    for lo, hi in [(0.6, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 2.5)]:
        d = sig[(sig.vr > lo) & (sig.vr <= hi)]
        stat(d, 3, f"vr {lo}~{hi}")
        stat(d, 10, f"vr {lo}~{hi}")

    print("\n【长阳日距今天数 age】")
    for lo, hi in [(1, 2), (3, 4), (5, 6), (7, 8), (9, 10)]:
        d = sig[(sig.age >= lo) & (sig.age <= hi)]
        stat(d, 3, f"age {lo}~{hi}")
        stat(d, 10, f"age {lo}~{hi}")

    print("\n【回踩深度 dd_from_b（回踩期最低收盘 vs 长阳收盘）】")
    for lo, hi in [(-1, -0.10), (-0.10, -0.07), (-0.07, -0.05), (-0.05, -0.03), (-0.03, 0)]:
        d = sig[(sig.dd_from_b > lo) & (sig.dd_from_b <= hi)]
        stat(d, 3, f"dd {lo}~{hi}")
        stat(d, 10, f"dd {lo}~{hi}")

    print("\n【当日距长阳收盘 ret_from_b】")
    for lo, hi in [(-1, -0.10), (-0.10, -0.06), (-0.06, -0.03), (-0.03, 0.10)]:
        d = sig[(sig.ret_from_b > lo) & (sig.ret_from_b <= hi)]
        stat(d, 3, f"ret {lo}~{hi}")
        stat(d, 10, f"ret {lo}~{hi}")

    print("\n【生命周期段 life_ext】")
    for lo, hi in [(0, 0.3), (0.3, 0.8), (0.8, 1.5), (1.5, 3.0), (3.0, 99)]:
        d = sig[(sig.life_ext >= lo) & (sig.life_ext < hi)]
        stat(d, 3, f"life {lo}~{hi}")
        stat(d, 10, f"life {lo}~{hi}")

    print("\n【MA20 缓冲】")
    for lo, hi in [(-1, 0.0), (0.0, 0.02), (0.02, 0.05), (0.05, 0.10), (0.10, 99)]:
        d = sig[(sig.ma20_buf > lo) & (sig.ma20_buf <= hi)]
        stat(d, 3, f"ma20 {lo}~{hi}")
        stat(d, 10, f"ma20 {lo}~{hi}")

    print("\n【长阳日质量 b_volr / b_pc】")
    for lo, hi in [(1.3, 1.8), (1.8, 2.5), (2.5, 4.0), (4.0, 99)]:
        d = sig[(sig.b_volr > lo) & (sig.b_volr <= hi)]
        stat(d, 3, f"b_volr {lo}~{hi}")
        stat(d, 10, f"b_volr {lo}~{hi}")
    for lo, hi in [(7, 9), (9, 11), (11, 99)]:
        d = sig[(sig.b_pc > lo) & (sig.b_pc <= hi)]
        stat(d, 3, f"b_pc {lo}~{hi}")
        stat(d, 10, f"b_pc {lo}~{hi}")

    print("\n【当日涨跌 day_pc】")
    for lo, hi in [(-99, -4), (-4, -1), (-1, 0), (0, 3), (3, 99)]:
        d = sig[(sig.day_pc > lo) & (sig.day_pc <= hi)]
        stat(d, 3, f"day {lo}~{hi}")
        stat(d, 10, f"day {lo}~{hi}")

    # 20261026 明细
    d26 = sig[sig.date == "20261026"]
    if len(d26):
        print(f"\n【20261026 命中 {len(d26)} 只】")
        cols = ["code", "name", "industry", "b_date", "age", "close", "vr", "vr20", "day_pc",
                "b_pc", "b_volr", "ret_from_b", "dd_from_b", "ma20_buf", "life_ext",
                "mkt_ret", "batch", "ret3", "ex3", "ret10", "ex10"]
        print(d26[cols].to_string(index=False,
                                  float_format=lambda x: f"{x:.3f}"))


if __name__ == "__main__":
    main()
