import sys
sys.path.insert(0, r'c:\Users\kongx\mystock\solo')
from analyze_backbone import parse_tdx_day_file
import pandas as pd

filepath = r'C:\new_tdx\vipdoc\sh\lday\sh600226.day'
data = parse_tdx_day_file(filepath)
if data:
    print(f'总记录数: {len(data)}')
    print('最后5条记录:')
    df = pd.DataFrame(data[-5:])
    print(df[['date', 'close', 'vol', 'amount']])
    print(f'\n最新日期: {data[-1]["date"]}')
else:
    print('没有数据')
