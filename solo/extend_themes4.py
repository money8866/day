"""继续扩展主题配置"""
import json

THEME_FILE = r"d:\mystock\solo\theme.json"

with open(THEME_FILE, 'r', encoding='utf-8') as f:
    data = json.load(f)

hot_themes = data['HOT_THEMES']

# 1. 扩展航空运输主题（覆盖海航控股）
if '航空运输' in hot_themes:
    at = hot_themes['航空运输']
    add_kws = ['航空运输', '航空公司', '客运', '货运']
    for kw in add_kws:
        if kw not in at.get('keywords', []):
            at['keywords'].append(kw)
    print(f"航空运输扩展keywords: {add_kws}")

# 2. 扩展化纤主题（覆盖中复神鹰）
if '化工链' in hot_themes:
    chem = hot_themes['化工链']
    add_inds = ['化学纤维', '其他化学纤维']
    for ind in add_inds:
        if ind not in chem.get('industry', []):
            chem['industry'].append(ind)
    print(f"化工链扩展industry: {add_inds}")

# 3. 扩展电力公用事业主题（覆盖京能电力）
if '红利公用事业' in hot_themes:
    util = hot_themes['红利公用事业']
    add_inds = ['火力发电', '电力', '公用事业']
    for ind in add_inds:
        if ind not in util.get('industry', []):
            util['industry'].append(ind)
    print(f"红利公用事业扩展industry: {add_inds}")

# 4. 新增化学纤维主题
if '化学纤维' not in hot_themes:
    hot_themes['化学纤维'] = {
        "industry": ["化学纤维", "其他化学纤维", "合成纤维", "粘胶纤维"],
        "concept": ["化纤", "涤纶", "锦纶", "氨纶"],
        "keywords": ["化纤", "化学纤维", "涤纶", "锦纶", "氨纶", "粘胶", "聚酯纤维"],
        "exclude_keywords": ["半导体", "芯片", "AI", "医药"],
        "core_companies": ["中复神鹰", "恒力石化", "荣盛石化", "桐昆股份", "恒逸石化"],
        "leader_companies": ["中复神鹰", "恒力石化"],
        "etf": "",
        "style": "周期",
        "capacity": "中",
        "leader_type": "趋势中军",
        "fund_type": "机构+游资",
        "priority": "S"
    }
    print("新增主题: 化学纤维")

# 5. 扩展纺织服饰主题（覆盖雅戈尔）
if '纺织服饰' in hot_themes:
    tex = hot_themes['纺织服饰']
    add_inds = ['服装家纺', '非运动服装']
    for ind in add_inds:
        if ind not in tex.get('industry', []):
            tex['industry'].append(ind)
    print(f"纺织服饰扩展industry: {add_inds}")

# 6. 扩展工业气体主题（覆盖中巨芯）
if '工业气体' in hot_themes:
    ig = hot_themes['工业气体']
    add_concepts = ['电子化学品']
    for c in add_concepts:
        if c not in ig.get('concept', []):
            ig['concept'].append(c)
    print(f"工业气体扩展concept: {add_concepts}")

# 7. 扩展多元金融主题（覆盖江苏金租）
if '多元金融' in hot_themes:
    mf = hot_themes['多元金融']
    add_kws = ['租赁', '融资租赁', '金融租赁']
    for kw in add_kws:
        if kw not in mf.get('keywords', []):
            mf['keywords'].append(kw)
    print(f"多元金融扩展keywords: {add_kws}")

# 8. 扩展工业母机与自动化主题（覆盖华曙高科）
if '工业母机与自动化' in hot_themes:
    auto = hot_themes['工业母机与自动化']
    add_inds = ['其他通用设备']
    for ind in add_inds:
        if ind not in auto.get('industry', []):
            auto['industry'].append(ind)
    print(f"工业母机与自动化扩展industry: {add_inds}")

# 9. 扩展汽车零部件主题（覆盖联合动力）
if '汽车零部件' in hot_themes:
    ap = hot_themes['汽车零部件']
    add_inds = ['汽车电子电气系统']
    for ind in add_inds:
        if ind not in ap.get('industry', []):
            ap['industry'].append(ind)
    print(f"汽车零部件扩展industry: {add_inds}")

# 10. 扩展石油石化主题（覆盖桐昆股份）
if '石油石化' in hot_themes:
    petro = hot_themes['石油石化']
    add_kws = ['桐昆']
    for kw in add_kws:
        if kw not in petro.get('keywords', []):
            petro['keywords'].append(kw)
    print(f"石油石化扩展keywords: {add_kws}")

with open(THEME_FILE, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n主题总数: {len(hot_themes)}")