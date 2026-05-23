# -*- coding: utf-8 -*-
import pandas as pd, sqlite3, os, struct
from datetime import datetime, timedelta

BASE_DIR = r'C:\Users\kongx\mystock'
TDX_SH = r'C:\new_tdx\vipdoc\sh\lday'
TDX_SZ = r'C:\new_tdx\vipdoc\sz\lday'

df = pd.read_csv(r'C:\Users\kongx\mystock\backtest_v4_local_halfyear.csv', encoding='utf-8-sig')

# 1. Monthly breakdown
df['month'] = (df['date'].astype(str).str[:6])
monthly = df.groupby('month').agg(
    trades=('return_pct','count'),
    avg_ret=('return_pct','mean'),
    win_rate=('return_pct', lambda x: (x>0).mean()*100),
    stopped=('stopped','sum'),
    big_win=('return_pct', lambda x: (x>10).sum()),
).reset_index()
monthly['cum'] = (1 + monthly['avg_ret']/100).cumprod() - 1

print('='*70)
print('Monthly breakdown:')
print(f"{'Month':>6} {'Trades':>6} {'AvgRet':>8} {'WinRate':>8} {'Stopped':>8} {'BigWin':>6} {'CumRet':>8}")
for _, r in monthly.iterrows():
    print(f"{r['month']:>6} {r['trades']:>6} {r['avg_ret']:>+7.2f}% {r['win_rate']:>6.1f}% {r['stopped']:>5}/{r['trades']:>3} {r['big_win']:>5} {r['cum']:>+7.2f}%")

print()
print('='*70)
print('Entry type monthly breakdown:')
for month in ['202411', '202412', '202501', '202505', '202506']:
    mdf = df[df['month']==month]
    if len(mdf) == 0: continue
    print(f"\n{month}: {len(mdf)} trades, avg={mdf['return_pct'].mean():+.2f}%, wr={mdf['return_pct'].apply(lambda x: x>0).mean()*100:.0f}%")
    for et, grp in mdf.groupby('entry_type'):
        print(f"  {et}: {len(grp)} trades, avg={grp['return_pct'].mean():+.2f}%, wr={grp['return_pct'].apply(lambda x: x>0).mean()*100:.0f}%")

print()
print('='*70)
print('High-repeat stocks (frequent losers):')
repeat_stats = df.groupby(['name','repeat']).agg(
    ret=('return_pct','mean'),
    cnt=('return_pct','count')
).reset_index()
# Show stocks with 5+ repeat and consistently losing
high_rep = df[df['repeat']>=5]
lose_rep = high_rep[high_rep['return_pct'] < -3]
lose_summary = lose_rep.groupby('name').agg(cnt=('return_pct','count'), avg=('return_pct','mean')).sort_values('cnt', ascending=False)
print(f"Stocks with 5+ repeats and avg < -3%:")
for name, row in lose_summary.head(10).iterrows():
    print(f"  {name}: {row['cnt']} times, avg={row['avg']:+.2f}%")

# Check the top 10 trade dates: what was the market doing?
print()
print('='*70)
print('Best/worst months context:')
best_month = monthly.loc[monthly['avg_ret'].idxmax()]
worst_month = monthly.loc[monthly['avg_ret'].idxmin()]
print(f"Best: {best_month['month']} avg={best_month['avg_ret']:+.2f}%")
print(f"Worst: {worst_month['month']} avg={worst_month['avg_ret']:+.2f}%")