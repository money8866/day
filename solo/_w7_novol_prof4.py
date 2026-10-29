# -*- coding: utf-8 -*-
"""W7 配对回测架构决策测量（第三轮）：

① 探针预筛成本：t0_pullback_hold + t0_rebreak 单次成本 vs analyze 单次成本
② 新旧口径探针包含关系验证（old 的 T0 信号 ⊆ new 的 T0 信号？）
③ reader.fina 在 analyze 内的成本占比
④ 单 worker 内存占用基线
⑤ 端到端小样本耗时推算

结论用途：决定「全市场配对回测」最终架构与并行度。
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
SAMPLE = codes[:40]
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


# ---------- 构造样本 (股票, 日期) 组合 ----------
all_dates = [str(x[0]) for x in r.conn.execute(
    "SELECT DISTINCT trade_date FROM index_daily_cache WHERE ts_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
    (m.MARKET_INDEX_CODE, "20260803", "20261028")).fetchall()]
print("交易日 %d 个" % len(all_dates))

frames = {}
for code in SAMPLE[:20]:
    s = df[df.ts_code == code].reset_index(drop=True)
    s = s[s.trade_date.astype(str) >= m.DATA_START].reset_index(drop=True)
    frames[code] = s

# 取一个真实锚点（用引擎 find_event_anchor 在末日截断上）
combos = []
for code, s in frames.items():
    if len(s) < m.MIN_BARS:
        continue
    for D in all_dates:
        mask = s.trade_date.astype(str) <= D
        sub = s[mask].copy()
        if len(sub) < m.MIN_BARS:
            continue
        combos.append((code, D, sub))
print("样本组合 %d 个（20 只 × 57 日）" % len(combos))

# ---------- ① 探针 vs analyze 成本 ----------
print("\n=== ① 探针与 analyze 成本 ===")
N = 60
probe_subs = combos[:N]

set_cal(False)  # new
t = time.time()
probe_new = []
for code, D, sub in probe_subs:
    a = m.find_event_anchor(sub)
    if a is None:
        a = m.find_activation_anchor(sub)
    if a is None:
        probe_new.append(None)
        continue
    ei, ep = a
    ph_, _ = m.t0_pullback_hold(sub, ei, len(sub) - 1)
    rb_, _ = m.t0_rebreak(sub, ei, len(sub) - 1)
    probe_new.append((ei, ph_, rb_))
t_probe = (time.time() - t) / N * 1000

t = time.time()
for code, D, sub in probe_subs:
    a = m.find_event_anchor(sub)
    if a is None:
        a = m.find_activation_anchor(sub)
    if a is None:
        continue
    ei, ep = a
    m.analyze(code, "测试", "行业", sub, anchors, reader=r, mkt=mkt,
              sector_strength={}, sector_growth={}, ige_adj=None, event_hint=(ei, ep))
t_analyze = (time.time() - t) / N * 1000

print("  find_anchor+双探针 %.2f ms/次   analyze(含锚查找) %.2f ms/次" % (t_probe, t_analyze))

# 纯 analyze（锚已缓存）
cached = []
for code, D, sub in probe_subs:
    a = m.find_event_anchor(sub) or m.find_activation_anchor(sub)
    if a is not None:
        cached.append((code, sub, a))
t = time.time()
for code, sub, a in cached:
    m.analyze(code, "测试", "行业", sub, anchors, reader=r, mkt=mkt,
              sector_strength={}, sector_growth={}, ige_adj=None, event_hint=(a[0], a[1]))
t_analyze2 = (time.time() - t) / max(1, len(cached)) * 1000
print("  analyze（锚复用） %.2f ms/次" % t_analyze2)

# ---------- ② new/old 探针包含关系 ----------
print("\n=== ② old/new 探针包含关系（old ⊆ new？）===")
set_cal(True)   # old
t = time.time()
probe_old = []
for code, D, sub in probe_subs:
    a = m.find_event_anchor(sub)
    if a is None:
        a = m.find_activation_anchor(sub)
    if a is None:
        probe_old.append(None)
        continue
    ei, ep = a
    ph_, _ = m.t0_pullback_hold(sub, ei, len(sub) - 1)
    rb_, _ = m.t0_rebreak(sub, ei, len(sub) - 1)
    probe_old.append((ei, ph_, rb_))
t_probe_old = (time.time() - t) / N * 1000
print("  old 探针 %.2f ms/次" % t_probe_old)

viol = 0
both = 0
n_new = 0
n_old = 0
for pn, po in zip(probe_new, probe_old):
    if pn is None or po is None:
        continue
    if pn[0] != po[0]:
        viol += 1
        continue
    new_hit = pn[1] or pn[2]
    old_hit = po[1] or po[2]
    n_new += bool(new_hit)
    n_old += bool(old_hit)
    if old_hit and not new_hit:
        viol += 1
    if old_hit and new_hit:
        both += 1
print("  new 探针命中 %d / old 探针命中 %d / 同时命中 %d / 违反包含关系 %d"
      % (n_new, n_old, both, viol))
print("  锚点一致（old vs new 事件索引）: %s" % ("是" if viol == 0 else "否"))

# ---------- ③ fina 成本 ----------
print("\n=== ③ reader.fina 成本 ===")
fina_codes = [c for c in SAMPLE if c in r.fina_frames][:20]
if fina_codes:
    t = time.time()
    for _ in range(200):
        for c in fina_codes[:5]:
            r.fina(c, as_of="20261028")
    t_fina = (time.time() - t) / (200 * 5) * 1000
    print("  fina() %.3f ms/次（%d 只有财务）" % (t_fina, len(fina_codes)))
else:
    print("  样本无财务数据")

# ---------- ④ 内存基线 ----------
print("\n=== ④ 内存基线 ===")
try:
    import psutil
    print("  当前进程 RSS = %.0f MB" % (psutil.Process().memory_info().rss / 1e6))
except Exception:
    print("  psutil 不可用")

# ---------- ⑤ 端到端推算 ----------
print("\n=== ⑤ 端到端推算（40 只 × 57 日 × 2 口径）===")
set_cal(False)
t0 = time.time()
n_sig = 0
for code, s in frames.items():
    for D in all_dates:
        mask = s.trade_date.astype(str) <= D
        sub = s[mask].copy()
        if len(sub) < m.MIN_BARS:
            continue
        sub = m._fill_ma_columns(sub, price=m._qfq_price(sub))
        a = m.find_event_anchor(sub) or m.find_activation_anchor(sub)
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
print("  探针预筛后耗时 %.1fs（20 只）  → 5570 只单进程 %.1f 分钟"
      % (dt, dt / 20 * 5570 / 60))
print("  命中买点态 %d 次" % n_sig)
