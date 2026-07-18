"""继续扩展主题配置覆盖更多未匹配股票"""
import json

THEME_FILE = r"d:\mystock\solo\theme.json"

with open(THEME_FILE, 'r', encoding='utf-8') as f:
    data = json.load(f)

hot_themes = data['HOT_THEMES']

# 1. 扩展工程机械与重型装备主题（覆盖能源及重型设备）
if '工程机械与重型装备' in hot_themes:
    mch = hot_themes['工程机械与重型装备']
    add_inds = ['能源及重型设备', '能源设备', '矿山设备', '油气设备', '海工装备', '钻井设备']
    for ind in add_inds:
        if ind not in mch.get('industry', []):
            mch['industry'].append(ind)
    print(f"工程机械与重型装备扩展industry: {add_inds}")

# 2. 扩展通信主题（如果存在）或扩展软件与IT服务
if '软件与IT服务' in hot_themes:
    soft = hot_themes['软件与IT服务']
    add_inds = ['通信终端及配件', '通信网络设备及器件', '通信设备']
    for ind in add_inds:
        if ind not in soft.get('industry', []):
            soft['industry'].append(ind)
    print(f"软件与IT服务扩展industry: {add_inds}")

# 3. 扩展消费电子与AI终端主题（覆盖其他电子）
if '消费电子与AI终端' in hot_themes:
    ce = hot_themes['消费电子与AI终端']
    add_inds = ['其他电子Ⅱ', '其他电子Ⅲ', '电子元器件']
    for ind in add_inds:
        if ind not in ce.get('industry', []):
            ce['industry'].append(ind)
    print(f"消费电子与AI终端扩展industry: {add_inds}")

# 4. 扩展汽车零部件主题（覆盖轮胎轮毂）
if '汽车零部件' in hot_themes:
    ap = hot_themes['汽车零部件']
    add_inds = ['轮胎轮毂', '摩托车', '摩托车及其他']
    for ind in add_inds:
        if ind not in ap.get('industry', []):
            ap['industry'].append(ind)
    print(f"汽车零部件扩展industry: {add_inds}")

# 5. 扩展化工链主题（覆盖塑料、膜材料、橡胶）
if '化工链' in hot_themes:
    chem = hot_themes['化工链']
    add_inds = ['塑料', '膜材料', '橡胶', '橡胶助剂', '合成树脂']
    for ind in add_inds:
        if ind not in chem.get('industry', []):
            chem['industry'].append(ind)
    print(f"化工链扩展industry: {add_inds}")

# 6. 扩展煤炭链主题（覆盖动力煤）
if '煤炭链' in hot_themes:
    coal = hot_themes['煤炭链']
    add_inds = ['动力煤', '煤炭开采', '煤炭']
    for ind in add_inds:
        if ind not in coal.get('industry', []):
            coal['industry'].append(ind)
    print(f"煤炭链扩展industry: {add_inds}")

# 7. 扩展医药产业链主题（覆盖电子化学品）
if '医药产业链' in hot_themes:
    pharma = hot_themes['医药产业链']
    add_inds = ['电子化学品Ⅱ', '电子化学品Ⅲ']
    for ind in add_inds:
        if ind not in pharma.get('industry', []):
            pharma['industry'].append(ind)
    print(f"医药产业链扩展industry: {add_inds}")

# 8. 扩展纺织服饰主题（如果存在）
if '纺织服饰' in hot_themes:
    tex = hot_themes['纺织服饰']
    add_inds = ['纺织服饰']
    for ind in add_inds:
        if ind not in tex.get('industry', []):
            tex['industry'].append(ind)
    print(f"纺织服饰扩展industry: {add_inds}")

# 9. 新增金属制品主题（覆盖鼎泰高科等）
if '金属制品' not in hot_themes:
    hot_themes['金属制品'] = {
        "industry": ["金属制品", "通用设备", "机械设备"],
        "concept": ["金属制品", "精密制造", "工业金属"],
        "keywords": ["金属制品", "精密制造", "金属加工", "模具", "紧固件", "弹簧", "冲压件", "钣金"],
        "exclude_keywords": ["半导体", "芯片", "AI"],
        "core_companies": ["鼎泰高科", "恒立液压", "艾迪精密", "锋龙股份"],
        "leader_companies": ["鼎泰高科", "恒立液压"],
        "etf": "",
        "style": "高端制造",
        "capacity": "中",
        "leader_type": "趋势中军",
        "fund_type": "机构+游资",
        "priority": "S"
    }
    print("新增主题: 金属制品")

with open(THEME_FILE, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n主题总数: {len(hot_themes)}")