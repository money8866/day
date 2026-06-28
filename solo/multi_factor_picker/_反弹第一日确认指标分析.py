"""反弹第一日确认性指标优化分析"""
import pandas as pd
import glob
import os

cache_dir = r'D:\mystock\cache_daily'

print('=== 反弹第一日确认性指标优化分析 ===\n')
print('目标：找到第一日介入的确认性指标，胜率超过第4天（80.3%）\n')

# 读取之前的回测结果
df_0608 = pd.read_csv(r'D:\mystock\solo\multi_factor_picker\vshape_0608_results.csv')

print(f'总样本数：{len(df_0608)}只\n')

# 筛选首日反弹（rebound_days == 0）
first_day = df_0608[df_0608['rebound_days'] == 0].copy()

print(f'首日反弹样本数：{len(first_day)}只\n')

# 分析首日反弹胜率
win_first = first_day['profit'].sum()
winrate_first = win_first / len(first_day) * 100
print(f'首日反弹整体胜率：{winrate_first:.1f}%\n')

# 开始分析确认性指标
print('='*80)
print('\n【确认性指标分析】\n')

all_files = glob.glob(f'{cache_dir}/*.csv')

# 存储带确认指标的样本
samples_with_indicators = []

for idx, row in first_day.iterrows():
    try:
        ts_code = row['code']
        file = f'{cache_dir}\\{ts_code}'
        
        if not os.path.exists(file):
            continue
        
        df = pd.read_csv(file, encoding='utf-8')
        df['trade_date'] = df['trade_date'].astype(str)
        df = df.sort_values('trade_date', ascending=True).reset_index(drop=True)
        
        # 找到20260608
        target_idx = df[df['trade_date'] == '20260608'].index[0]
        
        # ===== 确认性指标 =====
        
        # 1. 收盘形态
        today_close = float(df.loc[target_idx, 'close'])
        today_open = float(df.loc[target_idx, 'open'])
        today_high = float(df.loc[target_idx, 'high'])
        today_low = float(df.loc[target_idx, 'low'])
        today_pct = float(df.loc[target_idx, 'pct_chg'])
        
        # 实体大小
        body_size = abs(today_close - today_open) / today_open * 100
        
        # 收盘位置（相对于日内高低点）
        close_position = (today_close - today_low) / (today_high - today_low) * 100 if today_high > today_low else 50
        
        # 是否阳线
        is_bullish = today_close > today_open
        
        # 是否反包（收盘价超过昨日最高）
        yesterday_high = float(df.loc[target_idx-1, 'high'])
        is_engulfing = today_close > yesterday_high
        
        # 2. 成交量确认
        today_vol = float(df.loc[target_idx, 'vol'])
        vol_ma5 = float(df.loc[target_idx-5:target_idx, 'vol'].mean())
        vol_ratio = today_vol / vol_ma5 if vol_ma5 > 0 else 1
        
        # 放量程度
        is_volume_surge = vol_ratio > 1.5
        
        # 3. 技术指标确认
        # RSI（最低点那天的RSI）
        pullback_data = df.loc[target_idx-15:target_idx]
        min_low_idx = pullback_data['low'].idxmin()
        
        if min_low_idx >= 13:
            min_14 = df.loc[min_low_idx-13:min_low_idx, 'close']
            gains_min = min_14.diff()
            gains_min_pos = gains_min[gains_min > 0]
            losses_min = -gains_min[gains_min < 0]
            avg_gain_min = gains_min_pos.mean() if len(gains_min_pos) > 0 else 0
            avg_loss_min = losses_min.mean() if len(losses_min) > 0 else 0.01
            rs_min = avg_gain_min / avg_loss_min
            rsi_min = 100 - (100 / (1 + rs_min))
        else:
            rsi_min = 50
        
        # MACD金叉（今日）
        if target_idx >= 34:
            recent_close = df.loc[target_idx-33:target_idx, 'close']
            ema12 = recent_close.ewm(span=12).mean()
            ema26 = recent_close.ewm(span=26).mean()
            dif = ema12 - ema26
            dea = dif.ewm(span=9).mean()
            macd = (dif - dea) * 2
            
            # MACD金叉
            is_macd_golden = dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-2] <= dea.iloc[-2]
            # MACD底背离
            is_macd_divergence = dif.iloc[-1] > dif.iloc[-5] and df.loc[target_idx, 'low'] < df.loc[target_idx-5, 'low']
        else:
            is_macd_golden = False
            is_macd_divergence = False
        
        # 4. 均线支撑确认
        ma5 = float(df.loc[target_idx-5:target_idx, 'close'].mean())
        ma10 = float(df.loc[target_idx-10:target_idx, 'close'].mean())
        ma20 = float(df.loc[target_idx-20:target_idx, 'close'].mean())
        ma60 = float(df.loc[target_idx-60:target_idx, 'close'].mean())
        
        min_low = float(df.loc[min_low_idx, 'low'])
        
        # 均线支撑数量
        support_count = 0
        if min_low >= ma20 * 0.98:
            support_count += 1
        if min_low >= ma60 * 0.98:
            support_count += 1
        
        # 5. 资金流向（如有）
        # 假设无数据，跳过
        
        # 6. 分时形态（日内）
        # 如果涨幅>5%，且收盘接近最高点，说明强势
        is_strong_close = today_pct > 5 and close_position > 80
        
        # 7. 跳空高开
        yesterday_close = float(df.loc[target_idx-1, 'close'])
        is_gap_up = today_open > yesterday_high * 1.01
        
        # 8. 突破压力位
        # 计算10日高点
        high_10 = float(df.loc[target_idx-10:target_idx-1, 'high'].max())
        is_breakout = today_close > high_10
        
        # 计算收益
        future_return = row['return']
        is_profit = row['profit']
        
        # 存储样本
        samples_with_indicators.append({
            'code': ts_code,
            'profit': is_profit,
            'return': future_return,
            'body_size': body_size,
            'close_position': close_position,
            'is_bullish': is_bullish,
            'is_engulfing': is_engulfing,
            'vol_ratio': vol_ratio,
            'is_volume_surge': is_volume_surge,
            'rsi_min': rsi_min,
            'is_macd_golden': is_macd_golden,
            'is_macd_divergence': is_macd_divergence,
            'support_count': support_count,
            'is_strong_close': is_strong_close,
            'is_gap_up': is_gap_up,
            'is_breakout': is_breakout,
            'pct_chg': today_pct,
        })
    
    except Exception as e:
        continue

