"""反弹第一日确认指标分析 - 直接读取结果"""
import pandas as pd
import numpy as np

print('=== 反弹第一日确认指标分析 ===\n')

# 读取之前的结果
df_0608 = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\vshape_0608_results.csv')

# 筛选首日反弹
first_day = df_0608[df_0608['rebound_days'] == 0].copy()

print(f'首日反弹样本：{len(first_day)}只\n')
print(f'基准胜率：{first_day["profit"].sum()/len(first_day)*100:.1f}%\n')

# 基于现有字段模拟确认指标
# rebound_days=0 说明是最低点当日

# 分析涨幅分布
print('='*80)
print('\n【涨幅分布胜率】\n')
print(f'{"涨幅区间":<15} {"数量":<10} {"胜率":<12} {"均收益":<10}')
print('-' * 60)

bins = [0, 3, 5, 7, 9, 100]
labels = ['0-3%', '3-5%', '5-7%', '7-9%', '>9%']

for i in range(len(bins)-1):
    segment = first_day[(first_day['pullback'] >= bins[i]/100) & (first_day['pullback'] < bins[i+1]/100)]
    
    # 这里的pullback是回踩比例，不是涨幅
    # 用rsi_min代替涨幅分析
    
for i, (low, high) in enumerate([(0, 3), (3, 5), (5, 7), (7, 9), (9, 20)]):
    segment = first_day[(first_day['rsi_min'] >= low) & (first_day['rsi_min'] < high)]
    
bins_pct = [(-100, 0), (0, 3), (3, 5), (5, 7), (7, 10), (10, 100)]
labels_pct = ['下跌', '0-3%', '3-5%', '5-7%', '7-10%', '>10%']

# 先看RSI分布
print('\n【RSI分布胜率】\n')
print(f'{"RSI区间":<15} {"数量":<10} {"胜率":<12} {"均收益":<10}')
print('-' * 60)

for i, (low, high) in enumerate([(0, 25), (25, 30), (30, 40), (40, 50), (50, 100)]):
    segment = first_day[(first_day['rsi_min'] >= low) & (first_day['rsi_min'] < high)]
    if len(segment) >= 5:
        win = segment['profit'].sum()
        winrate = win / len(segment) * 100
        avg_return = segment['return'].mean()
        print(f'{low}-{high}        {len(segment):<10} {winrate:<12.1f}% {avg_return:<10.1f}%')

# 回踩深度分析
print('\n\n【回踩深度胜率】\n')
print(f'{"回踩区间":<15} {"数量":<10} {"胜率":<12} {"均收益":<10}')
print('-' * 60)

for i, (low, high) in enumerate([(0.75, 0.80), (0.80, 0.85), (0.85, 0.90), (0.90, 0.95), (0.95, 1.00)]):
    segment = first_day[(first_day['pullback'] >= low) & (first_day['pullback'] < high)]
    if len(segment) >= 5:
        win = segment['profit'].sum()
        winrate = win / len(segment) * 100
        avg_return = segment['return'].mean()
        note = '✓最佳' if low >= 0.80 and high <= 0.85 else ''
        print(f'{low:.0%}-{high:.0%}      {len(segment):<10} {winrate:<12.1f}% {avg_return:<10.1f}% {note}')

# 评分分段
print('\n\n【评分分段胜率】\n')
print(f'{"评分区间":<15} {"数量":<10} {"胜率":<12} {"均收益":<10}')
print('-' * 60)

for threshold in [30, 35, 40, 45]:
    segment = first_day[first_day['score'] >= threshold]
    if len(segment) >= 5:
        win = segment['profit'].sum()
        winrate = win / len(segment) * 100
        avg_return = segment['return'].mean()
        mark = '✓' if winrate > 80 else ''
        print(f'{threshold}分以上       {len(segment):<10} {winrate:<12.1f}% {avg_return:<10.1f}% {mark}')

# 组合分析
print('\n\n' + '='*80)
print('\n【组合指标胜率】\n')
print(f'{"组合条件":<30} {"数量":<10} {"胜率":<12} {"均收益":<10} {"提升"}')
print('-' * 80)

baseline = first_day['profit'].sum() / len(first_day) * 100

