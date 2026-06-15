import sys, os, json, importlib.util
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 直接复用 theme_score_v3 的 dc_member 缓存机制
from theme_score_v3 import load_stock_basics, get_dc_members, get_stock_to_plates

stock_info = load_stock_basics()
print(f"stock_basics loaded: {len(stock_info)} rows")

# 查找这两只股票的 ts_code
targets = {"宗申动力": None, "万丰奥威": None}
for row in stock_info:
    name = row.get("name") or row.get("stock_name") or ""
    if name in targets:
        targets[name] = row.get("ts_code")
        print(f"  {name} -> ts_code={targets[name]}")

# 建立 stock_to_plates 映射
stock_to_plates = get_stock_to_plates()
print(f"stock_to_plates loaded: {len(stock_to_plates)} stocks mapped")

# 查这两只股票的东财行业/概念板块
for name, ts_code in targets.items():
    if ts_code is None:
        # fallback: 用 name 查
        for tc, plates in stock_to_plates.items():
            # name 也在 plates 映射里？看下结构
            continue
    plates = stock_to_plates.get(ts_code, {})
    print(f"\n=== {name} ({ts_code}) ===")
    print(f"  industry: {plates.get('industry', '?')}")
    print(f"  concepts: {plates.get('concepts', [])[:10]}")
