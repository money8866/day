# -*- coding: utf-8 -*-
"""S1 加成大盘环境过滤的验证（临时脚本）"""
import os
import sqlite3

import numpy as np
import pandas as pd

CACHE = os.environ.get("MSTOCK_CACHE", r"D:\mystock\cache_daily")
DB = os.path.join(CACHE, "stock_data.db")
HERE = os.path.dirname(os.path.abspath(__file__))

con = sqlite3.connect(DB, timeout=60)
idx = pd.read_sql_query(
    "SELECT trade_date, close FROM index_daily_cache WHERE ts_code='000852.SH' ORDER BY trade_date", con)
con.close()
idx["trade_date"] = idx["trade_date"].astype(str)
idx["close"] = pd.to_numeric(idx["close"], errors="coerce")
idx["ret20"] = (idx["close"] / idx["close"].shift(20) - 1.0) * 100.0
RMAP = dict(zip(idx["trade_date"], idx["ret20"]))

raw = pd.read_csv(os.path.join(HERE, "e08_low_start_trades.csv"),
                  dtype={"ts_code": str, "t0_date": str, "d_in": str, "d_out": str})
raw["idx20"] = [RMAP.get(d) for d in raw["t0_date"]]
raw["ym"] = raw["d_in"].str[:6]

df = raw[(raw["ma"] == 5) & (raw["rise_from_low"] < 20)].copy()

print("样本 %d，中证1000 20日涨幅可得 %d" % (len(df), df["idx20"].notna().sum()))

edges = [-1e9, -8, -5, -2, 0, 2, 5, 1e9]
labels = ["<-8%", "-8~-5", "-5~-2", "-2~0", "0~+2", "+2~+5", ">+5%"]
df["bucket"] = pd.cut(df["idx20"], bins=edges, labels=labels)
print("\n== 按 T0 日中证1000 20日涨幅分档（均值 vs 中位数） ==")
g = df.groupby("bucket", observed=False).agg(
    n=("ret", "size"), ret=("ret", "mean"), ret_med=("ret", "median"),
    ex=("ex", "mean"), ex_med=("ex", "median"),
    win=("ret", lambda s: (s > 0).mean() * 100),
    big=("ex", lambda s: (s > 30).sum()))
print(g.round(2).to_string())

print("\n== 叠加环境阈值 ==")
for thr in (5, 3, 2, 0, -3, -5):
    s = df[df["idx20"] < thr]
    if len(s) < 4:
        continue
    t = s["ex"].mean() / (s["ex"].std() / len(s) ** 0.5)
    print("  指数20日 < %+d%%   n=%3d  收益 %+6.2f%%  超额 %+6.2f%%  t=%5.2f  胜率 %3.0f%%  中位 %+5.2f%%"
          % (thr, len(s), s["ret"].mean(), s["ex"].mean(), t,
             (s["ret"] > 0).mean() * 100, s["ret"].median()))

print("\n== idx20>+5%% 桶的入场月份构成（n=%d） ==" % (df["idx20"] >= 5).sum())
print(df[df["idx20"] >= 5]["ym"].value_counts().head(6).to_string())
print("\n== 全样本入场月份 Top6 ==")
print(df["ym"].value_counts().head(6).to_string())

core = df[~df["ym"].isin(["202409", "202410"])]
print("\n== 已剔除入场 202409/202410（n=%d）后再叠加环境阈值 ==" % len(core))
print("  无环境过滤        n=%3d  收益 %+6.2f%%  超额 %+6.2f%%  中位 %+5.2f%%"
      % (len(core), core["ret"].mean(), core["ex"].mean(), core["ret"].median()))
for thr in (3, 2, 0):
    s = core[core["idx20"] < thr]
    if len(s) < 4:
        continue
    t = s["ex"].mean() / (s["ex"].std() / len(s) ** 0.5)
    print("  指数20日 < %+d%%   n=%3d  收益 %+6.2f%%  超额 %+6.2f%%  t=%5.2f  中位 %+5.2f%%"
          % (thr, len(s), s["ret"].mean(), s["ex"].mean(), t, s["ret"].median()))

# ── 核心体检：均值是否只由少数极端单撑起 ──
print("\n== 均值/中位数 与 极端单依赖度（ma × 低位阈值） ==")
print("  %-5s %-6s %4s %8s %8s %8s %8s %6s %6s" %
      ("均线", "低位", "n", "收益均", "收益中位", "超额均", "超额中位", "胜率", "ex>30"))
for ma in (5, 10):
    for thr in (15, 20, 25, 30):
        s = raw[(raw["ma"] == ma) & (raw["rise_from_low"] < thr)]
        if len(s) < 5:
            continue
        print("  MA%-4d <%-5d %4d %+8.2f %+8.2f %+8.2f %+8.2f %5.0f%% %6d"
              % (ma, thr, len(s), s["ret"].mean(), s["ret"].median(),
                 s["ex"].mean(), s["ex"].median(),
                 (s["ret"] > 0).mean() * 100, (s["ex"] > 30).sum()))

