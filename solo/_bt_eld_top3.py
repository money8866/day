# -*- coding: utf-8 -*-
"""ELD Top3 新旧逻辑对照回测 —— 用本地 SQLite 日线库算 T+5/T+10 前瞻收益"""
import os, glob, sys, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, r"D:\mystock\solo")
import pandas as pd
import numpy as np

REP = r"D:\mystock\report_daily"
files = sorted(glob.glob(os.path.join(REP, "eld_report_*.csv")))

# ---------- 前瞻收益计算 ----------
from stock_cache import get_daily_cache, get_daily_cache_range

def fwd_return(code, base_date, horizon_days):
    """取 base_date 后 horizon_days 个交易日的区间最高涨幅（以收盘价计）"""
    try:
        df = get_daily_cache(code, base_date, "20991231")
        if df is None or len(df) < 2:
            return None
        df = df[df["trade_date"] > base_date].copy()
        if df.empty:
            return None
        df = df.sort_values("trade_date").head(horizon_days)
        if df.empty:
            return None
        base_close = get_daily_cache(code, base_date, base_date)
        if base_close is None or base_close.empty:
            return None
        base_px = float(base_close.iloc[0]["close"])
        return (df["close"].max() / base_px - 1) if base_px > 0 else None
    except Exception:
        return None

# ---------- 选股逻辑 ----------
def pick_old(df):
    return df.sort_values("final_score_v2", ascending=False).head(3)

def pick_new(df):
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
    d = d[pd.to_numeric(d["buy_score"], errors="coerce").fillna(0) >= 60]
    if d.empty:
        return d
    d["_r"] = pd.to_numeric(d["final_score_v2"], errors="coerce").fillna(0) * 0.5 \
            + pd.to_numeric(d["buy_score"], errors="coerce").fillna(0) * 0.5
    return d.sort_values("_r", ascending=False).head(3)

# ---------- 逐报告回测 ----------
rows = []
for f in files:
    d8 = os.path.basename(f)[11:19]
    if not ("20260629" <= d8 <= "20260817"):
        continue
    df = pd.read_csv(f, encoding="utf-8-sig")
    if df.empty or "final_score_v2" not in df.columns:
        continue

    for tag, picker in [("旧(V2纯排序)", pick_old), ("新(Buy过滤+综合分)", pick_new)]:
        picked = picker(df)
        for _, r in picked.iterrows():
            f5 = fwd_return(r["ts_code"], d8, 5)
            f10 = fwd_return(r["ts_code"], d8, 10)
            if f5 is not None or f10 is not None:
                rows.append({
                    "date": d8, "mode": tag, "name": r["name"], "code": r["ts_code"],
                    "v2": float(r.get("final_score_v2", 0)),
                    "buy": float(r.get("buy_score", 0)),
                    "inst": str(r.get("institution_state", "-")),
                    "lvl": str(r.get("buy_score_level", "-")),
                    "t5": f5, "t10": f10,
                })

r = pd.DataFrame(rows)
if r.empty:
    print("无样本")
else:
    r.to_csv(r"D:\mystock\cache_daily\_eld_top3_bt.csv", index=False, encoding="utf-8-sig")
    print(f"总样本: {len(r)}")
    for m, g in r.groupby("mode"):
        print(f"\n== {m} ==  n={len(g)}")
        for c, label in [("t5", "T+5"), ("t10", "T+10")]:
            s = g[c].dropna()
            if len(s):
                mean_r = (s * 100).mean()
                med_r = (s * 100).median()
                win_r = (s > 0).mean() * 100
                best_r = (s * 100).max()
                worst_r = (s * 100).min()
                print(f"  {label}: 均值{mean_r:+.1f}%  中位{med_r:+.1f}%  胜率{win_r:.0f}%  最佳{best_r:+.1f}%  最差{worst_r:+.1f}%")

    print("\n== 每日对照（T+5均值）==")
    pv = r.pivot_table(index="date", columns="mode", values="t5", aggfunc="mean")
    if not pv.empty:
        print((pv * 100).round(1).to_string())

    print("\n== 每日对照（T+10均值）==")
    pv10 = r.pivot_table(index="date", columns="mode", values="t10", aggfunc="mean")
    if not pv10.empty:
        print((pv10 * 100).round(1).to_string())