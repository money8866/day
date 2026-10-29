# -*- coding: utf-8 -*-
"""W7 配对回测优化验证（第二轮）：① analyze 内部 cProfile 分解 ② flags 向量化等价性
③ numpy-backed 帧对 analyze 的实际加速 ④ DB 批量加载吞吐 ⑤ 单股票 57 日 × 2 口径耗时。

目的：确定「全市场配对回测」能在可接受时间内跑完的最终架构。
"""
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
codes = list(r.universe("20261028").ts_code.tolist())
print("股池 %d 只" % len(codes))
SAMPLE = codes[:24]
Q = m._BARS_SELECT
ph = ",".join(["?"] * len(SAMPLE))
q = Q + (" WHERE d.ts_code IN (%s) AND d.trade_date>=? AND d.trade_date<=? "
         "ORDER BY d.ts_code,d.trade_date" % ph)
t = time.time()
df = pd.read_sql_query(q, r.conn, params=SAMPLE + ["20230103", "20261028"])
print("load %.2fs rows=%d (%.0f 行/秒)" % (time.time() - t, len(df), len(df) / (time.time() - t)))

anchors = {}
for label, (acode, adate) in m.ANCHORS.items():
    adf = r.bars_sql(acode, "20261028")
    anchors[label] = m.anchor_features(adf, adate)
mdates, mvals = r.market_curve("20261028")
mkt = m.MarketCtx(mdates, mvals)

code = SAMPLE[0]
sub0 = df[df.ts_code == code].reset_index(drop=True)
sub0 = m._fill_ma_columns(sub0.copy(), price=m._qfq_price(sub0.copy()))

# ---------------------------------------------------------------- ② flags 向量化
print("\n=== ② extreme_event 向量化等价性 ===")
W = 120


def prep(dfx):
    return dict(
        vols=pd.to_numeric(dfx.vol, errors="coerce").fillna(0).to_numpy(dtype=float),
        amts=pd.to_numeric(dfx.amount, errors="coerce").fillna(0).to_numpy(dtype=float),
        turns=pd.to_numeric(dfx.turnover_rate, errors="coerce").fillna(0).to_numpy(dtype=float),
        pct=pd.to_numeric(dfx.pct_chg, errors="coerce").fillna(0).to_numpy(dtype=float),
        opn=dfx.open.to_numpy(dtype=float), hig=dfx.high.to_numpy(dtype=float),
        low=dfx.low.to_numpy(dtype=float), cls=dfx.close.to_numpy(dtype=float),
        codes=dfx.ts_code.to_numpy(),
    )


def vec_flags(A, lo, hi):
    """向量化 extreme_event（window=120），返回 {i: (True, ep)}，仅 i in [lo, hi)。"""
    out = {}
    n = len(A["vols"])
    if hi <= W:
        return out
    hi = min(hi, n)
    sw_v = np.lib.stride_tricks.sliding_window_view(A["vols"][:hi - 1], W)
    sw_a = np.lib.stride_tricks.sliding_window_view(A["amts"][:hi - 1], W)
    sw_t = np.lib.stride_tricks.sliding_window_view(A["turns"][:hi - 1], W)
    p_vol = (sw_v <= A["vols"][W:hi][:, None]).sum(axis=1) * 100.0 / W
    p_amt = (sw_a <= A["amts"][W:hi][:, None]).sum(axis=1) * 100.0 / W
    p_turn = (sw_t <= A["turns"][W:hi][:, None]).sum(axis=1) * 100.0 / W
    pct, low, cls, hig = A["pct"], A["low"], A["cls"], A["hig"]
    codes = A["codes"]
    for i in range(max(W, lo), hi):
        k = i - W
        pv, pa, pt = p_vol[k], p_amt[k], p_turn[k]
        if pv < m.MAIN_EVENT_PCT:
            continue
        prev_pct = pct[i - 1] if i >= 1 else 0.0
        if prev_pct > 10.0:
            if pct[i] <= -(m.limit_down_pct(codes[i]) - 0.5):
                continue
            span = hig[i] - low[i]
            if span > 0 and (cls[i] - low[i]) / span < 0.30:
                continue
        elif pct[i] <= 0.0:
            span = hig[i] - low[i]
            if span > 0 and (cls[i] - low[i]) / span < 0.30:
                continue
        if pa >= m.MAIN_EVENT_PCT:
            out[i] = (True, max(pa, pv))
        elif pt >= m.MAIN_EVENT_PCT and A["turns"][i] > 0:
            out[i] = (True, max(pt, pv))
    return out


A = prep(sub0)
t = time.time()
vf = vec_flags(A, m.MIN_BARS, len(sub0))
t_vec = time.time() - t
t = time.time()
rf = {}
for i in range(m.MIN_BARS, len(sub0) - 1):
    ok, ep = m.extreme_event(sub0, i)
    if ok:
        rf[i] = (True, ep)
t_ref = time.time() - t
print("  向量化 %.4fs  参考 %.4fs  加速 %.1fx" % (t_vec, t_ref, t_ref / max(t_vec, 1e-9)))
print("  结果完全一致：", vf == rf)
if vf != rf:
    print("  仅向量:", sorted(set(vf) - set(rf))[:8], " 仅参考:", sorted(set(rf) - set(vf))[:8])

