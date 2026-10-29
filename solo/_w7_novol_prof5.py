# -*- coding: utf-8 -*-
"""W7 配对回测架构决策测量（第四轮·修好版）：

① 探针（t0_pullback_hold + t0_rebreak）单独成本 vs analyze 成本
② new 口径探针命中 vs old 口径探针命中（验证 old ⊆ new 的包含关系）
③ 探针命中率（决定「探针预筛」能省掉多少 analyze）
④ MA/前复权「全历史预计算 + 逐日缩放」与「逐日精确重算」的等价性与加速
⑤ 端到端单股耗时外推

结论用途：决定全市场配对回测的最终架构（是否做探针预筛、并行度）。
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
    ld = importlib.machinery.SourceFileLoader(n, p)
    sp = importlib.util.spec_from_loader(n, ld)
    mm = importlib.util.module_from_spec(sp)
    sys.modules[n] = mm
    ld.exec_module(mm)
    return mm


m = load(os.path.join(SOLO, "w7_second_wave_engine.py"), "w7eng")
r = m.CacheReader()
r.load_fina()
codes = list(r.universe("20261028").ts_code.tolist())
SAMPLE = codes[:60]
Q = m._BARS_SELECT
ph = ",".join(["?"] * len(SAMPLE))
q = Q + (" WHERE d.ts_code IN (%s) AND d.trade_date>=? AND d.trade_date<=? "
         "ORDER BY d.ts_code,d.trade_date" % ph)
df = pd.read_sql_query(q, r.conn, params=SAMPLE + ["20230103", "20261028"])

anchors = {}
for label, (acode, adate) in m.ANCHORS.items():
    adf = r.bars_sql(acode, "20261028")
    anchors[label] = m.anchor_features(adf, adate)
mdates, mvals = r.market_curve("20261028")
mkt = m.MarketCtx(mdates, mvals)

GATE_KEYS = ("W7_GATE_A_PULL_SHRINK", "W7_GATE_A_STALL_VOLR", "W7_GATE_B_PULL_SHRINK",
             "W7_GATE_B_BREAK_VOLR", "W7_GATE_B_DIST_RISK", "W7_GATE_PERSIST_SELL")


def set_cal(v):
    for k in GATE_KEYS:
        setattr(m, k, v)


all_dates = [str(x[0]) for x in r.conn.execute(
    "SELECT DISTINCT trade_date FROM index_daily_cache WHERE ts_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
    (m.MARKET_INDEX_CODE, "20260803", "20261028")).fetchall()]
print("股池 %d 只 / 交易日 %d 个" % (len(codes), len(all_dates)))

frames = {}
for code in SAMPLE[:24]:
    s = df[df.ts_code == code].reset_index(drop=True)
    s = s[s.trade_date.astype(str) >= m.DATA_START].reset_index(drop=True)
    frames[code] = s

# 构造 (code, D) 组合（截断帧带 MA 列）
combos = []
for code, s in frames.items():
    if len(s) < m.MIN_BARS:
        continue
    for D in all_dates:
        sub = s[s.trade_date.astype(str) <= D].copy()
        if len(sub) < m.MIN_BARS:
            continue
        sub = m._fill_ma_columns(sub, price=m._qfq_price(sub))
        combos.append((code, D, sub))
print("样本组合 %d 个" % len(combos))


def anchor_of(sub):
    a = m.find_event_anchor(sub)
    if a is None:
        a = m.find_activation_anchor(sub)
    return a


# ---------------------------------------------------------------- ① 成本
print("\n=== ① 探针 vs analyze 成本 ===")
N = 80
probe_subs = combos[:N]

set_cal(False)  # new
t = time.time()
probe_new = []
for code, D, sub in probe_subs:
    a = anchor_of(sub)
    if a is None:
        probe_new.append(None)
        continue
    ei, ep = a
    ph_, _ = m.t0_pullback_hold(sub, ei, len(sub) - 1)
    rb_, _ = m.t0_rebreak(sub, ei, len(sub) - 1)
    probe_new.append((ei, ph_, rb_))
t_probe = (time.time() - t) / N * 1000

set_cal(False)
t = time.time()
for code, D, sub in probe_subs:
    a = anchor_of(sub)
    if a is None:
        continue
    ei, ep = a
    m.analyze(code, "测试", "行业", sub, anchors, reader=r, mkt=mkt,
              sector_strength={}, sector_growth={}, ige_adj=None, event_hint=(ei, ep))
t_analyze = (time.time() - t) / N * 1000
print("  锚点+双探针 %.2f ms/次    锚点+analyze %.2f ms/次    analyze 净成本 %.2f ms/次"
      % (t_probe, t_analyze, t_analyze - t_probe))

# 锚点单独成本
t = time.time()
for code, D, sub in probe_subs:
    anchor_of(sub)
t_anchor = (time.time() - t) / N * 1000
print("  锚点单独 %.2f ms/次    探针净成本 %.2f ms/次" % (t_anchor, t_probe - t_anchor))

# ---------------------------------------------------------------- ② 包含关系
print("\n=== ② old/new 探针包含关系 ===")
set_cal(True)  # old
t = time.time()
probe_old = []
for code, D, sub in probe_subs:
    a = anchor_of(sub)
    if a is None:
        probe_old.append(None)
        continue
    ei, ep = a
    ph_, _ = m.t0_pullback_hold(sub, ei, len(sub) - 1)
    rb_, _ = m.t0_rebreak(sub, ei, len(sub) - 1)
    probe_old.append((ei, ph_, rb_))
print("  old 探针 %.2f ms/次" % ((time.time() - t) / N * 1000))

viol_anchor = 0
viol_hit = 0
n_new = n_old = n_both = n_only_new = 0
for pn, po in zip(probe_new, probe_old):
    if pn is None or po is None:
        continue
    if pn[0] != po[0]:
        viol_anchor += 1
        continue
    new_hit = bool(pn[1] or pn[2])
    old_hit = bool(po[1] or po[2])
    n_new += new_hit
    n_old += old_hit
    if old_hit and new_hit:
        n_both += 1
    elif new_hit and not old_hit:
        n_only_new += 1
    if old_hit and not new_hit:
        viol_hit += 1
print("  锚点不一致 %d（应为0，锚与口径无关）" % viol_anchor)
print("  new 命中 %d / old 命中 %d / 交集 %d / 仅 new %d / 违反包含(old⊆new) %d"
      % (n_new, n_old, n_both, n_only_new, viol_hit))

# ---------------------------------------------------------------- ③ 命中率
print("\n=== ③ 探针命中率 ===")
set_cal(False)
n_anchor = len(probe_new)
n_hit_new = sum(1 for p in probe_new if p and (p[1] or p[2]))
print("  有锚点 %d / %d   new 探针命中 %d (%.2f%%)"
      % (n_anchor, len(probe_new), n_hit_new, 100.0 * n_hit_new / max(1, len(probe_new))))
set_cal(True)
n_hit_old = sum(1 for p in probe_old if p and (p[1] or p[2]))
print("  old 探针命中 %d (%.2f%%)" % (n_hit_old, 100.0 * n_hit_old / max(1, len(probe_old))))
set_cal(False)

# ---------------------------------------------------------------- ④ MA 预计算等价性
print("\n=== ④ MA/前复权：全历史预计算 + 逐日缩放 vs 逐日精确重算 ===")
code0 = list(frames)[0]
s0 = frames[code0]
adjf = s0["adj_factor"].astype(float).ffill().to_numpy()
close = s0["close"].astype(float).to_numpy()
p = close * adjf
ma_raw = {}
for w in (10, 20, 60, 120):
    ma_raw[w] = pd.Series(p).rolling(w, min_periods=w).mean().to_numpy()
adjf_end = adjf[-1]

max_ma_diff = 0.0
max_price_diff = 0.0
t0 = time.time()
for D in all_dates:
    sub_ref = s0[s0.trade_date.astype(str) <= D].copy()
    if len(sub_ref) < m.MIN_BARS:
        continue
    ref_price = m._qfq_price(sub_ref).to_numpy()
    ref = m._fill_ma_columns(sub_ref, price=ref_price)
    k = len(sub_ref)
    # 预计算版：MA_raw / adjf_D
    adjf_D = adjf[k - 1]
    for w in (10, 20, 60, 120):
        pre = ma_raw[w][:k] / adjf_D
        got = ref["ma_bfq_%d" % w].to_numpy()
        d = np.nanmax(np.abs(np.nan_to_num(pre - got, nan=0.0)))
        max_ma_diff = max(max_ma_diff, float(d))
    pre_price = p[:k] / adjf_D
    d = np.nanmax(np.abs(np.nan_to_num(pre_price - ref_price, nan=0.0)))
    max_price_diff = max(max_price_diff, float(d))
t_pre = time.time() - t0
print("  MA 最大逐点差 = %.3e    价格最大逐点差 = %.3e" % (max_ma_diff, max_price_diff))
print("  逐日精确重算 57 日耗时 %.2fs" % t_pre)

# ---------------------------------------------------------------- ⑤ 外推
print("\n=== ⑤ 端到端外推（24 只样本）===")
set_cal(False)
t0 = time.time()
n_sig = 0
for code, s in frames.items():
    for D in all_dates:
        sub = s[s.trade_date.astype(str) <= D].copy()
        if len(sub) < m.MIN_BARS:
            continue
        sub = m._fill_ma_columns(sub, price=m._qfq_price(sub))
        a = anchor_of(sub)
        if a is None:
            continue
        ei, ep = a
        ph_, _ = m.t0_pullback_hold(sub, ei, len(sub) - 1)
        rb_, _ = m.t0_rebreak(sub, ei, len(sub) - 1)
        if not (ph_ or rb_):
            continue
        for cal in ("old", "new"):
            set_cal(cal == "old")
            res = m.analyze(code, "测试", "行业", sub, anchors, reader=r, mkt=mkt,
                            sector_strength={}, sector_growth={}, ige_adj=None, event_hint=(ei, ep))
            if res and res["state"] in m.ACTION_BUY_STATES:
                n_sig += 1
        set_cal(False)
dt = time.time() - t0
print("  探针预筛路径 %.2fs / %d 只  → 5570 只单进程 %.1f 分钟"
      % (dt, len(frames), dt / len(frames) * 5570 / 60))
print("  买点态命中 %d 次" % n_sig)
