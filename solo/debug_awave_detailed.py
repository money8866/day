"""
飞龙股份（002536.SZ）A浪检测详细调试
======================================
打印detect_awave函数中的每一步过滤情况。
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bwave_strategy import get_data
import pandas as pd

ts_code = '002536.SZ'
df = get_data(ts_code)

if df is None:
    print(f'无法获取{ts_code}数据')
    sys.exit(1)

print(f'=== {ts_code} A浪检测详细调试 ===')
print(f'数据长度: {len(df)}')
print(f'日期范围: {df.iloc[0]["trade_date"]} 至 {df.iloc[-1]["trade_date"]}')
print()

# 直接复制detect_awave函数的逻辑，但加上详细打印
def debug_detect_awave(df):
    '''详细调试版A浪检测'''
    
    # 参数
    MIN_GAIN = 40   # 涨幅门槛（已改）
    MIN_DUR = 20
    MAX_DUR = 60
    MA20_UP_RATIO_MIN = 0.4   # MA20上行比例门槛（已改）
    ABOVE_MA20_RATIO_MIN = 0.4   # 价格在MA20上方比例门槛（已改）
    VOL_RATIO_MIN = 1.1   # 成交量比例门槛（已改）
    
    # 找候选起点（RSI<50 + 缩量）
    # 注意：这是原函数的逻辑，可能起点就找错了
    print('步骤1: 找候选起点（RSI<50 + 缩量）...')
    recent = df.tail(120).copy()
    candidates = []
    for i in range(len(recent) - MIN_DUR):
        row = recent.iloc[i]
        rsi = row.get('rsi_bfq_6', 50)
        vol = row['vol']
        avg_vol = recent.iloc[max(0, i-20):i]['vol'].mean() if i >= 20 else vol
        
        if rsi < 50 and vol < avg_vol * 0.8:
            candidates.append(i)
    
    print(f'  候选起点数量: {len(candidates)}')
    if not candidates:
        print('  ❌ 无候选起点！RSI<50 + 缩量的日子不存在')
        print('  可能原因: 飞龙股份一直在上涨，RSI经常>50')
        return None
    
    print(f'  前5个候选起点:')
    for idx in candidates[:5]:
        row = recent.iloc[idx]
        print(f'    {row["trade_date"]}: close={row["close"]:.2f}, RSI6={row.get("rsi_bfq_6", 0):.1f}')
    print()
    
    # 找局部低点和高点
    print('步骤2: 找局部低点和高点...')
    lows = []
    highs = []
    for i in range(1, len(recent) - 1):
        if recent.iloc[i]['close'] <= recent.iloc[i-1]['close'] and recent.iloc[i]['close'] <= recent.iloc[i+1]['close']:
            lows.append(i)
        if recent.iloc[i]['close'] >= recent.iloc[i-1]['close'] and recent.iloc[i]['close'] >= recent.iloc[i+1]['close']:
            highs.append(i)
    
    print(f'  局部低点: {len(lows)}个')
    print(f'  局部高点: {len(highs)}个')
    print()
    
    # 对每个低点→高点对，检查A浪特征
    print('步骤3: 检查所有低点→高点对...')
    valid_awaves = []
    
    for a_start in lows:
        for a_end in highs:
            if a_end <= a_start + MIN_DUR or a_end > a_start + MAX_DUR:
                continue
            
            start_price = recent.iloc[a_start]['close']
            end_price = recent.iloc[a_end]['close']
            if start_price <= 0:
                continue
            
            gain = (end_price / start_price - 1) * 100
            if gain < MIN_GAIN:
                continue
            
            duration = a_end - a_start
            
            # 检查MA20上行比例
            ma20_slice = recent.iloc[a_start:a_end + 1]['ma_bfq_20'].values
            ma20_up_count = sum(1 for i in range(1, len(ma20_slice)) if ma20_slice[i] > ma20_slice[i - 1] and ma20_slice[i] > 0)
            ma20_up_ratio = ma20_up_count / max(len(ma20_slice) - 1, 1)
            if ma20_up_ratio < MA20_UP_RATIO_MIN:
                continue
            
            # 检查价格在MA20上方比例
            above_ma20 = sum(1 for i in range(a_start, a_end + 1) if recent.iloc[i]['close'] > recent.iloc[i]['ma_bfq_20'] > 0)
            above_ratio = above_ma20 / max(duration, 1)
            if above_ratio < ABOVE_MA20_RATIO_MIN:
                continue
            
            # 检查成交量比例
            a_vol = recent.iloc[a_start:a_end + 1]['vol'].mean()
            vol_40 = recent.iloc[max(0, a_start - 40):a_start]['vol'].mean()
            vol_ratio_a = a_vol / vol_40 if vol_40 > 0 else 0
            if vol_ratio_a < VOL_RATIO_MIN:
                continue
            
            # 通过所有条件！
            valid_awaves.append({
                'start_date': recent.iloc[a_start]['trade_date'],
                'end_date': recent.iloc[a_end]['trade_date'],
                'gain': gain,
                'duration': duration,
                'ma20_up_ratio': ma20_up_ratio,
                'above_ratio': above_ratio,
                'vol_ratio_a': vol_ratio_a,
            })
    
    print(f'  通过所有条件的A浪数量: {len(valid_awaves)}')
    print()
    
    if valid_awaves:
        # 按涨幅排序
        valid_awaves.sort(key=lambda x: x['gain'], reverse=True)
        
        print('前3个有效A浪:')
        for i, awave in enumerate(valid_awaves[:3]):
            print(f'  {i+1}. {awave["start_date"]} → {awave["end_date"]}')
            print(f'     涨幅: {awave["gain"]:.1f}%, 持续: {awave["duration"]}天')
            print(f'     MA20上行比例: {awave["ma20_up_ratio"]:.2f}')
            print(f'     价格在MA20上方比例: {awave["above_ratio"]:.2f}')
            print(f'     成交量比例: {awave["vol_ratio_a"]:.2f}')
            print()
        
        return valid_awaves[0]
    else:
        print('❌ 未找到通过所有条件的A浪')
        print()
        print('调试建议:')
        print('  1. 进一步放宽条件（比如MA20上行比例≥30%）')
        print('  2. 或者放弃B浪策略（飞龙股份不符合"一波连续上涨"假设）')
        return None

# 运行调试版函数
result = debug_detect_awave(df)
print('=' * 60)
if result:
    print(f'调试结论: 找到A浪！')
    print(f'  {result["start_date"]} → {result["end_date"]}')
    print(f'  涨幅: {result["gain"]:.1f}%, 持续: {result["duration"]}天')
else:
    print('调试结论: 未找到A浪')
    print('建议: 放弃B浪策略，换其他策略（比如趋势跟踪）')
