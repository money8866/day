import pandas as pd
import os

cache_dir = r'd:\mystock\cache_daily'
code = '001258.SZ'

f = os.path.join(cache_dir, code + '.csv')
df = pd.read_csv(f)
df['trade_date'] = df['trade_date'].astype(int)
df = df[df['trade_date'] <= 20260603].tail(300).reset_index(drop=True)

last = df.iloc[-1]
close = df['close'].values
today_ratio = close[-1] / close[-2]

print(f'6/3收盘: {last["close"]:.2f}, 涨幅: {last["pct_chg"]:.2f}%')
print(f'今日涨幅比例: {today_ratio:.4f}')
print(f'涨停阈值: 1.098 (主板)')
if today_ratio >= 1.098:
    print('>>> 触发涨停过滤!')
else:
    print('>>> 未触发涨停过滤')
