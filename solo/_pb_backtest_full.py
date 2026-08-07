# -*- coding: utf-8 -*-
"""回踩买点形态多日回测 V1.0
方法:对全量名单每只股票取一次日线(20260101~20260806),在内存中对多个 as-of 交易日
逐日截断 → 复用 pullback_buy.analyze_shape 判定形态 → 统计次日(T+1)涨幅命中率。
仅统计"回踩中/回踩完成"两个可操作阶段(最优买点窗口)。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "multi_factor_picker"))
import pandas as pd
import numpy as np
from data_fetcher import DataFetcher
from multi_factor_picker.main import load_config, get_token
from pullback_buy import analyze_shape

BASE = os.path.dirname(os.path.abspath(__file__))
df = pd.read_csv(os.path.join(BASE, "report_daily", "double_score_full.csv"), encoding="utf-8-sig")

# as-of 交易日(需有次日行情;数据到 20260806,故最晚 20260805)
ASOF = ["20260724", "20260727", "20260728", "20260729", "20260730",
        "20260731", "20260803", "20260804", "20260805"]
fetcher = DataFetcher(get_token(load_config()), load_config())

records = []
t0 = __import__("time").time()
for i, (_, r) in enumerate(df.iterrows()):
    code = r.get("code") or r.get("ts_code")
    s = str(code).split(".")[0].zfill(6)
    ts = f"{s}.SH" if s[0] in "69" else f"{s}.SZ"
    try:
        daily = fetcher.get_daily_by_code(ts, start_date="20260101", end_date="20260806")
    except Exception:
        continue
    if daily is None or len(daily) < 30:
        continue
    d = daily.sort_values("trade_date").reset_index(drop=True)
    dates = d["trade_date"].astype(str).tolist()
    if dates[-1] != "20260806":
        continue
    for ad in ASOF:
        if ad not in dates:
            continue
        idx = dates.index(ad)
        if idx + 1 >= len(dates):
            continue
        sub = d.iloc[: idx + 1].copy()
        shape = analyze_shape(sub)
        if not shape or shape["stage"] not in ("回踩中", "回踩完成"):
            continue
        nxt = float(d.iloc[idx + 1]["pct_chg"])
        records.append({
            "code": ts, "name": r.get("name", ""), "asof": ad,
            "stage": shape["stage"], "score": shape["pullback_score"],
            "first_yang_date": shape.get("first_yang_date", ""),
            "t1_pct": round(nxt, 2),
        })
    if (i + 1) % 100 == 0:
        print(f"  [{i+1}/{len(df)}] 样本 {len(records)} | {__import__('time').time()-t0:.0f}s")

res = pd.DataFrame(records)
if len(res) == 0:
    print("无样本")
    sys.exit(0)

print(f"\n总样本: {len(res)} | as-of 日期 {sorted(res['asof'].unique())}")
for st in ["回踩中", "回踩完成"]:
    sub = res[res["stage"] == st]
    print(f"\n=== {st} {len(sub)} 样本 ===")
    for label, thr in [("全部", 0), (">=60分", 60), (">=70分", 70)]:
        s = sub[sub["score"] >= thr]
        if len(s) == 0:
            continue
        print(f"  {label}: n={len(s)} | 次日均值 {s['t1_pct'].mean():+.2f}% 中位 {s['t1_pct'].median():+.2f}% | "
              f"涨>3% {((s['t1_pct']>3).mean()*100):.0f}% 涨>5% {((s['t1_pct']>5).mean()*100):.0f}% "
              f"涨停 {((s['t1_pct']>=9.5).mean()*100):.0f}% | 上涨率 {((s['t1_pct']>0).mean()*100):.0f}%")

# 按日期汇总(高分>=60)
print("\n=== 按 as-of 日期(回踩中+回踩完成,score>=60) ===")
hi = res[res["score"] >= 60]
for ad in sorted(res["asof"].unique()):
    s = hi[hi["asof"] == ad]
    if len(s) == 0:
        continue
    print(f"  {ad}: n={len(s)} | 次日均值 {s['t1_pct'].mean():+.2f}% 涨>3% {((s['t1_pct']>3).mean()*100):.0f}%")

out = os.path.join(BASE, "report_daily", "pullback_backtest_multiday.csv")
res.to_csv(out, index=False, encoding="utf-8-sig")
print(f"\n→ {out}")
