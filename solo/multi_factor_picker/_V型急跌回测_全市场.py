"""V型急跌回测 - 扩大股池"""
import pandas as pd
import glob
import os

cache_dir = r'D:\mystock\cache_daily'

print('=== V型急跌新评分模型回测（全市场） ===\n')

# 回测结果
results_old = []
results_new = []

all_files = glob.glob(f'{cache_dir}/*.csv')
total_files = len(all_files)

print(f'扫描股票数：{total_files}只\n')

for i, file in enumerate(all_files):
    try:
        ts_code = os.path.basename(file).replace('.csv', '')
        
        # 只处理A股
        if not (ts_code.startswith('6') or ts_code.startswith('0') or ts_code.startswith('3')):
            continue
        
        df = pd.read_csv(file, encoding='utf-8')
        df['trade_date'] = df['trade_date'].astype(str)
        df = df.sort_values('trade_date', ascending=True).reset_index(drop=True)
        
        # 找到6月8日（如果存在）
        target_row = df[df['trade_date'] == '20260608']
        if len(target_row) == 0:
            continue
        
        target_idx = target_row.index[0]
        
        # 找最近60天内的首波涨停（排除最近5天）
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
        
        # ===== 旧评分（40分制）=====
        score_old = 0
        
        # F1（10分）
        if 0.80 <= pullback_ratio <= 0.85:
            score_old += 10
        elif 0.85 < pullback_ratio <= 0.90:
            score_old += 8
        elif pullback_ratio < 0.80:
            score_old += 12
        else:
            score_old += 6
        
        # F2（10分）
        vol_ma5 = float(df.loc[target_idx-5:target_idx, 'vol'].mean())
        vol_ratio = float(target_row.iloc[0]['vol']) / vol_ma5 if vol_ma5 > 0 else 1
        
        if vol_ratio < 0.5:
            score_old += 10
        elif vol_ratio < 0.7:
            score_old += 8
        elif vol_ratio < 1.0:
            score_old += 6
        else:
            score_old += 4
        
        # F3（10分）
        if target_idx >= 14:
            recent_14 = df.loc[target_idx-13:target_idx, 'close']
            gains = recent_14.diff()
            gains_pos = gains[gains > 0]
            losses = -gains[gains < 0]
            avg_gain = gains_pos.mean() if len(gains_pos) > 0 else 0
            avg_loss = losses.mean() if len(losses) > 0 else 0.01
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        else:
            rsi = 50
        
        if rsi < 30:
            score_old += 10
        elif rsi < 40:
            score_old += 8
        elif rsi < 50:
            score_old += 6
        else:
            score_old += 4
        
        # F4（10分）
        ma60 = float(df.loc[max(0,target_idx-60):target_idx, 'close'].mean())
        ma120 = float(df.loc[max(0,target_idx-120):target_idx, 'close'].mean())
        
        if min_low >= ma60 * 0.98:
            score_old += 5
        if min_low >= ma120 * 0.98:
            score_old += 5
        
        # ===== 新评分（50分制）=====
        score_new = 0
        
        # F1（12分）
        if 0.75 <= pullback_ratio < 0.80:
            score_new += 12
        elif 0.80 <= pullback_ratio < 0.85:
            score_new += 10
        elif 0.85 <= pullback_ratio < 0.90:
            score_new += 8
        else:
            score_new += 5
        
        # F5反弹时机（15分）🔥新增
        if rebound_days == 0:
            score_new += 15
        elif rebound_days == 1:
            score_new += 12
        elif rebound_days == 2:
            score_new += 10
        elif rebound_days <= 5:
            score_new += 8
        elif rebound_days <= 10:
            score_new += 5
        else:
            score_new += 0
        
        # F2（10分）
        if vol_ratio < 0.5:
            score_new += 10
        elif vol_ratio < 0.7:
            score_new += 8
        elif vol_ratio < 1.0:
            score_new += 6
        else:
            score_new += 4
        
        # F3（10分）- 用最低点的RSI
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
            score_new += 10
        elif rsi_min < 40:
            score_new += 8
        elif rsi_min < 50:
            score_new += 6
        else:
            score_new += 4
        
        # F4（10分）
        if min_low >= ma60 * 0.98:
            score_new += 5
        if min_low >= ma120 * 0.98:
            score_new += 5
        
        # 计算未来收益（持有10天）
        future_start = target_idx + 1
        future_end = min(target_idx + 11, len(df) - 1)
        
        if future_end > future_start:
            future_data = df.loc[future_start:future_end]
            max_price = float(future_data['high'].max())
            buy_price = float(target_row.iloc[0]['close'])
            max_return = (max_price / buy_price - 1) * 100
            is_profit = max_return > 5
        else:
            max_return = 0
            is_profit = False
        
        results_old.append({
            'code': ts_code,
            'score': score_old,
            'rebound_days': rebound_days,
            'return': max_return,
            'profit': is_profit,
        })
        
        results_new.append({
            'code': ts_code,
            'score': score_new,
            'rebound_days': rebound_days,
            'return': max_return,
            'profit': is_profit,
        })
    
    except:
        continue
    
    if (i+1) % 1000 == 0:
        print(f'已扫描 {i+1}/{total_files}，发现{len(results_old)}只V型急跌信号...')

