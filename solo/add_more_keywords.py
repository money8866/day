"""继续添加通用关键词"""
import json

THEME_FILE = r"d:\mystock\solo\theme.json"

with open(THEME_FILE, 'r', encoding='utf-8') as f:
    data = json.load(f)

hot_themes = data['HOT_THEMES']

# 1. 化工链主题添加更通用关键词
if '化工链' in hot_themes:
    chem = hot_themes['化工链']
    add_kws = ['化工', '材料', '树脂', '塑料', '膜', '油墨', '涂料', '合成', '化学']
    for kw in add_kws:
        if kw not in chem.get('keywords', []):
            chem['keywords'].append(kw)
    print(f"化工链扩展keywords: {add_kws}")

# 2. 汽车零部件主题添加更通用关键词
if '汽车零部件' in hot_themes:
    ap = hot_themes['汽车零部件']
    add_kws = ['汽车', '摩托', '车', '动力', '九号']
    for kw in add_kws:
        if kw not in ap.get('keywords', []):
            ap['keywords'].append(kw)
    print(f"汽车零部件扩展keywords: {add_kws}")

# 3. 半导体材料主题添加更通用关键词
if '半导体材料' in hot_themes:
    sm = hot_themes['半导体材料']
    add_kws = ['电子化学品', '化学品', '电子材料', '材料']
    for kw in add_kws:
        if kw not in sm.get('keywords', []):
            sm['keywords'].append(kw)
    print(f"半导体材料扩展keywords: {add_kws}")

# 4. 金属制品主题添加更通用关键词
if '金属制品' in hot_themes:
    mp = hot_themes['金属制品']
    add_kws = ['金属', '制品', '股份', '制造']
    for kw in add_kws:
        if kw not in mp.get('keywords', []):
            mp['keywords'].append(kw)
    print(f"金属制品扩展keywords: {add_kws}")

# 5. 化学纤维主题添加更通用关键词
if '化学纤维' in hot_themes:
    cf = hot_themes['化学纤维']
    add_kws = ['化纤', '涤纶', '锦纶', '纤维', '纺织', '新材料']
    for kw in add_kws:
        if kw not in cf.get('keywords', []):
            cf['keywords'].append(kw)
    print(f"化学纤维扩展keywords: {add_kws}")

# 6. 钢铁主题添加更通用关键词
if '钢铁' in hot_themes:
    steel = hot_themes['钢铁']
    add_kws = ['钢铁', '钢', '铁', '冶金', '合金']
    for kw in add_kws:
        if kw not in steel.get('keywords', []):
            steel['keywords'].append(kw)
    print(f"钢铁扩展keywords: {add_kws}")

# 7. 家用电器主题添加更通用关键词
if '家电家居链' in hot_themes:
    home = hot_themes['家电家居链']
    add_kws = ['家电', '电器', '彩电', '电视', '长虹']
    for kw in add_kws:
        if kw not in home.get('keywords', []):
            home['keywords'].append(kw)
    print(f"家电家居链扩展keywords: {add_kws}")

# 8. 消费电子与AI终端主题添加更通用关键词
if '消费电子与AI终端' in hot_themes:
    ce = hot_themes['消费电子与AI终端']
    add_kws = ['电子', '消费电子', '零部件', '组装']
    for kw in add_kws:
        if kw not in ce.get('keywords', []):
            ce['keywords'].append(kw)
    print(f"消费电子与AI终端扩展keywords: {add_kws}")

# 9. 工业母机与自动化主题添加更通用关键词
if '工业母机与自动化' in hot_themes:
    auto = hot_themes['工业母机与自动化']
    add_kws = ['设备', '制造', '机械', '工业', '高科']
    for kw in add_kws:
        if kw not in auto.get('keywords', []):
            auto['keywords'].append(kw)
    print(f"工业母机与自动化扩展keywords: {add_kws}")

# 10. 纺织服饰主题添加更通用关键词
if '纺织服饰' in hot_themes:
    tex = hot_themes['纺织服饰']
    add_kws = ['服装', '服饰', '纺织', '家纺', '海澜']
    for kw in add_kws:
        if kw not in tex.get('keywords', []):
            tex['keywords'].append(kw)
    print(f"纺织服饰扩展keywords: {add_kws}")

# 11. 交通运输物流主题添加更通用关键词
if '交通运输物流' in hot_themes:
    transport = hot_themes['交通运输物流']
    add_kws = ['港口', '航运', '海运', '物流', '中远', '辽港']
    for kw in add_kws:
        if kw not in transport.get('keywords', []):
            transport['keywords'].append(kw)
    print(f"交通运输物流扩展keywords: {add_kws}")

# 12. 光学光电子主题添加更通用关键词
if '光学光电子' in hot_themes:
    opt = hot_themes['光学光电子']
    add_kws = ['光学', '元件', '激光', '光电子']
    for kw in add_kws:
        if kw not in opt.get('keywords', []):
            opt['keywords'].append(kw)
    print(f"光学光电子扩展keywords: {add_kws}")

with open(THEME_FILE, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n主题总数: {len(hot_themes)}")