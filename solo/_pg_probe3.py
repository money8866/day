# -*- coding: utf-8 -*-
"""探针3: index_weight 历史深度（成分股月度快照）"""
import os
os.environ.setdefault("TUSHARE_TOKEN", "")
try:
    from dotenv import load_dotenv
    load_dotenv(r"D:\mystock\config\.env")
except Exception:
    pass
import tushare as ts
pro = ts.pro_api()

for code in ("932000.CSI", "000852.SH"):
    print("=" * 50)
    print(code)
    got = []
    for y in range(2019, 2027):
        for md in ("0331", "0930"):
            d = f"{y}{md}"
            try:
                df = pro.index_weight(index_code=code, start_date=d, end_date=d)
                if df is not None and len(df):
                    got.append((d, len(df)))
            except Exception as e:
                got.append((d, f"ERR {e}"))
    print("probe:", got)
    # 完整拉一段看最早
    try:
        df = pro.index_weight(index_code=code, start_date="20180101", end_date="20260930")
        print("full rows:", len(df), "min:", df["trade_date"].min(), "max:", df["trade_date"].max())
        print("snapshot dates:", sorted(df["trade_date"].unique())[:8], "...",
              sorted(df["trade_date"].unique())[-4:])
        print("n dates:", df["trade_date"].nunique())
    except Exception as e:
        print("full FAIL:", e)
