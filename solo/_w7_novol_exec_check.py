# -*- coding: utf-8 -*-
"""量化「二波信号日收盘价入场」的成交可行性。

口径：回测收益基准价 = 信号日(D0)前复权收盘（_w7_novol_eval.py L333）。
用户确认执行方式 = T0 后等待二波机会（信号日）入场，不是 T+1 追。
本脚本检验：信号日当天的涨幅分布——当日涨幅越大，收盘价越难按回测假设成交。
"""
import os
import sys

import pandas as pd

SOLO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SOLO)
import w7_second_wave_engine as m  # noqa: E402

OUT = os.path.join(SOLO, "_w7_novol_out")
df = pd.read_csv(os.path.join(OUT, "eval_signals.csv"))
df = df[df.caliber == "new"].copy()  # 只看 new（信号量大的口径）

reader = m.CacheReader()


def prev_close(code, date):
    """信号日前一交易日的前复权收盘。"""
    try:
        d = reader.bars_sql(code, str(date))
    except Exception:
        return None
    if d is None or len(d) < 2:
        return None
    d = d.reset_index(drop=True)
    px = m._qfq_price(d)
    i = d.index[d.trade_date.astype(str) == str(date)]
    if len(i) == 0:
        return None
    i = i[0]
    if i < 1:
        return None
    return float(px.iloc[i - 1])


rows = []
for _, r in df.iterrows():
    pc = prev_close(r["code"], r["date"])
    if pc and pc > 0:
        chg = (r["close"] / pc - 1) * 100
        rows.append({"state": r["state"], "chg": chg, "ret3": r.get("ret3"),
                     "mkt3": r.get("mkt3"), "ret10": r.get("ret10"), "mkt10": r.get("mkt10")})
s = pd.DataFrame(rows)
s["band"] = pd.cut(s.chg, [-99, 0, 3, 5, 9, 11, 99],
                   labels=["<0（收跌）", "(0,3%]", "(3,5%]", "(5,9%]", "(9,11%]", ">11%（≈涨停/大阳）"])


def perf(g):
    if len(g) == 0:
        return None
    ex3 = g.ret3 - g.mkt3
    return pd.Series({
        "n": len(g),
        "ret3": round(g.ret3.mean(), 2),
        "ex3": round(ex3.mean(), 2),
        "win3": round((g.ret3.dropna() > 0).mean() * 100, 1),
    })


print("=" * 80)
print("二波信号日当天涨幅分布（new 口径，%d 条）" % len(s))
print("=" * 80)
for st in ("T0_PULLBACK_HOLD", "T0_REBREAK"):
    sub = s[s.state == st]
    print("\n【%s】%d 条，信号日涨幅分布：" % (st, len(sub)))
    vc = sub.band.value_counts().reindex(sub.band.cat.categories).fillna(0)
    for b, n in vc.items():
        print("  %-18s %5d (%.1f%%)" % (b, n, 100.0 * n / len(sub)))
    print("  —— 按信号日涨幅分档的事后表现：")
    g = sub.groupby("band", observed=True).apply(perf, include_groups=False).dropna(how="all")
    print("  %-18s %5s %8s %9s %8s" % ("档", "n", "T+3", "T+3超额", "胜率3"))
    for b, row in g.iterrows():
        print("  %-18s %5d %+8.2f %+9.2f %7.1f%%" % (
            b, int(row["n"]), row["ret3"], row["ex3"], row["win3"]))
