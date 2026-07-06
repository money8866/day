"""调试：测试Tushare API连通性"""
import os
import tushare as ts

token = os.environ.get('TUSHARE_TOKEN')
if not token:
    # 从.env读取
    with open('.env') as f:
        for line in f:
            if line.startswith('TUSHARE_TOKEN='):
                token = line.strip().split('=', 1)[1]
                break

print(f'Token: {token[:8]}...{token[-4:]}')
pro = ts.pro_api(token)

# 测试ETF日线
try:
    df = pro.daily(ts_code='510050.SH', start_date='20250706', end_date='20260706', fields='ts_code,trade_date,open,high,low,close,vol')
    print(f'daily result: type={type(df).__name__}, shape={df.shape}')
    if len(df) > 0:
        print(df.head(3))
except Exception as e:
    print(f'daily error: {e}')

# 测试指数
try:
    df = pro.index_daily(ts_code='000300.SH', start_date='20250706', end_date='20260706')
    print(f'index_daily result: type={type(df).__name__}, shape={df.shape}')
    if len(df) > 0:
        print(df.head(3))
except Exception as e:
    print(f'index_daily error: {e}')
