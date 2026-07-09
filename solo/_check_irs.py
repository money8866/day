import json
d = json.load(open(r'd:\mystock\cache_daily\theme_stock_map_latest.json', 'r', encoding='utf-8'))

# 半导体设备
print("=== 半导体设备 ===")
stocks = d.get('themes', {}).get('半导体设备', [])
print(f"总数: {len(stocks)}只")
for s in stocks:
    print(f"  {s['code']} {s['name']:8s} via={s.get('via',''):20s} irs={s.get('irs_score',0):3d} layer={s.get('irs_layer',''):10s}")

# 检查目标股票
target = ['002371.SZ','688012.SH','688072.SH','300604.SZ','603690.SH',
          '688037.SH','688082.SH','688120.SH','688361.SH','300567.SZ','688147.SH',
          '688729.SH','688478.SH','603061.SH','688419.SH','688200.SH']
print(f"\n=== 目标股票主题归属 ===")
for code in target:
    stock_info = d.get('stocks', {}).get(code, {})
    themes = stock_info.get('themes', [])
    print(f"  {code} {stock_info.get('name',''):8s} themes={themes}")

# IRS 分层统计
print(f"\n=== IRS 分层统计 ===")
layer_counts = {}
for theme_name, stock_list in d.get('themes', {}).items():
    for s in stock_list:
        layer = s.get('irs_layer', '')
        layer_counts[layer] = layer_counts.get(layer, 0) + 1
for layer, count in sorted(layer_counts.items()):
    print(f"  {layer}: {count} 只")

print(f"\n总成份股: {sum(len(v) for v in d.get('themes', {}).values())} 条")
print(f"总个股: {len(d.get('stocks', {}))} 只")

# 几个关键主题的成份股数
for t in ['半导体设备', '半导体制造', '半导体材料', 'AI算力基建', '光通信', '人形机器人']:
    n = len(d.get('themes', {}).get(t, []))
    print(f"  {t}: {n}只")
