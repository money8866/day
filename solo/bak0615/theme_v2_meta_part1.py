"""
生成 theme3.json
- 一级和二级目录结构延续自 theme2.json
- 每个主题新增 V2 字段：core_semantic, industry_roles, business_dna_tags,
  weak_positive_tags, negative_pressure_tags, industry_soft_constraints,
  stock_role_mapping, matching_strategy 等
"""
import json

# ==========================================================================
# 主题 V2 元数据映射表
# ==========================================================================
THEME_V2_META_PART1 = {

    # ------------------------------------------------------------------
    # AI
    # ------------------------------------------------------------------
    "AI算力链": {
        "theme_name": "AI算力链",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": [
            "AI算力基础设施与加速芯片",
            "数据中心网络与高速互联",
            "光模块与800G/1.6T产业链"
        ],
        "industry_roles": {
            "算力芯片设计制造": 0.30,
            "光模块与高速互联": 0.25,
            "网络交换与交换机": 0.20,
            "服务器与整机制造": 0.15,
            "铜连接与连接器": 0.10
        },
        "business_dna_tags": [
            "GPU芯片", "光模块", "800G/1.6T", "液冷服务器", "交换机",
            "硅光子", "AI加速芯片", "高速连接器"
        ],
        "weak_positive_tags": [
            "电子制造", "PCB", "通信设备", "连接器"
        ],
        "negative_pressure_tags": {
            "消费电子终端": -0.7, "手机产业链": -0.6, "家电": -0.5, "传统IDC运营商": -0.4
        },
        "industry_soft_constraints": {
            "计算机设备": 0.3, "通信设备": 0.3, "电子化学品Ⅱ": 0.2, "半导体": 0.2
        },
        "stock_role_mapping": {
            "龙头": "GPU/光模块/交换机环节全球领先企业",
            "中军": "服务器整机或ODM巨头/大型IDC或光模块头部",
            "补涨": "PCB/电源/连接/散热/辅材单点受益"
        },
        "matching_strategy": {
            "mode": "semantic_business_hybrid",
            "embedding_weight": 0.55,
            "business_weight": 0.30,
            "industry_weight": 0.15
        }
    },

    "数据中心瓶颈硬件链": {
        "theme_name": "数据中心瓶颈硬件链",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": [
            "数据中心热管理与液冷系统",
            "高压直流电源与配电模块",
            "IDC运营与服务器散热配套"
        ],
        "industry_roles": {
            "液冷温控设备": 0.30,
            "电源模块与UPS": 0.25,
            "数据中心运营": 0.20,
            "母线铜排与连接": 0.15,
            "散热材料与导热": 0.10
        },
        "business_dna_tags": [
            "冷板式液冷", "浸没式液冷", "高压直流", "UPS",
            "电源模块", "液冷板", "热管", "PUE优化"
        ],
        "weak_positive_tags": ["电力设备", "工业电子", "磁性元件", "铜加工"],
        "negative_pressure_tags": {
            "消费电子": -0.7, "家电整机": -0.6, "手机产业链": -0.5,
            "GPU芯片设计": -0.3
        },
        "industry_soft_constraints": {"电源设备": 0.4, "计算机设备": 0.3, "工业金属": 0.3},
        "stock_role_mapping": {
            "龙头": "液冷系统或高压直流电源头部",
            "中军": "IDC运营商或大型电源/散热企业",
            "补涨": "导热材料/电磁兼容/精密结构件"
        },
        "matching_strategy": {
            "mode": "semantic_business_hybrid",
            "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15
        }
    },

    "AI应用": {
        "theme_name": "AI应用",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": [
            "企业级AI软件与智能体",
            "通用大模型与垂类行业应用",
            "办公/搜索/编程等生产力AI"
        ],
        "industry_roles": {
            "大模型与智能体平台": 0.30,
            "企业软件与ERP": 0.25,
            "IT服务与系统集成": 0.20,
            "行业信息化软件": 0.15,
            "数字媒体与内容": 0.10
        },
        "business_dna_tags": ["Agent", "大模型", "RAG", "Copilot", "企业智能体", "AI搜索", "AI办公", "AI编程"],
        "weak_positive_tags": ["软件外包", "传统软件", "云服务", "SaaS"],
        "negative_pressure_tags": {"游戏": -0.7, "短剧视频": -0.6, "芯片制造": -0.4, "PCB": -0.3},
        "industry_soft_constraints": {"软件开发": 0.5, "IT服务Ⅱ": 0.3, "数字媒体": 0.2},
        "stock_role_mapping": {
            "龙头": "大模型平台或通用AI应用头部",
            "中军": "办公/搜索/编程等头部AI软件",
            "补涨": "垂类行业软件或系统集成"
        },
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "AI模型与AI Agent": {
        "theme_name": "AI模型与AI Agent",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["大模型训练与推理技术", "AI智能体与自动化工作流", "多模态与端到端AI系统"],
        "industry_roles": {"大模型训练平台": 0.35, "Agent中间件": 0.25, "企业AI应用": 0.25, "AI研发外包": 0.15},
        "business_dna_tags": ["大模型", "LLM", "Agent", "MCP", "Copilot", "Function Call", "Prompt", "RAG"],
        "weak_positive_tags": ["SaaS软件", "客服系统", "企业信息化"],
        "negative_pressure_tags": {"GPU芯片制造": -0.3, "光模块": -0.3, "游戏": -0.6, "消费电子": -0.5},
        "industry_soft_constraints": {"软件开发": 0.6, "IT服务Ⅱ": 0.4},
        "stock_role_mapping": {
            "龙头": "自研大模型或通用Agent平台",
            "中军": "Agent中间件或企业AI软件",
            "补涨": "行业信息化/传统软件AI转型"
        },
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "AI终端": {
        "theme_name": "AI终端",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["AI PC/AI手机等端侧智能设备", "MR/AR/VR可穿戴硬件", "端侧AI芯片与传感器"],
        "industry_roles": {"消费电子制造": 0.30, "可穿戴与MR硬件": 0.25, "端侧AI芯片": 0.20, "光学镜头与显示": 0.15, "结构件与组装": 0.10},
        "business_dna_tags": ["AI PC", "AI手机", "MR", "AR", "VR", "端侧大模型", "折叠屏", "智能穿戴", "光学镜头"],
        "weak_positive_tags": ["消费电子", "小家电", "汽车电子", "面板"],
        "negative_pressure_tags": {"传统白电": -0.6, "纯PCB制造": -0.3, "纯算力芯片": -0.3, "医药生物": -0.8},
        "industry_soft_constraints": {"消费电子": 0.5, "光学光电子": 0.3, "半导体": 0.2},
        "stock_role_mapping": {"龙头": "终端品牌或核心器件", "中军": "MR/AI硬件链主", "补涨": "结构件/传感器/连接"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "AI文化娱乐": {
        "theme_name": "AI文化娱乐",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["AI视频/短剧/游戏内容生成", "数字人与虚拟偶像", "动漫/影视IP制作工具"],
        "industry_roles": {"视频与短剧": 0.30, "游戏": 0.25, "动漫与影视": 0.20, "IP运营": 0.15, "数字人/元宇宙": 0.10},
        "business_dna_tags": ["AI短剧", "文生视频", "Sora", "Runway", "Midjourney", "数字人", "AIGC", "虚拟偶像", "互动娱乐"],
        "weak_positive_tags": ["出版传媒", "广告营销", "影视院线"],
        "negative_pressure_tags": {"GPU/算力芯片": -0.3, "创新药": -0.8, "新能源": -0.7},
        "industry_soft_constraints": {"数字媒体": 0.4, "影视院线": 0.3, "出版": 0.3},
        "stock_role_mapping": {"龙头": "短剧/视频平台或头部游戏", "中军": "IP运营与影视制作", "补涨": "广告营销/出版传媒延伸"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    # ------------------------------------------------------------------
    # 半导体
    # ------------------------------------------------------------------
    "半导体设备": {
        "theme_name": "半导体设备",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["晶圆制造前道设备国产替代", "刻蚀/薄膜沉积/光刻机", "量测/检测/涂胶显影后道设备"],
        "industry_roles": {"前道设备制造": 0.35, "量测与检测设备": 0.20, "后道封装设备": 0.15, "零部件与真空系统": 0.15, "湿法清洗与热处理": 0.15},
        "business_dna_tags": ["刻蚀设备", "PVD", "CVD", "ALD", "涂胶显影", "量测", "缺陷检测", "热处理", "离子注入"],
        "weak_positive_tags": ["高端装备", "精密机械", "真空技术"],
        "negative_pressure_tags": {"光刻胶/电子特气": -0.4, "消费电子终端": -0.7, "家电": -0.6, "医药": -0.8},
        "industry_soft_constraints": {"自动化设备": 0.6, "半导体": 0.4},
        "stock_role_mapping": {"龙头": "刻蚀/薄膜沉积头部", "中军": "量测/涂胶显影/热处理", "补涨": "零部件/真空泵/电源模块"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "半导体材料": {
        "theme_name": "半导体材料",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["硅片/光刻胶/电子特气", "前驱体/靶材/抛光材料", "晶圆制造关键化学品国产替代"],
        "industry_roles": {"电子化学品": 0.30, "硅片晶圆制造": 0.20, "特种气体": 0.15, "靶材": 0.15, "抛光液/抛光垫": 0.10, "光刻胶配套": 0.10},
        "business_dna_tags": ["光刻胶", "ArF", "KrF", "电子特气", "硅片", "靶材", "CMP", "抛光液", "前驱体", "湿电子化学品"],
        "weak_positive_tags": ["化工原料", "特种金属", "精细化工"],
        "negative_pressure_tags": {"半导体设备": -0.4, "消费电子": -0.6, "传统煤化工": -0.5, "医药": -0.7},
        "industry_soft_constraints": {"电子化学品Ⅱ": 0.4, "化学原料": 0.3, "半导体材料": 0.3},
        "stock_role_mapping": {"龙头": "光刻胶/电子特气/硅片头部", "中军": "靶材/前驱体/湿电子化学品", "补涨": "CMP材料/特种电子配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "先进封装": {
        "theme_name": "先进封装",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["Chiplet/3D封装/CoWoS", "ABF载板与IC载板", "凸块/扇出/TSV等先进工艺"],
        "industry_roles": {"封测代工": 0.35, "封装基板/ABF": 0.25, "凸块与Bumping": 0.15, "封装设备与材料": 0.15, "测试与探针": 0.10},
        "business_dna_tags": ["CoWoS", "Chiplet", "3D封装", "SiP", "TSV", "Bumping", "扇出型", "PLP", "ABF载板", "引线框架"],
        "weak_positive_tags": ["PCB制造", "被动元件", "精密结构件"],
        "negative_pressure_tags": {"光刻机/刻蚀": -0.4, "手机终端": -0.6, "白电": -0.7, "医药": -0.8},
        "industry_soft_constraints": {"半导体": 0.5, "电子制造": 0.3, "自动化设备": 0.2},
        "stock_role_mapping": {"龙头": "封测代工头部", "中军": "ABF载板/大型封测厂", "补涨": "引线框架/探针台/测试设备"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "存储芯片": {
        "theme_name": "存储芯片",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["DRAM/NAND/3D NAND国产替代", "HBM高带宽内存", "存储控制器与接口"],
        "industry_roles": {"存储芯片设计": 0.35, "存储制造与晶圆": 0.25, "HBM与高带宽": 0.20, "存储控制器": 0.10, "存储模组与封测": 0.10},
        "business_dna_tags": ["DRAM", "NAND", "3D NAND", "HBM", "Nor Flash", "MCU", "存储控制器", "CXL"],
        "weak_positive_tags": ["封测", "PCB", "模组制造"],
        "negative_pressure_tags": {"光刻机设备": -0.3, "消费电子品牌": -0.5, "传统家电": -0.6, "医药": -0.7},
        "industry_soft_constraints": {"半导体": 0.6, "电子": 0.4},
        "stock_role_mapping": {"龙头": "DRAM/NAND设计或制造", "中军": "MCU/存储控制芯片", "补涨": "模组/封测/接口"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "芯片设计": {
        "theme_name": "芯片设计",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["数字/模拟/射频/FPGA全品类芯片设计", "EDA工具与IP授权", "国产替代芯片"],
        "industry_roles": {"数字与SOC设计": 0.30, "模拟/电源/射频": 0.25, "FPGA与可编程": 0.15, "EDA与IP": 0.15, "蓝牙/WiFi/物联网": 0.15},
        "business_dna_tags": ["EDA", "FPGA", "ASIC", "SoC", "IP授权", "RISC-V", "模拟芯片", "射频", "MCU", "GPU"],
        "weak_positive_tags": ["软件工具", "消费电子芯片"],
        "negative_pressure_tags": {"晶圆代工制造": -0.3, "家电整机": -0.6, "医药": -0.8, "能源开采": -0.7},
        "industry_soft_constraints": {"半导体": 0.7, "软件开发": 0.3},
        "stock_role_mapping": {"龙头": "通用大芯片/高端模拟/射频头部", "中军": "国产MCU/Power/传感器设计", "补涨": "EDA/IP/细分领域设计"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "功率半导体": {
        "theme_name": "功率半导体",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["碳化硅/氮化镓第三代半导体", "IGBT/MOSFET功率器件", "车规级功率模块"],
        "industry_roles": {"SiC/GaN材料与器件": 0.30, "IGBT模块": 0.25, "MOSFET与电源芯片": 0.20, "车规级功率": 0.15, "智能功率模块IPM": 0.10},
        "business_dna_tags": ["SiC", "碳化硅", "GaN", "氮化镓", "IGBT", "MOSFET", "功率器件", "第三代半导体", "宽禁带", "车规级"],
        "weak_positive_tags": ["新能源车电子", "工业自动化", "光伏逆变器"],
        "negative_pressure_tags": {"纯设计类芯片": -0.3, "消费电子": -0.5, "医药": -0.7},
        "industry_soft_constraints": {"半导体": 0.5, "电子": 0.3, "汽车零部件": 0.2},
        "stock_role_mapping": {"龙头": "SiC/IGBT头部", "中军": "车规级功率模块厂商", "补涨": "MOSFET/驱动IC/IPM"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "晶圆代工与IDM": {
        "theme_name": "晶圆代工与IDM",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["晶圆制造代工Foundry", "成熟制程扩产与国产替代", "IDM一体化企业"],
        "industry_roles": {"晶圆代工企业": 0.40, "IDM综合制造": 0.30, "特种工艺代工": 0.15, "功率/模拟代工": 0.15},
        "business_dna_tags": ["Foundry", "IDM", "晶圆代工", "成熟制程", "先进制程", "28nm", "BCD", "CIS", "MEMS"],
        "weak_positive_tags": ["半导体设备", "半导体材料"],
        "negative_pressure_tags": {"纯芯片设计": -0.2, "消费电子终端": -0.6, "医药": -0.7, "传统能源": -0.6},
        "industry_soft_constraints": {"半导体": 0.7, "电子": 0.3},
        "stock_role_mapping": {"龙头": "大型晶圆代工/IDM", "中军": "特色工艺代工厂", "补涨": "功率/模拟代工与扩产配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    # ------------------------------------------------------------------
    # 新能源
    # ------------------------------------------------------------------
    "电力链": {
        "theme_name": "电力链",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["火电/水电/核电等传统电源", "电网设备与特高压", "电力市场化与容量电价"],
        "industry_roles": {"火电运营": 0.25, "水电/核电": 0.20, "电网设备与特高压": 0.25, "输变电设备": 0.15, "配网与电力电子": 0.15},
        "business_dna_tags": ["火电", "煤电", "超超临界", "水电", "核电", "特高压", "电网", "电力市场化", "容量电价", "调峰"],
        "weak_positive_tags": ["煤炭", "工业机械", "通用设备"],
        "negative_pressure_tags": {"消费电子": -0.7, "医药": -0.8, "半导体设计": -0.5, "银行": -0.3},
        "industry_soft_constraints": {"电力": 0.4, "火力发电": 0.3, "电网设备": 0.2, "风电设备": 0.1},
        "stock_role_mapping": {"龙头": "大型电力运营集团", "中军": "水电/核电核心运营商", "补涨": "输变电/配网设备厂商"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "电网数字化": {
        "theme_name": "电网数字化",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["虚拟电厂与源网荷储协调", "配网自动化与调度", "电力物联网与数字电网"],
        "industry_roles": {"调度自动化与软件": 0.35, "配网自动化设备": 0.25, "电力物联网": 0.20, "柔性直流与电力电子": 0.20},
        "business_dna_tags": ["虚拟电厂", "配网改造", "数字电网", "调度自动化", "能源互联网", "配电自动化", "柔性直流", "电力物联网"],
        "weak_positive_tags": ["工业软件", "工业自动化", "通信设备"],
        "negative_pressure_tags": {"火电煤炭开采": -0.4, "医药": -0.7, "消费电子": -0.5},
        "industry_soft_constraints": {"电网设备": 0.5, "软件开发": 0.3, "自动化设备": 0.2},
        "stock_role_mapping": {"龙头": "调度/继保/电力软件", "中军": "配网自动化与物联网设备", "补涨": "传感器/电力信息安全"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "新型储能": {
        "theme_name": "新型储能",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["电化学储能/工商业储能", "PCS/逆变器", "钠离子电池与液流电池"],
        "industry_roles": {"电化学储能系统": 0.30, "PCS与逆变器": 0.25, "钠电与液流电池": 0.20, "温控与消防": 0.15, "BMS/EMS": 0.10},
        "business_dna_tags": ["储能", "工商业储能", "大型储能", "PCS", "BMS", "EMS", "钠离子电池", "液流电池", "电化学储能", "虚拟电厂配套"],
        "weak_positive_tags": ["新能源车电池", "光伏逆变器", "电力运营"],
        "negative_pressure_tags": {"传统火电煤炭": -0.5, "消费电子": -0.6, "医药": -0.7},
        "industry_soft_constraints": {"电池": 0.4, "电网设备": 0.3, "电力设备": 0.3},
        "stock_role_mapping": {"龙头": "大型储能系统集成商", "中军": "PCS/温控/钠电材料", "补涨": "BMS/消防/电力电子配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "固态电池": {
        "theme_name": "固态电池",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["半固态/全固态电池", "硫化物/氧化物电解质", "锂金属负极与高镍正极"],
        "industry_roles": {"固态电池制造": 0.30, "硫化物/氧化物电解质": 0.25, "高镍正极与富锂锰基": 0.20, "锂金属/硅碳负极": 0.15, "聚合物电解质": 0.10},
        "business_dna_tags": ["全固态", "半固态", "硫化物", "氧化物", "聚合物电解质", "锂金属负极", "高镍", "富锂锰基", "固态电解质", "LLZO", "LATP"],
        "weak_positive_tags": ["锂电材料", "新能源车", "储能"],
        "negative_pressure_tags": {"传统燃油车": -0.7, "煤炭/石油": -0.6, "医药": -0.7, "消费电子": -0.5},
        "industry_soft_constraints": {"电池": 0.5, "电池化学品": 0.3, "能源金属": 0.2},
        "stock_role_mapping": {"龙头": "固态电池中试线或半固态量产", "中军": "高镍正极/固态电解质材料", "补涨": "锂金属负极/硅碳/粘结剂"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "光伏产业链": {
        "theme_name": "光伏产业链",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["N型TOPCon/BC电池/叠层技术", "硅料/硅片/电池片/组件", "逆变器与支架跟踪"],
        "industry_roles": {"硅料与硅片": 0.20, "电池片技术": 0.25, "组件与胶膜/玻璃": 0.20, "逆变器": 0.20, "支架与跟踪": 0.15},
        "business_dna_tags": ["光伏", "TOPCon", "BC电池", "HJT", "钙钛矿", "叠层", "硅料", "石英坩埚", "光伏玻璃", "胶膜", "金刚线"],
        "weak_positive_tags": ["储能", "新能源运营", "工业金属"],
        "negative_pressure_tags": {"风电整机": -0.3, "消费电子": -0.6, "医药": -0.7, "传统能源": -0.5},
        "industry_soft_constraints": {"光伏设备": 0.5, "电力": 0.3, "有色金属": 0.2},
        "stock_role_mapping": {"龙头": "一体化组件或逆变器头部", "中军": "新型电池片/胶膜/玻璃龙头", "补涨": "辅材/支架/设备"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "风电产业链": {
        "theme_name": "风电产业链",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["海上风电与深远海", "风机大型化与大兆瓦", "齿轮箱/主轴/塔筒/海缆"],
        "industry_roles": {"风机整机": 0.30, "海缆与传输": 0.20, "叶片/齿轮箱/主轴": 0.25, "塔筒与基础": 0.15, "漂浮式与深远海": 0.10},
        "business_dna_tags": ["风电", "海上风电", "风机", "大型化", "叶片", "齿轮箱", "轴承", "法兰", "塔筒", "海缆", "漂浮式"],
        "weak_positive_tags": ["海工装备", "船舶制造", "电力运营"],
        "negative_pressure_tags": {"光伏组件": -0.3, "消费电子": -0.6, "医药": -0.7, "传统能源": -0.4},
        "industry_soft_constraints": {"风电设备": 0.6, "电力": 0.2, "船舶制造": 0.2},
        "stock_role_mapping": {"龙头": "风机整机龙头", "中军": "海缆/轴承/叶片", "补涨": "塔筒/法兰/海工装备"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    # ------------------------------------------------------------------
    # 新能源汽车
    # ------------------------------------------------------------------
    "新能源汽车整车制造": {
        "theme_name": "新能源汽车整车制造",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["新能源乘用车整车品牌", "商用车电动化与出口", "增程/纯电/插混平台"],
        "industry_roles": {"新能源乘用车": 0.40, "新能源商用车": 0.25, "汽车平台与出口": 0.20, "经销商与渠道": 0.15},
        "business_dna_tags": ["新能源汽车", "电动车", "纯电动", "插混", "增程式", "出口", "乘用车", "商用车"],
        "weak_positive_tags": ["汽车零部件", "汽车电子", "汽车经销"],
        "negative_pressure_tags": {"燃油车品牌": -0.5, "消费电子": -0.5, "医药": -0.7},
        "industry_soft_constraints": {"汽车整车": 0.7, "汽车零部件": 0.3},
        "stock_role_mapping": {"龙头": "头部新势力或大型自主", "中军": "商用车/出口/合资新能源", "补涨": "经销渠道/平台公司"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "动力系统": {
        "theme_name": "动力系统",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["动力电池与BMS", "电机电控与电驱", "SiC/GaN高压平台"],
        "industry_roles": {"动力电池": 0.35, "电驱与电机": 0.25, "电控与BMS": 0.20, "高压平台与SiC": 0.20},
        "business_dna_tags": ["电池", "动力电池", "锂电池", "磷酸铁锂", "三元锂", "电机", "电驱", "电控", "BMS", "SiC", "800V高压"],
        "weak_positive_tags": ["新能源车电子", "IGBT", "储能电池"],
        "negative_pressure_tags": {"传统燃油发动机": -0.6, "消费电子": -0.5, "医药": -0.7, "半导体设计公司": -0.3},
        "industry_soft_constraints": {"电池": 0.4, "汽车零部件": 0.3, "电机Ⅱ": 0.3},
        "stock_role_mapping": {"龙头": "大型动力电池厂", "中军": "电驱/电机/电控厂商", "补涨": "电池辅材/热管理/碳化硅器件"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "新能源汽车智能化": {
        "theme_name": "新能源汽车智能化",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["高阶智驾NOA与城市NOA", "激光雷达/毫米波/摄像头", "座舱芯片与车规OS"],
        "industry_roles": {"智驾域控制器与芯片": 0.30, "激光雷达与传感器": 0.25, "智能座舱": 0.20, "车规软件与OS": 0.15, "V2X与车路协同": 0.10},
        "business_dna_tags": ["自动驾驶", "NOA", "城市NOA", "激光雷达", "毫米波", "摄像头", "域控制器", "智驾芯片", "座舱芯片", "车规OS", "BEV", "Transformer"],
        "weak_positive_tags": ["消费电子", "半导体", "工业软件"],
        "negative_pressure_tags": {"传统燃油车": -0.5, "医药": -0.7, "传统火电": -0.5},
        "industry_soft_constraints": {"汽车零部件": 0.4, "软件开发": 0.3, "半导体": 0.3},
        "stock_role_mapping": {"龙头": "激光雷达或智驾域控头部", "中军": "座舱/驾驶芯片Tier1", "补涨": "摄像头/毫米波/线束"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "充换电与能源补给": {
        "theme_name": "充换电与能源补给",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["超快充/800V充电网络", "换电站与运营", "光储充一体化"],
        "industry_roles": {"充电桩/站制造": 0.30, "超快充与模块": 0.25, "换电站运营": 0.25, "V2G/光储充": 0.20},
        "business_dna_tags": ["充电桩", "充电站", "快充", "超充", "换电", "换电站", "充电模块", "充电枪", "V2G", "光储充"],
        "weak_positive_tags": ["储能", "电力运营", "电力电子"],
        "negative_pressure_tags": {"燃油车": -0.5, "医药": -0.7, "消费电子": -0.5},
        "industry_soft_constraints": {"电力": 0.4, "电源设备": 0.3, "通用设备": 0.3},
        "stock_role_mapping": {"龙头": "大型充电/换电设备商", "中军": "充电模块/运营平台", "补涨": "配网接入/充电枪/光储充集成"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "新能源汽车材料链": {
        "theme_name": "新能源汽车材料链",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["锂/钴/镍/锰能源金属", "正极/负极/电解液/隔膜", "铜箔铝箔与结构件"],
        "industry_roles": {"能源金属开采": 0.30, "正极材料": 0.25, "负极与硅碳": 0.20, "电解液与锂盐": 0.15, "隔膜/铜箔铝箔": 0.10},
        "business_dna_tags": ["锂矿", "碳酸锂", "氢氧化锂", "钴", "镍", "锰", "正极材料", "负极材料", "电解液", "隔膜", "铜箔", "铝箔", "PVDF"],
        "weak_positive_tags": ["有色金属", "化工材料", "铜加工"],
        "negative_pressure_tags": {"整车品牌": -0.4, "消费电子": -0.5, "医药": -0.7},
        "industry_soft_constraints": {"能源金属": 0.4, "化学原料": 0.3, "有色金属": 0.3},
        "stock_role_mapping": {"龙头": "锂矿/正极头部", "中军": "负极/电解液/隔膜龙头", "补涨": "PVDF/导电剂/结构件"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },
}
