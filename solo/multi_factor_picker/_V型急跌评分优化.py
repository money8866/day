"""V型急跌评分优化 - 规避已反弹股票，提升首日反弹评分"""
import pandas as pd
import glob
import os

cache_dir = r'D:\mystock\cache_daily'

print('=== V型急跌评分优化分析 ===\n')
print('目标：规避已反弹股票（如300710），提升首日反弹评分（如光智科技）\n')

# 先分析300710和光智科技的差异
print('【案例对比分析】\n')

for code, name in [('300710.SZ', '万祥科技'), ('300489.SZ', '光智科技')]:
    file = f'{cache_dir}\\{code}'
    df = pd.read_csv(file, encoding='utf-8')
    df['trade_date'] = df['trade_date'].astype(str)
    df = df.sort_values('trade_date', ascending=True).reset_index(drop=True)
    
    # 找到20260608
    target_idx = df[df['trade_date'] == '20260608'].index[0]
    
    print(f'\n{name}（{code}）20260608前后走势：')
    print(f'{"日期":<12} {"收盘":<8} {"涨幅":<8} {"最低":<8} {"说明"}')
    print('-' * 60)
    
    # 显示前后10天
    for i in range(max(0, target_idx-10), min(len(df), target_idx+5)):
        row = df.loc[i]
        date = row['trade_date']
        close = float(row['close'])
        pct = float(row['pct_chg'])
        low = float(row['low'])
        
        note = ''
        if date == '20260608':
            note = '🎯目标日'
        elif i < target_idx:
            if pct < -5:
                note = '⚠️急跌'
            elif pct > 5:
                note = '📈反弹'
        
        print(f'{date:<12} {close:<8.2f} {pct:+6.2f}%  {low:<8.2f} {note}')
    
    # 找到真正的最低点
    lookback_start = max(0, target_idx - 15)
    recent_data = df.loc[lookback_start:target_idx]
    min_low_idx = recent_data['low'].idxmin()
    min_low_date = df.loc[min_low_idx, 'trade_date']
    min_low = float(df.loc[min_low_idx, 'low'])
    
    # 计算反弹天数
    rebound_days = target_idx - min_low_idx
    
    print(f'\n最低点：{min_low_date}，价格：{min_low:.2f}元')
    print(f'反弹天数：{rebound_days}天')
    print(f'反弹幅度：{(float(df.loc[target_idx, "close"])/min_low-1)*100:.1f}%')

print('\n\n' + '='*80)
print('\n【优化方案：V型急跌专项评分】\n')

# 新的评分体系（V型急跌专用，满分50分）
print('新增因子：F5反弹时机（15分）')
print('-' * 60)
print('评分规则：')
print('  首日反弹（最低点次日）：+15分 ✨最佳')
print('  第2日反弹：+12分')
print('  第3日反弹：+10分')
print('  第4-5日反弹：+8分')
print('  第6-10日反弹：+5分')
print('  >10日反弹：+0分 ✗过滤')

print('\n\n调整F1回踩位置评分（优化版）：')
print('-' * 60)
print('  回踩75-80%：+12分（极深，风险高但收益高）')
print('  回踩80-85%：+10分（最佳区间）')
print('  回踩85-90%：+8分（较浅）')
print('  回踩>90%：+5分（过浅）')

print('\n\n【重新评分光智科技】')
print('-' * 60)

# 光智科技重新评分
target_file = f'{cache_dir}\\300489.SZ.csv'
df = pd.read_csv(target_file, encoding='utf-8')
df['trade_date'] = df['trade_date'].astype(str)
df = df.sort_values('trade_date', ascending=True).reset_index(drop=True)

target_idx = df[df['trade_date'] == '20260608'].index[0]

# 找首波涨停
wave1_idx = df[df['trade_date'] == '20260528'].index[0]
wave1_close = float(df.loc[wave1_idx, 'close'])

# 找最低点
lookback_start = max(wave1_idx+1, target_idx-15)
recent_data = df.loc[lookback_start:target_idx]
min_low_idx = recent_data['low'].idxmin()
min_low = float(df.loc[min_low_idx, 'low'])
min_low_date = df.loc[min_low_idx, 'trade_date']

# 计算反弹天数
rebound_days = target_idx - min_low_idx

pullback_ratio = min_low / wave1_close

print(f'最低点日期：{min_low_date}')
print(f'最低价：{min_low:.2f}元')
print(f'反弹天数：{rebound_days}天（首日反弹！）')
print(f'回踩比例：{pullback_ratio:.1%}')

score = 0

# F1: 回踩位置（10分）
if 0.75 <= pullback_ratio < 0.80:
    score += 12
    f1_note = '+12分（极深回踩）'
elif 0.80 <= pullback_ratio < 0.85:
    score += 10
    f1_note = '+10分（最佳区间）'
elif 0.85 <= pullback_ratio < 0.90:
    score += 8
    f1_note = '+8分（较浅回踩）'
else:
    score += 5
    f1_note = '+5分（过浅回踩）'

# F5: 反弹时机（15分）- 新增
if rebound_days == 1:
    score += 15
    f5_note = '+15分（首日反弹✨）'
elif rebound_days == 2:
    score += 12
    f5_note = '+12分（第2日反弹）'
elif rebound_days == 3:
    score += 10
    f5_note = '+10分（第3日反弹）'
elif rebound_days <= 5:
    score += 8
    f5_note = '+8分（第4-5日反弹）'
