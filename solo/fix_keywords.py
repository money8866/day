"""修复银行主题关键词"""
import json

THEME_FILE = r"d:\mystock\solo\theme.json"

with open(THEME_FILE, 'r', encoding='utf-8') as f:
    data = json.load(f)

hot_themes = data['HOT_THEMES']

# 修复银行主题
if '银行' in hot_themes:
    bank = hot_themes['银行']
    # 添加更多关键词，覆盖各种银行名称
    add_keywords = ['农商行', '城商行', '股份制银行', '国有银行', '商行', '邮储']
    for kw in add_keywords:
        if kw not in bank.get('keywords', []):
            bank['keywords'].append(kw)
    print(f"银行主题关键词已更新: {bank['keywords']}")

# 修复交通运输物流主题
if '交通运输物流' in hot_themes:
    transport = hot_themes['交通运输物流']
    # 添加港口相关关键词
    add_keywords = ['港口', '海港', '码头', '空港']
    for kw in add_keywords:
        if kw not in transport.get('keywords', []):
            transport['keywords'].append(kw)
    print(f"交通运输物流关键词已更新: {transport['keywords']}")

with open(THEME_FILE, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("\n主题配置已更新")