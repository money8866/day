import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from multi_factor_picker.data_fetcher import DataFetcher

with open('.env') as f:
    token = [line.split('=',1)[1].strip().strip("'\"") for line in f if line.startswith('TUSHARE_TOKEN')][0]

config = {'cache': {'enabled': True, 'dir': 'cache'}, 'tushare': {'max_retry': 3, 'retry_delay': 5}}
fetcher = DataFetcher(token, config)

basic = fetcher.get_stock_list()
r = basic[basic['ts_code'] == '002440.SZ']
print('股票信息:')
print(r[['ts_code', 'name', 'industry', 'list_date']].to_string())

daily = fetcher.get_daily_history('20260717', 120)
stock_df = daily[daily['ts_code'] == '002440.SZ']
print(f'\n日线数据: {len(stock_df)}条')

from daily_timing import score_stock, load_timing_params
params = load_timing_params()
result = score_stock('002440.SZ', stock_df, params)
print('\n择时分数:')
print(f'  综合分: {result["composite_score"]}')
print(f'  趋势分: {result["trend_score"]}')
print(f'  低吸分: {result["dip_score"]}')
print(f'  突破分: {result["breakout_score"]}')
print(f'  信号类型: {result["signal_type"]}')
print(f'  信号等级: {result["signal_level"]}')
print(f'  信号因子: {result["signals"]}')
print(f'  操作建议: {result["suggestion"]}')

# 检查主题匹配
import json
theme_map_path = 'D:/mystock/cache_daily/theme_stock_map_latest.json'
if os.path.exists(theme_map_path):
    with open(theme_map_path, encoding='utf-8') as f:
        theme_map = json.load(f)
    matched_themes = []
    for theme, stocks in theme_map.items():
        if '002440.SZ' in stocks or '002440' in stocks:
            matched_themes.append(theme)
    print(f'\n匹配主题: {matched_themes if matched_themes else "未匹配到任何主题"}')
else:
    print('\n主题映射文件不存在')
