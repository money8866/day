import sys
sys.path.insert(0, '.')
import numpy as np
from bts.data import load_daily
from prb.engine import PRBEngine

eng = PRBEngine()
df = load_daily('300404.SZ', '20260811', 300)
df = eng._prep(df)
n = len(df)
end_idx = n - 1
b = end_idx - 4  # 8/7
bc_close = float(df['close'].iloc[b])
plat = eng.find_platforms(df, b)[0]
bc = eng._breakout_conditions(df, b, plat)

seg = df.iloc[b + 1:end_idx + 1]
closes = seg['close'].values
ma5s = seg['ma5'].values
print('回踩段 K线:')
for i, (_, row) in enumerate(seg.iterrows()):
    print(f'  {row["trade_date"]} close={row["close"]:.2f} ma5={row["ma5"]:.2f} '
          f'close<ma5: {row["close"] < row["ma5"]} close较前日跌: {closes[i] < (bc_close if i == 0 else closes[i-1])}')
mask = (closes < ma5s) | (np.r_[bc_close, closes[:-1]] > closes)
print('pull_days_mask:', mask, 'any:', bool(np.any(mask)))
