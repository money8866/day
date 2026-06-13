import sys
sys.path.insert(0, '.')
import json
import tushare as ts
import pandas as pd

# 导入主题匹配函数
from theme_trend_sentiment_score_gpt import match_theme_stocks

# 读取主题配置
with open('theme.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
hot_themes = data.get('HOT_THEMES', data)

# 获取股票基本信息
pro = ts.pro_api()
stock_basic = pro.stock_basic()

# 获取东财概念数据（用于主题匹配）
try:
    dc_df = pro.concept_detail()
except:
    dc_df = pd.DataFrame()  # 如果接口不可用，使用空DataFrame

# 匹配主题股票
theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts = match_theme_stocks(hot_themes, dc_df, stock_basic)

# 检查物理AI主题的成分股
if '物理AI' in theme_stock_map:
    phys_ai_stocks = theme_stock_map['物理AI']
    print(f'物理AI主题共 {len(phys_ai_stocks)} 只成分股')
    print('=' * 60)
    
    # 打印所有成分股
    for code, info in phys_ai_stocks.items():
        name = name_map_basic.get(code, '未知')
        print(f'{code} - {name} (匹配方式: {info["via"]})')
    
    # 检查太辰光是否在其中
    taichenguang_code = '300570.SZ'
    if taichenguang_code in phys_ai_stocks:
        print(f'\n太辰光(300570.SZ) 在物理AI成分股中！匹配方式: {phys_ai_stocks[taichenguang_code]["via"]}')
    else:
        print(f'\n太辰光(300570.SZ) 不在物理AI成分股中！')
        # 检查太辰光的名称
        print(f'太辰光名称: {name_map_basic.get(taichenguang_code, "未知")}')
        # 检查核心公司列表
        core_companies = hot_themes['物理AI'].get('core_companies', [])
        print(f'物理AI核心公司: {core_companies}')
else:
    print('物理AI主题不存在')