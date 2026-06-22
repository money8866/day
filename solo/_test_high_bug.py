import pandas as pd
import os

ts_code = '000970.SZ'
cache_file = os.path.join(r'd:\mystock\solo\cache_daily', ts_code + '.csv')

print('缓存文件:', cache_file)
print('存在:', os.path.exists(cache_file))

df = pd.read_csv(cache_file)
df['trade_date'] = df['trade_date'].astype(str)
df = df.sort_values('trade_date').reset_index(drop=True)
print('\n总行数:', len(df))
print('列名:', list(df.columns))
print('\n最近10日数据:')
cols = [c for c in ['trade_date', 'open', 'high', 'low', 'close'] if c in df.columns]
print(df[cols].tail(10).to_string())

# 检查 high 列
print('\n=== 检查 high 列 ===')
print('high 列最后10个值:', df['high'].tail(10).tolist())
print('close 列最后10个值:', df['close'].tail(10).tolist())
print('high == close? 最后10行:', (df['high'].tail(10) == df['close'].tail(10)).tolist())

# 检查 20日/60日高点
print('\n=== 20日高点计算 ===')
hist_20_high = df['high'].iloc[:-1].tail(20)
print('排除当天后，最后20个 high 值:', hist_20_high.tolist())
print('max:', hist_20_high.max())

print('\n=== 60日高点计算 ===')
hist_60_high = df['high'].iloc[:-1].tail(60)
print('排除当天后，最后60个 high 值max:', hist_60_high.max())

# 用 rolling 对照
print('\n=== rolling HHV 验证（包含当天）===')
high_series = df['high']
print('rolling(20).max 当天:', high_series.rolling(20).max().iloc[-1])
print('rolling(60).max 当天:', high_series.rolling(60).max().iloc[-1])

print('\n=== rolling HHV 验证（排除当天）===')
print('rolling(20).max 排除当天:', high_series.iloc[:-1].rolling(20).max().iloc[-1])
print('rolling(60).max 排除当天:', high_series.iloc[:-1].rolling(60).max().iloc[-1])

# 检查更久以前的数据
print('\n=== 往前120天的 high 数据 ===')
if len(df) >= 120:
    old_data = df[['trade_date', 'high', 'close']].iloc[-120:]
    print('前20行:')
    print(old_data.head(20).to_string())
    print('后20行:')
    print(old_data.tail(20).to_string())

# 测试：如果 high 数据全部等于 close？
print('\n=== 整体 high vs close ===')
diff = (df['high'] - df['close']).abs()
print('high 与 close 的差异 > 0 的行数:', (diff > 0).sum(), '/', len(df))
print('最近200天的 max high:', df['high'].tail(200).max())
print('最近200天的 max close:', df['close'].tail(200).max())
