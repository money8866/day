# -*- coding: utf-8 -*-
"""
产业链映射配置

本配置定义了核心产业链的标签、关键词映射、用于动态识别个股所属的真实产业链。
这与申万/证监会行业分类不同，是基于"需求链驱动模型"的真实产业关系映射。

重要原则：
- 禁止使用申万行业涨跌判断景气度
- 必须使用真实产业链关系（终端需求 → 中游 → 上游）
"""
import os
import json
from typing import Dict, List, Set
import loguru

logger = loguru.logger

# ============================================================
# 产业链定义
# ============================================================

INDUSTRY_CHAINS = {
    # AI算力产业链：最下游是AI服务器需求
    "AI算力链": {
        "description": "AI服务器 → GPU/HBM → 光模块 → PCB载板 → 散热",
        "terminal_demand": "AI服务器出货量、全球算力投资",
        "key_companies": [
            "浪潮信息", "中科曙光", "华为", "英伟达概念", "AMD概念"
        ],
        "sub_chains": {
            "AI芯片": ["GPU", "HBM", "AI芯片", "NPU", "算力芯片"],
            "光模块": ["光模块", "光通信", "光纤", "光器件"],
            "PCB载板": ["PCB载板", "IC载板", "封装基板"],
            "服务器": ["服务器", "AI服务器", "数据中心"]
        }
    },

    # PCB产业链：受益于AI服务器、汽車电子、消费电子
    "PCB链": {
        "description": "覆铜板 → PCB → 精密加工",
        "terminal_demand": "AI服务器（高多层板）、汽车电子、消费电子",
        "key_companies": [
            "生益科技", "华正新材", "建滔集团", "景旺电子", "胜宏科技", "鹏鼎控股"
        ]
    },

    # 半导体设备产业链
    "半导体设备链": {
        "description": "晶圆厂扩产 → 设备采购 → 材料配套",
        "terminal_demand": "晶圆厂资本开支、中芯国际、华虹半导体",
        "key_companies": [
            "北方华创", "中微公司", "华海清科", "拓荆科技", "盛美半导体",
            "芯源微", "至纯科技"
        ],
        "dna_tags": ["半导体设备", "半导体材料", "PVD", "CVD", "刻蚀", "光刻机", "沉积"]
    },

    # 半导体材料产业链
    "半导体材料链": {
        "description": "硅片 → 光刻胶 → 电子特气 → 靶材",
        "terminal_demand": "晶圆厂需求",
        "key_companies": [
            "沪硅产业", "立昂微", "神工股份", "彤程新材", "华特气体", "江丰电子"
        ]
    },

    # 新能源汽车产业链
    "新能源链": {
        "description": "电动车 → 电池 → 材料 → 设备",
        "terminal_demand": "新能源车销量、动力电池装机量",
        "key_companies": [
            "宁德时代", "比亚迪", "亿纬锂能", "国轩高科", "欣旺达",
            "恩捷股份", "星源材质", "天赐材料", "新宙邦"
        ],
        "sub_chains": {
            "动力电池": ["动力电池", "锂电池", "储能电池"],
            "锂电材料": ["正极", "负极", "隔膜", "电解液"],
            "锂电设备": ["锂电设备", "电芯制造"]
        }
    },

    # 机器人产业链
    "机器人链": {
        "description": "工业机器人 → 核心零部件 → 系统集成",
        "terminal_demand": "制造业资本开支、自动化改造需求",
        "key_companies": [
            "汇川技术", "埃斯顿", "绿的谐波", "机器人", "拓斯达",
            "凯尔达", "禾川科技"
        ],
        "sub_chains": {
            "减速器": ["减速器", "谐波减速器", "RV减速器"],
            "伺服系统": ["伺服", "伺服电机", "伺服驱动"],
            "控制器": ["控制器", "PLC", "工业自动化"]
        }
    },

    # 消费电子产业链
    "消费电子链": {
        "description": "手机 → 零部件 → 材料",
        "terminal_demand": "智能手机出货量、可穿戴设备",
        "key_companies": [
            "立讯精密", "歌尔股份", "蓝思科技", "领益智造", "鹏鼎控股",
            "韦尔股份", "舜宇光学"
        ],
        "sub_chains": {
            "光学": ["光学", "摄像头", "镜头", "CMOS"],
            "显示": ["面板", "显示屏", "OLED", "MiniLED"],
            "精密制造": ["精密制造", "结构件", "连接器"]
        }
    },

    # 低空经济产业链
    "低空经济链": {
        "description": "eVTOL → 无人机 → 低空基础设施 → 运营服务",
        "terminal_demand": "低空经济政策、新兴出行需求",
        "key_companies": [
            "亿航智能", "小鹏汽车", "大疆创新", "航天电子", "中直股份"
        ],
        "sub_chains": {
            "飞行器": ["eVTOL", "无人机", "直升机"],
            "基础设施": ["低空通信", "低空导航", "低空雷达"],
            "运营": ["低空运营", "低空服务"]
        }
    },

    # 光伏产业链
    "光伏链": {
        "description": "硅料 → 硅片 → 电池片 → 组件 → 电站",
        "terminal_demand": "光伏装机量、组件出口",
        "key_companies": [
            "隆基绿能", "通威股份", "天合光能", "晶澳科技", "晶科能源",
            "阳光电源", "锦浪科技"
        ],
        "sub_chains": {
            "硅料": ["硅料", "多晶硅", "工业硅"],
            "硅片": ["硅片", "光伏硅片"],
            "逆变器": ["逆变器", "光伏逆变器", "储能逆变器"]
        }
    },

    # 军工产业链
    "军工链": {
        "description": "航空航天装备 → 军用电子 → 关键材料",
        "terminal_demand": "国防预算、装备采购",
        "key_companies": [
            "中航沈飞", "中航西飞", "航发动力", "中航光电", "振华科技",
            "紫光国微", "菲利华"
        ]
    },

    # 医药产业链
    "医药链": {
        "description": "创新药 → CXO → 医疗器械",
        "terminal_demand": "创新药研发投入、老龄化需求",
        "key_companies": [
            "恒瑞医药", "百济神州", "药明康德", "药明生物", "迈瑞医疗",
            "联影医疗"
        ],
        "sub_chains": {
            "创新药": ["创新药", "生物医药", "ADC"],
            "CXO": ["CXO", "合同研发", "合同生产"],
            "医疗器械": ["医疗器械", "医疗设备", "耗材"]
        }
    }
}


# ============================================================
# 关键词匹配规则（用于从东财行业/概念字段识别）
# ============================================================

