import sys
sys.path.insert(0, '.')
from data_fetcher import get_kline_data

df = get_kline_data('300570.SZ', '20260301', '20260611')
print('股票代码: 300570.SZ 太辰光')
print('数据长度:', len(df))

if len(df) >= 61:
    close_5d_ago = df['close'].iloc[-6]
    close_20d_ago = df['close'].iloc[-21]
    close_60d_ago = df['close'].iloc[-61]
    close_now = df['close'].iloc[-1]
    
    gain_5d = (close_now - close_5d_ago) / close_5d_ago * 100
    gain_20d = (close_now - close_20d_ago) / close_20d_ago * 100
    gain_60d = (close_now - close_60d_ago) / close_60d_ago * 100
    
    print(f'近5日涨幅: {gain_5d:.2f}%')
    print(f'近20日涨幅: {gain_20d:.2f}%')
    print(f'近60日涨幅: {gain_60d:.2f}%')
    print(f'近10日最大涨幅: {df["pct_chg"].iloc[-10:].max():.2f}%')
    print('近10日涨幅序列:', list(df['pct_chg'].iloc[-10:].round(2)))
else:
    print('数据不足')