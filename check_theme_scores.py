import json

with open(r'D:\mystock\report_daily\theme_evolution_20260612.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

print('theme_evolution中的主题评分：')
for t in data.get('theme_table', []):
    theme = t['theme']
    score = t['score']
    print(f'  {theme}: {score}分')
