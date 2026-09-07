# -*- coding: utf-8 -*-
import sqlite3
import pandas as pd

c = sqlite3.connect(r"D:\mystock\cache_daily\stock_data.db")
for t in ("stk_factor_pro", "daily_cache", "daily_basic_cache"):
    mx = c.execute(f"select max(trade_date) from {t}").fetchone()[0]
    mn = c.execute(f"select min(trade_date) from {t}").fetchone()[0]
    print(t, "range", mn, "->", mx)

full = pd.read_csv(r"d:\mystock\solo\ige\output\ige_full_20260904.csv",
                   low_memory=False)
pool = full[(full["t120_rocket_core"] == True)]
print("pool", len(pool))
codes = tuple(pool["code"].tolist())
q = f"""select ts_code, trade_date, close, pct_chg, open, high, low, vol,
        turnover_rate, volume_ratio, ma_bfq_5, ma_bfq_10, ma_bfq_20,
        ma_bfq_60, ma_bfq_250, macd_bfq, macd_dif_bfq, macd_dea_bfq,
        rsi_bfq_6, rsi_bfq_12, kdj_k_bfq, kdj_d_bfq,
        total_mv, circ_mv
        from stk_factor_pro where ts_code in ({','.join('?'*len(codes))})
        order by trade_date desc"""
df = pd.read_sql(q, c, params=list(codes))
print("rows", len(df))
print("per-code latest date coverage:")
dmax = df.groupby("ts_code")["trade_date"].max()
print("max dates:", dmax.value_counts().head().to_dict())
print("top latest rows sample:")
top = df.loc[df.groupby("ts_code")["trade_date"].idxmax()]
print(top[["ts_code", "trade_date", "close", "pct_chg", "ma_bfq_20",
           "volume_ratio"]].head(8).to_string())
c.close()