# 转换为DataFrame
df_indicators = pd.DataFrame(samples_with_indicators)

print(f'有效样本数：{len(df_indicators)}只\n')

# 分析各指标的胜率
print('='*80)
print('\n【单一确认指标胜率分析】\n')
print(f'{"指标":<20} {"数量":<10} {"胜率":<12} {"均收益":<10} {"提升"}')
print('-' * 70)

# 基准胜率
baseline_winrate = winrate_first
baseline_return = first_day['return'].mean()

indicators = [
    ('阳线', df_indicators['is_bullish'] == True),
    ('反包形态', df_indicators['is_engulfing'] == True),
    ('放量', df_indicators['is_volume_surge'] == True),
    ('收盘强势(>80%)', df_indicators['close_position'] > 80),
    ('实体大(>3%)', df_indicators['body_size'] > 3),
    ('涨幅>5%', df_indicators['pct_chg'] > 5),
    ('涨幅>8%', df_indicators['pct_chg'] > 8),
    ('RSI<30', df_indicators['rsi_min'] < 30),
    ('RSI<40', df_indicators['rsi_min'] < 40),
    ('MACD金叉', df_indicators['is_macd_golden'] == True),
    ('MACD底背离', df_indicators['is_macd_divergence'] == True),
    ('双均线支撑', df_indicators['support_count'] >= 2),
    ('跳空高开', df_indicators['is_gap_up'] == True),
    ('突破10日高点', df_indicators['is_breakout'] == True),
    ('强势收盘', df_indicators['is_strong_close'] == True),
]

for name, condition in indicators:
    segment = df_indicators[condition]
    if len(segment) >= 10:
        win = segment['profit'].sum()
        winrate = win / len(segment) * 100
        avg_return = segment['return'].mean()
        lift = winrate - baseline_winrate
        mark = '✓' if winrate > 80.3 else ''
        print(f'{name:<20} {len(segment):<10} {winrate:<12.1f}% {avg_return:<10.1f}% {lift:+.1f}pp {mark}')

print('\n\n' + '='*80)
print('\n【组合确认指标胜率分析】\n')
print(f'{"组合条件":<35} {"数量":<10} {"胜率":<12} {"均收益":<10} {"提升"}')
print('-' * 80)

