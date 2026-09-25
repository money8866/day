"""三花聚顶研究 - 数据审计第三轮：parquet / stock_basic / trade_cal / regime 输出 / HVT 输出"""
import os
import glob

CD = r"D:\mystock\cache_daily"
SOLO = r"d:\mystock\solo"

print("=== parquet files in cache_daily ===")
n = 0
for p in glob.glob(os.path.join(CD, "**", "*.parquet"), recursive=True):
    print("  ", os.path.relpath(p, CD), os.path.getsize(p))
    n += 1
    if n > 60:
        print("   ...(truncated)")
        break
print("  total parquet:", len(glob.glob(os.path.join(CD, '**', '*.parquet'), recursive=True)))

print("\n=== market_regime_v3\\output ===")
p = os.path.join(SOLO, "market_regime_v3", "output")
if os.path.isdir(p):
    for f in sorted(os.listdir(p)):
        print("  ", f, os.path.getsize(os.path.join(p, f)))

print("\n=== hvt_bull dir ===")
for f in sorted(os.listdir(os.path.join(SOLO, "hvt_bull"))):
    print("  ", f)

print("\n=== report_daily (solo) ===")
for f in sorted(os.listdir(os.path.join(SOLO, "report_daily"))):
    print("  ", f)

print("\n=== cache_daily: hvt / ige / sli / theme_heat related ===")
for f in sorted(os.listdir(CD)):
    low = f.lower()
    if any(k in low for k in ("hvt", "ige", "sli", "theme_heat", "report_daily")):
        print("  ", f)

print("\n=== D:\\mystock\\report_daily (global) ===")
p = r"D:\mystock\report_daily"
if os.path.isdir(p):
    fs = sorted(os.listdir(p))
    print("  count:", len(fs))
    for f in fs[:40]:
        print("  ", f)
