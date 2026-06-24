# -*- coding: utf-8 -*-
"""用pytdx逐只获取实时收盘价，验证82只信号股6/24表现"""
import os, sys, time
sys.path.insert(0, r'D:\mystock')

from pymupdf import open as fitz_open
import pandas as pd
from pytdx.hq import TdxHq_API

# 1. 从PDF提取信号股
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

# 2. pytdx连接
api = TdxHq_API()
servers = [('218.6.170.47', 7709), ('123.125.108.14', 7709), ('180.153.18.170', 7709), ('180.153.18.172', 80), ('202.108.253.139', 80)]
connected = False
for host, port in servers:
    try:
        if api.connect(host, port):
            connected = True
            print(f'连接: {host}:{port}')
            break
    except:
        continue
if not connected:
    print('连接失败'); sys.exit(1)

# 3. 逐只获取最近2根日线（6/23和6/24）
results = []
for i, row in enumerate(all_rows):
    ts_code = row[0].strip()
    try:
        score = float(row[3]) if row[3] else 0
    except:
        score = 0
    pattern = row[2]
    try:
        wave1_gain = float(row[4].replace('+','')) if row[4] else 0
    except:
        wave1_gain = 0
    try:
        pullback_pct = float(row[5]) if row[5] else 0
    except:
        pullback_pct = 0
    try:
        rsi_str = row[7] if len(row) > 7 else '0'
        rsi = float(rsi_str)
    except:
        rsi = 0
    
    market = 0 if ts_code.startswith('6') else 1
    code_num = ts_code[:6]
    
    try:
        # 获取最近5根日线
        bars = api.get_security_bars(8, market, code_num, 0, 5)
        if bars:
            df_bars = api.to_df(bars)
            df_bars = df_bars.sort_values('datetime').reset_index(drop=True)
            
            if len(df_bars) >= 2:
                last = df_bars.iloc[-1]    # 最新日
                prev = df_bars.iloc[-2]    # 前一日
                
                close = last['close']
                pre_close = prev['close']
                pct_chg = (close / pre_close - 1) * 100
                
                # 涨停判断
                if ts_code.startswith('68') or ts_code.startswith('30'):
                    is_limit = pct_chg >= 19.9
                else:
                    is_limit = pct_chg >= 9.9
                
                results.append({
                    'ts_code': ts_code, 'score': score, 'pattern': pattern,
                    'wave1_gain': wave1_gain, 'pullback_pct': pullback_pct,
                    'rsi': rsi,
                    'close': close, 'pct_chg': pct_chg, 'is_limit': is_limit,
                    'date': str(last['datetime'])[:10],
                })
    except Exception as e:
        pass
    
    time.sleep(0.05)
    if (i+1) % 20 == 0:
        print(f'  已查询 {i+1}/{len(all_rows)}')

api.disconnect()
print(f'\n成功获取: {len(results)}只')

df_r = pd.DataFrame(results)
# 过滤出6/24的数据
df_r['date_short'] = df_r['date'].str.replace('-', '').str[:8]
df_0624 = df_r[df_r['date_short'] == '20260624']
print(f'6/24数据: {len(df_0624)}只')

# 如果6/24数据不够，用最新日数据
if len(df_0624) < 30:
    print(f'用最新日数据代替（{df_r["date_short"].unique()}）')
    df_0624 = df_r

print(f'\n{"="*60}')
print(f'=== 全量验证：共振评分 vs 次日涨跌 ===')

# 按共振评分分档
print(f'\n--- 按共振评分分档 ---')
for label, lo, hi in [('[5-12]低评分', 5, 12), ('[13-17]中评分', 13, 17), ('[18-23]高评分', 18, 99)]:
    sub = df_0624[(df_0624['score'] >= lo) & (df_0624['score'] <= hi)]
    if len(sub) > 0:
        lc = int(sub['is_limit'].sum())
        avg = sub['pct_chg'].mean()
        up = (sub['pct_chg'] > 0).sum()
        print(f'{label}: {len(sub)}只 | 均涨{avg:+.2f}% | 上涨{up}只({up/len(sub)*100:.0f}%) | 涨停{lc}只({lc/len(sub)*100:.1f}%)')

# 涨停股
limit_s = df_0624[df_0624['is_limit']]
if len(limit_s) > 0:
    print(f'\n--- 涨停股 ---')
    for _, r in limit_s.sort_values('score', ascending=False).iterrows():
        print(f"  {r['ts_code']} 评分{r['score']:.0f} {r['pattern']} 一波+{r['wave1_gain']:.1f}% 回调{r['pullback_pct']:.1f}% RSI{r['rsi']:.1f} 涨{r['pct_chg']:+.2f}%")

# TOP10涨幅
print(f'\n--- 涨幅TOP10 ---')
for _, r in df_0624.nlargest(10, 'pct_chg').iterrows():
    print(f"  {r['ts_code']} 评分{r['score']:.0f} {r['pattern']} 一波+{r['wave1_gain']:.1f}% 回调{r['pullback_pct']:.1f}% RSI{r['rsi']:.1f} 涨{r['pct_chg']:+.2f}%")

# BOTTOM10跌幅
print(f'\n--- 跌幅TOP10 ---')
for _, r in df_0624.nsmallest(10, 'pct_chg').iterrows():
    print(f"  {r['ts_code']} 评分{r['score']:.0f} {r['pattern']} 一波+{r['wave1_gain']:.1f}% 回调{r['pullback_pct']:.1f}% RSI{r['rsi']:.1f} 涨{r['pct_chg']:+.2f}%")

# 相关性
if len(df_0624) > 2:
    corr_s = df_0624['score'].corr(df_0624['pct_chg'])
    corr_w = df_0624['wave1_gain'].corr(df_0624['pct_chg'])
    corr_r = df_0624['rsi'].corr(df_0624['pct_chg'])
    corr_p = df_0624['pullback_pct'].corr(df_0624['pct_chg'])
    print(f'\n相关性:')
    print(f'  共振评分-涨幅: {corr_s:.3f}')
    print(f'  一波涨幅-涨幅: {corr_w:.3f}')
    print(f'  RSI-涨幅: {corr_r:.3f}')
    print(f'  调整幅度-涨幅: {corr_p:.3f}')

# 按一波涨幅分档
print(f'\n--- 按一波涨幅分档 ---')
for label, lo, hi in [('[20-30]小一波', 20, 30), ('[30-50]中一波', 30, 50), ('[50+]大一波', 50, 999)]:
    sub = df_0624[(df_0624['wave1_gain'] >= lo) & (df_0624['wave1_gain'] < hi)]
    if len(sub) > 0:
        lc = int(sub['is_limit'].sum())
        avg = sub['pct_chg'].mean()
        print(f'{label}: {len(sub)}只 | 均涨{avg:+.2f}% | 涨停{lc}只({lc/len(sub)*100:.1f}%)')

# 特殊分析：强势横盘+低评分 vs 涨停
print(f'\n--- 强势横盘(回调<10%)的评分分布 ---')
sw = df_0624[df_0624['pattern'] == '强势横盘']
if len(sw) > 0:
    for label, lo, hi in [('评分5-10', 5, 10), ('评分11-14', 11, 14), ('评分≥15', 15, 99)]:
        sub = sw[(sw['score'] >= lo) & (sw['score'] <= hi)]
        if len(sub) > 0:
            lc = int(sub['is_limit'].sum())
            print(f'{label}: {len(sub)}只 | 均涨{sub["pct_chg"].mean():+.2f}% | 涨停{lc}只')
