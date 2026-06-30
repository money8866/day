"""
飞龙股份（002536.SZ）B浪策略分析
=====================================
详细分析为什么没检测到A浪，以及历史走势特征。
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bwave_strategy import get_data, detect_awave
import pandas as pd

ts_code = '002536.SZ'
df = get_data(ts_code)

if df is None:
    print(f'无法获取{ts_code}数据')
    sys.exit(1)

print(f'=== {ts_code} 飞龙股份 详细分析 ===')
print(f'数据长度: {len(df)}')
print(f'日期范围: {df.iloc[0]["trade_date"]} 至 {df.iloc[-1]["trade_date"]}')
print()

# 看看最近120天的走势关键点
recent = df.tail(120).copy()
print('最近120天关键数据:')
print(recent[['trade_date', 'close', 'pct_chg', 'macd_dif_bfq', 'macd_dea_bfq', 'rsi_bfq_6']].to_string())
print()

# 手动检测A浪
awave = detect_awave(df)
if awave:
    print(f'A浪检测结果:')
    print(f'  起点: {awave["start_date"]} (价格={awave["start_price"]:.2f})')
    print(f'  终点: {awave["end_date"]} (价格={awave["end_price"]:.2f})')
    print(f'  涨幅: {awave["gain"]:.1f}%')
    print(f'  持续: {awave["duration"]}天')
    print(f'  评分: {awave["score"]}分')
    print()
    
    # 显示A浪期间走势
    start_idx = df[df['trade_date'] == awave['start_date']].index[0]
    end_idx = df[df['trade_date'] == awave['end_date']].index[0]
    seg = df.iloc[start_idx:end_idx+1]
    print(f'A浪期间走势 ({awave["duration"]}天):')
    print(seg[['trade_date', 'close', 'pct_chg', 'vol', 'macd_dif_bfq']].to_string())
    print()
else:
    print('A浪检测: 未检测到')
    print()
    print('可能原因:')
    print('  1. 最近120天没有涨幅≥60%的主升浪')
    print('  2. 主升浪持续时间不在20-60天范围内')
    print('  3. MA20上行比例不足60%')
    print('  4. 价格在MA20上方比例不足60%')
    print()
    
    # 检查最近走势：有没有一波上涨？
    print('检查最近走势:')
    close = df['close'].tail(120).values
    if len(close) >= 20:
        # 找局部低点和高点
        lows = []
        highs = []
        for i in range(1, len(close) - 1):
            if close[i] <= close[i-1] and close[i] <= close[i+1]:
                lows.append(i)
            if close[i] >= close[i-1] and close[i] >= close[i+1]:
                highs.append(i)
        
        if lows and highs:
            last_low = lows[-1]
            last_high = highs[-1]
            
            # 计算最近一波涨幅
            if last_low < last_high:
                gain = (close[last_high] / close[last_low] - 1) * 100
                print(f'  最近一波: 从低点{close[last_low]:.2f}到高点{close[last_high]:.2f}')
                print(f'  涨幅: {gain:.1f}%')
                if gain < 60:
                    print(f'  结论: 涨幅{gain:.1f}% < 60%，不符合A浪条件')
                else:
                    print(f'  结论: 涨幅符合要求，但其他条件可能不满足')
            else:
                print('  未找到完整的一波上涨')
        else:
            print('  未找到局部低点/高点')
    else:
        print('  数据不足')

# 保存最近120天数据到CSV，方便查看
output_path = r'D:\mystock\solo\trend_feature_output\002536_analysis.csv'
recent.to_csv(output_path, index=False, encoding='utf-8-sig')
print()
print(f'最近120天数据已保存: {output_path}')
