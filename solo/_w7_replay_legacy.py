# -*- coding: utf-8 -*-
"""旧口径 5 形态对单票逐日 as-of 复现（BREAKOUT_RETEST / MIDLINE_HOLD / SHAKEOUT_HOLD /
RIGHT_BOTTOM / SQUAT_SHRINK），用于回答「旧 W7 会给这只票什么信号」。

这些函数仍留在 w7_second_wave_engine 内，但已退出主链可操作集合，
本脚本直接调用它们（不改引擎、不落库）。
"""
import importlib.util
import os
import sys

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
    basic = reader.basic.loc[code] if code in reader.basic.index else {}
    name = str(basic.get("name", code))

    print("=== %s %s 旧口径形态逐日复现 ===" % (code, name))
    hits = []
    for d in dates:
        df = reader.bars_sql(code, d)
        if len(df) < mod.MIN_BARS:
            continue
        end = len(df) - 1
        anchor = mod.find_event_anchor(df)
        fb = False
        if not anchor:
            anchor = mod.find_activation_anchor(df)
            fb = True
        if not anchor:
            continue
        ei, ep = anchor
        lv = mod._t0_levels(df, ei)
        ev_close = mod.finite(df.iloc[ei].close)
        found = []

        # BREAKOUT_RETEST（需 T0 锚；latest 传当日行）
        try:
            ok, bi = mod.breakout_retest(df, ei, end, df.iloc[end], ev_close)
            if ok and bi is not None and bi >= 0:
                found.append("BREAKOUT_RETEST(B=%s close=%.2f)" % (df.iloc[bi].trade_date, df.iloc[bi].close))
        except Exception as exc:
            found.append("BREAKOUT_RETEST_ERR:%r" % exc)
        # MIDLINE_HOLD
        try:
            ok, bi = mod.midline_hold(df, end)
            if ok and bi is not None and bi >= 0:
                found.append("MIDLINE_HOLD(B=%s)" % df.iloc[bi].trade_date)
        except Exception as exc:
            found.append("MIDLINE_HOLD_ERR:%r" % exc)
        # SHAKEOUT_HOLD
        try:
            ok, info = mod.shakeout_hold(df, end)
            if ok:
                found.append("SHAKEOUT_HOLD(%s)" % json.dumps({k: (round(v, 2) if isinstance(v, float) else v)
                                                           for k, v in (info or {}).items()}, ensure_ascii=False))
        except Exception as exc:
            found.append("SHAKEOUT_HOLD_ERR:%r" % exc)
        # RIGHT_BOTTOM
        try:
            ok, info = mod.right_bottom(df, end, ei)
            if ok:
                found.append("RIGHT_BOTTOM(%s)" % json.dumps({k: (round(v, 2) if isinstance(v, float) else v)
                                                           for k, v in (info or {}).items()}, ensure_ascii=False))
        except Exception as exc:
            found.append("RIGHT_BOTTOM_ERR:%r" % exc)
        # SQUAT_SHRINK
        try:
            ok, info = mod.squat_shrink(df, ei, end)
            if ok:
                found.append("SQUAT_SHRINK(%s)" % json.dumps({k: (round(v, 2) if isinstance(v, float) else v)
                                                            for k, v in (info or {}).items()}, ensure_ascii=False))
        except Exception as exc:
            found.append("SQUAT_SHRINK_ERR:%r" % exc)
        if found:
            hits.append((d, df.iloc[end].close, found))
            print("%s close=%.2f  %s" % (d, df.iloc[end].close, " | ".join(found)))
    print("\n合计命中日：%d" % len(hits))
    reader.close()


if __name__ == "__main__":
    import json
    main()
