# -*- coding: utf-8 -*-
"""分析6/26和6/29信号股中涨/跌组的规律差异"""
import pandas as pd

csv_path = r'D:\mystock\solo\trend_feature_output\entry_precision_20260629_224320_qualified.csv'
df = pd.read_csv(csv_path, dtype={'ts_code': str})

# 今日实时涨幅
today_pct_map = {
    '688170.SH': +5.02, '688772.SH': -1.99, '300801.SZ': -6.58,
    '603156.SH': -0.53, '300395.SZ': +2.58, '300093.SZ': -4.15,
    '300480.SZ': +7.78, '300236.SZ': -0.39, '603281.SH': -5.47,
    '601066.SH': -0.51, '688386.SH': -1.89, '688629.SH': +3.18,
    '688235.SH': -1.52, '688690.SH': -4.96, '688503.SH': +8.53,
    '688758.SH': -2.17, '688135.SH': +1.98, '688222.SH': -3.87,
    '688230.SH': +2.90, '688802.SH': +3.96, '688187.SH': +4.77,
}

target_dates = ['20260626', '20260629']
sub = df[df['signal_date'].astype(str).isin(target_dates)].copy()
sub['today_pct'] = sub['ts_code'].map(today_pct_map)
sub = sub.dropna(subset=['today_pct']).reset_index(drop=True)

print(f'样本数: {len(sub)} 只  上涨: {(sub["today_pct"]>0).sum()}  下跌: {(sub["today_pct"]<=0).sum()}')
print()

# === 分组统计 ===
up = sub[sub['today_pct'] > 0].reset_index(drop=True)
dn = sub[sub['today_pct'] <= 0].reset_index(drop=True)

def stat(df, label):
    print(f'【{label}】共{len(df)}只')
    print(f'  评分均值: {df["entry_score"].mean():.1f}  中位数: {df["entry_score"].median():.0f}')
    print(f'  信号日收益(return_1d)均值: {df["return_1d"].mean():.2f}%  中位数: {df["return_1d"].median():.2f}%')
    print(f'  return_5d均值: {df["return_5d"].mean():.2f}%')
    print(f'  return_10d均值: {df["return_10d"].mean():.2f}%')
    print(f'  连涨天数(consecutive_up)均值: {df["consecutive_up"].mean():.1f}  中位数: {df["consecutive_up"].median():.0f}')
    print(f'  量比(vol_ratio)均值: {df["vol_ratio"].mean():.2f}  中位数: {df["vol_ratio"].median():.2f}')
    print(f'  RSI6均值: {df["rsi6"].mean():.1f}  中位数: {df["rsi6"].median():.1f}')
    print(f'  涨幅(pct_chg)均值: {df["pct_chg"].mean():.2f}%')
    # 信号类型
    print(f'  信号类型: {df["signal_type"].value_counts().to_dict()}')
    print()

stat(up, '上涨组 🟢')
stat(dn, '下跌组 🔴')

# === 关键差异 ===
print('='*60)
print('【关键差异（上涨组 − 下跌组）】')
diffs = {
    '评分':       up['entry_score'].mean() - dn['entry_score'].mean(),
    '信号日收益':  up['return_1d'].mean() - dn['return_1d'].mean(),
    'return_5d':  up['return_5d'].mean() - dn['return_5d'].mean(),
    'return_10d': up['return_10d'].mean() - dn['return_10d'].mean(),
    '连涨天数':    up['consecutive_up'].mean() - dn['consecutive_up'].mean(),
    '量比':       up['vol_ratio'].mean() - dn['vol_ratio'].mean(),
    'RSI6':      up['rsi6'].mean() - dn['rsi6'].mean(),
    '涨幅pct_chg':up['pct_chg'].mean() - dn['pct_chg'].mean(),
}
for k, v in diffs.items():
    print(f'  {k}: {v:+.2f}')

print()
print('='*60)
print('【按评分分档的今日涨幅】')
for lo, hi in [(0,59),(60,69),(70,79),(80,100)]:
    bin_df = sub[(sub['entry_score']>=lo)&(sub['entry_score']<=hi)]
    if len(bin_df)>=1:
        avg = bin_df['today_pct'].mean()
        up_rate = (bin_df['today_pct']>0).sum()/len(bin_df)*100
        print(f'  评分{lo}-{hi}: {len(bin_df)}只  今日均值{avg:+.2f}%  上涨占比{up_rate:.0f}%')

print()
print('='*60)
print('【按信号日收益(return_1d)分档】')
for label, cond in [
    ('return_1d < 0%',  sub['return_1d']<0),
    ('0% <= return_1d < 3%', (sub['return_1d']>=0)&(sub['return_1d']<3)),
    ('3% <= return_1d < 5%', (sub['return_1d']>=3)&(sub['return_1d']<5)),
    ('return_1d >= 5%', sub['return_1d']>=5),
]:
    bin_df = sub[cond]
    if len(bin_df)>=1:
        avg = bin_df['today_pct'].mean()
        up_rate = (bin_df['today_pct']>0).sum()/len(bin_df)*100
        print(f'  {label}: {len(bin_df)}只  今日均值{avg:+.2f}%  上涨占比{up_rate:.0f}%')

print()
print('='*60)
print('【按连涨天数(consecutive_up)】')
for ud in sorted(sub['consecutive_up'].unique()):
    bin_df = sub[sub['consecutive_up']==ud]
    avg = bin_df['today_pct'].mean()
    up_rate = (bin_df['today_pct']>0).sum()/len(bin_df)*100
    print(f'  连涨{ud}天: {len(bin_df)}只  今日均值{avg:+.2f}%  上涨占比{up_rate:.0f}%')

print()
print('='*60)
print('【按RSI6分档】')
for label, cond in [
    ('RSI6 < 50（低位）', sub['rsi6']<50),
    ('50 <= RSI6 < 70', (sub['rsi6']>=50)&(sub['rsi6']<70)),
    ('RSI6 >= 70（超买）', sub['rsi6']>=70),
]:
    bin_df = sub[cond]
    if len(bin_df)>=1:
        avg = bin_df['today_pct'].mean()
        up_rate = (bin_df['today_pct']>0).sum()/len(bin_df)*100
        print(f'  {label}: {len(bin_df)}只  今日均值{avg:+.2f}%  上涨占比{up_rate:.0f}%')

print()
print('='*60)
print('【按量比(vol_ratio)分档】')
for label, cond in [
    ('量比 < 1.0（缩量）', sub['vol_ratio']<1.0),
    ('1.0 <= 量比 < 2.0', (sub['vol_ratio']>=1.0)&(sub['vol_ratio']<2.0)),
    ('量比 >= 2.0（放量）', sub['vol_ratio']>=2.0),
]:
    bin_df = sub[cond]
    if len(bin_df)>=1:
        avg = bin_df['today_pct'].mean()
        up_rate = (bin_df['today_pct']>0).sum()/len(bin_df)*100
        print(f'  {label}: {len(bin_df)}只  今日均值{avg:+.2f}%  上涨占比{up_rate:.0f}%')

print()
print('完成。')
