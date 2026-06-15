"""
生成 theme3.json 的脚本（正确处理 theme2.json 的 CATEGORIES / THEME_FLAT_MAP 结构）
- 一级目录：继承 theme2.json 的 CATEGORIES 顶级键（AI/半导体/新能源/...）
- 二级目录：继承每个 category.themes 的主题名（AI算力链/半导体设备/...）
- 每个主题内容：使用 V2 元数据覆盖；未显式定义者使用默认模板
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from theme_v2_meta_part1 import THEME_V2_META_PART1
from theme_v2_meta_part2 import THEME_V2_META_PART2
from theme_v2_meta_part3 import THEME_V2_META_PART3
from theme_v2_meta_part4 import THEME_V2_META_PART4

THEME_V2_META = {}
for d in [THEME_V2_META_PART1, THEME_V2_META_PART2, THEME_V2_META_PART3, THEME_V2_META_PART4]:
    for k, v in d.items():
        THEME_V2_META[k] = v

THEME2_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "theme2.json")
THEME3_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "theme3.json")

DEFAULT_TEMPLATE = {
    "theme_name": "",
    "version": "V2",
    "theme_type": "产业链主题",
    "core_semantic": [],
    "industry_roles": {},
    "business_dna_tags": [],
    "weak_positive_tags": [],
    "negative_pressure_tags": {},
    "industry_soft_constraints": {},
    "stock_role_mapping": {
        "龙头": "产业链核心控制或订单核心企业",
        "中军": "业务覆盖面广/收入稳定",
        "补涨": "细分环节或单点业务映射"
    },
    "matching_strategy": {
        "mode": "semantic_business_hybrid",
        "embedding_weight": 0.55,
        "business_weight": 0.30,
        "industry_weight": 0.15
    }
}


def fill_theme(theme_name: str) -> dict:
    if theme_name in THEME_V2_META:
        result = dict(THEME_V2_META[theme_name])
        result["theme_name"] = theme_name
        return result
    result = json.loads(json.dumps(DEFAULT_TEMPLATE))
    result["theme_name"] = theme_name
    return result


def main():
    with open(THEME2_PATH, "r", encoding="utf-8") as f:
        theme2 = json.load(f)

    categories = theme2.get("CATEGORIES", {})

    # 保留原目录结构，只替换 themes 内容
    new_categories = {}
    new_flat_map = {}
    processed = 0
    covered = 0

    for cat_name in sorted(categories.keys()):
        cat = categories[cat_name]
        old_themes = cat.get("themes", {}) or {}
        new_themes = {}
        for theme_name in sorted(old_themes.keys()):
            cfg = fill_theme(theme_name)
            new_themes[theme_name] = cfg
            new_flat_map[theme_name] = cfg
            processed += 1
            if theme_name in THEME_V2_META:
                covered += 1

        new_categories[cat_name] = {
            "name": cat.get("name", cat_name),
            "desc": cat.get("desc", ""),
            "themes": new_themes
        }

    theme3 = {
        "CATEGORIES": new_categories,
        "THEME_FLAT_MAP": new_flat_map,
        "version": "V2",
        "_generated_from": "theme2.json + theme_v2_meta_part1~4"
    }

    with open(THEME3_PATH, "w", encoding="utf-8") as f:
        json.dump(theme3, f, ensure_ascii=False, indent=2)

    print(f"theme3.json 生成完成，共 {len(new_categories)} 个一级目录，{processed} 个子主题。")
    print(f"已命中 V2 元数据 {covered} 个，默认模板兜底 {processed - covered} 个。")
    print(f"保存路径: {THEME3_PATH}")


if __name__ == "__main__":
    main()
