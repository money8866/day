"""继续扩展主题配置"""
import json

THEME_FILE = r"d:\mystock\solo\theme.json"

with open(THEME_FILE, 'r', encoding='utf-8') as f:
    data = json.load(f)

hot_themes = data['HOT_THEMES']

# 1. 新增造纸轻工主题
if '造纸轻工' not in hot_themes:
    hot_themes['造纸轻工'] = {
        "industry": ["造纸", "轻工制造", "大宗用纸", "包装印刷", "文娱用品"],
        "concept": ["造纸", "包装", "印刷", "轻工"],
        "keywords": ["造纸", "纸浆", "包装", "印刷", "纸张", "纸板", "纸箱"],
        "exclude_keywords": ["半导体", "芯片", "AI", "医药"],
        "core_companies": ["太阳纸业", "玖龙纸业", "晨鸣纸业", "山鹰国际", "景兴纸业"],
        "leader_companies": ["太阳纸业", "玖龙纸业"],
        "etf": "",
        "style": "周期",
        "capacity": "中",
        "leader_type": "趋势中军",
        "fund_type": "机构+游资",
        "priority": "S"
    }
    print("新增主题: 造纸轻工")

# 2. 扩展多元金融主题（覆盖租赁）
if '多元金融' in hot_themes:
    mf = hot_themes['多元金融']
    add_inds = ['租赁', '融资租赁']
    for ind in add_inds:
        if ind not in mf.get('industry', []):
            mf['industry'].append(ind)
    print(f"多元金融扩展industry: {add_inds}")

# 3. 扩展建筑装饰主题（如果存在）或新增专业工程主题
if '建筑装饰' not in hot_themes:
    hot_themes['建筑装饰'] = {
        "industry": ["建筑装饰", "专业工程", "其他专业工程", "装修建材", "基础建设"],
        "concept": ["建筑", "基建", "装饰", "装修"],
        "keywords": ["建筑装饰", "装修", "装饰", "基建", "工程", "专业工程"],
        "exclude_keywords": ["半导体", "芯片", "AI", "医药"],
        "core_companies": ["亚翔集成", "金螳螂", "洪涛股份", "广田集团", "全筑股份"],
        "leader_companies": ["金螳螂", "亚翔集成"],
        "etf": "",
        "style": "周期",
        "capacity": "中",
        "leader_type": "趋势中军",
        "fund_type": "机构+游资",
        "priority": "S"
    }
    print("新增主题: 建筑装饰")

# 4. 扩展改性塑料主题（如果存在）或扩展化工链
if '化工链' in hot_themes:
    chem = hot_themes['化工链']
    add_inds = ['改性塑料']
    for ind in add_inds:
        if ind not in chem.get('industry', []):
            chem['industry'].append(ind)
    print(f"化工链扩展industry: {add_inds}")

# 5. 扩展消费电子与AI终端关键词（覆盖香农芯创）
if '消费电子与AI终端' in hot_themes:
    ce = hot_themes['消费电子与AI终端']
    add_kws = ['芯片分销', '电子分销', '元器件分销']
    for kw in add_kws:
        if kw not in ce.get('keywords', []):
            ce['keywords'].append(kw)
    print(f"消费电子与AI终端扩展keywords: {add_kws}")

# 6. 扩展工程机械与重型装备关键词（覆盖杰瑞股份）
if '工程机械与重型装备' in hot_themes:
    mch = hot_themes['工程机械与重型装备']
    add_kws = ['油气设备', '油田服务', '钻井设备', '海工装备', '能源装备']
    for kw in add_kws:
        if kw not in mch.get('keywords', []):
            mch['keywords'].append(kw)
    print(f"工程机械与重型装备扩展keywords: {add_kws}")

# 7. 扩展汽车零部件关键词（覆盖赛轮轮胎等）
if '汽车零部件' in hot_themes:
    ap = hot_themes['汽车零部件']
    add_kws = ['轮胎', '轮毂', '摩托车', '摩托']
    for kw in add_kws:
        if kw not in ap.get('keywords', []):
            ap['keywords'].append(kw)
    print(f"汽车零部件扩展keywords: {add_kws}")

# 8. 扩展石油石化关键词（覆盖桐昆股份）
if '石油石化' in hot_themes:
    petro = hot_themes['石油石化']
    add_kws = ['炼化', '石化', '聚酯', '涤纶', 'PTA']
    for kw in add_kws:
        if kw not in petro.get('keywords', []):
            petro['keywords'].append(kw)
    print(f"石油石化扩展keywords: {add_kws}")

# 9. 扩展基建地产链关键词（覆盖张江高科）
if '基建地产链' in hot_themes:
    real = hot_themes['基建地产链']
    add_kws = ['产业地产', '园区开发', '房地产开发']
    for kw in add_kws:
        if kw not in real.get('keywords', []):
            real['keywords'].append(kw)
    print(f"基建地产链扩展keywords: {add_kws}")

# 10. 扩展软件与IT服务关键词（覆盖信科移动）
if '软件与IT服务' in hot_themes:
    soft = hot_themes['软件与IT服务']
    add_kws = ['通信设备', '通信网络', '基站', '5G', '通信终端']
    for kw in add_kws:
        if kw not in soft.get('keywords', []):
            soft['keywords'].append(kw)
    print(f"软件与IT服务扩展keywords: {add_kws}")

with open(THEME_FILE, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n主题总数: {len(hot_themes)}")