"""继续扩展主题配置"""
import json

THEME_FILE = r"d:\mystock\solo\theme.json"

with open(THEME_FILE, 'r', encoding='utf-8') as f:
    data = json.load(f)

hot_themes = data['HOT_THEMES']

# 1. 新增综合主题（覆盖东阳光等综合行业股票）
if '综合' not in hot_themes:
    hot_themes['综合'] = {
        "industry": ["综合", "综合Ⅱ", "综合Ⅲ"],
        "concept": ["综合", "多元化", "集团"],
        "keywords": ["集团", "控股", "综合", "多元"],
        "exclude_keywords": ["半导体", "芯片", "AI"],
        "core_companies": ["东阳光"],
        "leader_companies": ["东阳光"],
        "etf": "",
        "style": "综合",
        "capacity": "小",
        "leader_type": "趋势中军",
        "fund_type": "机构+游资",
        "priority": "B"
    }
    print("新增主题: 综合")

# 2. 扩展钢铁主题（覆盖鄂尔多斯）
if '钢铁' in hot_themes:
    steel = hot_themes['钢铁']
    add_inds = ['冶钢辅料', '冶钢原料']
    for ind in add_inds:
        if ind not in steel.get('industry', []):
            steel['industry'].append(ind)
    print(f"钢铁扩展industry: {add_inds}")

# 3. 扩展化工链主题（覆盖膜材料、非金属材料、涂料油墨）
if '化工链' in hot_themes:
    chem = hot_themes['化工链']
    add_inds = ['膜材料', '非金属材料Ⅱ', '非金属材料Ⅲ', '涂料油墨', '化学制品']
    for ind in add_inds:
        if ind not in chem.get('industry', []):
            chem['industry'].append(ind)
    print(f"化工链扩展industry: {add_inds}")

# 4. 扩展半导体材料主题（覆盖电子化学品）
if '半导体材料' in hot_themes:
    sm = hot_themes['半导体材料']
    add_inds = ['电子化学品Ⅱ', '电子化学品Ⅲ']
    for ind in add_inds:
        if ind not in sm.get('industry', []):
            sm['industry'].append(ind)
    print(f"半导体材料扩展industry: {add_inds}")

# 5. 扩展金属制品主题（覆盖纽威股份）
if '金属制品' in hot_themes:
    mp = hot_themes['金属制品']
    add_kws = ['金属', '制品', '阀门', '管件']
    for kw in add_kws:
        if kw not in mp.get('keywords', []):
            mp['keywords'].append(kw)
    print(f"金属制品扩展keywords: {add_kws}")

# 6. 扩展化学纤维主题（覆盖新凤鸣）
if '化学纤维' in hot_themes:
    cf = hot_themes['化学纤维']
    add_kws = ['涤纶', '锦纶', '化纤', '聚酯']
    for kw in add_kws:
        if kw not in cf.get('keywords', []):
            cf['keywords'].append(kw)
    print(f"化学纤维扩展keywords: {add_kws}")

# 7. 扩展汽车零部件主题（覆盖春风动力、九号公司）
if '汽车零部件' in hot_themes:
    ap = hot_themes['汽车零部件']
    add_kws = ['摩托', '摩托车', '电动车', '电动']
    for kw in add_kws:
        if kw not in ap.get('keywords', []):
            ap['keywords'].append(kw)
    print(f"汽车零部件扩展keywords: {add_kws}")

# 8. 扩展家用电器主题（覆盖四川长虹）
if '家电家居链' in hot_themes:
    home = hot_themes['家电家居链']
    add_inds = ['彩电', '黑色家电', '白色家电']
    for ind in add_inds:
        if ind not in home.get('industry', []):
            home['industry'].append(ind)
    print(f"家电家居链扩展industry: {add_inds}")

# 9. 扩展交通运输物流主题（覆盖辽港股份、中远海发、中远海特）
if '交通运输物流' in hot_themes:
    transport = hot_themes['交通运输物流']
    add_kws = ['港口', '航运', '海运', '远洋']
    for kw in add_kws:
        if kw not in transport.get('keywords', []):
            transport['keywords'].append(kw)
    print(f"交通运输物流扩展keywords: {add_kws}")

# 10. 扩展纺织服饰主题（覆盖雅戈尔）
if '纺织服饰' in hot_themes:
    tex = hot_themes['纺织服饰']
    add_kws = ['雅戈尔', '服装']
    for kw in add_kws:
        if kw not in tex.get('keywords', []):
            tex['keywords'].append(kw)
    print(f"纺织服饰扩展keywords: {add_kws}")

# 11. 扩展钾肥磷化工主题（覆盖亚钾国际）
if '钾肥磷化工' in hot_themes:
    potash = hot_themes['钾肥磷化工']
    add_kws = ['钾', '磷', '化肥']
    for kw in add_kws:
        if kw not in potash.get('keywords', []):
            potash['keywords'].append(kw)
    print(f"钾肥磷化工扩展keywords: {add_kws}")

with open(THEME_FILE, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n主题总数: {len(hot_themes)}")