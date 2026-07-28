"""
调试：检查筹码数据字段
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

logging.basicConfig(level=logging.INFO, format="%(message)s")
import tushare as ts
pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])

# 测试 cyq_perf
codes = ["688110.SH", "300001.SZ", "300033.SZ"]
for code in codes:
    print(f"\n=== {code} ===")
    df = pro.cyq_perf(ts_code=code, trade_date="20260724")
    print(f"cyq_perf columns: {list(df.columns) if df is not None and len(df) > 0 else 'NO DATA'}")
    if df is not None and len(df) > 0:
        print(df.to_string())
    
    # test more dates
    for d in ["20260727", "20260724", "20260721"]:
        df2 = pro.cyq_perf(ts_code=code, trade_date=d)
        if df2 is not None and len(df2) > 0:
            print(f"  {d}: profit_ratio={df2.iloc[0].get('profit_ratio', 'N/A')}, avg_cost={df2.iloc[0].get('avg_cost', 'N/A')}")
        else:
            print(f"  {d}: 无数据")
