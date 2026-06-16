import json
data = json.load(open("theme3.json", "r", encoding="utf-8"))
cats = data["CATEGORIES"]

# === 1) 修 低空经济 4个主题的 industry_soft_constraints ===
low = cats["低空经济"]["themes"]

# 低空飞行器制造 - 扩大行业范围：原来只接受航空装备/军工电子，新增通用设备/汽车零部件/机械设备
low["低空飞行器制造"]["industry_soft_constraints"] = {
    "航空装备": 0.35,
    "军工电子": 0.25,
    "通用设备": 0.15,
    "汽车零部件": 0.15,
    "机械设备": 0.10,
}

# 低空基础设施 - 新增通用设备/电力设备
low["低空基础设施"]["industry_soft_constraints"] = {
    "基础建设": 0.25,
    "通信服务": 0.25,
    "电力设备": 0.20,
    "通用设备": 0.15,
    "机场": 0.15,
}

# 低空数据与控制 - 扩大到通信设备/软件开发
low["低空数据与控制"]["industry_soft_constraints"] = {
    "通信设备": 0.35,
    "软件开发": 0.25,
    "半导体": 0.20,
    "军工电子": 0.20,
}

# 低空运营服务 - 扩大到物流/汽车
low["低空运营服务"]["industry_soft_constraints"] = {
    "物流": 0.3,
    "航空运输": 0.25,
    "软件开发": 0.2,
    "汽车零部件": 0.15,
    "电力": 0.10,
}

# === 2) 消费电子 - weak_positive_tags 加"光学光电子" ===
cons_e = cats["消费"]["themes"]["消费电子"]
if "光学光电子" not in cons_e.get("weak_positive_tags", []):
    cons_e["weak_positive_tags"] = list(cons_e.get("weak_positive_tags", [])) + ["光学光电子"]

with open("theme3.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("✅ theme3.json 已更新")
print("   低空飞行器制造 industry:", low["低空飞行器制造"]["industry_soft_constraints"])
print("   低空基础设施 industry:", low["低空基础设施"]["industry_soft_constraints"])
print("   低空数据与控制 industry:", low["低空数据与控制"]["industry_soft_constraints"])
print("   低空运营服务 industry:", low["低空运营服务"]["industry_soft_constraints"])
print("   消费电子 weak_positive_tags:", cons_e["weak_positive_tags"])
