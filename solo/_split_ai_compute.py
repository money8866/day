"""将 theme3.json 中的 'AI算力链' 拆分为 5 个独立子主题：
  1. AI算力芯片    — GPU/NPU/AI加速芯片设计
  2. 光模块与CPO  — 800G/1.6T光模块 + CPO共封装
  3. 数据中心网络  — 交换机/网络设备/服务器
  4. 数据中心散热  — 液冷/散热/温控
  5. 高速铜连接    — 连接器/铜缆/背板
"""
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
path = os.path.join(BASE, "theme3.json")

with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

cats = data["CATEGORIES"]
ai_themes = cats["AI"]["themes"]

# 原 AI算力链 的完整配置
src = ai_themes.pop("AI算力链")
print(f"已移除: AI算力链")

# 新主题配置（继承原版式，仅改关键字段）
new_themes = {

    "AI算力芯片": {
        "theme_name": "AI算力芯片",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": [
            "GPU/NPU/TPU等AI训练推理芯片",
            "AI加速卡与HBM内存",
            "先进封装与制程"
        ],
        "industry_roles": {
            "AI芯片设计": 0.4,
            "先进封装代工": 0.3,
            "芯片制造配套": 0.3
        },
        "business_dna_tags": [
            "GPU芯片",
            "AI加速芯片",
            "NPU",
            "AI加速卡",
            "HBM内存",
            "AI推理芯片",
            "国产GPU",
            "Chiplet"
        ],
        "weak_positive_tags": [
            "光模块",
            "服务器整机",
            "电子制造"
        ],
        "negative_pressure_tags": {
            "消费电子终端": -0.6,
            "手机SoC": -0.5,
            "家电": -0.5,
            "汽车芯片": -0.3
        },
        "industry_soft_constraints": {
            "半导体": 0.6,
            "电子制造": 0.2,
            "通信设备": 0.2
        },
        "stock_role_mapping": {
            "龙头": "AI芯片全球头部或自研GPU/NPU",
            "中军": "AI加速卡/HBM封装/代工",
            "补涨": "国产替代AI芯片/单点封装设备"
        },
        "matching_strategy": {
            "mode": "semantic_business_hybrid",
            "embedding_weight": 0.55,
            "business_weight": 0.30,
            "industry_weight": 0.15
        }
    },

    "光模块与CPO": {
        "theme_name": "光模块与CPO",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": [
            "800G/1.6T高速光模块",
            "CPO共封装光学技术",
            "硅光子与光引擎"
        ],
        "industry_roles": {
            "光模块封装": 0.35,
            "光芯片/硅光芯片": 0.25,
            "CPO/光引擎": 0.25,
            "光纤连接器件": 0.15
        },
        "business_dna_tags": [
            "光模块",
            "800G",
            "1.6T光模块",
            "CPO",
            "共封装光学",
            "硅光子",
            "光引擎",
            "光通信芯片",
            "EML",
            "VCSEL"
        ],
        "weak_positive_tags": [
            "光纤",
            "交换机",
            "数据中心"
        ],
        "negative_pressure_tags": {
            "消费电子终端": -0.7,
            "手机产业链": -0.6,
            "家电": -0.5,
            "汽车电子": -0.4
        },
        "industry_soft_constraints": {
            "通信设备": 0.5,
            "光通信": 0.3,
            "通信电子": 0.2
        },
        "stock_role_mapping": {
            "龙头": "800G/1.6T光模块全球头部",
            "中军": "光芯片/CPO/硅光",
            "补涨": "光纤跳线/光无源器件/辅材"
        },
        "matching_strategy": {
            "mode": "semantic_business_hybrid",
            "embedding_weight": 0.55,
            "business_weight": 0.30,
            "industry_weight": 0.15
        }
    },

    "数据中心网络": {
        "theme_name": "数据中心网络",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": [
            "AI服务器与GPU集群",
            "数据中心交换机与路由器",
            "数据中心电源与电力设备"
        ],
        "industry_roles": {
            "AI服务器整机": 0.35,
            "网络交换设备": 0.25,
            "数据中心电源": 0.2,
            "机柜与基础设施": 0.2
        },
        "business_dna_tags": [
            "AI服务器",
            "交换机",
            "路由器",
            "数据中心电源",
            "UPS",
            "机柜",
            "服务器ODM",
            "GPU服务器",
            "算力服务器"
        ],
        "weak_positive_tags": [
            "PCB",
            "连接器",
            "服务器电源"
        ],
        "negative_pressure_tags": {
            "消费电子终端": -0.7,
            "手机": -0.6,
            "家电": -0.5,
            "汽车电子": -0.4
        },
        "industry_soft_constraints": {
            "计算机设备": 0.4,
            "通信设备": 0.3,
            "电力设备": 0.3
        },
        "stock_role_mapping": {
            "龙头": "AI服务器/交换机全球头部",
            "中军": "ODM/电源/机柜",
            "补涨": "服务器配件/连接/散热单点"
        },
        "matching_strategy": {
            "mode": "semantic_business_hybrid",
            "embedding_weight": 0.55,
            "business_weight": 0.30,
            "industry_weight": 0.15
        }
    },

    "数据中心散热": {
        "theme_name": "数据中心散热",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": [
            "数据中心液冷散热系统",
            "服务器风扇与温控",
            "绿色数据中心与能效"
        ],
        "industry_roles": {
            "液冷系统": 0.35,
            "散热模组": 0.25,
            "温控设备": 0.2,
            "CDU/冷却机组": 0.2
        },
        "business_dna_tags": [
            "液冷服务器",
            "液冷散热",
            "冷板",
            "CDU",
            "冷却机组",
            "散热模组",
            "风扇",
            "温控",
            "热管",
            "数据中心温控"
        ],
        "weak_positive_tags": [
            "服务器",
            "电力设备",
            "IDC运营"
        ],
        "negative_pressure_tags": {
            "消费电子终端": -0.7,
            "手机": -0.6,
            "家电": -0.5,
            "汽车电子": -0.4
        },
        "industry_soft_constraints": {
            "电力设备": 0.5,
            "通用设备": 0.3,
            "计算机设备": 0.2
        },
        "stock_role_mapping": {
            "龙头": "液冷系统整体解决方案头部",
            "中军": "液冷模组/CDU/温控设备",
            "补涨": "散热材料/风扇/单点配件"
        },
        "matching_strategy": {
            "mode": "semantic_business_hybrid",
            "embedding_weight": 0.55,
            "business_weight": 0.30,
            "industry_weight": 0.15
        }
    },

    "高速铜连接": {
        "theme_name": "高速铜连接",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": [
            "高速背板连接器",
            "AI服务器内部铜互联",
            "光铜混合连接方案"
        ],
        "industry_roles": {
            "高速连接器": 0.4,
            "背板/线缆": 0.3,
            "精密制造": 0.3
        },
        "business_dna_tags": [
            "高速连接器",
            "背板连接器",
            "铜缆",
            "高速铜缆",
            "AI服务器连接",
            "连接器",
            "精密连接件",
            "IO连接器",
            "SFP"
        ],
        "weak_positive_tags": [
            "服务器",
            "通信设备",
            "PCB"
        ],
        "negative_pressure_tags": {
            "消费电子终端": -0.7,
            "手机": -0.6,
            "家电": -0.5,
            "汽车连接器": -0.3
        },
        "industry_soft_constraints": {
            "电子制造": 0.4,
            "通信设备": 0.3,
            "计算机设备": 0.3
        },
        "stock_role_mapping": {
            "龙头": "高速背板连接器全球头部",
            "中军": "AI服务器铜缆/连接方案",
            "补涨": "单点精密连接件/辅材"
        },
        "matching_strategy": {
            "mode": "semantic_business_hybrid",
            "embedding_weight": 0.55,
            "business_weight": 0.30,
            "industry_weight": 0.15
        }
    },
}

# 插入到 AI 的主题列表中（在 "AI终端" 之前）
# 保持顺序：AI应用 → AI文化娱乐 → AI模型与AI Agent → 新拆的5个 → 数据中心瓶颈硬件链 → AI终端
insert_order = [
    "AI应用", "AI文化娱乐", "AI模型与AI Agent",
    "AI算力芯片", "光模块与CPO", "数据中心网络",
    "数据中心散热", "高速铜连接",
    "数据中心瓶颈硬件链", "AI终端"
]

new_themes_ordered = {}
for name in insert_order:
    if name in ai_themes:
        new_themes_ordered[name] = ai_themes[name]
    elif name in new_themes:
        new_themes_ordered[name] = new_themes[name]

cats["AI"]["themes"] = new_themes_ordered

with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

# 验证
n_themes = sum(len(v.get("themes", {})) for v in cats.values())
print(f"\n✅ 已拆分并保存到: {path}")
print(f"   AI 主题列表: {list(new_themes_ordered.keys())}")
print(f"   全局主题总数: {n_themes}")
