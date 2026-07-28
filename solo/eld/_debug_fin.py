"""调试财务数据"""
import os, sys, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_env_path = r"D:\mystock\config\.env"
if os.path.exists(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("TUSHARE_TOKEN="):
                os.environ["TUSHARE_TOKEN"] = line.split("=", 1)[1].strip()

try:
    from eld.datasource import EldDataSource
    from eld.cache import EldCache
    from eld.config import get_config
    cfg = get_config()
    cache = EldCache(cfg.cache)
    ds = EldDataSource(cfg.tushare.token, cache)
    
    # 通过 tushare 直接调用
    import tushare as ts
    pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])
    print("=== Direct API ===")
    df = pro.fina_indicator(ts_code="688110.SH", end_date="20260630", 
        fields="ts_code,end_date,roe,roic,gross_margin,profit_dedt,or_yoy,ocf_to_orp,debt_to_assets")
    if df is not None:
        print(f"API返回: {len(df)}条")
        if len(df) > 0:
            print(dict(df.iloc[0]))
    else:
        print("API返回 None")
    
    print("\n=== ds._call_api ===")
    df2 = ds._call_api("fina_indicator", ts_code="688110.SH", end_date="20260630",
        fields="ts_code,end_date,roe,roic,gross_margin,profit_dedt,or_yoy,ocf_to_orp,debt_to_assets")
    if df2 is not None:
        print(f"_call_api返回: {len(df2)}条")
        if len(df2) > 0:
            print(dict(df2.iloc[0]))
    else:
        print("_call_api返回 None")
    
    print("\n=== ds.get_financial ===")
    fin = ds.get_financial("688110.SH")
    if fin:
        print(f"roe={fin.roe}, revenue_yoy={fin.revenue_yoy}, gross_margin={fin.gross_margin}")
        print(f"revenue={fin.revenue}, net_profit={fin.net_profit}")
    else:
        print("get_financial返回 None")
        
except Exception as e:
    traceback.print_exc()
    print(f"Error: {e}", flush=True)
