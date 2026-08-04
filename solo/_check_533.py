# -*- coding: utf-8 -*-
"""单独验证 000533 7/31 行的连板基因与市场环境"""
import pandas as pd
import numpy as np
import glob

code = '000533.SZ'


def newest(files):
    return max(files, key=lambda f: f.rsplit('_', 2)[-1].split('.')[0])


files = glob.glob(r"D:\mystock\cache_daily\treasure_daily_%s_*.parquet" % code.replace('.', '_'))
df = pd.read_parquet(newest(files)).sort_values('trade_date').reset_index(drop=True)
pc = df['pct_chg'].values.astype(float)
l = df['low'].values.astype(float)
h = df['high'].values.astype(float)
c = df['close'].values.astype(float)
dates = df['trade_date'].astype(str).tolist()

sh = pd.read_csv(r"d:\mystock\cache_daily\000001_SH.csv").sort_values('trade_date').reset_index(drop=True)
sh['ma20'] = sh['close'].rolling(20).mean()
sh_env = dict(zip(sh['trade_date'].astype(str), (sh['close'] > sh['ma20']).astype(int)))

# 000533 主板 → 10% 涨停
lim = pc >= 9.8
n = len(df)
low60 = pd.Series(l).rolling(60, min_periods=60).min().values

i = dates.index('20260731')
w = l[i - 59:i + 1]
li = i - 59 + int(np.argmin(w))
rally_high = h[li:i + 1].max()
rally_gain = (rally_high - low60[i]) / low60[i] * 100
pullback = (rally_high - c[i]) / rally_high * 100

# 主升期间最大连续涨停
seg = lim[li:i]
if seg.any():
    s = pd.Series(seg.astype(int))
    grp = (s == 0).cumsum()
    lb = int(s.groupby(grp).cumsum().max())
else:
    lb = 0

# 未来20日
i20 = min(i + 20, n - 1)
f20 = (c[i20] / c[i] - 1) * 100

print("000533 20260731: 主升=%.1f%% 回撤=%.1f%% 连板=%d 环境=%s 20日后=%+.1f%%" %
      (rally_gain, pullback, lb, '强' if sh_env.get('20260731', 0) else '弱', f20))

# 也看7/17~7/30 每日的涨停/回调分表现（打印日线摘要）
for i2 in range(dates.index('20260715'), n):
    d = dates[i2]
    if d < '20260715':
        continue
    if d > '20260803':
        break
    print("%s 收盘%.2f 涨幅%+.1f%% 涨停=%s" % (d, c[i2], pc[i2], 'Y' if lim[i2] else 'N'))
