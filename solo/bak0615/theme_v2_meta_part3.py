"""
theme3.json V2 元数据 - 第三部分（资源、金融、消费、物理AI、人形机器人、低空经济、商业航天）
"""

THEME_V2_META_PART3 = {

    # ------------------------------------------------------------------
    # 资源
    # ------------------------------------------------------------------
    "有色资源": {
        "theme_name": "有色资源",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["铜/铝/锌/铅等工业金属", "新能源金属锂/钴/镍/稀土", "金/银/铂等贵金属"],
        "industry_roles": {"工业金属(铜/铝)": 0.25, "新能源金属(锂/钴/镍)": 0.30, "稀土永磁": 0.20, "贵金属(金/银)": 0.15, "金属加工与铜材": 0.10},
        "business_dna_tags": ["铜矿", "铜矿带", "电解铝", "氧化铝", "铜箔", "铝箔", "铜杆", "铝材", "钴", "镍", "稀土", "钨", "钼", "锂", "黄金", "白银", "铂", "大宗商品", "矿产", "矿产开发", "矿采选"],
        "weak_positive_tags": ["钢铁", "煤炭", "化工", "机械制造"],
        "negative_pressure_tags": {"消费电子": -0.5, "医药": -0.6, "白酒": -0.3, "房地产": -0.4},
        "industry_soft_constraints": {"工业金属": 0.5, "能源金属": 0.3, "贵金属": 0.2},
        "stock_role_mapping": {"龙头": "大型铜矿/电解铝/稀土集团", "中军": "锂/钴/镍/黄金龙头", "补涨": "金属加工/铜箔/铝箔/钨钼"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "煤炭链": {
        "theme_name": "煤炭链",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["动力煤/焦煤/焦炭生产", "煤炭开采与洗选", "煤化工与煤电一体化"],
        "industry_roles": {"动力煤开采": 0.30, "焦煤与焦炭": 0.25, "煤化工与甲醇": 0.20, "煤炭运输与港口": 0.15, "煤电一体化": 0.10},
        "business_dna_tags": ["煤炭", "动力煤", "焦煤", "焦炭", "煤矿", "煤化工", "甲醇", "煤制烯烃", "洗选煤", "煤电", "煤炭运输"],
        "weak_positive_tags": ["火电", "钢铁", "化工"],
        "negative_pressure_tags": {"新能源": -0.5, "半导体": -0.6, "医药": -0.7, "消费电子": -0.6},
        "industry_soft_constraints": {"煤炭开采": 0.6, "煤化工": 0.2, "火力发电": 0.2},
        "stock_role_mapping": {"龙头": "大型煤企集团", "中军": "焦煤焦炭或煤化工", "补涨": "煤炭运输/洗选/港口配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "稀土永磁": {
        "theme_name": "稀土永磁",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["稀土氧化物分离与冶炼", "钕铁硼/钐钴永磁材料", "稀土磁材在电机/风电/消费电子应用"],
        "industry_roles": {"稀土矿与分离": 0.35, "高性能钕铁硼": 0.30, "磁材应用与器件": 0.20, "稀土回收": 0.15},
        "business_dna_tags": ["稀土", "钕铁硼", "钐钴", "永磁", "稀土矿", "轻稀土", "中重稀土", "磁材", "磁性材料", "稀土氧化物", "镨钕", "镝铽"],
        "weak_positive_tags": ["新能源车电机", "风电", "工业电机"],
        "negative_pressure_tags": {"消费电子终端": -0.4, "医药": -0.7, "煤炭石油": -0.5},
        "industry_soft_constraints": {"有色金属": 0.7, "能源金属": 0.3},
        "stock_role_mapping": {"龙头": "大型稀土集团或磁材龙头", "中军": "高性能钕铁硼", "补涨": "稀土回收/磁材器件配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "贵金属与黄金": {
        "theme_name": "贵金属与黄金",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["黄金开采与冶炼", "白银/铂系金属", "黄金饰品与投资", "央行动态与去美元化"],
        "industry_roles": {"黄金开采与冶炼": 0.40, "黄金饰品与零售": 0.25, "白银与铂系金属": 0.20, "黄金回收与投资": 0.15},
        "business_dna_tags": ["黄金", "金矿", "白银", "铂", "钯", "贵金属", "金饰品", "金条", "黄金ETF", "去美元化", "避险"],
        "weak_positive_tags": ["珠宝零售", "有色金属", "银行"],
        "negative_pressure_tags": {"半导体": -0.5, "医药": -0.6, "新能源车": -0.4, "白酒": -0.3},
        "industry_soft_constraints": {"贵金属": 0.5, "有色金属": 0.3, "饰品": 0.2},
        "stock_role_mapping": {"龙头": "大型黄金矿业集团", "中军": "黄金饰品或白银", "补涨": "黄金回收/铂系/投资品"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "基础化工": {
        "theme_name": "基础化工",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["基础化学品与大宗化工", "精细化工与新材料", "化肥农药与农化"],
        "industry_roles": {"大宗化工品": 0.30, "精细化工": 0.25, "化肥与农药": 0.20, "化学原料": 0.15, "化工新材料": 0.10},
        "business_dna_tags": ["化工", "纯碱", "烧碱", "PVC", "涤纶", "MDI", "钛白粉", "化肥", "农药", "草甘膦", "精细化工", "化工新材料"],
        "weak_positive_tags": ["农业", "纺织", "建筑材料"],
        "negative_pressure_tags": {"消费电子": -0.5, "医药生物": -0.4, "半导体": -0.3, "白酒": -0.3},
        "industry_soft_constraints": {"化学原料": 0.5, "化学制品": 0.3, "农药化肥": 0.2},
        "stock_role_mapping": {"龙头": "大宗化工龙头(MDI/纯碱/涤纶)", "中军": "精细化工或新材料", "补涨": "农化/农药/化肥"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "钢铁": {
        "theme_name": "钢铁",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["长材/板材/特钢", "铁矿石与炼钢上游", "特钢与高端装备用钢"],
        "industry_roles": {"长材与建筑用钢": 0.25, "板材与制造用钢": 0.25, "特钢与高端": 0.25, "铁矿石与原料": 0.15, "不锈钢": 0.10},
        "business_dna_tags": ["钢铁", "钢材", "板材", "长材", "螺纹钢", "热轧", "冷轧", "特钢", "不锈钢", "铁矿石", "焦煤", "焦炭", "高炉", "电炉钢"],
        "weak_positive_tags": ["煤炭", "基建", "机械装备", "造船"],
        "negative_pressure_tags": {"医药": -0.7, "半导体": -0.6, "消费电子": -0.5, "白酒": -0.3},
        "industry_soft_constraints": {"钢铁": 0.7, "普钢": 0.15, "特钢": 0.15},
        "stock_role_mapping": {"龙头": "大型钢铁集团或特钢龙头", "中军": "板材/长材区域龙头", "补涨": "特钢细分/不锈钢/铁合金"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    # ------------------------------------------------------------------
    # 金融
    # ------------------------------------------------------------------
    "券商": {
        "theme_name": "券商",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["证券经纪与投行", "资产管理与自营", "资本市场活跃带来的业绩弹性"],
        "industry_roles": {"综合券商龙头": 0.40, "投行与资管特色券商": 0.25, "互联网经纪": 0.20, "参股券商": 0.15},
        "business_dna_tags": ["券商", "证券公司", "投行", "经纪", "自营", "资管", "公募", "基金", "财富管理", "投行业务", "资本市场"],
        "weak_positive_tags": ["银行", "保险", "金融科技"],
        "negative_pressure_tags": {"消费电子": -0.7, "医药": -0.8, "半导体": -0.5, "新能源": -0.5},
        "industry_soft_constraints": {"证券": 0.7, "多元金融": 0.3},
        "stock_role_mapping": {"龙头": "头部综合大券商", "中军": "区域/特色投行券商", "补涨": "参股券商或金融科技"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "银行": {
        "theme_name": "银行",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["国有大行/股份制/城商行", "零售银行与财富管理", "高股息与低估值修复"],
        "industry_roles": {"国有大行": 0.30, "股份制银行": 0.30, "城商行与农商行": 0.25, "零售与财富管理": 0.15},
        "business_dna_tags": ["银行", "大行", "股份制银行", "城商行", "农商行", "零售银行", "财富管理", "净息差", "高股息", "金融稳定"],
        "weak_positive_tags": ["保险", "券商", "高股息公用事业"],
        "negative_pressure_tags": {"半导体": -0.6, "医药": -0.6, "消费电子": -0.5, "新能源": -0.4},
        "industry_soft_constraints": {"银行": 1.0},
        "stock_role_mapping": {"龙头": "国有大行或头部股份行", "中军": "特色零售银行", "补涨": "城商行/区域银行"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "保险": {
        "theme_name": "保险",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["寿险与财产险保险公司", "代理人渠道与NBV改善", "长端利率上行与投资收益"],
        "industry_roles": {"大型保险集团": 0.40, "寿险公司": 0.30, "财产险公司": 0.20, "保险科技与中介": 0.10},
        "business_dna_tags": ["保险", "寿险", "财险", "健康险", "代理人", "NBV", "新业务价值", "长端利率", "保险科技", "互联网保险"],
        "weak_positive_tags": ["银行", "券商", "高股息"],
        "negative_pressure_tags": {"半导体": -0.6, "医药": -0.6, "消费电子": -0.5},
        "industry_soft_constraints": {"保险": 0.8, "多元金融": 0.2},
        "stock_role_mapping": {"龙头": "大型保险集团", "中军": "特色财险/寿险", "补涨": "保险科技/中介/参股保险"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "金融科技": {
        "theme_name": "金融科技",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["支付/征信/数字货币", "证券与基金IT系统", "金融信息服务与数据"],
        "industry_roles": {"证券基金IT": 0.30, "支付与清算": 0.25, "金融信息服务": 0.25, "区块链与数字人民币": 0.20},
        "business_dna_tags": ["金融科技", "支付", "清算", "数字人民币", "证券IT", "基金IT", "金融信息", "行情数据", "区块链", "智能投顾"],
        "weak_positive_tags": ["软件开发", "银行", "券商"],
        "negative_pressure_tags": {"医药": -0.7, "半导体": -0.4, "消费电子": -0.5, "新能源": -0.4},
        "industry_soft_constraints": {"软件开发": 0.5, "多元金融": 0.3, "IT服务Ⅱ": 0.2},
        "stock_role_mapping": {"龙头": "证券基金IT或支付龙头", "中军": "金融信息服务公司", "补涨": "数字人民币/区块链应用"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "多元金融": {
        "theme_name": "多元金融",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["信托/租赁/AMC/期货", "类金融服务", "资产管理与不良资产处置"],
        "industry_roles": {"资产管理与信托": 0.35, "金融租赁": 0.25, "期货与AMC": 0.25, "小额贷款与典当": 0.15},
        "business_dna_tags": ["信托", "租赁", "AMC", "不良资产", "期货", "资产管理", "小额贷款", "典当", "类金融"],
        "weak_positive_tags": ["银行", "券商", "高股息"],
        "negative_pressure_tags": {"医药": -0.7, "半导体": -0.6, "消费电子": -0.6, "新能源": -0.4},
        "industry_soft_constraints": {"多元金融": 1.0},
        "stock_role_mapping": {"龙头": "大型资管或AMC", "中军": "金融租赁/期货", "补涨": "小贷/典当/特色金融"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    # ------------------------------------------------------------------
    # 消费
    # ------------------------------------------------------------------
    "必选消费红利链": {
        "theme_name": "必选消费红利链",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["食品饮料/乳业/调味品等高股息龙头", "农业与农产品加工", "商超与食品制造"],
        "industry_roles": {"食品饮料龙头": 0.35, "乳业": 0.20, "调味品与食品制造": 0.20, "农业与农副": 0.15, "商超零售": 0.10},
        "business_dna_tags": ["食品饮料", "乳业", "奶粉", "调味品", "酱油", "食用油", "米面", "肉禽", "水产", "农业", "高股息消费", "商超", "零售"],
        "weak_positive_tags": ["白酒", "家电", "医药零售"],
        "negative_pressure_tags": {"半导体": -0.5, "新能源车": -0.4, "军工": -0.4},
        "industry_soft_constraints": {"食品饮料": 0.4, "农业": 0.3, "零售": 0.3},
        "stock_role_mapping": {"龙头": "食品饮料乳业龙头", "中军": "调味品/米面/农副", "补涨": "商超零售或高股息农业"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "情绪消费成长链": {
        "theme_name": "情绪消费成长链",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["连锁餐饮/茶饮/零食", "免税与旅游零售", "网红消费与电商渠道"],
        "industry_roles": {"连锁餐饮茶饮": 0.35, "旅游与免税零售": 0.25, "休闲食品/零食连锁": 0.20, "美妆个护": 0.10, "电商与MCN": 0.10},
        "business_dna_tags": ["连锁餐饮", "奶茶", "咖啡", "零食", "免税", "旅游", "美妆", "个护", "电商", "MCN", "网红经济", "直播带货", "消费复苏"],
        "weak_positive_tags": ["白酒", "商业零售", "影视娱乐"],
        "negative_pressure_tags": {"半导体": -0.5, "医药": -0.5, "军工": -0.4, "传统能源": -0.3},
        "industry_soft_constraints": {"餐饮": 0.4, "零售": 0.3, "旅游Ⅱ": 0.3},
        "stock_role_mapping": {"龙头": "连锁餐饮或免税龙头", "中军": "零食连锁/旅游", "补涨": "美妆/电商配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "消费电子": {
        "theme_name": "消费电子",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["智能手机/PC/平板/可穿戴", "AI终端/MR/AI眼镜", "苹果链/安卓链创新"],
        "industry_roles": {"智能手机链": 0.30, "MR/VR/AR/可穿戴": 0.25, "消费电子零部件": 0.25, "AI终端硬件": 0.20},
        "business_dna_tags": ["智能手机", "AI手机", "AI PC", "MR", "VR", "AR", "可穿戴", "折叠屏", "苹果链", "安卓链", "消费电子零部件", "显示面板", "摄像头", "结构件", "被动元件"],
        "weak_positive_tags": ["半导体", "面板", "精密制造"],
        "negative_pressure_tags": {"传统家电": -0.4, "医药": -0.5, "煤炭": -0.5},
        "industry_soft_constraints": {"消费电子": 0.6, "光学光电子": 0.2, "电子制造": 0.2},
        "stock_role_mapping": {"龙头": "消费电子制造龙头或苹果链", "中军": "MR/可穿戴/AI硬件", "补涨": "被动元件/结构件/配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "智能驾驶": {
        "theme_name": "智能驾驶",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["汽车智能化/智驾/座舱", "车载电子与车规芯片", "汽车软件与域控制器"],
        "industry_roles": {"智驾与域控制器": 0.35, "智能座舱": 0.25, "车规芯片与传感器": 0.20, "车载软件与OS": 0.10, "车路协同/V2X": 0.10},
        "business_dna_tags": ["智能驾驶", "自动驾驶", "NOA", "域控制器", "智驾芯片", "智能座舱", "车载芯片", "车规级", "车载OS", "车联网", "V2X", "车路协同", "OTA", "毫米波雷达", "激光雷达", "摄像头"],
        "weak_positive_tags": ["汽车零部件", "汽车电子", "半导体"],
        "negative_pressure_tags": {"消费电子终端": -0.4, "医药": -0.5, "煤炭石油": -0.5},
        "industry_soft_constraints": {"汽车零部件": 0.5, "软件开发": 0.3, "半导体": 0.2},
        "stock_role_mapping": {"龙头": "智驾域控或座舱龙头", "中军": "车规芯片/车载软件", "补涨": "传感器/线束/配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "白酒": {
        "theme_name": "白酒",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["高端/次高端/区域白酒", "白酒渠道与动销", "品牌力与提价能力"],
        "industry_roles": {"高端白酒": 0.40, "次高端白酒": 0.30, "区域白酒": 0.20, "白酒渠道与配套": 0.10},
        "business_dna_tags": ["白酒", "高端白酒", "次高端", "酱酒", "浓香型", "清香型", "品牌白酒", "白酒渠道", "宴席消费", "送礼"],
        "weak_positive_tags": ["食品饮料", "商业零售", "餐饮"],
        "negative_pressure_tags": {"半导体": -0.5, "新能源": -0.4, "医药": -0.3, "军工": -0.4},
        "industry_soft_constraints": {"白酒": 0.7, "食品饮料": 0.3},
        "stock_role_mapping": {"龙头": "高端白酒龙头", "中军": "次高端白酒", "补涨": "区域白酒或渠道配套"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },

    "家电家装": {
        "theme_name": "家电家装",
        "version": "V2",
        "theme_type": "产业链主题",
        "core_semantic": ["白电/厨电/小家电", "以旧换新与出口", "家居建材与装修链"],
        "industry_roles": {"白电(空调/冰箱/洗衣机)": 0.35, "厨电与小家电": 0.25, "家居建材": 0.20, "装修与装饰": 0.10, "家电出口制造": 0.10},
        "business_dna_tags": ["白电", "空调", "冰箱", "洗衣机", "厨电", "小家电", "智能家居", "以旧换新", "家电出口", "家居", "建材", "装修", "装饰"],
        "weak_positive_tags": ["家具", "建材", "消费零售"],
        "negative_pressure_tags": {"半导体": -0.5, "医药": -0.5, "新能源车": -0.4},
        "industry_soft_constraints": {"白色家电": 0.4, "小家电": 0.3, "家居装饰": 0.3},
        "stock_role_mapping": {"龙头": "大型白电集团", "中军": "厨电/小家电龙头", "补涨": "家居建材/装修链"},
        "matching_strategy": {"mode": "semantic_business_hybrid", "embedding_weight": 0.55, "business_weight": 0.30, "industry_weight": 0.15}
    },
}
