"""调试为什么银行股没匹配到银行主题"""
import sys
sys.path.insert(0, '.')
import theme_trend_sentiment_score as theme_ts
import json
import pandas as pd

# 加载theme
themes = theme_ts.load_theme_json()
dc_df = theme_ts.get_dc_members()
stock_basic = theme_ts.get_stock_basic()

# 只跑银行主题
bank_theme = {'银行': themes['银行']}
result = theme_ts.match_theme_stocks(bank_theme, dc_df, stock_basic)
matched_dict = result[0] if isinstance(result, tuple) else result

print('=== 银行主题匹配结果 ===')
bank_stocks = matched_dict.get('银行', {})
print(f'匹配股票数: {len(bank_stocks)}')
for code, info in list(bank_stocks.items())[:10]:
    print(f'  {code}: {info}')

# 检查银行主题配置
print('\n=== 银行主题配置 ===')
print(json.dumps(themes['银行'], ensure_ascii=False, indent=2))

# 检查券商主题
broker_theme = {'券商': themes['券商']}
result2 = theme_ts.match_theme_stocks(broker_theme, dc_df, stock_basic)
matched_dict2 = result2[0] if isinstance(result2, tuple) else result2
print('\n=== 券商主题匹配结果 ===')
broker_stocks = matched_dict2.get('券商', {})
print(f'匹配股票数: {len(broker_stocks)}')
for code, info in list(broker_stocks.items())[:10]:
    print(f'  {code}: {info}')

# 检查电力设备主题
power_theme = {'电力设备': themes['电力设备']}
result3 = theme_ts.match_theme_stocks(power_theme, dc_df, stock_basic)
matched_dict3 = result3[0] if isinstance(result3, tuple) else result3
print('\n=== 电力设备主题匹配结果 ===')
power_stocks = matched_dict3.get('电力设备', {})
print(f'匹配股票数: {len(power_stocks)}')
print('\n=== 电力设备主题配置 ===')
print(json.dumps(themes['电力设备'], ensure_ascii=False, indent=2))
