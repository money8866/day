import json
d = json.load(open("theme3.json", "r", encoding="utf-8"))
targets = ["光模块与CPO", "高速铜连接"]
for t in targets:
    for cat, cat_obj in d["CATEGORIES"].items():
        if t in cat_obj["themes"]:
            cfg = cat_obj["themes"][t]
            print(f"[{cat}] {t}")
            print(f"  business_dna_tags : {cfg.get('business_dna_tags', [])}")
            print(f"  weak_positive_tags: {cfg.get('weak_positive_tags', [])}")
            break
