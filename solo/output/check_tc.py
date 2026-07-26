import json
tc = json.load(open(r'D:\mystock\solo\theme_kg_v3\theme_kg_v3\config\theme_config.json', encoding='utf-8'))
lines = []
for key, val in tc.items():
    cn = val.get('name_cn', '')
    kws = val.get('keywords', [])
    stocks = val.get('stocks', [])
    # 只取前3个关键词
    lines.append(f'{key:25s} {cn:10s} keywords={str(kws[:5])} stocks={len(stocks) if isinstance(stocks,list) else "N/A"}')
open(r'd:\mystock\solo\output\theme_config_summary.txt', 'w', encoding='utf-8').write('\n'.join(lines))
print('ok')
