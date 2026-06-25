# -*- coding: utf-8 -*-
"""查询光电股份的所有主题是否在keep_themes中"""
import json

# 加载主题映射
data = json.load(open(r'd:\mystock\cache_daily\theme_stock_map_latest.json', 'r', encoding='utf-8'))
theme_stock_map = {}
for theme_name, stock_list in data.get("themes", {}).items():
    matched = {}
    for s in stock_list:
        matched[s["code"]] = s.get("score", 0)
    theme_stock_map[theme_name] = matched

# 查所有主题
print(f"全部主题数: {len(theme_stock_map)}")
print(f"全部主题列表: {sorted(theme_stock_map.keys())}")

# 光电股份的所有主题
ts_code = '600184.SH'
gd_hits = []
for theme_name, stocks in theme_stock_map.items():
    if ts_code in stocks:
        score = stocks[ts_code] if isinstance(stocks[ts_code], (int, float)) else stocks[ts_code].get('score', 0)
        gd_hits.append((theme_name, score))
gd_hits.sort(key=lambda x: -x[1])
print(f"\n光电股份的全部主题匹配(score降序):")
for n, s in gd_hits:
    print(f"  {n} | score={s}")

# 看军工、物理AI主题的所有股票数
for theme_name in ['军工', '物理AI', '人形机器人', '工业母机']:
    if theme_name in theme_stock_map:
        cnt = len(theme_stock_map[theme_name])
        print(f"\n{theme_name}: 包含 {cnt} 只股票")
