import sys
sys.path.insert(0, '.')
from theme_portfolio_strategy_cached_dc import ThemeTrendSentimentScore, DataCenter

# 初始化
theme_score = ThemeTrendSentimentScore()
dc = DataCenter()

# 获取热门主题和股票数据
hot_themes = theme_score.get_hot_themes()
dc_df = dc.get_all_component_stocks()
stock_basic = dc.get_stock_basic()

# 匹配主题股票
theme_stock_map, _, _, _ = theme_score.match_theme_stocks(hot_themes, dc_df, stock_basic)

# 检查物理AI主题
if '物理AI' in theme_stock_map:
    phys_ai_stocks = theme_stock_map['物理AI']
    print(f'物理AI主题共 {len(phys_ai_stocks)} 只股票')
    is_in = '300570.SZ' in phys_ai_stocks
    print(f'太辰光(300570.SZ)是否在列: {is_in}')