"""
检查财务数据和行业排名修复
"""
import logging, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_env_path = r"D:\mystock\config\.env"
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("TUSHARE_TOKEN="):
                os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from eld.config import get_config
from eld.cache import EldCache
from eld.datasource import EldDataSource
from eld.utils import get_last_trade_date

cfg = get_config()
cache = EldCache(cfg.cache)
ds = EldDataSource(cfg.tushare.token, cache)

# 测试股票
codes = ["688110.SH", "300001.SZ", "300033.SZ"]
for code in codes:
    print(f"\n=== {code} ===")
    
    # 1. Stock basic (industry)
    sb = ds.get_stock_basic(code)
    print(f"  行业: {sb.industry if sb else 'N/A'}")
    
    # 2. Financial data
    fin = ds.get_financial(code)
    if fin:
        print(f"  财务: revenue_yoy={fin.revenue_yoy:.1f}% deducted_yoy={fin.deducted_yoy:.1f}% roe={fin.roe:.1f}%")
        print(f"        revenue={fin.revenue:.0f} net_profit={fin.net_profit:.0f}")
    else:
        print(f"  财务: 无数据")
    
    # 3. Industry data
    ind_data = ds.get_industry_data()
    print(f"  行业数据总数: {len(ind_data)}")
    if sb and sb.industry and sb.industry in ind_data:
        print(f"  行业 '{sb.industry}' 热度: {ind_data[sb.industry]:.1f}")
    else:
        print(f"  行业 '{sb.industry if sb else 'N/A'}' 不在行业热度数据中")
    
    # 4. Industry rank
    rank = ds.get_industry_rank(code)
    print(f"  行业排名: {rank}")

# 检查 fina_indicator 直接调用
print("\n=== fina_indicator 直接测试 ===")
import tushare as ts
pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])
for code in ["688110.SH", "300001.SZ"]:
    df = pro.fina_indicator(ts_code=code, end_date="20260630", fields="ts_code,end_date,roe,roic,gross_margin,profit_dedt,or_yoy")
    if df is not None and len(df) > 0:
        row = df.iloc[0]
        print(f"  {code}: roe={row.get('roe','N/A')}, or_yoy={row.get('or_yoy','N/A')}, gross_margin={row.get('gross_margin','N/A')}")
    else:
        print(f"  {code}: fina_indicator 无数据")
