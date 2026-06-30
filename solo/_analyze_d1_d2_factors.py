# -*- coding: utf-8 -*-
"""计算每个信号的D1、D2收益，并分析影响因子"""
import os, time, pandas as pd, numpy as np
from datetime import datetime, timedelta

csv_path = r'D:\mystock\solo\trend_feature_output\entry_precision_20260629_224320_qualified.csv'
df = pd.read_csv(csv_path, dtype={'ts_code': str})
print(f'CSV共 {len(df)} 条信号')

# === 初始化Tushare ===
try:
    import tushare as ts
    from dotenv import load_dotenv
    load_dotenv(r'D:\mystock\config\.env')
    ts.set_token(os.getenv('TUSHARE_TOKEN'))
    pro = ts.pro_api()
    print('Tushare 初始化成功')
except Exception as e:
    print(f'Tushare 初始化失败: {e}')
    exit(1)

# === 获取每个信号的D1、D2收盘价 ===
print('\n获取D1、D2收益数据...')
results = []

# 按股票分组，批量获取日线，减少API调用
grouped = df.groupby('ts_code')
total = len(grouped)
cnt = 0

for ts_code, group in grouped:
    ts_code = str(ts_code).strip()
    cnt += 1
    
    # 获取该股票从最早信号日到今天的日线
    min_date = str(int(group['signal_date'].min()))
    # 确保min_date格式正确
    if len(min_date) == 8:
        start = min_date
    else:
        start = f'{min_date[:4]}{min_date[4:6]}{min_date[6:8]}' if len(min_date) == 6 else min_date
    
    try:
        # 获取日线数据
        daily = pro.daily(ts_code=ts_code, start_date=start)
        if daily is None or daily.empty:
            if cnt % 20 == 0:
                print(f'  已处理 {cnt}/{total} 只（无数据: {ts_code}）...')
            time.sleep(0.06)
            continue
        
        # 按日期排序
        daily = daily.sort_values('trade_date')
        daily['trade_date'] = daily['trade_date'].astype(str)
        
        # 为每个信号日计算D1、D2收益
        for _, row in group.iterrows():
            sig_date = str(int(row['signal_date']))
            
            # 找到信号日在daily中的索引
            sig_idx = daily[daily['trade_date'] == sig_date].index
            if len(sig_idx) == 0:
                continue
            sig_idx = sig_idx[0]
            sig_close = daily.loc[sig_idx, 'close']
            
            # D1：信号日后第1个交易日
            if sig_idx + 1 < len(daily):
                d1_close = daily.loc[sig_idx + 1, 'close']
                d1_ret = (d1_close - sig_close) / sig_close * 100
            else:
                d1_ret = None
            
            # D2：信号日后第2个交易日
            if sig_idx + 2 < len(daily):
                d2_close = daily.loc[sig_idx + 2, 'close']
                d2_ret = (d2_close - sig_close) / sig_close * 100
            else:
                d2_ret = None
            
            results.append({
                'ts_code': ts_code,
                'signal_date': sig_date,
                'entry_score': int(row['entry_score']),
                'consecutive_up': int(row['consecutive_up']),
                'pct_chg': float(row['pct_chg']),
                'vol_ratio': float(row['vol_ratio']),
                'rsi6': float(row['rsi6']),
                'return_1d': float(row['return_1d']),
                'sig_close': sig_close,
                'd1_ret': d1_ret,
                'd2_ret': d2_ret,
            })
        
    except Exception as e:
        print(f'  处理 {ts_code} 失败: {e}')
    
    if cnt % 20 == 0:
        print(f'  已处理 {cnt}/{total} 只...')
    time.sleep(0.06)

print(f'\n有效样本: {len(results)} 条')

if len(results) == 0:
    print('无有效数据，退出。')
    exit(1)

# === 保存原始数据 ===
rdf = pd.DataFrame(results)
out_csv = r'D:\mystock\solo\trend_feature_output\signal_d1_d2_ret_20260630.csv'
rdf.to_csv(out_csv, index=False, encoding='utf-8-sig')
print(f'已保存: {out_csv}')

# === 分析D1、D2收益的影响因子 ===
print('\n' + '=' * 70)
print('【D1收益的影响因子分析】')
print('=' * 70)

# 去除D1为None的样本
d1_df = rdf.dropna(subset=['d1_ret']).reset_index(drop=True)
print(f'D1有效样本: {len(d1_df)} 条')
print(f'D1平均收益: {d1_df["d1_ret"].mean():.2f}%')
print(f'D1收益>0占比: {(d1_df["d1_ret"]>0).sum()/len(d1_df)*100:.0f}%')
print()