# 组合指标
combinations = [
    ('反包+放量', (df_indicators['is_engulfing']) & (df_indicators['is_volume_surge'])),
    ('反包+收盘强势', (df_indicators['is_engulfing']) & (df_indicators['close_position'] > 80)),
    ('反包+涨幅>5%', (df_indicators['is_engulfing']) & (df_indicators['pct_chg'] > 5)),
    ('放量+收盘强势', (df_indicators['is_volume_surge']) & (df_indicators['close_position'] > 80)),
    ('放量+涨幅>5%', (df_indicators['is_volume_surge']) & (df_indicators['pct_chg'] > 5)),
    ('收盘强势+涨幅>5%', (df_indicators['close_position'] > 80) & (df_indicators['pct_chg'] > 5)),
    ('反包+放量+收盘强势', 
     (df_indicators['is_engulfing']) & (df_indicators['is_volume_surge']) & (df_indicators['close_position'] > 80)),
    ('反包+放量+涨幅>5%', 
     (df_indicators['is_engulfing']) & (df_indicators['is_volume_surge']) & (df_indicators['pct_chg'] > 5)),
    ('放量+收盘强势+双均线', 
     (df_indicators['is_volume_surge']) & (df_indicators['close_position'] > 80) & (df_indicators['support_count'] >= 2)),
    ('反包+放量+双均线', 
     (df_indicators['is_engulfing']) & (df_indicators['is_volume_surge']) & (df_indicators['support_count'] >= 2)),
    ('涨幅>8%+放量+收盘强势', 
     (df_indicators['pct_chg'] > 8) & (df_indicators['is_volume_surge']) & (df_indicators['close_position'] > 80)),
    ('反包+放量+涨幅>8%', 
     (df_indicators['is_engulfing']) & (df_indicators['is_volume_surge']) & (df_indicators['pct_chg'] > 8)),
    ('反包+放量+收盘强势+双均线', 
     (df_indicators['is_engulfing']) & (df_indicators['is_volume_surge']) & 
     (df_indicators['close_position'] > 80) & (df_indicators['support_count'] >= 2)),
]

for name, condition in combinations:
    segment = df_indicators[condition]
    if len(segment) >= 5:
        win = segment['profit'].sum()
        winrate = win / len(segment) * 100
        avg_return = segment['return'].mean()
        lift = winrate - baseline_winrate
        mark = '✓✓' if winrate > 80.3 else ('✓' if winrate > 70 else '')
        print(f'{name:<35} {len(segment):<10} {winrate:<12.1f}% {avg_return:<10.1f}% {lift:+.1f}pp {mark}')

# 找出最优组合
print('\n\n' + '='*80)
print('\n【最优确认组合】\n')

best_combinations = []

for name, condition in combinations:
    segment = df_indicators[condition]
    if len(segment) >= 5:
        win = segment['profit'].sum()
        winrate = win / len(segment) * 100
        avg_return = segment['return'].mean()
        
        if winrate > 80.3:  # 超过第4天胜率
            best_combinations.append({
                'name': name,
                'count': len(segment),
                'winrate': winrate,
                'return': avg_return,
            })

if len(best_combinations) > 0:
    best_combinations.sort(key=lambda x: x['winrate'], reverse=True)
    
    print(f'胜率超过80.3%（第4天基准）的组合：\n')
    print(f'{"组合":<35} {"数量":<10} {"胜率":<12} {"均收益":<10}')
    print('-' * 70)
    
    for combo in best_combinations[:10]:
        print(f'{combo["name"]:<35} {combo["count"]:<10} {combo["winrate"]:<12.1f}% {combo["return"]:<10.1f}%')
    
    print(f'\n最优组合：{best_combinations[0]["name"]}')
    print(f'胜率：{best_combinations[0]["winrate"]:.1f}%（超过第4天{(best_combinations[0]["winrate"]-80.3):.1f}pp）')
    print(f'样本数：{best_combinations[0]["count"]}只')
else:
    print('⚠️ 未找到胜率超过80.3%的组合')

# 保存详细结果
df_indicators.to_csv(r'D:\mystock\solo\multi_factor_picker\vshape_first_day_indicators.csv', index=False, encoding='utf-8-sig')
print(f'\n\n详细结果已保存')
