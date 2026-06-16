import json
from collections import defaultdict

# 读取主题成分股数据
with open('d:/mystock/solo/cache_backbone_tushare/theme3_constituents_20260612.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

all_themes = data['themes']

# 模拟 V11 的 primary_map 构建逻辑
categories = list(set(t.get("top_category", "其他") for t in all_themes))
primary_scores = {}

# 第一步：计算每个一级主题的去重成交额
for cat in categories:
    themes_in_cat = [t for t in all_themes if t.get("top_category") == cat]
    seen = set()
    amt = 0
    for t in themes_in_cat:
        for s in t.get("stocks", []):
            code = s.get("ts_code", "")
            if code not in seen:
                seen.add(code)
                amt += (s.get("avg_amount_5d") or 0) / 1e8
    primary_scores[cat] = amt

# 第二步：为每只股票分配主导主题
stock_cats = defaultdict(list)
for t in all_themes:
    cat = t.get("top_category", "其他")
    for s in t.get("stocks", []):
        code = s.get("ts_code", "")
        if code and cat not in stock_cats[code]:
            stock_cats[code].append(cat)

primary_map = {}
for code, cats in stock_cats.items():
    if len(cats) == 1:
        primary_map[code] = cats[0]
    else:
        best = max(cats, key=lambda c: primary_scores.get(c, 0))
        primary_map[code] = best

# 筛选金融主题
fin_themes = [t for t in data['themes'] if t.get('top_category') == '金融']
print(f"金融一级主题下的子主题:")
for t in fin_themes:
    print(f"  - {t['theme_name']}: {len(t['stocks'])}只股票")

# 收集所有金融股票和仅属于金融的股票
all_fin_stocks = []
only_fin_stocks = []
for t in fin_themes:
    for s in t['stocks']:
        all_fin_stocks.append(s)
        code = s.get('ts_code')
        if primary_map.get(code) == '金融':
            only_fin_stocks.append(s)

print(f"\n金融板块共 {len(all_fin_stocks)} 只股票")
print(f"被 primary_map 分配到金融的股票: {len(only_fin_stocks)} 只")

print("\n【全部金融股票】中期数据统计:")
change_20d_list = [s.get('change_20d_pct', 0) for s in all_fin_stocks]
change_60d_list = [s.get('change_60d_pct', 0) for s in all_fin_stocks]
ma20_slope_list = [s.get('ma20_slope_pct', 0) for s in all_fin_stocks]
ma60_slope_list = [s.get('ma60_slope_pct', 0) for s in all_fin_stocks]
above_ma20_count = sum(1 for s in all_fin_stocks if s.get('close_above_ma20'))
above_ma60_count = sum(1 for s in all_fin_stocks if s.get('close_above_ma60'))

print(f"  20日涨幅均值: {sum(change_20d_list)/len(change_20d_list):.2f}%")
print(f"  60日涨幅均值: {sum(change_60d_list)/len(change_60d_list):.2f}%")
print(f"  MA20斜率均值: {sum(ma20_slope_list)/len(ma20_slope_list):.2f}")
print(f"  MA60斜率均值: {sum(ma60_slope_list)/len(ma60_slope_list):.2f}")
print(f"  站上MA20比例: {above_ma20_count/len(all_fin_stocks)*100:.1f}%")
print(f"  站上MA60比例: {above_ma60_count/len(all_fin_stocks)*100:.1f}%")

print("\n【仅分配到金融的股票】中期数据统计 (V11使用的样本):")
change_20d_list2 = [s.get('change_20d_pct', 0) for s in only_fin_stocks]
change_60d_list2 = [s.get('change_60d_pct', 0) for s in only_fin_stocks]
ma20_slope_list2 = [s.get('ma20_slope_pct', 0) for s in only_fin_stocks]
ma60_slope_list2 = [s.get('ma60_slope_pct', 0) for s in only_fin_stocks]
above_ma20_count2 = sum(1 for s in only_fin_stocks if s.get('close_above_ma20'))
above_ma60_count2 = sum(1 for s in only_fin_stocks if s.get('close_above_ma60'))

print(f"  20日涨幅均值: {sum(change_20d_list2)/len(change_20d_list2):.2f}%")
print(f"  60日涨幅均值: {sum(change_60d_list2)/len(change_60d_list2):.2f}%")
print(f"  MA20斜率均值: {sum(ma20_slope_list2)/len(ma20_slope_list2):.2f}")
print(f"  MA60斜率均值: {sum(ma60_slope_list2)/len(ma60_slope_list2):.2f}")
print(f"  站上MA20比例: {above_ma20_count2/len(only_fin_stocks)*100:.1f}%")
print(f"  站上MA60比例: {above_ma60_count2/len(only_fin_stocks)*100:.1f}%")

print("\n【V11输出值】(对比参考):")
print("  20日涨幅均值: -2.3%")
print("  60日涨幅均值: -7.4%")
print("  MA20斜率均值: -1.32")
print("  MA60斜率均值: -0.61")
print("  站上MA20比例: 52.7%")
print("  站上MA60比例: 34.8%")