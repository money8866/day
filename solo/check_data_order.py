"""检查数据排序方向"""
import sys, os
sys.path.insert(0, r"d:\mystock\solo")
os.chdir(r"d:\mystock\solo")

from dotenv import load_dotenv
load_dotenv(r"d:\mystock\config\.env")

import importlib.util
spec = importlib.util.spec_from_file_location("tushare_quant", r"d:\mystock\solo\tushare_quant.py")
tq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tq)

tq.TRADE_DATE = "20260801"
df = tq.get_hist_data("002294.SZ")
print("前5行:")
print(df[['trade_date','close','vol']].head())
print("\n后5行:")
print(df[['trade_date','close','vol']].tail())
print(f"\n行数: {len(df)}")
print(f"trade_date是升序还是降序？第一个:{df['trade_date'].iloc[0]}, 最后一个:{df['trade_date'].iloc[-1]}")
