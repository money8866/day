"""检查未匹配股票的IRS评分"""
import sys
sys.path.insert(0, '.')
import theme_trend_sentiment_score as theme_ts
from collections import defaultdict

themes = theme_ts.load_theme_json()
dc_df = theme_ts.get_dc_members()
stock_basic = theme_ts.get_stock_basic()

# 拆分数据
stock_concepts = defaultdict(list)
stock_dc_industries = defaultdict(list)
for _, r in dc_df.iterrows():
    con_code = r["con_code"]
    board_name = r["concept_name"]
    if con_code and board_name:
        is_industry = r.get("is_industry", False)
        if is_industry:
            stock_dc_industries[con_code].append(board_name)
        else:
            stock_concepts[con_code].append(board_name)

name_map_basic = {}
for _, row in stock_basic.iterrows():
    name_map_basic[row["ts_code"]] = row.get("name", "")

targets = [
    ("600221.SH", "海航控股", "航空运输"),
    ("600895.SH", "张江高科", "基建地产链"),
    ("000893.SZ", "亚钾国际", "钾肥磷化工"),
    ("600901.SH", "江苏金租", "多元金融"),
    ("600177.SH", "雅戈尔", "纺织服饰"),
    ("600578.SH", "京能电力", "红利公用事业"),
]

for code, name, theme_name in targets:
    print(f"\n{'='*60}")
    print(f"{code} {name} -> {theme_name}")
    print(f"行业: {stock_dc_industries.get(code, [])}")
    print(f"概念: {stock_concepts.get(code, [])[:10]}")
    
    theme = themes.get(theme_name, {})
    industry_list = theme.get("industry", [])
    concept_list = theme.get("concept", [])
    keyword_list = theme.get("keywords", [])
    
    # 检查行业匹配
    ind_match = False
    for ind in stock_dc_industries.get(code, []):
        if theme_ts._in_industry_list(ind, industry_list):
            ind_match = True
            break
    print(f"行业匹配: {ind_match}")
    
    # 检查概念重叠
    concept_overlap = 0
    for cc in stock_concepts.get(code, []):
        for tc in concept_list:
            if tc == cc or tc in cc:
                concept_overlap += 1
                break
    print(f"概念重叠: {concept_overlap}")
    
    # 检查关键词匹配
    kw_match = False
    for kw in keyword_list:
        if kw in name:
            kw_match = True
            break
    if not kw_match:
        for kw in keyword_list:
            for c in stock_concepts.get(code, []):
                if kw in c:
                    kw_match = True
                    break
            if kw_match:
                break
    print(f"关键词匹配: {kw_match}")