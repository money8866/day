import tushare as ts
import os
from dotenv import load_dotenv
from datetime import datetime

DOTENV_PATH = os.path.join(os.path.dirname(os.path.abspath('.')), 'config', '.env')
load_dotenv(DOTENV_PATH)

TUSHARE_TOKEN = os.getenv('TUSHARE_TOKEN')
pro = ts.pro_api(TUSHARE_TOKEN)

print('测试市值接口...')
current_date = datetime.now().strftime('%Y%m%d')
print(f'当前日期: {current_date}')

# 方法1: 使用pro.stock_basic获取基本信息
print('\n=== 方法1: stock_basic ===')
try:
    df = pro.stock_basic(
        ts_code='300308.SZ',
        fields='ts_code,name,area,industry,market,list_date'
    )
    print('基本信息:')
    print(df)
except Exception as e:
    print(f'stock_basic失败: {e}')

# 方法2: 尝试不同的市值接口
print('\n=== 方法2: daily_basic with recent date ===')
try:
    # 使用近期的几个日期尝试
    test_dates = ['20260528', '20260527', '20260526', '20260523']
    for test_date in test_dates:
        df = pro.daily_basic(
            ts_code='300308.SZ',
            trade_date=test_date,
            fields='ts_code,trade_date,total_mv,circ_mv'
        )
        if df is not None and len(df) > 0:
            print(f'{test_date} 市值数据:')
            print(df)
            market_cap_yi = df.iloc[0]['total_mv'] / 10000
            print(f'市值（亿元）: {market_cap_yi:.2f}')
            break
        else:
            print(f'{test_date}: 无数据')
except Exception as e:
    print(f'daily_basic失败: {e}')

# 方法3: 使用股票列表获取市值
print('\n=== 方法3: daily_basic 批量获取 ===')
try:
    df = pro.daily_basic(
        ts_code='300308.SZ',
        fields='ts_code,trade_date,total_mv,circ_mv'
    )
    print(f'批量获取结果:')
    print(f'数据条数: {len(df) if df is not None else 0}')
    if df is not None and len(df) > 0:
        print(df.head())
except Exception as e:
    print(f'批量获取失败: {e}')
