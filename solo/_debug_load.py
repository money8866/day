# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, r"d:\mystock\solo")
import pandas as pd
from dotenv import load_dotenv
import tushare as ts

load_dotenv(r"d:/mystock/config/.env")
pro = ts.pro_api(os.getenv("TUSHARE_TOKEN"))

ETF_CONS_DIR = r"D:\mystock\cache_daily\etf_cons"
os.makedirs(ETF_CONS_DIR, exist_ok=True)

ts_code = "688409.SH"
end_date = "20260430"
cache_fp = os.path.join(ETF_CONS_DIR, f"stock_{ts_code.replace('.','_')}_{end_date}.csv")

print(f"cache_fp: {cache_fp}")
print(f"缓存存在? {os.path.exists(cache_fp)}")

if os.path.exists(cache_fp):
    df = pd.read_csv(cache_fp)
    print(f"缓存数据: {len(df)} 行")
    print(df.head())
else:
    # 从Tushare下载
    print("从Tushare下载...")
    import time
    df = pro.daily(ts_code=ts_code, start_date="20260101", end_date=end_date)
    print(f"下载: {len(df) if df is not None else 0} 行")
    if df is not None and not df.empty:
        df.to_csv(cache_fp, index=False)
        print(f"已保存到 {cache_fp}")
    time.sleep(0.12)
