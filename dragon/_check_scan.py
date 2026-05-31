import json, sys
sys.stdout.reconfigure(encoding='utf-8')
with open('D:/mystock/dragon/cache/scan_20260525.json', 'r', encoding='utf-8') as f:
    d = json.load(f)
print('Keys:', list(d.keys()))
dragons = d.get('dragons', {})
print('Dragons type:', type(dragons).__name__)
if isinstance(dragons, dict):
    print('Dragons keys:', list(dragons.keys()))
    for k, v in dragons.items():
        print(f'  {k}: {type(v).__name__}, len={len(v) if hasattr(v,"__len__") else "N/A"}')
        if isinstance(v, list) and v:
            print(f'    First: {str(v[0])[:200]}')
elif isinstance(dragons, list):
    print('Dragons len:', len(dragons))
    if dragons:
        print('First:', str(dragons[0])[:200])
