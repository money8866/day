# -*- coding: utf-8 -*-
"""ELD Top3 排序公式回测：8月数据，测试不同 V2/Buy 权重组合"""
import os, glob, sys, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, r"D:\mystock\solo")
import pandas as pd
import numpy as np

REP = r"D:\mystock\report_daily"
files = sorted(glob.glob(os.path.join(REP, "eld_report_*.csv")))

from stock_cache import get_daily_cache

def fwd_return(code, base_date, horizon_days):
    try:
        df = get_daily_cache(code, base_date, "20991231")
        if df is None or len(df) < 2:
            return None
        df = df[df["trade_date"] > base_date].sort_values("trade_date").head(horizon_days)
        if df.empty:
            return None
        base = get_daily_cache(code, base_date, base_date)
        if base is None or base.empty:
            return None
        bp = float(base.iloc[0]["close"])
        return (df["close"].max() / bp - 1) if bp > 0 else None
    except:
        return None

def filter_pool(df):
    d = df.copy()
    if "buy_score" not in d.columns:
        return d.iloc[0:0]
    if "buy_score_level" not in d.columns:
        d["buy_score_level"] = ""
    if "institution_state" not in d.columns:
        d["institution_state"] = ""
    d = d[~d["ts_code"].astype(str).str.endswith(".BJ")]
    d = d[d["institution_state"].astype(str) != "派发"]
    d = d[d["buy_score_level"].astype(str) != "禁止"]
    d["_buy"] = pd.to_numeric(d["buy_score"], errors="coerce").fillna(0)
    d = d[d["_buy"] >= 60]
    return d

# 测试权重组合
weights = [(0, 100), (20, 80), (30, 70), (40, 60), (50, 50), (60, 40), (70, 30), (80, 20), (100, 0)]
results = []

for f in files:
    d8 = os.path.basename(f)[11:19]
    if not ("20260801" <= d8 <= "20260820"):
        continue
    df = pd.read_csv(f, encoding="utf-8-sig")
    if df.empty or "final_score_v2" not in df.columns:
        continue
    pool = filter_pool(df)
    if pool.empty:
        continue
    pool = pool.copy()
    pool["_v2"] = pd.to_numeric(pool["final_score_v2"], errors="coerce").fillna(0)
    pool["_buy"] = pd.to_numeric(pool["buy_score"], errors="coerce").fillna(0)

    for w_v2, w_buy in weights:
        pool["_r"] = pool["_v2"] * (w_v2 / 100) + pool["_buy"] * (w_buy / 100)
        top3 = pool.sort_values("_r", ascending=False).head(3)
        for _, r in top3.iterrows():
            f5 = fwd_return(r["ts_code"], d8, 5)
            f10 = fwd_return(r["ts_code"], d8, 10)
            if f5 is not None or f10 is not None:
                results.append({"date": d8, "w_v2": w_v2, "w_buy": w_buy,
                                "code": r["ts_code"], "name": r["name"],
                                "v2": r["_v2"], "buy": r["_buy"],
                                "t5": f5, "t10": f10})

r = pd.DataFrame(results)
if r.empty:
    print("无样本")
else:
    print(f"8月样本: {len(r)} 条，覆盖日期: {r['date'].unique().tolist()}")
    print()
    print(f"{'V2权重':>6} {'Buy权重':>7} {'n':>4}  {'T+5均值':>8} {'T+5中位':>8} {'T+5胜率':>6} {'T+5最佳':>8} {'T+5最差':>8}  {'T+10均值':>8} {'T+10中位':>8} {'T+10胜率':>6} {'T+10最佳':>8} {'T+10最差':>8}")
    print("-" * 130)
    best_t5 = (0, None)
    best_t10 = (0, None)
    for w_v2, w_buy in weights:
        g = r[(r.w_v2 == w_v2) & (r.w_buy == w_buy)]
        if g.empty:
            continue
        t5 = g["t5"].dropna()
        t10 = g["t10"].dropna()
        def fmt(s):
            if len(s) == 0:
                return ("-" * 8, "-" * 8, "-" * 6, "-" * 8, "-" * 8)
            return (f"{(s*100).mean():+.1f}%", f"{(s*100).median():+.1f}%",
                    f"{(s>0).mean()*100:.0f}%", f"{(s*100).max():+.1f}%", f"{(s*100).min():+.1f}%")
        m5, md5, w5, b5, ws5 = fmt(t5)
        m10, md10, w10, b10, ws10 = fmt(t10)
        print(f"{w_v2:>6}% {w_buy:>7}% {len(g):>4}  {m5:>8} {md5:>8} {w5:>6} {b5:>8} {ws5:>8}  {m10:>8} {md10:>8} {w10:>6} {b10:>8} {ws10:>8}")
        if len(t5) and t5.median() > best_t5[0]:
            best_t5 = (t5.median(), w_v2, w_buy)
        if len(t10) and t10.median() > best_t10[0]:
            best_t10 = (t10.median(), w_v2, w_buy)

    print(f"\nT+5 中位最佳: V2={best_t5[1]}% Buy={best_t5[2]}%  ({best_t5[0]*100:+.1f}%)")
    print(f"T+10 中位最佳: V2={best_t10[1]}% Buy={best_t10[2]}%  ({best_t10[0]*100:+.1f}%)")

    # 逐日对比最佳三条
    print("\n== 逐日 T+5 均值对照（V2:Buy = 50:50 / 30:70 / 70:30）==")
    for wv, wb in [(50, 50), (30, 70), (70, 30)]:
        pv = r[(r.w_v2 == wv) & (r.w_buy == wb)].pivot_table(index="date", values="t5", aggfunc="mean")
        if not pv.empty:
            print(f"\n  V2:{wv}/Buy:{wb}")
            print((pv * 100).round(1).to_string())