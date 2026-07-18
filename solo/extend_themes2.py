"""继续扩展主题配置"""
import json

THEME_FILE = r"d:\mystock\solo\theme.json"

with open(THEME_FILE, 'r', encoding='utf-8') as f:
    data = json.load(f)

hot_themes = data['HOT_THEMES']

# 1. 新增纺织服饰主题
if '纺织服饰' not in hot_themes:
    hot_themes['纺织服饰'] = {
        "industry": ["纺织服饰", "纺织制造", "纺织鞋类制造", "服装家纺", "家纺", "鞋类制造"],
        "concept": ["纺织", "服装", "家纺", "鞋类", "跨境电商"],
        "keywords": ["纺织", "服装", "家纺", "鞋类", "面料", "印染", "纺织制造", "服装制造"],
        "exclude_keywords": ["半导体", "芯片", "AI", "医药"],
        "core_companies": ["华利集团", "申洲国际", "安踏体育", "李宁", "特步国际"],
        "leader_companies": ["华利集团", "申洲国际"],
        "etf": "516620",
        "style": "消费",
        "capacity": "中",
        "leader_type": "趋势中军",
        "fund_type": "机构+游资",
        "priority": "S"
    }
    print("新增主题: 纺织服饰")

# 2. 新增航空运输主题
if '航空运输' not in hot_themes:
    hot_themes['航空运输'] = {
        "industry": ["航空运输", "航空机场", "机场", "跨境物流", "物流"],
        "concept": ["航空", "机场", "物流", "跨境物流"],
        "keywords": ["航空运输", "机场", "空运", "航空货运", "物流", "货运"],
        "exclude_keywords": ["半导体", "芯片", "AI", "医药"],
        "core_companies": ["中国国航", "南方航空", "东方航空", "海南航空", "上海机场", "白云机场"],
        "leader_companies": ["中国国航", "上海机场"],
        "etf": "",
        "style": "价值",
        "capacity": "大",
        "leader_type": "趋势中军",
        "fund_type": "机构+游资",
        "priority": "S"
    }
    print("新增主题: 航空运输")

# 3. 新增工业气体主题（覆盖广钢气体等）
if '工业气体' not in hot_themes:
    hot_themes['工业气体'] = {
        "industry": ["电子化学品Ⅱ", "电子化学品Ⅲ", "化学原料", "其他化学原料", "化学制品"],
        "concept": ["工业气体", "电子特气", "半导体材料"],
        "keywords": ["工业气体", "电子特气", "特种气体", "气体", "氩气", "氮气", "氧气", "氦气", "氖气"],
        "exclude_keywords": ["半导体设备", "光刻胶", "芯片"],
        "core_companies": ["广钢气体", "杭氧股份", "华特气体", "金宏气体", "昊华科技"],
        "leader_companies": ["杭氧股份", "华特气体"],
        "etf": "",
        "style": "资源",
        "capacity": "中",
        "leader_type": "趋势中军",
        "fund_type": "机构+游资",
        "priority": "S"
    }
    print("新增主题: 工业气体")

# 4. 扩展石油石化主题（覆盖其他石化）
if '石油石化' in hot_themes:
    petro = hot_themes['石油石化']
    add_inds = ['其他石化', '炼化及贸易']
    for ind in add_inds:
        if ind not in petro.get('industry', []):
            petro['industry'].append(ind)
    print(f"石油石化扩展industry: {add_inds}")

# 5. 扩展基建地产链主题（覆盖产业地产）
if '基建地产链' in hot_themes:
    real = hot_themes['基建地产链']
    add_inds = ['产业地产']
    for ind in add_inds:
        if ind not in real.get('industry', []):
            real['industry'].append(ind)
    print(f"基建地产链扩展industry: {add_inds}")

# 6. 扩展煤炭链主题（覆盖电投能源的动力煤）
if '煤炭链' in hot_themes:
    coal = hot_themes['煤炭链']
    add_concepts = ['动力煤', '煤炭']
    for c in add_concepts:
        if c not in coal.get('concept', []):
            coal['concept'].append(c)
    print(f"煤炭链扩展concept: {add_concepts}")

# 7. 扩展钾肥磷化工主题（覆盖亚钾国际）
if '钾肥磷化工' in hot_themes:
    potash = hot_themes['钾肥磷化工']
    add_inds = ['农化制品']
    for ind in add_inds:
        if ind not in potash.get('industry', []):
            potash['industry'].append(ind)
    print(f"钾肥磷化工扩展industry: {add_inds}")

# 8. 扩展工业母机与自动化主题（覆盖其他自动化设备）
if '工业母机与自动化' in hot_themes:
    auto = hot_themes['工业母机与自动化']
    add_inds = ['其他自动化设备']
    for ind in add_inds:
        if ind not in auto.get('industry', []):
            auto['industry'].append(ind)
    print(f"工业母机与自动化扩展industry: {add_inds}")

with open(THEME_FILE, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n主题总数: {len(hot_themes)}")