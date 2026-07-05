# -*- coding: utf-8 -*-
"""扫描2026-07-03精选信号 - 宽松版"""
import pandas as pd
import numpy as np
from data_loader import load_kline
from indicators import add_indicators, MA, RSI

# 扫描单只股票
def scan_stock(ts_code, start_date='20260601'):
    try:
        df = load_kline(ts_code, start_date=start_date)
        if df is None or len(df) < 30:
            return []
        
        df = df.sort_values('trade_date').reset_index(drop=True)
        df = add_indicators(df)
        
        signals = []
        
        for i in range(20, len(df)):
            date = df.iloc[i]['trade_date']
            if date != '20260703':
                continue
            
            close = df.iloc[i]['close']
            vol = df.iloc[i]['vol']
            
            # 计算MA
            ma5 = df['MA5'].iloc[i] if 'MA5' in df.columns else MA(df['close'], 5).iloc[i]
            ma20 = df['MA20'].iloc[i] if 'MA20' in df.columns else MA(df['close'], 20).iloc[i]
            rsi6 = df['RSI6'].iloc[i] if 'RSI6' in df.columns else RSI(df['close'], 6).iloc[i]
            
            # 基础条件
            if ma5 <= ma20:
                continue
            if rsi6 < 40 or rsi6 > 75:
                continue
            
            # 量比
            avg_vol = np.mean(df['vol'].iloc[max(0, i-20):i])
            vol_ratio = vol / avg_vol if avg_vol > 0 else 1
            if vol_ratio < 1.2:
                continue
            
            # MACD
            if 'DIF' in df.columns and 'DEA' in df.columns:
                dif = df['DIF'].iloc[i]
                dea = df['DEA'].iloc[i]
                if dif <= dea:
                    continue
            
            # 20日动量
            if i >= 20:
                mom_20 = (close - df.iloc[i-20]['close']) / df.iloc[i-20]['close'] * 100
            else:
                mom_20 = 0
            
            # 计算评分
            ma_diff = (ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0
            
            # 趋势评分
            if ma_diff > 8:
                trend_score = 60
            elif ma_diff > 5:
                trend_score = 50 + (ma_diff - 5) * 3
            elif ma_diff > 2:
                trend_score = 30 + (ma_diff - 2) * 6
            else:
                continue
            
            # 动量评分
            if mom_20 > 25:
                momentum_score = 70
            elif mom_20 > 15:
                momentum_score = 60 + (mom_20 - 15) * 2
            elif mom_20 > 5:
                momentum_score = 40 + (mom_20 - 5) * 2
            else:
                momentum_score = 30 + mom_20
            
            # RSI评分
            if 50 <= rsi6 <= 65:
                rsi_score = 100
            elif rsi6 < 50:
                rsi_score = 80 + rsi6 * 0.4
            else:
                rsi_score = 100 - (rsi6 - 65) * 3
            
            # 量比评分
            if 1.5 <= vol_ratio <= 2.5:
                vol_score = 100
            elif vol_ratio < 1.5:
                vol_score = 70 + vol_ratio * 10
            else:
                vol_score = 90 - (vol_ratio - 2.5) * 20
            
            total_score = trend_score * 0.25 + momentum_score * 0.30 + rsi_score * 0.25 + vol_score * 0.20
            
            signals.append({
                'ts_code': ts_code,
                'date': date,
                'close': close,
                'rsi6': rsi6,
                'vol_ratio': vol_ratio,
                'ma_diff': ma_diff,
                'momentum': mom_20,
                'total_score': total_score
            })
        
        return signals
    except Exception as e:
        return []

# 扫描所有股票
df_stocks = pd.read_csv('high_mv_stocks.csv', encoding='utf-8-sig')
stocks = df_stocks['ts_code'].tolist()

print('扫描 2026-07-03 精选信号...')
print('=' * 60)

all_signals = []
for idx, ts_code in enumerate(stocks):
    signals = scan_stock(ts_code)
    all_signals.extend(signals)
    
    if (idx + 1) % 500 == 0:
        print('已扫描 ' + str(idx + 1) + '/' + str(len(stocks)) + ' 只')

if all_signals:
    df = pd.DataFrame(all_signals)
    df = df.sort_values('total_score', ascending=False)
    
    print('')
    print('2026-07-03 精选信号 TOP10:')
    print('-' * 60)
    print('代码             收盘价    RSI     量比     评分    20日动量')
    print('-' * 60)
    
    for i, (_, row) in enumerate(df.head(10).iterrows()):
        code = row['ts_code']
        close = '%.2f' % row['close']
        rsi = '%.1f' % row['rsi6']
        vol = '%.2f' % row['vol_ratio']
        score = '%.1f' % row['total_score']
        mom = '%.1f' % row['momentum']
        print(code + '  ' + close + '   ' + rsi + '    ' + vol + '   ' + score + '    ' + mom + '%')
    
    print('')
    print('共 ' + str(len(df)) + ' 只信号')
    
    # 保存
    df.to_csv('signals_20260703.csv', index=False, encoding='utf-8-sig')
    print('已保存到 signals_20260703.csv')
else:
    print('未发现任何信号')
