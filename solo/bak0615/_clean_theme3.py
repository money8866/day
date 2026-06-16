"""清理 theme3.json：只保留 V2 模板原始字段，去掉所有匹配辅助字段。

保留字段: theme_name, version, theme_type, core_semantic,
         industry_roles, business_dna_tags, weak_positive_tags,
         negative_pressure_tags, industry_soft_constraints,
         stock_role_mapping, matching_strategy

删除字段: industry, concept, keywords, exclude_keywords,
         core_companies, leader_companies
"""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
path = os.path.join(BASE, "theme3.json")

with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

# 要删除的字段
DROP = {
    "industry", "concept", "keywords", "exclude_keywords",
    "core_companies", "leader_companies",
}

cats = data.get("CATEGORIES", data)
stats = {"themes": 0, "fields_removed": 0}

for cat_name, cat_obj in cats.items():
    themes = cat_obj.get("themes", {}) or {}
    for theme_name, cfg in themes.items():
        before = set(cfg.keys())
        for k in DROP:
            cfg.pop(k, None)
        after = set(cfg.keys())
        removed = before - after
        stats["themes"] += 1
        stats["fields_removed"] += len(removed)
        cat_obj["themes"][theme_name] = cfg

with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"✅ 已清理 {stats['themes']} 个主题，共删除 {stats['fields_removed']} 个字段")
print(f"   保留字段: {sorted(cats['AI']['themes']['AI应用'].keys())}")
