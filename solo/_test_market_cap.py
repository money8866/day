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

# 测试获取市值
try:
    df = pro.daily_basic(
        ts_code='300308.SZ',
        trade_date=current_date,
        fields='ts_code,total_mv,circ_mv'
    )
    print(f'今日市值数据:')
    print(df)
except Exception as e:
    print(f'今日数据失败: {e}')

# 尝试获取最近交易日
try:
    cal = pro.trade_cal(
        end_date=current_date,
        is_open='1'
    )
    last_trade_date = cal.tail(1)['cal_date'].values[0]
    print(f'\n最近交易日: {last_trade_date}')
    
    df = pro.daily_basic(
        ts_code='300308.SZ',
        trade_date=last_trade_date,
        fields='ts_code,total_mv,circ_mv'
    )
    print(f'最近交易日市值数据:')
    print(df)
    
    if df is not None and len(df) > 0:
        market_cap_yi = df.iloc[0]['total_mv'] / 10000
        print(f'\n市值（亿元）: {market_cap_yi:.2f}')
except Exception as e:
    print(f'获取最近交易日市值失败: {e}')
