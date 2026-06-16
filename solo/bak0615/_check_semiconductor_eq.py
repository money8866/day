import json

with open('d:/mystock/solo/cache_backbone_tushare/theme3_constituents_20260615.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

ths = [t for t in data['themes'] if t['theme_name'] == '半导体设备']
if ths:
    stocks = ths[0]['stocks']
    print(f"半导体设备股票数: {len(stocks)}")
    print("\n成分股列表:")
    for s in stocks[:20]:
        print(f"  {s['name']}({s['ts_code']})")
else:
    print("未找到半导体设备主题")
