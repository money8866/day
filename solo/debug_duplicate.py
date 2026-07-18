"""检查exclude_keywords和多主题去重"""
import sys
sys.path.insert(0, '.')
import theme_trend_sentiment_score as theme_ts

themes = theme_ts.load_theme_json()
dc_df = theme_ts.get_dc_members()
stock_basic = theme_ts.get_stock_basic()

print("=== 检查银行主题配置 ===")
bank_theme = themes.get('银行', {})
print(f"银行主题 exclude_keywords: {bank_theme.get('exclude_keywords', [])}")

print("\n=== 检查其他主题是否有 '银行' 在exclude_keywords中 ===")
for theme_name, cfg in themes.items():
    ex_keys = cfg.get('exclude_keywords', [])
    if '银行' in ex_keys:
        print(f"  {theme_name}: exclude_keywords = {ex_keys}")

print("\n=== 检查券商主题配置 ===")
broker_theme = themes.get('券商', {})
print(f"券商主题 exclude_keywords: {broker_theme.get('exclude_keywords', [])}")
print(f"券商主题 industry_list: {broker_theme.get('industry', [])}")

# 检查多主题去重逻辑
print("\n=== 检查多主题去重逻辑 ===")
result = theme_ts.match_theme_stocks(themes, dc_df, stock_basic)
if isinstance(result, tuple):
    matched_dict = result[0]
else:
    matched_dict = result

target_code = "601825.SH"
target_name = stock_basic.loc[stock_basic['ts_code'] == target_code, 'name'].values[0]

print(f"\n{target_code} {target_name} 的匹配情况:")
matched_themes = []
for theme_name, stocks in matched_dict.items():
    if isinstance(stocks, dict):
        if target_code in stocks:
            matched_themes.append(theme_name)
    elif isinstance(stocks, list):
        if target_code in stocks:
            matched_themes.append(theme_name)

if matched_themes:
    print(f"  ✓ 匹配到主题: {matched_themes}")
else:
    print(f"  ✗ 未匹配任何主题")

# 检查是否在券商主题中
print(f"\n检查是否在券商主题中:")
broker_stocks = matched_dict.get('券商', {})
if isinstance(broker_stocks, dict):
    if target_code in broker_stocks:
        print(f"  ✓ 在券商主题中")
        print(f"  详情: {broker_stocks[target_code]}")
    else:
        print(f"  ✗ 不在券商主题中")