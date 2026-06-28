"""测试烽火通信趋势评分"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

import pandas as pd
from data_fetcher import DataFetcher
from trend_picker import trend_scan, to_dataframe

# 配置
token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
config = {'cache': {'dir': 'cache'}, 'tushare': {'token': token}}

fetcher = DataFetcher(token, config)

# 测试兆易创新（6月17日）
test_stocks = pd.DataFrame([
    {'ts_code': '603986.SH', 'name': '兆易创新', 'industry': '半导体'},
])

end_date = '20260617'
start_date = '20260301'

print('=== 烽火通信趋势选股测试 ===')
print(f'日期: {start_date} ~ {end_date}\n')

results = trend_scan(fetcher, test_stocks, start_date, end_date)

if results:
    r = results[0]
    print(f'股票: {r.name} ({r.ts_code})')
    print(f'行业: {r.industry}')
    print(f'总分: {r.total_score:.1f}/18 (标准化: {r.normalized_score:.1f}/100)')
    print(f'趋势强度: {r.trend_status}')
    print(f'买点信号: {r.buy_signal if r.buy_signal else "无"}')
    print(f'止损价: {r.stop_loss_price if r.stop_loss_price > 0 else "未设置"}')
    print(f'\n分项得分:')
    print(f'  基本面: {r.fundamental_score:.1f}分')
    print(f'  资金面: {r.capital_score:.1f}分')
    print(f'  技术面: {r.technical_score:.1f}分')
    print(f'\n因子详情:')
    for code, f in r.factors.items():
        if f.raw_score > 0:
            print(f'  {code} {f.name}: {f.raw_score:.1f}分')
else:
    print('未获取到结果')
