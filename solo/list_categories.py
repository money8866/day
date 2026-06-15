import json

data = json.load(open('theme2.json', 'r', encoding='utf-8'))
cats = data.get('CATEGORIES', {})
print(f'一级目录数: {len(cats)}')
for k, v in cats.items():
    print(f'  {k} ({v.get("name", "")}): {len(v["themes"])}个主题')
    for t in v['themes'].keys():
        print(f'    - {t}')
