import glob
from pathlib import Path

cache_dir = Path('cache')

for year in ['2021', '2022', '2023', '2024', '2025', '2026']:
    for dtype in ['income', 'balance']:
        csv = glob.glob(str(cache_dir / f"{dtype}_*_{year}_None.csv"))
        pq  = glob.glob(str(cache_dir / f"{dtype}_*_{year}_None.parquet"))
        print(f"{dtype}_*_{year}_None: {len(csv)} CSV + {len(pq)} parquet")

# forecast
fc = glob.glob(str(cache_dir / "forecast_*.csv"))
print(f"\nforecast_*: {len(fc)} CSV")

# Total distinct stocks
stocks = set()
for f in glob.glob(str(cache_dir / "income_*_None.csv")):
    parts = Path(f).stem.split('_')
    # income_000001.SZ_2021_None
    stocks.add('_'.join(parts[1:2]))
print(f"\nTotal distinct stocks with income cache: {len(stocks)}")
print(f"Sample codes: {list(stocks)[:5]}")