# ── 基准口径校验：改用「全市场等权」同期收益，排除中证1000 基准偏差 ──
con = sqlite3.connect(DB, timeout=60)
ew = pd.read_sql_query(
    "SELECT trade_date, AVG(pct_chg) AS r FROM daily_cache WHERE trade_date >= '20230101' "
    "GROUP BY trade_date ORDER BY trade_date", con)
con.close()
ew["trade_date"] = ew["trade_date"].astype(str)
ew["r"] = pd.to_numeric(ew["r"], errors="coerce") / 100.0
ew["nav"] = (1.0 + ew["r"].fillna(0.0)).cumprod()
NAV = dict(zip(ew["trade_date"], ew["nav"]))

def _ew_ret(d_in, d_out):
    a, b = NAV.get(d_in), NAV.get(d_out)
    return (b / a - 1.0) * 100.0 if (a and b) else np.nan

raw["ex_ew"] = [_ew_ret(a, b) for a, b in zip(raw["d_in"], raw["d_out"])]
raw["ex2"] = raw["ret"] - raw["ex_ew"]

print("\n== 换基准：中证1000 超额 vs 全市场等权超额 ==")
print("  %-5s %-6s %4s %9s %9s %9s %9s" %
      ("均线", "低位", "n", "ex(指数)均", "ex(等权)均", "ex(等权)中位", "胜率"))
for ma in (5, 10):
    for thr in (20, 25, 30):
        s = raw[(raw["ma"] == ma) & (raw["rise_from_low"] < thr)]
        t = s["ex2"].mean() / (s["ex2"].std() / len(s) ** 0.5)
        print("  MA%-4d <%-5d %4d %+9.2f %+9.2f %+9.2f %5.0f%%  t=%5.2f"
              % (ma, thr, len(s), s["ex"].mean(), s["ex2"].mean(), s["ex2"].median(),
                 (s["ret"] > 0).mean() * 100, t))

s = raw[(raw["ma"] == 5) & (raw["rise_from_low"] < 20)]
top = s.reindex(s["ex"].abs().sort_values(ascending=False).index).head(8)
print("\n== MA5 低位<20 的 8 笔最大 |超额| 明细（看是不是 outlier 独撑） ==")
print(top[["ts_code", "t0_date", "d_in", "d_out", "ret", "ex", "days", "rise_from_low"]]
      .round(2).to_string(index=False))

# ── 最终基准：同期全市场个股收益的横截面均值（等权买入并持有，「随便买一只」） ──
con = sqlite3.connect(DB, timeout=60)
px = pd.read_sql_query(
    "SELECT d.ts_code, d.trade_date, d.close, a.adj_factor FROM daily_cache AS d "
    "LEFT JOIN adj_factor_cache AS a ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date "
    "WHERE d.trade_date >= '20230101'", con)
con.close()
px["trade_date"] = px["trade_date"].astype(str)
px["qc"] = pd.to_numeric(px["close"], errors="coerce") * pd.to_numeric(px["adj_factor"], errors="coerce")
px["qc"] = px.groupby("ts_code")["qc"].transform(lambda s: s / s.ffill().bfill().iloc[-1] if s.notna().any() else s)
PIV = px.pivot_table(index="trade_date", columns="ts_code", values="qc")
print("\n== 最终基准：同期全市场个股收益横截面均值（等权买入持有） ==")
pairs = raw[["d_in", "d_out"]].drop_duplicates().values.tolist()
cache_ew = {}
for a, b in pairs:
    if a not in PIV.index or b not in PIV.index:
        cache_ew[(a, b)] = np.nan
        continue
    r = (PIV.loc[b] / PIV.loc[a] - 1.0) * 100.0
    cache_ew[(a, b)] = float(np.nanmean(r.to_numpy(dtype=float)))
raw["ex_bh"] = [raw["ret"].iloc[i] - cache_ew.get((a, b), np.nan)
                for i, (a, b) in enumerate(zip(raw["d_in"], raw["d_out"]))]
print("  %-5s %-6s %4s %9s %9s %9s %6s" % ("均线", "低位", "n", "ex(指数)", "ex(等权持有)均", "ex(等权持有)中位", "t值"))
for ma in (5, 10):
    for thr in (15, 20, 25, 30):
        s = raw[(raw["ma"] == ma) & (raw["rise_from_low"] < thr)]
        if len(s) < 5:
            continue
        t = s["ex_bh"].mean() / (s["ex_bh"].std() / len(s) ** 0.5)
        print("  MA%-4d <%-5d %4d %+9.2f %+9.2f %+9.2f %6.2f"
              % (ma, thr, len(s), s["ex"].mean(), s["ex_bh"].mean(), s["ex_bh"].median(), t))
