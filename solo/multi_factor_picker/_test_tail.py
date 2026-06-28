"""调试二波检测"""
import pandas as pd

# 模拟数据（倒序，最新在前）
dates = pd.date_range('20260301', '20260611', freq='D')[::-1]
df = pd.DataFrame({'trade_date': dates, 'pct_chg': [0]*len(dates)})

print(f'总数据: {len(df)}')
print(f'前5条: {df.head()["trade_date"].tolist()}')
print(f'后5条: {df.tail()["trade_date"].tolist()}')
print(f'\ntail(60)取的是: {df.tail(60)["trade_date"].iloc[0]} ~ {df.tail(60)["trade_date"].iloc[-1]}')
print(f'这是{"最新" if df.tail(60)["trade_date"].iloc[0] > df.tail(60)["trade_date"].iloc[-1] else "最旧"}的数据')
