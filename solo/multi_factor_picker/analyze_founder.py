# -*- coding: utf-8 -*-
"""方正科技 减持完成公告分析"""
import tushare as ts
import os

ENV_PATH = r'D:\mystock\config\.env'
with open(ENV_PATH, 'r', encoding='utf-8') as f:
    for line in f:
        if line.startswith('TUSHARE_TOKEN='):
            TOKEN = line.strip().split('=', 1)[1]
            break

pro = ts.pro_api(TOKEN)

ts_code = '600601.SH'
name = '方正科技'

print('='*80)
print(f'{name}({ts_code}) 减持完成公告分析')
print('='*80)

# 1. 近期走势
print('\n[1/4] 近期走势')
df = pro.daily(ts_code=ts_code, start_date='20260401', end_date='20260628',
               fields='trade_date,open,high,low,close,pct_chg,vol')
if df is not None and len(df) > 0:
    df = df.sort_values('trade_date')
    print(f'{"日期":<12} {"收盘":<8} {"涨跌幅":<10} {"成交量(万手)":<15}')
    print('-'*55)
    for _, r in df.tail(20).iterrows():
        vol_wan = r['vol'] / 10000
        print(f'{r["trade_date"]:<12} {r["close"]:<8.2f} {r["pct_chg"]:<+10.2f} {vol_wan:<15.0f}')

# 2. 派息/分红/转增
print('\n[2/4] 近期分红/转增')
try:
    df2 = pro.dividend(ts_code=ts_code)
    print(df2.head(3))
except Exception as e:
    print(f'  fail: {e}')

# 3. 高管
print('\n[3/4] 高管')
try:
    df3 = pro.stk_managers(ts_code=ts_code)
    print(df3[['name', 'title', 'ann_date']].head(10))
except Exception as e:
    print(f'  fail: {e}')

# 4. 技术面
print('\n[4/4] 技术指标（最新交易日20260626）')
try:
    df4 = pro.stk_factor(ts_code=ts_code, trade_date='20260626',
                          fields='ts_code,trade_date,macd,macd_diff,macd_dea,kdj_k,kdj_d,kdj_j,rsi_6,rsi_12,rsi_24,cci,boll_upper,boll_mid,boll_lower')
    print(df4.to_string(index=False))
except Exception as e:
    print(f'  fail: {e}')

# 5. 统计区间涨跌幅
print('\n=== 区间统计 ===')
df_full = pro.daily(ts_code=ts_code, start_date='20260601', end_date='20260628')
if df_full is not None and len(df_full) > 0:
    df_full = df_full.sort_values('trade_date')
    first = df_full.iloc[0]
    last = df_full.iloc[-1]
    high = df_full['high'].max()
    low = df_full['low'].min()
    pct = (last['close'] - first['close']) / first['close'] * 100
    print(f'  区间：2026-06-01 至 2026-06-26')
    print(f'  期初收盘：{first["close"]:.2f}')
    print(f'  期末收盘：{last["close"]:.2f}')
    print(f'  区间涨幅：{pct:+.2f}%')
    print(f'  最高：{high:.2f}')
    print(f'  最低：{low:.2f}')
    print(f'  振幅：{(high-low)/low*100:.2f}%')
    
    # 6月26日特别关注
    jun26 = df_full[df_full['trade_date'] == '20260626']
    if len(jun26) > 0:
        r = jun26.iloc[0]
        print(f'\n  6月26日暴跌分析：')
        print(f'  开盘：{r["open"]:.2f}  收盘：{r["close"]:.2f}')
        print(f'  最高：{r["high"]:.2f}  最低：{r["low"]:.2f}')
        print(f'  跌幅：{r["pct_chg"]:.2f}%')
        print(f'  成交量：{r["vol"]/10000:.0f}万手')
        prev_day = df_full[df_full['trade_date'] == '20260625']
        if len(prev_day) > 0:
            prev_r = prev_day.iloc[0]
            vol_change = (r['vol'] / prev_r['vol'] - 1) * 100
            print(f'  前一交易日(6/25)涨跌幅：{prev_r["pct_chg"]:+.2f}%')
            print(f'  成交量变化：{vol_change:+.0f}%')
    
    # 6月25日异动
    jun25 = df_full[df_full['trade_date'] == '20260625']
    if len(jun25) > 0:
        r = jun25.iloc[0]
        print(f'\n  6月25日上涨分析：')
        print(f'  涨幅：{r["pct_chg"]:+.2f}%')
        print(f'  成交量：{r["vol"]/10000:.0f}万手')

# 股东人数变化
print('\n=== 股东人数 ===')
try:
    hn = pro.stk_holdernumber(ts_code=ts_code)
    if hn is not None and len(hn) > 0:
        hn = hn.sort_values('end_date', ascending=False)
        print(f'{"报告期":<12} {"股东人数":<12} {"变化":<10}')
        print('-'*35)
        prev = None
        for _, r in hn.iterrows():
            end_date = r['end_date']
            num = r['holder_num']
            if prev:
                change = (num - prev) / prev * 100
                print(f'{end_date:<12} {num:<12} {change:<+10.2f}')
            else:
                print(f'{end_date:<12} {num:<12}')
            prev = num
except Exception as e:
    print(f'  fail: {e}')
