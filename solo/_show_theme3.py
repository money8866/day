"""展示 theme3.json 核心字段"""
import json

d = json.load(open("theme3.json", "r", encoding="utf-8"))
cats = d["CATEGORIES"]

samples = [
    ("AI", "AI算力链"),
    ("AI", "AI终端"),
    ("半导体", "功率半导体"),
    ("半导体", "半导体材料"),
    ("新能源", "新型储能"),
    ("新能源汽车", "充换电与能源补给"),
    ("人形机器人", "人形机器人整机与集成"),
    ("低空经济", "低空飞行器制造"),
    ("低空经济", "低空数据与控制"),
    ("军工", "军工"),
    ("消费", "智能驾驶"),
    ("生物医药", "创新医药主线"),
    ("金融", "金融科技"),
    ("商业航天", "卫星制造与发射"),
]

for cat, theme in samples:
    cfg = cats[cat]["themes"].get(theme) or {}
    print(f"\n{'='*60}")
    print(f"[{cat}] {theme}")
    print(f"  industry        : {cfg.get('industry', [])}")
    print(f"  concept         : {cfg.get('concept', [])}")
    print(f"  keywords        : {cfg.get('keywords', [])}")
    print(f"  exclude_keywords: {cfg.get('exclude_keywords', [])}")
