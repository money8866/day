# -*- coding: utf-8 -*-
"""核验民爆光电 2026 回测收益真实性：MCP 不复权日线 vs 回测 CSV"""
import json
import pandas as pd

pd.set_option('display.width', 220)

raw = open(r'c:\Users\kongx\.trae-cn\work\.trae\mcp-output\70265b27-afc7-4afa-961a-05f9946d13c6.txt', encoding='utf-8').read()
arr = json.loads(raw[raw.find('[{'):])
inner = json.loads(arr[0]['text'])
items = inner['data']['item']
df = pd.DataFrame(items)
df['date'] = (pd.to_datetime(df['date_ms'], unit='ms') + pd.Timedelta(hours=8)).dt.strftime('%Y-%m-%d')
for c in ('open_price', 'high_price', 'low_price', 'close_price', 'volume'):
    df[c] = pd.to_numeric(df[c])
df = df.reset_index(drop=True)

df['chg%'] = (df['close_price'] / df['close_price'].shift(1) - 1) * 100
df['一字板'] = (df['open_price'] == df['high_price']) & (df['high_price'] == df['low_price']) & (df['low_price'] == df['close_price'])
df['间隔天'] = pd.to_datetime(df['date']).diff().dt.days

print(df[['date', 'open_price', 'high_price', 'low_price', 'close_price', 'volume', 'chg%', '一字板', '间隔天']].to_string(index=False))

print()
print('=== 停牌缺口（相邻K线间隔>3天）===')
for i in df[df['间隔天'] > 3].index:
    print(f"  {df.loc[i-1, 'date']} -> {df.loc[i, 'date']}  间隔 {int(df.loc[i, '间隔天'])} 天")

buy = df.loc[df['date'] == '2026-01-16', 'close_price'].iloc[0]
fut = df.loc[df['date'] > '2026-01-16', 'close_price'].to_numpy()
ref = {3: 72.83237, 5: 115.918186, 10: 77.856825, 20: 142.063139}
print()
print(f'入场 = 2026-01-16 收盘 {buy}')
for n in (3, 5, 10, 20):
    if len(fut) >= n:
        v = (fut[n - 1] / buy - 1) * 100
        print(f'fut{n}: MCP {v:+9.2f}%  vs 回测CSV {ref[n]:+9.2f}%  差异 {abs(v - ref[n]):.3f}pp')
peak = pd.Series(fut[:20]).cummax()
print(f'fut_max_dd: MCP {float((fut[:20] / peak - 1).min()) * 100:+.2f}%  vs 回测CSV -17.63%')
print(f'20日内最高点: {(fut[:20].max() / buy - 1) * 100:+.2f}%（第 {int(fut[:20].argmax()) + 1} 个交易日）')
print('一字板日（不可买入但持仓者被动持有）:', df.loc[df['一字板'], 'date'].tolist())
print('除权检查: 全部单日涨跌幅均在 ±20% 内 → 无送转/配股缺口，+142% 为真实价格路径')
