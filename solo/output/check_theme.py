import json

d = json.load(open(r'D:\mystock\cache_daily\theme_stock_map_v2_20260724.json'))
themes = d.get('themes', d)

# 飞龙股份属于哪些主题
lines = []
stock_code = '002536.SZ'
matches = []
for tname, stocks in themes.items():
    if isinstance(stocks, list):
        for s in stocks:
            code = s.get('code', '') if isinstance(s, dict) else s
            if code == stock_code:
                name = s.get('name', '') if isinstance(s, dict) else ''
                score = s.get('score', '') if isinstance(s, dict) else ''
                matches.append((tname, name, score))

lines.append(f'{stock_code} 出现在以下主题:')
for t, n, s in matches:
    lines.append(f'  {t}: {n} 评分={s}')

# 看 theme_config.json 是否有更好的映射
try:
    tc = json.load(open(r'D:\mystock\solo\theme_kg_v3\theme_kg_v3\config\theme_config.json', encoding='utf-8'))
    lines.append('')
    lines.append('theme_config.json 结构:')
    if isinstance(tc, dict):
        for k in list(tc.keys())[:5]:
            lines.append(f'  key: {k}')
    elif isinstance(tc, list):
        lines.append(f'  list of {len(tc)} items')
        lines.append(str(tc[0])[:200])
except:
    lines.append('theme_config.json not found')

open(r'd:\mystock\solo\output\theme_check.txt', 'w', encoding='utf-8').write('\n'.join(lines))
print('ok')
