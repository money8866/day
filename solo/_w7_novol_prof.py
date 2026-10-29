# -*- coding: utf-8 -*-
"""W7 配对回测性能剖析：定位 (股票, 交易日) 回放的主要耗时分布。

用途：决定全市场配对回测（5570 只 × 57 日 × 2 口径）该用什么口径跑。
"""
import cProfile
import io
import os
import pstats
import sys
import time

import numpy as np
import pandas as pd

SOLO = os.path.dirname(os.path.abspath(__file__))
if SOLO not in sys.path:
    sys.path.insert(0, SOLO)


def load(p, n):
    import importlib.machinery
    l = importlib.machinery.SourceFileLoader(n, p)
    sp = importlib.util.spec_from_loader(n, l)
    m = importlib.util.module_from_spec(sp)
    sys.modules[n] = m
    l.exec_module(m)
    return m


ev = load(os.path.join(SOLO, "_w7_novol_eval.py"), "evprof")
m = ev.load_engine()
r = m.CacheReader()
r.load_fina()

START, END = "20260901", "20260918"
dates = [str(x[0]) for x in r.conn.execute(
    "SELECT DISTINCT trade_date FROM index_daily_cache WHERE ts_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
    (m.MARKET_INDEX_CODE, START, END)).fetchall()]
print("dates", len(dates))

t = time.time()
ctx = ev.build_ctx(r, m, dates, END)
print("build_ctx %.1fs" % (time.time() - t))

codes = sorted(ctx[dates[-1]]["uni"])[:8]
frames = ev._load_chunk(m, r, codes, m.DATA_START, dates[-1])
ev._MOD = m
ev._READER = r
ev._CTX = ctx
ev._DATES = dates

# ---- 分段计时 ----
def stage_times():
    mod, reader = m, r
    t_dict = {}
    for code in codes:
        df_full = frames.get(code)
        if df_full is None:
            continue
        df_full = df_full[df_full.trade_date.astype(str) >= mod.DATA_START].reset_index(drop=True)
        t0 = time.time()
        flags = {}
        for i in range(mod.MIN_BARS, len(df_full) - 1):
            ok, ep = mod.extreme_event(df_full, i)
            if ok:
                flags[i] = (True, ep)
        t_dict["flags_budget"] = t_dict.get("flags_budget", 0) + time.time() - t0

        for D in dates:
            mask = df_full.trade_date.astype(str) <= D
            t0 = time.time()
            sub = df_full[mask].copy()
            t_dict["slice_copy"] = t_dict.get("slice_copy", 0) + time.time() - t0

            t0 = time.time()
            price = mod._qfq_price(sub)
            t_dict["qfq"] = t_dict.get("qfq", 0) + time.time() - t0

            t0 = time.time()
            sub2 = mod._fill_ma_columns(sub, price=price)
            t_dict["ma"] = t_dict.get("ma", 0) + time.time() - t0

            t0 = time.time()
            anchor = ev._fast_anchor(mod, sub2, flags)
            t_dict["fast_anchor"] = t_dict.get("fast_anchor", 0) + time.time() - t0

            if anchor is None:
                anchor = mod.find_activation_anchor(sub2)
                if anchor is None:
                    continue
            event_idx, ep = anchor

            uni = ctx[D]["uni"]
            name, industry = uni[code]
            ige = ctx[D]["ige"].get(code)

            for cal in ("old", "new"):
                ev.set_caliber(mod, cal)
                t0 = time.time()
                res = mod.analyze(code, name, industry, sub2, ctx[D]["anchors"], reader=reader,
                                  mkt=ctx[D]["mkt"], sector_strength={}, sector_growth={},
                                  ige_adj=ige, event_hint=(event_idx, ep))
                t_dict["analyze"] = t_dict.get("analyze", 0) + time.time() - t0
                if not res:
                    continue
                t0 = time.time()
                res["ige_adj"] = ige
                res["market_regime"] = ctx[D]["regime"]
                st, fl, fn = mod.w7_exec_status(res)
                ok, why = mod.w7_quality_gate(res)
                t_dict["gate"] = t_dict.get("gate", 0) + time.time() - t0
    return t_dict


t0 = time.time()
td = stage_times()
total = time.time() - t0
print("\n=== 分段耗时（%d 只 × %d 日 × 2 口径，总 %.1fs）===" % (len(codes), len(dates), total))
for k, v in sorted(td.items(), key=lambda kv: -kv[1]):
    print("  %-14s %7.2fs  %5.1f%%" % (k, v, v / total * 100))

# ---- cProfile 细看 analyze 内部 ----
print("\n=== cProfile（单票全流程）===")
code = codes[0]
df_full = frames[code]
df_full = df_full[df_full.trade_date.astype(str) >= m.DATA_START].reset_index(drop=True)
flags = {}
for i in range(m.MIN_BARS, len(df_full) - 1):
    ok, ep = m.extreme_event(df_full, i)
    if ok:
        flags[i] = (True, ep)
pr = cProfile.Profile()
pr.enable()
for D in dates:
    sub = df_full[df_full.trade_date.astype(str) <= D].copy()
    sub = m._fill_ma_columns(sub, price=m._qfq_price(sub))
    anchor = ev._fast_anchor(m, sub, flags) or m.find_activation_anchor(sub)
    if anchor is None:
        continue
    uni = ctx[D]["uni"]
    name, industry = uni[code]
    for cal in ("old", "new"):
        ev.set_caliber(m, cal)
        mod = m
        mod.analyze(code, name, industry, sub, ctx[D]["anchors"], reader=r,
                    mkt=ctx[D]["mkt"], sector_strength={}, sector_growth={},
                    ige_adj=ctx[D]["ige"].get(code), event_hint=anchor)
pr.disable()
s = io.StringIO()
pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(28)
print(s.getvalue()[:6000])
