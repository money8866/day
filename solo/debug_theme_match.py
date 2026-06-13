import sys
sys.path.insert(0, '.')
from theme_portfolio_strategy_cached_dc import *

# 初始化主题评分和数据中心
theme_score = ThemeTrendSentimentScore()
dc = DataCenter()

# 获取热门主题
hot_themes = theme_score.get_hot_themes()

# 获取股票基础数据
dc_df = dc.get_all_component_stocks()
stock_basic = dc.get_stock_basic()

# 匹配主题股票
theme_stock_map, name_map_basic, stock_basic_industry, stock_concepts = theme_score.match_theme_stocks(hot_themes, dc_df, stock_basic)

# 检查太辰光是否在物理AI主题中
if '物理AI' in theme_stock_map:
    phys_ai_stocks = theme_stock_map['物理AI']
    print(f'物理AI主题共 {len(phys_ai_stocks)} 只股票')
    if '300570.SZ' in phys_ai_stocks:
        print('太辰光在物理AI主题中')
    else:
        print('太辰光不在物理AI主题中')
        print('物理AI主题部分股票:', list(phys_ai_stocks)[:10])
else:
    print('物理AI主题不存在')