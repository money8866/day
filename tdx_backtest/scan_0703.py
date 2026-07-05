# -*- coding: utf-8 -*-
"""扫描2026-07-03精选信号"""
import pandas as pd
from optimized_scanner_v2 import scan_stock_v2

# 加载股票池
df_stocks = pd.read_csv('high_mv_stocks.csv', encoding='utf-8-sig')
stocks = df_stocks['ts_code'].tolist()

print('扫描 2026-07-03 精选信号...')
print('=' * 60)

all_signals = []
for ts_code in stocks:
    signals = scan_stock_v2(ts_code, start_date='20260601', hold_days=5)
    if signals:
        for sig in signals:
            sig['ts_code'] = ts_code
            all_signals.append(sig)

if all_signals:
    df = pd.DataFrame(all_signals)
    
    # 筛选2026-07-03的信号
    df_0703 = df[df['date'] == '20260703']
    
    if len(df_0703) > 0:
        # 按评分排序
        df_0703 = df_0703.sort_values('total_score', ascending=False)
        
        print('2026-07-03 精选信号 TOP10:')
        print('-' * 60)
        print('代码             收盘价    RSI     量比     评分    20日动量')
        print('-' * 60)
        
        for i, (_, row) in enumerate(df_0703.head(10).iterrows()):
            code = row['ts_code']
            close = '%.2f' % row['close']
            rsi = '%.1f' % row['rsi6']
            vol = '%.2f' % row['vol_ratio']
            score = '%.1f' % row['total_score']
            mom = '%.1f' % row['momentum']
            print(code + '  ' + close + '   ' + rsi + '    ' + vol + '   ' + score + '    ' + mom + '%')
        
        print('')
        print('共 ' + str(len(df_0703)) + ' 只信号')
        
        # 保存详细数据
        df_0703.to_csv('signals_20260703.csv', index=False, encoding='utf-8-sig')
        print('已保存到 signals_20260703.csv')
    else:
        print('2026-07-03 无信号')
else:
    print('未发现任何信号')
