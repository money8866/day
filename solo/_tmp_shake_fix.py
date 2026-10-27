# -*- coding: utf-8 -*-
"""修正基准口径：把「起涨日当日全市场涨跌」真正算出来，回填明细 CSV（临时脚本，用完删除）。"""
import os
import sqlite3
from collections import defaultdict

import numpy as np
import pandas as pd

DB = r"D:\mystock\cache_daily\stock_data.db"
DETAIL = r"D:\mystock\solo\_tmp_shake_detail.csv"
FWD = (1, 3, 5, 10)


def main():
    conn = sqlite3.connect(DB)
    q = ("SELECT d.ts_code, d.trade_date, d.close, a.adj_factor "
         "  FROM daily_cache AS d LEFT JOIN adj_factor_cache AS a "
         "    ON a.ts_code=d.ts_code AND a.trade_date=d.trade_date "
         " WHERE d.trade_date >= '20230101'")
    df = pd.read_sql_query(q, conn)
    conn.close()
    df = df.dropna(subset=["close"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["adj_factor"] = pd.to_numeric(df["adj_factor"], errors="coerce")
    df = df[df.ts_code.str.endswith((".SZ", ".SH"), na=False)]
    df = df[~df.ts_code.str.startswith(("8", "43", "83", "87", "92"), na=False)]
    df = df.sort_values(["ts_code", "trade_date"])
    df["padj"] = df.close * df.adj_factor.ffill().bfill()
    df["ret1"] = df.groupby("ts_code")["padj"].pct_change()
    # 当日全市场等权涨跌（当日 close-to-close）
    day = df.dropna(subset=["ret1"]).groupby("trade_date")["ret1"].mean()
    day.index = day.index.astype(str)
    # 次日全市场等权涨跌（起涨日次日，用于「长阳日次日是否延续强势」）
    nxt = (df.groupby("ts_code")["ret1"].shift(-1))
    day_next = pd.DataFrame({"ts_code": df.ts_code, "trade_date": df.trade_date.astype(str),
                             "nxt": nxt}).dropna().groupby("trade_date")["nxt"].mean()
    day_next.index = day_next.index.astype(str)

    sig = pd.read_csv(DETAIL, dtype={"code": str, "date": str, "b_date": str})
    sig["mkt_same_day"] = sig["b_date"].map(day)          # 起涨日当日全市场涨跌
    sig["mkt_next_day"] = sig["b_date"].map(day_next)     # 起涨日次日全市场涨跌
    sig.to_csv(DETAIL, index=False, encoding="utf-8-sig")
    print("回填完成")
    print(f"20261023 当日全市场等权涨跌 {day.get('20261023', np.nan)*100:.2f}%  "
          f"20261026 {day.get('20261026', np.nan)*100:.2f}%")
    print(f"20261023 次日 {day_next.get('20261023', np.nan)*100:.2f}%")

    print("\n【起涨日当日市场涨跌 分档】")
    for lo, hi in [(-9, -0.005), (-0.005, 0.005), (0.005, 0.02), (0.02, 99)]:
        d = sig[(sig.mkt_same_day > lo) & (sig.mkt_same_day <= hi)]
        if len(d) == 0:
            continue
        for k in (3, 10):
            e = d[f"ex{k}"].dropna()
            print(f"  mkt_day {lo}~{hi:<6} T+{k:<2} n={len(e):>5} 超额{e.mean()*100:>6.2f}% "
                  f"胜率{(e>0).mean()*100:>5.1f}%")
        print()


if __name__ == "__main__":
    main()
