"""Debug: why ETF K-line is empty"""
import sys
sys.path.insert(0, 'd:\\mystock\\solo')
import theme_trend_sentiment_score as theme_ts

# Test with a single ETF
codes = ['515980.SH']
start = '20260601'
end = '20260724'

print(f"Testing get_daily_kline with {codes}...")
df = theme_ts.get_daily_kline(codes, start, end)
if df is not None:
    print(f"  df shape: {df.shape}")
    print(f"  columns: {df.columns.tolist()}")
    print(f"  head:\n{df.head()}")
else:
    print("  df is None")

# Try index_kline instead
print(f"\nTesting get_index_kline with 515980.SH...")
idx_df = theme_ts.get_index_kline('515980.SH', start, end)
if idx_df is not None:
    print(f"  idx_df shape: {idx_df.shape}")
else:
    print("  idx_df is None")

# Try without .SH suffix
print(f"\nTesting get_daily_kline with just '515980'...")
df2 = theme_ts.get_daily_kline(['515980'], start, end)
if df2 is not None:
    print(f"  df2 shape: {df2.shape}")
    print(f"  head:\n{df2.head()}")
else:
    print("  df2 is None")
