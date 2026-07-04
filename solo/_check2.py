import json
d = json.load(open(r'd:\mystock\cache_daily\theme_stock_map_latest.json', 'r', encoding='utf-8'))
for s in d['themes'].get('煤炭链', []):
    if s['name'] in ['北元化工', '君正集团']:
        print(f"{s['name']}({s['code']}) 行业:{s['industry']} via:{s['via']} score:{s['score']}")

# 也检查主营业务
mb = json.load(open(r'd:\mystock\cache_daily\stock_company_mainbiz.json', 'r', encoding='utf-8'))
for code in ['601216.SH', '601568.SH']:
    if code in mb:
        print(f"  {code} mainbiz: {mb[code][:80]}")
