"""
theme3.json V2 元数据 - 第二部分（生物医药、军工、资源、金融、消费、物理AI、人形机器人、低空经济、商业航天）
"""

THEME_V2_META_PART2 = {

    # ------------------------------------------------------------------
    # 生物医药
    # ------------------------------------------------------------------
    "创新医药主线": {
        "theme_name": "创新医药主线",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["创新药ADC/GLP-1/双抗等新一代生物药", "license-out出海授权", "临床数据与NDA/BLA"],
        "industry_roles": {"创新药与Biotech": 0.35, "ADC/双抗/GLP-1": 0.25, "mRNA/核酸药物": 0.15, "License-out与合作": 0.15, "First-in-class/最佳同类": 0.10},
        "business_dna_tags": ["创新药", "ADC", "GLP-1", "PD-1", "单抗", "双抗", "mRNA", "抗体药物", "临床数据", "NDA", "BLA", "出海授权", "license-out", "突破性疗法"],
        "weak_positive_tags": ["CXO", "CDMO", "医疗器械"],
        "negative_pressure_tags": {"仿制药": -0.5, "中药饮片": -0.3, "保健品": -0.5, "消费电子": -0.7, "能源开采": -0.6},
        "industry_soft_constraints": {"化学制药": 0.5, "生物制品": 0.5},
        "stock_role_mapping": {"龙头": "大药企或ADC/GLP-1头部", "中军": "Biotech或临床阶段创新", "补涨": "license-out/合作企业"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "CXO周期修复链": {
        "theme_name": "CXO周期修复链",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["CRO/CDMO海外订单回暖", "创新药研发外包复苏", "产能利用率修复"],
        "industry_roles": {"CDMO与生产外包": 0.35, "CRO研发服务": 0.30, "临床CRO": 0.15, "API与中间体CDMO": 0.10, "安评毒理": 0.10},
        "business_dna_tags": ["CXO", "CRO", "CDMO", "订单回暖", "海外订单", "研发外包", "产能利用率", "CMC", "IND申报", "临床前", "安评", "GLP实验室"],
        "weak_positive_tags": ["原料药", "精细化工", "实验室耗材"],
        "negative_pressure_tags": {"创新药Biotech": -0.3, "消费电子": -0.7, "煤炭石油": -0.6},
        "industry_soft_constraints": {"医疗服务": 0.5, "化学制药": 0.5},
        "stock_role_mapping": {"龙头": "大型CDMO或综合CRO", "中军": "临床CRO或安评", "补涨": "API/中间体/实验室耗材"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "医疗AI智能化": {
        "theme_name": "医疗AI智能化",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["AI制药与分子模拟", "医学影像AI辅助诊断", "数字病理与大模型医疗应用"],
        "industry_roles": {"AI制药与研发": 0.35, "医学影像AI": 0.30, "数字病理": 0.20, "医疗大模型": 0.15},
        "business_dna_tags": ["AI医疗", "AI制药", "药物发现", "分子模拟", "医疗大模型", "AI诊断", "影像识别", "数字病理", "R&D AI"],
        "weak_positive_tags": ["医疗器械", "创新药", "软件开发"],
        "negative_pressure_tags": {"传统医院服务": -0.4, "医药零售": -0.5, "消费电子": -0.6, "煤炭石油": -0.7},
        "industry_soft_constraints": {"软件开发": 0.4, "医疗器械": 0.4, "IT服务Ⅱ": 0.2},
        "stock_role_mapping": {"龙头": "AI制药平台或影像AI头部", "中军": "医疗设备+AI集成", "补涨": "医院信息化/数字病理配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "合成生物": {
        "theme_name": "合成生物",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["合成生物学与微生物改造", "生物基材料与可降解", "工业生物技术发酵工程"],
        "industry_roles": {"合成生物平台": 0.30, "生物基材料": 0.25, "工业发酵工程": 0.20, "PLA/PHA可降解": 0.15, "特种氨基酸/长链二元酸": 0.10},
        "business_dna_tags": ["合成生物", "生物制造", "工业生物技术", "生物基材料", "生物可降解", "PLA", "PHA", "生物发酵", "发酵工程", "菌种改造", "酶工程", "细胞工厂", "CO2固定", "生物合成"],
        "weak_positive_tags": ["精细化工", "农产品加工", "食品工业"],
        "negative_pressure_tags": {"石油化工传统路线": -0.4, "创新药研发": -0.3, "消费电子": -0.6},
        "industry_soft_constraints": {"生物制品": 0.4, "农化制品": 0.3, "食品加工": 0.15, "化学原料": 0.15},
        "stock_role_mapping": {"龙头": "合成生物平台或工业发酵头部", "中军": "长链二元酸/氨基酸/特种发酵", "补涨": "菌种/酶工程/生物制造配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "脑机接口": {
        "theme_name": "脑机接口",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["侵入式/非侵入式脑电信号采集", "神经调控与神经反馈", "脑控设备与植入式芯片"],
        "industry_roles": {"脑电采集与设备": 0.30, "植入式BCI芯片": 0.25, "神经调控治疗": 0.25, "脑控应用": 0.20},
        "business_dna_tags": ["脑机接口", "BCI", "神经接口", "脑电信号", "神经调控", "神经反馈", "脑科学", "脑控", "植入式芯片", "脑电设备"],
        "weak_positive_tags": ["医疗器械", "神经科药物", "AI芯片"],
        "negative_pressure_tags": {"传统医院服务": -0.4, "消费品": -0.6, "煤炭石油": -0.7},
        "industry_soft_constraints": {"医疗器械": 0.5, "生物制品": 0.3, "软件开发": 0.2},
        "stock_role_mapping": {"龙头": "脑电设备/植入式BCI领先", "中军": "神经调控设备厂商", "补涨": "配套传感器/电极/耗材"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "中药": {
        "theme_name": "中药",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["品牌中药与保密品种", "中药配方颗粒与创新药", "国家基药与医保目录"],
        "industry_roles": {"品牌中成药": 0.35, "中药配方颗粒": 0.25, "中药创新药": 0.15, "中药饮片与药材": 0.15, "中药出海": 0.10},
        "business_dna_tags": ["中药", "中成药", "中药材", "配方颗粒", "中药创新药", "中药OTC", "品牌中药", "保密品种", "国家基药", "医保目录", "中药现代化", "中药出海"],
        "weak_positive_tags": ["医药商业", "保健品", "农林牧渔"],
        "negative_pressure_tags": {"创新药生物药": -0.3, "消费电子": -0.6, "能源开采": -0.6},
        "industry_soft_constraints": {"中药Ⅱ": 0.6, "中药生产": 0.4},
        "stock_role_mapping": {"龙头": "品牌中药保密品种", "中军": "配方颗粒或OTC", "补涨": "中药材/中药创新药/出海"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "医疗器械": {
        "theme_name": "医疗器械",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["高端影像设备与进口替代", "高值耗材与骨科/心脏介入", "IVD体外诊断与PET-CT/MRI"],
        "industry_roles": {"影像设备(CT/MRI/PET)": 0.25, "高值耗材(骨科/支架)": 0.25, "IVD体外诊断": 0.20, "眼科/牙科": 0.15, "监护生命支持": 0.15},
        "business_dna_tags": ["医疗器械", "高值耗材", "医疗设备", "IVD", "体外诊断", "CT", "MRI", "PET-CT", "超声", "内窥镜", "手术机器人", "骨科植入", "心脏支架", "人工关节", "起搏器", "眼科", "OK镜", "ICL", "牙科", "种植牙"],
        "weak_positive_tags": ["创新药", "医疗服务", "精密制造"],
        "negative_pressure_tags": {"消费电子": -0.5, "煤炭石油": -0.6, "传统纺织": -0.7},
        "industry_soft_constraints": {"医疗器械": 1.0},
        "stock_role_mapping": {"龙头": "大型影像设备或综合器械", "中军": "IVD龙头/高值耗材", "补涨": "专科(眼科/牙科/机器人)"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    # ------------------------------------------------------------------
    # 军工
    # ------------------------------------------------------------------
    "军工": {
        "theme_name": "军工",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["军机/舰船/导弹等主战装备", "军工电子与相控阵雷达", "军用发动机与复合材料"],
        "industry_roles": {"航空航天装备": 0.25, "航海与舰船装备": 0.20, "军工电子与雷达": 0.20, "航空发动机": 0.15, "地面装备与弹药": 0.10, "军用复合材料": 0.10},
        "business_dna_tags": ["战斗机", "运输机", "直升机", "教练机", "航空发动机", "涡扇", "涡轴", "燃气轮机", "无人机", "军用无人机", "导弹", "精确制导", "雷达", "相控阵雷达", "电子对抗", "电子战", "军工电子", "军用芯片", "红外探测", "热成像", "装甲车", "坦克", "火炮", "弹药", "火控系统", "航空母舰", "驱逐舰", "护卫舰", "潜艇", "舰船动力", "复合材料", "钛合金", "高温合金", "隐身材料", "吸波材料", "航天器", "卫星", "火箭发动机", "固体火箭", "液体火箭", "军事通信", "数据链", "军用计算机", "军用软件", "军贸", "军工改革", "资产证券化"],
        "weak_positive_tags": ["民用航空", "船舶制造", "高端装备"],
        "negative_pressure_tags": {"消费电子": -0.6, "医药": -0.7, "传统能源": -0.5, "白酒": -0.4},
        "industry_soft_constraints": {"航天装备Ⅱ": 0.2, "航空装备Ⅱ": 0.2, "军工电子Ⅱ": 0.2, "地面兵装Ⅱ": 0.2, "航海装备Ⅱ": 0.2},
        "stock_role_mapping": {"龙头": "总装厂或发动机主机厂", "中军": "军工电子/导弹/舰船系统", "补涨": "高温合金/钛合金/隐身材料/配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "核聚变": {
        "theme_name": "核聚变",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["可控核聚变与托卡马克", "超导磁体与第一壁", "氚增殖与偏滤器材料"],
        "industry_roles": {"超导磁体技术": 0.30, "第一壁与偏滤器": 0.25, "托卡马克装置与工程": 0.20, "高温超导材料": 0.15, "等离子体加热与诊断": 0.10},
        "business_dna_tags": ["托卡马克", "人造太阳", "聚变能源", "超导磁体", "第一壁", "偏滤器", "氚", "聚变堆", "CFETR", "高温超导"],
        "weak_positive_tags": ["核电裂变", "特种金属", "真空技术"],
        "negative_pressure_tags": {"传统化石能源": -0.5, "消费电子": -0.7, "医药": -0.8},
        "industry_soft_constraints": {"其他电源设备Ⅱ": 0.5, "工业金属": 0.3, "通用设备": 0.2},
        "stock_role_mapping": {"龙头": "超导磁体或第一壁材料领先", "中军": "聚变工程参与单位", "补涨": "高温超导/真空设备/诊断配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "船舶制造": {
        "theme_name": "船舶制造",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["LNG船/集装箱船/油轮高端制造", "军舰/航母/驱逐舰建造", "绿色船舶与双燃料动力"],
        "industry_roles": {"大型造船总装": 0.35, "高端船舶(LNG/集装箱)": 0.25, "舰船动力主机": 0.20, "船舶配套与海工": 0.20},
        "business_dna_tags": ["船舶", "造船", "LNG船", "集装箱船", "油轮", "散货船", "军舰", "航母", "驱逐舰", "护卫舰", "船用发动机", "船用主机", "船舶动力", "绿色船舶", "双燃料", "造船订单", "船坞", "海工装备"],
        "weak_positive_tags": ["海工装备", "军工航海", "港口设备"],
        "negative_pressure_tags": {"消费电子": -0.7, "医药": -0.7, "新能源整车": -0.3, "白酒": -0.4},
        "industry_soft_constraints": {"船舶制造": 0.6, "航海装备Ⅱ": 0.4},
        "stock_role_mapping": {"龙头": "大型船舶总装龙头", "中军": "LNG/高端船型配套", "补涨": "主机/动力/设备配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "军工电子与信息化": {
        "theme_name": "军工电子与信息化",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["军用芯片/FPGA/射频", "相控阵雷达与电子战", "数据链与军事通信"],
        "industry_roles": {"军用芯片与FPGA": 0.30, "相控阵雷达与TR组件": 0.30, "数据链与军事通信": 0.20, "红外与光电探测": 0.20},
        "business_dna_tags": ["军工电子", "军用芯片", "FPGA", "TR组件", "相控阵", "雷达", "电子战", "数据链", "军事通信", "红外探测", "光电", "军用计算机", "军工软件"],
        "weak_positive_tags": ["民用射频", "通信设备", "半导体设计"],
        "negative_pressure_tags": {"消费电子": -0.5, "医药": -0.7, "传统能源": -0.5},
        "industry_soft_constraints": {"军工电子Ⅱ": 0.6, "半导体": 0.2, "通信设备": 0.2},
        "stock_role_mapping": {"龙头": "军用芯片或雷达系统总装", "中军": "TR组件/红外/射频", "补涨": "数据链/通信/电源配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },
}
