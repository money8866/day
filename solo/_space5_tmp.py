# -*- coding: utf-8 -*-
import sqlite3
import numpy as np
import pandas as pd
import w7_second_wave_engine as w7

# 报告(20260907 T20 PRIMARY_BUY)关键位: code, 名称, 突破价, 回踩区[lo,hi], 失效位, MA20, ATR, T20分, 类型
KEYS = [
    ("002452.SZ", "长高电气", 12.06, (11.68, 12.17), 11.60, 11.48, 0.38, "BREAKOUT_BUY"),
    ("002960.SZ", "青鸟智控",  8.73, (8.53, 8.79),   8.49,  8.52, 0.20, "HVT_RB_BUY"),
    ("300855.SZ", "图南股份", 35.45, (34.16, 35.84), 33.90, 33.50, 1.29, "HVT_RB_BUY"),
    ("601298.SH", "青岛港",    9.13, (8.95, 9.18),   8.91,  8.98, 0.18, "BREAKOUT_BUY"),
    ("603444.SH", "吉比特",  397.33, (385.33, 400.93), 382.93, 387.17, 12.00, "HVT_RB_BUY"),
]
END = "20260907"
con = sqlite3.connect(w7.DB_PATH)
for code, name, brk, (zl, zh), inv, ma20r, atr, btype in KEYS:
    df = w7.CacheReader().bars_sql(code, END)
    if df.empty:
        print(name, "无数据"); continue
    price = w7._qfq_price(df)          # 前复权收盘(末根=现价口径)
    high = df["high"].astype(float) * (price / df["close"].astype(float).replace(0, np.nan)).ffill()
    low = df["low"].astype(float) * (price / df["close"].astype(float).replace(0, np.nan)).ffill()
    c = float(price.iloc[-1]); d_last = str(df["trade_date"].astype(str).iloc[-1])
    n = len(df)
    ma20 = float(price.rolling(20).mean().iloc[-1]); ma60 = float(price.rolling(60).mean().iloc[-1])
    h60 = n >= 60 and float(high.iloc[-61:-1].max()) if n > 61 else None
    h120 = float(high.iloc[-121:-1].max()) if n > 121 else None
    h250 = float(high.iloc[-251:-1].max()) if n > 251 else None
    lo60 = float(low.iloc[-61:-1].min()) if n > 61 else None
    g5 = (c / float(price.iloc[-6]) - 1) * 100 if n > 6 else None
    g20 = (c / float(price.iloc[-21]) - 1) * 100 if n > 21 else None
    t1 = brk + 2.5 * atr                       # 引擎目标口径
    t_lo = zl - brk + brk - 0                    # 回踩区下沿即潜在买点
    up1 = (t1 / c - 1) * 100
    rows = []
    for lbl, v in [("近120日高", h120), ("近250日高", h250), ("近60日高", h60)]:
        if v is not None and v > c * 1.001:
            rows.append(f"{lbl}={v:.2f}(+{(v/c-1)*100:.1f}%)")
    rows.append(f"引擎目标1={t1:.2f}(+{up1:.1f}%)")
    print(f"\n═ {name}({code}) 现价{c:.2f}@{d_last} 近5日{g5 if g5 is not None else 0:+.1f}% 近20日{g20 if g20 is not None else 0:+.1f}%")
    print(f"  上行参考: {'; '.join(rows)}")
    print(f"  下方支撑: 回踩区[{zl:.2f},{zh:.2f}](距现价{(c-zl)/c*100:.1f}%~{(c-zh)/c*100:.1f}%) | MA20={ma20:.2f}({ma60 and (c/ma60-1)*100:+.0f}%对MA60) | 失效位={inv:.2f}({(c/inv-1)*100:+.1f}%) | 近60日低={lo60 and round(lo60,2)}")
    print(f"  T20 {btype} 结构分90-100 RR2.08 趋势:收于MA20{(c/ma20-1)*100:+.1f}%, 量比/换手需再取")
con.close()
