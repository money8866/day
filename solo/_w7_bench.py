# -*- coding: utf-8 -*-
"""一次性性能基准：确定配对回测的实现路线（各环节耗时）。"""
import importlib.util
import os
import sys
import time

import numpy as np
import pandas as pd

SOLO = r"d:\mystock\solo"
sys.path.insert(0, SOLO)


def load_engine():
    spec = importlib.util.spec_from_file_location("w7eng", os.path.join(SOLO, "w7_second_wave_engine.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["w7eng"] = mod
    spec.loader.exec_module(mod)
    return mod


m = load_engine()
r = m.CacheReader()
d = "20261028"
u = r.universe(d)
codes = list(u.ts_code.tolist())[:120]
t0 = time.time()
r.load_fina()
print("load_fina: %.1fs" % (time.time() - t0))
t = time.time()
r.load_all(d, codes=codes)
print("load_all 120: %.1fs" % (time.time() - t0))

frames = r.frames
print("frames:", len(frames), "avg rows:", int(np.mean([len(x) for x in frames.values()])))

# ---- 1) 单股 qfq + MA 重算（截断序列）耗时 ----
df0 = list(frames.values())[0]
t = time.time()
for _ in range(50):
    sub = df0.iloc[:400].copy()
    p = m._qfq_price(sub)
    ma = m._fill_ma_columns(sub, price=p)
print("qfq+MA per (stock,date): %.2f ms" % ((time.time() - t) / 50 * 1000))

# ---- 2) state_and_features 耗时 ----
anchors = {}
res_list = []
t = time.time()
n = 0
for c, df in frames.items():
    a = m.find_event_anchor(df)
    if not a:
        a = m.find_activation_anchor(df)
    if a:
        r2 = m.state_and_features(df, a[0], a[1])
        if r2:
            res_list.append((c, df, a, r2))
            n += 1
dt = time.time() - t
print("anchor+state over %d stocks: %.2fs (%.1f ms/只)  有状态=%d" % (len(frames), dt, dt / len(frames) * 1000, n))

# ---- 3) analyze() 全流程耗时（含 fina/IGE/sector）----
idx = r.universe(d).set_index("ts_code")
mkt_dates, mkt_vals = r.market_curve(d)
mkt = m.MarketCtx(mkt_dates, mkt_vals)
ige_info, ige_snap = m.load_ige_adj(d)
t = time.time()
cnt = 0
for c, df, a, r2 in res_list[:40]:
    row = idx.loc[c] if c in idx.index else {}
    ig = ige_info.get(c)
    res = m.analyze(c, str(row.get("name", c)), str(row.get("industry", "") or ""), df, anchors,
                    reader=r, mkt=mkt, sector_strength={}, sector_growth={},
                    ige_adj=ig["ige_adj"] if ig else None, event_hint=a)
    if res:
        cnt += 1
dt = time.time() - t
print("analyze() x%d: %.2fs (%.1f ms/只) 有结果=%d" % (40, dt, dt / 40 * 1000, cnt))

# ---- 4) reader.fina 单次耗时 ----
c0 = res_list[0][0]
t = time.time()
for _ in range(30):
    r.fina(c0, as_of=d)
print("reader.fina per call: %.1f ms" % ((time.time() - t) / 30 * 1000))

# ---- 5) load_ige_adj 耗时 ----
t = time.time()
m.load_ige_adj(d)
print("load_ige_adj: %.2fs" % (time.time() - t))

# ---- 6) 全市场等权基准（每日均值）可用性 ----
q = "SELECT trade_date, AVG(pct_chg) FROM daily_cache WHERE trade_date>=? AND trade_date<=? GROUP BY trade_date ORDER BY trade_date"
t = time.time()
eq = pd.read_sql_query(q, r.conn, params=["20260101", d])
print("equal-weight curve rows=%d in %.2fs" % (len(eq), time.time() - t))
print(eq.head(3).to_string(), "...", eq.tail(2).to_string())
r.close()
