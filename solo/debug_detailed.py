"""详细调试股票匹配过程"""
import sys
sys.path.insert(0, '.')
import theme_trend_sentiment_score as theme_ts
from collections import defaultdict

themes = theme_ts.load_theme_json()
dc_df = theme_ts.get_dc_members()
stock_basic = theme_ts.get_stock_basic()

target_code = "601825.SH"  # 沪农商行

# 拆分东财数据
stock_concepts = defaultdict(list)
stock_dc_industries = defaultdict(list)
dc_concept_board_members = defaultdict(set)
dc_industry_board_members = defaultdict(set)

for _, r in dc_df.iterrows():
    con_code = r["con_code"]
    board_name = r["concept_name"]
    if con_code and board_name:
        is_industry = r.get("is_industry", False)
        if is_industry:
            stock_dc_industries[con_code].append(board_name)
            dc_industry_board_members[board_name].add(con_code)
        else:
            stock_concepts[con_code].append(board_name)
            dc_concept_board_members[board_name].add(con_code)

# stock_basic_industry
stock_basic_industry = {}
name_map_basic = {}
for _, row in stock_basic.iterrows():
    stock_basic_industry[row["ts_code"]] = row.get("industry", "")
    name_map_basic[row["ts_code"]] = row.get("name", "")

print(f"股票: {target_code} {name_map_basic.get(target_code, '')}")
print(f"东财行业: {stock_dc_industries.get(target_code, [])}")
print(f"东财概念: {stock_concepts.get(target_code, [])[:10]}")

# 检查银行主题
theme = themes.get('银行', {})
industry_list = theme.get("industry", [])
concept_list = theme.get("concept", [])
keyword_list = theme.get("keywords", [])
exclude_keywords = theme.get("exclude_keywords", [])
core_companies = theme.get("core_companies", [])
leader_companies = theme.get("leader_companies", [])
dna_concept_required = theme.get("dna_concept_required", [])

print(f"\n银行主题配置:")
print(f"  industry_list: {industry_list}")
print(f"  concept_list: {concept_list}")
print(f"  dna_concept_required: {dna_concept_required}")

# 模拟Phase 1的各种方式
print("\n=== Phase 1: Industry Gate ===")

# 方式A
print(f"\n[方式A] 检查 industry_list 中的名称是否直接匹配东财行业板块")
for ind_name in industry_list:
    if ind_name in dc_industry_board_members:
        if target_code in dc_industry_board_members[ind_name]:
            print(f"  ✓ {ind_name}: 匹配")
        else:
            print(f"  ✗ {ind_name}: 不匹配")

# 方式B
print(f"\n[方式B] 检查股票行业是否匹配 theme industry_list")
for ind in stock_dc_industries.get(target_code, []):
    result = theme_ts._in_industry_list(ind, industry_list)
    print(f"  {ind}: _in_industry_list = {result}")

# 方式C
print(f"\n[方式C] stock_basic 行业匹配")
basic_ind = stock_basic_industry.get(target_code, "")
print(f"  stock_basic行业: {basic_ind}")
if basic_ind:
    result = theme_ts._in_industry_list(basic_ind, industry_list)
    print(f"  _in_industry_list = {result}")

# 如果进入候选池，检查DNA Gate
if dna_concept_required:
    print(f"\n=== DNA Gate ===")
    print(f"  dna_concept_required: {dna_concept_required}")
    dna_match = False
    for dc in dna_concept_required:
        if dc in stock_concepts.get(target_code, []):
            dna_match = True
            print(f"  ✓ 概念匹配: {dc}")
            break
        for ind in stock_dc_industries.get(target_code, []):
            if dc in ind or ind in dc:
                dna_match = True
                print(f"  ✓ 行业匹配: {ind} 包含 {dc}")
                break
        if dna_match:
            break
    print(f"  DNA匹配结果: {dna_match}")