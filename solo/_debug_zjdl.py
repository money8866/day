import json
with open('report_daily/theme_stock_map_latest_v2.json', 'rb') as f:
    d = json.load(f)
t = d.get('themes', {})
for tname, stocks in t.items():
    for s in stocks:
        if s.get('name') == '浙江鼎力':
            print(f'{tname}: via={s.get("via")} score={s.get("score")}')
            
# Also check stocks section
s2 = d.get('stocks', {})
if '603338.SH' in s2:
    info = s2['603338.SH']
    print(f'\nStocks section: themes={info.get("themes")}')
    print(f'dominant_theme={info.get("dominant_theme")}')
    print(f'dominant_score={info.get("dominant_score")}')
