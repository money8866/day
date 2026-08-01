"""测试V2版本 - 先清空观察池，然后按日期顺序验证回踩买点功能"""
import sys, os, json
sys.path.insert(0, r"d:\mystock\solo")
os.chdir(r"d:\mystock\solo")

from dotenv import load_dotenv
load_dotenv(r"d:\mystock\config\.env")

# 先清空观察池
watchlist_file = r"d:\mystock\cache_daily\VolMaSync_WatchList.json"
if os.path.exists(watchlist_file):
    os.remove(watchlist_file)
    print("已清空旧观察池")

import importlib.util
spec2 = importlib.util.spec_from_file_location("vms", r"d:\mystock\solo\vol_ma_sync_surge_scan.py")
vms = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(vms)

# 测试一个已知的成功案例：依依股份 001206.SZ 在20260708信号，T+2(0710)回踩买点后涨+11%
# 以及三美股份 603379.SH 在20260624信号，T+2(0626)回踩后涨+13.68%
# 按日期顺序运行：先0708（信号日），再0710（T+2回踩日）

test_dates = [
    ("20260708", "依依股份信号日(T日)，应加入观察池"),
    ("20260709", "T+1日"),
    ("20260710", "T+2日，依依股份应该出现回踩买点"),
]

for date, desc in test_dates:
    print("\n" + "#" * 70)
    print(f"# 交易日 {date} - {desc}")
    print("#" * 70)
    
    vms.tq.TRADE_DATE = date
    vms.tq.load_turnover_cache()
    obs, buys = vms.daily_scan()
    print(f"\n>>> 本次新观察: {len(obs)}只, 买点: {len(buys)}只")
    if buys:
        for b in buys:
            print(f"    🟢 {b['code']} {b['name']} 买点评分{b['buy_score']} "
                  f"回踩{b['touch_ma']} T+{b['buy_offset']} 买价{b['buy_price']}")
