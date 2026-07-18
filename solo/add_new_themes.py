import json
import os

THEME_FILE = r"d:\mystock\solo\theme.json"

with open(THEME_FILE, 'r', encoding='utf-8') as f:
    data = json.load(f)

hot_themes = data['HOT_THEMES']

# 1. 新增轨交设备主题
if '轨交设备' not in hot_themes:
    hot_themes['轨交设备'] = {
        "industry": ["轨交设备Ⅱ", "轨交设备Ⅲ", "其他专用设备", "工程机械整机", "工程机械器件", "铁路运输"],
        "concept": ["轨道交通", "高铁", "地铁", "城轨"],
        "keywords": ["轨道交通", "高铁", "动车", "地铁", "城轨", "轨交", "铁路装备", "机车", "动车组", "轨交设备", "轨交零部件", "轨交信号", "轨交通信", "轨交信息化"],
        "exclude_keywords": ["军工", "航天", "航空"],
        "core_companies": ["中国中车", "中国通号", "中铁工业", "时代电气", "晋西车轴", "铁科轨道", "利源精制", "思维列控", "众合科技", "通业科技"],
        "leader_companies": ["中国中车", "中国通号", "时代电气"],
        "etf": "516880",
        "style": "高端制造",
        "capacity": "大",
        "leader_type": "趋势中军",
        "fund_type": "机构+游资",
        "priority": "SS"
    }

# 2. 新增品牌消费电子主题
if '品牌消费电子' not in hot_themes:
    hot_themes['品牌消费电子'] = {
        "industry": ["品牌消费电子", "消费电子", "其他电子Ⅱ", "其他电子Ⅲ", "消费电子零部件及组装"],
        "concept": ["消费电子", "智能穿戴", "智能硬件", "智能家居", "VR设备", "AR设备"],
        "keywords": ["消费电子", "智能穿戴", "智能手表", "智能手环", "VR", "AR", "智能眼镜", "耳机", "音箱", "智能家居", "智能硬件", "消费电子品牌", "跨境电商", "出海品牌", "可穿戴"],
        "exclude_keywords": ["半导体", "芯片", "GPU", "AI芯片", "汽车电子"],
        "core_companies": ["安克创新", "影石创新", "漫步者", "奋达科技", "华米科技", "石头科技", "科沃斯", "倍轻松", "汉王科技", "万魔声学"],
        "leader_companies": ["安克创新", "影石创新", "漫步者"],
        "etf": "159997",
        "style": "消费",
        "capacity": "中",
        "leader_type": "趋势中军",
        "fund_type": "机构+游资",
        "priority": "SS"
    }

# 3. 新增钾肥磷化工主题
if '钾肥磷化工' not in hot_themes:
    hot_themes['钾肥磷化工'] = {
        "industry": ["钾肥", "磷肥及磷化工", "化学原料", "其他化学原料", "农化制品", "化肥"],
        "concept": ["钾肥", "磷化工", "化肥", "磷酸铁锂", "盐湖提锂"],
        "keywords": ["钾肥", "氯化钾", "磷酸一铵", "磷酸二铵", "磷化工", "磷矿石", "黄磷", "磷酸", "化肥", "复合肥", "盐湖提锂", "盐湖提钾", "磷铵"],
        "exclude_keywords": ["半导体", "芯片", "AI", "军工", "医药"],
        "core_companies": ["盐湖股份", "藏格矿业", "云天化", "兴发集团", "川恒股份", "中泰化学", "六国化工", "湖北宜化", "华鲁恒升", "新洋丰"],
        "leader_companies": ["盐湖股份", "藏格矿业", "云天化"],
        "etf": "",
        "style": "资源",
        "capacity": "大",
        "leader_type": "趋势中军",
        "fund_type": "机构+游资",
        "priority": "SS"
    }

# 4. 新增中药主题
if '中药' not in hot_themes:
    hot_themes['中药'] = {
        "industry": ["中药Ⅱ", "中药Ⅲ", "医药生物", "医疗服务"],
        "concept": ["中药", "中药创新", "中药配方颗粒", "中医药"],
        "keywords": ["中药", "中成药", "中药饮片", "中药配方颗粒", "中医药", "中药材", "中药创新药", "中药国际化", "中药老字号", "中医药传承", "中药种植"],
        "exclude_keywords": ["西药", "化药", "医疗器械", "AI", "半导体"],
        "core_companies": ["片仔癀", "云南白药", "同仁堂", "白云山", "华润三九", "东阿阿胶", "以岭药业", "步长制药", "康恩贝", "天士力", "济川药业", "千金药业"],
        "leader_companies": ["片仔癀", "云南白药", "同仁堂"],
        "etf": "512080",
        "style": "医药",
        "capacity": "大",
        "leader_type": "趋势中军",
        "fund_type": "机构+游资",
        "priority": "SS"
    }

