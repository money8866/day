"""V型急跌20260605评分分析 - 低吸日前一天"""
import pandas as pd
import glob
import os

cache_dir = r'D:\mystock\cache_daily'

print('=== V型急跌20260605评分分析（低吸日前一天）===\n')
print('分析目标：对比20260605 vs 20260608的评分差异\n')

# 回测结果
results_0605 = []
results_0608 = []

all_files = glob.glob(f'{cache_dir}/*.csv')
total_files = len(all_files)

print(f'扫描股票数：{total_files}只\n')

for i, file in enumerate(all_files):
    try:
        ts_code = os.path.basename(file).replace('.csv', '')
        
        if not (ts_code.startswith('6') or ts_code.startswith('0') or ts_code.startswith('3')):
            continue
        
        df = pd.read_csv(file, encoding='utf-8')
        df['trade_date'] = df['trade_date'].astype(str)
        df = df.sort_values('trade_date', ascending=True).reset_index(drop=True)
        
        # 检查两个日期是否都存在
        row_0605 = df[df['trade_date'] == '20260605']
        row_0608 = df[df['trade_date'] == '20260608']
        
        if len(row_0605) == 0 or len(row_0608) == 0:
            continue
        
        idx_0605 = row_0605.index[0]
        idx_0608 = row_0608.index[0]
        
        # 找最近60天内的首波涨停（排除最近5天）
        for target_date, target_idx, results in [('20260605', idx_0605, results_0605), ('20260608', idx_0608, results_0608)]:
            lookback_start = max(0, target_idx - 60)
            lookback_end = max(0, target_idx - 5)
            recent = df.loc[lookback_start:lookback_end]
            
            limit_up = recent[recent['pct_chg'] >= 9.4]
            if len(limit_up) == 0:
                continue
            
            wave1_idx = limit_up['pct_chg'].idxmax()
            wave1_close = float(df.loc[wave1_idx, 'close'])
            
            # 找最低点
            pullback_start = wave1_idx + 1
            pullback_end = target_idx
            pullback_data = df.loc[pullback_start:pullback_end]
            
            if len(pullback_data) == 0:
                continue
            
            min_low_idx = pullback_data['low'].idxmin()
            min_low = float(df.loc[min_low_idx, 'low'])
            
            pullback_ratio = min_low / wave1_close
            
            # 只考虑回踩75-100%
            if not (0.75 <= pullback_ratio <= 1.0):
                continue
            
            # 计算反弹天数
            rebound_days = target_idx - min_low_idx
            
            # ===== 新评分（50分制）=====
            score = 0
            
            # F1回踩位置（12分）
            if 0.75 <= pullback_ratio < 0.80:
                score += 12
            elif 0.80 <= pullback_ratio < 0.85:
                score += 10
            elif 0.85 <= pullback_ratio < 0.90:
                score += 8
            else:
                score += 5
            
            # F5反弹时机（15分）
            if rebound_days == 0:
                score += 15
            elif rebound_days == 1:
                score += 12
            elif rebound_days == 2:
                score += 10
            elif rebound_days <= 5:
                score += 8
            elif rebound_days <= 10:
                score += 5
            else:
                score += 0
            
            # F2缩量（10分）
            vol_ma5 = float(df.loc[target_idx-5:target_idx, 'vol'].mean())
            target_row = df.loc[target_idx]
            vol_ratio = float(target_row['vol']) / vol_ma5 if vol_ma5 > 0 else 1
            
            if vol_ratio < 0.5:
                score += 10
            elif vol_ratio < 0.7:
                score += 8
            elif vol_ratio < 1.0:
                score += 6
            else:
                score += 4
            
            # F3 RSI超卖（10分）- 用最低点的RSI
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
            
            if rsi_min < 30:
                score += 10
            elif rsi_min < 40:
                score += 8
            elif rsi_min < 50:
                score += 6
            else:
                score += 4
            
            # F4均线支撑（10分）
            ma60 = float(df.loc[max(0,target_idx-60):target_idx, 'close'].mean())
            ma120 = float(df.loc[max(0,target_idx-120):target_idx, 'close'].mean())
            
            if min_low >= ma60 * 0.98:
                score += 5
            if min_low >= ma120 * 0.98:
                score += 5
            
            # 计算未来收益（持有10天）
            future_start = target_idx + 1
            future_end = min(target_idx + 11, len(df) - 1)
            
            if future_end > future_start:
                future_data = df.loc[future_start:future_end]
                max_price = float(future_data['high'].max())
                buy_price = float(target_row['close'])
                max_return = (max_price / buy_price - 1) * 100
                is_profit = max_return > 5
            else:
                max_return = 0
                is_profit = False
            
            results.append({
                'code': ts_code,
                'date': target_date,
                'score': score,
                'rebound_days': rebound_days,
                'return': max_return,
                'profit': is_profit,
                'pullback': pullback_ratio,
                'rsi_min': rsi_min,
            })
    
    except:
        continue
    
    if (i+1) % 2000 == 0:
        print(f'已扫描 {i+1}/{total_files}...')

