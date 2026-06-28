"""光智科技低吸时机分析"""
import pandas as pd

cache_file = r'D:\mystock\cache_daily\300489.SZ.csv'
df = pd.read_csv(cache_file, encoding='utf-8')
df['trade_date'] = df['trade_date'].astype(str)
df = df.sort_values('trade_date', ascending=True).reset_index(drop=True)

print('=== 光智科技（300489）低吸时机分析 ===\n')

# 找到6月11日的位置
target_idx = df[df['trade_date'] == '20260611'].index[0]

# 找首波涨停（20260528）
wave1_idx = df[df['trade_date'] == '20260528'].index[0]

# 显示首波后的回踩过程
print('【首波涨停后回踩过程】\n')
print(f'{"日期":<12} {"收盘":<8} {"涨幅":<8} {"最低":<8} {"成交量":<12} {"换手率":<10} {"说明"}')
print('-' * 80)

wave1_close = float(df.loc[wave1_idx, 'close'])

for i in range(wave1_idx, min(target_idx + 1, wave1_idx + 20)):
    row = df.loc[i]
    date = row['trade_date']
    close = float(row['close'])
    pct = float(row['pct_chg'])
    low = float(row['low'])
    vol = float(row['vol']) / 1e4  # 万手
    
    # 标记关键点
    if date == '20260528':
        note = '🔥首波涨停'
    elif date == '20260529':
        note = '首波次日'
    elif date == '20260611':
        note = '✓二波突破'
    elif i == wave1_idx + 1:
        note = '回踩开始'
    else:
        # 判断是否最低点
        pullback_data = df.loc[wave1_idx+1:target_idx-1]
        min_low_idx = pullback_data['low'].idxmin()
        if i == min_low_idx:
            note = '🎯回踩最低点（低吸日）'
        else:
            note = ''
    
    print(f'{date:<12} {close:<8.2f} {pct:+6.2f}%  {low:<8.2f} {vol:<12.0f}万手 {"":<10} {note}')

# 找到回踩最低点
pullback_data = df.loc[wave1_idx+1:target_idx-1]
min_low_idx = pullback_data['low'].idxmin()
min_low_row = df.loc[min_low_idx]

print('\n' + '='*80)
print('\n【低吸时机总结】\n')
print(f'首波涨停日：20260528')
print(f'  收盘价：{wave1_close:.2f}')
print(f'\n回踩最低日：{min_low_row["trade_date"]}')
print(f'  最低价：{float(min_low_row["low"]):.2f}')
print(f'  回踩幅度：{float(min_low_row["low"])/wave1_close*100:.1f}%')
print(f'\n二波突破日：20260611')
print(f'  收盘价：{float(df.loc[target_idx, "close"]):.2f}')
print(f'  涨幅：{float(df.loc[target_idx, "pct_chg"]):+.2f}%')

print(f'\n【低吸策略】')
print(f'  最佳低吸日：{min_low_row["trade_date"]}')
print(f'  低吸价格：{float(min_low_row["low"]):.2f}')
print(f'  止损价位：{float(min_low_row["low"])*0.95:.2f} (-5%)')
print(f'  止盈目标：{wave1_close:.2f} (首波收盘价)')
print(f'  预期收益：{(wave1_close/float(min_low_row["low"])-1)*100:.1f}%')

# 计算低吸收益
low_buy = float(min_low_row["low"])
sell_price = float(df.loc[target_idx, "close"])
print(f'\n【实际收益】')
print(f'  低吸价位：{low_buy:.2f}')
print(f'  二波卖出：{sell_price:.2f}')
print(f'  收益率：{(sell_price/low_buy-1)*100:.1f}%')
