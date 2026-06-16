import sys, time, pandas as pd, glob, os
sys.path.insert(0, '.')
from data_fetcher import load_cache, get_cache_dir, get_cache_path
from pathlib import Path

cache_dir = Path('cache')
start_year = str(2026 - 5)  # 2021
print(f"检查 cache_key 格式: income_XXX_{start_year}_None")

# 统计各类缓存文件
income_csv = glob.glob(str(cache_dir / f"income_*_{start_year}_None.csv"))
income_pq  = glob.glob(str(cache_dir / f"income_*_{start_year}_None.parquet"))
balance_csv = glob.glob(str(cache_dir / f"balance_*_{start_year}_None.csv"))
forecast_csv = glob.glob(str(cache_dir / f"forecast_*.csv"))

print(f"income_*_{start_year}_None.csv: {len(income_csv)} 个")
print(f"income_*_{start_year}_None.parquet: {len(income_pq)} 个")
print(f"balance_*_{start_year}_None.csv: {len(balance_csv)} 个")
print(f"forecast_*.csv: {len(forecast_csv)} 个")
print()

# 测试单个文件加载速度
if income_csv:
    # 加载100个文件做基准
    t0 = time.time()
    for f in income_csv[:100]:
        df = pd.read_csv(f)
    elapsed = time.time() - t0
    per_file = elapsed / 100
    total_estimate = per_file * 5297 * 3  # 3 APIs
    print(f"单CSV文件读取: {per_file*1000:.1f}ms")
    print(f"100个文件耗时: {elapsed:.1f}s")
    print(f"预计5297只股票全部读取(×3 APIs): {total_estimate/60:.1f}分钟")
    print(f"其中一个样例: {income_csv[0]}, 行数: {len(df)}")

# 测试 load_cache 函数速度
print()
print("测试 load_cache 函数...")
t0 = time.time()
for i in range(100):
    ts_code = income_csv[i].split('income_')[1].split(f'_{start_year}')[0]
    key = f"income_{ts_code}_{start_year}_None"
    df = load_cache(cache_dir, key, expire_hours=168)
elapsed = time.time() - t0
print(f"load_cache 100次: {elapsed:.1f}s ({elapsed/100*1000:.1f}ms/次)")
print(f"预计全部: {elapsed/100*5297*3/60:.1f}分钟")