CHAIN_KEYWORDS: Dict[str, List[str]] = {
    "AI算力链": [
        "AI", "人工智能", "GPU", "算力", "服务器", "数据中心", "云计算",
        "光模块", "光通信", "光纤", "高速光模块",
        "PCB载板", "IC载板", "封装基板", "BGA",
        "HBM", "存储芯片", "先进封装", "CoWoS", "HBM概念"
    ],
    "PCB链": [
        "PCB", "印制电路板", "覆铜板", "CCL", "电子级玻纤布",
        "柔性PCB", "FPC", "刚柔结合板",
        "高多层板", "HDI", "任意层互连"
    ],
    "半导体设备链": [
        "半导体设备", "半导体制造", "PVD", "CVD", "MOCVD",
        "光刻机", "刻蚀机", "沉积设备", "清洗设备", "离子注入",
        "晶圆减薄", "划片机", "封装测试设备",
        "北方华创", "中微公司", "华海清科", "拓荆科技", "盛美"
    ],
    "半导体材料链": [
        "半导体材料", "硅片", "光刻胶", "电子特气", "高纯试剂",
        "靶材", "抛光垫", "抛光液", "引线框架",
        "沪硅产业", "立昂微", "神工股份", "彤程新材"
    ],
    "新能源链": [
        "锂电池", "动力电池", "储能电池", "固态电池",
        "正极材料", "负极材料", "隔膜", "电解液",
        "锂电设备", "电芯制造", "模组", "PACK",
        "宁德时代", "比亚迪", "亿纬锂能", "国轩高科", "欣旺达",
        "恩捷股份", "星源材质", "天赐材料", "新宙邦"
    ],
    "机器人链": [
        "工业机器人", "机器人", "自动化设备", "智能制造",
        "减速器", "谐波减速器", "RV减速器",
        "伺服电机", "伺服系统", "伺服驱动",
        "控制器", "PLC", "工业自动化",
        "汇川技术", "埃斯顿", "绿的谐波", "埃夫特"
    ],
    "消费电子链": [
        "消费电子", "智能手机", "手机", "可穿戴",
        "光学镜头", "摄像头", "CMOS传感器",
        "面板", "显示屏", "OLED", "MiniLED",
        "精密制造", "结构件", "连接器",
        "立讯精密", "歌尔股份", "蓝思科技", "鹏鼎控股"
    ],
    "低空经济链": [
        "低空经济", "eVTOL", "无人机", "通用航空", "低空交通",
        "低空基础设施", "低空通信", "低空导航", "低空服务",
        "飞行汽车", "空中出行", "载人无人机"
    ],
    "光伏链": [
        "光伏", "太阳能", "硅料", "多晶硅", "工业硅",
        "硅片", "电池片", "光伏组件",
        "逆变器", "光伏逆变器", "储能逆变器",
        "光伏设备", "电池片设备",
        "隆基绿能", "通威股份", "天合光能", "阳光电源"
    ],
    "军工链": [
        "军工", "国防军工", "航空航天", "军用航空",
        "导弹", "精确制导", "军用电子", "军工通信",
        "舰船", "海军装备", "陆军装备",
        "中航沈飞", "航发动力", "中航西飞", "中航光电"
    ],
    "医药链": [
        "创新药", "生物医药", "化学制药", "中药",
        "医疗器械", "医疗设备", "医疗耗材",
        "CXO", "合同研发", "合同生产", "CDMO",
        "恒瑞医药", "百济神州", "药明康德", "迈瑞医疗"
    ]
}


# ============================================================
# 东财行业分类 → 产业链 映射
# ============================================================

# 东财一级行业到产业链的映射
EASTMONEY_INDUSTRY_TO_CHAIN: Dict[str, str] = {
    # 半导体
    "半导体": "半导体设备链",
    "半导体材料": "半导体材料链",

    # 电子
    "光学光电子": "消费电子链",
    "消费电子": "消费电子链",
    "电子化学品": "半导体材料链",
    "电子元件": "PCB链",
    "印制电路板": "PCB链",

    # 计算机/AI
    "软件开发": "AI算力链",
    "IT服务": "AI算力链",
    "计算机设备": "AI算力链",
    "互联网服务": "AI算力链",

    # 新能源
    "电池": "新能源链",
    "光伏设备": "光伏链",
    "风电设备": "新能源链",
    "电网设备": "新能源链",

    # 汽车
    "汽车零部件": "新能源链",
    "汽车整车": "新能源链",

    # 机械/机器人
    "通用设备": "机器人链",
    "专用设备": "机器人链",
    "工程机械": "机器人链",
    "自动化设备": "机器人链",

    # 军工
    "航空机场": "军工链",
    "航天航空": "军工链",
    "军工": "军工链",
    "船舶制造": "军工链",

    # 医药
    "医疗器械": "医药链",
    "生物制品": "医药链",
    "化学制药": "医药链",
    "中药": "医药链",
    "医疗服务": "医药链",
}


# ============================================================
# 供应链关键度评分（用于判断公司在产业链中的重要性）
# ============================================================

# 关键零部件/材料（在对应产业链中权重更高）
SUPPLY_CHAIN_CRITICALITY = {
    "AI算力链": {
        "高端AI芯片": 10, "HBM内存": 9, "光模块": 8, "PCB载板": 7, "服务器": 7, "散热": 6
    },
    "新能源链": {
        "动力电池": 10, "正极材料": 8, "负极材料": 7, "隔膜": 7, "电解液": 6,
        "锂矿": 8, "钴矿": 6, "镍矿": 6
    },
    "半导体设备链": {
        "光刻机": 10, "刻蚀设备": 9, "沉积设备": 8, "清洗设备": 7, "检测设备": 7
    },
    "机器人链": {
        "减速器": 10, "伺服系统": 9, "控制器": 8, "工业机器人本体": 7
    },
    "消费电子链": {
        "芯片": 9, "显示屏": 8, "摄像头": 7, "结构件": 6, "电池": 6
    }
}


# ============================================================
# 重点公司白名单（基于主营业务，优先级最高）
# 解决"PCB龙头但名称中没PCB关键字"的问题
# ============================================================

