"""Debug ETF data fetching"""
import sys
sys.path.insert(0, 'd:\\mystock\\solo')
from tushare_quant import pro

# Try pro.fund_daily
print("=== pro.fund_daily ===")
try:
    df = pro.fund_daily(ts_code='515980.SH', start_date='20260701', end_date='20260724')
    print(f'  shape: {df.shape}')
    if not df.empty:
        print(df.head())
except Exception as e:
    print(f'  error: {e}')

# Try common daily
print("\n=== pro.daily (as stock) ===")
try:
    df = pro.daily(ts_code='515980.SH', start_date='20260701', end_date='20260724')
    print(f'  shape: {df.shape}')
    if not df.empty:
        print(df.head())
except Exception as e:
    print(f'  error: {e}')

# Try 515980 without suffix
print("\n=== pro.daily (without suffix) ===")
try:
    df = pro.daily(ts_code='515980', start_date='20260701', end_date='20260724')
    print(f'  shape: {df.shape}')
    if not df.empty:
        print(df.head())
except Exception as e:
    print(f'  error: {e}')

# Check if fund_basic works
print("\n=== pro.fund_basic ===")
try:
    df = pro.fund_basic(ts_code='515980.SH')
    print(f'  shape: {df.shape}')
    if not df.empty:
        print(df.head())
except Exception as e:
    print(f'  error: {e}')
