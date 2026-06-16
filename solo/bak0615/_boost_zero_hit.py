"""对 4 个零命中主题，手动在 theme3.json 中补充东财现成的概念板块名。

东财概念板块名参考（从前面的板块名列表里挑）：
  AI相关：人工智能, AI应用, AI智能体, 多模态AI, ChatGPT概念, AIGC概念
  机器人相关：人形机器人, 减速器, 工业母机, 高端装备
  核能相关：核能核电
"""
import json, os

BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE, "theme3.json"), "r", encoding="utf-8") as f:
    data = json.load(f)

cats = data["CATEGORIES"]

# 每个零命中主题的补强 concept（都是东财已有的概念板块名）
boost = {
    ("AI", "AI应用"): [
        "人工智能", "AI应用", "AI智能体", "多模态AI",
        "ChatGPT概念", "AIGC概念", "数字人",
    ],
    ("AI", "AI模型与AI Agent"): [
        "人工智能", "AI应用", "AI智能体", "多模态AI",
        "ChatGPT概念", "AIGC概念", "AI芯片",
    ],
    ("人形机器人", "行星滚柱丝杠"): [
        "人形机器人", "减速器", "工业母机", "高端装备",
    ],
    ("军工", "核聚变"): [
        "核能核电", "高温超导", "军工", "军民融合",
    ],
}

added_summary = []
for (cat, theme), boards in boost.items():
    if cat not in cats:
        continue
    themes = cats[cat].get("themes", {})
    if theme not in themes:
        continue
    cfg = themes[theme]
    existing = cfg.setdefault("concept", [])
    added = 0
    for b in boards:
        if b not in existing:
            existing.append(b)
            added += 1
    cfg["concept"] = existing
    themes[theme] = cfg
    added_summary.append(f"[{cat}] {theme}: 新增 {added} 个概念板块 -> {boards}")

with open(os.path.join(BASE, "theme3.json"), "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

for line in added_summary:
    print(line)
print(f"\n✅ 已更新 {BASE}/theme3.json")