KEY_COMPANIES_WHITELIST: Dict[str, str] = {
    # PCB龙头及上下游
    "胜宏科技": "PCB链",
    "鼎泰高科": "PCB链",
    "广合科技": "PCB链",
    "生益科技": "PCB链",
    "生益电子": "PCB链",
    "深南电路": "PCB链",
    "沪电股份": "PCB链",
    "鹏鼎控股": "PCB链",
    "景旺电子": "PCB链",
    "依顿电子": "PCB链",
    "崇达技术": "PCB链",
    "超声电子": "PCB链",
    "弘信电子": "PCB链",
    "中京电子": "PCB链",
    "天津普林": "PCB链",
    "金安国纪": "PCB链",
    "南亚新材": "PCB链",
    "华正新材": "PCB链",
    "宏和科技": "PCB链",
    "菲利华": "PCB链",
    "中国巨石": "PCB链",
    "长海股份": "PCB链",
    "再升科技": "PCB链",
    "中材科技": "PCB链",

    # 半导体设备龙头
    "北方华创": "半导体设备链",
    "中微公司": "半导体设备链",
    "拓荆科技": "半导体设备链",
    "华海清科": "半导体设备链",
    "盛美上海": "半导体设备链",
    "芯源微": "半导体设备链",
    "至纯科技": "半导体设备链",
    "长川科技": "半导体设备链",
    "华峰测控": "半导体设备链",
    "精测电子": "半导体设备链",
    "富创精密": "半导体设备链",
    "新莱应材": "半导体设备链",

    # 半导体材料龙头
    "沪硅产业": "半导体材料链",
    "立昂微": "半导体材料链",
    "神工股份": "半导体材料链",
    "彤程新材": "半导体材料链",
    "华特气体": "半导体材料链",
    "金宏气体": "半导体材料链",
    "江丰电子": "半导体材料链",
    "鼎龙股份": "半导体材料链",
    "安集科技": "半导体材料链",
    "晶瑞电材": "半导体材料链",
    "江化微": "半导体材料链",
    "南大光电": "半导体材料链",
    "雅克科技": "半导体材料链",
    "有研新材": "半导体材料链",
    "巨化股份": "半导体材料链",
    "三美股份": "半导体材料链",
    "永和股份": "半导体材料链",
    "昊华科技": "半导体材料链",

    # 半导体芯片设计/AI算力核心
    "澜起科技": "AI算力链",
    "中科曙光": "AI算力链",
    "浪潮信息": "AI算力链",
    "紫光股份": "AI算力链",
    "海光信息": "AI算力链",
    "寒武纪": "AI算力链",
    "景嘉微": "AI算力链",
    "兆易创新": "AI算力链",
    "紫光国微": "AI算力链",
    "卓胜微": "AI算力链",
    "圣邦股份": "AI算力链",
    "韦尔股份": "AI算力链",
    "汇顶科技": "AI算力链",
    "士兰微": "AI算力链",
    "华润微": "AI算力链",
    "扬杰科技": "AI算力链",
    "瑞芯微": "AI算力链",
    "全志科技": "AI算力链",
    "晶晨股份": "AI算力链",
    "北京君正": "AI算力链",
    "聚辰股份": "AI算力链",
    "普冉股份": "AI算力链",
    "德明利": "AI算力链",

    # 光模块龙头
    "中际旭创": "AI算力链",
    "新易盛": "AI算力链",
    "天孚通信": "AI算力链",
    "剑桥科技": "AI算力链",
    "华工科技": "AI算力链",
    "光迅科技": "AI算力链",
    "太辰光": "AI算力链",
    "博创科技": "AI算力链",
    "长芯博创": "AI算力链",
    "源杰科技": "AI算力链",
    "仕佳光子": "AI算力链",

    # 新能源龙头
    "宁德时代": "新能源链",
    "比亚迪": "新能源链",
    "亿纬锂能": "新能源链",
    "国轩高科": "新能源链",
    "欣旺达": "新能源链",
    "赣锋锂业": "新能源链",
    "天齐锂业": "新能源链",
    "华友钴业": "新能源链",
    "当升科技": "新能源链",
    "容百科技": "新能源链",
    "贝特瑞": "新能源链",
    "杉杉股份": "新能源链",
    "璞泰来": "新能源链",
    "恩捷股份": "新能源链",
    "星源材质": "新能源链",
    "天赐材料": "新能源链",
    "新宙邦": "新能源链",
    "多氟多": "新能源链",
    "先导智能": "新能源链",
    "杭可科技": "新能源链",
    "海目星": "新能源链",
    "大族激光": "新能源链",

    # 机器人龙头
    "汇川技术": "机器人链",
    "埃斯顿": "机器人链",
    "绿的谐波": "机器人链",
    "双环传动": "机器人链",
    "中大力德": "机器人链",
    "兆威机电": "机器人链",
    "鸣志电器": "机器人链",
    "江苏雷利": "机器人链",
    "拓斯达": "机器人链",
    "机器人": "机器人链",
    "埃夫特": "机器人链",
    "凯尔达": "机器人链",
    "禾川科技": "机器人链",
    "华锐精密": "机器人链",
    "欧科亿": "机器人链",
    "海天精工": "机器人链",
    "秦川机床": "机器人链",
    "创世纪": "机器人链",
    "昊志机电": "机器人链",
    "华辰装备": "机器人链",

    # 消费电子龙头
    "立讯精密": "消费电子链",
    "歌尔股份": "消费电子链",
    "蓝思科技": "消费电子链",
    "领益智造": "消费电子链",
    "东山精密": "消费电子链",
    "长盈精密": "消费电子链",
    "欧菲光": "消费电子链",
    "水晶光电": "消费电子链",
    "舜宇光学": "消费电子链",
    "信维通信": "消费电子链",
    "恒铭达": "消费电子链",
    "安洁科技": "消费电子链",
    "蓝特光学": "消费电子链",

    # 光伏龙头
    "隆基绿能": "光伏链",
    "通威股份": "光伏链",
    "天合光能": "光伏链",
    "晶澳科技": "光伏链",
    "晶科能源": "光伏链",
    "阳光电源": "光伏链",
    "锦浪科技": "光伏链",
    "固德威": "光伏链",
    "禾迈股份": "光伏链",
    "福莱特": "光伏链",
    "金博股份": "光伏链",
    "美畅股份": "光伏链",
    "高测股份": "光伏链",
    "捷佳伟创": "光伏链",
    "迈为股份": "光伏链",
    "帝尔激光": "光伏链",

    # 低空经济
    "亿航智能": "低空经济链",
    "中直股份": "低空经济链",
    "航天电子": "低空经济链",
    "深城交": "低空经济链",
    "中信海直": "低空经济链",
    "洪都航空": "低空经济链",
    "航发动力": "低空经济链",

    # 军工
    "中航沈飞": "军工链",
    "中航西飞": "军工链",
    "中航高科": "军工链",
    "中航电子": "军工链",
    "中航光电": "军工链",
    "中航重机": "军工链",
    "振华科技": "军工链",
    "中国船舶": "军工链",
    "中国动力": "军工链",
    "中国重工": "军工链",
    "中国卫星": "军工链",
    "中国核建": "军工链",
    "中国核电": "军工链",
    "中国广核": "军工链",

    # 医药
    "恒瑞医药": "医药链",
    "百济神州": "医药链",
    "药明康德": "医药链",
    "药明生物": "医药链",
    "康龙化成": "医药链",
    "泰格医药": "医药链",
    "凯莱英": "医药链",
    "迈瑞医疗": "医药链",
    "联影医疗": "医药链",
}


# ============================================================
# 辅助函数
# ============================================================

# 产业链优先级（多个匹配时优先级最高的获胜）
# 原则: 主营业务>热点概念, 具体产业链>宽泛产业链
# 实际调整: 主营业务应当被放在更高优先级
CHAIN_PRIORITY = {
    "机器人链": 14,           # 主营业务明确
    "PCB链": 13,              # 主营业务明确
    "半导体设备链": 12,       # 主营业务明确
    "半导体材料链": 11,       # 主营业务明确
    "新能源链": 10,           # 主营业务明确
    "光伏链": 9,              # 主营业务明确
    "低空经济链": 9,          # 主营业务明确
    "消费电子链": 8,          # 主营业务明确
    "军工链": 7,              # 主营业务明确
    "AI算力链": 6,            # 较狭义
    "医药链": 5,              # 主营业务明确
}

