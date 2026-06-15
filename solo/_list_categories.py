import json
d = json.load(open('theme3.json', encoding='utf-8'))
cats = d['CATEGORIES']

print('=== 一级主题列表 (共 {} 个) ===\n'.format(len(cats)))
for i, (key, cat) in enumerate(cats.items(), 1):
    name = cat.get('name', key)
    desc = cat.get('desc', '')
    sub_themes = list(cat.get('themes', {}).keys())
    total_stocks = sum(len(v.get('stocks', [])) for v in [cat])  # just sub theme count
    print(f"{i}. 【{key}】")
    print(f"   描述: {desc}")
    print(f"   子主题数: {len(sub_themes)}")
    print(f"   子主题: {', '.join(sub_themes)}")
    print()
