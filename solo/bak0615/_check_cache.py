import json

# 检查 full_market_stats 最新文件的结构
fpath = 'cache_daily/full_market_stats_20260612.json'
data = json.load(open(fpath, encoding='utf-8'))

print("="*80)
print(f"  full_market_stats 文件结构:")
print("="*80)
print(f"  顶层key: {list(data.keys())[:20]}")
print(f"  数据条数: {len(data.get('stocks', [])) if 'stocks' in data else 'N/A'}")

# 看看有没有20日/60日字段
if 'stocks' in data:
    s = data['stocks'][0]
    print(f"\n  第一条股票数据key: {list(s.keys())}")
    
    # 找包含 20/60 的key
    all_keys = set()
    for st in data['stocks'][:20]:
        all_keys.update(st.keys())
    
    print(f"\n  包含20/60相关的key:")
    for k in sorted(all_keys):
        if '20' in k or '60' in k or 'ma' in k.lower() or 'slope' in k.lower() or 'pct' in k.lower():
            print(f"    {k}")

print()

# 检查 theme3_constituents 中的字段
con = json.load(open('cache_backbone_tushare/theme3_constituents_20260612.json', encoding='utf-8'))
all_keys2 = set()
for t in con.get('themes', []):
    for s in t.get('stocks', []):
        all_keys2.update(s.keys())
        break

print(f"  theme3_constituents 中的个股字段:")
for k in sorted(all_keys2):
    print(f"    {k}")
