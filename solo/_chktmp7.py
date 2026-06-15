import json

data = json.load(open("theme3.json", "r", encoding="utf-8"))
low_altitude = data["CATEGORIES"].get("低空经济", {})
print(f"低空经济子主题: {list(low_altitude.get('themes', {}).keys())}")
print()

for tname, tcfg in low_altitude.get("themes", {}).items():
    print(f"=== {tname} ===")
    print(f"  business_dna_tags: {tcfg.get('business_dna_tags', [])}")
    print(f"  weak_positive_tags: {tcfg.get('weak_positive_tags', [])}")
    print(f"  core_semantic: {tcfg.get('core_semantic', [])}")
    print(f"  industry_roles: {list(tcfg.get('industry_roles', {}).keys())}")
    print(f"  negative_pressure_tags: {list(tcfg.get('negative_pressure_tags', {}).keys())}")
    print()