# 同花顺概念关键词 → 产业链的精确映射
THS_CONCEPT_TO_CHAIN: Dict[str, str] = {
    # PCB链（主营业务优先，避免被其他概念覆盖）
    "PCB": "PCB链",
    "PCB概念": "PCB链",
    "印制电路板": "PCB链",
    "覆铜板": "PCB链",
    "CCL": "PCB链",
    "电子级玻纤": "PCB链",
    "高多层板": "PCB链",
    "HDI": "PCB链",
    "FPC": "PCB链",
    "PCB载板": "PCB链",

    # 半导体设备链
    "半导体设备": "半导体设备链",
    "光刻机": "半导体设备链",
    "刻蚀机": "半导体设备链",
    "PVD": "半导体设备链",
    "CVD": "半导体设备链",
    "MOCVD": "半导体设备链",
    "清洗设备": "半导体设备链",
    "晶圆制造": "半导体设备链",
    "晶圆代工": "半导体设备链",
    "IDM": "半导体设备链",
    "半导体": "半导体设备链",  # 注意: 这个会覆盖其他

    # 半导体材料链
    "半导体材料": "半导体材料链",
    "硅片": "半导体材料链",
    "光刻胶": "半导体材料链",
    "电子特气": "半导体材料链",
    "靶材": "半导体材料链",
    "抛光垫": "半导体材料链",
    "抛光液": "半导体材料链",
    "引线框架": "半导体材料链",
    "PVDF概念": "新能源链",
    "氟化工概念": "新能源链",  # 锂电池电解液（六氟磷酸锂）上游材料

    # 机器人链
    "人形机器人": "机器人链",
    "机器人": "机器人链",
    "机器人概念": "机器人链",
    "工业机器人": "机器人链",
    "减速器": "机器人链",
    "伺服系统": "机器人链",
    "工业母机": "机器人链",
    "机床": "机器人链",
    "机器视觉": "机器人链",
    "智能制造": "机器人链",
    "工业自动化": "机器人链",

    # 新能源链
    "锂电池": "新能源链",
    "锂电池概念": "新能源链",
    "动力电池": "新能源链",
    "固态电池": "新能源链",
    "储能电池": "新能源链",
    "锂矿": "新能源链",
    "钴": "新能源链",
    "镍": "新能源链",
    "正极材料": "新能源链",
    "负极材料": "新能源链",
    "隔膜": "新能源链",
    "电解液": "新能源链",
    "钠离子电池": "新能源链",
    "新能源车": "新能源链",
    "新能源汽车": "新能源链",
    "充电桩": "新能源链",
    "锂电设备": "新能源链",
    "燃料电池": "新能源链",
    "氢能源": "新能源链",
    "储能": "新能源链",
    "比亚迪概念": "新能源链",
    "特斯拉概念": "新能源链",

    # 消费电子链
    "消费电子": "消费电子链",
    "消费电子概念": "消费电子链",
    "苹果产业链": "消费电子链",
    "苹果概念": "消费电子链",
    "华为产业链": "消费电子链",
    "华为概念": "消费电子链",
    "小米产业链": "消费电子链",
    "小米概念": "消费电子链",
    "VR": "消费电子链",
    "AR": "消费电子链",
    "MR": "消费电子链",
    "光学": "消费电子链",
    "摄像头": "消费电子链",
    "CMOS": "消费电子链",
    "OLED": "消费电子链",
    "MiniLED": "消费电子链",
    "MicroLED": "消费电子链",
    "面板": "消费电子链",
    "WiFi6": "消费电子链",
    "富士康概念": "消费电子链",
    "智能穿戴": "消费电子链",
    "智能家居": "消费电子链",
    "虚拟现实": "消费电子链",
    "折叠屏": "消费电子链",
    "柔性屏": "消费电子链",

    # AI算力链（狭义：核心算力）
    "AI算力": "AI算力链",
    "算力租赁": "AI算力链",
    "东数西算": "AI算力链",
    "数据中心": "AI算力链",
    "服务器": "AI算力链",
    "AIGC": "AI算力链",
    "多模态AI": "AI算力链",
    "AI智能体": "AI算力链",
    "AI PC": "AI算力链",
    "DeepSeek概念": "AI算力链",
    "国产软件": "AI算力链",
    "CPO": "AI算力链",
    "共封装光学(CPO)": "AI算力链",
    "光模块": "AI算力链",
    "F5G": "AI算力链",
    "高速光模块": "AI算力链",
    "光通信": "AI算力链",
    "HBM": "AI算力链",
    "存储芯片": "AI算力链",
    "先进封装": "AI算力链",
    "CoWoS": "AI算力链",
    "Chiplet": "AI算力链",
    "ASIC": "AI算力链",
    "国产芯片": "AI算力链",
    "MCU": "AI算力链",
    "中芯国际概念": "AI算力链",
    "阿里巴巴概念": "AI算力链",
    "腾讯概念": "AI算力链",
    "百度概念": "AI算力链",
    "人工智能": "AI算力链",
    "液冷服务器": "AI算力链",
    "创投": "AI算力链",
    "智能汽车": "AI算力链",
    "无人驾驶": "AI算力链",
    "车联网(车路协同)": "AI算力链",
    "汽车电子": "AI算力链",

    # 低空经济链
    "低空经济": "低空经济链",
    "eVTOL": "低空经济链",
    "飞行汽车": "低空经济链",
    "无人机": "低空经济链",
    "通用航空": "低空经济链",
    "载人无人机": "低空经济链",

    # 光伏链
    "光伏": "光伏链",
    "光伏概念": "光伏链",
    "光伏建筑一体化": "光伏链",
    "TOPCon": "光伏链",
    "HJT电池": "光伏链",
    "BC电池": "光伏链",
    "钙钛矿电池": "光伏链",
    "逆变器": "光伏链",
    "硅料": "光伏链",
    "电池片": "光伏链",
    "组件": "光伏链",
    "风电": "光伏链",  # 风电与光伏同属新能源电力

    # 军工链
    "军工": "军工链",
    "军工电子": "军工链",
    "国防军工": "军工链",
    "航空航天": "军工链",
    "导弹": "军工链",
    "精确制导": "军工链",
    "卫星互联网": "军工链",
    "卫星导航": "军工链",
    "商业航天": "军工链",
    "船舶": "军工链",
    "航空发动机": "军工链",
    "歼20": "军工链",
    "运20": "军工链",
    "核电": "军工链",
    "中字头股票": "军工链",
    "央企国企改革": "军工链",
    "国企改革": "军工链",

    # 医药链
    "创新药": "医药链",
    "CXO": "医药链",
    "医疗器械": "医药链",
    "生物医药": "医药链",
    "ADC": "医药链",
    "GLP-1": "医药链",
    "减肥药": "医药链",
    "中药": "医药链",
    "原料药": "医药链",
    "医药商业": "医药链",
}


def get_chain_keywords(chain: str) -> List[str]:
    """获取指定产业链的关键词列表"""
    return CHAIN_KEYWORDS.get(chain, [])


# ============================================================
# 下游客户映射（用于 ThemeMatchScore 下游客户匹配度）
# 判断该概念是否表示"该股票的下游客户属于X产业链"
# ============================================================