# 5. 新增激光设备主题
if '激光设备' not in hot_themes:
    hot_themes['激光设备'] = {
        "industry": ["激光设备", "其他专用设备", "专用设备", "机械设备", "仪器仪表"],
        "concept": ["激光", "激光器", "激光加工"],
        "keywords": ["激光", "激光器", "激光切割", "激光焊接", "激光打标", "激光雕刻", "激光加工", "激光设备", "光纤激光器", "CO2激光器", "紫外激光器", "超快激光", "激光雷达"],
        "exclude_keywords": ["半导体设备", "光刻机", "医疗", "医药"],
        "core_companies": ["大族激光", "华工科技", "锐科激光", "创鑫激光", "联赢激光", "杰普特", "柏楚电子", "海目星", "金运激光", "亚威股份"],
        "leader_companies": ["大族激光", "华工科技", "锐科激光"],
        "etf": "",
        "style": "高端制造",
        "capacity": "中",
        "leader_type": "趋势中军",
        "fund_type": "机构+游资",
        "priority": "SS"
    }

# 6. 新增环保主题
if '环保' not in hot_themes:
    hot_themes['环保'] = {
        "industry": ["环保工程及服务", "固废治理", "环境治理", "水务及水治理", "其他环保服务", "环保设备"],
        "concept": ["环保", "垃圾分类", "碳中和", "碳减排"],
        "keywords": ["环保", "固废处理", "垃圾分类", "垃圾焚烧", "危废处理", "水处理", "污水处理", "烟气治理", "环保工程", "碳中和", "碳减排", "环保设备", "环保服务"],
        "exclude_keywords": ["半导体", "芯片", "AI", "军工", "医药"],
        "core_companies": ["伟明环保", "东江环保", "高能环境", "绿色动力", "旺能环境", "中环环保", "维尔利", "德创环保", "雪浪环境", "先河环保"],
        "leader_companies": ["伟明环保", "东江环保", "高能环境"],
        "etf": "512580",
        "style": "价值",
        "capacity": "中",
        "leader_type": "趋势中军",
        "fund_type": "机构+游资",
        "priority": "S"
    }

# 7. 扩展大农业concept配置
if '大农业' in hot_themes:
    big_agri = hot_themes['大农业']
    add_concepts = ["粮油加工", "食品加工", "农产品加工", "食用油", "粮食", "大豆", "玉米", "小麦", "稻谷", "饲料原料"]
    for c in add_concepts:
        if c not in big_agri.get('concept', []):
            big_agri['concept'].append(c)

# 8. 扩展交通运输物流industry配置
if '交通运输物流' in hot_themes:
    transport = hot_themes['交通运输物流']
    add_industries = ["港口Ⅲ", "航运港口Ⅲ", "机场Ⅲ", "航空机场Ⅲ", "铁路运输Ⅲ", "物流Ⅲ"]
    for ind in add_industries:
        if ind not in transport.get('industry', []):
            transport['industry'].append(ind)

# 9. 扩展汽车零部件主题，添加乘用车整车相关
if '汽车零部件' in hot_themes:
    auto_parts = hot_themes['汽车零部件']
    add_industries = ["乘用车", "综合乘用车", "电动乘用车", "商用车", "其他乘用车"]
    for ind in add_industries:
        if ind not in auto_parts.get('industry', []):
            auto_parts['industry'].append(ind)

with open(THEME_FILE, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Total themes: {len(hot_themes)}")
print("New themes added:")
new_themes = ['轨交设备', '品牌消费电子', '钾肥磷化工', '中药', '激光设备', '环保']
for t in new_themes:
    if t in hot_themes:
        print(f"  ✓ {t}")

print("\nThemes extended:")
extended = ['大农业', '交通运输物流', '汽车零部件']
for t in extended:
    if t in hot_themes:
        print(f"  ✓ {t}")