import sys
sys.path.insert(0, '.')

from advanced_buzhang_analysis import AdvancedBuzhangDetector
import tushare as ts
import pandas as pd
import numpy as np

# 初始化分析器
analyzer = AdvancedBuzhangDetector()

# 获取太辰光的数据
pro = ts.pro_api()
df = pro.stock_basic(ts_code='300570.SZ')
stock_name = df.iloc[0]['name'] if not df.empty else '太辰光'
print(f'分析股票: {stock_name} (300570.SZ)')

# 获取K线数据
kline_df = pro.daily(ts_code='300570.SZ', start_date='20260301', end_date='20260611')
print(f'K线数据行数: {len(kline_df)}')

if len(kline_df) >= 30:
    # 获取市值和换手率数据
    daily_basic = pro.daily_basic(ts_code='300570.SZ', trade_date='20260611')
    market_cap = daily_basic.iloc[0]['total_mv'] / 10000 if not daily_basic.empty else None  # 转换为亿
    turnover_rate = daily_basic.iloc[0]['turnover_rate'] if not daily_basic.empty else None
    
    print(f'市值: {market_cap}亿')
    print(f'换手率: {turnover_rate}%')
    
    # 计算近20日平均成交额
    amounts = kline_df['amount'].astype(float).values
    avg_20_amount = (np.mean(amounts[-21:-1]) / 10000) if len(amounts) >= 21 else (np.mean(amounts) / 10000)  # 千元转亿元
    print(f'近20日平均成交额: {avg_20_amount:.2f}亿')
    
    # 分析太辰光
    result = analyzer.analyze_stock(kline_df, market_cap=market_cap, turnover_rate=turnover_rate)
    if result:
        print('\n分析结果:')
        print(f"有效: {result.get('valid', 'N/A')}")
        print(f"原因: {result.get('reason', 'N/A')}")
        print(f"综合评分: {result.get('overall_score', 'N/A')}")
        metrics = result.get('metrics', {})
        print(f"成交额评分: {metrics.get('big_amount', 'N/A')}")
        print(f"换手率评分: {metrics.get('turnover_rate', 'N/A')}")
        print(f"市值评分: {metrics.get('big_market_cap', 'N/A')}")
        print(f"趋势评分: {metrics.get('price_trend', 'N/A')}")
        print(f"量价评分: {metrics.get('volume_coordination', 'N/A')}")
        print(f"技术面评分: {metrics.get('technicals', 'N/A')}")
        print(f"涨幅控制评分: {metrics.get('gain_control', 'N/A')}")
        print(f"检测特征: {result.get('detected_patterns', 'N/A')}")
    else:
        print('分析失败')
else:
    print('K线数据不足')