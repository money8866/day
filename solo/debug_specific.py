"""排查特定股票未匹配原因"""
import sys
sys.path.insert(0, '.')
import theme_trend_sentiment_score as theme_ts
import pandas as pd

themes = theme_ts.load_theme_json()
dc_df = theme_ts.get_dc_members()
stock_basic = theme_ts.get_stock_basic()

targets = ["001872.SZ", "601825.SH", "601077.SH"]

print("=== 排查股票行业信息 ===")
stock_industries = {}
stock_concepts = {}
for _, row in dc_df.iterrows():
    code = row['con_code']
    if row['is_industry']:
        if code not in stock_industries:
            stock_industries[code] = []
        stock_industries[code].append(row['concept_name'])
    else:
        if code not in stock_concepts:
            stock_concepts[code] = []
        stock_concepts[code].append(row['concept_name'])

for code in targets:
    name = stock_basic.loc[stock_basic['ts_code'] == code, 'name'].values[0] if len(stock_basic[stock_basic['ts_code'] == code]) > 0 else 'unknown'
    print(f"\n{code} {name}")
    print(f"  东财行业: {stock_industries.get(code, [])}")
    print(f"  东财概念: {stock_concepts.get(code, [])[:10]}")

print("\n=== 检查银行主题配置 ===")
bank_theme = themes.get('银行', {})
print(f"银行主题industry: {bank_theme.get('industry', [])}")
print(f"银行主题concept: {bank_theme.get('concept', [])}")

print("\n=== 检查交通运输物流主题配置 ===")
transport_theme = themes.get('交通运输物流', {})
print(f"交通运输物流industry: {transport_theme.get('industry', [])}")
print(f"交通运输物流concept: {transport_theme.get('concept', [])}")

print("\n=== 检查strip_ii函数 ===")
for ind in ['农商行Ⅲ', '银行Ⅱ', '港口', '航运港口']:
    stripped = theme_ts._strip_ii(ind)
    print(f"  _strip_ii('{ind}') = '{stripped}'")

print("\n=== 检查in_industry_list函数 ===")
for test_ind in ['农商行Ⅲ', '港口']:
    for theme_name in ['银行', '交通运输物流']:
        industry_list = themes.get(theme_name, {}).get('industry', [])
        result = theme_ts._in_industry_list(test_ind, industry_list)
        print(f"  _in_industry_list('{test_ind}', {theme_name}) = {result}")