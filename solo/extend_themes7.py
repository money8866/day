"""继续扩展主题配置"""
import json

THEME_FILE = r"d:\mystock\solo\theme.json"

with open(THEME_FILE, 'r', encoding='utf-8') as f:
    data = json.load(f)

hot_themes = data['HOT_THEMES']

# 1. 扩展化工链主题（覆盖东材科技、圣泉集团、百合花、长裕集团、广东宏大）
if '化工链' in hot_themes:
    chem = hot_themes['化工链']
    add_kws = ['化工', '材料', '树脂', '塑料', '膜', '油墨', '涂料', '合成', '化学',
               '东材', '圣泉', '百合花', '长裕', '无机盐', '民爆', '宏大']
    for kw in add_kws:
        if kw not in chem.get('keywords', []):
            chem['keywords'].append(kw)
    print(f"化工链扩展keywords: {add_kws}")

# 2. 扩展医药产业链主题（覆盖天坛生物、特宝生物）
if '医药产业链' in hot_themes:
    pharma = hot_themes['医药产业链']
    add_kws = ['医药', '生物', '血液', '天坛', '特宝', '生物制品']
    for kw in add_kws:
        if kw not in pharma.get('keywords', []):
            pharma['keywords'].append(kw)
    print(f"医药产业链扩展keywords: {add_kws}")

# 3. 扩展功率半导体主题（覆盖*ST闻泰）
if '功率半导体' in hot_themes:
    ps = hot_themes['功率半导体']
    add_kws = ['半导体', '芯片', '闻泰', '分立器件']
    for kw in add_kws:
        if kw not in ps.get('keywords', []):
            ps['keywords'].append(kw)
    print(f"功率半导体扩展keywords: {add_kws}")

# 4. 扩展消费电子与AI终端主题（覆盖深圳华强、飞荣达、致尚科技）
if '消费电子与AI终端' in hot_themes:
    ce = hot_themes['消费电子与AI终端']
    add_kws = ['电子', '消费电子', '零部件', '华强', '飞荣达', '致尚']
    for kw in add_kws:
        if kw not in ce.get('keywords', []):
            ce['keywords'].append(kw)
    print(f"消费电子与AI终端扩展keywords: {add_kws}")

# 5. 扩展汽车零部件主题（覆盖华懋科技）
if '汽车零部件' in hot_themes:
    ap = hot_themes['汽车零部件']
    add_kws = ['汽车', '零部件', '车身', '华懋']
    for kw in add_kws:
        if kw not in ap.get('keywords', []):
            ap['keywords'].append(kw)
    print(f"汽车零部件扩展keywords: {add_kws}")

# 6. 扩展工业母机与自动化主题（覆盖凌云光、开山股份）
if '工业母机与自动化' in hot_themes:
    auto = hot_themes['工业母机与自动化']
    add_kws = ['设备', '制造', '机械', '工业', '凌云', '开山']
    for kw in add_kws:
        if kw not in auto.get('keywords', []):
            auto['keywords'].append(kw)
    print(f"工业母机与自动化扩展keywords: {add_kws}")

# 7. 扩展软件与IT服务主题（覆盖萤石网络）
if '软件与IT服务' in hot_themes:
    soft = hot_themes['软件与IT服务']
    add_kws = ['安防', '设备', '网络', '萤石']
    for kw in add_kws:
        if kw not in soft.get('keywords', []):
            soft['keywords'].append(kw)
    print(f"软件与IT服务扩展keywords: {add_kws}")

# 8. 扩展交通运输物流主题（覆盖广深铁路）
if '交通运输物流' in hot_themes:
    transport = hot_themes['交通运输物流']
    add_kws = ['铁路', '运输', '广深']
    for kw in add_kws:
        if kw not in transport.get('keywords', []):
            transport['keywords'].append(kw)
    print(f"交通运输物流扩展keywords: {add_kws}")

# 9. 扩展半导体材料主题（覆盖嘉德利）
if '半导体材料' in hot_themes:
    sm = hot_themes['半导体材料']
    add_kws = ['电子化学品', '化学品', '材料', '嘉德']
    for kw in add_kws:
        if kw not in sm.get('keywords', []):
            sm['keywords'].append(kw)
    print(f"半导体材料扩展keywords: {add_kws}")

# 10. 扩展钢铁主题（覆盖方大炭素）
if '钢铁' in hot_themes:
    steel = hot_themes['钢铁']
    add_kws = ['钢铁', '钢', '铁', '方大', '炭素']
    for kw in add_kws:
        if kw not in steel.get('keywords', []):
            steel['keywords'].append(kw)
    print(f"钢铁扩展keywords: {add_kws}")

# 11. 扩展电力设备主题（覆盖恒运昌）
if '电力设备' in hot_themes:
    pe = hot_themes['电力设备']
    add_kws = ['电力', '设备', '电源', '恒运']
    for kw in add_kws:
        if kw not in pe.get('keywords', []):
            pe['keywords'].append(kw)
    print(f"电力设备扩展keywords: {add_kws}")

# 12. 扩展软件与IT服务主题（覆盖华测导航）
if '软件与IT服务' in hot_themes:
    soft = hot_themes['软件与IT服务']
    add_kws = ['导航', '通信', '华测']
    for kw in add_kws:
        if kw not in soft.get('keywords', []):
            soft['keywords'].append(kw)
    print(f"软件与IT服务扩展keywords: {add_kws}")

with open(THEME_FILE, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n主题总数: {len(hot_themes)}")