elif rebound_days <= 10:
    score += 5
    f5_note = '+5分（第6-10日反弹）'
else:
    score += 0
    f5_note = '+0分（反弹过晚✗）'

# F2: 缩量（10分）
vol_ma5 = float(df.loc[target_idx-5:target_idx, 'vol'].mean())
vol_ratio = float(df.loc[target_idx, 'vol']) / vol_ma5 if vol_ma5 > 0 else 1

if vol_ratio < 0.5:
    score += 10
    f2_note = '+10分（极度缩量）'
elif vol_ratio < 0.7:
    score += 8
    f2_note = '+8分（明显缩量）'
elif vol_ratio < 1.0:
    score += 6
    f2_note = '+6分（轻度缩量）'
else:
    score += 4
    f2_note = '+4分（未缩量）'

# F3: RSI超卖（10分）- 用最低点那天的RSI
min_low_14 = df.loc[min_low_idx-13:min_low_idx, 'close']
if len(min_low_14) == 14:
    gains = min_low_14.diff()
    gains_pos = gains[gains > 0]
    losses = -gains[gains < 0]
    avg_gain = gains_pos.mean() if len(gains_pos) > 0 else 0
    avg_loss = losses.mean() if len(losses) > 0 else 0.01
    rs = avg_gain / avg_loss
    rsi_min = 100 - (100 / (1 + rs))
else:
    rsi_min = 50

if rsi_min < 30:
    score += 10
    f3_note = f'+10分（RSI={rsi_min:.0f}极度超卖）'
elif rsi_min < 40:
    score += 8
    f3_note = f'+8分（RSI={rsi_min:.0f}明显超卖）'
elif rsi_min < 50:
    score += 6
    f3_note = f'+6分（RSI={rsi_min:.0f}轻度超卖）'
else:
    score += 4
    f3_note = f'+4分（RSI={rsi_min:.0f}未超卖）'

# F4: 均线支撑（5分）
ma60 = float(df.loc[target_idx-60:target_idx, 'close'].mean())
ma120 = float(df.loc[target_idx-120:target_idx, 'close'].mean())

support_count = 0
if min_low >= ma60 * 0.98:
    support_count += 1
    score += 5
if min_low >= ma120 * 0.98:
    support_count += 1
    score += 5

f4_note = f'+{support_count*5}分（{support_count}条均线支撑）'

print(f'\n新评分明细（满分50分）：')
print(f'  F1回踩位置：{f1_note}')
print(f'  F5反弹时机：{f5_note} 🔥')
print(f'  F2缩量程度：{f2_note}')
print(f'  F3超卖程度：{f3_note}')
print(f'  F4均线支撑：{f4_note}')
print(f'\n总评分：{score}/50分')

print('\n\n【对比300710】')
print('-' * 60)

target_file2 = f'{cache_dir}\\300710.SZ.csv'
df2 = pd.read_csv(target_file2, encoding='utf-8')
df2['trade_date'] = df2['trade_date'].astype(str)
df2 = df2.sort_values('trade_date', ascending=True).reset_index(drop=True)

target_idx2 = df2[df2['trade_date'] == '20260608'].index[0]

# 找最低点
wave1_idx2 = df2[df2['trade_date'] == '20260528'].index[0]
lookback_start2 = max(wave1_idx2+1, target_idx2-15)
recent_data2 = df2.loc[lookback_start2:target_idx2]
min_low_idx2 = recent_data2['low'].idxmin()
min_low2 = float(df2.loc[min_low_idx2, 'low'])
min_low_date2 = df2.loc[min_low_idx2, 'trade_date']

rebound_days2 = target_idx2 - min_low_idx2

print(f'最低点日期：{min_low_date2}')
print(f'最低价：{min_low2:.2f}元')
print(f'反弹天数：{rebound_days2}天（已反弹多日）')
print(f'评分调整：')

if rebound_days2 > 5:
    print(f'  F5反弹时机：+0分（已反弹{rebound_days2}天✗过滤）')
    print(f'  总评分：预计<20分，被排除')

print('\n\n' + '='*80)
print('\n【V型急跌评分标准（优化版）】\n')
print('满分：50分（新增F5因子）')
print('\nF1 回踩位置（12分）：')
print('  75-80%：+12分（极深）')
print('  80-85%：+10分（最佳）')
print('  85-90%：+8分（较浅）')
print('  >90%：+5分（过浅）')
print('\nF5 反弹时机（15分）：🔥新增')
print('  首日反弹：+15分 ✨')
print('  第2日：+12分')
print('  第3日：+10分')
print('  第4-5日：+8分')
print('  第6-10日：+5分')
print('  >10日：+0分 ✗过滤')
print('\nF2 缩量程度（10分）：')
print('  量比<0.5：+10分')
print('  量比<0.7：+8分')
print('  量比<1.0：+6分')
print('  量比≥1.0：+4分')
print('\nF3 RSI超卖（10分）：用最低点那天的RSI')
print('  RSI<30：+10分')
print('  RSI<40：+8分')
print('  RSI<50：+6分')
print('  RSI≥50：+4分')
print('\nF4 均线支撑（10分）：')
print('  MA60支撑：+5分')
print('  MA120支撑：+5分')

print('\n\n【过滤规则】')
print('  1. 反弹天数>10日：直接过滤')
print('  2. 总评分<25分：过滤')
print('  3. 回踩比例<75%或>100%：过滤')