CUSTOMER_CHAIN_MAP: Dict[str, str] = {
    # 消费电子链下游客户
    "苹果产业链": "消费电子链",
    "苹果概念": "消费电子链",
    "华为产业链": "消费电子链",
    "华为概念": "消费电子链",
    "小米产业链": "消费电子链",
    "小米概念": "消费电子链",
    "富士康概念": "消费电子链",

    # 新能源链下游客户
    "比亚迪概念": "新能源链",
    "特斯拉概念": "新能源链",
    "宁德时代概念": "新能源链",

    # 军工链下游客户
    "军工": "军工链",
    "军工概念": "军工链",
    "航天": "军工链",
    "航空": "军工链",
    "大飞机": "军工链",
    "航母": "军工链",
}


def calc_theme_match_score(stock_name: str, industry: str,
                            ths_concepts: List[str], chain: str) -> float:
    """
    计算股票与产业链的 ThemeMatchScore 匹配度评分

    ThemeMatchScore = 0.5×主营收入占比 + 0.3×产品关键词匹配度 + 0.2×下游客户匹配度

    主营收入占比 < 30% → 禁止进入主题（返回 0 分）

    Args:
        stock_name: 股票名称
        industry: 东财行业
        ths_concepts: 同花顺概念列表
        chain: 待评估的产业链标签

    Returns:
        0~1 匹配度评分
    """
    if not stock_name:
        return 0.0

    chain_keywords = CHAIN_KEYWORDS.get(chain, [])

    # ── 1. 主营收入占比 (0.5) ──
    # 白名单：主营业务100%来自该链
    if KEY_COMPANIES_WHITELIST.get(stock_name) == chain:
        revenue_ratio = 1.0
    # 东财行业直接映射到该链：收入70%+来自该领域
    elif EASTMONEY_INDUSTRY_TO_CHAIN.get(industry) == chain:
        revenue_ratio = 0.7
    # 同花顺概念有直接映射到该链
    elif ths_concepts:
        chain_concepts = {k for k, v in THS_CONCEPT_TO_CHAIN.items() if v == chain}
        if chain_concepts:
            matched = any(
                con in chain_concepts or any(kw in con or con in kw for kw in chain_concepts)
                for con in ths_concepts
            )
            revenue_ratio = 0.5 if matched else 0.0
        else:
            revenue_ratio = 0.0
    else:
        revenue_ratio = 0.0

    # 硬门槛：主营收入占比 < 30% → 直接禁止
    if revenue_ratio < 0.3:
        return 0.0

    # ── 2. 产品关键词匹配度 (0.3) ──
    # 在 股票名称 + 行业 + 概念 中搜索产业链关键词
    search_text = f"{stock_name} {industry} {' '.join(ths_concepts or [])}"
    match_count = sum(1 for kw in chain_keywords if kw.lower() in search_text.lower())
    # 匹配到 3 个及以上关键词得满分，线性插值
    keyword_score = min(match_count / 3.0, 1.0)

    # ── 3. 下游客户匹配度 (0.2) ──
    # 判断该股票的下游客户是否属于该产业链
    customer_score = 0.0
    if ths_concepts:
        # 检查该链是否有对应的下游客户概念
        chain_customer_concepts = {
            k for k, v in CUSTOMER_CHAIN_MAP.items() if v == chain
        }
        if chain_customer_concepts:
            for con in ths_concepts:
                if con in chain_customer_concepts:
                    customer_score = 1.0
                    break
                # 子串匹配
                for cc in chain_customer_concepts:
                    if cc in con or con in cc:
                        customer_score = 0.8
                        break
                if customer_score > 0:
                    break

    total = 0.5 * revenue_ratio + 0.3 * keyword_score + 0.2 * customer_score
    return round(total, 4)


def identify_stock_chain_v2(stock_name: str, industry: str,
                            ths_concepts: List[str] = None) -> str:
    """
    ThemeMatchScore 版产业链识别（替代原链式匹配）

    流程：
      1. 白名单（KEY_COMPANIES_WHITELIST，主营业务明确）→ 直接返回
      2. 对所有产业链计算 ThemeMatchScore
      3. 取最高分且 ≥0.6 的链返回
      4. 若无 ≥0.6 的链 → 东财行业兜底
      5. 仍无 → 返回空字符串（不归属任何产业链）

    Args:
        stock_name: 股票名称
        industry: 东财行业分类
        ths_concepts: 股票所属同花顺概念列表

    Returns:
        产业链标签，若匹配失败返回空字符串
    """
    # ── 优先级0: 白名单（主营业务明确，最高置信度）──
    if stock_name in KEY_COMPANIES_WHITELIST:
        return KEY_COMPANIES_WHITELIST[stock_name]

    # ── ThemeMatchScore 评分筛选 ──
    best_chain = ""
    best_score = 0.0
    scores = {}

    for chain in CHAIN_KEYWORDS.keys():
        score = calc_theme_match_score(stock_name, industry, ths_concepts or [], chain)
        scores[chain] = score
        if score >= 0.6 and score > best_score:
            best_score = score
            best_chain = chain

    if best_chain:
        return best_chain

    # ── 兜底: 东财行业映射（低置信度，仅作最后手段）──
    if industry and industry in EASTMONEY_INDUSTRY_TO_CHAIN:
        return EASTMONEY_INDUSTRY_TO_CHAIN[industry]

    return ""


def identify_stock_chain(stock_name: str, industry: str = "", concept: str = "") -> str:
    """
    根据股票名称、行业、概念识别所属产业链

    Args:
        stock_name: 股票名称
        industry: 东财行业分类
        concept: 东财概念板块

    Returns:
        产业链标签，如果未识别到则返回空字符串
    """
    search_text = f"{stock_name} {industry} {concept}".lower()

    best_chain = ""
    best_match_count = 0

    for chain, keywords in CHAIN_KEYWORDS.items():
        match_count = sum(1 for kw in keywords if kw.lower() in search_text)
        if match_count > best_match_count:
            best_match_count = match_count
            best_chain = chain

    # 至少需要匹配2个关键词
    if best_match_count >= 2:
        return best_chain

    # 使用东财行业映射作为兜底
    if industry and industry in EASTMONEY_INDUSTRY_TO_CHAIN:
        return EASTMONEY_INDUSTRY_TO_CHAIN[industry]

    return ""


def get_chain_stocks(chain: str, stock_pool: List[str]) -> List[str]:
    """
    从股票池中筛选指定产业链的股票

    Args:
        chain: 产业链名称
        stock_pool: [(name, industry, concept), ...] 元组列表

    Returns:
        匹配到的股票名称列表
    """
    keywords = get_chain_keywords(chain)
    matched = []

    for name, industry, concept in stock_pool:
        search_text = f"{name} {industry} {concept}".lower()
        if any(kw.lower() in search_text for kw in keywords):
            matched.append(name)

    return matched


# 全局缓存：股票→概念的映射
_global_stock_concepts: Dict[str, List[str]] = {}


