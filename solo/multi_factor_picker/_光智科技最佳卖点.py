"""光智科技最佳卖出时机分析"""
import pandas as pd

cache_file = r'D:\mystock\cache_daily\300489.SZ.csv'
df = pd.read_csv(cache_file, encoding='utf-8')
df['trade_date'] = df['trade_date'].astype(str)
df = df.sort_values('trade_date', ascending=True).reset_index(drop=True)

print('=== 光智科技最佳卖出时机分析 ===\n')

# 找到6月11日二波突破后的走势
target_idx = df[df['trade_date'] == '20260611'].index[0]

# 显示6月11日后的走势（假设数据到6月26日）
print('【二波突破后走势】\n')
print(f'{"日期":<12} {"开盘":<8} {"收盘":<8} {"最高":<8} {"涨幅":<8} {"成交量":<12} {"说明"}')
print('-' * 80)

wave2_close = float(df.loc[target_idx, 'close'])  # 176.00
max_price = wave2_close
max_date = '20260611'

for i in range(target_idx, min(target_idx + 20, len(df))):
    row = df.loc[i]
    date = row['trade_date']
    close = float(row['close'])
    high = float(row['high'])
    pct = float(row['pct_chg'])
    vol = float(row['vol']) / 1e4
    
    # 更新最高价
    if high > max_price:
        max_price = high
        max_date = date
    
    # 标记关键点
    note = ''
    if date == '20260611':
        note = '✓二波突破'
    elif high >= wave2_close * 1.15:
        note = f'突破+15%止盈线'
    elif pct < -5:
        note = '⚠️跌破-5%止损线'
    elif close < wave2_close * 0.95:
        note = '跌破首波收盘'
    
    print(f'{date:<12} {float(row["open"]):<8.2f} {close:<8.2f} {high:<8.2f} {pct:+6.2f}%  {vol:<12.0f}万手 {note}')

# 分析卖点策略
print('\n' + '='*80)
print('\n【卖出策略分析】\n')

# 假设数据到6月26日
latest_idx = min(target_idx + 15, len(df) - 1)
latest_close = float(df.loc[latest_idx, 'close'])
latest_high = float(df.loc[latest_idx, 'high'])
latest_date = df.loc[latest_idx, 'trade_date']

print(f'二波买入价：176.00元（20260611收盘）')
print(f'当前日期：{latest_date}')
print(f'当前收盘：{latest_close:.2f}元')
print(f'期间最高：{max_price:.2f}元（{max_date}）')
print(f'当前收益：{(latest_close/176-1)*100:+.2f}%')

print('\n\n【卖点策略对比】\n')

strategies = [
    ('固定止盈', '首波收盘+15%', wave2_close * 1.15, '+15%', f'{(wave2_close*1.15/176-1)*100:.1f}%'),
    ('回撤止盈', '最高价回撤-5%', max_price * 0.95, '动态', f'{(max_price*0.95/176-1)*100:.1f}%'),
    ('均线止盈', '跌破MA5', None, '动态', '观察'),
    ('目标止盈', '首波高点+10%', 140.02 * 1.10, '+10%', f'{(140.02*1.10/176-1)*100:.1f}%'),
    ('时间止盈', '持仓10天', None, '10天', '等待'),
]

print(f'{"策略":<12} {"规则":<18} {"触发价位":<12} {"收益率":<10} {"适用场景"}')
print('-' * 80)

for strategy, rule, price, target_rate, actual_rate in strategies:
    price_str = f'{price:.2f}' if price else '动态'
    print(f'{strategy:<12} {rule:<18} {price_str:<12} {actual_rate:<10} {"强势股"}' if strategy != '时间止盈' else f'{strategy:<12} {rule:<18} {price_str:<12} {actual_rate:<10} {"波段股"}')

# 智能卖点分析
print('\n\n【智能卖点建议】\n')

if latest_high >= wave2_close * 1.20:
    print(f'✓ 已突破+20%，建议分批止盈')
    print(f'  第一卖点：{wave2_close * 1.15:.2f}元（+15%）')
    print(f'  第二卖点：{max_price * 0.95:.2f}元（回撤-5%）')
    print(f'  第三卖点：跌破MA10')
elif latest_high >= wave2_close * 1.10:
    print(f'✓ 已突破+10%，建议设置移动止盈')
    print(f'  移动止盈线：{max_price * 0.95:.2f}元（最高价-5%）')
    print(f'  或跌破MA5离场')
else:
    print(f'⚠️ 涨幅不足+10%，继续持有')
    print(f'  止损线：{wave2_close * 0.95:.2f}元（-5%）')

# 计算最佳卖点
print('\n\n【最佳卖点回测】\n')

# 找到二波后的最高点
if target_idx + 1 < len(df):
    future_data = df.loc[target_idx:target_idx+15]
    
    # 方案1：固定止盈+15%
    target1 = wave2_close * 1.15
    hit1 = future_data[future_data['high'] >= target1]
    if len(hit1) > 0:
        print(f'方案1：固定止盈+15%')
        print(f'  触发日期：{hit1.iloc[0]["trade_date"]}')
        print(f'  卖出价格：{target1:.2f}元')
        print(f'  收益率：+15.0%\n')
    
    # 方案2：回撤止盈
    cumulative_max = future_data['high'].cummax()
    drawdown = (future_data['close'] / cumulative_max - 1) * 100
    trigger_idx = drawdown[drawdown <= -5].index
    
    if len(trigger_idx) > 0:
        sell_idx = trigger_idx[0]
        sell_price = float(future_data.loc[sell_idx, 'close'])
        print(f'方案2：回撤止盈（最高价-5%）')
        print(f'  最高日期：{future_data.loc[sell_idx, "trade_date"]}')
        print(f'  卖出价格：{sell_price:.2f}元')
        print(f'  收益率：{(sell_price/wave2_close-1)*100:.1f}%\n')
    
    # 方案3：均线止盈
    future_data_copy = future_data.copy()
    future_data_copy['ma5'] = future_data_copy['close'].rolling(5).mean()
    
    if len(future_data_copy) > 5:
        break_ma5 = future_data_copy[future_data_copy['close'] < future_data_copy['ma5']]
        if len(break_ma5) > 0:
            sell_idx2 = break_ma5.index[0]
            sell_price2 = float(break_ma5.iloc[0]['close'])
            print(f'方案3：均线止盈（跌破MA5）')
            print(f'  触发日期：{break_ma5.iloc[0]["trade_date"]}')
            print(f'  卖出价格：{sell_price2:.2f}元')
            print(f'  收益率：{(sell_price2/wave2_close-1)*100:.1f}%\n')
