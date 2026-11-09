# -*- coding: utf-8 -*-
"""
换维度普查：股东户数环比 × 股价位置 二维交叉。

口径（与 S1 阶段一致，不可改）：
- 基准 = 同期全市场个股未来收益的横截面均值（等权买入持有 → 「随便买一只」）
- 信号日 T = 公告日 ann_date 之后的第一个交易日（PIT，公告当日不用）
- 同时报 n / 均值 / 中位数 / 胜率；n<30 的格子标注样本不足
- 收益用后复权价（close × adj_factor），不处理停牌与涨跌停
"""
import os
import sqlite3

import numpy as np
import pandas as pd

CACHE = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB = os.path.join(CACHE, "stock_data.db")

con = sqlite3.connect(DB, timeout=120)

# ── 1. 股东户数：季度环比 ──
h = pd.read_sql_query(
    "SELECT ts_code, end_date, ann_date, holder_num FROM holder_num_cache", con)
h["ann_date"] = h["ann_date"].astype(str).str.replace("-", "", regex=False)
h.loc[~h["ann_date"].str.match(r"^\d{8}$"), "ann_date"] = None
h = h.dropna(subset=["ann_date", "holder_num"])
h = h.sort_values(["ts_code", "end_date"], kind="mergesort").reset_index(drop=True)

h["qidx"] = h["end_date"].str[:4].astype(int) * 4 + (h["end_date"].str[4:6].astype(int) - 1) // 3
g = h.groupby("ts_code", sort=False)
h["prev_num"] = g["holder_num"].shift(1)
h["prev_qidx"] = g["qidx"].shift(1)
h["chg"] = (h["holder_num"] / h["prev_num"] - 1.0) * 100.0
# 只保留紧邻上一个季度（间隔 1 个季度），避免跨季度过长
h = h[h["prev_qidx"].notna() & ((h["qidx"] - h["prev_qidx"]) == 1)]
h = h[h["chg"].abs() < 500]        # 剔除量级异常（数据错误）
h = h[h["ann_date"] >= "20230101"]  # 公告日须落在价格窗口内，避免信号用了 2 个月前的旧信息
print("股东户数环比样本: %d，股票 %d，季度 %d"
      % (len(h), h["ts_code"].nunique(), h["end_date"].nunique()))

# ── 2. 交易日历（adj_factor 只从 20230103 起，故研究窗口 2023 起） ──
td = pd.read_sql_query(
    "SELECT DISTINCT trade_date FROM index_daily_cache WHERE ts_code='000852.SH' "
    "AND trade_date>='20230103' ORDER BY trade_date", con)
TD = td["trade_date"].astype(str).to_numpy()
TD_POS = {d: i for i, d in enumerate(TD)}
print("交易日: %d （%s ~ %s）" % (len(TD), TD[0], TD[-1]))

# ── 3. 价格矩阵（后复权） ──
px = pd.read_sql_query(
    "SELECT d.ts_code, d.trade_date, d.close, a.adj_factor FROM daily_cache AS d "
    "LEFT JOIN adj_factor_cache AS a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date "
    "WHERE d.trade_date >= '20230103'", con)
con.close()
px["qc"] = pd.to_numeric(px["close"], errors="coerce") * pd.to_numeric(px["adj_factor"], errors="coerce")
codes, ci = np.unique(px["ts_code"].to_numpy(), return_inverse=True)
dates, di = np.unique(px["trade_date"].to_numpy(), return_inverse=True)
V = np.full((len(dates), len(codes)), np.nan, dtype=np.float32)
V[di, ci] = px["qc"].to_numpy(dtype=np.float32)
del px
print("价格矩阵: %d 日 × %d 股，非空 %.1f%%"
      % (V.shape[0], V.shape[1], 100.0 * np.isfinite(V).mean()))

# 日期索引对齐（V 的日期集合应等于 TD 的子集）
dpos = {d: i for i, d in enumerate(dates)}
assert all(d in dpos for d in TD), "日历与价格矩阵日期不一致"