# 按评分分档
print('--- 按评分分档 ---')
for lo, hi in [(0,59),(60,69),(70,79),(80,100)]:
    sub = d1_df[(d1_df['entry_score']>=lo) & (d1_df['entry_score']<=hi)]
    if len(sub) >= 1:
        print(f'  评分{lo}-{hi}: {len(sub)}条  D1均值{sub["d1_ret"].mean():+.2f}%  D1正收益占比{(sub["d1_ret"]>0).sum()/len(sub)*100:.0f}%')

print()
print('--- 按RSI6分档 ---')
for label, cond in [
    ('RSI6 < 50', d1_df['rsi6']<50),
    ('50 <= RSI6 < 70', (d1_df['rsi6']>=50) & (d1_df['rsi6']<70)),
    ('RSI6 >= 70', d1_df['rsi6']>=70),
]:
    sub = d1_df[cond]
    if len(sub) >= 1:
        print(f'  {label}: {len(sub)}条  D1均值{sub["d1_ret"].mean():+.2f}%  D1正收益占比{(sub["d1_ret"]>0).sum()/len(sub)*100:.0f}%')

print()
print('--- 按信号日收益(return_1d)分档 ---')
for label, cond in [
    ('return_1d < 0%', d1_df['return_1d']<0),
    ('0% <= return_1d < 3%', (d1_df['return_1d']>=0) & (d1_df['return_1d']<3)),
    ('return_1d >= 3%', d1_df['return_1d']>=3),
]:
    sub = d1_df[cond]
    if len(sub) >= 1:
        print(f'  {label}: {len(sub)}条  D1均值{sub["d1_ret"].mean():+.2f}%  D1正收益占比{(sub["d1_ret"]>0).sum()/len(sub)*100:.0f}%')

print()
print('=' * 70)
print('【D2收益的影响因子分析】')
print('=' * 70)

# 去除D2为None的样本
d2_df = rdf.dropna(subset=['d2_ret']).reset_index(drop=True)
print(f'D2有效样本: {len(d2_df)} 条')
print(f'D2平均收益: {d2_df["d2_ret"].mean():.2f}%')
print(f'D2收益>0占比: {(d2_df["d2_ret"]>0).sum()/len(d2_df)*100:.0f}%')
print()

# 按评分分档
print('--- 按评分分档 ---')
for lo, hi in [(0,59),(60,69),(70,79),(80,100)]:
    sub = d2_df[(d2_df['entry_score']>=lo) & (d2_df['entry_score']<=hi)]
    if len(sub) >= 1:
        print(f'  评分{lo}-{hi}: {len(sub)}条  D2均值{sub["d2_ret"].mean():+.2f}%  D2正收益占比{(sub["d2_ret"]>0).sum()/len(sub)*100:.0f}%')

print()
print('--- 按RSI6分档 ---')
for label, cond in [
    ('RSI6 < 50', d2_df['rsi6']<50),
    ('50 <= RSI6 < 70', (d2_df['rsi6']>=50) & (d2_df['rsi6']<70)),
    ('RSI6 >= 70', d2_df['rsi6']>=70),
]:
    sub = d2_df[cond]
    if len(sub) >= 1:
        print(f'  {label}: {len(sub)}条  D2均值{sub["d2_ret"].mean():+.2f}%  D2正收益占比{(sub["d2_ret"]>0).sum()/len(sub)*100:.0f}%')

print()
print('--- 按信号日收益(return_1d)分档 ---')
for label, cond in [
    ('return_1d < 0%', d2_df['return_1d']<0),
    ('0% <= return_1d < 3%', (d2_df['return_1d']>=0) & (d2_df['return_1d']<3)),
    ('return_1d >= 3%', d2_df['return_1d']>=3),
]:
    sub = d2_df[cond]
    if len(sub) >= 1:
        print(f'  {label}: {len(sub)}条  D2均值{sub["d2_ret"].mean():+.2f}%  D2正收益占比{(sub["d2_ret"]>0).sum()/len(sub)*100:.0f}%')

print()
print('=' * 70)
print('【D1和D2收益的相关系数】')
print('=' * 70)
if len(d1_df) > 0 and len(d2_df) > 0:
    # 合并D1和D2数据
    merged = pd.merge(d1_df[['ts_code', 'signal_date', 'd1_ret']], 
                      d2_df[['ts_code', 'signal_date', 'd2_ret']], 
                      on=['ts_code', 'signal_date'], how='inner')
    if len(merged) > 0:
        corr = merged['d1_ret'].corr(merged['d2_ret'])
        print(f'D1收益与D2收益的相关系数: {corr:.3f}')
        print(f'（样本数: {len(merged)}）')
    else:
        print('无重叠样本')
else:
    print('数据不足')

print()
print('完成。')
