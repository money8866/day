import json, sys
sys.stdout.reconfigure(encoding='utf-8')
d = json.load(open('D:/mystock/dragon/cache/scan_20260525.json', encoding='utf-8'))
div = d.get('divergence', {})
print('divergence type:', type(div).__name__)
if isinstance(div, dict):
    print('divergence keys:', list(div.keys())[:10])
    for k, v in list(div.items())[:3]:
        print(f'  {k}: {type(v).__name__} -> {str(v)[:100]}')
