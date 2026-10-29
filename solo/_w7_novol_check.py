# -*- coding: utf-8 -*-
"""W7 numpy 快版 vs 引擎原生：对拍验证（100% 一致是回测结论可信的前提）。

对拍对象：
  ① extreme_event 向量化预算 vs 引擎 extreme_event
  ② find_event_anchor（含 preshift/cluster）numpy 版 vs 引擎版
  ③ find_activation_anchor numpy 版 vs 引擎版
  ④ t0_pullback_hold / t0_rebreak numpy 版 vs 引擎版（两套口径全跑）
  ⑤ MA/前复权「全历史预计算 + 逐日缩放」vs 引擎逐日重算

用法：
  python _w7_novol_check.py            # 60 只 × 57 日
  python _w7_novol_check.py --codes 200
"""
import argparse
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

import _w7_novol_fast as F  # noqa: E402


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

GATE_KEYS = ("W7_GATE_A_PULL_SHRINK", "W7_GATE_A_STALL_VOLR", "W7_GATE_B_PULL_SHRINK",
             "W7_GATE_B_BREAK_VOLR", "W7_GATE_B_DIST_RISK", "W7_GATE_PERSIST_SELL")


def set_cal(v):
    for k in GATE_KEYS:
        setattr(m, k, v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", type=int, default=60)
    ap.add_argument("--start", default="20260803")
    ap.add_argument("--end", default="20261028")
    args = ap.parse_args()

    codes = list(r.universe("20261028").ts_code.tolist())
    sample = codes[:args.codes]
    Q = m._BARS_SELECT
    ph = ",".join(["?"] * len(sample))
    q = Q + (" WHERE d.ts_code IN (%s) AND d.trade_date>=? AND d.trade_date<=? "
             "ORDER BY d.ts_code,d.trade_date" % ph)
    df = pd.read_sql_query(q, r.conn, params=sample + [m.DATA_START, args.end])
    dates = [str(x[0]) for x in r.conn.execute(
        "SELECT DISTINCT trade_date FROM index_daily_cache WHERE ts_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date",
        (m.MARKET_INDEX_CODE, args.start, args.end)).fetchall()]
    print("样本 %d 只 × %d 交易日" % (len(sample), len(dates)))

    # ── ① extreme_event 向量化 ───────────────────────────────
    print("\n=== ① extreme_event 向量化 ===")
    bad = 0
    checked = 0
    for code in sample[:20]:
        s = df[df.ts_code == code].reset_index(drop=True)
        s = s[s.trade_date.astype(str) >= m.DATA_START].reset_index(drop=True)
        if len(s) < m.MIN_BARS:
            continue
        sp = F.StockPrep(m, s, code)
        ref = {}
        for i in range(m.MIN_BARS, len(s) - 1):
            ok, ep = m.extreme_event(s, i)
            if ok:
                ref[i] = (True, ep)
        checked += 1
        if sp.flags != ref:
            bad += 1
            print("  ✗ %s 不一致：仅向量化 %s 仅引擎 %s" %
                  (code, sorted(set(sp.flags) - set(ref))[:5], sorted(set(ref) - set(sp.flags))[:5]))
    print("  %d 只逐条比对：%s" % (checked, "全部一致" if bad == 0 else "存在 %d 只不一致" % bad))

    # ── ②③④ 锚点 + 探针逐条对拍 ─────────────────────────────
    print("\n=== ②③④ 锚点 / 双探针 逐条对拍（两套口径）===")
    n_anchor_checked = 0
    n_anchor_bad = 0
    n_act_checked = 0
    n_act_bad = 0
    n_ph_checked = n_ph_bad = 0
    n_rb_checked = n_rb_bad = 0
    n_ma_bad = 0
    ma_maxdiff = 0.0
    t0 = time.time()
    for code in sample:
        s = df[df.ts_code == code].reset_index(drop=True)
        s = s[s.trade_date.astype(str) >= m.DATA_START].reset_index(drop=True)
        if len(s) < m.MIN_BARS:
            continue
        sp = F.StockPrep(m, s, code)
        ps_cache = {}
        for D in dates:
            mask = s.trade_date.astype(str) <= D
            sub = s[mask].copy()
            if len(sub) < m.MIN_BARS:
                continue
            k = len(sub) - 1
            # 引擎逐日重算 MA/前复权
            sub = m._fill_ma_columns(sub, price=m._qfq_price(sub))
            # numpy 版 MA（缩放）
            adjf_D = sp.adjf_at(k)
            for w in (10, 20, 60, 120):
                got = sub["ma_bfq_%d" % w].to_numpy()
                pre = sp.ma_raw[w][:k + 1] / adjf_D
                d = np.nanmax(np.abs(np.nan_to_num(pre - got, nan=0.0)))
                ma_maxdiff = max(ma_maxdiff, float(d))
                if d > 1e-9:
                    n_ma_bad += 1

            # 锚点
            ref_a = m.find_event_anchor(sub)
            np_a = F.find_event_anchor_np(m, sp, k, ps_cache)
            n_anchor_checked += 1
            ref_t = (None, None) if ref_a is None else (ref_a[0], round(float(ref_a[1]), 9))
            np_t = (None, None) if np_a is None else (np_a[0], round(float(np_a[1]), 9))
            if ref_t != np_t:
                n_anchor_bad += 1
                if n_anchor_bad <= 5:
                    print("  ✗ 锚点 %s %s：引擎 %s numpy %s" % (code, D, ref_t, np_t))
                continue
            # 兜底锚
            ref_fb = m.find_activation_anchor(sub)
            np_fb = F.find_activation_anchor_np(m, sp, k)
            n_act_checked += 1
            ref_fb_t = (None, None) if ref_fb is None else (ref_fb[0], ref_fb[1])
            np_fb_t = (None, None) if np_fb is None else (np_fb[0], np_fb[1])
            if ref_fb_t != np_fb_t:
                n_act_bad += 1
                if n_act_bad <= 5:
                    print("  ✗ 兜底锚 %s %s：引擎 %s numpy %s" % (code, D, ref_fb_t, np_fb_t))
                continue

            anchor = ref_a if ref_a is not None else ref_fb
            if anchor is None:
                continue
            ei = anchor[0]
            t0_open = float(s.open.iloc[ei])
            t0_close = float(s.close.iloc[ei])
            t0_vol = float(s.vol.iloc[ei])
            ma60_end = float(sub["ma_bfq_60"].iloc[k])
            ma60_np = sp.ma_raw[60][k] / adjf_D

            for cal, (gps, gstall, gbshrink, gbreak) in (
                    ("old", (True, True, True, True)),
                    ("new", (False, False, False, False))):
                set_cal(cal == "old")
                ref_ph, ref_ph_i = m.t0_pullback_hold(sub, ei, k)
                np_ph, np_ph_i = F.t0_pullback_hold_np(m, sp, ei, k, gps, gstall,
                                                      ma60_np, t0_open, t0_close, t0_vol)
                n_ph_checked += 1
                if bool(ref_ph) != bool(np_ph):
                    n_ph_bad += 1
                    if n_ph_bad <= 5:
                        print("  ✗ 类型A %s %s %s：引擎 %s numpy %s" % (code, D, cal, ref_ph, np_ph))
                ref_rb, ref_rb_i = m.t0_rebreak(sub, ei, k)
                np_rb, np_rb_i = F.t0_rebreak_np(m, sp, ei, k, gbshrink, gbreak,
                                                ma60_np, t0_open, t0_close, t0_vol)
                n_rb_checked += 1
                if bool(ref_rb) != bool(np_rb):
                    n_rb_bad += 1
                    if n_rb_bad <= 5:
                        print("  ✗ 类型B %s %s %s：引擎 %s numpy %s" % (code, D, cal, ref_rb, np_rb))
            set_cal(False)
    dt = time.time() - t0
    print("  锚点    %d 条对拍，不一致 %d" % (n_anchor_checked, n_anchor_bad))
    print("  兜底锚  %d 条对拍，不一致 %d" % (n_act_checked, n_act_bad))
    print("  类型A   %d 条对拍，不一致 %d" % (n_ph_checked, n_ph_bad))
    print("  类型B   %d 条对拍，不一致 %d" % (n_rb_checked, n_rb_bad))
    print("  MA/前复权 最大逐点差 = %.3e（不一致计数 %d）" % (ma_maxdiff, n_ma_bad))
    print("  对拍耗时 %.1fs" % dt)

    ok = (bad == 0 and n_anchor_bad == 0 and n_act_bad == 0
          and n_ph_bad == 0 and n_rb_bad == 0 and n_ma_bad == 0)
    print("\n最终结论：%s" % ("全部一致 ✅" if ok else "存在差异 ❌"))


if __name__ == "__main__":
    main()
