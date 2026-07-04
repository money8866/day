# -*- coding: utf-8 -*-
"""检查误判股票是否已被过滤"""
import json

with open(r'd:\mystock\cache_daily\theme_stock_map_latest.json', 'r', encoding='utf-8') as f:
    d = json.load(f)

themes = d['themes']
checks = {
    'AI算力基建': ['思源电气', '中国宝安', '金刚光伏', '诺德股份'],
    '金融科技': ['汤姆猫', '天下秀', '数据港', '智洋创新', '恒实科技'],
    '合成生物': ['牧原股份', '凯莱英', '双成药业'],
    'AI文娱消费': ['华设集团'],
    '氢能': ['金风科技', '许继电气', '横店东磁'],
    '煤炭链': ['君正集团', '北元化工'],
    '半导体封测与先进封装': ['利亚德', '沃格光电', '长信科技'],
    '功率半导体': ['威孚高科'],
}

print("=== 误判股票检查 ===")
for theme, stocks in checks.items():
    found = []
    for s in themes.get(theme, []):
        if s['name'] in stocks:
            found.append((s['name'], s['via'], s['score']))
    if found:
        print(f"[{theme}] 仍存在: {found}")
    else:
        print(f"[{theme}] 已全部过滤 ✓")

# 统计
print(f"\n总股票数: {d['n_stocks']}, 总映射数: {d['n_stock_refs']}")