combinations = [
    ('RSI<30', first_day['rsi_min'] < 30),
    ('RSI<40', first_day['rsi_min'] < 40),
    ('回踩80-85%', (first_day['pullback'] >= 0.80) & (first_day['pullback'] < 0.85)),
    ('评分≥40', first_day['score'] >= 40),
    ('评分≥45', first_day['score'] >= 45),
    ('RSI<30+评分≥40', (first_day['rsi_min'] < 30) & (first_day['score'] >= 40)),
    ('回踩80-85%+评分≥40', (first_day['pullback'] >= 0.80) & (first_day['pullback'] < 0.85) & (first_day['score'] >= 40)),
    ('RSI<40+回踩80-90%', (first_day['rsi_min'] < 40) & (first_day['pullback'] >= 0.80) & (first_day['pullback'] < 0.90)),
    ('RSI<40+评分≥40', (first_day['rsi_min'] < 40) & (first_day['score'] >= 40)),
    ('回踩<85%+评分≥40', (first_day['pullback'] < 0.85) & (first_day['score'] >= 40)),
    ('RSI<40+回踩<85%', (first_day['rsi_min'] < 40) & (first_day['pullback'] < 0.85)),
    ('RSI<40+回踩<85%+评分≥40', 
     (first_day['rsi_min'] < 40) & (first_day['pullback'] < 0.85) & (first_day['score'] >= 40)),
]

best_combos = []

for name, condition in combinations:
    segment = first_day[condition]
    if len(segment) >= 3:
        win = segment['profit'].sum()
        winrate = win / len(segment) * 100
        avg_return = segment['return'].mean()
        lift = winrate - baseline
        mark = '✓✓' if winrate > 80.3 else ('✓' if winrate > 70 else '')
        print(f'{name:<30} {len(segment):<10} {winrate:<12.1f}% {avg_return:<10.1f}% {lift:+.1f}pp {mark}')
        
        if winrate > 80.3:
            best_combos.append({
                'name': name,
                'count': len(segment),
                'winrate': winrate,
                'return': avg_return,
            })

# 输出最优组合
print('\n\n' + '='*80)
print('\n【胜率超过第4天（80.3%）的最优组合】\n')

if len(best_combos) > 0:
    best_combos.sort(key=lambda x: x['winrate'], reverse=True)
    
    print(f'{"排名":<6} {"组合":<30} {"数量":<10} {"胜率":<12} {"均收益":<10}')
    print('-' * 70)
    
    for i, combo in enumerate(best_combos[:10], 1):
        print(f'{i:<6} {combo["name"]:<30} {combo["count"]:<10} {combo["winrate"]:<12.1f}% {combo["return"]:<10.1f}%')
    
    print(f'\n✨ 最优组合：{best_combos[0]["name"]}')
    print(f'   胜率：{best_combos[0]["winrate"]:.1f}%（超过第4天{best_combos[0]["winrate"]-80.3:.1f}pp）')
    print(f'   样本：{best_combos[0]["count"]}只')
else:
    print('⚠️ 未找到胜率超过80.3%的组合')
    print('\n建议：')
    print('1. 第一日介入风险较大，胜率难以超过80%')
    print('2. 推荐等待第4天确认后介入（80.3%胜率）')
    print('3. 或等待次日确认后介入')

# 光智科技案例分析
print('\n\n' + '='*80)
print('\n【光智科技确认指标分析】\n')

gz = first_day[first_day['code'] == '300489.SZ']

if len(gz) > 0:
    print(f'评分：{gz["score"].values[0]:.0f}分')
    print(f'RSI：{gz["rsi_min"].values[0]:.1f}')
    print(f'回踩比例：{gz["pullback"].values[0]:.1%}')
    print(f'收益：{gz["return"].values[0]:.1f}%')
    
    print('\n符合的确认条件：')
    if gz['score'].values[0] >= 40:
        print('  ✓ 评分≥40分')
    if gz['pullback'].values[0] < 0.85:
        print('  ✓ 回踩<85%（深回踩）')
    if gz['rsi_min'].values[0] < 40:
        print('  ✓ RSI<40（超卖）')
