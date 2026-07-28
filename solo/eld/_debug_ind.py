"""调试行业数据"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_env_path = r"D:\mystock\config\.env"
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("TUSHARE_TOKEN="):
                os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip()

from eld.datasource import EldDataSource
from eld.cache import EldCache
from eld.config import get_config
cfg = get_config()
cache = EldCache(cfg.cache)
ds = EldDataSource(cfg.tushare.token, cache)

# 1. 直接测试 index_classify
import tushare as ts
pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])
df = pro.index_classify(level="L1", src="SW", fields="index_code,industry_name")
print(f"index_classify: {len(df) if df is not None else 'None'}条")
if df is not None and len(df) > 0:
    print(list(df["industry_name"].values[:10]))

# 2. get_industry_data
print("\nget_industry_data...")
data = ds.get_industry_data()
print(f"  获取到 {len(data)} 个行业")
if data:
    for name, score in list(data.items())[:5]:
        print(f"  {name}: {score:.1f}")

# 3. Stock industry
for code in ["688110.SH", "300001.SZ"]:
    sb = ds.get_stock_basic(code)
    print(f"\n{code}: industry='{sb.industry if sb else 'N/A'}'")
    if sb and sb.industry:
        # 检查行业名是否在热度数据中
        for ind_name in data:
            if sb.industry in ind_name or ind_name in sb.industry:
                print(f"  -> 匹配 '{ind_name}', rank={ds.get_industry_rank(code)}")
                break
        else:
            print(f"  -> 未匹配到任何行业")
