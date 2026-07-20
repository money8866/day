import json

with open('D:/mystock/cache_daily/theme_stock_map_latest.json', encoding='utf-8') as f:
    d = json.load(f)

themes = d.get('themes', {})
matched = []
for theme, stocks in themes.items():
    for stock in stocks:
        if stock.get('code') == '002440.SZ':
            matched.append((theme, stock))

if matched:
    print(f'找到 {len(matched)} 个主题匹配:')
    for theme, stock in matched:
        print(f'  主题: {theme}')
        print(f'  匹配方式: {stock.get("via", "")}')
        print(f'  产业链距离: {stock.get("chain_distance", "")}')
        print(f'  行业匹配: {stock.get("industry_match", "")}')
        print(f'  主题分: {stock.get("score", "")}')
        print(f'  IRS层: {stock.get("irs_layer", "")}')
        print(f'  行业: {stock.get("industry", "")}')
        print(f'  概念: {", ".join(stock.get("concepts", [])[:5])}...')
        print()
else:
    print('002440.SZ 未匹配到任何主题')