print(f'\n扫描完成，发现{len(results_old)}只V型急跌信号')

# 转换为DataFrame
df_old = pd.DataFrame(results_old)
df_new = pd.DataFrame(results_new)

# 统计分析
print('\n' + '='*80)
print('\n【胜率对比分析】\n')

print(f'{"评分体系":<15} {"总信号":<10} {"平均分":<10} {"胜率":<12} {"均收益":<10}')
print('-' * 60)

# 旧评分>=20分
top_old = df_old[df_old['score'] >= 20]
win_old = top_old['profit'].sum() if len(top_old) > 0 else 0
winrate_old = win_old / len(top_old) * 100 if len(top_old) > 0 else 0
avg_return_old = top_old['return'].mean() if len(top_old) > 0 else 0

print(f'旧评分(≥20分)     {len(top_old):<10} {top_old["score"].mean() if len(top_old)>0 else 0:<10.1f} {winrate_old:<12.1f}% {avg_return_old:<10.1f}%')

# 新评分>=25分
top_new = df_new[df_new['score'] >= 25]
win_new = top_new['profit'].sum() if len(top_new) > 0 else 0
winrate_new = win_new / len(top_new) * 100 if len(top_new) > 0 else 0
avg_return_new = top_new['return'].mean() if len(top_new) > 0 else 0

print(f'新评分(≥25分)     {len(top_new):<10} {top_new["score"].mean() if len(top_new)>0 else 0:<10.1f} {winrate_new:<12.1f}% {avg_return_new:<10.1f}%')

# 新评分+首日反弹
top_new_first = df_new[(df_new['score'] >= 30) & (df_new['rebound_days'] == 0)]
win_new_first = top_new_first['profit'].sum() if len(top_new_first) > 0 else 0
winrate_new_first = win_new_first / len(top_new_first) * 100 if len(top_new_first) > 0 else 0
avg_return_new_first = top_new_first['return'].mean() if len(top_new_first) > 0 else 0

print(f'新评分+首日反弹   {len(top_new_first):<10} {top_new_first["score"].mean() if len(top_new_first)>0 else 0:<10.1f} {winrate_new_first:<12.1f}% {avg_return_new_first:<10.1f}%')

# 胜率提升
print('\n\n【胜率提升】')
print('-' * 60)
if winrate_old > 0 and winrate_new > 0:
    print(f'旧评分 → 新评分：{winrate_old:.1f}% → {winrate_new:.1f}%（+{winrate_new-winrate_old:.1f}pp）')
if winrate_new_first > 0:
    print(f'首日反弹优势：{winrate_new_first:.1f}%（+{winrate_new_first-winrate_old:.1f}pp）')

# 反弹天数分段
print('\n\n【反弹天数影响】')
print('-' * 60)
print(f'{"反弹天数":<10} {"数量":<10} {"胜率":<12} {"均收益":<10}')
print('-' * 60)

for days in [0, 1, 2, 3, 4, 5]:
    segment = df_new[df_new['rebound_days'] == days]
    if len(segment) >= 2:
        win_seg = segment['profit'].sum()
        winrate_seg = win_seg / len(segment) * 100
        avg_return_seg = segment['return'].mean()
        note = '✓首日' if days == 0 else ''
        print(f'{days}天       {len(segment):<10} {winrate_seg:<12.1f}% {avg_return_seg:<10.1f}% {note}')

# 保存详细结果
df_new.to_csv(r'D:\mystock\solo\multi_factor_picker\vshape_backtest_results.csv', index=False, encoding='utf-8-sig')
print(f'\n\n详细结果已保存：vshape_backtest_results.csv')
