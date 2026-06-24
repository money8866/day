# -*- coding: utf-8 -*-
"""验证：低共振评分（如12分）涨停 vs 一波涨幅/创新高的关系"""
import os, sys, datetime, time
sys.path.insert(0, r'D:\mystock')
os.environ['TUSHARE_TOKEN'] = '1a4e203d2cd96efc75a0c0aaa5f68069e3277c3ac13d2abfa4463d34'

import pandas as pd
import tushare as ts
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

code = '603929.SH'
print(f'=== {code} 亚翔集成 详细分析 ===')

# 1. 获取近期日线
df = pro.daily(ts_code=code, start_date='20260501', end_date='20260624')
df = df.sort_values('trade_date').reset_index(drop=True)
print(f'\n近期日线 ({len(df)}天):')
for _, r in df.tail(15).iterrows():
    mark = ' ← 信号日' if r['trade_date'] == '20260623' else (' ← 涨停日' if r['trade_date'] == '20260624' else '')
    print(f"  {r['trade_date']} 收盘{r['close']:.2f} 涨跌{r['pct_chg']:+.2f}% 成交量{r['vol']:.0f}{mark}")

# 2. 获取stk_factor_pro看技术指标
factor = pro.stk_factor_pro(ts_code=code, start_date='20260501', end_date='20260624',
    fields='ts_code,trade_date,close,rsi,rsi_5,kdj_k,kdj_d,kdj_j,cci,wr,macd,macd_dif,macd_dea,mfi,bias1,bias2,psy,vr,atr,turnover_rate,pe,pb')
factor = factor.sort_values('trade_date').reset_index(drop=True)
print(f'\n技术指标 (近10天):')
for _, r in factor.tail(10).iterrows():
    print(f"  {r['trade_date']} RSI={r['rsi']:.1f} KDJ-J={r['kdj_j']:.1f} MACD={r['macd']:.4f} CCI={r['cci']:.1f} WR={r['wr']:.1f} MFI={r['mfi']:.1f} BIAS1={r['bias1']:.2f}")

# 3. 找一波拉升的高点
print(f'\n=== 一波拉升分析 ===')
# 找近60日最高价
df60 = pro.daily(ts_code=code, start_date='20260401', end_date='20260624')
df60 = df60.sort_values('trade_date').reset_index(drop=True)
high_idx = df60['close'].idxmax()
high_row = df60.iloc[high_idx]
print(f'60日内最高价: {high_row["trade_date"]} 收盘{high_row["close"]:.2f}')

# 找一波起点（最低点）
low_idx = df60['close'].idxmin()
low_row = df60.iloc[low_idx]
print(f'60日内最低价: {low_row["trade_date"]} 收盘{low_row["close"]:.2f}')

# 一波涨幅
wave1_gain = (high_row['close'] / low_row['close'] - 1) * 100
print(f'一波涨幅: {low_row["close"]:.2f} → {high_row["close"]:.2f} = +{wave1_gain:.1f}%')

# 4. 信号日(6/23)是否创新高？
signal_close = df[df['trade_date'] == '20260623']['close'].values
if len(signal_close) > 0:
    signal_close = signal_close[0]
    # 对比一波高点
    print(f'\n信号日(6/23)收盘: {signal_close:.2f}')
    print(f'一波最高收盘: {high_row["close"]:.2f}')
    if signal_close >= high_row['close']:
        print('→ 信号日已创新高！')
    else:
        gap = (signal_close / high_row['close'] - 1) * 100
        print(f'→ 距一波高点: {gap:+.1f}%')

# 5. 6/24涨停分析
today = df[df['trade_date'] == '20260624']
if len(today) > 0:
    today = today.iloc[0]
    print(f'\n6/24涨停: 收盘{today["close"]:.2f} 涨跌{today["pct_chg"]:+.2f}%')
    new_high = (today['close'] / high_row['close'] - 1) * 100
    print(f'是否突破一波高点: {today["close"]:.2f} vs {high_row["close"]:.2f} = {new_high:+.1f}%')

# 6. 全量验证：85只信号股中，低评分(≤12)的次日表现
print(f'\n=== 全量验证：共振评分 vs 次日涨跌 ===')
# 从PDF提取的数据已排序，这里用wave2_daily的数据+次日行情
from pymupdf import open as fitz_open
PDF_PATH = r'D:\mystock\solo\multi_factor_picker\output\wave2_pattern_bull_stocks_20260624.pdf'
doc = fitz_open(PDF_PATH)
all_rows = []
for page in doc:
    tables = page.find_tables()
    for table in tables.tables:
        data = table.extract()
        if data and len(data[0]) > 5:
            for row in data[1:]:
                all_rows.append(row)
doc.close()

print(f'信号股总数: {len(all_rows)}')

# 逐只查次日(6/24)涨跌
results = []
for i, row in enumerate(all_rows):
    ts_code = row[0].strip()
    score = float(row[3]) if row[3] else 0
    pattern = row[2]
    entry_price = float(row[9]) if row[9] else 0
    
    # 获取6/24行情
    try:
        df_today = pro.daily(ts_code=ts_code, start_date='20260624', end_date='20260624')
        if len(df_today) > 0:
            pct = df_today.iloc[0]['pct_chg']
            close_today = df_today.iloc[0]['close']
            # 是否涨停
            is_limit = pct >= 9.9 or (ts_code.startswith('30') and pct >= 19.9) or (ts_code.startswith('68') and pct >= 19.9)
            results.append({
                'ts_code': ts_code,
                'score': score,
                'pattern': pattern,
                'pct_0624': pct,
                'is_limit': is_limit,
                'entry_price': entry_price,
                'close_0624': close_today,
            })
        time.sleep(0.06)
    except:
        pass
    
    if (i+1) % 20 == 0:
        print(f'  已查询 {i+1}/{len(all_rows)}')

print(f'\n查询到6/24行情: {len(results)}只')

if results:
    df_r = pd.DataFrame(results)
    
    # 按评分分档
    print(f'\n--- 按共振评分分档 vs 6/24涨跌 ---')
    for label, lo, hi in [('低评分≤12', 0, 12), ('中评分13-17', 13, 17), ('高评分≥18', 18, 999)]:
        sub = df_r[(df_r['score'] >= lo) & (df_r['score'] <= hi)]
        if len(sub) > 0:
            limit_count = sub['is_limit'].sum()
            print(f'{label}: {len(sub)}只 | 均涨{sub["pct_0624"].mean():+.2f}% | 涨停{limit_count}只 | 涨停率{limit_count/len(sub)*100:.1f}%')
    
    # 涨停股的评分分布
    limit_stocks = df_r[df_r['is_limit']]
    if len(limit_stocks) > 0:
        print(f'\n--- 涨停股详情 ---')
        for _, r in limit_stocks.iterrows():
            print(f"  {r['ts_code']} 评分{r['score']:.0f} {r['pattern']} 涨{r['pct_0624']:+.2f}%")
    
    # 评分与涨幅相关性
    corr = df_r['score'].corr(df_r['pct_0624'])
    print(f'\n评分-涨幅相关系数: {corr:.3f}')
