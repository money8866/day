"""继续扩展主题配置"""
import json

THEME_FILE = r"d:\mystock\solo\theme.json"

with open(THEME_FILE, 'r', encoding='utf-8') as f:
    data = json.load(f)

hot_themes = data['HOT_THEMES']

# 1. 扩展半导体材料主题（覆盖电子化学品股票）
if '半导体材料' in hot_themes:
    sm = hot_themes['半导体材料']
    add_kws = ['电子化学品', '化学品', '材料', '电子', '巨芯', '兴福', '新阳']
    for kw in add_kws:
        if kw not in sm.get('keywords', []):
            sm['keywords'].append(kw)
    print(f"半导体材料扩展keywords: {add_kws}")

# 2. 扩展化工链主题（覆盖非金属材料、石英等）
if '化工链' in hot_themes:
    chem = hot_themes['化工链']
    add_kws = ['化工', '材料', '非金属', '石英', '联瑞', '硅']
    for kw in add_kws:
        if kw not in chem.get('keywords', []):
            chem['keywords'].append(kw)
    print(f"化工链扩展keywords: {add_kws}")

# 3. 扩展消费电子与AI终端主题（覆盖利通电子）
if '消费电子与AI终端' in hot_themes:
    ce = hot_themes['消费电子与AI终端']
    add_kws = ['电子', '消费电子', '零部件', '利通']
    for kw in add_kws:
        if kw not in ce.get('keywords', []):
            ce['keywords'].append(kw)
    print(f"消费电子与AI终端扩展keywords: {add_kws}")

# 4. 扩展化学纤维主题（覆盖新凤鸣）
if '化学纤维' in hot_themes:
    cf = hot_themes['化学纤维']
    add_kws = ['化纤', '涤纶', '锦纶', '纤维', '新凤鸣']
    for kw in add_kws:
        if kw not in cf.get('keywords', []):
            cf['keywords'].append(kw)
    print(f"化学纤维扩展keywords: {add_kws}")

# 5. 扩展钢铁主题（覆盖鄂尔多斯、钒钛股份）
if '钢铁' in hot_themes:
    steel = hot_themes['钢铁']
    add_kws = ['钢铁', '钢', '铁', '鄂尔多斯', '钒钛', '合金']
    for kw in add_kws:
        if kw not in steel.get('keywords', []):
            steel['keywords'].append(kw)
    print(f"钢铁扩展keywords: {add_kws}")

# 6. 扩展交通运输物流主题（覆盖北部湾港、建发股份）
if '交通运输物流' in hot_themes:
    transport = hot_themes['交通运输物流']
    add_kws = ['港口', '航运', '物流', '北部湾', '建发', '供应链']
    for kw in add_kws:
        if kw not in transport.get('keywords', []):
            transport['keywords'].append(kw)
    print(f"交通运输物流扩展keywords: {add_kws}")

# 7. 扩展光学光电子主题（覆盖光智科技）
if '光学光电子' in hot_themes:
    opt = hot_themes['光学光电子']
    add_kws = ['光学', '元件', '光电子', '光智']
    for kw in add_kws:
        if kw not in opt.get('keywords', []):
            opt['keywords'].append(kw)
    print(f"光学光电子扩展keywords: {add_kws}")

# 8. 扩展AI芯片主题（覆盖联芸科技）
if 'AI芯片' in hot_themes:
    ai_chip = hot_themes['AI芯片']
    add_kws = ['芯片', '半导体', '联芸']
    for kw in add_kws:
        if kw not in ai_chip.get('keywords', []):
            ai_chip['keywords'].append(kw)
    print(f"AI芯片扩展keywords: {add_kws}")

# 9. 扩展工程机械与重型装备主题（覆盖中创智领）
if '工程机械与重型装备' in hot_themes:
    mch = hot_themes['工程机械与重型装备']
    add_kws = ['机械', '装备', '重工', '设备', '中创']
    for kw in add_kws:
        if kw not in mch.get('keywords', []):
            mch['keywords'].append(kw)
    print(f"工程机械与重型装备扩展keywords: {add_kws}")

# 10. 扩展玻璃建材主题（覆盖天山股份）
if '玻璃建材' in hot_themes:
    glass = hot_themes['玻璃建材']
    add_kws = ['建材', '水泥', '玻璃', '天山']
    for kw in add_kws:
        if kw not in glass.get('keywords', []):
            glass['keywords'].append(kw)
    print(f"玻璃建材扩展keywords: {add_kws}")

# 11. 扩展医药产业链主题（覆盖天坛生物）
if '医药产业链' in hot_themes:
    pharma = hot_themes['医药产业链']
    add_kws = ['医药', '生物', '血液', '天坛']
    for kw in add_kws:
        if kw not in pharma.get('keywords', []):
            pharma['keywords'].append(kw)
    print(f"医药产业链扩展keywords: {add_kws}")

# 12. 扩展汽车零部件主题（覆盖斯菱智驱）
if '汽车零部件' in hot_themes:
    ap = hot_themes['汽车零部件']
    add_kws = ['汽车', '零部件', '底盘', '斯菱', '智驱']
    for kw in add_kws:
        if kw not in ap.get('keywords', []):
            ap['keywords'].append(kw)
    print(f"汽车零部件扩展keywords: {add_kws}")

# 13. 扩展家电家居链主题（添加更多行业）
if '家电家居链' in hot_themes:
    home = hot_themes['家电家居链']
    add_inds = ['家用电器']
    for ind in add_inds:
        if ind not in home.get('industry', []):
            home['industry'].append(ind)
    print(f"家电家居链扩展industry: {add_inds}")

with open(THEME_FILE, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n主题总数: {len(hot_themes)}")