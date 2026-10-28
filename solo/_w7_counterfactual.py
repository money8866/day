# -*- coding: utf-8 -*-
"""反事实分析：T0 锚「滚动更新」与「不更新」两种口径下，301550.SZ 的类型A/B 信号差异。

背景：现行 find_event_anchor 只在「最近 MAX_EVENT_AGE=60 根」内找 P99 天量，
      再 preshift 前移到起涨长阳。301550 的 P99 天量唯一落在 20260812
      （量 21.2 万手 = 该股历史最大量，分位 P100），此后 0907/1020/1023/1028
      的量能分位只有 95~97.5，无法成为新锚 → T0 永远钉在 0811，age 一路涨到 50，
      类型B（age≤15）与类型A（age≤40）相继结构性失效。

本脚本构造反事实口径：
  A) 现���口径（anchor 不更新）——复现主链
  B) 滚动口径（T0 取「最近一根放量激活日」，即 find_activation_anchor 语义，
     但不设 age 上限以外的限制，用于观察若锚随新激活日滚动会产出什么信号）
并对比两者在 20260801~20261028 的信号集合 + 事后表现。
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


def rolling_anchor(mod, df, end):
    """滚动口径：取 end 之前最近一根「放量激活长阳」为 T0（含当日）。

    复用 find_activation_anchor，但把 max_age 放大到覆盖全区间（找最新的而非最老的）。
    """
    n = len(df)
    if n < 30:
        return None
    for j in range(end, 29, -1):
        if mod.is_preshift_bar(df, j):
            return j, 0.0
    return None


def main():
    mod = load_engine()
    reader = mod.CacheReader()
    code = "301550.SZ"
    start = "20260801"

    idx = pd.read_sql_query(
        "SELECT trade_date, close FROM index_daily_cache WHERE ts_code=? ORDER BY trade_date",
        reader.conn, params=(mod.MARKET_INDEX_CODE,))
    idx["trade_date"] = idx.trade_date.astype(str)
    idx["close"] = pd.to_numeric(idx.close, errors="coerce")
    idx_map = dict(zip(idx.trade_date, idx.close))
    idx_dates = list(idx.trade_date)

    full = reader.bars_sql(code, "20261028")
    alld = list(full.trade_date.astype(str))
    cmap = dict(zip(alld, full.close.astype(float)))
    lowmap = dict(zip(alld, full.low.astype(float)))

    q = ("SELECT DISTINCT trade_date FROM daily_cache WHERE ts_code=? AND trade_date>=? ORDER BY trade_date")
    dates = [r[0] for r in reader.conn.execute(q, (code, start))]

    rows = []
    for d in dates:
        df = reader.bars_sql(code, d)
        if len(df) < mod.MIN_BARS:
            continue
        end = len(df) - 1
        # 现口径
        a_now = mod.find_event_anchor(df) or mod.find_activation_anchor(df)
        # 滚动口径
        a_roll = rolling_anchor(mod, df, end)
        for tag, anc in (("现口径", a_now), ("滚动口径", a_roll)):
            if not anc:
                continue
            ei, ep = anc
            age = end - ei
            if age < 3:
                continue
            ok_b, info_b = mod.t0_rebreak(df, ei, end)
            ok_a, info_a = (False, None)
            if not ok_b:
                ok_a, info_a = mod.t0_pullback_hold(df, ei, end)
            if not (ok_b or ok_a):
                continue
            info = info_b if ok_b else info_a
            state = "T0_REBREAK" if ok_b else "T0_PULLBACK_HOLD"
            entry = mod.finite(df.iloc[end].close)
            i0 = alld.index(d)
            fail = mod.finite(info.get("t0_open"), 0.0) if state == "T0_PULLBACK_HOLD" else mod.finite(info.get("pull_low"), 0.0)
            rec = {"口径": tag, "date": d, "state": state, "T0": info.get("t0_date"),
                   "T0开": round(mod.finite(info.get("t0_open")), 2),
                   "T0收": round(mod.finite(info.get("t0_close")), 2),
                   "回踩低": round(mod.finite(info.get("pull_low")), 2),
                   "age": age, "entry": entry,
                   "volr": round(mod.finite(df.iloc[end].vol) / mod.safe_mean(df.vol.iloc[max(0, end - 19):end]), 2)}
            for h in (1, 3, 5, 10):
                j = i0 + h
                rec["T+%d" % h] = round((cmap[alld[j]] / entry - 1) * 100, 2) if j < len(alld) else None
                ij = idx_dates.index(d)
                k = ij + h
                rec["超额+%d" % h] = (round((cmap[alld[j]] / entry - 1) * 100 - (idx_map[idx_dates[k]] / idx_map[d] - 1) * 100, 2)
                                   if (j < len(alld) and k < len(idx_dates)) else None)
            hit = next((alld[j] for j in range(i0 + 1, min(i0 + 11, len(alld))) if lowmap[alld[j]] <= fail), None)
            rec["10日内触失效位"] = hit or "未触及"
            rows.append(rec)

    out = pd.DataFrame(rows)
    pd.set_option("display.width", 280)
    if out.empty:
        print("两种口径均无信号")
    else:
        print(out.to_string(index=False))
        p = os.path.join(SOLO, "_w7_replay_out", "counterfactual_301550.csv")
        out.to_csv(p, index=False, encoding="utf-8-sig")
        print("\n输出：%s" % p)
    reader.close()


if __name__ == "__main__":
    main()
