# -*- coding: utf-8 -*-
"""
V型急跌评分优化v2.11回测验证
对比优化前后评分的胜率和收益差异
"""
import sys
sys.path.insert(0, r'D:\mystock\solo\multi_factor_picker')
sys.path.insert(0, r'D:\mystock\solo')

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import tushare as ts
import os

if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break
pro = ts.pro_api()

print('='*80)
print('V型急跌评分优化v2.11回测验证')
print('='*80)

# 读取历史扫描结果（含二波验证）
cache_files = []
cache_dir = r'D:\mystock\solo\multi_factor_picker\output'
for f in os.listdir(cache_dir):
    if f.startswith('wave2_pattern_') and f.endswith('.csv'):
        if '20260625' in f or '20260626' in f:
            cache_files.append(os.path.join(cache_dir, f))

print(f'\n找到扫描文件：{len(cache_files)}个')

# 合并数据
all_signals = []
for f in cache_files:
    df = pd.read_csv(f)
    all_signals.append(df)

df_all = pd.concat(all_signals, ignore_index=True)
df_all = df_all.drop_duplicates(subset=['ts_code', 'entry_date'])

# 筛选V型急跌
vshape = df_all[df_all['pattern'] == 'V型急跌'].copy()

print(f'\nV型急跌信号数：{len(vshape)}只')
print(f'入场日期范围：{vshape["entry_date"].min()} - {vshape["entry_date"].max()}')

print('\n' + '='*80)
print('\n【第一步：获取实际涨跌数据】\n')

# 获取入场后5日、10日、20日涨跌
results = []
for idx, row in vshape.iterrows():
    ts_code = row['ts_code']
    entry_date = str(row['entry_date'])
    entry_price = row['entry_price']
    
    print(f'  处理 {row["name"]}({ts_code}) 入场{entry_date}...', end=' ')
    
    try:
        # 获取入场后数据
        end_date = (datetime.strptime(entry_date, '%Y%m%d') + timedelta(days=30)).strftime('%Y%m%d')
        df_daily = pro.daily(ts_code=ts_code, start_date=entry_date, end_date=end_date)
        
        if df_daily.empty:
            print('无数据')
            continue
        
        df_daily = df_daily.sort_values('trade_date')
        
        # 计算持有收益
        if len(df_daily) >= 5:
            ret_5d = (df_daily.iloc[4]['close'] - entry_price) / entry_price * 100
        else:
            ret_5d = None
        
        if len(df_daily) >= 10:
            ret_10d = (df_daily.iloc[9]['close'] - entry_price) / entry_price * 100
        else:
            ret_10d = None
        
        if len(df_daily) >= 20:
            ret_20d = (df_daily.iloc[19]['close'] - entry_price) / entry_price * 100
        else:
            ret_20d = None
        
        # 最大收益
        if len(df_daily) > 0:
            max_price = df_daily['close'].max()
            max_ret = (max_price - entry_price) / entry_price * 100
        else:
            max_ret = None
        
        result = {
            'ts_code': ts_code,
            'name': row['name'],
            'entry_date': entry_date,
            'entry_price': entry_price,
            'old_score': row['score'],
            'wave1_gain': row['wave1_gain'],
            'pullback_pct': row['pullback_pct'],
            'vol_ratio': row['vol_ratio'],
            'ret_5d': ret_5d,
            'ret_10d': ret_10d,
            'ret_20d': ret_20d,
            'max_ret': max_ret
        }
        results.append(result)
        print(f'5日{ret_5d:+.1f}%, 10日{ret_10d:+.1f}%, 最大{max_ret:+.1f}%')
        
    except Exception as e:
        print(f'错误: {e}')
        continue

df_results = pd.DataFrame(results)

print('\n' + '='*80)
print('\n【第二步：计算新评分】\n')

def calc_new_score(row):
    """模拟v2.11新评分"""
    old_score = row['old_score']
    wave1_gain = row['wave1_gain']
    pullback_pct = row['pullback_pct']
    vol_ratio = row['vol_ratio']
    
    new_score = old_score
    
    # 新增加分项
    if 50 <= wave1_gain <= 60:
        new_score += 3
    if 18 <= pullback_pct < 22:
        new_score += 3
    if vol_ratio > 1.2:
        new_score += 5
    
    # 新增扣分项
    if wave1_gain > 60:
        new_score -= 5
    if pullback_pct > 25:
        new_score -= 5
    if vol_ratio < 0.8:
        new_score -= 3
    
    return new_score

df_results['new_score'] = df_results.apply(calc_new_score, axis=1)

print(f'计算完成，评分变化：')
for idx, row in df_results.iterrows():
    change = row['new_score'] - row['old_score']
    sign = '+' if change >= 0 else ''
    print(f'  {row["name"]}: {row["old_score"]:.0f}分→{row["new_score"]:.0f}分({sign}{change:.0f})')

print('\n' + '='*80)
print('\n【第三步：对比优化前后效果】\n')

