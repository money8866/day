# -*- coding: utf-8 -*-
"""实时拉取 515030/512480 最近行情，与缓存对比验证"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import etf_mainline_strategy_tushare as M

for ts_code in ['515030.SH', '512480.SH', '159667.SZ', '159995.SZ']:
    df = M.pro.fund_daily(ts_code=ts_code, start_date='20260701', end_date='20260806',
                          fields='ts_code,trade_date,open,close,high,low,vol')
    if df is None or df.empty:
        print(f"{ts_code}: 无数据")
        continue
    df = df.sort_values('trade_date').reset_index(drop=True)
    print(f"\n=== {ts_code} 实时API最新5行 ===")
    print(df.tail(5).to_string(index=False))
    # 缓存对比
    cache_file = os.path.join(M.ETF_FUND_CACHE_DIR, f"{ts_code}_20260806.csv")
    cdf = M._read_cache(cache_file)
    if cdf is not None:
        cdf["trade_date"] = pd.to_datetime(cdf["trade_date"], format="%Y%m%d")
        cdf = cdf.sort_values("trade_date").reset_index(drop=True)
        api_last = df.iloc[-1]
        cache_last = cdf.iloc[-1]
        print(f"  缓存最后日期: {cache_last['trade_date'].strftime('%Y%m%d')} close={cache_last['close']} "
              f"| API最后日期: {api_last['trade_date']} close={api_last['close']} | "
              f"一致={abs(cache_last['close']-api_last['close'])<1e-6}")
