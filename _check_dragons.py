import json, sys
sys.stdout.reconfigure(encoding='utf-8')
with open('D:/mystock/dragon/cache/scan_20260525.json', 'r', encoding='utf-8') as f:
    d = json.load(f)
dragons = d.get('dragons', [])
print(f'Total: {len(dragons)}')
bad = [(i, type(x).__name__, str(x)[:80]) for i, x in enumerate(dragons) if not isinstance(x, dict)]
print(f'Bad entries: {len(bad)}')
for idx, t, s in bad:
    print(f'  [{idx}] {t}: {s}')
# Also check divergence
div = d.get('divergence', [])
print(f'\nDivergence: {len(div)}, types: {set(type(x).__name__ for x in div)}')
bad_div = [(i, type(x).__name__, str(x)[:80]) for i, x in enumerate(div) if not isinstance(x, dict)]
print(f'Bad divergence: {len(bad_div)}')
