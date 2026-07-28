"""排查日线数据问题"""
import os, sys, json, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from eld.config import get_config
from eld.cache import EldCache
from eld.datasource import EldDataSource
from datetime import datetime, timedelta

cfg = get_config()
cache = EldCache(cfg.cache)
ds = EldDataSource(cfg.tushare.token, cache)

# 1. 检查 price_batch_cache 中是否有非 hk/fund 的日线缓存
conn = sqlite3.connect("cache/eld/eld_cache.sqlite")
c = conn.execute("SELECT ts_code FROM price_batch_cache WHERE ts_code NOT LIKE 'hk_%' AND ts_code NOT LIKE 'fund_%'")
rows = c.fetchall()
print(f"price_batch_cache 非hk/fund记录: {len(rows)}")
for r in rows[:5]:
    print(f"  {r[0]}")

# 2. 检查 benchmark_cache
c2 = conn.execute("SELECT ts_code FROM price_batch_cache WHERE ts_code LIKE '%benchmark%'")
print(f"benchmark cache: {c2.fetchall()}")

# 3. 测试一个具体股票的日线数据
ts_code = "002759.SZ"
start = "20260515"
end = "20260727"
print(f"\n测试 get_daily_data({ts_code}):")
dd = ds.get_daily_data(ts_code, start, end)
print(f"  返回 {len(dd)} 条记录")
if dd:
    print(f"  日期范围: {dd[0].trade_date} ~ {dd[-1].trade_date}")

# 4. 对比 CSV 缓存直接读取
print(f"\n直接读取 cache_daily CSV:")
csv_data = ds._daily_csv_data
if csv_data and ts_code in csv_data:
    records = csv_data[ts_code]
    in_range = [r for r in records if start <= r["trade_date"] <= end]
    print(f"  CSV 中有 {len(records)} 条, 范围内 {len(in_range)} 条")
    if in_range:
        print(f"  日期范围: {in_range[0]['trade_date']} ~ {in_range[-1]['trade_date']}")
else:
    print(f"  CSV 缓存中无 {ts_code}")

conn.close()
