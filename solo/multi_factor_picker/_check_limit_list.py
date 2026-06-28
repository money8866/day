"""检查6月11日涨停股"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')

from data_fetcher import DataFetcher
import pandas as pd

token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
fetcher = DataFetcher(token, {'cache': {'dir': 'cache'}})

# 查询涨停股列表
limit_list = fetcher.pro.limit_list_d(trade_date='20260611', limit_type='U')
print(f'20260611涨停股总数: {len(limit_list)}')

# 检查烽火通信是否在涨停名单中
if '600498.SH' in limit_list['ts_code'].values:
    print(f'✓ 烽火通信在涨停名单中')
else:
    print(f'✗ 烽火通信不在涨停名单中（实际涨幅9.5%，接近涨停）')

# 显示部分涨停股
print(f'\n涨停股示例：')
print(limit_list.head(10)[['ts_code', 'name', 'close', 'pct_chg', 'limit_times']])
