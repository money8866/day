"""排查 cache_daily CSV 数据"""
import os
import pandas as pd

d = "d:/mystock/cache_daily"
files = sorted([f for f in os.listdir(d) if f.startswith("daily_") and f.endswith(".csv")])
print(f"Daily CSV files: {len(files)}")
print(f"Range: {files[0]} ~ {files[-1]}")

# Check latest file
fpath = os.path.join(d, files[-1])
df = pd.read_csv(fpath)
print(f"\nLatest file {files[-1]}: {len(df)} rows, columns={list(df.columns)}")
print(f"First 5 ts_codes: {df['ts_code'].head(5).tolist()}")

# Check if 002759.SZ is in any file
print("\n002759.SZ in CSVs:")
count = 0
for f in files:
    fpath = os.path.join(d, f)
    df = pd.read_csv(fpath)
    if "002759.SZ" in df["ts_code"].values:
        row = df[df["ts_code"] == "002759.SZ"].iloc[0]
        print(f"  {f}: close={row.get('close','?')}, vol={row.get('vol','?')}")
        count += 1
print(f"Total days for 002759.SZ: {count}")

# Check how many stocks per file on average
print(f"\nStock count per file sample:")
for f in files[:5]:
    fpath = os.path.join(d, f)
    df = pd.read_csv(fpath)
    print(f"  {f}: {len(df)} stocks")
for f in files[-5:]:
    fpath = os.path.join(d, f)
    df = pd.read_csv(fpath)
    print(f"  {f}: {len(df)} stocks")
