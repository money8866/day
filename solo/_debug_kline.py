import pandas as pd
import os
import glob

# 看一个有代表性的股票
fpath = r"D:\mystock\solo\cache_daily\600519.SH.csv"
if not os.path.exists(fpath):
    # 找任意文件
    files = glob.glob(r"D:\mystock\solo\cache_daily\*.csv")
    if files:
        fpath = files[0]
        print(f"Using: {fpath}")
    else:
        print("No CSV files!")
        exit()

df = pd.read_csv(fpath)
print(f"Columns: {df.columns.tolist()}")
print(f"Shape: {df.shape}")
print(f"\nLast 5 rows:")
print(df.tail())
print(f"\nAmount field stats:")
if "amount" in df.columns:
    print(df["amount"].describe())
else:
    for c in df.columns:
        if "amount" in c.lower() or "vol" in c.lower() or "turnover" in c.lower():
            print(f"  {c}: {df[c].describe()}")
