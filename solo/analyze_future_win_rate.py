"""
分析信号的未来胜率，找出真正的失败率原因
"""
import pandas as pd
import sqlite3
import os

DB = r'D:\mystock\cache_daily\stock_data.db'

# 加载信号
csv_path = r'D:\mystock\solo\trend_feature_output\entry_precision_20260630_211404_qualified.csv'
df = pd.read_csv(csv_path, encoding='utf-8-sig')
print(f'Signal count: {len(df)}')
print()

# 计算每个信号的未来收益
conn = sqlite3.connect(DB)
cursor = conn.cursor()

results = []

for _, row in df.iterrows():
    code = row['ts_code']
    signal_date = row['signal_date']
    
    # 获取信号后5天的数据
    cursor.execute('''
        SELECT trade_date, pct_chg FROM stk_factor_pro 
        WHERE ts_code = ? AND trade_date > ?
        ORDER BY trade_date LIMIT 5
    ''', (code, signal_date))
    
    future = cursor.fetchall()
    
    if len(future) >= 2:
        # 计算2日累计收益
        d1 = future[0][1] / 100 + 1
        d2 = future[1][1] / 100 + 1
        ret_2d = (d1 * d2 - 1) * 100
        
        results.append({
            'ts_code': code,
            'signal_date': signal_date,
            'pct_chg': row['pct_chg'],
            'vol_ratio': row['vol_ratio'],
            'above_ma20_pct': row['above_ma20_pct'],
            'entry_score': row['entry_score'],
            'ret_2d': ret_2d,
            'win_2d': 1 if ret_2d > 0 else 0
        })

conn.close()

df_result = pd.DataFrame(results)
print(f'Samples with 2-day data: {len(df_result)}')
print()

# 分析未来胜率
print('=' * 70)
print('Future 2-Day Return Analysis (Real Failure Rate)')
print('=' * 70)
print()

# 1. MA20偏离度
print('1. MA20 Offset:')
for name, low, high in [('8-12%', 8, 12), ('12-15%', 12, 15), ('15-20%', 15, 20), ('20%+', 20, 999)]:
    subset = df_result[(df_result['above_ma20_pct'] >= low) & (df_result['above_ma20_pct'] < high)]
    if len(subset) >= 3:
        win_rate = subset['win_2d'].mean() * 100
        avg_ret = subset['ret_2d'].mean()
        print(f'  {name}: {len(subset):3} signals, 2d win={win_rate:5.1f}%, avg={avg_ret:+5.2f}%')

print()
print('2. Volume Ratio:')
for name, low, high in [('<1.0', 0, 1), ('1.0-1.5', 1, 1.5), ('1.5-2.0', 1.5, 2), ('2.0-3.0', 2, 3), ('3.0+', 3, 999)]:
    subset = df_result[(df_result['vol_ratio'] >= low) & (df_result['vol_ratio'] < high)]
    if len(subset) >= 3:
        win_rate = subset['win_2d'].mean() * 100
        avg_ret = subset['ret_2d'].mean()
        print(f'  {name}: {len(subset):3} signals, 2d win={win_rate:5.1f}%, avg={avg_ret:+5.2f}%')

print()
print('3. Signal Day Return:')
for name, low, high in [('3-7%', 3, 7), ('7-10%', 7, 10), ('10-15%', 10, 15), ('15%+', 15, 999)]:
    subset = df_result[(df_result['pct_chg'] >= low) & (df_result['pct_chg'] < high)]
    if len(subset) >= 3:
        win_rate = subset['win_2d'].mean() * 100
        avg_ret = subset['ret_2d'].mean()
        print(f'  {name}: {len(subset):3} signals, 2d win={win_rate:5.1f}%, avg={avg_ret:+5.2f}%')

print()
print('4. Entry Score:')
for name, low, high in [('>=80', 80, 999), ('65-79', 65, 80), ('50-64', 50, 65)]:
    subset = df_result[(df_result['entry_score'] >= low) & (df_result['entry_score'] < high)]
    if len(subset) >= 3:
        win_rate = subset['win_2d'].mean() * 100
        avg_ret = subset['ret_2d'].mean()
        print(f'  {name}: {len(subset):3} signals, 2d win={win_rate:5.1f}%, avg={avg_ret:+5.2f}%')

print()
print('5. Optimal Combo (MA20 15-20% + Vol 1.5-3.0 + Return 5-15%):')
subset = df_result[
    (df_result['above_ma20_pct'] >= 15) & 
    (df_result['above_ma20_pct'] < 20) &
    (df_result['vol_ratio'] >= 1.5) &
    (df_result['vol_ratio'] < 3.0) &
    (df_result['pct_chg'] >= 5) &
    (df_result['pct_chg'] <= 15)
]
if len(subset) >= 3:
    win_rate = subset['win_2d'].mean() * 100
    avg_ret = subset['ret_2d'].mean()
    print(f'  {len(subset)} signals, 2d win={win_rate:.1f}%, avg={avg_ret:+.2f}%')
else:
    print(f'  Too few samples: {len(subset)}')

# Save detailed results
df_result.to_csv(r'D:\mystock\solo\trend_feature_output\entry_with_future_return.csv', index=False, encoding='utf-8-sig')
print()
print(f'Saved to: D:\\mystock\\solo\\trend_feature_output\\entry_with_future_return.csv')