# 计算胜率和收益
def calc_stats(df, score_col='old_score', threshold=40):
    """计算胜率和收益"""
    filtered = df[df[score_col] >= threshold].copy()
    
    if len(filtered) == 0:
        return None
    
    # 胜率计算（以10日收益为准）
    valid = filtered[filtered['ret_10d'].notna()]
    if len(valid) == 0:
        return None
    
    win_rate = (valid['ret_10d'] > 0).sum() / len(valid) * 100
    avg_ret = valid['ret_10d'].mean()
    max_ret = valid['max_ret'].mean()
    
    return {
        'threshold': threshold,
        'count': len(filtered),
        'win_rate_10d': win_rate,
        'avg_ret_10d': avg_ret,
        'avg_max_ret': max_ret
    }

print('【优化前评分】')
for threshold in [35, 40, 45]:
    stats = calc_stats(df_results, 'old_score', threshold)
    if stats:
        print(f'  ≥{threshold}分: {stats["count"]}只, 胜率{stats["win_rate_10d"]:.1f}%, 均10日{stats["avg_ret_10d"]:+.1f}%, 均最大{stats["avg_max_ret"]:+.1f}%')

print('\n【优化后评分】')
for threshold in [35, 40, 45]:
    stats = calc_stats(df_results, 'new_score', threshold)
    if stats:
        print(f'  ≥{threshold}分: {stats["count"]}只, 胜率{stats["win_rate_10d"]:.1f}%, 均10日{stats["avg_ret_10d"]:+.1f}%, 均最大{stats["avg_max_ret"]:+.1f}%')

print('\n' + '='*80)
print('\n【第四步：评分分档胜率对比】\n')

# 按评分分档
print('【优化前评分分档】')
df_results['old_score_bin'] = pd.cut(df_results['old_score'], bins=[0, 30, 35, 40, 50], labels=['<30', '30-35', '35-40', '≥40'])
for bin_label in ['<30', '30-35', '35-40', '≥40']:
    subset = df_results[df_results['old_score_bin'] == bin_label]
    if len(subset) > 0:
        valid = subset[subset['ret_10d'].notna()]
        if len(valid) > 0:
            win_rate = (valid['ret_10d'] > 0).sum() / len(valid) * 100
            avg_ret = valid['ret_10d'].mean()
            print(f'  {bin_label}分: {len(subset)}只, 胜率{win_rate:.1f}%, 均10日{avg_ret:+.1f}%')

print('\n【优化后评分分档】')
df_results['new_score_bin'] = pd.cut(df_results['new_score'], bins=[0, 30, 35, 40, 50], labels=['<30', '30-35', '35-40', '≥40'])
for bin_label in ['<30', '30-35', '35-40', '≥40']:
    subset = df_results[df_results['new_score_bin'] == bin_label]
    if len(subset) > 0:
        valid = subset[subset['ret_10d'].notna()]
        if len(valid) > 0:
            win_rate = (valid['ret_10d'] > 0).sum() / len(valid) * 100
            avg_ret = valid['ret_10d'].mean()
            print(f'  {bin_label}分: {len(subset)}只, 胜率{win_rate:.1f}%, 均10日{avg_ret:+.1f}%')

print('\n' + '='*80)
print('\n【第五步：详细信号对比】\n')

print(f'{"名称":<10} {"代码":<12} {"旧分":>5} {"新分":>5} {"10日%":>7} {"最大%":>7} {"一波%":>6} {"回踩%":>6} {"量比":>5}')
print('-'*85)
for idx, row in df_results.sort_values('new_score', ascending=False).iterrows():
    ret10 = f'{row["ret_10d"]:+.1f}' if pd.notna(row['ret_10d']) else 'N/A'
    maxr = f'{row["max_ret"]:+.1f}' if pd.notna(row['max_ret']) else 'N/A'
    print(f'{row["name"]:<10} {row["ts_code"]:<12} {row["old_score"]:>5.0f} {row["new_score"]:>5.0f} {ret10:>7} {maxr:>7} {row["wave1_gain"]:>5.1f}% {row["pullback_pct"]:>5.1f}% {row["vol_ratio"]:>5.2f}')

print('\n' + '='*80)
print('\n【回测结论】\n')

# 统计优化效果
old_high = df_results[df_results['old_score'] >= 40]
new_high = df_results[df_results['new_score'] >= 40]

if len(old_high) > 0 and len(new_high) > 0:
    old_valid = old_high[old_high['ret_10d'].notna()]
    new_valid = new_high[new_high['ret_10d'].notna()]
    
    if len(old_valid) > 0 and len(new_valid) > 0:
        old_win = (old_valid['ret_10d'] > 0).sum() / len(old_valid) * 100
        new_win = (new_valid['ret_10d'] > 0).sum() / len(new_valid) * 100
        
        old_avg = old_valid['ret_10d'].mean()
        new_avg = new_valid['ret_10d'].mean()
        
        print(f'优化效果（≥40分信号）：')
        print(f'  优化前：{len(old_high)}只, 胜率{old_win:.1f}%, 均10日{old_avg:+.1f}%')
        print(f'  优化后：{len(new_high)}只, 胜率{new_win:.1f}%, 均10日{new_avg:+.1f}%')
        print(f'  胜率变化：{new_win - old_win:+.1f}pp')
        print(f'  收益变化：{new_avg - old_avg:+.1f}pp')

print('\n' + '='*80)
print('回测完成！')
print('='*80)
