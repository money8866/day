# -*- coding: utf-8 -*-
"""v1 止损/入场规则失效诊断 + 样本内事件量核实"""
import os
import numpy as np
import pandas as pd

RD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_daily")


def rd(name, **kw):
    return pd.read_csv(os.path.join(RD, name), **kw)


print("=" * 70)
print("A. v1 台账诊断（样本外 20260612~20260908）")
print("=" * 70)
led = rd("double_forward_ledger.csv", dtype={"code": str, "date": str, "entry_date": str})
led["tier_short"] = led["tier"].str.extract(r"(TIER1|TIER2)")
has_entry = led[led.entry_price > 0].copy()
has_entry["stop_dist"] = (has_entry.stop_price / has_entry.entry_price - 1) * 100
has_entry["entry_gap"] = (has_entry.entry_price / has_entry.close - 1) * 100

print("\n[A1] 止损线距入场价的距离（%）— 分层")
for t, g in has_entry.groupby("tier_short"):
    q = g.stop_dist.describe(percentiles=[.25, .5, .75])
    print(f"  {t}: n={len(g)} 中位={q['50%']:.1f}% p25={q['25%']:.1f}% p75={q['75%']:.1f}% "
          f"min={q['min']:.1f}% max={q['max']:.1f}%")
q = has_entry.stop_dist.describe(percentiles=[.25, .5, .75])
print(f"  全体: 中位={q['50%']:.1f}% p25={q['25%']:.1f}% p75={q['75%']:.1f}%")

print("\n[A2] 入场滑点（入场价/事件收盘-1，%）— 涨停顺延 vs 非涨停")
for lu, g in has_entry.groupby("is_limitup"):
    gg = g[g.entry_gap.notna() & (g.entry_gap != 0)] if lu else g
    print(f"  is_limitup={lu}: n={len(gg)} 中位gap={gg.entry_gap.median():.1f}% p75={gg.entry_gap.quantile(.75):.1f}%")

print("\n[A3] 已出场止损单：MFE 回吐统计")
exited = has_entry[has_entry.status.isin(["stop_hit", "stop_gap"])].copy()
exited["giveback"] = exited.mfe_e - exited.net_ret
print(f"  n={len(exited)} 持有天数中位={exited.hold_days.median():.0f} "
      f"p25={exited.hold_days.quantile(.25):.0f} p75={exited.hold_days.quantile(.75):.0f}")
print(f"  MFE中位={exited.mfe_e.median():.1f}% 净收益中位={exited.net_ret.median():.1f}% "
      f"回吐中位={exited.giveback.median():.1f}%")
for th in (5, 10, 15, 20):
    m = exited.mfe_e >= th
    if m.sum():
        print(f"  浮盈曾≥+{th}%: {m.sum()}笔({m.mean()*100:.0f}%) → 仍以止损离场, "
              f"这些单净收益中位={exited[m].net_ret.median():.1f}%")

print("\n[A4] 持有中(open) 当前状态")
op = has_entry[has_entry.status == "open"]
print(f"  n={len(op)} r_now中位={op.r_now.median():.1f}% mfe中位={op.mfe_e.median():.1f}% "
      f"mae中位={op.mae_e.median():.1f}%")
print(f"  r_now>0 占比={(op.r_now > 0).mean()*100:.0f}%  r_now<-10% 占比={(op.r_now < -10).mean()*100:.0f}%")

print("\n[A5] 事件日距离止损触发：止损线 vs 事件日最低价关系")
print(f"  stop==事件日最低(即-20%更深, 由事件日最低主导): "
      f"{(np.abs(has_entry.stop_dist - ((has_entry.groupby('code') and 0))) .size and 0)}")  # placeholder
# stop_price 与 entry*0.8 比较：若 stop_price < entry*0.8 则事件日最低更深（主动权在-20%）
dom20 = (has_entry.stop_price < has_entry.entry_price * 0.8 - 1e-9).mean()
print(f"  止损线由-20%主导占比: {dom20*100:.0f}% ｜由事件日最低主导占比: {(1-dom20)*100:.0f}%")

print()
print("=" * 70)
print("B. 样本内事件量核实（double_sli_pool_events.csv）")
print("=" * 70)
ev = rd("double_sli_pool_events.csv", dtype={"code": str, "date": str})
print(f"  全部事件: {len(ev)}  代码数: {ev.code.nunique()}  日期范围: {ev.date.min()}~{ev.date.max()}")
ins = ev[ev.date < "20260611"].copy()
mv = 10 ** (ins.log_mv - 4)
t1 = (mv <= 30) & (ins.turn_today >= 8) & (ins.rel_hi250 <= -0.25) & (ins.ma60_x < 0.10) & (ins.ma20_x < 0.15)
t2 = (mv <= 50) & (ins.turn_today >= 8) & (ins.turn_20m >= 3)
ins["tier"] = np.where(t1, "T1", np.where(t2, "T2", ""))
print(f"  样本内(<20260611): {len(ins)} ｜ TIER1={int(t1.sum())} ｜ TIER2(非T1)={int(((t2) & (~t1)).sum())} ｜ TIER合计={int(ins.tier.ne('').sum())}")
yr = ins.date.str[:4]
print("  TIER1 分年度:", dict(ins[t1].groupby(yr[t1]).size()))
print("  TIER2 分年度:", dict(ins[(t2) & (~t1)].groupby(yr[(t2) & (~t1)]).size()))
print(f"  atr20pct 可用: {ins.atr20pct.notna().mean()*100:.0f}%  中位={ins.atr20pct.median():.3f}")

print()
print("=" * 70)
print("C. double_backtest_tdx.csv（TDX 前视重算）")
print("=" * 70)
p = os.path.join(RD, "double_backtest_tdx.csv")
if os.path.exists(p):
    fw = rd(p, dtype={"code": str, "date": str})
    print(f"  行数={len(fw)} 列={list(fw.columns)}")
    print(f"  日期范围: {fw.date.min()}~{fw.date.max()}")
else:
    print("  不存在")
