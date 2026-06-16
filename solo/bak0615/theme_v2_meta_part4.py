"""
theme3.json V2 元数据 - 第四部分（物理AI、人形机器人、低空经济、商业航天）
"""

THEME_V2_META_PART4 = {

    # ------------------------------------------------------------------
    # 物理AI
    # ------------------------------------------------------------------
    "具身智能大模型": {
        "theme_name": "具身智能大模型",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["物理世界通用人工智能/具身AGI", "机器人基础大模型与多模态", "视觉-语言-动作统一模型"],
        "industry_roles": {"具身大模型平台": 0.35, "多模态与视觉语言": 0.25, "机器人智能体": 0.20, "运动控制与强化学习": 0.20},
        "business_dna_tags": ["具身智能", "物理AI", "具身大模型", "机器人GPT", "VLA", "视觉语言动作", "多模态", "机器人智能体", "通用人工智能", "AGI", "强化学习", "基础模型"],
        "weak_positive_tags": ["AI大模型", "机器人制造", "机器视觉"],
        "negative_pressure_tags": {"消费电子": -0.5, "医药": -0.7, "传统能源": -0.5, "白酒": -0.4},
        "industry_soft_constraints": {"软件开发": 0.4, "IT服务Ⅱ": 0.3, "自动化设备": 0.3},
        "stock_role_mapping": {"龙头": "具身大模型平台或通用AI龙头", "中军": "多模态/机器人智能体", "补涨": "运动控制/强化学习应用"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "机器视觉与3D感知": {
        "theme_name": "机器视觉与3D感知",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["工业/服务机器人视觉系统", "3D视觉/深度相机/激光", "SLAM与环境感知"],
        "industry_roles": {"机器视觉系统": 0.35, "3D视觉/深度相机": 0.30, "SLAM与定位": 0.20, "图像传感器与摄像头": 0.15},
        "business_dna_tags": ["机器视觉", "3D视觉", "深度相机", "激光雷达", "视觉定位", "SLAM", "环境感知", "图像传感器", "视觉算法", "视觉检测"],
        "weak_positive_tags": ["安防监控", "半导体图像传感器", "工业自动化"],
        "negative_pressure_tags": {"医药": -0.7, "煤炭石油": -0.6, "消费电子纯终端": -0.4},
        "industry_soft_constraints": {"自动化设备": 0.4, "光学光电子": 0.3, "软件开发": 0.3},
        "stock_role_mapping": {"龙头": "机器视觉系统龙头", "中军": "3D视觉/深度相机厂商", "补涨": "SLAM/图像传感器配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "传感器": {
        "theme_name": "传感器",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["MEMS/惯性/力觉/触觉传感器", "毫米波/超声波/激光", "温度/压力/气体工业传感"],
        "industry_roles": {"MEMS与惯性传感器": 0.30, "激光/毫米波雷达": 0.25, "力觉/触觉传感器": 0.20, "工业传感器": 0.15, "图像与视觉传感器": 0.10},
        "business_dna_tags": ["传感器", "MEMS", "惯性测量", "IMU", "六维力", "力矩传感器", "触觉传感器", "激光雷达", "毫米波", "超声波", "工业传感器", "压力传感", "温度传感", "气体传感"],
        "weak_positive_tags": ["半导体设计", "消费电子", "汽车电子"],
        "negative_pressure_tags": {"传统煤炭": -0.6, "医药": -0.6, "白酒": -0.4},
        "industry_soft_constraints": {"半导体": 0.4, "电子制造": 0.3, "自动化设备": 0.3},
        "stock_role_mapping": {"龙头": "MEMS/工业传感器龙头", "中军": "激光/毫米波/力觉厂商", "补涨": "触觉/气体/视觉配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "边缘计算与AI芯片": {
        "theme_name": "边缘计算与AI芯片",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["边缘AI推理芯片", "FPGA/ASIC/NPU嵌入式加速", "端侧大模型与小模型"],
        "industry_roles": {"边缘AI芯片": 0.35, "嵌入式FPGA/ASIC": 0.25, "端侧大模型与推理": 0.20, "边缘计算网关与模组": 0.20},
        "business_dna_tags": ["边缘计算", "边缘AI", "AI芯片", "NPU", "FPGA", "ASIC", "端侧推理", "端侧大模型", "边缘网关", "嵌入式AI", "低功耗AI"],
        "weak_positive_tags": ["半导体设计", "通信模组", "工业自动化"],
        "negative_pressure_tags": {"GPU/算力中心": -0.3, "医药": -0.7, "煤炭石油": -0.6},
        "industry_soft_constraints": {"半导体": 0.5, "软件开发": 0.3, "通信设备": 0.2},
        "stock_role_mapping": {"龙头": "边缘AI芯片或FPGA厂商", "中军": "端侧大模型与推理", "补涨": "边缘网关/模组配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "数字孪生与工业仿真": {
        "theme_name": "数字孪生与工业仿真",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["工业数字孪生与仿真软件", "CAE/EDA级物理仿真", "工厂数字化与工业软件"],
        "industry_roles": {"工业仿真与CAE": 0.35, "数字孪生平台": 0.30, "工厂数字化/MES": 0.20, "工业数据与可视化": 0.15},
        "business_dna_tags": ["数字孪生", "工业仿真", "CAE", "工厂数字化", "MES", "工业软件", "物理仿真", "可视化", "工业大数据", "仿真引擎"],
        "weak_positive_tags": ["工业软件", "工业自动化", "云计算"],
        "negative_pressure_tags": {"消费游戏": -0.5, "医药": -0.6, "煤炭石油": -0.5},
        "industry_soft_constraints": {"软件开发": 0.5, "IT服务Ⅱ": 0.3, "自动化设备": 0.2},
        "stock_role_mapping": {"龙头": "工业仿真或数字孪生平台", "中军": "工厂数字化/MES", "补涨": "工业数据/可视化配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    # ------------------------------------------------------------------
    # 人形机器人
    # ------------------------------------------------------------------
    "执行器总成": {
        "theme_name": "执行器总成",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["人形机器人关节执行器", "伺服/谐波/行星减速", "直线/旋转执行器"],
        "industry_roles": {"关节执行器总成": 0.40, "伺服电机与驱动器": 0.25, "减速器集成": 0.20, "传感器与编码器": 0.15},
        "business_dna_tags": ["执行器", "关节模组", "伺服电机", "驱动器", "编码器", "旋转执行器", "直线执行器", "一体化关节", "模组", "关节电机"],
        "weak_positive_tags": ["精密机械", "自动化设备", "伺服系统"],
        "negative_pressure_tags": {"医药": -0.7, "煤炭石油": -0.6, "消费电子": -0.5},
        "industry_soft_constraints": {"自动化设备": 0.5, "通用设备": 0.3, "电机Ⅱ": 0.2},
        "stock_role_mapping": {"龙头": "执行器总成或关节模组龙头", "中军": "伺服电机与驱动器", "补涨": "编码器/传感器配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "精密减速器": {
        "theme_name": "精密减速器",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["谐波减速器", "RV减速器", "行星减速器与人形机器人关节应用"],
        "industry_roles": {"谐波减速器": 0.35, "RV减速器": 0.30, "行星减速器": 0.20, "精密齿轮与轴承": 0.15},
        "business_dna_tags": ["谐波", "减速器", "RV减速器", "行星减速器", "精密齿轮", "轴承", "齿轮箱", "谐波减速器", "精密传动", "关节减速器"],
        "weak_positive_tags": ["工业自动化", "精密制造", "机床"],
        "negative_pressure_tags": {"消费电子": -0.5, "医药": -0.6, "煤炭石油": -0.5},
        "industry_soft_constraints": {"通用设备": 0.5, "自动化设备": 0.3, "机床": 0.2},
        "stock_role_mapping": {"龙头": "谐波/RV减速器龙头", "中军": "行星减速器或精密齿轮", "补涨": "轴承/精密加工配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "伺服电机与运动控制": {
        "theme_name": "伺服电机与运动控制",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["高性能伺服电机/驱动", "运动控制卡与总线", "力矩控制与力觉"],
        "industry_roles": {"伺服电机与驱动": 0.40, "运动控制卡/PLC": 0.30, "力矩控制与力觉": 0.20, "编码器与反馈": 0.10},
        "business_dna_tags": ["伺服", "伺服电机", "驱动器", "运动控制", "运动控制卡", "PLC", "力矩控制", "力觉", "编码器", "总线", "EtherCAT"],
        "weak_positive_tags": ["工业自动化", "数控机床", "机器人制造"],
        "negative_pressure_tags": {"医药": -0.6, "煤炭石油": -0.5, "消费电子终端": -0.4},
        "industry_soft_constraints": {"电机Ⅱ": 0.4, "自动化设备": 0.4, "通用设备": 0.2},
        "stock_role_mapping": {"龙头": "伺服驱动龙头或运动控制", "中军": "高端伺服/力矩控制", "补涨": "编码器/总线配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "灵巧手": {
        "theme_name": "灵巧手",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["多指灵巧手与抓握", "欠驱动/腱传动手", "触觉与力觉手指"],
        "industry_roles": {"多指灵巧手整机": 0.40, "微型执行器与腱传动": 0.30, "触觉与力觉传感": 0.20, "抓取规划与算法": 0.10},
        "business_dna_tags": ["灵巧手", "多指手", "腱传动", "欠驱动", "仿人手", "抓握", "触觉手指", "微执行器", "抓取算法"],
        "weak_positive_tags": ["精密机械", "微电机", "传感器"],
        "negative_pressure_tags": {"医药": -0.7, "煤炭石油": -0.6, "消费电子终端": -0.4},
        "industry_soft_constraints": {"自动化设备": 0.5, "通用设备": 0.3, "电机Ⅱ": 0.2},
        "stock_role_mapping": {"龙头": "多指灵巧手或仿人手机构", "中军": "微型执行器与腱传动", "补涨": "触觉/力觉传感配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "行星滚柱丝杠": {
        "theme_name": "行星滚柱丝杠",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["行星滚柱丝杠与直线执行器", "高负载精密直线传动", "人形机器人腿部与腰部关节"],
        "industry_roles": {"行星滚柱丝杠": 0.40, "滚珠丝杠与精密传动": 0.30, "直线执行器与模组": 0.20, "精密加工与热处理": 0.10},
        "business_dna_tags": ["行星滚柱丝杠", "滚珠丝杠", "精密丝杠", "直线执行器", "线性模组", "精密传动", "高负载", "精密加工", "热处理"],
        "weak_positive_tags": ["精密机械", "机床", "自动化设备"],
        "negative_pressure_tags": {"医药": -0.7, "煤炭石油": -0.6, "消费电子": -0.5},
        "industry_soft_constraints": {"通用设备": 0.5, "机床": 0.3, "自动化设备": 0.2},
        "stock_role_mapping": {"龙头": "行星滚柱丝杠或精密传动", "中军": "滚珠丝杠与精密传动", "补涨": "精密加工/热处理配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "人形机器人整机与集成": {
        "theme_name": "人形机器人整机与集成",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["双足人型机器人整机品牌", "系统集成与场景应用", "工业/服务人形整机"],
        "industry_roles": {"人形机器人整机制造": 0.40, "系统集成与应用": 0.30, "场景运营与租赁": 0.20, "工业/服务机器人": 0.10},
        "business_dna_tags": ["人形机器人", "双足机器人", "具身机器人", "整机", "系统集成", "工业人形", "服务人形", "场景应用", "机器人租赁"],
        "weak_positive_tags": ["自动化设备", "人工智能", "机器视觉"],
        "negative_pressure_tags": {"医药": -0.6, "煤炭石油": -0.5, "白酒": -0.4},
        "industry_soft_constraints": {"自动化设备": 0.6, "通用设备": 0.2, "软件开发": 0.2},
        "stock_role_mapping": {"龙头": "人形机器人整机龙头", "中军": "系统集成商或工业应用", "补涨": "服务/租赁/场景应用"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    # ------------------------------------------------------------------
    # 低空经济
    # ------------------------------------------------------------------
    "低空飞行器制造": {
        "theme_name": "低空飞行器制造",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["eVTOL电动垂直起降", "工业/消费级无人机", "通航飞机与飞行器制造"],
        "industry_roles": {"eVTOL与城市空中交通": 0.35, "无人机整机": 0.30, "通航飞机制造": 0.20, "复合材料与结构件": 0.15},
        "business_dna_tags": ["eVTOL", "电动垂直起降", "UAM", "城市空中交通", "无人机", "工业无人机", "消费级无人机", "通航飞机", "航空器", "飞行器", "飞行汽车"],
        "weak_positive_tags": ["航空制造", "碳纤维", "军工航空"],
        "negative_pressure_tags": {"医药": -0.6, "煤炭石油": -0.5, "消费电子终端": -0.4},
        "industry_soft_constraints": {"航空装备Ⅱ": 0.5, "军工电子Ⅱ": 0.2, "复合材料": 0.3},
        "stock_role_mapping": {"龙头": "eVTOL或无人机整机龙头", "中军": "通航飞机或大型无人机", "补涨": "复合材料/结构件配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "低空运营服务": {
        "theme_name": "低空运营服务",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["低空物流/巡检/农林作业运营", "飞行培训与通航服务", "低空基础设施运营"],
        "industry_roles": {"低空物流与配送运营": 0.35, "电力/能源/应急巡检": 0.25, "农林植保作业": 0.20, "飞行培训/通航服务": 0.20},
        "business_dna_tags": ["低空物流", "无人机物流", "电力巡检", "能源巡检", "应急巡检", "农林植保", "飞行培训", "通航运营", "低空运营", "无人机服务"],
        "weak_positive_tags": ["物流", "电力公用", "农林牧渔"],
        "negative_pressure_tags": {"医药": -0.5, "煤炭石油": -0.4, "白酒": -0.3, "消费电子": -0.4},
        "industry_soft_constraints": {"物流": 0.4, "航空运输": 0.3, "软件": 0.3},
        "stock_role_mapping": {"龙头": "低空物流或大型巡检运营", "中军": "农林植保/飞行培训", "补涨": "低空基础设施运营"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "低空基础设施": {
        "theme_name": "低空基础设施",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["城市空中交通起降场与保障系统", "低空空管与通信导航系统", "无人机运行基础设施网络"],
        "industry_roles": {"基础设施建设与起降场": 0.35, "通信网络与导航": 0.25, "能源补给与充电换电": 0.20, "空管与空域管理系统": 0.20},
        "business_dna_tags": ["起降场", "停机坪", "起降设施", "低空雷达", "空管系统", "通信导航", "UTM", "低空通信", "无人机机场", "充电网络", "城市空域管理"],
        "weak_positive_tags": ["通信设备", "智慧城市", "交通信息化", "电力设备"],
        "negative_pressure_tags": {"军工武器导弹": -0.6, "消费电子终端": -0.4, "医药": -0.5},
        "industry_soft_constraints": {"通信服务": 0.3, "基础建设": 0.3, "机场Ⅱ": 0.2, "电力设备": 0.2},
        "stock_role_mapping": {"龙头": "低空基础设施或空管系统", "中军": "通信导航/起降场建设", "补涨": "充电网络/空域管理配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "低空数据与控制": {
        "theme_name": "低空数据与控制",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["低空通信网络与数据链", "无人机飞控与导航芯片", "UTM飞行管理与调度平台"],
        "industry_roles": {"低空通信数据链与导航": 0.35, "飞控与导航芯片": 0.30, "UTM与飞行调度": 0.20, "低空地理与测绘": 0.15},
        "business_dna_tags": ["飞控", "飞控芯片", "导航芯片", "北斗", "低空通信", "数据链", "UTM", "无人机交通管理", "低空测绘", "地理信息", "卫星导航"],
        "weak_positive_tags": ["通信设备", "半导体", "软件开发"],
        "negative_pressure_tags": {"医药": -0.5, "煤炭石油": -0.5, "白酒": -0.3},
        "industry_soft_constraints": {"通信设备": 0.4, "软件开发": 0.3, "半导体": 0.3},
        "stock_role_mapping": {"龙头": "飞控芯片或UTM平台", "中军": "低空通信数据链", "补涨": "北斗/地理信息/测绘"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    # ------------------------------------------------------------------
    # 商业航天
    # ------------------------------------------------------------------
    "卫星制造与发射": {
        "theme_name": "卫星制造与发射",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["卫星平台与载荷制造", "商业火箭与可回收", "卫星互联网与低轨星座"],
        "industry_roles": {"卫星平台与制造": 0.30, "商业火箭与发射服务": 0.30, "低轨星座与卫星互联网": 0.20, "卫星载荷与元器件": 0.20},
        "business_dna_tags": ["卫星", "卫星制造", "商业火箭", "可回收火箭", "低轨卫星", "LEO", "卫星互联网", "星座", "载荷", "星载芯片", "卫星平台"],
        "weak_positive_tags": ["航天军工", "半导体", "通信"],
        "negative_pressure_tags": {"医药": -0.6, "煤炭石油": -0.5, "消费电子终端": -0.4},
        "industry_soft_constraints": {"航天装备Ⅱ": 0.5, "军工电子Ⅱ": 0.3, "通信设备": 0.2},
        "stock_role_mapping": {"龙头": "卫星制造或商业火箭龙头", "中军": "低轨星座/卫星互联网", "补涨": "载荷/星载芯片配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "卫星运营": {
        "theme_name": "卫星运营",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["卫星通信运营与服务", "遥感卫星数据与应用", "卫星导航与位置服务"],
        "industry_roles": {"卫星通信运营": 0.35, "遥感数据与应用": 0.30, "卫星导航与位置服务": 0.25, "地面站与测控": 0.10},
        "business_dna_tags": ["卫星运营", "卫星通信", "遥感", "地理信息", "北斗", "GNSS", "位置服务", "卫星数据", "地面站", "测控", "卫星互联网服务"],
        "weak_positive_tags": ["通信运营", "软件", "测绘地理"],
        "negative_pressure_tags": {"医药": -0.6, "煤炭石油": -0.4, "白酒": -0.3},
        "industry_soft_constraints": {"通信服务": 0.6, "软件开发": 0.2, "IT服务Ⅱ": 0.2},
        "stock_role_mapping": {"龙头": "卫星通信或北斗运营龙头", "中军": "遥感数据与地理信息", "补涨": "地面站/测控配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "卫星应用": {
        "theme_name": "卫星应用",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["直连手机/直连消费终端", "车规与智能驾驶卫星应用", "应急与海洋/林业等行业应用"],
        "industry_roles": {"卫星直连终端与消费应用": 0.35, "车规与智能驾驶卫星": 0.25, "行业应用(应急/海洋/林业)": 0.25, "卫星物联网": 0.15},
        "business_dna_tags": ["卫星直连", "卫星手机", "直连终端", "车规卫星", "车载卫星", "卫星物联网", "NTN", "非地面网络", "应急通信", "海洋遥感", "林业应用"],
        "weak_positive_tags": ["消费电子", "车联网", "通信设备"],
        "negative_pressure_tags": {"医药": -0.5, "煤炭石油": -0.4, "白酒": -0.3},
        "industry_soft_constraints": {"通信设备": 0.4, "消费电子": 0.3, "软件开发": 0.3},
        "stock_role_mapping": {"龙头": "卫星直连终端或车规应用", "中军": "行业应用解决方案", "补涨": "物联网/芯片/终端配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },
}