DF = pd.DataFrame(V)
low60 = DF.rolling(60, min_periods=45).min().to_numpy()
high250 = DF.rolling(250, min_periods=180).max().to_numpy()
del DF

r20 = np.full(len(TD), np.nan)
r40 = np.full(len(TD), np.nan)
for i in range(len(TD) - 1):
    if i + 20 < len(TD):
        rr = V[i + 20] / V[i] - 1.0
        r20[i] = np.nanmean(rr) * 100.0
    if i + 40 < len(TD):
        rr = V[i + 40] / V[i] - 1.0
        r40[i] = np.nanmean(rr) * 100.0


def _fwd(i, hz):
    j = i + hz
    if j >= len(TD):
        return np.full(len(codes), np.nan, dtype=np.float32)
    return (V[j] / V[i] - 1.0) * 100.0


# ── 4. 构造事件表 ──
code_pos = {c: i for i, c in enumerate(codes)}
recs = []
skip_notrade = 0
for ts_code, end_date, ann_date, chg in zip(
        h["ts_code"], h["end_date"], h["ann_date"], h["chg"]):
    p = TD_POS.get(ann_date)
    i = (p + 1) if p is not None else int(np.searchsorted(TD, ann_date, side="right"))
    if i >= len(TD) - 40:
        continue
    j = dpos.get(TD[i])
    if j is None:
        skip_notrade += 1
        continue
    col = code_pos.get(ts_code)
    if col is None:
        skip_notrade += 1
        continue
    px_t = V[j, col]
    if not np.isfinite(px_t) or px_t <= 0:
        skip_notrade += 1
        continue
    lo = low60[j, col]
    hi = high250[j, col]
    recs.append((
        ts_code, end_date, ann_date, TD[i], i, chg,
        (px_t / lo - 1.0) * 100.0 if np.isfinite(lo) and lo > 0 else np.nan,
        (px_t / hi - 1.0) * 100.0 if np.isfinite(hi) and hi > 0 else np.nan,
        _fwd(j, 20)[col], _fwd(j, 40)[col],
        r20[i], r40[i],
    ))
print("构造事件 %d 条（跳过无价格 %d）" % (len(recs), skip_notrade))

ev = pd.DataFrame(recs, columns=[
    "ts_code", "end_date", "ann_date", "signal_date", "si", "chg",
    "rise_low60", "dd_high250", "ret20", "ret40", "bm20", "bm40"])
ev["ex20"] = ev["ret20"] - ev["bm20"]
ev["ex40"] = ev["ret40"] - ev["bm40"]
ev["year"] = ev["signal_date"].str[:4]

CHG_BINS = [-1e9, -15, -5, 5, 15, 1e9]
CHG_LAB = ["<-15%", "-15~-5", "-5~+5", "+5~+15", ">+15%"]
POS_BINS = [-1e9, 10, 20, 35, 60, 1e9]
POS_LAB = ["<10%", "10~20", "20~35", "35~60", ">60%"]
DD_BINS = [-1e9, -30, -15, -5, 0, 1e9]
DD_LAB = ["<-30%", "-30~-15", "-15~-5", "-5~0", ">0"]

ev["c"] = pd.cut(ev["chg"], CHG_BINS, labels=CHG_LAB)
ev["p"] = pd.cut(ev["rise_low60"], POS_BINS, labels=POS_LAB)
ev["d"] = pd.cut(ev["dd_high250"], DD_BINS, labels=DD_LAB)


def stat(s):
    return pd.Series({
        "n": len(s),
        "超额均": s["ex20"].mean(),
        "超额中位": s["ex20"].median(),
        "收益均": s["ret20"].mean(),
        "收益中位": s["ret20"].median(),
        "胜率": (s["ret20"] > 0).mean() * 100.0,
        "t值": s["ex20"].mean() / (s["ex20"].std() / np.sqrt(max(len(s), 1))),
    })


def table(df, dim, title, cols=None):
    print("\n" + "=" * 96)
    print("== %s ==" % title)
    rows = []
    for k, s in df.groupby(dim, observed=False):
        if len(s) == 0:
            continue
        r = stat(s)
        r["格子"] = k
        rows.append(r)
    out = pd.DataFrame(rows).set_index("格子")
    print(out.round(2).to_string())
    return out


