import json, sys
sys.stdout.reconfigure(encoding='utf-8')
d = json.load(open('D:/mystock/dragon/cache/scan_20260525.json', encoding='utf-8'))
for s in d.get('dragons', [])[:10]:
    print(s['code'], s['name'])
