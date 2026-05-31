import json, sys
sys.stdout.reconfigure(encoding='utf-8')
d = json.load(open('D:/mystock/dragon/cache/scan_20260525.json', encoding='utf-8'))
dg = d.get('dragons', [])
if dg:
    print('dragons[0] keys:', list(dg[0].keys()))
    print('dragons[0]:', dg[0])
print()
div = d.get('divergence', {})
print('divergence:', div)
