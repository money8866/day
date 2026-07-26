import json

d = json.load(open(r'D:\mystock\cache_daily\theme_stock_map_v2_20260724.json'))
themes = d.get('themes', d)

lines = []
stock_code = '002536.SZ'

# 获取该股票的完整信息
for tname, stocks in themes.items():
    if isinstance(stocks, list):
        for s in stocks:
            code = s.get('code', '') if isinstance(s, dict) else s
            if code == stock_code and isinstance(s, dict):
                lines.append(f'主题: {tname}')
                for k, v in s.items():
                    lines.append(f'  {k}: {v}')
                lines.append('')

# 看 theme_config.json 的 AI_COMPUTE
try:
    tc = json.load(open(r'D:\mystock\solo\theme_kg_v3\theme_kg_v3\config\theme_config.json', encoding='utf-8'))
    ai = tc.get('AI_COMPUTE', {})
    lines.append('=== AI_COMPUTE config ===')
    for k, v in ai.items():
        if k in ('name_cn', 'keywords', 'concepts'):
            lines.append(f'  {k}: {v}')
    
    # 检查AI_COMPUTE下是否有飞龙股份
    stocks_map = ai.get('stocks', [])
    if stocks_map:
        lines.append(f'  AI_COMPUTE stocks count: {len(stocks_map)}')
        has = any(s.get('code','')==stock_code for s in (stocks_map if isinstance(stocks_map,list) else []))
        lines.append(f'  飞龙在AI_COMPUTE中: {has}')
except Exception as e:
    lines.append(f'error: {e}')

open(r'd:\mystock\solo\output\stock_detail.txt', 'w', encoding='utf-8').write('\n'.join(lines))
print('ok')
