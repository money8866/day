# -*- coding: utf-8 -*-
"""诊断：T0 锚为何不更新——逐日列出 find_event_anchor 的候选事件与分位。"""
import importlib.util
import os
import sys

import numpy as np
import pandas as pd

SOLO = r"d:\mystock\solo"
ENGINE = os.path.join(SOLO, "w7_second_wave_engine.py")


def load_engine():
    spec = importlib.util.spec_from_file_location("w7eng", ENGINE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["w7eng"] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    mod = load_engine()
    reader = mod.CacheReader()
    code = sys.argv[1] if len(sys.argv) > 1 else "301550.SZ"
    start = sys.argv[2] if len(sys.argv) > 2 else "20260801"
    q = ("SELECT DISTINCT trade_date FROM daily_cache WHERE ts_code=? AND trade_date>=? ORDER BY trade_date")
    dates = [r[0] for r in reader.conn.execute(q, (code, start))]

    print("=== %s 天量候选逐日（窗口=%d, MAX_EVENT_AGE=%d）===" % (code, mod.MAIN_EVENT_PCT, mod.MAX_EVENT_AGE))
    for d in dates:
        df = reader.bars_sql(code, d)
        if len(df) < mod.MIN_BARS:
            continue
        lo = max(mod.MIN_BARS, len(df) - mod.MAX_EVENT_AGE - 1)
        cands = []
        for i in range(lo, len(df) - 2):
            ok, ep = mod.extreme_event(df, i)
            if ok:
                cands.append((i, ep))
        if not cands:
            # 看窗口内的量能最高者分位
            best = None
            for i in range(lo, len(df) - 2):
                st = i - 120
                if st < 0:
                    continue
                v = mod.finite(df.vol.iloc[i])
                vols = pd.to_numeric(df.vol.iloc[st:i], errors="coerce").fillna(0).values
                pv = mod.percentile_rank(vols, v)
                if best is None or pv > best[1]:
                    best = (i, pv)
            print("%s 候选=0 | 窗口内最高量分位=%s (idx=%s, %s)"
                  % (d, ("P%.1f" % best[1]) if best else "NA",
                     str(df.trade_date.iloc[best[0]]) if best else "-",
                     ("vol=%.0f" % mod.finite(df.vol.iloc[best[0]])) if best else "-"))
            continue
        ei, ep = mod.extreme_cluster_anchor(df, cands)
        ps = mod.preshift_start_bar(df, ei)
        desc = " | ".join("%s(P%.0f)" % (df.trade_date.iloc[i], e) for i, e in cands[:6])
        print("%s 候选=%d %s → 簇锚=%s → preshift=%s  最终age=%d"
              % (d, len(cands), desc, df.trade_date.iloc[ei], df.trade_date.iloc[ps], len(df) - 1 - ps))
    reader.close()


if __name__ == "__main__":
    main()
