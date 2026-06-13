import sys
sys.path.insert(0, '.')
from theme_portfolio_strategy_cached_dc import ThemePortfolioStrategy

tps = ThemePortfolioStrategy()
themes = tps.get_all_themes()

for theme in themes:
    components = tps.get_theme_components(theme['name'])
    if components:
        codes = [c['code'] for c in components]
        if '300570.SZ' in codes:
            print(f'太辰光属于主题: {theme["name"]}')
            break
else:
    print('太辰光不在任何主题的成分股列表中')