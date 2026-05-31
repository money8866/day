import json
with open(r'C:\Users\kongx\.openclaw\config\openclaw.json', 'r', encoding='utf-8') as f:
    cfg = json.load(f)
sb = cfg.get('sandbox', {})
ap = sb.get('allowedPaths', [])
print("allowedPaths:", ap)
