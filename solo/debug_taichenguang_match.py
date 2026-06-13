import sys
sys.path.insert(0, '.')
import json
import tushare as ts

# 读取主题配置
with open('theme.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
hot_themes = data.get('HOT_THEMES', data)

# 获取物理AI主题配置
phys_ai = hot_themes.get('物理AI', {})
core_companies = phys_ai.get('core_companies', [])
print('物理AI核心公司:', core_companies)

# 获取股票基本信息
pro = ts.pro_api()
stock_basic = pro.stock_basic()

# 检查太辰光的信息
taichenguang = stock_basic[stock_basic['ts_code'] == '300570.SZ']
if not taichenguang.empty:
    name = taichenguang.iloc[0]['name']
    industry = taichenguang.iloc[0]['industry']
    print(f'太辰光信息:')
    print(f'  股票代码: 300570.SZ')
    print(f'  股票名称: {name}')
    print(f'  行业: {industry}')
    
    # 检查核心公司匹配
    matched_core = any(company in name for company in core_companies)
    print(f'  是否匹配核心公司: {matched_core}')
    
    # 检查行业匹配
    industry_list = phys_ai.get('industry', [])
    print(f'  物理AI行业列表: {industry_list}')
    industry_match = industry in industry_list or any(v in industry for v in industry_list)
    print(f'  是否匹配行业: {industry_match}')
else:
    print('未找到太辰光')

# 检查太辰光的概念
concepts = pro.concept_detail(ts_code='300570.SZ')
print(f'\n太辰光概念板块:')
if not concepts.empty:
    for _, row in concepts.iterrows():
        print(f'  - {row["concept_name"]}')
else:
    print('  无概念数据')