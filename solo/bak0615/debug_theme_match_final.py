import sys
sys.path.insert(0, '.')
import json
import tushare as ts
import pandas as pd

# 读取主题配置
with open('theme.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
hot_themes = data.get('HOT_THEMES', data)

# 获取物理AI主题
phys_ai = hot_themes.get('物理AI', {})

# 获取股票基本信息
pro = ts.pro_api()
stock_basic = pro.stock_basic()

# 创建名称映射
name_map_basic = {}
for _, row in stock_basic.iterrows():
    name_map_basic[row["ts_code"]] = row.get("name", "")

# 检查太辰光
taichenguang_code = '300570.SZ'
taichenguang_name = name_map_basic.get(taichenguang_code, '')
print(f'太辰光信息:')
print(f'  代码: {taichenguang_code}')
print(f'  名称: {taichenguang_name}')

# 检查核心公司匹配
core_companies = phys_ai.get('core_companies', [])
matched_core = any(company in taichenguang_name for company in core_companies)
print(f'\n核心公司匹配:')
print(f'  物理AI核心公司: {core_companies}')
print(f'  太辰光是否匹配核心公司: {matched_core}')

# 获取东财概念数据
dc_df = pro.concept_detail()

# 检查太辰光的东财概念
tcg_concepts = dc_df[dc_df['con_code'] == taichenguang_code]
print(f'\n太辰光的东财概念:')
if not tcg_concepts.empty:
    for _, row in tcg_concepts.iterrows():
        print(f'  - {row["concept_name"]}')
else:
    print('  无东财概念数据')

# 检查行业匹配
tcg_industry = stock_basic[stock_basic['ts_code'] == taichenguang_code].iloc[0]['industry']
industry_list = phys_ai.get('industry', [])
industry_match = tcg_industry in industry_list or any(v in tcg_industry for v in industry_list)
print(f'\n行业匹配:')
print(f'  太辰光行业: {tcg_industry}')
print(f'  物理AI行业列表: {industry_list}')
print(f'  是否匹配行业: {industry_match}')

# 检查排除关键词
exclude_keywords = phys_ai.get('exclude_keywords', [])
excluded = False
for ek in exclude_keywords:
    if ek in taichenguang_name:
        excluded = True
        break
print(f'\n排除关键词检查:')
print(f'  排除关键词: {exclude_keywords}')
print(f'  是否被排除: {excluded}')