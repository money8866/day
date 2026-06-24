# -*- coding: utf-8 -*-
"""验证：亚翔集成603929共振分12分涨停规律 + 全量验证"""
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

# 找一波高点
wave1_high_date = '20260611'
wave1_high = 230.86  # 一波最高收盘
wave1_low = 149.65   # 一波起点
wave1_gain = (wave1_high / wave1_low - 1) * 100

print(f'\n一波: {wave1_low:.2f} → {wave1_high:.2f} = +{wave1_gain:.1f}%')
print(f'一波高点: {wave1_high_date} 收盘{wave1_high:.2f}')

# 信号日(6/23)
signal_close = df[df['trade_date'] == '20260623']['close'].values[0]
gap_to_high = (signal_close / wave1_high - 1) * 100
print(f'信号日收盘: {signal_close:.2f} 距一波高点: {gap_to_high:+.1f}%')

# 6/24
df_today = pro.daily(ts_code=code, start_date='20260624', end_date='20260624')
if len(df_today) > 0:
    today_close = df_today.iloc[0]['close']
    today_pct = df_today.iloc[0]['pct_chg']
    vs_high = (today_close / wave1_high - 1) * 100
    print(f'6/24: 收盘{today_close:.2f} 涨{today_pct:+.2f}% 距一波高点{vs_high:+.1f}%')
    if today_close >= wave1_high:
        print('→ 突破一波高点，创新高！')

# 2. 调整形态分析
print(f'\n=== 调整形态 ===')
adjust_start = 230.86  # 一波高点
adjust_low = df[(df['trade_date'] >= '20260611') & (df['trade_date'] <= '20260623')]['close'].min()
adjust_pct = (adjust_low / adjust_start - 1) * 100
adjust_days = len(df[(df['trade_date'] >= '20260612') & (df['trade_date'] <= '20260623')])
print(f'调整: {adjust_start:.2f} → {adjust_low:.2f} = {adjust_pct:.1f}% ({adjust_days}天)')

# 中间反弹（6/17涨停）
print(f'6/17曾涨停反弹: 232.18 → 再回落215.52 → 210.80')
print(f'→ 这是V型急跌+二次回踩形态！')

# 3. 获取stk_factor_pro看6/23指标
factor = pro.stk_factor_pro(ts_code=code, start_date='20260620', end_date='20260623')
if factor is not None and len(factor) > 0:
    factor = factor.sort_values('trade_date')
    print(f'\n=== 6/23技术指标 ===')
    r = factor.iloc[-1]
    print(f'可用字段: {factor.columns.tolist()}')
    for col in factor.columns:
        if col not in ['ts_code', 'trade_date']:
            try:
                print(f'  {col}: {r[col]}')
            except:
                pass

# 4. 全量验证85只信号股6/24表现
print(f'\n{"="*60}')
print(f'=== 全量验证：共振评分 vs 次日涨跌 ===')

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

results = []
for i, row in enumerate(all_rows):
    ts_code = row[0].strip()
    try:
        score = float(row[3]) if row[3] else 0
    except:
        score = 0
    pattern = row[2]
    try:
        entry_price = float(row[9]) if row[9] else 0
    except:
        entry_price = 0
    
    # 获取6/24行情
    try:
        df_t = pro.daily(ts_code=ts_code, start_date='20260624', end_date='20260624')
        if len(df_t) > 0:
            pct = df_t.iloc[0]['pct_chg']
            close_t = df_t.iloc[0]['close']
            # 涨停判断
            if ts_code.startswith('68') or ts_code.startswith('30'):
                is_limit = pct >= 19.9
            else:
                is_limit = pct >= 9.9
            results.append({
                'ts_code': ts_code, 'score': score, 'pattern': pattern,
                'pct_0624': pct, 'is_limit': is_limit,
                'entry_price': entry_price, 'close_0624': close_t,
            })
        time.sleep(0.06)
    except:
        pass
    
    if (i+1) % 20 == 0:
        print(f'  已查询 {i+1}/{len(all_rows)}')

print(f'\n查询到6/24行情: {len(results)}只')

if results:
    df_r = pd.DataFrame(results)
    
    print(f'\n--- 按共振评分分档 vs 6/24涨跌 ---')
    for label, lo, hi in [('[5-12]低评分', 5, 12), ('[13-17]中评分', 13, 17), ('[18-23]高评分', 18, 99)]:
        sub = df_r[(df_r['score'] >= lo) & (df_r['score'] <= hi)]
        if len(sub) > 0:
            limit_count = int(sub['is_limit'].sum())
            avg_pct = sub['pct_0624'].mean()
            up_count = (sub['pct_0624'] > 0).sum()
            print(f'{label}: {len(sub)}只 | 均涨{avg_pct:+.2f}% | 上涨{up_count}只({up_count/len(sub)*100:.0f}%) | 涨停{limit_count}只({limit_count/len(sub)*100:.1f}%)')
    
    # 涨停股详情
    limit_stocks = df_r[df_r['is_limit']]
    if len(limit_stocks) > 0:
        print(f'\n--- 涨停股详情 ---')
        for _, r in limit_stocks.sort_values('score', ascending=False).iterrows():
            print(f"  {r['ts_code']} 评分{r['score']:.0f} {r['pattern']} 涨{r['pct_0624']:+.2f}%")
    
    # 相关性
    corr = df_r['score'].corr(df_r['pct_0624'])
    print(f'\n评分-涨幅相关系数: {corr:.3f}')
    
    # 5. 额外分析：一波涨幅vs次日涨跌
    print(f'\n--- 一波涨幅(从PDF) vs 6/24涨跌 ---')
    # PDF中一波涨幅列是第5列
    for _, r in df_r.iterrows():
        # 获取一波涨幅
        matching = [row for row in all_rows if row[0].strip() == r['ts_code']]
        if matching:
            try:
                wave1 = float(matching[0][4].replace('+',''))
                r['wave1_gain'] = wave1
            except:
                r['wave1_gain'] = 0
    
    df_r['wave1_gain'] = df_r['ts_code'].apply(lambda c: 
        float([row[4] for row in all_rows if row[0].strip() == c][0].replace('+','')) 
        if [row[4] for row in all_rows if row[0].strip() == c] else 0)
    
    # 按一波涨幅分档
    for label, lo, hi in [('[20-30]小一波', 20, 30), ('[30-50]中一波', 30, 50), ('[50+]大一波', 50, 999)]:
        sub = df_r[(df_r['wave1_gain'] >= lo) & (df_r['wave1_gain'] < hi)]
        if len(sub) > 0:
            print(f'{label}: {len(sub)}只 | 均涨{sub["pct_0624"].mean():+.2f}% | 涨停{int(sub["is_limit"].sum())}只')
    
    corr_w = df_r['wave1_gain'].corr(df_r['pct_0624'])
    print(f'一波涨幅-次日涨跌相关: {corr_w:.3f}')
