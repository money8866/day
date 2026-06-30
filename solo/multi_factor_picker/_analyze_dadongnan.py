# -*- coding: utf-8 -*-
"""分析大东南(002263.SZ)的二波形态"""
import os, sys, numpy as np
from datetime import datetime

if 'TUSHARE_TOKEN' not in os.environ:
    for _l in open(r'D:\mystock\config\.env'):
        if _l.strip().startswith('TUSHARE_TOKEN='):
            os.environ['TUSHARE_TOKEN'] = _l.strip().split('=', 1)[1].strip().strip('"')
            break
import tushare as ts
ts.set_token(os.environ['TUSHARE_TOKEN'])
pro = ts.pro_api()

code = '002263.SZ'
name = '大东南'

# 日线
df = pro.daily(ts_code=code, start_date='20250101', end_date='20260625')
df = df.sort_values('trade_date').reset_index(drop=True)
print(f'{name}({code}) 交易日数: {len(df)}')
last = df.iloc[-1]
print(f'最新 {last.trade_date}: close={last.close:.2f} high={last.high:.2f} low={last.low:.2f} vol={last.vol:.0f}')

# 均线
df['ma20'] = df['close'].rolling(20).mean()
df['ma60'] = df['close'].rolling(60).mean()
ma20 = df.ma20.iloc[-1]
ma60 = df.ma60.iloc[-1]
print(f'MA20={ma20:.2f}  MA60={ma60:.2f}')
print(f'现价距MA20: {(last.close/ma20-1)*100:.1f}%')
print(f'现价距MA60: {(last.close/ma60-1)*100:.1f}%')

# 找一波拉升
lookback = df.tail(120)
peak_idx = lookback['close'].idxmax()
peak_val = lookback.loc[peak_idx, 'close']
peak_date = lookback.loc[peak_idx, 'trade_date']
current_val = df.iloc[-1]['close']
dd_pct = current_val / peak_val - 1
n_days = (datetime.strptime(last.trade_date, '%Y%m%d') - datetime.strptime(peak_date, '%Y%m%d')).days
print(f'\n=== 一波拉升分析 ===')
print(f'120日区间高点: {peak_date} close={peak_val:.2f}')
print(f'当前价 {current_val:.2f} 距高点: {dd_pct*100:.1f}%')
print(f'距高点已 {n_days} 天')

# 拉升前低点
before_peak = df.loc[:peak_idx]
min_idx = before_peak.tail(60)['close'].idxmin()
min_val = before_peak.loc[min_idx, 'close']
min_date = before_peak.loc[min_idx, 'trade_date']
rally = peak_val / min_val - 1
print(f'拉升起点: {min_date} {min_val:.2f} 拉升幅度: {rally*100:.1f}%')

# 判断形态
print(f'\n=== 形态判断 ===')
if abs(dd_pct) < 10:
    print(f'强势横盘: 回调{abs(dd_pct)*100:.1f}% < 10%')
elif abs(dd_pct) < 20:
    print(f'深度回调: 回调{abs(dd_pct)*100:.1f}% 在10-20%之间')
else:
    print(f'深度回调/急跌: 回调{abs(dd_pct)*100:.1f}% >= 20%')

# 是否创新低
after_peak = df.loc[peak_idx:]
min_after = after_peak['close'].min()
is_new_low = min_after < min_val
print(f'一波起点价: {min_val:.2f}')
print(f'回调最低价: {min_after:.2f}')
print(f'创新低: {"是 ❌" if is_new_low else "否 ✅"}')

# RSI
closes = df['close'].values
gains = np.maximum(np.diff(closes), 0)
losses = -np.minimum(np.diff(closes), 0)
avg_g = np.mean(gains[-14:])
avg_l = np.mean(losses[-14:])
rsi = 100 - 100 / (1 + avg_g/avg_l) if avg_l != 0 else 100
print(f'RSI(14) = {rsi:.1f}')

# 成交量分析
recent_vol = df.tail(20)['vol'].mean()
pre_recent_vol = df.tail(60).head(40)['vol'].mean()
vol_ratio = recent_vol / pre_recent_vol if pre_recent_vol > 0 else 1
print(f'近20日均量/前40日均量: {vol_ratio:.2f}')

# 压力位分析
print(f'\n=== 压力位分析 ===')
# 前高 = 拉升高点
print(f'前高压力: {peak_val:.2f} (距现价+{(peak_val/current_val-1)*100:.1f}%)')
# MA250
if len(df) >= 250:
    df['ma250'] = df['close'].rolling(250).mean()
    ma250 = df.ma250.iloc[-1]
    print(f'MA250: {ma250:.2f} (距现价+{(ma250/current_val-1)*100:.1f}%)')

# 历史更高高点
if len(df) >= 500:
    all_peak = df['close'].max()
    all_peak_date = df.loc[df['close'].idxmax(), 'trade_date']
    print(f'历史最高价: {all_peak:.2f} ({all_peak_date}) 距现价+{(all_peak/current_val-1)*100:.1f}%')

# 最近60天走势打印
print(f'\n=== 近60日走势 ===')
for _, r in df.tail(60).iterrows():
    chg = r['close'] / peak_val - 1
    mark = '**' if r.trade_date == peak_date else ''
    print(f'{r.trade_date}: close={r.close:>8.2f} 距前高{chg*100:>6.1f}%  vol={r.vol:>8.0f}')
