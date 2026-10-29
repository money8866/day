# -*- coding: utf-8 -*-
"""W7 配对回测优化验证：① 行访问速度（arrow vs numpy）② MA 预计算等价性 ③ 57 日 build_ctx 成本。"""
import importlib.machinery
import importlib.util
import os
import sys
import time

import numpy as np
import pandas as pd

SOLO = os.path.dirname(os.path.abspath(__file__))
if SOLO not in sys.path:
    sys.path.insert(0, SOLO)
print("pandas", pd.__version__)


def load(p, n):
    l = importlib.machinery.SourceFileLoader(n, p)
    sp = importlib.util.spec_from_loader(n, l)
    m = importlib.util.module_from_spec(sp)
    sys.modules[n] = m
    l.exec_module(m)
    return m


m = load(os.path.join(SOLO, "w7_second_wave_engine.py"), "w7eng")
r = m.CacheReader()
r.load_fina()
codes = list(r.universe("20261028").ts_code.tolist())[:6]
Q = m._BARS_SELECT
ph = ",".join(["?"] * len(codes))
q = Q + " WHERE d.ts_code IN (%s) AND d.trade_date>=? AND d.trade_date<=? ORDER BY d.ts_code,d.trade_date" % ph
t = time.time()
df = pd.read_sql_query(q, r.conn, params=codes + ["20230103", "20261028"])
print("load %.2fs rows=%d" % (time.time() - t, len(df)))
print("dtypes:\n", df.dtypes)

# ---------- ① 行访问速度 ----------
code = codes[0]
sub0 = df[df.ts_code == code].reset_index(drop=True)
print("\n=== ① df.iloc[i] 行访问速度（%d 行）===" % len(sub0))


def bench(dfx, label, n=3000):
    t = time.time()
    s = 0.0
    for i in range(200, min(200 + n, len(dfx))):
        row = dfx.iloc[i]
        s += float(row.close)
    dt = time.time() - t
    print("  %-22s %7.1f µs/行   sum=%.1f" % (label, dt / n * 1e6, s))


bench(sub0, "原始(arrow-backed)")

dfn = sub0.copy()
for c in dfn.columns:
    if pd.api.types.is_numeric_dtype(dfn[c]):
        dfn[c] = dfn[c].astype("float64")
    else:
        dfn[c] = dfn[c].astype(object)
bench(dfn, "numpy-backed")
print("  → 转换后 dtypes:\n", dfn.dtypes.to_string())

# ---------- ② MA 预计算等价性 ----------
print("\n=== ② MA 预计算 vs 逐日精确重算 ===")
full = dfn.copy()
adjf = full["adj_factor"].astype(float).ffill()
p = full["close"].astype(float) * adjf
rm = {}
for w, col in m._MA_WINDOWS:
    rm[col] = p.rolling(w, min_periods=w).mean()

dates = full["trade_date"].astype(str).to_numpy()
targets = [dates[-1], dates[-5], dates[-20], dates[-40]]
ok_all = True
for D in targets:
    k = int(np.searchsorted(dates, D, side="right"))
    trunc = full.iloc[:k].copy()
    # 精确路径：引擎原函数
    exact = m._fill_ma_columns(trunc.copy(), price=m._qfq_price(trunc.copy()))
    # 快速路径：p 的滚动均值 ÷ 截断序列末端 adjf
    last_adjf = adjf.iloc[:k].iloc[-1]
    for w, col in m._MA_WINDOWS:
        fast = rm[col].iloc[:k] / last_adjf
        e = exact[col].astype(float).to_numpy()
        f = fast.to_numpy()
        both_nan = np.isnan(e) & np.isnan(f)
        diff = np.abs(np.where(both_nan, 0.0, e - f))
        mx = np.nanmax(diff) if diff.size else 0.0
        if mx > 1e-9:
            ok_all = False
            print("  ✗ %s %s maxdiff=%.3e" % (D, col, mx))
    # qfq 价本身
    ex_p = m._qfq_price(trunc.copy()).astype(float).to_numpy()
    fs_p = (p.iloc[:k] / last_adjf).to_numpy()
    both_nan = np.isnan(ex_p) & np.isnan(fs_p)
    d2 = np.abs(np.where(both_nan, 0.0, ex_p - fs_p))
    print("  %s rows=%d  qfq_maxdiff=%.3e" % (D, k, np.nanmax(d2)))
print("  MA 全等：", ok_all)

# ---------- ③ 逐日截断的现状成本 ----------
print("\n=== ③ 现状：逐日 mask+copy+fill_ma 成本 ===")
t = time.time()
for D in targets:
    mask = full["trade_date"].astype(str) <= D
    tr = full[mask].copy()
    tr = m._fill_ma_columns(tr, price=m._qfq_price(tr))
dt = time.time() - t
print("  逐日精确重算 %.1f ms/(股,日)" % (dt / len(targets) * 1000))

t = time.time()
for D in targets:
    k = int(np.searchsorted(dates, D, side="right"))
    tr = full.iloc[:k].copy()
    last_adjf = adjf.iloc[:k].iloc[-1]
    tr["ma_bfq_10"] = rm["ma_bfq_10"].iloc[:k] / last_adjf
    tr["ma_bfq_20"] = rm["ma_bfq_20"].iloc[:k] / last_adjf
    tr["ma_bfq_60"] = rm["ma_bfq_60"].iloc[:k] / last_adjf
    tr["ma_bfq_120"] = rm["ma_bfq_120"].iloc[:k] / last_adjf
dt2 = time.time() - t
print("  预计算+缩放 %.1f ms/(股,日)   加速 %.1fx" % (dt2 / len(targets) * 1000, dt / dt2))

# ---------- ④ build_ctx 57 日 ----------
print("\n=== ④ build_ctx 57 日成本 ===")
ev = load(os.path.join(SOLO, "_w7_novol_eval.py"), "evx")
alldates = [str(x[0]) for x in r.conn.execute(
    "SELECT DISTINCT trade_date FROM index_daily_cache WHERE ts_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
    (m.MARKET_INDEX_CODE, "20260803", "20261028")).fetchall()]
print("  交易日", len(alldates))
t = time.time()
ctx = ev.build_ctx(r, m, alldates, "20261028")
print("  build_ctx %.1fs" % (time.time() - t))
print("  ctx 内存代理: uni %d 日 × %d 只" % (len(alldates), len(ctx[alldates[-1]]["uni"])))
ige = ctx[alldates[-1]]["ige"]
print("  ige 条目 %d" % len(ige))
