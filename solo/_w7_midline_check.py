"""一致性交叉验证：引擎 w7_second_wave_engine.midline_hold  vs  _w7_midline.signal_at。

对全池抽样逐日比对「命中/未命中」与「命中的长阳日 index」，不一致即打印。
"""
import os
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _w7_midline as M
from w7_second_wave_engine import midline_hold

DB = M.DB
START = os.environ.get("CHECK_START", "20250601")
END = os.environ.get("CHECK_END", "20260930")
STEP = int(os.environ.get("CHECK_STEP", "1"))  # 抽稀：>1 时每 N 个交易日核一次
CODES = int(os.environ.get("CHECK_CODES", "400"))


def main():
    conn = sqlite3.connect(DB)
    print("加载日线...", flush=True)
    frames = M.load_bars(conn, since="20250101", stride=int(os.environ.get("CHECK_STRIDE", "8")))
    for f in frames.values():
        f["idx"] = {d: i for i, d in enumerate(f["date"])}
    print(f"  代码数={len(frames)}", flush=True)

    p = dict(M.DEFAULTS)
    codes = sorted(frames.keys())[:CODES]
    tot = sig = bad = 0
    mism = []
    dates_all = sorted({d for c in codes for d in frames[c]["date"] if START <= d <= END})[::STEP]
    print(f"比对区间 {dates_all[0]}~{dates_all[-1]}（{len(dates_all)} 个交易日 × {len(codes)} 只）", flush=True)
    for code in codes:
        f = frames[code]
        M.prep(f, p)
        df = pd.DataFrame({
            "trade_date": f["date"], "open": f["o"], "high": f["h"], "low": f["l"],
            "close": f["c"], "pct_chg": f["pc"], "vol": f["v"],
        })
        for d in dates_all:
            i = f["idx"].get(d)
            if i is None or i < M.MIN_BARS:
                continue
            a = midline_hold(df, i)[1]
            b = M.signal_at(f, p, i)
            tot += 1
            if a >= 0:
                sig += 1
            if a != b:
                bad += 1
                if len(mism) < 20:
                    mism.append((code, d, a, b))
    print(f"比对完毕：样本={tot}  引擎命中={sig}  不一致={bad}", flush=True)
    for m in mism:
        print("  MISMATCH", m, flush=True)


if __name__ == "__main__":
    main()
