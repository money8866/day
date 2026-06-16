import tushare as ts
import sys
sys.path.insert(0, '.')
from main import load_config, get_token

config = load_config()
token = get_token(config)
pro = ts.pro_api(token=token)

for d in ['20260613','20260616','20260611']:
    df = pro.trade_cal(exchange='SSE', start_date=d, end_date=d)
    if len(df) > 0:
        print(f'{d}: {df.iloc[0].to_dict()}')
    else:
        print(f'{d}: 空')

# 尝试另一种调用
print()
df2 = pro.trade_cal(exchange='SSE', start_date='20260610', end_date='20260616')
print(f'周数据:\n{df2}')