print("\n样本区间: %s ~ %s；事件 %d 条，股票 %d 只"
      % (ev["signal_date"].min(), ev["signal_date"].max(), len(ev), ev["ts_code"].nunique()))
print("户数环比分布: 均值 %+.2f%% 中位 %+.2f%%  P10 %+.2f%%  P90 %+.2f%%"
      % (ev["chg"].mean(), ev["chg"].median(),
         ev["chg"].quantile(0.1), ev["chg"].quantile(0.9)))
print("距60日低点: 中位 %+.1f%%；距250日高点: 中位 %+.1f%%；有效 %d / %d"
      % (ev["rise_low60"].median(), ev["dd_high250"].median(),
         ev["dd_high250"].notna().sum(), len(ev)))

# ── 5. 单维度边际（先看有没有单调性） ──
table(ev, "c", "维度A：股东户数环比（全部样本）")
table(ev, "p", "维度B1：距60日低点涨幅（全部样本）")
table(ev, "d", "维度B2：距250日高点回撤（全部样本）")

# ── 6. 二维交叉：户数环比 × 距60日低点 ──
print("\n" + "=" * 96)
print("== 二维交叉：股东户数环比 × 距60日低点涨幅（ex20 超额均值） ==")
pv = ev.pivot_table(index="c", columns="p", values="ex20", aggfunc="mean", observed=False)
pn = ev.pivot_table(index="c", columns="p", values="ex20", aggfunc="size", observed=False)
print("超额均值(%):")
print(pv.round(2).to_string())
print("\n样本数:")
print(pn.fillna(0).astype(int).to_string())

print("\n== 同格子超额中位数(%) ==")
print(ev.pivot_table(index="c", columns="p", values="ex20",
                     aggfunc="median", observed=False).round(2).to_string())

print("\n== 同格子胜率(%) ==")
print(ev.pivot_table(index="c", columns="p", values="ret20",
                     aggfunc=lambda s: (s > 0).mean() * 100.0,
                     observed=False).round(1).to_string())

print("\n== 同格子样本数（n<30 不作结论） ==")
print(pn.fillna(0).astype(int).to_string())

# ── 7. 二维交叉：户数环比 × 距250日高点回撤 ──
print("\n" + "=" * 96)
print("== 二维交叉：股东户数环比 × 距250日高点回撤（ex20 超额均值） ==")
print(ev.pivot_table(index="c", columns="d", values="ex20", aggfunc="mean",
                     observed=False).round(2).to_string())
print("\n样本数:")
print(ev.pivot_table(index="c", columns="d", values="ex20", aggfunc="size",
                     observed=False).fillna(0).astype(int).to_string())
print("\n超额中位数(%):")
print(ev.pivot_table(index="c", columns="d", values="ex20", aggfunc="median",
                     observed=False).round(2).to_string())

# ── 8. 分年稳定性：户数环比边际 ──
print("\n" + "=" * 96)
print("== 分年稳定性：股东户数环比 → ex20 超额均值(%) / n ==")
yr = ev.pivot_table(index="c", columns="year", values="ex20", aggfunc="mean", observed=False)
yn = ev.pivot_table(index="c", columns="year", values="ex20", aggfunc="size", observed=False)
print(yr.round(2).to_string())
print("\n各年样本数:")
print(yn.fillna(0).astype(int).to_string())

# ── 9. 40 日口径复核（若 20 日不行，40 日是否也不行） ──
print("\n" + "=" * 96)
print("== 40日口径：户数环比边际 ==")
e40 = ev.dropna(subset=["ex40"])
print(e40.groupby("c", observed=False).apply(
    lambda s: pd.Series({
        "n": len(s), "超额均": s["ex40"].mean(), "超额中位": s["ex40"].median(),
        "收益中位": s["ret40"].median(),
        "胜率": (s["ret40"] > 0).mean() * 100.0})).round(2).to_string())

ev.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_dim_events.csv"), index=False)
print("\n事件明细已存 _dim_events.csv")
