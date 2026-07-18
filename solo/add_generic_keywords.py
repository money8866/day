"""添加更通用的关键词提高匹配度"""
import json

THEME_FILE = r"d:\mystock\solo\theme.json"

with open(THEME_FILE, 'r', encoding='utf-8') as f:
    data = json.load(f)

hot_themes = data['HOT_THEMES']

# 1. 航空运输主题
if '航空运输' in hot_themes:
    at = hot_themes['航空运输']
    add_kws = ['航空', '空运', '机场', '航']
    for kw in add_kws:
        if kw not in at.get('keywords', []):
            at['keywords'].append(kw)
    print(f"航空运输扩展keywords: {add_kws}")

# 2. 基建地产链主题
if '基建地产链' in hot_themes:
    real = hot_themes['基建地产链']
    add_kws = ['地产', '房地产', '园区', '开发']
    for kw in add_kws:
        if kw not in real.get('keywords', []):
            real['keywords'].append(kw)
    print(f"基建地产链扩展keywords: {add_kws}")

# 3. 钾肥磷化工主题
if '钾肥磷化工' in hot_themes:
    potash = hot_themes['钾肥磷化工']
    add_kws = ['钾肥', '磷化工', '化肥', '化工']
    for kw in add_kws:
        if kw not in potash.get('keywords', []):
            potash['keywords'].append(kw)
    print(f"钾肥磷化工扩展keywords: {add_kws}")

# 4. 多元金融主题
if '多元金融' in hot_themes:
    mf = hot_themes['多元金融']
    add_kws = ['金融', '租赁', '金租']
    for kw in add_kws:
        if kw not in mf.get('keywords', []):
            mf['keywords'].append(kw)
    print(f"多元金融扩展keywords: {add_kws}")

# 5. 纺织服饰主题
if '纺织服饰' in hot_themes:
    tex = hot_themes['纺织服饰']
    add_kws = ['服装', '纺织', '服饰', '家纺']
    for kw in add_kws:
        if kw not in tex.get('keywords', []):
            tex['keywords'].append(kw)
    print(f"纺织服饰扩展keywords: {add_kws}")

# 6. 红利公用事业主题
if '红利公用事业' in hot_themes:
    util = hot_themes['红利公用事业']
    add_kws = ['电力', '公用', '能源', '发电']
    for kw in add_kws:
        if kw not in util.get('keywords', []):
            util['keywords'].append(kw)
    print(f"红利公用事业扩展keywords: {add_kws}")

# 7. 汽车零部件主题
if '汽车零部件' in hot_themes:
    ap = hot_themes['汽车零部件']
    add_kws = ['汽车', '零部件', '轮胎', '橡胶']
    for kw in add_kws:
        if kw not in ap.get('keywords', []):
            ap['keywords'].append(kw)
    print(f"汽车零部件扩展keywords: {add_kws}")

# 8. 化工链主题
if '化工链' in hot_themes:
    chem = hot_themes['化工链']
    add_kws = ['化工', '材料', '树脂', '塑料']
    for kw in add_kws:
        if kw not in chem.get('keywords', []):
            chem['keywords'].append(kw)
    print(f"化工链扩展keywords: {add_kws}")

# 9. 工程机械与重型装备主题
if '工程机械与重型装备' in hot_themes:
    mch = hot_themes['工程机械与重型装备']
    add_kws = ['机械', '装备', '重工', '设备']
    for kw in add_kws:
        if kw not in mch.get('keywords', []):
            mch['keywords'].append(kw)
    print(f"工程机械与重型装备扩展keywords: {add_kws}")

# 10. 消费电子与AI终端主题
if '消费电子与AI终端' in hot_themes:
    ce = hot_themes['消费电子与AI终端']
    add_kws = ['电子', '芯片', '元器件']
    for kw in add_kws:
        if kw not in ce.get('keywords', []):
            ce['keywords'].append(kw)
    print(f"消费电子与AI终端扩展keywords: {add_kws}")

# 11. 软件与IT服务主题
if '软件与IT服务' in hot_themes:
    soft = hot_themes['软件与IT服务']
    add_kws = ['通信', '设备', '网络']
    for kw in add_kws:
        if kw not in soft.get('keywords', []):
            soft['keywords'].append(kw)
    print(f"软件与IT服务扩展keywords: {add_kws}")

# 12. 造纸轻工主题
if '造纸轻工' in hot_themes:
    paper = hot_themes['造纸轻工']
    add_kws = ['造纸', '纸业', '轻工']
    for kw in add_kws:
        if kw not in paper.get('keywords', []):
            paper['keywords'].append(kw)
    print(f"造纸轻工扩展keywords: {add_kws}")

with open(THEME_FILE, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n主题总数: {len(hot_themes)}")