print(f'扫描完成\n')

# 转换为DataFrame
df_0605 = pd.DataFrame(results_0605)
df_0608 = pd.DataFrame(results_0608)

print('='*80)
print('\n【20260605评分表现】\n')

if len(df_0605) > 0:
    print(f'总信号数：{len(df_0605)}只\n')
    
    print(f'{"评分区间":<15} {"数量":<10} {"胜率":<12} {"均收益":<10}')
    print('-' * 50)
    
    for threshold in [30, 35, 40, 45]:
        segment = df_0605[df_0605['score'] >= threshold]
        if len(segment) > 0:
            win = segment['profit'].sum()
            winrate = win / len(segment) * 100
            avg_return = segment['return'].mean()
            print(f'{threshold}分以上        {len(segment):<10} {winrate:<12.1f}% {avg_return:<10.1f}%')
    
    # 反弹天数分段
    print('\n\n【反弹天数影响】\n')
    print(f'{"反弹天数":<10} {"数量":<10} {"胜率":<12} {"均收益":<10}')
    print('-' * 50)
    
    for days in [0, 1, 2, 3, 4, 5]:
        segment = df_0605[df_0605['rebound_days'] == days]
        if len(segment) >= 2:
            win = segment['profit'].sum()
            winrate = win / len(segment) * 100
            avg_return = segment['return'].mean()
            note = '✓首日' if days == 0 else ''
            print(f'{days}天       {len(segment):<10} {winrate:<12.1f}% {avg_return:<10.1f}% {note}')

print('\n\n' + '='*80)
print('\n【20260608评分表现】\n')

if len(df_0608) > 0:
    print(f'总信号数：{len(df_0608)}只\n')
    
    print(f'{"评分区间":<15} {"数量":<10} {"胜率":<12} {"均收益":<10}')
    print('-' * 50)
    
    for threshold in [30, 35, 40, 45]:
        segment = df_0608[df_0608['score'] >= threshold]
        if len(segment) > 0:
            win = segment['profit'].sum()
            winrate = win / len(segment) * 100
            avg_return = segment['return'].mean()
            print(f'{threshold}分以上        {len(segment):<10} {winrate:<12.1f}% {avg_return:<10.1f}%')

print('\n\n' + '='*80)
print('\n【两日对比】\n')

if len(df_0605) > 0 and len(df_0608) > 0:
    # 高分信号对比
    high_0605 = df_0605[df_0605['score'] >= 40]
    high_0608 = df_0608[df_0608['score'] >= 40]
    
    print(f'{"日期":<15} {"总信号":<10} {"40分以上":<10} {"高分占比":<10}')
    print('-' * 50)
    print(f'20260605       {len(df_0605):<10} {len(high_0605):<10} {len(high_0605)/len(df_0605)*100:.1f}%')
    print(f'20260608       {len(df_0608):<10} {len(high_0608):<10} {len(high_0608)/len(df_0608)*100:.1f}%')
    
    # 找共同股票
    common = set(df_0605['code']) & set(df_0608['code'])
    print(f'\n共同信号数：{len(common)}只')
    
    if len(common) > 0:
        print('\n\n【共同信号评分变化】\n')
        print(f'{"代码":<12} {"0605评分":<10} {"0608评分":<10} {"变化":<10}')
        print('-' * 50)
        
        for code in list(common)[:20]:
            score_0605 = df_0605[df_0605['code'] == code]['score'].values[0]
            score_0608 = df_0608[df_0608['code'] == code]['score'].values[0]
            change = score_0608 - score_0605
            mark = '✓光智' if code == '300489.SZ' else ''
            print(f'{code:<12} {score_0605:<10.0f} {score_0608:<10.0f} {change:+.0f}分    {mark}')

# 保存结果
df_0605.to_csv(r'D:\mystock\solo\multi_factor_picker\vshape_0605_results.csv', index=False, encoding='utf-8-sig')
df_0608.to_csv(r'D:\mystock\solo\multi_factor_picker\vshape_0608_results.csv', index=False, encoding='utf-8-sig')
print(f'\n\n结果已保存')
