import json, sys
sys.stdout.reconfigure(encoding='utf-8')
d = json.load(open('D:/mystock/dragon/cache/scan_20260525.json', encoding='utf-8'))
print('Keys:', list(d.keys()))
print('dragons type:', type(d.get('dragons')).__name__)
dg = d.get('dragons', [])
print('dragons len:', len(dg))
if dg:
    print('dragons[0]:', type(dg[0]).__name__, str(dg[0])[:120])
print('divergence type:', type(d.get('divergence')).__name__)
div = d.get('divergence', [])
print('divergence len:', len(div))
if div:
    print('divergence[0]:', type(div[0]).__name__, str(div[0])[:120])
print('sector:', d.get('sector'))
print('position:', d.get('position'))
