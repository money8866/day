"""继续扩展主题配置"""
import json

THEME_FILE = r"d:\mystock\solo\theme.json"

with open(THEME_FILE, 'r', encoding='utf-8') as f:
    data = json.load(f)

hot_themes = data['HOT_THEMES']

# 1. 扩展医药产业链主题（覆盖血液制品、其他生物制品）
if '医药产业链' in hot_themes:
    pharma = hot_themes['医药产业链']
    add_kws = ['医药', '生物', '血液', '生物制品', '天坛', '特宝']
    for kw in add_kws:
        if kw not in pharma.get('keywords', []):
            pharma['keywords'].append(kw)
    print(f"医药产业链扩展keywords: {add_kws}")

# 2. 扩展软件与IT服务主题（覆盖安防设备）
if '软件与IT服务' in hot_themes:
    soft = hot_themes['软件与IT服务']
    add_kws = ['安防', '设备', '网络', '萤石', '监控']
    for kw in add_kws:
        if kw not in soft.get('keywords', []):
            soft['keywords'].append(kw)
    print(f"软件与IT服务扩展keywords: {add_kws}")

# 3. 扩展消费电子与AI终端主题（覆盖致尚科技、胜蓝股份）
if '消费电子与AI终端' in hot_themes:
    ce = hot_themes['消费电子与AI终端']
    add_kws = ['电子', '消费电子', '零部件', '致尚', '胜蓝']
    for kw in add_kws:
        if kw not in ce.get('keywords', []):
            ce['keywords'].append(kw)
    print(f"消费电子与AI终端扩展keywords: {add_kws}")

# 4. 扩展化工链主题（覆盖广东宏大、民爆制品）
if '化工链' in hot_themes:
    chem = hot_themes['化工链']
    add_kws = ['化工', '材料', '民爆', '宏大', '爆破']
    for kw in add_kws:
        if kw not in chem.get('keywords', []):
            chem['keywords'].append(kw)
    print(f"化工链扩展keywords: {add_kws}")

# 5. 扩展钾肥磷化工主题（覆盖东方铁塔）
if '钾肥磷化工' in hot_themes:
    potash = hot_themes['钾肥磷化工']
    add_kws = ['钾肥', '磷化工', '化肥', '东方', '铁塔']
    for kw in add_kws:
        if kw not in potash.get('keywords', []):
            potash['keywords'].append(kw)
    print(f"钾肥磷化工扩展keywords: {add_kws}")

# 6. 扩展交通运输物流主题（覆盖秦港股份、安通控股）
if '交通运输物流' in hot_themes:
    transport = hot_themes['交通运输物流']
    add_kws = ['港口', '航运', '物流', '秦港', '安通']
    for kw in add_kws:
        if kw not in transport.get('keywords', []):
            transport['keywords'].append(kw)
    print(f"交通运输物流扩展keywords: {add_kws}")

# 7. 扩展工业母机与自动化主题（覆盖上海机电、杰克科技、东方精工）
if '工业母机与自动化' in hot_themes:
    auto = hot_themes['工业母机与自动化']
    add_kws = ['设备', '制造', '机械', '工业', '机电', '杰克', '精工']
    for kw in add_kws:
        if kw not in auto.get('keywords', []):
            auto['keywords'].append(kw)
    print(f"工业母机与自动化扩展keywords: {add_kws}")

# 8. 扩展军工主题（覆盖长城军工）
if '军工' in hot_themes:
    military = hot_themes['军工']
    add_kws = ['军工', '国防', '兵装', '长城']
    for kw in add_kws:
        if kw not in military.get('keywords', []):
            military['keywords'].append(kw)
    print(f"军工扩展keywords: {add_kws}")

# 9. 扩展家电家居链主题（覆盖民爆光电、照明设备）
if '家电家居链' in hot_themes:
    home = hot_themes['家电家居链']
    add_kws = ['家电', '电器', '照明', '光电']
    for kw in add_kws:
        if kw not in home.get('keywords', []):
            home['keywords'].append(kw)
    print(f"家电家居链扩展keywords: {add_kws}")

# 10. 扩展半导体材料主题（覆盖唯特偶）
if '半导体材料' in hot_themes:
    sm = hot_themes['半导体材料']
    add_kws = ['电子化学品', '化学品', '材料', '唯特']
    for kw in add_kws:
        if kw not in sm.get('keywords', []):
            sm['keywords'].append(kw)
    print(f"半导体材料扩展keywords: {add_kws}")

# 11. 扩展消费电子与AI终端主题（覆盖瑞可达、汉朔科技）
if '消费电子与AI终端' in hot_themes:
    ce = hot_themes['消费电子与AI终端']
    add_kws = ['电子', '消费电子', '瑞可达', '汉朔']
    for kw in add_kws:
        if kw not in ce.get('keywords', []):
            ce['keywords'].append(kw)
    print(f"消费电子与AI终端扩展keywords: {add_kws}")

# 12. 扩展被动元件主题（覆盖海星股份）
if '被动元件' in hot_themes:
    pc = hot_themes['被动元件']
    add_kws = ['被动元件', '元件', '电容', '电阻', '海星']
    for kw in add_kws:
        if kw not in pc.get('keywords', []):
            pc['keywords'].append(kw)
    print(f"被动元件扩展keywords: {add_kws}")

# 13. 扩展大农业主题（覆盖安德利）
if '大农业' in hot_themes:
    agri = hot_themes['大农业']
    add_kws = ['农业', '农产品', '果蔬', '加工', '安德利']
    for kw in add_kws:
        if kw not in agri.get('keywords', []):
            agri['keywords'].append(kw)
    print(f"大农业扩展keywords: {add_kws}")

with open(THEME_FILE, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n主题总数: {len(hot_themes)}")