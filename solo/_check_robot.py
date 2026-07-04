import json

with open(r'd:\mystock\cache_daily\theme_stock_map_latest.json', 'r', encoding='utf-8') as f:
    d = json.load(f)

print("=== 人形机器人 TOP20 ===")
for s in d['themes']['人形机器人'][:20]:
    print(f"  {s['name']}({s['code']}) via:{s['via']} score:{s['score']}")

print("\n=== 工业母机与自动化 TOP20 ===")
for s in d['themes']['工业母机与自动化'][:20]:
    print(f"  {s['name']}({s['code']}) via:{s['via']} score:{s['score']}")
