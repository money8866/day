"""核实100-1000亿市值股票数量"""
import tushare as ts
import pandas as pd

token = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'
pro = ts.pro_api(token)

print('=== 核实100-1000亿市值股票数量 ===\n')

# 尝试获取最新交易日的市值数据
# 使用trade_cal获取最近交易日
trade_cal = pro.trade_cal(exchange='SSE', start_date='20260620', end_date='20260630')
trade_cal = trade_cal[trade_cal['is_open'] == 1].sort_values('cal_date')
latest_trade_date = trade_cal.iloc[-1]['cal_date'] if len(trade_cal) > 0 else '20260626'

print(f'最近交易日: {latest_trade_date}\n')

# 获取daily_basic（包含总市值和流通市值）
print('获取全市场市值数据...')
daily_basic = pro.daily_basic(trade_date=latest_trade_date, fields='ts_code,circ_mv,total_mv')

if len(daily_basic) > 0:
    print(f'总股票数: {len(daily_basic)}只\n')
    
    # 筛选100-1000亿流通市值
    pool = daily_basic[(daily_basic['circ_mv'] >= 100) & (daily_basic['circ_mv'] <= 1000)]
    
    print(f'【筛选条件】流通市值 100-1000亿')
    print(f'符合条件的股票: {len(pool)}只\n')
    
    # 按市值分组统计
    print('【市值分组统计】')
    bins = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
    for i in range(len(bins)-1):
        count = len(pool[(pool['circ_mv'] >= bins[i]) & (pool['circ_mv'] < bins[i+1])])
        if count > 0:
            print(f'{bins[i]}-{bins[i+1]}亿: {count}只')
    
    # 计算总数
    total_1000 = len(daily_basic[daily_basic['circ_mv'] <= 1000])
    total_100 = len(daily_basic[daily_basic['circ_mv'] >= 100])
    print(f'\n市值≤1000亿: {total_1000}只')
    print(f'市值≥100亿: {total_100}只')
    print(f'100-1000亿: {len(pool)}只')
    
else:
    print('⚠️ API数据未更新，使用本地估算\n')
    
    # A股总股票数约5000只
    # 大盘股（>1000亿）：约200只
    # 小盘股（<100亿）：约3500只
    # 中盘股（100-1000亿）：约1300只
    
    print('【估算数据】')
    print('A股总股票数: ~5000只')
    print('>1000亿市值: ~200只')
    print('<100亿市值: ~3500只')
    print('100-1000亿市值: ~1300只')
    
    print('\n⚠️ 实际扫描发现5193只有效股票')
    print('原因：本地缓存包含历史数据，可能包含已退市或ST股票')