def load_concept_cache(config: dict) -> Dict[str, List[str]]:
    """
    加载概念缓存到全局

    Returns:
        {ts_code: [concept_names]}
    """
    global _global_stock_concepts
    if _global_stock_concepts:
        return _global_stock_concepts

    try:
        from data_fetcher import load_cache, get_cache_dir
        from concept_cache_builder import build_concept_cache

        cache_dir = get_cache_dir(config)
        cache_key_concepts = "ths_concepts_list"
        cache_key_members = "ths_concepts_members"

        concepts_df = load_cache(cache_dir, cache_key_concepts, 168)
        members_df = load_cache(cache_dir, cache_key_members, 168)

        if concepts_df is None or members_df is None:
            logger.info("概念缓存不存在，自动构建...")
            result = build_concept_cache(config)
            if not result:
                return {}
            _global_stock_concepts = result.get('stock_concepts', {})
        else:
            logger.info(f"加载概念缓存: {len(concepts_df)} 个概念, {len(members_df)} 条记录")
            _global_stock_concepts = {}
            for _, row in members_df.iterrows():
                # 字段说明: con_code=股票代码, concept_name=概念名称
                ts_code = row.get('con_code', '')
                con_name = row.get('concept_name', '')
                if ts_code and con_name:
                    _global_stock_concepts.setdefault(ts_code, []).append(con_name)

        return _global_stock_concepts
    except Exception as e:
        logger.warning(f"加载概念缓存失败: {e}")
        return {}


def get_stock_ths_concepts(ts_code: str, config: dict = None) -> List[str]:
    """
    获取股票所属的同花顺概念列表

    Args:
        ts_code: 股票代码
        config: 配置字典

    Returns:
        概念名称列表
    """
    if not config:
        return _global_stock_concepts.get(ts_code, [])

    stock_concepts = load_concept_cache(config)
    return stock_concepts.get(ts_code, [])


def identify_chain_with_cache_v2_fallback(ts_code: str, stock_name: str, industry: str,
                              config: dict = None) -> str:
    """
    [已弃用,被下方查表版本覆盖,保留仅作回退参考]
    带缓存的产业链识别 - 基于同花顺概念+关键词匹配

    Args:
        ts_code: 股票代码
        stock_name: 股票名称
        industry: 东财行业
        config: 配置字典

    Returns:
        产业链标签
    """
    # 处理 NaN 值（来自 pandas DataFrame）
    import pandas as pd
    if pd.isna(industry) or industry == 'nan':
        industry = ''
    if pd.isna(stock_name) or stock_name == 'nan':
        stock_name = ''

    ths_concepts = get_stock_ths_concepts(ts_code, config)
    return identify_stock_chain_v2(stock_name, industry, ths_concepts)


# ============================================================
# 需求链数据获取配置
# ============================================================

# Tushare财务指标字段配置
FINANCIAL_FIELDS = {
    "income": [
        "ts_code", "end_date", "total_revenue", "revenue", "n_income",  # 营收、净利润
        "gross_profit", "total_cogs", "operate_profit",  # 毛利、营业利润
        "rd_exp"  # 研发费用
    ],
    "balance_sheet": [
        "ts_code", "end_date",
        "inventories", "fix_assets", "total_current_assets",  # 存货、固定资产
        "advance_payment", "contract_liability",  # 预付款、合同负债
        "total_hldr_eqy_exc_min_int"  # 股东权益
    ],
    "cashflow": [
        "ts_code", "end_date",
        "n_cashflow_act", "n_cashflow_inv_act",  # 经营现金流净额、投资现金流净额
        "c_pay_acq_const_fiolta"  # 购建固定资产等支付的现金(资本开支)
    ]
}

# 计算公式配置
DERIVED_METRICS = {
    "gross_margin": "gross_profit / revenue if revenue > 0",
    "capex_to_revenue": "c_pay_acq_const_fiolta / revenue if revenue > 0",
    "inventory_turnover_change": "需要对比两年数据计算",
    "fixed_asset_turnover": "revenue / fix_assets if fix_assets > 0",
    "contract_liability_yoy": "需要对比两年数据计算",
}


# ============================================================
# 基于 theme.json 的产业链识别（增强版）
# ============================================================

# theme.json 路径（从上级目录加载）
THEME_JSON_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "theme.json")

# 全局缓存
_theme_json_cache = None