# 多股验证
ok_all = True
for c in SAMPLE[:10]:
    s = df[df.ts_code == c].reset_index(drop=True)
    s = m._fill_ma_columns(s.copy(), price=m._qfq_price(s.copy()))
    AA = prep(s)
    v2 = vec_flags(AA, m.MIN_BARS, len(s))
    r2 = {}
    for i in range(m.MIN_BARS, len(s) - 1):
        o, ep = m.extreme_event(s, i)
        if o:
            r2[i] = (True, ep)
    if v2 != r2:
        ok_all = False
        print("  ✗ %s 不一致：仅向量 %s 仅参考 %s" % (c, sorted(set(v2) - set(r2))[:5],
                                                sorted(set(r2) - set(v2))[:5]))
print("  10 只股票交叉验证：", "全部一致" if ok_all else "存在差异")

# ---------------------------------------------------------------- ③ numpy-backed 加速
print("\n=== ③ numpy-backed 帧对 analyze 的实际加速 ===")


def to_np(dfx):
    d2 = dfx.copy()
    for c in d2.columns:
        if pd.api.types.is_numeric_dtype(d2[c]):
            d2[c] = d2[c].astype("float64")
        else:
            d2[c] = d2[c].astype(object)
    return d2


sub_np = to_np(sub0)


def bench_analyze(dfx, n=60):
    t = time.time()
    for _ in range(n):
        m.analyze(code, "测试", "行业", dfx, anchors, reader=r, mkt=mkt,
                  sector_strength={}, sector_growth={}, ige_adj=None,
                  event_hint=(m.MIN_BARS + 10, 99.0))
    return (time.time() - t) / n * 1000


t_arrow = bench_analyze(sub0)
t_np = bench_analyze(sub_np)
print("  原始(str/arrow-backed) %.2f ms/次   numpy-backed %.2f ms/次   加速 %.2fx"
      % (t_arrow, t_np, t_arrow / max(t_np, 1e-9)))

# 去掉 ts_code/trade_date 列后的影响（字符串列是否拖慢行访问）
sub_min = to_np(sub0.drop(columns=["ts_code"]))
print("  numpy-backed 去掉 ts_code 列：%.2f ms/次" % bench_analyze(sub_min))

# ---------------------------------------------------------------- ④ DB 批量加载吞吐
print("\n=== ④ DB 批量加载吞吐 ===")
for blk in (40, 120):
    t = time.time()
    n_rows = 0
    for i in range(0, 240, blk):
        cs = SAMPLE[i:i + blk]
        p2 = ",".join(["?"] * len(cs))
        qq = Q + (" WHERE d.ts_code IN (%s) AND d.trade_date>=? AND d.trade_date<=? "
                  "ORDER BY d.ts_code,d.trade_date" % p2)
        d3 = pd.read_sql_query(qq, r.conn, params=list(cs) + ["20230103", "20261028"])
        n_rows += len(d3)
    dt = time.time() - t
    print("  block=%3d  240 只 %.2fs  %d 行  → 全市场 5570 只 ≈ %.0fs"
          % (blk, dt, n_rows, dt / 240 * 5570))

# ---------------------------------------------------------------- ⑤ 单股 57 日耗时
print("\n=== ⑤ 单股票 57 日 × 2 口径（引擎原生路径）===")
all_dates = [str(x[0]) for x in r.conn.execute(
    "SELECT DISTINCT trade_date FROM index_daily_cache WHERE ts_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
    (m.MARKET_INDEX_CODE, "20260803", "20261028")).fetchall()]
print("  交易日 %d 个" % len(all_dates))

GATE_KEYS = ("W7_GATE_A_PULL_SHRINK", "W7_GATE_A_STALL_VOLR", "W7_GATE_B_PULL_SHRINK",
             "W7_GATE_B_BREAK_VOLR", "W7_GATE_B_DIST_RISK", "W7_GATE_PERSIST_SELL")


def native_stock(sub_full, use_np=False):
    if use_np:
        sub_full = to_np(sub_full)
    t0 = time.time()
    for D in all_dates:
        mask = sub_full.trade_date.astype(str) <= D
        sub = sub_full[mask].copy()
        if len(sub) < m.MIN_BARS:
            continue
        sub = m._fill_ma_columns(sub, price=m._qfq_price(sub))
        for cal in ("old", "new"):
            for k in GATE_KEYS:
                setattr(m, k, cal == "old")
            m.analyze(code, "测试", "行业", sub, anchors, reader=r, mkt=mkt,
                      sector_strength={}, sector_growth={}, ige_adj=None,
                      event_hint=(m.MIN_BARS + 10, 99.0))
    return time.time() - t0


sf = df[df.ts_code == code].reset_index(drop=True)
sf = sf[sf.trade_date.astype(str) >= m.DATA_START].reset_index(drop=True)
t1 = native_stock(sf, False)
t2 = native_stock(sf, True)
print("  原始 %.2fs/股   numpy-backed %.2fs/股" % (t1, t2))
print("  → 全市场 5570 只单进程 %.0f 分钟；8 进程 %.0f 分钟"
      % (t2 / 5570 * 60, t2 / 5570 * 60 / 8))
