import sys
sys.path.insert(0, '.')
import tushare as ts
import json

# 读取主题配置
with open('theme.json', 'r', encoding='utf-8') as f:
    themes = json.load(f)

# 获取物理AI主题的core_companies
phys_ai_core = themes.get('物理AI', {}).get('core_companies', [])
print('物理AI主题的core_companies:', phys_ai_core)
print('太辰光是否在core_companies中:', '太辰光' in phys_ai_core)

# 获取太辰光的股票名称
pro = ts.pro_api()
df = pro.stock_basic(ts_code='300570.SZ')
if not df.empty:
    stock_name = df.iloc[0]['name']
    print(f'太辰光的股票名称: {stock_name}')
    print(f'"太辰光" in "{stock_name}": {"太辰光" in stock_name}')