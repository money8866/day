import re
path = r'D:\mystock\etf_quant.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()
refs = re.findall(r'block\.\w+', content)
for r in sorted(set(refs)):
    print(r)
