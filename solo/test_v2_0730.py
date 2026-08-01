"""测试V2版本：先跑7月30日（信号日），再跑后续日期看回踩买点"""
import sys, os
sys.path.insert(0, r"d:\mystock\solo")
os.chdir(r"d:\mystock\solo")

from dotenv import load_dotenv
load_dotenv(r"d:\mystock\config\.env")

# 清空观察池
WATCHLIST_FILE = r"d:\mystock\cache_daily\VolMaSync_WatchList.json"
if os.path.exists(WATCHLIST_FILE):
    os.remove(WATCHLIST_FILE)
    print("已清空旧观察池")

import importlib.util
spec = importlib.util.spec_from_file_location("tushare_quant", r"d:\mystock\solo\tushare_quant.py")
tq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tq)

# 重新加载V2模块
spec2 = importlib.util.spec_from_file_location("vol_scan", r"d:\mystock\solo\vol_ma_sync_surge_scan.py")
vol_scan = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(vol_scan)

# 测试日期序列
test_dates = ["20260730", "20260731"]

for test_date in test_dates:
    print(f"\n{'#'*70}")
    print(f"# 测试日期: {test_date}")
    print(f"{'#'*70}\n")
    
    # 关键：同时修改两个模块的TRADE_DATE
    tq.TRADE_DATE = test_date
    vol_scan.tq.TRADE_DATE = test_date
    
    # 运行选股
    obs, buys = vol_scan.daily_scan(observe_threshold=75, buy_threshold=60)
    
    print(f"\n>>> {test_date} 结果: 观察信号{len(obs)}只, 买点信号{len(buys)}只")
    if buys:
        print("\n买点详情:")
        for b in buys:
            print(f"  {b['code']} {b['name']} | 信号日{b['signal_date']} T+{b['buy_offset']} | "
                  f"回踩{b['touch_ma']} | 买价{b['buy_price']} | 量缩{b['vol_ratio']} | "
                  f"当日涨跌{b['pct_on_buy_day']}% | 评分{b['buy_score']}")
