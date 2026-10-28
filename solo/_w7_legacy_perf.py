# -*- coding: utf-8 -*-
"""301550.SZ 旧口径信号的事后表现核对（BREAKOUT_RETEST / MIDLINE_HOLD 命中日）。

对 _w7_replay_legacy.py 命中的日子，计算：
  - 该日实际状态（新口径主链）
  - 旧形态的失效位 / 触发价
  - T+1/T+3/T+5/T+10 绝对收益与相对上证超额
  - 是否触及失效位
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
    reader.load_fina()
    code = "301550.SZ"
    start = "20260801"

    idx = pd.read_sql_query(
        "SELECT trade_date, close FROM index_daily_cache WHERE ts_code=? ORDER BY trade_date",
        reader.conn, params=(mod.MARKET_INDEX_CODE,))
    idx["trade_date"] = idx.trade_date.astype(str)
    idx["close"] = pd.to_numeric(idx.close, errors="coerce")
    idx_map = dict(zip(idx.trade_date, idx.close))
    idx_dates = list(idx.trade_date)

    q = ("SELECT DISTINCT trade_date FROM daily_cache WHERE ts_code=? AND trade_date>=? ORDER BY trade_date")
    dates = [r[0] for r in reader.conn.execute(q, (code, start))]

    full = reader.bars_sql(code, "20261028")
    alld = list(full.trade_date.astype(str))
    cmap = dict(zip(alld, full.close.astype(float)))
    lowmap = dict(zip(alld, full.low.astype(float)))

    rows = []
    for d in dates:
        df = reader.bars_sql(code, d)
        if len(df) < mod.MIN_BARS:
            continue
        end = len(df) - 1
        anchor = mod.find_event_anchor(df) or mod.find_activation_anchor(df)
        if not anchor:
            continue
        ei, ep = anchor
        ev_close = mod.finite(df.iloc[ei].close)
        latest = df.iloc[end]

        tags = []
        ok, bi = mod.breakout_retest(df, ei, end, latest, ev_close)
        if ok and bi is not None and bi >= 0:
            b = df.iloc[bi]
            tags.append(("BREAKOUT_RETEST", str(b.trade_date), mod.finite(b.close), mod.finite(b.low)))
        ok2, bi2 = mod.midline_hold(df, end)
        if ok2 and bi2 is not None and bi2 >= 0:
            b2 = df.iloc[bi2]
            mid = (mod.finite(b2.high) + mod.finite(b2.low)) / 2
            tags.append(("MIDLINE_HOLD", str(b2.trade_date), mod.finite(b2.close), mid))
        if not tags:
            continue

        entry = mod.finite(latest.close)
        i0 = alld.index(d)
        rec = {"date": d, "close": entry, "pct_chg": mod.finite(latest.pct_chg),
               "volr": round(mod.finite(latest.vol) / mod.safe_mean(df.vol.iloc[max(0, end - 19):end]), 2)}
        for tag, bd, bclose, bline in tags:
            rec["形态"] = tag
            rec["B日"] = bd
            rec["B收盘"] = round(bclose, 2)
            rec["失效位"] = round(bline, 2)
            r = {}
            for h in (1, 3, 5, 10):
                j = i0 + h
                r["T+%d" % h] = round((cmap[alld[j]] / entry - 1) * 100, 2) if j < len(alld) else None
                ij = idx_dates.index(d)
                k = ij + h
                r["超额+%d" % h] = (round((cmap[alld[j]] / entry - 1) * 100 - (idx_map[idx_dates[k]] / idx_map[d] - 1) * 100, 2)
                                   if (j < len(alld) and k < len(idx_dates)) else None)
            hit = next((alld[j] for j in range(i0 + 1, min(i0 + 11, len(alld))) if lowmap[alld[j]] <= bline), None)
            rec.update(r)
            rec["10日内触失效位"] = hit or "未触及"
            rows.append(rec)
    df_out = pd.DataFrame(rows)
    pd.set_option("display.width", 260)
    print(df_out.to_string(index=False))
    out = os.path.join(SOLO, "_w7_replay_out", "legacy_perf_301550.csv")
    df_out.to_csv(out, index=False, encoding="utf-8-sig")
    print("\n输出：%s" % out)
    reader.close()


if __name__ == "__main__":
    main()