def load_theme_json() -> Dict:
    """加载 theme.json 配置"""
    global _theme_json_cache
    if _theme_json_cache is not None:
        return _theme_json_cache
    
    if not os.path.exists(THEME_JSON_PATH):
        logger.warning(f"未找到 theme.json: {THEME_JSON_PATH}")
        return {}
    
    try:
        with open(THEME_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _theme_json_cache = data.get("HOT_THEMES", {})
        logger.info(f"加载 theme.json: {len(_theme_json_cache)} 个主题")
        return _theme_json_cache
    except Exception as e:
        logger.warning(f"加载 theme.json 失败: {e}")
        return {}


def _in_industry_list(industry_name: str, industry_list: List[str]) -> bool:
    """检查行业名是否在行业列表中（支持部分匹配）"""
    if not industry_name or not industry_list:
        return False
    for ind in industry_list:
        if ind in industry_name or industry_name in ind:
            return True
    return False


def _match_keyword(search_text: str, keywords: List[str]) -> bool:
    """检查搜索文本中是否包含关键词"""
    if not search_text or not keywords:
        return False
    search_text = search_text.lower()
    for kw in keywords:
        if kw.lower() in search_text:
            return True
    return False


def _is_core_company(stock_name: str, core_companies: List[str], leader_companies: List[str]) -> bool:
    """检查是否为核心公司或龙头公司"""
    if not stock_name:
        return False
    if core_companies and any(c in stock_name for c in core_companies):
        return True
    if leader_companies and any(c in stock_name for c in leader_companies):
        return True
    return False


def _should_exclude(search_text: str, exclude_keywords: List[str], core_companies: List[str], leader_companies: List[str]) -> bool:
    """检查是否应排除（核心公司不排除）"""
    if not search_text or not exclude_keywords:
        return False
    # 核心公司不排除
    stock_name = search_text.split()[0] if search_text else ""
    if _is_core_company(stock_name, core_companies, leader_companies):
        return False
    search_text = search_text.lower()
    for ek in exclude_keywords:
        if ek.lower() in search_text:
            return True
    return False


def identify_stock_chain_v3(stock_name: str, industry: str,
                            ths_concepts: List[str] = None) -> str:
    """
    基于 theme.json 的产业链识别（增强版）
    
    匹配优先级：
    1. 核心公司/龙头公司白名单（core_companies/leader_companies）
    2. 行业匹配 + 概念/关键词验证
    3. 纯概念匹配（当主题无行业配置时）
    4. 纯关键词匹配
    5. 兜底：旧版 identify_stock_chain_v2
    """
    hot_themes = load_theme_json()
    if not hot_themes:
        return identify_stock_chain_v2(stock_name, industry, ths_concepts)
    
    ths_concepts = ths_concepts or []
    search_text = f"{stock_name} {industry} {' '.join(ths_concepts)}"
    
    best_score = 0.0
    best_chain = ""
    
    for theme_name, cfg in hot_themes.items():
        industry_list = cfg.get("industry", [])
        concept_list = cfg.get("concept", [])
        keyword_list = cfg.get("keywords", [])
        exclude_keywords = cfg.get("exclude_keywords", [])
        core_companies = cfg.get("core_companies", [])
        leader_companies = cfg.get("leader_companies", [])
        
        # 跳过空配置
        if not industry_list and not concept_list and not keyword_list:
            continue
        
        # 核心/龙头公司：直接命中
        if _is_core_company(stock_name, core_companies, leader_companies):
            chain_name = THEME_TO_CHAIN.get(theme_name)
            if chain_name:
                return chain_name
            continue
        
        # 排除检查
        if _should_exclude(search_text, exclude_keywords, core_companies, leader_companies):
            continue
        
        score = 0
        has_industry_match = False
        has_concept_match = False
        has_keyword_match = False
        
        # 行业匹配（权重最高）
        if industry_list:
            if industry and _in_industry_list(industry, industry_list):
                score += 50
                has_industry_match = True
        
        # 概念匹配
        if concept_list and ths_concepts:
            match_count = sum(1 for conc in ths_concepts if conc in concept_list)
            if match_count > 0:
                score += 30 * match_count
                has_concept_match = True
        
        # 关键词匹配
        if keyword_list:
            match_count = sum(1 for kw in keyword_list if kw.lower() in search_text.lower())
            if match_count > 0:
                score += 10 * min(match_count, 3)
                has_keyword_match = True
        
        # 评分规则优化：
        # - 行业+概念 → 80+ 分，高置信度
        # - 行业+关键词 → 60+ 分，中等置信度
        # - 纯概念匹配（主题无行业配置）→ 70 分
        # - 纯关键词匹配（主题无行业配置）→ 50 分
        # - 概念+关键词 → 60 分
        # - 单概念匹配（主题有行业配置但股票无行业匹配）→ 40 分（放宽阈值）
        if not industry_list:
            if has_concept_match:
                score = max(score, 70)
            elif has_keyword_match:
                score = max(score, 50)
        
        if has_concept_match and has_keyword_match and not has_industry_match:
            score = max(score, 60)
        
        # 放宽纯概念匹配阈值：当主题有概念配置且股票匹配到概念时，最低给40分
        if has_concept_match and not has_industry_match and score > 0:
            score = max(score, 40)
        
        if score >= 40 and score > best_score:
            best_score = score
            best_chain = THEME_TO_CHAIN.get(theme_name)
    
    # 返回最高分的链
    if best_chain:
        return best_chain
    
    # 兜底：使用旧版识别
    return identify_stock_chain_v2(stock_name, industry, ths_concepts)


# theme.json 主题名 → chain_mapping 链名 映射
THEME_TO_CHAIN = {
    # AI算力相关
    "光通信": "AI算力链",
    "AI服务器与算力基建": "AI算力链",
    "数据中心瓶颈硬件链": "AI算力链",
    "AI应用": "AI算力链",
    "AI文化娱乐": "AI算力链",
    "AI模型与AI Agent": "AI算力链",
    "AI终端": "AI算力链",
    "AI芯片": "AI算力链",
    "AI能源链": "AI算力链",
    "物理AI": "AI算力链",
    "智能驾驶": "AI算力链",
    "金融科技": "AI算力链",
    "数据要素": "AI算力链",
    
    # 半导体相关
    "半导体设备": "半导体设备链",
    "半导体材料": "半导体材料链",
    "半导体设计": "AI算力链",
    "半导体制造": "半导体设备链",
    "光刻机链": "半导体设备链",
    "先进封装": "半导体设备链",
    "先进封装材料": "半导体材料链",
    "存储芯片": "AI算力链",
    "IC设计": "AI算力链",
    "功率半导体": "AI算力链",
    "半导体封测": "半导体设备链",
    "半导体EDA/IP": "半导体设备链",
    "被动元件": "消费电子链",
    
    # 消费电子相关
    "PCB": "PCB链",
    "PCB电子电路": "PCB链",
    "消费电子": "消费电子链",
    "光学光电子": "消费电子链",
    "苹果产业链": "消费电子链",
    "华为产业链": "消费电子链",
    "小米产业链": "消费电子链",
    "智能穿戴": "消费电子链",
    "虚拟现实": "消费电子链",
    "情绪消费成长链": "消费电子链",
    
    # 新能源相关
    "新能源车": "新能源链",
    "新能源汽车链": "新能源链",
    "电池": "新能源链",
    "光伏": "光伏链",
    "风电": "新能源链",
    "储能": "新能源链",
    "新型储能": "新能源链",
    "固态电池": "新能源链",
    "电力链": "新能源链",
    "电力设备出海": "新能源链",
    "电网数字化": "新能源链",
    "氢能": "新能源链",
    "核聚变": "新能源链",
    
    # 机器人相关
    "机器人": "机器人链",
    "人形机器人": "机器人链",
    "工业自动化": "机器人链",
    "工业母机": "机器人链",
    
    # 军工相关
    "军工": "军工链",
    "航空航天": "军工链",
    "低空经济": "低空经济链",
    "商业航天": "军工链",
    
    # 医药相关
    "医药": "医药链",
    "创新医药主线": "医药链",
    "创新药": "医药链",
    "医疗器械": "医药链",
    "CXO": "医药链",
    "合成生物": "医药链",
    
    # 周期相关（修正: 不再错误映射到新能源链/半导体材料链）
    "煤炭链": "周期链",
    "工业金属": "周期链",
    "贵金属": "周期链",
    "能源金属": "周期链",
    "小金属": "周期链",
    "硫磺磷化工链": "化工材料链",
    "氟化工制冷剂": "化工材料链",
    "培育钻石": "化工材料链",
    
    # 必选消费
    "必选消费红利链": "消费链",
    
    # 金融（修正: 不再错误映射到AI算力链）
    "券商": "金融链",
    "保险": "金融链",
    "银行": "金融链",
    
    # 信创
    "信创软件": "AI算力链",
    
    # 脑机接口
    "脑机接口": "AI算力链",
}


# ============================================================
# 基于 theme_stock_map_latest.json 的产业链识别（直接查表）
# ============================================================

# theme_stock_map_latest.json 路径
THEME_STOCK_MAP_PATH = r"D:\mystock\cache_daily\theme_stock_map_latest.json"

# 全局缓存：股票代码 -> 主题名
# None = 未加载, {} = 已加载但文件不存在/加载失败(不再重试)
_stock_theme_cache = None
_loaded_attempted = False  # 是否已尝试加载(避免重复打日志)


def load_theme_stock_map(force_reload: bool = False) -> Dict[str, str]:
    """
    加载 theme_stock_map_latest.json，直接建立 股票代码 -> 主题名 的映射

    Args:
        force_reload: 强制重新加载(忽略缓存)

    Returns:
        Dict[str, str]: {ts_code: theme_name}
    """
    global _stock_theme_cache, _loaded_attempted

    if force_reload:
        _stock_theme_cache = None
        _loaded_attempted = False

    if _stock_theme_cache is not None:
        return _stock_theme_cache

    if _loaded_attempted:
        return {}

    _loaded_attempted = True

    if not os.path.exists(THEME_STOCK_MAP_PATH):
        logger.warning(f"未找到 theme_stock_map_latest.json: {THEME_STOCK_MAP_PATH}")
        _stock_theme_cache = {}
        return {}

    try:
        with open(THEME_STOCK_MAP_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        _stock_theme_cache = {}
        themes = data.get("themes", {})
        for theme_name, stocks in themes.items():
            for stock_info in stocks:
                ts_code = stock_info.get("code", "")
                if ts_code:
                    _stock_theme_cache[ts_code] = theme_name

        logger.info(f"加载 theme_stock_map: {len(_stock_theme_cache)} 只股票 -> 主题映射")
        return _stock_theme_cache
    except Exception as e:
        logger.warning(f"加载 theme_stock_map 失败: {e}")
        _stock_theme_cache = {}
        return {}


def identify_chain_with_cache(ts_code: str, stock_name: str, industry: str,
                              config: dict = None) -> str:
    """
    带缓存的产业链识别 -- 优先查表 theme_stock_map_latest.json,
    未命中时回退到同花顺概念+关键词匹配算法

    Args:
        ts_code: 股票代码
        stock_name: 股票名称
        industry: 东财行业
        config: 配置字典

    Returns:
        产业链标签（主题名），未匹配则返回空字符串
    """
    import pandas as pd
    if pd.isna(ts_code) or ts_code == 'nan':
        return ""

    # 1. 优先直接查表（theme_stock_map_latest.json, 2029只股票精确映射）
    stock_theme_map = load_theme_stock_map()
    result = stock_theme_map.get(ts_code, "")
    if result:
        return result

    # 2. 查表未命中,回退到同花顺概念+关键词匹配算法
    if pd.isna(industry) or industry == 'nan':
        industry = ''
    if pd.isna(stock_name) or stock_name == 'nan':
        stock_name = ''
    try:
        ths_concepts = get_stock_ths_concepts(ts_code, config)
        return identify_stock_chain_v2(stock_name, industry, ths_concepts)
    except Exception:
        return ""


# ============================================================
# 旧版基于 theme.json 的产业链识别（保留兜底）
# ============================================================

# theme.json 路径（从上级目录加载）
THEME_JSON_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "theme.json")

# 全局缓存
_theme_json_cache = None


def load_theme_json() -> Dict:
    """加载 theme.json 配置"""
    global _theme_json_cache
    if _theme_json_cache is not None:
        return _theme_json_cache
    
    if not os.path.exists(THEME_JSON_PATH):
        logger.warning(f"未找到 theme.json: {THEME_JSON_PATH}")
        return {}
    
    try:
        with open(THEME_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _theme_json_cache = data.get("HOT_THEMES", {})
        logger.info(f"加载 theme.json: {len(_theme_json_cache)} 个主题")
        return _theme_json_cache
    except Exception as e:
        logger.warning(f"加载 theme.json 失败: {e}")
        return {}


def _in_industry_list(industry_name: str, industry_list: List[str]) -> bool:
    """检查行业名是否在行业列表中（支持部分匹配）"""
    if not industry_name or not industry_list:
        return False
    for ind in industry_list:
        if ind in industry_name or industry_name in ind:
            return True
    return False


def _match_keyword(search_text: str, keywords: List[str]) -> bool:
    """检查搜索文本中是否包含关键词"""
    if not search_text or not keywords:
        return False
    search_text = search_text.lower()
    for kw in keywords:
        if kw.lower() in search_text:
            return True
    return False


def _is_core_company(stock_name: str, core_companies: List[str], leader_companies: List[str]) -> bool:
    """检查是否为核心公司或龙头公司"""
    if not stock_name:
        return False
    if core_companies and any(c in stock_name for c in core_companies):
        return True
    if leader_companies and any(c in stock_name for c in leader_companies):
        return True
    return False


def _should_exclude(search_text: str, exclude_keywords: List[str], core_companies: List[str], leader_companies: List[str]) -> bool:
    """检查是否应排除（核心公司不排除）"""
    if not search_text or not exclude_keywords:
        return False
    stock_name = search_text.split()[0] if search_text else ""
    if _is_core_company(stock_name, core_companies, leader_companies):
        return False
    search_text = search_text.lower()
    for ek in exclude_keywords:
        if ek.lower() in search_text:
            return True
    return False


def identify_stock_chain_v3(stock_name: str, industry: str,
                            ths_concepts: List[str] = None) -> str:
    """
    基于 theme.json 的产业链识别（增强版）- 仅作兜底使用
    """
    hot_themes = load_theme_json()
    if not hot_themes:
        return identify_stock_chain_v2(stock_name, industry, ths_concepts)
    
    ths_concepts = ths_concepts or []
    search_text = f"{stock_name} {industry} {' '.join(ths_concepts)}"
    
    best_score = 0.0
    best_chain = ""
    
    for theme_name, cfg in hot_themes.items():
        industry_list = cfg.get("industry", [])
        concept_list = cfg.get("concept", [])
        keyword_list = cfg.get("keywords", [])
        exclude_keywords = cfg.get("exclude_keywords", [])
        core_companies = cfg.get("core_companies", [])
        leader_companies = cfg.get("leader_companies", [])
        
        if not industry_list and not concept_list and not keyword_list:
            continue
        
        if _is_core_company(stock_name, core_companies, leader_companies):
            chain_name = THEME_TO_CHAIN.get(theme_name)
            if chain_name:
                return chain_name
            continue
        
        if _should_exclude(search_text, exclude_keywords, core_companies, leader_companies):
            continue
        
        score = 0
        has_industry_match = False
        has_concept_match = False
        has_keyword_match = False
        
        if industry_list:
            if industry and _in_industry_list(industry, industry_list):
                score += 50
                has_industry_match = True
        
        if concept_list and ths_concepts:
            match_count = sum(1 for conc in ths_concepts if conc in concept_list)
            if match_count > 0:
                score += 30 * match_count
                has_concept_match = True
        
        if keyword_list:
            match_count = sum(1 for kw in keyword_list if kw.lower() in search_text.lower())
            if match_count > 0:
                score += 10 * min(match_count, 3)
                has_keyword_match = True
        
        if not industry_list:
            if has_concept_match:
                score = max(score, 70)
            elif has_keyword_match:
                score = max(score, 50)
        
        if has_concept_match and has_keyword_match and not has_industry_match:
            score = max(score, 60)
        
        if has_concept_match and not has_industry_match and score > 0:
            score = max(score, 40)
        
        if score >= 40 and score > best_score:
            best_score = score
            best_chain = THEME_TO_CHAIN.get(theme_name)
    
    if best_chain:
        return best_chain
    
    return identify_stock_chain_v2(stock_name, industry, ths_concepts)


if __name__ == "__main__":
    # 测试
    print("产业链映射配置")
    print("=" * 50)
    print(f"支持的产业链数量: {len(INDUSTRY_CHAINS)}")
    for chain in INDUSTRY_CHAINS:
        print(f"  - {chain}")

    print("\n关键词匹配测试:")
    test_stocks = [
        ("宁德时代", "电池", "动力电池、新能源车"),
        ("北方华创", "半导体设备", "半导体设备、国产替代"),
        ("浪潮信息", "计算机设备", "AI服务器、算力"),
        ("汇川技术", "自动化设备", "工业机器人、伺服系统"),
    ]

    for name, industry, concept in test_stocks:
        chain = identify_stock_chain(name, industry, concept)
        print(f"  {name}({industry}): {chain or '未识别'}")
