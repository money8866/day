"""
飞龙股份（002536.SZ）A浪检测调试
==================================
详细分析为什么降低门槛到40%后还是检测不到A浪。
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

print(f'=== {ts_code} A浪检测调试 ===')
print(f'数据长度: {len(df)}')
print(f'日期范围: {df.iloc[0]["trade_date"]} 至 {df.iloc[-1]["trade_date"]}')
print()

# 手动检查最近的上涨波段
print('手动检查最近120天的上涨波段:')
recent = df.tail(120).copy()
close = recent['close'].values

# 找所有局部低点和高点
lows = []
highs = []
for i in range(1, len(recent) - 1):
    if close[i] <= close[i-1] and close[i] <= close[i+1]:
        lows.append(i)
    if close[i] >= close[i-1] and close[i] >= close[i+1]:
        highs.append(i)

print(f'局部低点: {len(lows)}个')
print(f'局部高点: {len(highs)}个')
print()

# 显示局部低点
print('局部低点明细:')
for idx in lows[-10:]:  # 最近10个
    row = recent.iloc[idx]
    print(f'  {row["trade_date"]}: close={row["close"]:.2f}, RSI6={row.get("rsi_bfq_6", 0):.1f}')
print()

# 显示局部高点
print('局部高点明细:')
for idx in highs[-10:]:  # 最近10个
    row = recent.iloc[idx]
    print(f'  {row["trade_date"]}: close={row["close"]:.2f}, RSI6={row.get("rsi_bfq_6", 0):.1f}')
print()

# 计算所有可能的A浪（低点→高点）
print('计算所有可能的A浪（低点→高点，涨幅≥40%）:')
possible_awaves = []
for low_idx in lows:
    for high_idx in highs:
        if high_idx > low_idx:  # 高点必须在低点之后
            gain = (close[high_idx] / close[low_idx] - 1) * 100
            duration = high_idx - low_idx
            
            if gain >= 40 and 20 <= duration <= 60:
                possible_awaves.append({
                    'low_date': recent.iloc[low_idx]['trade_date'],
                    'high_date': recent.iloc[high_idx]['trade_date'],
                    'low_price': close[low_idx],
                    'high_price': close[high_idx],
                    'gain': gain,
                    'duration': duration,
                })

if possible_awaves:
    # 按涨幅排序
    possible_awaves.sort(key=lambda x: x['gain'], reverse=True)
    
    print(f'找到 {len(possible_awaves)} 个可能的A浪:')
    for i, awave in enumerate(possible_awaves[:5]):  # 显示前5个
        print(f'  {i+1}. {awave["low_date"]} → {awave["high_date"]}')
        print(f'     涨幅: {awave["gain"]:.1f}%, 持续: {awave["duration"]}天')
        print(f'     低点: {awave["low_price"]:.2f}, 高点: {awave["high_price"]:.2f}')
        print()
else:
    print('未找到符合条件的A浪')
    print()
    print('可能原因:')
    print('  1. 涨幅≥40%的低点→高点组合不存在')
    print('  2. 持续时间不在20-60天范围内')
    print('  3. 需要检查更长时间范围（当前只检查最近120天）')

# 正式调用detect_awave函数
print('=' * 60)
print('正式调用detect_awave函数:')
awave = detect_awave(df)
if awave:
    print(f'  ✅ 检测到A浪！')
    print(f'     起点: {awave["start_date"]} (价格={awave["start_price"]:.2f})')
    print(f'     终点: {awave["end_date"]} (价格={awave["end_price"]:.2f})')
    print(f'     涨幅: {awave["gain"]:.1f}%')
    print(f'     持续: {awave["duration"]}天')
    print(f'     评分: {awave["score"]}分')
else:
    print('  ❌ 未检测到A浪')
    print()
    print('调试建议:')
    print('  1. 检查detect_awave函数中的MA20上行比例条件')
    print('  2. 检查价格在MA20上方比例条件')
    print('  3. 可能一轮上涨中有回调，打断了MA20上行条件')
