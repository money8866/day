"""手动模拟IRS评分计算"""
import sys
sys.path.insert(0, '.')
import theme_trend_sentiment_score as theme_ts
from collections import defaultdict

themes = theme_ts.load_theme_json()
dc_df = theme_ts.get_dc_members()
stock_basic = theme_ts.get_stock_basic()

target_code = "601825.SH"  # 沪农商行

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

# 获取股票信息
stock_name = name_map_basic.get(target_code, "")
concepts = stock_concepts.get(target_code, [])
industries = stock_dc_industries.get(target_code, [])

# 获取银行主题配置
theme = themes.get('银行', {})
concept_list = theme.get("concept", [])
keyword_list = theme.get("keywords", [])
industry_list = theme.get("industry", [])
exclude_keywords = theme.get("exclude_keywords", [])
core_companies = theme.get("core_companies", [])
leader_companies = theme.get("leader_companies", [])

print(f"股票: {target_code} {stock_name}")
print(f"行业: {industries}")
print(f"概念: {concepts[:15]}")
print(f"\n银行主题:")
print(f"  concept_list: {concept_list}")
print(f"  keyword_list: {keyword_list[:10]}")
print(f"  industry_list: {industry_list}")

# === 模拟IRS计算 ===
print("\n=== IRS评分模拟 ===")

# 维度1: 主营匹配
mb_score = 0
print(f"维度1-主营匹配: {mb_score}")

# 维度2: 产业链距离
chain_score = 0
concept_overlap = 0
for cc in concepts:
    for tc in concept_list:
        if tc == cc or tc in cc:
            concept_overlap += 1
            break
print(f"概念重叠数: {concept_overlap}")

info = {"industry_match": True, "source": "dc_industry"}
if concept_overlap >= 3:
    chain_score = 25
elif concept_overlap >= 2:
    chain_score = 20
elif concept_overlap >= 1:
    if info.get("industry_match"):
        chain_score = 25
    else:
        chain_score = 15
elif info.get("industry_match"):
    # 纯行业匹配
    _GENERIC_INDS = ('半导体', '电子', '自动化设备', '专用设备', '通用设备',
                     '计算机设备', '通信设备', '消费电子', '电子元器件', '计算机',
                     '机械设备', '游戏', '游戏Ⅱ', '游戏Ⅲ', '传媒',
                     '电气设备', '电力设备', '电子元件', '元件',
                     '医药生物', '化学制药', '化学原料', '化学制品',
                     '汽车', '汽车零部件', '有色金属', '工业金属',
                     '基础化工', '机械设备', '通用设备', '专用设备')
    has_precise_ind = False
    for ind in industries:
        if theme_ts._in_industry_list(ind, industry_list):
            stripped_ind = theme_ts._strip_ii(ind)
            if stripped_ind not in _GENERIC_INDS and stripped_ind not in [theme_ts._strip_ii(x) for x in _GENERIC_INDS]:
                has_precise_ind = True
                break
    chain_score = 20 if has_precise_ind else 15
print(f"维度2-产业链距离: {chain_score}")

# 维度3: 关键词匹配
kw_score = 0
for kw in keyword_list:
    if kw in stock_name:
        kw_score += 10
        break
if kw_score == 0:
    for kw in keyword_list:
        for c in concepts:
            if kw in c:
                kw_score += 6
                break
        if kw_score > 0:
            break
print(f"维度3-关键词匹配: {kw_score}")

# 维度4: 行业板块
source = info.get("source", "")
ind_score = 0
if source in ("dc_industry_board", "dc_industry"):
    best_ind_score = 7
    for ind in industries:
        if theme_ts._in_industry_list(ind, industry_list):
            stripped_ind = theme_ts._strip_ii(ind)
            if stripped_ind not in ('半导体', '电子', '自动化设备', '专用设备', '通用设备',
                                    '计算机设备', '通信设备', '消费电子', '电子元器件', '计算机',
                                    '机械设备', '游戏', '游戏Ⅱ', '游戏Ⅲ', '传媒',
                                    '电气设备', '电力设备', '电子元件', '元件',
                                    '医药生物', '化学制药', '化学原料', '化学制品',
                                    '汽车', '汽车零部件', '有色金属', '工业金属',
                                    '基础化工'):
                best_ind_score = 15
                break
            elif best_ind_score < 10:
                best_ind_score = 10
    ind_score = best_ind_score
print(f"维度4-行业板块: {ind_score}")

# exclude_keywords惩罚
if exclude_keywords:
    for ek in exclude_keywords:
        if ek in stock_name:
            chain_score = max(0, chain_score - 15)
            print(f"股票名惩罚: {ek} in {stock_name}, chain_score -= 15 -> {chain_score}")
            break
    else:
        for ek in exclude_keywords:
            for c in concepts:
                if c.startswith('参股') or c.endswith('概念') or c.endswith('龙头'):
                    continue
                if ek == c:
                    chain_score = max(0, chain_score - 5)
                    print(f"概念惩罚: {ek} in concepts, chain_score -= 5 -> {chain_score}")
                    break
            else:
                continue
            break

irs = mb_score + chain_score + kw_score + ind_score
print(f"\n总分IRS: {irs}")
if irs >= 85:
    layer = 'core'
elif irs >= 60:
    layer = 'extended'
elif irs >= 40:
    layer = 'associated'
else:
    layer = 'excluded'
print(f"分层: {layer}")